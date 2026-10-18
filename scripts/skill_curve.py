"""Real skill over training: deployment games on held-out positions for saved checkpoints of both arms.

    python scripts/skill_curve.py --runs runs/chess_game-8b --steps 0 50 100 --tasks 24 --output runs/skill-curve.json

Step 0 is the untrained model. Every checkpoint plays the same held-out positions with only the
environment's deployment tools (evaluate.play with deploy=True), greedy decoding. Results are appended
to the output as they finish, so a partial curve is usable.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluate import Policy, evaluate_episodes  # noqa: E402
from train import utc_now  # noqa: E402
from verity import environment  # noqa: E402


def adapter(run, step):
    path = run / f"checkpoint-{step}"
    if not (path / "adapter_config.json").is_file():
        path = run / "adapter"  # the final step is also saved as adapter/
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", default="chess_game")
    parser.add_argument("--runs", required=True, help="directory with baseline/ and verity/ runs")
    parser.add_argument("--steps", type=int, nargs="+", required=True)
    parser.add_argument("--tasks", type=int, default=24)
    parser.add_argument("--eval-data")
    parser.add_argument("--output", required=True)
    parser.add_argument("--device", default="auto")
    args = parser.parse_args(argv)
    env, runs = environment.load(args.env), Path(args.runs)
    config = json.loads((runs / "baseline" / "run.json").read_text())["config"]
    tasks = env.tasks("eval", path=args.eval_data)[:args.tasks]
    output = Path(args.output)
    for step in args.steps:
        adapters = {} if step == 0 else {arm: str(adapter(runs / arm, step)) for arm in ("baseline", "verity")}
        policy = Policy(config["model"], adapters, device=args.device, max_new_tokens=256)
        for arm in (["base"] if step == 0 else ["baseline", "verity"]):
            result = evaluate_episodes(policy, env, tasks, arm, deploy=True)
            row = {"created_at": utc_now(), "step": step, "arm": arm, "tasks": len(tasks),
                   **{k: v for k, v in result.items() if k != "samples"}}
            with output.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row) + "\n")
            print(json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in row.items()}), flush=True)
        del policy


if __name__ == "__main__":
    main()
