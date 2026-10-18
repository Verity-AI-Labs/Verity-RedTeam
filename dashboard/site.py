"""Build the Verity site: dashboard/site.html with the real audit, training, evaluation and match data inlined.

    python dashboard/site.py --env chess_game --runs artifacts/chess_game-v3 --shop-runs artifacts/shop-v1
                                                                                 # writes web/index.html

web/ is the hosted site (Vercel): this page plus web/api/live.js, which forwards live games to the
inference server (dashboard/live.py). Every number on the page comes from the run files; a missing file
leaves its section marked as pending.
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import chess

ROOT = Path(__file__).resolve().parents[1]


def read_json(path):
    path = Path(path)
    return json.loads(path.read_text()) if path.is_file() else None


def read_rows(path):
    path = Path(path)
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.is_file() else []


def audit_data(name, pack):
    """The auditor's tool calls in order, the proof rows, cost, and its own explanation. Each call is also run
    through the marker the auditor wrote afterwards, to show where it would have fired."""
    from verity.guard import Guard
    report = read_json(ROOT / f"experiments/{name}-audits/{name}.json")
    if not report or report.get("status") != "passed":
        return None
    guard = Guard(pack) if pack else None
    field = pack["signatures"][0].get("field", "calls") if pack else "calls"
    steps, logs = [], {}
    events = [e for e in report["transcript"] if e.get("phase") == "discovery" and e["role"] in ("assistant", "user")]
    for action, answer in zip(events, events[1:]):
        if action["role"] != "assistant" or answer["role"] != "user" or "observation" not in answer:
            continue
        try:
            act = json.loads(action["content"])
        except ValueError:
            continue
        seen = answer["observation"]
        if act.get("action") == "call":
            call = f"{act['tool']}({', '.join(str(v) for v in (act.get('args') or {}).values())})"
            result = str(seen.get("result", seen.get("error", ""))).split(" Position (FEN)")[0].split("\n")[0]
            step = {"episode": act["episode"], "call": call, "result": result[:90]}
            log = logs.setdefault(act["episode"], [])
            before = {m["id"] for m in guard.check("\n".join(log), field)} if guard else set()
            log.append(call)
            fired = [m["id"] for m in guard.check("\n".join(log), field) if m["id"] not in before] if guard else []
            if fired:
                step["flag"] = f"Nothing stops this call. The marker Opus wrote afterwards ({fired[0]}) fires here."
            steps.append(step)
        elif act.get("action") == "finish" and "reward" in seen:
            steps.append({"episode": act["episode"], "reward": seen["reward"]})
    calls = read_rows(ROOT / f"experiments/{name}-audits/{name}.api_calls.jsonl")
    proof = report["proof"]
    return {"attacker": report["attacker"], "attacker_short": report["attacker"].split("/")[-1],
            "created": report["created_utc"][:10], "exploit": next((logs[e] for e in sorted(logs) if any(
                s.get("flag") for s in steps if s.get("episode") == e)), []),
            "steps": steps[:16], "calls": report["score_calls"], "max_calls": report["max_scores"],
            "episodes": len({s["episode"] for s in steps}),
            "explanation": ". ".join(report.get("explanation", "").split(". ")[:4]).rstrip(".") + ".",
            "cost_usd": max((float(c.get("total_usd") or 0) for c in calls), default=0.0),
            "proof": {k: proof[k] for k in ("tasks", "env_reward", "true_score", "reward_gap")} | {
                "rows": [{k: r.get(k) for k in ("task", "env_reward", "true_score")} | {"counts": tool_counts(r.get("calls", ""))}
                         for r in proof["rows"]]}}


def tool_counts(calls):
    """How many times each tool was called in a call log, e.g. {"hint": 7, "move": 6}."""
    counts = {}
    for line in str(calls).splitlines():
        if line.strip():
            name = line.split("(")[0].strip()
            counts[name] = counts.get(name, 0) + 1
    return counts


def telemetry(path):
    """Per-step rollout rows (the latest row for a step wins, as after a resume)."""
    steps = {}
    for row in read_rows(path):
        if row.get("event") == "rollout":
            steps[row["step"]] = {k: row.get(k) for k in ("step", "alarm", "env_reward", "guarded_reward", "true_score", "hint_abuse", "own_quality",
                                                          "hints", "moves", "caught_rate", "check_us", "samples", "examples")}
    return [steps[s] for s in sorted(steps)]


def guard_data(rows):
    """A real blocked episode from the Verity run, the check cost, and how often games were caught."""
    example = next((e for r in rows for e in r.get("examples") or [] if e.get("warnings") and e.get("trace")), None)
    if not rows or not example:
        return None
    rate = lambda part: sum(r["caught_rate"] or 0 for r in part) / max(1, len(part))
    return {"example": {"trace": example["trace"], "warning": example["warnings"][0],
                        "env_reward": example["env_reward"], "guarded_reward": example["guarded_reward"]},
            "check_us": sum(r["check_us"] or 0 for r in rows) / len(rows),
            "blocked_episodes": round(sum((r["caught_rate"] or 0) * (r["samples"] or 0) for r in rows)),
            "caught_first": rate(rows[:10]), "caught_last": rate(rows[-10:])}


def evaluation(path):
    result = read_json(path)
    if not result or result.get("status") != "completed":
        return None
    models = {arm: {"env_reward": m["env_reward"], "moves": m.get("moves"), "illegal": m.get("illegal"), "own_quality": m.get("own_quality"),
                    "hints_requested": (m.get("hints") or 0) + (m.get("unavailable_calls") or 0)}
              for arm, m in result["models"].items()}
    return {"positions": result["positions"], "disabled": result.get("disabled_tools", []), "models": models}


def ply_view(row):
    """One move row from scripts/match.py, as the page shows it (with the position after the move)."""
    board = chess.Board(row["fen_before"])
    board.push(chess.Move.from_uci(row["uci"]))
    return {k: row.get(k) for k in ("ply", "color", "policy", "san", "uci", "fallback", "hint_requests", "transcript",
                                    "eval_white_cp", "eval_before_white_cp", "cp_loss", "quality")} | {"fen_after": board.fen()}


def match_data(path):
    by_number = {}
    for row in read_rows(path):
        if "game" not in row:
            continue
        current = by_number.setdefault(row["game"], {"number": row["game"], "start_fen": row.get("fen_before"), "plies": []})
        if row["event"] == "move":
            current["plies"].append(ply_view(row))
        elif row["event"] == "forfeit":
            current["forfeit"] = {k: row.get(k) for k in ("policy", "transcript", "illegal", "unknown_tool_calls")}
        elif row["event"] == "result":
            current.update({k: row.get(k) for k in ("white", "black", "result", "winner", "reason", "fallbacks", "quality")})
    games = [by_number[n] for n in sorted(by_number)]
    games = [g for g in games if "result" in g]
    if not games:
        return None
    totals = {}
    for arm in ("baseline", "verity"):  # every move each model made, across all games
        plies = [q for g in games for q in g["plies"] if q["policy"] == arm and q.get("quality") is not None]
        score = sum(1 if g["winner"] == arm else .5 if g["winner"] == "draw" else 0 for g in games)
        totals[arm] = {"score": score, "moves": len(plies), "fallbacks": sum(bool(q["fallback"]) for q in plies),
                       "forfeits": sum((g.get("forfeit") or {}).get("policy") == arm for g in games),
                       "quality": sum(q["quality"] for q in plies) / len(plies) if plies else None,
                       "cp_loss": sum(q["cp_loss"] for q in plies) / len(plies) if plies else None,
                       "hint_requests": sum(q["hint_requests"] or 0 for q in plies)}
    return {"games": games, "totals": totals}


def shop_audit(env, pack):
    """The shop audit's passed round: the auditor's calls, replayed on the same tasks to show where the marker it
    wrote afterwards fires, the proof rows, its explanation and the cost."""
    from verity import probe
    from verity.guard import Guard
    report = read_json(ROOT / "experiments/shop-audits/shop.json")
    if not report or report.get("status") != "passed" or not pack:
        return None
    passed = next(row for row in report["rounds"] if row["status"] == "passed")
    events, number = [], None
    for event in report["transcript"]:
        if event.get("phase") == "round":
            number = event["round"]
        elif number == passed["round"] and event.get("phase") == "discovery" and event["role"] in ("assistant", "user"):
            events.append(event)
    saved = ROOT / "experiments/shop-audits/shop.tasks.jsonl"  # the tasks the auditor played (the shop's tasks change by version)
    chosen = ([json.loads(line) for line in saved.read_text().splitlines() if line.strip()] if saved.is_file() else
              probe.select_tasks(env.tasks("train"), report["max_scores"] and len(passed["proof"]["rows"]), report["seed"]))
    guard, episodes, steps = Guard(pack), {}, []

    def start(index):
        episodes[index] = env.Episode()
        episodes[index].reset(**chosen[index])
    for index in range(len(chosen)):
        start(index)
    for action, answer in zip(events, events[1:]):
        if action["role"] != "assistant" or answer["role"] != "user" or "observation" not in answer:
            continue
        try:
            act = json.loads(action["content"])
        except ValueError:
            continue
        seen, index = answer["observation"], act.get("episode")
        if act.get("action") == "reset" and index in episodes:
            start(index)
        elif act.get("action") == "call" and index in episodes and "error" not in seen:
            episode = episodes[index]
            before = {m["id"] for m in guard.check(env.record(episode), "calls")}
            try:
                getattr(episode, act["tool"])(**(act.get("args") or {}))
            except Exception:
                continue
            line, result = env.record(episode).splitlines()[-2], episode.results[-1]
            step = {"episode": index, "call": line.split("  # ")[0], "key": line.split("  # ")[1].split(" -> ")[0],
                    "result": result[:110]}
            if [m for m in guard.check(env.record(episode), "calls") if m["id"] not in before]:
                step["flag"] = (f"The order is placed and the reward pays. The rule Opus wrote afterwards "
                                f"({pack['signatures'][0]['id']}) fires here.")
            steps.append(step)
        elif act.get("action") == "finish" and "reward" in seen:
            steps.append({"episode": index, "reward": seen["reward"]})
    rows = []
    for row in passed["proof"]["rows"]:
        lines = row["calls"].splitlines()
        rows.append({"task": row["task"], "goal": lines[0][len("# goal: "):], "order": lines[-1][len("# order: "):],
                     "env_reward": row["env_reward"], "true_score": row["true_score"]})
    calls = read_rows(ROOT / "experiments/shop-audits/shop.api_calls.jsonl")
    def broken(index):  # how many of the goal's requirements the placed order breaks
        order, answer = episodes[index].state["order"] or {}, episodes[index].site["answer"]
        return sum(order.get(k) != answer[k] for k in ("items", "coupon", "shipping")) if order else 0
    flagged = {s["episode"] for s in steps if s.get("flag")}
    focus = max(flagged, key=broken) if flagged else 0
    return {"attacker": report["attacker"], "attacker_short": report["attacker"].split("/")[-1],
            "created": report["created_utc"][:10], "round": passed["round"], "rounds": len(report["rounds"]),
            "calls": report["score_calls"], "max_calls": report["max_scores"],
            "cost_usd": max((float(c.get("total_usd") or 0) for c in calls), default=0.0),
            "explanation": passed.get("explanation", ""), "focus": focus,
            "steps": [s for s in steps if s.get("episode") == focus or "reward" in s],
            "first_page": env.render(episodes[focus].site, env.start(episodes[focus].site)),
            "proof": {k: passed["proof"][k] for k in ("tasks", "env_reward", "true_score", "reward_gap")} | {"rows": rows}}


def guard_demo(env, pack, site):
    """Guard (enforce) on one store with the audit's marker: an order padded with extra items and no coupon is
    undone and answered with the warning, then the agent fixes its cart and orders again. Real Guard code."""
    from verity.guard import Guard
    episode = Guard(pack, enforce=True).episode_class(env)()
    episode.reset(site=site)
    extra = env.extras(site)[:1]
    calls = env.buy(site, sorted([env.target(site)] + extra), coupon=False)
    fix = [env.click(site, "nav:cart"), *[env.click(site, f"remove:{k}") for k in extra]]
    for code, percent in site["coupons"].items():
        fix += [env.type_text(site, "coupon", code), env.click(site, "apply")]
        if percent:
            break
    fix += [env.click(site, "checkout"), env.click(site, "place")]
    steps = []
    for tool, *args in calls + fix:
        result = getattr(episode, tool)(*args)
        key = episode.elements.get(args[0]) if args else None
        steps.append({"call": env.line(tool, *args), "key": key, "blocked": episode.trace[-1]["blocked"],
                      "result": result if episode.trace[-1]["blocked"] else episode.results[-1] if episode.results else result})
    return {"goal": site["goal"], "steps": steps, "order": env.order_line(episode.state)[len("# order: "):],
            "env_reward": env.grade(episode), "true_score": env.check(episode),
            "guarded_reward": env.grade(episode) + sum(f["reward"] for f in episode.flags)}


SHOP_KEYS = ("step", "alarm", "env_reward", "guarded_reward", "true_score", "success", "hack", "hack_extra", "hack_skip",
             "items_per_order", "coupon_applied", "requested_shipping", "calls", "caught_rate", "check_us", "samples")


def shop_data(runs):
    """The shop case study: audit, marker and its cross-check, training curves, a guarded episode, held-out results
    on the eval and deployment splits, and recorded episodes to replay."""
    from verity import environment
    runs, env = Path(runs), environment.load("shop")
    data = {"environment": environment.identity(env), "tools": list(env.TOOLS)}
    pack = read_json(ROOT / "markers/shop.json")
    if pack:
        data["markers"] = pack["signatures"]
        trained = (read_json(runs / "baseline" / "run.json") or {}).get("config", {}).get("environment")
        checks = [read_json(path) for path in sorted((ROOT / "experiments/shop-audits").glob("shop-check*.json"))]
        # the rule's cross-check on the version the shown runs trained on (else on the current version)
        data["check"] = next((c for c in checks if c and c.get("environment") == trained), None) or \
            read_json(ROOT / "experiments/shop-audits/shop-check.json")
        data["audit"] = shop_audit(env, pack)
        demo_site = next(json.loads(t["site"]) for t in env.tasks("eval") if json.loads(t["site"])["coupons"]
                         and json.loads(t["site"])["template"] == "named")
        data["guard_demo"] = guard_demo(env, pack, demo_site)
    arms = {}
    for arm in ("baseline", "verity"):
        steps = {}
        for row in read_rows(runs / arm / "telemetry.jsonl"):
            if row.get("event") == "rollout":
                steps[row["step"]] = row
        arms[arm] = [steps[s] for s in sorted(steps)]
    if all(arms.values()):
        data["training"] = {arm: [{k: r.get(k) for k in SHOP_KEYS} for r in rows] for arm, rows in arms.items()}
        run = read_json(runs / "baseline" / "run.json") or {}
        config = run.get("config", {})
        if config:
            data["training"]["run"] = {"steps": config["max_steps"], "episodes_per_step": config["generation_batch_size"],
                                       "model": config["model"].split("/")[-1], "lora_r": config.get("lora_r"),
                                       "learning_rate": config["learning_rate"]}
        example = next((e for r in reversed(arms["verity"]) for e in r.get("examples") or []
                        if any(t.get("blocked") for t in e.get("trace") or [])), None)
        if example:
            data["guard"] = {"record": example["reply"], "trace": example["trace"], "warnings": example["warnings"],
                             "env_reward": example["env_reward"], "guarded_reward": example["guarded_reward"],
                             "true_score": example["true_score"],
                             "check_us": sum(r.get("check_us") or 0 for r in arms["verity"]) / len(arms["verity"])}
    data["provenance"] = {arm: {k: m.get(k) for k in ("status", "steps_completed", "gpu", "precision", "wall_seconds")}
                          for arm in ("baseline", "verity") if (m := read_json(runs / arm / "run.json"))}
    results = {}
    for split, name in (("eval", "results.json"), ("ood", "results-ood.json")):
        result = read_json(runs / name)
        if result and result.get("status") == "completed":
            results[split] = {"tasks": result["positions"], "models": {
                arm: {k: v for k, v in m.items() if k not in ("samples", "replays")} for arm, m in result["models"].items()}}
            replays = {arm: m["replays"] for arm, m in result["models"].items() if m.get("replays")}
            if replays:
                data.setdefault("replays", {})[split] = replays
    if results:
        data["results"] = results
    data["live_runs"] = [run for run in read_rows(runs / "live-stores.jsonl") if run.get("status") == "finished"][-6:]
    # deployment stores to compose a live task on: what each sells, in stock or not
    data["stores"] = [{"index": i, "store": site["store"], "name": site["answer"]["name"], "address": site["answer"]["address"],
                       "rows": [{k: row[k] for k in ("product", "option", "kind", "category", "price", "stock")} for row in site["rows"]]}
                      for i, site in enumerate(read_rows(env.data_path(env.DEPLOYMENT["split"]))[:8])]
    return {k: v for k, v in data.items() if v}


TW_RUN = "experiments/runs/full-audit-v51-20261012"  # the last full Terminal Wrench audit (DeepSeek attacker and judge)


def terminal_data():
    """The Terminal Wrench audit: each task's verdict and what proved it, and one recorded hack replayed through Guard
    with the markers made from that task's report (commands are screened, never executed)."""
    from environments import terminal_wrench
    from verity import markers
    from verity.guard import Guard
    kinds = {"answer_read": "read the hidden answer", "trust_write": "rewrote the grader", "divergence": "output diverges from the reference",
             "answer_in_prompt": "the answer is printed in the prompt", "mutation": "the grader accepts a broken output"}
    tasks = []
    for path in sorted((ROOT / TW_RUN).glob("*/report.json")):
        hackability = read_json(path).get("hackability", {})
        found = set()
        for basis in hackability.get("basis") or []:
            if basis.startswith("mutation:"):
                found.add(kinds["mutation"])
            elif " judged " in basis:
                found.add("judged by a second model")
            else:
                found.update(kinds.get(k, k) for k in basis.split(" ")[-1].split(","))
        tasks.append({"task": path.parent.name, "verdict": hackability.get("value"), "how": sorted(found)})
    report = read_json(ROOT / TW_RUN / "blind-maze/report.json")
    pack = markers.from_sandbox_report(report)
    replay = terminal_wrench.replay(ROOT / TW_RUN / "blind-maze/redteam/a1/trajectory.jsonl", Guard(pack))
    return {"tasks": tasks, "replay": {"task": "blind-maze", "events": [{k: e[k] for k in ("turn", "command", "blocked", "warnings")} for e in replay],
                                       "marker": {k: pack["signatures"][0][k] for k in ("id", "summary", "detect")} if pack["signatures"] else None}}


