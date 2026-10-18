"""Hosted-API adapter for the ask(messages, model, fmt) and model_digest(model) that the audit injects.

DeepSeek defaults remain unchanged. With provider="openrouter", the configured attacker and judge go
to OpenRouter. A Client shares spend, retry counts and call logs between task threads. API keys are
read at request time and never logged or written.
"""

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import random
import threading
import time
import urllib.error
import urllib.request

API_URL = "https://api.deepseek.com/chat/completions"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
API_MODELS = ("deepseek-flash",)
MAX_TOKENS = 16384  # the attacker's budget; includes reasoning tokens (a "length" cut is a format-error turn)
# The judge's own budget: a high-effort verdict on task 462 spent all 8192 tokens reasoning and came
# back empty with finish_reason "length".
JUDGE_MAX_TOKENS = 32768
# A judge reply still cut by "length" is retried once with thinking disabled (the API's lowest effort;
# "high" and "max" are the only effort levels) and this note appended to the user message.
JUDGE_RETRY = "Your previous answer ran out of tokens before the verdict. Output the verdict JSON object now, with minimal reasoning."
TIMEOUT = 300
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_TRIES, BACKOFF_BASE, BACKOFF_CAP = 8, 2.0, 60.0
EMPTY_RETRIES = 2
PRICES = {"cache_hit": (0.003, 0.006), "cache_miss": (0.15, 0.30), "output": (0.60, 1.20)}  # USD/1M: off-peak, peak
PEAK_HOURS_UTC = ((1, 4), (6, 10))  # [start, end), Monday to Friday
# JSON mode needs the lowercase word "json" and an example; the attacker prompt only says "JSON", so attacker
# requests carry this fixed suffix on the system message.
JSON_SUFFIX = ('\n\nAnswer with exactly one json object, for example: '
               '{"action": "step", "task": 0, "tool": "click", "id": "e12"}\n')

_local = threading.local()


class Abort(Exception):
    """Raised by ask to stop the whole audit (bad key, empty balance, spend cap, deadline)."""


def set_task(task_id):
    """Tag this thread's API calls with a task id in api_calls.jsonl (the batch driver sets it)."""
    _local.task = task_id


def load_env(path=None):
    """Load API keys from a local .env without evaluating shell code or replacing exports."""
    path = Path(path) if path is not None else Path(__file__).resolve().parents[1] / ".env"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        key, separator, value = line.strip().removeprefix("export ").partition("=")
        key, value = key.strip(), value.strip()
        if not separator or key not in {"DEEPSEEK_API_KEY", "OPENROUTER_API_KEY"}:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if value:
            os.environ.setdefault(key, value)


def is_peak(when):
    """Peak pricing by UTC time: 01:00-04:00 and 06:00-10:00, Monday to Friday. A weekday holiday
    still counts as peak, which can only overestimate the cost."""
    when = when.astimezone(timezone.utc)
    return when.weekday() < 5 and any(start <= when.hour < end for start, end in PEAK_HOURS_UTC)


def next_peak(now):
    """The start of the next peak window (now itself if it is peak)."""
    if is_peak(now):
        return now
    hour = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return next(hour + timedelta(hours=k) for k in range(1, 24 * 8) if is_peak(hour + timedelta(hours=k)))


def _split(usage):
    """(cache-hit, cache-miss) prompt tokens; prompt tokens of unknown cache status bill as misses."""
    hit, miss = usage.get("prompt_cache_hit_tokens"), usage.get("prompt_cache_miss_tokens")
    if hit is None or miss is None:
        hit = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        miss = max(0, (usage.get("prompt_tokens") or 0) - hit)
    return hit, miss


def call_cost(usage, peak):
    """Estimated USD for one response's usage at off-peak or peak prices."""
    i, (hit, miss) = int(bool(peak)), _split(usage)
    return (hit * PRICES["cache_hit"][i] + miss * PRICES["cache_miss"][i]
            + (usage.get("completion_tokens") or 0) * PRICES["output"][i]) / 1e6


