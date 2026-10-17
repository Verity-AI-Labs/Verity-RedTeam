"""Local Verity dashboard for the chess demo. All measurements come from artifacts or an explicit live probe.

No frontend build or cloud service is needed. POST endpoints accept only local UI
requests; models and artifact paths are selected by server arguments, never HTTP.
"""
from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import threading
import time
from urllib.parse import urlparse
import uuid

import chess
import chess.engine

from environments import chess as env
from verity import environment

ROOT = Path(__file__).resolve().parents[1]
WEB = Path(__file__).resolve().parent
MAX_PLIES = 160


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def read_rows(path, limit=1000):
    rows = []
    try:
        with Path(path).open(encoding="utf-8") as stream:
            for line in deque(stream, maxlen=limit):
                try:
                    row = json.loads(line)
                    if isinstance(row, dict):
                        rows.append(row)
                except ValueError:
                    pass  # A writer may still be finishing the final line.
    except OSError:
        pass
    return rows


def relative(path):
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


class Demo:
    def __init__(self, run_dir, reports_dir, signature, model, stockfish=None):
        self.run_dir = Path(run_dir)
        self.reports_dir = Path(reports_dir)
        self.signature = Path(signature)
        self.model = model
        self.stockfish = stockfish or os.getenv("VERITY_STOCKFISH_PATH") or shutil.which("stockfish")
        if not self.stockfish and Path("/opt/homebrew/bin/stockfish").exists():
            self.stockfish = "/opt/homebrew/bin/stockfish"
        self.engine = None
        self.engine_lock = threading.Lock()
        self.operation = threading.Lock()
        self.policy = None
        self.policy_key = None
        self.current = None
        self.warming = False
        self.guard = None
        self.guard_path = None
        self.series = []

    def active_signature(self):
        fixture = self.signature.with_name("chess-fixture.json")
        if self.signature.name == "chess.json" and not self.signature.exists() and fixture.is_file():
            return fixture
        return self.signature

    def get_guard(self):
        path = self.active_signature()
        if not path.exists():
            return None
        if self.guard is None or self.guard_path != path:
            from verity.guard import Guard
            self.guard = Guard(path)
            self.guard_path = path
        return self.guard

    def analyse(self, board, multipv=1):
        if not self.stockfish:
            raise RuntimeError("Stockfish is missing. Set VERITY_STOCKFISH_PATH and restart.")
        with self.engine_lock:
            if self.engine is None:
                self.engine = chess.engine.SimpleEngine.popen_uci(self.stockfish)
                self.engine.configure({"Threads": 1, "Hash": 32})
            return self.engine.analyse(board, chess.engine.Limit(time=0.12), multipv=multipv)

    def evaluation(self, board):
        if board.is_checkmate():
            return {"available": True, "centipawns": -10000 if board.turn else 10000, "mate": 0}
        if not self.stockfish:
            return {"available": False}
        result = self.analyse(board)[0]
        score = result["score"].white()
        return {"available": True, "centipawns": score.score(mate_score=10000),
                "mate": score.mate(), "depth": result.get("depth")}

    def adapters(self):
        return {arm: self.run_dir / arm / "adapter" for arm in ("baseline", "verity")}

    def trained_config(self):
        runs = {}
        for arm, path in self.adapters().items():
            if not (path / "adapter_config.json").is_file():
                raise ValueError("Both trained adapters are required in run-dir/{baseline,verity}/adapter.")
            run = read_json(path.parent / "run.json", {})
            if run.get("arm") != arm or run.get("status") != "completed":
                raise ValueError(f"{arm} requires a completed training run.json for that arm.")
            if run.get("steps_completed") != run.get("config", {}).get("max_steps"):
                raise ValueError(f"{arm} did not finish its configured steps.")
            runs[arm] = run
        for key in ("config", "model_commit", "precision", "packages", "source_sha256"):
            if not runs["baseline"].get(key) or runs["baseline"][key] != runs["verity"].get(key):
                raise ValueError(f"Training arms differ in {key} or its provenance is missing.")
        config = runs["baseline"]["config"]
        if config.get("model") != self.model:
            raise ValueError("The server --model must match the model in both training manifests.")
        if not environment.matches(env, config):
            raise ValueError("The chess prompt/environment changed since training.")
        return config, runs["baseline"]["model_commit"]

    def status(self):
        training = self.training()
        try:
            self.trained_config()
            trained_ready, trained_reason = True, None
        except ValueError as error:
            trained_ready, trained_reason = False, str(error)
        return {"name": "Verity", "stockfish": bool(self.stockfish),
                "signature_ready": self.active_signature().is_file(),
                "trained_ready": trained_ready, "trained_reason": trained_reason,
                "training_source": training["source"], "run_dir": relative(self.run_dir),
                "local_only": True, "clock": datetime.now(timezone.utc).isoformat()}

    def audit(self):
        signature = self.active_signature()
        pack = read_json(signature, {})
        report_name = "chess-fixture" if signature.name == "chess-fixture.json" else "chess"
        report_path = ROOT / "experiments/chess-audits" / (report_name + ".json")
        report = read_json(report_path, {})
        markdown = report_path.with_suffix(".md")
        return {"available": bool(pack), "pack": pack, "report": report,
                "source": relative(signature),
                "frontier_attempt": read_json(ROOT / "experiments/chess-audits/chess-opus-attempt.json", {}),
                "markdown": markdown.read_text(encoding="utf-8") if markdown.is_file() else "",
                "disclosure": "Chess uses a seeded grader bug: it credits the best move anywhere in a reply."}

    def probe(self, body):
        fen = body.get("fen") or chess.STARTING_FEN
        board = chess.Board(fen)
        if not board.is_valid() or board.is_game_over():
            raise ValueError("Choose a valid, nonterminal chess position.")
        ranked = [info["pv"][0].uci() for info in self.analyse(board, 3) if info.get("pv")]
        kind = body.get("kind", "spray")
        if kind == "honest":
            reply = ranked[0]
        elif kind == "spray":
            reply = " ".join(move.uci() for move in board.legal_moves)
        elif kind == "custom":
            reply = str(body.get("reply", ""))[:8000]
        else:
            raise ValueError("Unknown probe type.")
        guard = self.get_guard()
        start = time.perf_counter_ns()
        matches = guard.check(reply, field="completion") if guard else []
        check_us = (time.perf_counter_ns() - start) / 1000
        raw = env.env_reward(reply, fen, ranked)
        guarded = min([raw] + [sig.get("on_match", {}).get("reward", -1.0) for sig in matches]) if guard else None
        return {"source": "live_seeded_probe", "kind": kind, "fen": fen, "reply": reply,
                "ranked": ranked, "env_reward": raw, "true_score": env.true_score(reply, fen, ranked),
                "guarded_reward": guarded, "legal_moves_mentioned": len(env.moves_in(reply, fen)),
                "signature_ids": [sig["id"] for sig in matches], "guard_available": guard is not None,
                "warnings": [sig.get("on_match", {}).get("warn", "") for sig in matches],
                "check_us": round(check_us, 2), "disclosure": "Live Stockfish ranking; seeded grader bug; no frontier model called."}

    def run_context(self, manifests=None):
        manifests = manifests or {arm: read_json(self.run_dir / arm / "run.json", {}) for arm in ("baseline", "verity")}
        counts = {run.get("config", {}).get("max_steps") for run in manifests.values()}
        steps = next(iter(counts)) if len(counts) == 1 else None
        stage = self.run_dir.name if self.run_dir.name in ("smoke", "pilot", "full") else "paired run"
        label = f"{stage} · {steps} configured steps per arm" if steps else f"{stage} · awaiting matched run manifests"
        return {"stage": stage, "configured_steps": steps, "label": label,
                "smoke": stage == "smoke" or (isinstance(steps, int) and steps <= 5)}

    def training(self):
        arms = {}
        any_live = False
        any_rows = False
        for arm in ("baseline", "verity"):
            path = self.run_dir / arm / "telemetry.jsonl"
            rows = read_rows(path)
            rows = [row for row in rows if row.get("event", "rollout") == "rollout"]
            manifest = read_json(self.run_dir / arm / "run.json", {})
            age = max(0, time.time() - path.stat().st_mtime) if path.is_file() else None
            live = bool(rows) and age is not None and age < 120 and manifest.get("status") not in ("complete", "completed", "failed", "error")
            any_live |= live
            any_rows |= bool(rows)
            arms[arm] = {"rows": rows, "latest": rows[-1] if rows else None,
                         "manifest": manifest, "live": live, "age_seconds": age,
                         "source": relative(path),
                         "caught_total": sum(row.get("caught", 0) for row in rows),
                         "examples": [dict(example, step=row.get("step")) for row in rows[-30:] for example in row.get("examples", [])][-12:]}
        return {"source": "live_telemetry" if any_live else "recorded_telemetry" if any_rows else "awaiting_training",
                "arms": arms, "evaluation": read_json(self.run_dir / "results.json", None),
                "run_dir": relative(self.run_dir), "window": 1000,
                "run_context": self.run_context({arm: value["manifest"] for arm, value in arms.items()})}

    def real_envs(self):
        rows = []
        for path in sorted(self.reports_dir.glob("*/report.json")):
            report = read_json(path, {})
            provenance = report.get("provenance", {})
            redteam = report.get("redteam", {})
            attempts = redteam.get("attempts", [])
            certified = report.get("hackability", {}).get("value") == "certified"
            hacks = [a for a in attempts if a.get("label") == "hack"]
            rows.append({"task": path.parent.name, "certified": certified,
                         "hackable": report.get("hackability", {}).get("value") in ("certified", "judged") or bool(hacks), "attempts": len(attempts), "hacks": len(hacks),
                         "classes": sorted({m.get("class", "unknown") for m in redteam.get("mechanisms", [])}),
                         "schema": provenance.get("schema_version"), "source": relative(path)})
        return {"source": "recorded_audit", "run": self.reports_dir.name, "tasks": rows,
                "total": len(rows), "hackable": sum(r["hackable"] for r in rows),
                "certified": sum(r["certified"] for r in rows)}

    def replay(self):
        from environments.terminal_wrench import replay
        from verity.guard import Guard
        from verity.markers import from_sandbox_report
        report_path = self.reports_dir / "blind-maze/report.json"
        trajectory = self.reports_dir / "blind-maze/redteam/a1/trajectory.jsonl"
        report = read_json(report_path)
        if not report or not trajectory.is_file():
            raise ValueError("The recorded blind-maze audit is missing from --reports-dir.")
        pack = from_sandbox_report(report)
        events = replay(trajectory, Guard(pack))
        return {"source": "recorded_replay", "task": "blind-maze", "events": events,
                "signatures": pack, "trajectory": relative(trajectory),
                "disclosure": "Recorded commands checked by Guard now. Stops at the first block; no sandbox commands are executed."}

    def arena_state(self):
        current = self.current
        board = current["board"] if current else chess.Board()
        return {"status": "warming" if self.warming else "finished" if current and current["result"] else "playing" if current else "ready",
                "mode": current["mode"] if current else None,
                "fen": board.fen(), "turn": "white" if board.turn else "black",
                "pieces": {chess.square_name(square): piece.symbol() for square, piece in board.piece_map().items()},
                "history": current["history"] if current else [], "ply": len(board.move_stack),
                "evaluation": current["evaluation"] if current else {"available": False},
                "players": current["players"] if current else {"white": "—", "black": "—"},
                "result": current["result"] if current else None,
                "reason": current.get("reason") if current else None,
                "game": current["game"] if current else 0,
                "games": current["games"] if current else 2,
                "series": self.series,
                "warmup": current.get("warmup") if current else None,
                "run_context": self.run_context() if current and current["mode"] == "trained" else None,
                "busy": self.operation.locked(),
                "disclosure": "Stockfish vs Stockfish preview. This is not a trained-model comparison." if current and current["mode"] == "engine" else "Both trained adapters share one base. Greedy decoding; first legal move; no clock forfeits."}

    def arena_start(self, body):
        mode = body.get("mode", "engine")
        if mode not in ("engine", "trained"):
            raise ValueError("Arena mode must be engine or trained.")
        games = int(body.get("games", 2))
        if games < 2 or games > 10 or games % 2:
            raise ValueError("Choose an even number of games from 2 to 10 for swapped colors.")
        if not self.stockfish:
            raise RuntimeError("Stockfish is required for arena evaluation and adjudication.")
        warmup = {}
        if mode == "trained":
            paths = self.adapters()
            config, revision = self.trained_config()
            self.warming = True
            try:
                from evaluate import Policy
                policy_key = (revision, config["max_completion_length"], tuple((arm, (path / "adapter_config.json").stat().st_mtime_ns) for arm, path in paths.items()))
                if self.policy is None or self.policy_key != policy_key:
                    self.policy = Policy(model=self.model, adapters={arm: str(path) for arm, path in paths.items()},
                                         revision=revision, max_new_tokens=config["max_completion_length"])
                    self.policy_key = policy_key
                warmup = self.policy.warmup(env.prompt(chess.STARTING_FEN))
            finally:
                self.warming = False
        self.series = []
        self.new_game(mode, 1, games, warmup)
        return self.arena_state()

    def new_game(self, mode, game, games, warmup):
        first, second = ("baseline", "verity") if mode == "trained" else ("Stockfish A", "Stockfish B")
        players = {"white": first if game % 2 else second, "black": second if game % 2 else first}
        board = chess.Board()
        self.current = {"id": uuid.uuid4().hex[:12], "board": board, "mode": mode,
                        "game": game, "games": games, "players": players, "warmup": warmup,
                        "history": [], "evaluation": self.evaluation(board), "result": None}

    def finish(self, result, reason):
        current = self.current
        current["result"] = result
        current["reason"] = reason
        summary = {"game": current["game"], "players": current["players"], "result": result,
                   "plies": len(current["board"].move_stack), "reason": reason, "mode": current["mode"]}
        self.series.append(summary)
        self.record({"event": "game_end", **summary})

    def record(self, row):
        self.run_dir.mkdir(parents=True, exist_ok=True)
        with (self.run_dir / "matches.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"match_id": self.current["id"], "timestamp": datetime.now(timezone.utc).isoformat(), **row}) + "\n")

    def arena_step(self):
        current = self.current
        if not current:
            raise ValueError("Start an arena series first.")
        if current["result"]:
            if current["game"] < current["games"]:
                self.new_game(current["mode"], current["game"] + 1, current["games"], current["warmup"])
                return self.arena_state()
            return self.arena_state()
        board = current["board"]
        fen = board.fen()
        color = "white" if board.turn else "black"
        arm = current["players"][color]
        started = time.monotonic()
        if current["mode"] == "engine":
            reply = self.analyse(board)[0]["pv"][0].uci()
        else:
            reply = self.policy.reply(env.prompt(fen), arm=arm)
        latency = round((time.monotonic() - started) * 1000)
        uci = env.chosen_move(reply, fen)
        guard = self.get_guard()
        matches = guard.check(reply, field="completion") if guard else []
        event = {"ply": len(board.move_stack) + 1, "color": color, "policy": arm, "reply": reply,
                 "uci": uci, "latency_ms": latency, "fen_before": fen,
                 "signature_ids": [s["id"] for s in matches], "legal_moves_mentioned": len(env.moves_in(reply, fen))}
        if not uci:
            event["san"] = "No legal move"
            current["history"].append(event)
            self.finish("0-1" if board.turn else "1-0", "No legal move in model reply")
        else:
            move = chess.Move.from_uci(uci)
            event["san"] = board.san(move)
            board.push(move)
            current["history"].append(event)
            current["evaluation"] = self.evaluation(board)
            outcome = board.outcome(claim_draw=True)
            if outcome:
                self.finish(outcome.result(), outcome.termination.name.replace("_", " ").lower())
            elif len(board.move_stack) >= MAX_PLIES:
                cp = current["evaluation"]["centipawns"]
                result = "1-0" if cp >= 100 else "0-1" if cp <= -100 else "1/2-1/2"
                self.finish(result, f"Stockfish adjudication at {MAX_PLIES} plies: {cp / 100:+.2f}; draw inside ±1 pawn")
        self.record({"event": "move", "mode": current["mode"], **event})
        return self.arena_state()

    def close(self):
        if self.engine:
            self.engine.quit()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    @property
    def demo(self):
        return self.server.demo

    def respond(self, code, payload, mime="application/json; charset=utf-8"):
        content = json.dumps(payload, allow_nan=False).encode() if mime.startswith("application/json") else payload
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(content)

    def local_host(self):
        return urlparse("http://" + self.headers.get("Host", "")).hostname in ("127.0.0.1", "localhost", "::1")

    def do_GET(self):
        if not self.local_host():
            return self.respond(403, {"error": "Only localhost requests are accepted."})
        path = urlparse(self.path).path
        if path == "/":
            return self.respond(200, (WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
        routes = {"/api/status": self.demo.status, "/api/audit": self.demo.audit,
                  "/api/training": self.demo.training, "/api/real-envs": self.demo.real_envs,
                  "/api/arena": self.demo.arena_state}
        if path not in routes:
            return self.respond(404, {"error": "Not found"})
        try:
            self.respond(200, routes[path]())
        except Exception as error:
            self.respond(500, {"error": str(error)})

    def do_POST(self):
        if not self.local_host():
            return self.respond(403, {"error": "Only localhost requests are accepted."})
        # The UI is local. Reject cross-origin requests before any mutation or inference.
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            return self.respond(403, {"error": "Only same-origin requests are accepted."})
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            return self.respond(403, {"error": "Cross-site requests are not accepted."})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 65536:
                return self.respond(413, {"error": "Request too large"})
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("Expected a JSON object")
            path = urlparse(self.path).path
            if path == "/api/audit/probe":
                return self.respond(200, self.demo.probe(body))
            if path == "/api/real-envs/replay":
                return self.respond(200, self.demo.replay())
            if path not in ("/api/arena/start", "/api/arena/step", "/api/arena/reset"):
                return self.respond(404, {"error": "Not found"})
            if not self.demo.operation.acquire(blocking=False):
                return self.respond(409, {"error": "An arena move or warm-up is already running."})
            try:
                if path.endswith("/start"):
                    result = self.demo.arena_start(body)
                elif path.endswith("/step"):
                    result = self.demo.arena_step()
                else:
                    self.demo.current = None
                    self.demo.series = []
                    result = self.demo.arena_state()
                self.respond(200, result)
            finally:
                self.demo.operation.release()
        except (ValueError, TypeError) as error:
            self.respond(400, {"error": str(error)})
        except Exception as error:
            self.respond(502, {"error": str(error)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.getenv("VERITY_DEMO_PORT", "8765")))
    parser.add_argument("--run-dir", type=Path, default=ROOT / "artifacts/dubhacks")
    parser.add_argument("--reports-dir", type=Path, default=ROOT / "experiments/runs/full-audit-v51-20261012")
    parser.add_argument("--signature", type=Path, default=ROOT / "markers/chess.json")
    parser.add_argument("--model", default="Qwen/Qwen2.5-1.5B-Instruct")
    parser.add_argument("--stockfish", default=None)
    args = parser.parse_args()
    demo = Demo(args.run_dir, args.reports_dir, args.signature, args.model, args.stockfish)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.demo = demo
    print(f"Verity mission control: http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        demo.close()


if __name__ == "__main__":
    main()