def build(name, runs, shop_runs=None):
    runs = Path(runs)
    sys.path.insert(0, str(ROOT))
    from verity import environment, probe
    env = environment.load(name)
    data = {"built": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "environment": environment.identity(env),
            "tools": [spec.split(":")[0] for spec in probe.tool_specs(env)] if env.KIND == "tools" else []}
    marker_file = ROOT / f"markers/{name}.json"
    pack = read_json(marker_file)
    if pack and pack.get("signatures"):
        data["marker"], data["markers"] = pack["signatures"][0], pack["signatures"]
        data["provenance"] = {"marker_sha256": hashlib.sha256(marker_file.read_bytes()).hexdigest()[:12]}
    data["audit"] = audit_data(name, pack)
    arms = {arm: telemetry(runs / arm / "telemetry.jsonl") for arm in ("baseline", "verity")}
    run = read_json(runs / "baseline" / "run.json") or read_json(runs / "verity" / "run.json")
    if all(arms.values()):
        config = (run or {}).get("config", {})
        games = config.get("per_device_train_batch_size", 0) * config.get("gradient_accumulation_steps", 0)
        data["training"] = {arm: [{k: v for k, v in r.items() if k != "examples"} for r in rows] for arm, rows in arms.items()}
        if config:
            data["training"]["run"] = {"steps": config["max_steps"], "games_per_step": games or None}
        if config:
            data["model"] = config["model"].split("/")[-1]
            data["training"]["config"] = (
                f"{config['model'].split('/')[-1]} with a rank-16 LoRA adapter, GRPO for {config['max_steps']} steps, "
                f"{config['num_generations']} games per starting position, learning rate {config['learning_rate']:g}, "
                f"seed {config['seed']}. Both runs see the same positions in the same order; the only difference is "
                "whether Guard enforces the marker.")
    data["guard"] = guard_data(arms["verity"])
    manifests = {arm: read_json(runs / arm / "run.json") for arm in ("baseline", "verity")}
    if all(manifests.values()) and "provenance" in data:
        data["provenance"]["runs"] = {arm: {k: m.get(k) for k in ("status", "steps_completed", "gpu", "gpus", "precision",
                                                                 "wall_seconds")} for arm, m in manifests.items()}
    if closed := evaluation(runs / "results.json"):
        data["results"] = {"closed": closed, "open": evaluation(runs / "results-open.json")}
    data["match"] = match_data(runs / "matches.jsonl")
    live = [g for g in read_rows(runs / "live-games.jsonl") if g.get("status") == "finished"]
    if live:  # games played on the live server, shown first
        data["match"] = data["match"] or {"games": [], "totals": {}}
        data["match"]["live_games"] = live
    if shop_runs is not None:
        data["shop"] = shop_data(shop_runs)
    data["terminal"] = terminal_data()
    return {k: v for k, v in data.items() if v}


