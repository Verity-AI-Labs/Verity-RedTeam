"""DeepSeek hosted-API adapter for the ask(messages, model, fmt) and model_digest(model) that audit.py injects.

Models in API_MODELS go to the DeepSeek API; every other name keeps the Ollama path (runner.chat and
audit.model_digest) unchanged. One Client holds a batch's shared spend estimate, retry counts and call
log, and is safe to share between task threads. The key is read from DEEPSEEK_API_KEY at request time
and is never stored, logged or written.
"""

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import http.client
import json
import os
import random
import threading
import time
import urllib.error
import urllib.request

try:
    from src import audit, runner
except ImportError:
    import audit
    import runner

API_URL = "https://api.deepseek.com/chat/completions"
API_MODELS = ("deepseek-flash",)
OLLAMA_HOST = "http://127.0.0.1:11434"
MAX_TOKENS = 8192  # includes reasoning tokens
TIMEOUT = 300
RETRY_STATUS = {429, 500, 502, 503, 504}
MAX_TRIES, BACKOFF_BASE, BACKOFF_CAP = 8, 2.0, 60.0
EMPTY_RETRIES = 2
PRICES = {"cache_hit": (0.003, 0.006), "cache_miss": (0.15, 0.30), "output": (0.60, 1.20)}  # USD/1M: off-peak, peak
PEAK_HOURS_UTC = ((1, 4), (6, 10))  # [start, end), Monday to Friday
# JSON mode needs the lowercase word "json" and an example; runner.SYSTEM only says "JSON", so attacker
# requests carry this fixed suffix on the system message (the prompt files are unchanged).
JSON_SUFFIX = ('\n\nAnswer with exactly one json object, for example: '
               '{"command": "ls -la", "done": false, "explanation": "inspect the working directory"}\n')

_local = threading.local()


def set_task(task_id):
    """Tag this thread's API calls with a task id in api_calls.jsonl (the batch driver sets it)."""
    _local.task = task_id


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
    key = os.environ.get("DEEPSEEK_API_KEY")
    return text.replace(key, "***") if key else text


def _server_message(raw):
    try:
        data = json.loads(raw)
        text = (data.get("error") or {}).get("message") or json.dumps(data)
    except (ValueError, AttributeError):
        text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    return _scrub(text[:1000])


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


