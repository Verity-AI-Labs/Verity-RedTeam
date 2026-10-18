"""The inference server behind the Verity site: both trained models of each demo, playing live.

    python dashboard/live.py --chess-runs artifacts/chess_game-v3 --shop-runs artifacts/shop-v1   # http://127.0.0.1:8770

One base model with a LoRA adapter per demo and arm (chess-baseline, chess-verity, shop-baseline, shop-verity).
- Chess: the two chess models play one game against each other as deployed (scripts/match.py: move and
  legal_moves only, illegal moves retried, no legal move in 4 turns loses on time); Stockfish scores every move.
- Shop: the two shop models work the same deployment store side by side (a store, products, words and layout
  they never trained on), one model turn each per round; the site renders each store page after every action.
No Guard runs here: this is how the trained models behave once deployed.

The page polls GET /api/live?demo=chess|shop and starts or stops with POST /api/live?demo=...&action=start|stop.
The hosted site (web/api/live.js) forwards to this server; with --token, POST needs the X-Verity-Token header.
Needs the adapters in <runs>/<arm>/adapter, and Stockfish for chess (STOCKFISH=/path or on PATH).
"""
import argparse
import json
import os
import random
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import chess

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dashboard import site  # noqa: E402
from environments import chess_game, shop  # noqa: E402
from evaluate import TOOL_CALL, Policy, run_call, tool_calls, tool_names  # noqa: E402
import scripts.match as match  # noqa: E402

ARMS = ("baseline", "verity")
LIVE_ROUNDS = 10  # live games: tries per move for both models (an illegal move is refused); the evaluation uses 4
# well-known openings a live game can start from (the models move deterministically, so the start varies the game)
OPENINGS = {"Italian Game": "e4 e5 Nf3 Nc6 Bc4", "Ruy Lopez": "e4 e5 Nf3 Nc6 Bb5", "Sicilian Defence": "e4 c5 Nf3 d6",
            "French Defence": "e4 e6 d4 d5", "Caro-Kann Defence": "e4 c6 d4 d5", "Scandinavian Defence": "e4 d5 exd5 Qxd5",
            "Queen's Gambit Declined": "d4 d5 c4 e6", "King's Indian Defence": "d4 Nf6 c4 g6 Nc3 Bg7",
            "Nimzo-Indian Defence": "d4 Nf6 c4 e6 Nc3 Bb4", "London System": "d4 d5 Bf4 Nf6", "English Opening": "c4 e5 Nc3 Nf6",
            "Reti Opening": "Nf3 d5 g3 Nf6"}


def opening_fen(moves):
    board = chess.Board()
    for move in moves.split():
        board.push_san(move)
    return board.fen()


class Prefixed:
    """One demo's view of the shared model: its arms are adapters named '<demo>-<arm>'."""

    def __init__(self, policy, demo):
        self.policy, self.demo = policy, demo

    def chat(self, messages, arm="base", tools=None):
        return self.policy.chat(messages, arm if arm == "base" else f"{self.demo}-{arm}", tools)


class Chess:
    """One live game at a time between the two chess models."""

    def __init__(self, policy, runs):
        self.policy, self.runs = Prefixed(policy, "chess"), Path(runs)
        self.starts = [task["fen"] for task in chess_game.tasks("eval")]
        self.lock, self.game, self.thread = threading.Lock(), None, None

    def start(self, body):
        """Start a game; one in progress is replaced (it stops after its current move). Returns an error or None."""
        with self.lock:
            if self.game and self.thread and self.thread.is_alive():
                self.game.update(stop=True, status="stopped", reason="replaced by a new game")
            white = body.get("white") if body.get("white") in ARMS else "verity"
            if body.get("start") == "heldout":
                fen, opening = random.choice(self.starts), None
            else:  # a random well-known opening, so each game differs
                opening = random.choice(list(OPENINGS))
                fen = opening_fen(OPENINGS[opening])
            self.game = {"white": white, "black": "baseline" if white == "verity" else "verity", "start_fen": fen,
                         "opening": opening, "plies": [], "status": "playing", "live": True}
            self.thread = threading.Thread(target=self.play, args=(self.game,), daemon=True)
            self.thread.start()
            return None

    def play(self, game):
        try:
            for row in match.game(self.policy, game["white"], game["black"], 0, fen=game["start_fen"], rounds=LIVE_ROUNDS):
                if game.get("stop"):
                    game.update(status="stopped", reason="stopped from the page")
                    return
                if row["event"] == "move":
                    game["plies"].append(site.ply_view(row))
                elif row["event"] == "forfeit":
                    game["forfeit"] = {k: row[k] for k in ("policy", "transcript", "illegal", "unknown_tool_calls")}
                else:
                    game.update({k: row[k] for k in ("result", "winner", "reason")}, status="finished")
                    with (self.runs / "live-games.jsonl").open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps({k: v for k, v in game.items() if k != "stop"}) + "\n")
        except Exception as error:  # shown on the page; the server keeps running
            game.update(status="error", reason=f"{type(error).__name__}: {error}")

    def stop(self):
        if self.game:
            self.game["stop"] = True

    def state(self):
        game = self.game
        return {"available": True, "game": None if game is None else {**game, "plies": list(game["plies"])}}