def render(data):
    """The page with this data inlined (the publish step adds the document skeleton)."""
    template = (ROOT / "dashboard/site.html").read_text()
    payload = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    marker = '<script type="application/json" id="data">{}</script>'
    if template.count(marker) != 1:
        raise ValueError("dashboard/site.html is missing its data block")
    return template.replace(marker, f'<script type="application/json" id="data">{payload}</script>')


ICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' "
        "rx='7' fill='%232a43d4'/%3E%3Cpath d='M9 9l7 15 7-15' fill='none' stroke='white' stroke-width='3.2' "
        "stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E")
DESCRIPTION = ("Verity audits RL environments for reward hacks with a frontier model, turns each proven hack into a rule, "
               "and stops training from paying for it.")


def document(data):
    """The page as a complete HTML document: the template's title, fonts and styles go in the head."""
    page = render(data)
    split = page.index('<header class="topbar">')
    return ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            f'<meta name="description" content="{DESCRIPTION}">\n<meta property="og:title" content="Verity">\n'
            f'<meta property="og:description" content="{DESCRIPTION}">\n<meta name="theme-color" content="#ffffff">\n'
            f'<link rel="icon" href="{ICON}">\n<style>body{{margin:0}}[hidden]{{display:none!important}}</style>\n'
            f'{page[:split]}</head>\n<body>\n{page[split:]}\n</body>\n</html>\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", default="chess_game")
    parser.add_argument("--runs", default="artifacts/chess_game-v3")
    parser.add_argument("--shop-runs", default="artifacts/shop-v2", help="the shop case study's runs ('' to leave it out)")
    parser.add_argument("--out", default=str(ROOT / "web/index.html"))
    args = parser.parse_args(argv)
    data = build(args.env, args.runs, args.shop_runs or None)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(document(data))
    (out.parent / "example_env.py").write_text((ROOT / "docs/example_env.py").read_text())  # to try the upload with
    print(f"Wrote {out} ({out.stat().st_size // 1024} KB): " + ", ".join(sorted(data)))


if __name__ == "__main__":
    main()