class Client:
    """One batch's DeepSeek settings, shared spend estimate, retry counts and call log (thread-safe).

    After a 401/402, the spend cap or the hard deadline, every later API call raises the same
    audit.Abort, so in-flight tasks stop at their next model call and still write report.json.
    """

    def __init__(self, log=None, max_usd=4.25, effort="high", run_date=None, deadline=None,
                 host=OLLAMA_HOST, spent=0.0, post=_post, sleep=time.sleep,
                 clock=lambda: datetime.now(timezone.utc), monotonic=time.monotonic):
        self.log, self.max_usd, self.effort, self.host = log, max_usd, effort, host
        self.run_date = run_date or clock().strftime("%Y-%m-%d")
        self.deadline, self.post, self.sleep, self.clock, self.monotonic = deadline, post, sleep, clock, monotonic
        self.lock, self.spent, self.aborted, self.calls, self.retries = threading.Lock(), spent, None, 0, {}
        self.tokens = dict.fromkeys(("prompt", "completion", "cache_hit", "cache_miss"), 0)

    def request_config(self):
        """Every setting that shapes an API generation; hashed into protocol_id."""
        return {"provider": "deepseek", "endpoint": API_URL, "thinking": "enabled",
                "reasoning_effort": self.effort, "max_tokens": MAX_TOKENS,
                "response_format": {"attacker": "json_object", "judge": None},
                "json_suffix_sha256": hashlib.sha256(JSON_SUFFIX.encode()).hexdigest(),
                "merge_consecutive_roles": True}

    def digest(self, model):
        """A pinned, network-free id for an API model; the Ollama /api/tags digest otherwise."""
        if model in API_MODELS:
            return f"deepseek-api:{model}@{self.run_date}"
        return audit.model_digest(model, self.host)

    def ask(self, messages, model, fmt):
        """Attacker (fmt set: JSON mode) or judge (fmt None) call, returned in Ollama's reply shape."""
        if model not in API_MODELS:
            return runner.chat(messages, model, self.host, fmt=fmt)
        attacker = fmt is not None
        body = {"model": model, "messages": _wire(messages, attacker), "max_tokens": MAX_TOKENS,
                "thinking": {"type": "enabled"}, "reasoning_effort": self.effort}
        if attacker:
            body["response_format"] = {"type": "json_object"}
        for empty in range(EMPTY_RETRIES + 1):
            data = self._request(body, "attacker" if attacker else "judge")
            choice = data["choices"][0]
            content = (choice.get("message") or {}).get("content") or ""
            if content.strip() or choice.get("finish_reason") == "length" or empty == EMPTY_RETRIES:
                break  # "length" goes back as is: the caller's format-error path handles it
            self._count("empty")
        usage = data.get("usage") or {}
        return {"message": {"role": "assistant", "content": content},
                "eval_count": usage.get("completion_tokens") or 0,
                "prompt_eval_count": usage.get("prompt_tokens") or 0,
                "done_reason": choice.get("finish_reason")}

    def preflight(self, model=API_MODELS[0]):
        """One 1-token call, thinking off, to catch a bad key or empty balance before Docker work."""
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
            if self.aborted is None and self.spent > self.max_usd:
                self.aborted = f"max_usd_exceeded (estimated ${self.spent:.4f} > ${self.max_usd})"
            if self.aborted is None and self.deadline is not None and self.monotonic() > self.deadline:
                self.aborted = "hard_deadline"
            if self.aborted is None and not os.environ.get("DEEPSEEK_API_KEY"):
                self.aborted = "missing_api_key (DEEPSEEK_API_KEY is not set)"
            if self.aborted:
                raise audit.Abort(self.aborted)

    def _count(self, why):
        with self.lock:
            self.retries[why] = self.retries.get(why, 0) + 1

    def _write(self, entry):
        if self.log is not None:  # called under self.lock
            with self.log.open("a") as stream:
                stream.write(json.dumps(entry) + "\n")

    def _request(self, body, role):
        """POST with retries; returns the parsed 200 body. Every response is logged, billed ones costed."""
        payload, tries = json.dumps(body).encode(), 0
        while True:
            self._check()
            headers = {"Content-Type": "application/json",
                       "Authorization": "Bearer " + os.environ["DEEPSEEK_API_KEY"]}
            started, t0, headers_in, tries = self.clock(), self.monotonic(), {}, tries + 1
            try:
                status, headers_in, raw = self.post(API_URL, payload, headers, TIMEOUT)
            except (OSError, http.client.HTTPException) as error:
                status, raw = None, b""
                why = "timeout" if "timed out" in str(error).lower() else "connection"
            entry = {"utc": started.isoformat(), "task": getattr(_local, "task", None), "role": role,
                     "status": status, "retries": tries - 1, "latency_s": round(self.monotonic() - t0, 3)}
            if status == 200:
                try:
                    data = json.loads(raw)
                    data["choices"][0]
                except (ValueError, KeyError, IndexError, TypeError):
                    why = "bad_body"
                else:
                    self._bill(entry, data, started)
                    return data
            elif status in (401, 402):
                reason = {401: "invalid_api_key", 402: "balance_exhausted"}[status]
                self._fail(entry, _server_message(raw))
                self.abort(f"{reason} (HTTP {status}: {_server_message(raw)})")
                raise audit.Abort(self.aborted)
            elif status is not None and status not in RETRY_STATUS:
                self._fail(entry, _server_message(raw))
                raise RuntimeError(f"DeepSeek HTTP {status}: {_server_message(raw)}")
            elif status is not None:
                why = str(status)
            self._count(why)
            self._fail(entry, why if status is None else f"{why}: {_server_message(raw)}")
            if tries >= MAX_TRIES:
                raise RuntimeError(f"DeepSeek: gave up after {tries} tries (last: {why})")
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
