"""Head-to-head chess between the two trained arms of the chess_game environment, as deployed.

    python scripts/match.py --baseline runs/baseline --verity runs/verity --games 2 --output runs/matches.jsonl

Deployment rules: each turn the side to move gets the deployment prompt for the current position (board,
FEN, legal moves) and only the deployment tools, move() and legal_moves(). Training-only tools such as
hint() do not exist. An illegal move is refused and the model tries again; a model that has not played a
legal move after 4 turns loses on time, as in a real game. Nothing is ever played for a model.

Colors alternate between games. --start opening plays from the initial position; --start heldout from the
environment's held-out positions, each played twice (once with each model as White). Every move is scored
by Stockfish the way the environment scores moves: centipawns lost against the best move, and quality
exp(-loss / 200). Games still going at 80 plies are adjudicated by Stockfish (draw within one pawn).
"""
import argparse
import json
import math
import sys
import time
from pathlib import Path

import chess

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from environments import chess_game as env  # noqa: E402
from evaluate import Policy, play  # noqa: E402
from train import utc_now  # noqa: E402

MAX_PLIES, ROUNDS = 80, 4


class Turn(env.Episode):
    """One player's turn in a game between two agents: move() plays the move and ends the turn."""

    def reset(self, board=None, **_):
        self.board, self.side = board, board.turn
        self.log, self.hints, self.moves, self.illegal, self.played = [], 0, 0, 0, None
        self.quality, self.hinted, self.suggested = [], [], None

    def move(self, move: str) -> str:
        """Play your move; your opponent answers on its turn.

        Args:
            move: Your move in standard notation, for example Nf3, exd5 or O-O (UCI like g1f3 also works).

        Returns:
            Confirmation, or the legal moves if the move is illegal.
        """
        self.log.append(f"move({move})")
        if self._over():
            return "You have already moved."
        chosen = env.parse_move(self.board, move)
        if chosen is None:
            self.illegal += 1
            return f"Illegal move {move}. Legal moves: {' '.join(self.board.san(m) for m in self.board.legal_moves)}"
        self.played = chosen
        return "Move played. Your opponent is thinking."

    def _over(self):
        return self.played is not None or self.board.is_game_over(claim_draw=True)


def game(policy, white, black, number, fen=None):
    """Play one game from fen (default: the initial position); yields a row per ply, then the result row."""
    board, arms = chess.Board(fen or chess.STARTING_FEN), {chess.WHITE: white, chess.BLACK: black}
    before, forfeit = env.evaluation(board, chess.WHITE), None
    while not board.is_game_over(claim_draw=True) and len(board.move_stack) < MAX_PLIES:
        arm, fen, started, mover = arms[board.turn], board.fen(), time.monotonic(), board.turn
        turn = Turn()
        turn.reset(board=board.copy())
        play(policy, env, {"fen": fen}, arm, episode=turn, rounds=ROUNDS, deploy=True)
        if turn.played is None:  # no legal move in 4 turns: loses on time
            forfeit = (arm, turn)
            break
        move = turn.played
        row = {"event": "move", "game": number, "ply": len(board.move_stack) + 1,
               "color": "white" if mover == chess.WHITE else "black", "policy": arm, "fen_before": fen,
               "transcript": turn.transcript, "uci": move.uci(), "san": board.san(move), "fallback": False,
               "hint_requests": turn.unavailable, "illegal": turn.illegal,
               "latency_ms": round(1000 * (time.monotonic() - started))}
        board.push(move)
        after = env.evaluation(board, chess.WHITE)
        loss = max(0, (before - after) if mover == chess.WHITE else (after - before))  # centipawns the mover gave away
        row.update(eval_before_white_cp=before, eval_white_cp=after, cp_loss=min(loss, 1000), quality=math.exp(-loss / 200))
        before = after
        yield row
    if forfeit:
        arm, turn = forfeit
        result = "0-1" if board.turn == chess.WHITE else "1-0"
        reason = f"{arm} played no legal move in {ROUNDS} turns and lost on time"
        yield {"event": "forfeit", "game": number, "ply": len(board.move_stack) + 1, "policy": arm,
               "fen_before": board.fen(), "transcript": turn.transcript, "illegal": turn.illegal,
               "unknown_tool_calls": turn.unavailable}
    elif board.is_game_over(claim_draw=True):
        result, reason = board.result(claim_draw=True), board.outcome(claim_draw=True).termination.name.lower()
    else:
        cp = env.evaluation(board, chess.WHITE)
        result = "1/2-1/2" if abs(cp) < 100 else "1-0" if cp > 0 else "0-1"
        reason = f"Stockfish adjudication at {MAX_PLIES} plies: {cp / 100:+.2f}"
    winner = {"1-0": white, "0-1": black}.get(result, "draw")
    yield {"event": "result", "game": number, "white": white, "black": black, "result": result, "winner": winner,
           "reason": reason, "plies": len(board.move_stack), "moves_uci": " ".join(m.uci() for m in board.move_stack)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--baseline", required=True, help="baseline run directory (with adapter/)")
    parser.add_argument("--verity", required=True, help="verity run directory (with adapter/)")
    parser.add_argument("--games", type=int, default=2)
    parser.add_argument("--start", choices=("opening", "heldout"), default="opening")
    parser.add_argument("--eval-data", help="held-out positions (default: the environment's eval split)")
    parser.add_argument("--output", required=True)
    parser.add_argument("--device", default="auto")
    args = parser.parse_args(argv)
    runs = {arm: json.loads((Path(path) / "run.json").read_text()) for arm, path in
            (("baseline", args.baseline), ("verity", args.verity))}
    config = runs["baseline"]["config"]
    if config != runs["verity"]["config"]:
        parser.error("The two runs differ in configuration; refusing a matched comparison")
    policy = Policy(config["model"], {"baseline": str(Path(args.baseline) / "adapter"),
                                      "verity": str(Path(args.verity) / "adapter")},
                    device=args.device, max_new_tokens=256, revision=runs["baseline"].get("model_commit") or config["revision"])
    held_out = [task["fen"] for task in env.tasks("eval", path=args.eval_data)] if args.start == "heldout" else []
    starts = [held_out[n // 2] if held_out else chess.STARTING_FEN for n in range(args.games)]
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"event": "match", "created_at": utc_now(), "games": args.games, "start": args.start,
                                 "model": config["model"], "max_plies": MAX_PLIES, "rounds_per_turn": ROUNDS,
                                 "tools": list(env.DEPLOYMENT["tools"]), "starts": starts}) + "\n")
        for number in range(args.games):
            white, black = ("verity", "baseline") if number % 2 == 0 else ("baseline", "verity")
            for row in game(policy, white, black, number, fen=starts[number]):
                stream.write(json.dumps(row) + "\n")
                stream.flush()
                if row["event"] == "result":
                    print(f"game {number}: {white} (white) vs {black} (black): {row['result']} ({row['reason']})", flush=True)
    env.close_engines()


if __name__ == "__main__":
    main()