class Shop:
    """One live store at a time: both shop models get the same deployment task and act in turn."""

    def __init__(self, policy, runs):
        self.policy, self.runs = Prefixed(policy, "shop"), Path(runs)
        self.tasks = shop.tasks(shop.DEPLOYMENT["split"])
        from transformers.utils import get_json_schema
        self.tools = [get_json_schema(getattr(shop.Episode(), name)) for name in tool_names(shop.Episode)]
        self.lock, self.run, self.thread = threading.Lock(), None, None

    def start(self, body):
        """Start a task on a store: a request in plain words ("text"), a composed one ("row"), or a random held-out task.
        A task in progress is replaced. Returns an error the page can show, or None."""
        with self.lock:
            index = body.get("store", body.get("task"))
            index = index if isinstance(index, int) and 0 <= index < len(self.tasks) else random.randrange(len(self.tasks))
            task, request, understood = self.tasks[index], str(body.get("text") or "").strip()[:300], None
            if request:  # the shopper's words, read against this store's catalogue, become the order both agents get
                base = json.loads(task["site"])
                try:
                    found = shop.interpret(base, request)
                except ValueError as error:
                    return str(error)
                site, understood = shop.compose(base, found["row"], found["shipping"], found["coupon"]), found["understood"]
                task = {"prompt": shop.prompt(site), "site": json.dumps(site), "key": shop.key(site)}
            elif isinstance(body.get("row"), int):  # a task composed on the page: this store, this item, these options
                site = shop.compose(json.loads(task["site"]), body["row"], body.get("shipping", "standard"),
                                    bool(body.get("coupon")))
                task = {"prompt": shop.prompt(site), "site": json.dumps(site), "key": shop.key(site)}
            if self.run and self.thread and self.thread.is_alive():
                self.run.update(stop=True, status="stopped", reason="replaced by a new task")
            site = json.loads(task["site"])
            self.run = {"task": index, "goal": site["goal"], "store": site["store"], "status": "playing",
                        "request": request or None, "understood": understood,
                        "arms": {arm: {"frames": [], "done": False} for arm in ARMS}}
            self.thread = threading.Thread(target=self.play, args=(self.run, task), daemon=True)
            self.thread.start()
            return None

    def play(self, run, task):
        try:
            episodes, chats = {}, {}
            for arm in ARMS:
                episodes[arm] = shop.Episode()
                first = episodes[arm].reset(**task)
                episodes[arm].unavailable = 0  # calls to tools that do not exist (run_call counts them)
                chats[arm] = [{"role": "user", "content": task["prompt"] + first}]
                run["arms"][arm].update(page=first, first_page=first)
            for _ in range(shop.MAX_TOOL_CALLS):
                for arm in ARMS:
                    state, episode = run["arms"][arm], episodes[arm]
                    if run.get("stop"):
                        run.update(status="stopped")
                        return
                    if state["done"]:
                        continue
                    reply = self.policy.chat(chats[arm], arm, self.tools)
                    calls = tool_calls(reply)
                    said = re.sub(r"<think>.*?</think>", "", TOOL_CALL.sub("", reply), flags=re.S).strip()
                    chats[arm].append({"role": "assistant", "content": said,
                                       **({"tool_calls": [{"type": "function", "function": c} for c in calls]} if calls else {})})
                    for call in calls:
                        result = str(run_call(episode, shop.TOOLS, call))
                        chats[arm].append({"role": "tool", "name": call["name"], "content": result})
                        state["frames"].append({"call": call["name"], "arguments": call["arguments"],
                                                "result": "page: " + episode.state["page"] if result.startswith("<h1>") else result,
                                                "page": shop.render(episode.site, episode.state)})
                        state["page"] = shop.render(episode.site, episode.state)
                    if said and not calls:
                        state["said"] = said[:300]
                    if not calls or shop.done(episode):
                        state.update(self.result(episode))
                if all(run["arms"][arm]["done"] for arm in ARMS):
                    break
            for arm in ARMS:  # out of actions
                if not run["arms"][arm]["done"]:
                    run["arms"][arm].update(self.result(episodes[arm]))
            run["status"] = "finished"
            with (self.runs / "live-stores.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({k: v for k, v in run.items() if k != "stop"}) + "\n")
        except Exception as error:
            run.update(status="error", reason=f"{type(error).__name__}: {error}")

    @staticmethod
    def result(episode):
        """How the episode ended: the order as placed, whether it was exactly what was asked, and the shop's numbers."""
        return {**shop.measure(episode), "done": True, "order": shop.order_line(episode.state)[len("# order: "):],
                "outcome": shop.check(episode), "reward": shop.grade(episode)}

    def stop(self):
        if self.run:
            self.run["stop"] = True

    def state(self):
        run = self.run
        return {"available": True, "run": None if run is None else json.loads(json.dumps(
            {k: v for k, v in run.items() if k != "stop"}))}


def handler(demos, page, token=None):
    class Handler(BaseHTTPRequestHandler):
        def send(self, code, body, kind="application/json"):
            data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", kind + "; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def demo(self):
            url = urlparse(self.path)
            query = parse_qs(url.query)
            return url.path, query.get("demo", ["chess"])[0], query.get("action", [""])[0]

        def do_GET(self):
            path, name, _ = self.demo()
            if path in ("/", "/index.html") and page:
                return self.send(200, page, "text/html")
            if path == "/api/live":
                return self.send(200, demos[name].state() if name in demos else {"available": False})
            self.send(404, {"error": "not found"})

        def do_POST(self):
            length = min(int(self.headers.get("Content-Length") or 0), 4096)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self.send(400, {"error": "invalid JSON"})
            if token and self.headers.get("X-Verity-Token") != token:
                return self.send(403, {"error": "missing or wrong X-Verity-Token"})
            path, name, action = self.demo()
            if path != "/api/live" or name not in demos or action not in ("start", "stop"):
                return self.send(404, {"error": "POST /api/live?demo=chess|shop&action=start|stop"})
            if action == "start":
                error = demos[name].start(body)
                return self.send(400, {**demos[name].state(), "error": error}) if error else self.send(200, demos[name].state())
            demos[name].stop()
            return self.send(200, demos[name].state())

        def log_message(self, *_args):
            pass
    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--chess-runs", help="chess_game runs with baseline/adapter and verity/adapter")
    parser.add_argument("--shop-runs", help="shop runs with baseline/adapter and verity/adapter")
    parser.add_argument("--model", default="Qwen/Qwen3-8B")
    parser.add_argument("--host", default="127.0.0.1", help="127.0.0.1 keeps it on this machine")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--page", default=str(ROOT / "web/index.html"), help="page served at / for local use")
    parser.add_argument("--token", default=os.environ.get("VERITY_TOKEN"), help="required to start or stop")
    args = parser.parse_args(argv)
    runs = {"chess": args.chess_runs, "shop": args.shop_runs}
    adapters = {f"{demo}-{arm}": str(Path(path) / arm / "adapter") for demo, path in runs.items() if path for arm in ARMS}
    if not adapters:
        parser.error("give --chess-runs and/or --shop-runs")
    print(f"Loading {args.model} with {', '.join(adapters)}...", flush=True)
    policy = Policy(args.model, adapters, device=args.device, max_new_tokens=256)
    demos = {}
    if args.chess_runs:
        demos["chess"] = Chess(policy, args.chess_runs)
    if args.shop_runs:
        demos["shop"] = Shop(policy, args.shop_runs)
    page = Path(args.page).read_text() if Path(args.page).is_file() else None
    server = ThreadingHTTPServer((args.host, args.port), handler(demos, page, args.token))
    print(f"Verity live ({', '.join(demos)}): http://{args.host}:{args.port}  (Ctrl-C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        chess_game.close_engines()


if __name__ == "__main__":
    main()
