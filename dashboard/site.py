"""Build the Verity site: dashboard/site.html with the real audit, training, evaluation and match data inlined.

    python dashboard/site.py --env chess_game --runs artifacts/chess_game-v3      # writes web/index.html

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
            result = str(seen.get("result", seen.get("error", ""))).split(" Position (FEN)")[0]
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


def build(name, runs):
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
            f'<meta property="og:description" content="{DESCRIPTION}">\n<meta name="theme-color" content="#eef1f4">\n'
            f'<link rel="icon" href="{ICON}">\n<style>body{{margin:0}}[hidden]{{display:none!important}}</style>\n'
            f'{page[:split]}</head>\n<body>\n{page[split:]}\n</body>\n</html>\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", default="chess_game")
    parser.add_argument("--runs", default="artifacts/chess_game-v3")
    parser.add_argument("--out", default=str(ROOT / "web/index.html"))
    args = parser.parse_args(argv)
    data = build(args.env, args.runs)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(document(data))
    print(f"Wrote {out} ({out.stat().st_size // 1024} KB): " + ", ".join(sorted(data)))


if __name__ == "__main__":
    main()