def logged_spend(path):
    """Estimated USD already recorded in an api_calls.jsonl (so --resume keeps one budget per folder)."""
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return 0.0
    total = 0.0
    for line in lines:
        try:
            total += float(json.loads(line).get("cost_usd") or 0)
        except (ValueError, AttributeError):
            continue
    return total


def _post(url, body, headers, timeout):
    """POST; returns (status, headers, raw body). HTTP errors are returned, transport errors raise."""
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers or {}), error.read()


def _retry_after(headers):
    value = next((v for k, v in headers.items() if k.lower() == "retry-after"), None)
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        return max(0.0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return None


def _scrub(text):
    for variable in ("DEEPSEEK_API_KEY", "OPENROUTER_API_KEY"):
        key = os.environ.get(variable)
        if key:
            text = text.replace(key, "***")
    return text


def model_family(model):
    """OpenRouter's organization prefix identifies the model family for independent judging."""
    return model.split("/", 1)[0].lower().split(":", 1)[0]


def _server_message(raw):
    try:
        data = json.loads(raw)
        text = (data.get("error") or {}).get("message") or json.dumps(data)
    except (ValueError, AttributeError):
        text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    return _scrub(text)[:1000]


def _wire(messages, attacker):
    """Messages as sent: role and content only, consecutive same-role turns merged (fit_context puts
    its omission note right after the task message), and the JSON-mode suffix on the attacker's system."""
    out = []
    for m in messages:
        content = m.get("content") or ("(empty reply)" if m["role"] == "assistant" else "")
        if out and out[-1]["role"] == m["role"]:
            out[-1]["content"] += "\n\n" + content
        else:
            out.append({"role": m["role"], "content": content})
    if attacker:
        if out and out[0]["role"] == "system":
            out[0]["content"] += JSON_SUFFIX
        else:
            out.insert(0, {"role": "system", "content": JSON_SUFFIX.strip()})
    return out


def _leading_json(content):
    """The reply's leading JSON object as sent, minus anything after it. DeepSeek sometimes appends
    leaked tool-call markup to a valid action, which json.loads rejects; left in the history, the model
    then copies it every turn. Replies that do not start with a JSON object are returned unchanged."""
    text = content.strip()
    try:
        obj, end = json.JSONDecoder().raw_decode(text)
    except ValueError:
        return content
    return text[:end] if isinstance(obj, dict) else content


class Client:
    """One batch's DeepSeek settings, shared spend estimate, retry counts and call log (thread-safe).

    After a 401/402, the spend cap or the hard deadline, every later API call raises the same
    Abort, so in-flight tasks stop at their next model call and still write report.json.
    """

    def __init__(self, log=None, max_usd=4.25, effort="high", run_date=None, deadline=None,
                 spent=0.0, post=_post, sleep=time.sleep,
                 clock=lambda: datetime.now(timezone.utc), monotonic=time.monotonic,
                 provider="deepseek", attacker=None, judge=None, input_price=None, output_price=None,
                 max_tokens=None, judge_max_tokens=None, max_tries=MAX_TRIES):
        if provider not in ("deepseek", "openrouter"):
            raise ValueError("provider must be deepseek or openrouter")
        if not math.isfinite(max_usd) or max_usd <= 0:
            raise ValueError("max_usd must be positive and finite")
        if type(max_tries) is not int or not 1 <= max_tries <= MAX_TRIES:
            raise ValueError(f"max_tries must be 1..{MAX_TRIES}")
        self.max_tries = max_tries
        self.provider = provider
        self.endpoint = OPENROUTER_URL if provider == "openrouter" else API_URL
        self.key_env = "OPENROUTER_API_KEY" if provider == "openrouter" else "DEEPSEEK_API_KEY"
        self.api_models = tuple(dict.fromkeys(m for m in (attacker, judge) if m)) if provider == "openrouter" else API_MODELS
        self.input_price, self.output_price = input_price, output_price
        self.max_tokens = max_tokens if max_tokens is not None else MAX_TOKENS
        self.judge_max_tokens = judge_max_tokens if judge_max_tokens is not None else JUDGE_MAX_TOKENS
        if any(type(n) is not int or n < 1 for n in (self.max_tokens, self.judge_max_tokens)):
            raise ValueError("Token budgets must be positive integers")
        if provider == "openrouter":
            if not self.api_models:
                raise ValueError("OpenRouter needs a configured attacker or judge model")
            if any(p is None or not math.isfinite(p) or p < 0 for p in (input_price, output_price)):
                raise ValueError("OpenRouter needs nonnegative input_price and output_price (USD per 1M tokens)")
        self.log, self.max_usd, self.effort = log, max_usd, effort
        self.run_date = run_date or clock().strftime("%Y-%m-%d")
        self.deadline, self.post, self.sleep, self.clock, self.monotonic = deadline, post, sleep, clock, monotonic
        self.lock, self.spent, self.aborted, self.calls, self.retries = threading.Lock(), spent, None, 0, {}
        self.reserved = 0.0
        self.tokens = dict.fromkeys(("prompt", "completion", "cache_hit", "cache_miss"), 0)

    def request_config(self):
        """Every setting that shapes an API generation; hashed into protocol_id."""
        if self.provider == "openrouter":
            return {"provider": self.provider, "endpoint": self.endpoint, "models": list(self.api_models),
                    "max_tokens": {"attacker": self.max_tokens, "judge": self.judge_max_tokens},
                    "response_format": {"attacker": None, "judge": None},
                    "merge_consecutive_roles": True, "attacker_leading_json_only": True,
                    "fallback_usd_per_million": {"input": self.input_price, "output": self.output_price}}
        return {"provider": "deepseek", "endpoint": API_URL, "thinking": "enabled",
                "reasoning_effort": self.effort, "max_tokens": {"attacker": MAX_TOKENS, "judge": JUDGE_MAX_TOKENS},
                "judge_length_retry": {"thinking": "disabled", "max_tokens": JUDGE_MAX_TOKENS,
                                       "note_sha256": hashlib.sha256(JUDGE_RETRY.encode()).hexdigest()},
                "response_format": {"attacker": "json_object", "judge": None},
                "json_suffix_sha256": hashlib.sha256(JSON_SUFFIX.encode()).hexdigest(),
                "merge_consecutive_roles": True, "attacker_leading_json_only": True}

    def digest(self, model):
        """A pinned, network-free id for an API model."""
        if model not in self.api_models:
            raise ValueError(f"{model} is not a configured {self.provider} model")
        prefix = "deepseek-api" if self.provider == "deepseek" else self.provider
        return f"{prefix}:{model}@{self.run_date}"

    def ask(self, messages, model, fmt):
        """Attacker (fmt set: JSON mode) or judge (fmt None) call, returned in Ollama's reply shape."""
        if model not in self.api_models:
            raise ValueError(f"{model} is not a configured {self.provider} model")
        attacker = fmt is not None
        if self.provider == "openrouter":
            body = {"model": model, "messages": _wire(messages, False),
                    "max_tokens": self.max_tokens if attacker else self.judge_max_tokens}
            data, choice, content = self._generate(body, "attacker" if attacker else "judge")
            usage = data.get("usage") or {}
            return {"message": {"role": "assistant", "content": _leading_json(content) if attacker else content},
                    "eval_count": usage.get("completion_tokens") or 0,
                    "prompt_eval_count": usage.get("prompt_tokens") or 0,
                    "done_reason": choice.get("finish_reason")}
        body = {"model": model, "messages": _wire(messages, attacker),
                "max_tokens": MAX_TOKENS if attacker else JUDGE_MAX_TOKENS,
                "thinking": {"type": "enabled"}, "reasoning_effort": self.effort}
        if attacker:
            body["response_format"] = {"type": "json_object"}
        data, choice, content = self._generate(body, "attacker" if attacker else "judge")
        if not attacker and choice.get("finish_reason") == "length":
            self._count("judge_length")
            retry = {k: v for k, v in body.items() if k != "reasoning_effort"}
            retry.update(messages=_wire(messages + [{"role": "user", "content": JUDGE_RETRY}], False),
                         thinking={"type": "disabled"})
            data, choice, content = self._generate(retry, "judge")
        if attacker and _leading_json(content) != content:
            content = _leading_json(content)
            self._count("trailing_text_stripped")
        usage = data.get("usage") or {}
        return {"message": {"role": "assistant", "content": content},
                "eval_count": usage.get("completion_tokens") or 0,
                "prompt_eval_count": usage.get("prompt_tokens") or 0,
                "done_reason": choice.get("finish_reason")}

    def _generate(self, body, role):
        """One generation; an empty reply is retried up to EMPTY_RETRIES times unless cut by "length"."""
        for empty in range(EMPTY_RETRIES + 1):
            data = self._request(body, role)
            choice = data["choices"][0]
            content = (choice.get("message") or {}).get("content") or ""
            if content.strip() or choice.get("finish_reason") == "length" or empty == EMPTY_RETRIES:
                break  # an attacker's "length" goes back as is: the caller's format-error path handles it
            self._count("empty")
        return data, choice, content

    def preflight(self, model=None):
        """One 1-token call, thinking off, to catch a bad key or empty balance before Docker work."""
        model = model or self.api_models[0]
        if self.provider == "openrouter":
            return self._request({"model": model, "messages": [{"role": "user", "content": "ping"}],
                                  "max_tokens": 1}, "preflight")
        self._request({"model": model, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1,
                       "thinking": {"type": "disabled"}}, "preflight")

    def abort(self, reason):
        with self.lock:
            self.aborted = self.aborted or reason

    def stats(self):
        with self.lock:
            seen = self.tokens["cache_hit"] + self.tokens["cache_miss"]
            return {"estimated_usd": round(self.spent, 6), "calls": self.calls, "tokens": dict(self.tokens),
                    "cache_hit_ratio": round(self.tokens["cache_hit"] / seen, 4) if seen else None,
                    "retries_by_status": dict(self.retries), "aborted": self.aborted}

    def _check(self):
        with self.lock:
            if self.aborted is None and self.spent >= self.max_usd:
                self.aborted = f"max_usd_exceeded (estimated ${self.spent:.4f} > ${self.max_usd})"
            if self.aborted is None and self.deadline is not None and self.monotonic() > self.deadline:
                self.aborted = "hard_deadline"
            if self.aborted is None and not os.environ.get(self.key_env):
                self.aborted = f"missing_api_key ({self.key_env} is not set)"
            if self.aborted:
                raise Abort(self.aborted)

    def _count(self, why):
        with self.lock:
            self.retries[why] = self.retries.get(why, 0) + 1

    def _write(self, entry):
        if self.log is not None:  # called under self.lock
            with self.log.open("a") as stream:
                stream.write(json.dumps(entry) + "\n")

    def _request(self, body, role):
        if self.provider != "openrouter":
            return self._request_unreserved(body, role)
        # Reserve a conservative upper estimate before starting concurrent calls.
        # UTF-8 bytes bound text tokens; include message framing and full output cap.
        prompt_bound = sum(len(m.get("content", "").encode("utf-8")) + 32 for m in body["messages"]) + 100
        reserve = (prompt_bound * self.input_price + body.get("max_tokens", 0) * self.output_price) / 1e6
        self._check()
        with self.lock:
            if self.spent + self.reserved + reserve > self.max_usd:
                self.aborted = (f"max_usd_reservation (spent ${self.spent:.4f}, pending ${self.reserved:.4f}, "
                                f"next-call upper estimate ${reserve:.4f}, cap ${self.max_usd})")
                raise Abort(self.aborted)
            self.reserved += reserve
        try:
            return self._request_unreserved(body, role)
        finally:
            with self.lock:
                self.reserved = max(0.0, self.reserved - reserve)

    def _request_unreserved(self, body, role):
        """POST with retries; returns the parsed 200 body. Every response is logged, billed ones costed."""
        payload, tries = json.dumps(body).encode(), 0
        while True:
            self._check()
            headers = {"Content-Type": "application/json",
                       "Authorization": "Bearer " + os.environ[self.key_env]}
            started, t0, headers_in, tries = self.clock(), self.monotonic(), {}, tries + 1
            try:
                status, headers_in, raw = self.post(self.endpoint, payload, headers, TIMEOUT)
            except (OSError, http.client.HTTPException) as error:
                status, raw = None, b""
                why = "timeout" if "timed out" in str(error).lower() else "connection"
            entry = {"utc": started.isoformat(), "task": getattr(_local, "task", None), "role": role,
                     "provider": self.provider, "model": body["model"],
                     "status": status, "retries": tries - 1, "latency_s": round(self.monotonic() - t0, 3)}
            if status == 200:
                try:
                    data = json.loads(raw)
                    data["choices"][0]
                except (ValueError, KeyError, IndexError, TypeError):
                    why = "bad_body"
                else:
                    self._bill({**entry, "provider": self.provider, "model": body["model"]}, data, started)
                    return data
            elif status in (401, 402):
                reason = {401: "invalid_api_key", 402: "balance_exhausted"}[status]
                self._fail(entry, _server_message(raw))
                self.abort(f"{reason} (HTTP {status}: {_server_message(raw)})")
                raise Abort(self.aborted)
            elif status is not None and status not in RETRY_STATUS:
                self._fail(entry, _server_message(raw))
                raise RuntimeError(f"{self.provider} HTTP {status}: {_server_message(raw)}")
            elif status is not None:
                why = str(status)
            self._count(why)
            self._fail(entry, why if status is None else f"{why}: {_server_message(raw)}")
            if tries >= self.max_tries:
                raise RuntimeError(f"{self.provider}: gave up after {tries} tries (last: {why})")
            delay = _retry_after(headers_in)
            if delay is None:
                cap = min(BACKOFF_CAP, BACKOFF_BASE * 2 ** (tries - 1))
                delay = random.uniform(cap / 2, cap)
            self.sleep(min(delay, 600.0))

    def _fail(self, entry, error):
        with self.lock:
            self._write({**entry, "error": _scrub(error), "cost_usd": 0.0})

    def _bill(self, entry, data, started):
        usage = data.get("usage") or {}
        peak = is_peak(started) or is_peak(self.clock())
        cost, (hit, miss) = call_cost(usage, peak), _split(usage)
        if self.provider == "openrouter":
            reported = usage.get("cost")
            if isinstance(reported, (int, float)) and math.isfinite(reported) and reported >= 0:
                cost, source = float(reported), "provider_reported"
            else:
                cost = ((usage.get("prompt_tokens") or 0) * self.input_price
                        + (usage.get("completion_tokens") or 0) * self.output_price) / 1e6
                source = "configured_fallback"
            entry = {**entry, "cost_source": source}
            peak = None
        choice = data["choices"][0]
        with self.lock:
            self.spent += cost
            self.calls += 1
            for key, value in (("prompt", usage.get("prompt_tokens")), ("completion", usage.get("completion_tokens")),
                               ("cache_hit", hit), ("cache_miss", miss)):
                self.tokens[key] += value or 0
            self._write({**entry, "finish_reason": choice.get("finish_reason"), "usage": usage,
                         "system_fingerprint": data.get("system_fingerprint"), "cost_usd": round(cost, 8),
                         "peak": peak, "total_usd": round(self.spent, 6),
                         "reasoning_content": (choice.get("message") or {}).get("reasoning_content")})
