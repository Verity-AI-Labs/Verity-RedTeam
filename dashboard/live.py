"""Live games between the two trained models: the inference server behind the Verity site.

    python dashboard/live.py --runs artifacts/chess_game-v3      # then open http://127.0.0.1:8770

Serves the Verity page and its live-game API. Both models are loaded once (one base model, two LoRA
adapters). A game runs in a background thread with the same code as the recorded match (scripts/match.py),
and every move streams to the page as it is played. The hosted site (web/) reaches this server through its
own API route; with --token, starting or stopping a game needs the X-Verity-Token header. Needs the trained
adapters in <runs>/<arm>/adapter and Stockfish (STOCKFISH=/path/to/stockfish, or on PATH).

API: GET /api/live -> {"available": true, "game": ...}; POST /api/live?action=start {"white", "start"};
POST /api/live?action=stop.
"""
import argparse
import json
import os
import random
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import chess

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dashboard import site  # noqa: E402
from environments import chess_game as env  # noqa: E402
from evaluate import Policy  # noqa: E402
import scripts.match as match  # noqa: E402

class Live:
    """One game at a time; the page polls state()."""

    def __init__(self, runs, device):
        runs = Path(runs)
        config = json.loads((runs / "baseline" / "run.json").read_text())["config"]
        self.policy = Policy(config["model"], {arm: str(runs / arm / "adapter") for arm in ("baseline", "verity")},
                             device=device, max_new_tokens=256)
        self.starts = [task["fen"] for task in env.tasks("eval")]
        self.record = runs / "live-games.jsonl"  # finished games, so the hosted page can replay them
        self.lock, self.game, self.thread = threading.Lock(), None, None

    def start(self, white, start):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return False
            fen = chess.STARTING_FEN if start == "opening" else random.choice(self.starts)
            self.game = {"white": white, "black": "baseline" if white == "verity" else "verity", "start_fen": fen,
                         "plies": [], "status": "playing", "live": True}
            self.thread = threading.Thread(target=self.play, args=(self.game,), daemon=True)
            self.thread.start()
            return True

    def play(self, game):
        try:
            for row in match.game(self.policy, game["white"], game["black"], 0, fen=game["start_fen"]):
                if game.get("stop"):
                    game.update(status="stopped", reason="stopped from the page")
                    return
                if row["event"] == "move":
                    game["plies"].append(site.ply_view(row))
                elif row["event"] == "forfeit":
                    game["forfeit"] = {k: row[k] for k in ("policy", "transcript", "illegal", "unknown_tool_calls")}
                else:
                    game.update({k: row[k] for k in ("result", "winner", "reason")}, status="finished")
                    with self.record.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps({k: v for k, v in game.items() if k != "stop"}) + "\n")
        except Exception as error:  # shown on the page; the server keeps running
            game.update(status="error", reason=f"{type(error).__name__}: {error}")

    def stop(self):
        if self.game:
            self.game["stop"] = True

    def state(self):
        game = self.game
        return {"available": True, "game": None if game is None else {**game, "plies": list(game["plies"])}}


def handler(live, page, token=None):
    class Handler(BaseHTTPRequestHandler):
        def send(self, code, body, kind="application/json"):
            data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", kind + "; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                return self.send(200, page, "text/html")
            if self.path.split("?")[0] == "/api/live":
                return self.send(200, live.state())
            self.send(404, {"error": "not found"})

        def do_POST(self):
            length = min(int(self.headers.get("Content-Length") or 0), 4096)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self.send(400, {"error": "invalid JSON"})
            if token and self.headers.get("X-Verity-Token") != token:
                return self.send(403, {"error": "missing or wrong X-Verity-Token"})
            action = parse_qs(urlparse(self.path).query).get("action", [""])[0]
            if self.path == "/api/live/start" or action == "start":
                white = body.get("white") if body.get("white") in ("verity", "baseline") else "verity"
                start = body.get("start") if body.get("start") in ("opening", "heldout") else "opening"
                return self.send(200 if live.start(white, start) else 409, live.state())
            if self.path == "/api/live/stop" or action == "stop":
                live.stop()
                return self.send(200, live.state())
            self.send(404, {"error": "not found"})

        def log_message(self, *_args):
            pass
    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", default="artifacts/chess_game-v3")
    parser.add_argument("--host", default="127.0.0.1", help="127.0.0.1 keeps it on this machine")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--token", default=os.environ.get("VERITY_TOKEN"), help="required to start or stop games")
    args = parser.parse_args(argv)
    page = site.document(site.build("chess_game", args.runs))
    print("Loading both models...", flush=True)
    live = Live(args.runs, args.device)
    server = ThreadingHTTPServer((args.host, args.port), handler(live, page, args.token))
    print(f"Verity live: http://{args.host}:{args.port}  (Ctrl-C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        env.close_engines()


if __name__ == "__main__":
    main()
