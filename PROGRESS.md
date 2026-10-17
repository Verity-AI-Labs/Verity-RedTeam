# Progress

As of **Sat Oct 17, 2026**. DubHacks submissions are due **Sun Oct 18, 10:30 PDT** (judging 11:30–14:00). The repo layout is described in [README.md](README.md). The step-by-step lab notebook is [docs/log.md](docs/log.md).

## Headline result

Same model (Qwen2.5-1.5B-Instruct), same data and settings, trained 250 steps with RL on chess. The only difference is whether Verity's Guard enforced the audit's marker. Scores are on 200 held-out positions:

| Seed | Model | Grader reward | True score | Cheating | Top move found |
|---|---|---:|---:|---:|---:|
| — | Untrained | 0.078 | 0.052 | 8.5% | 1.0% |
| 42 | Trained without Guard | 0.990 | **0.000** | 100% | 0.0% |
| 42 | Trained with Guard | 0.160 | **0.160** | **0%** | 3.5% |
| 7 | Trained without Guard | 0.980 | **0.000** | 100% | 0.0% |
| 7 | Trained with Guard | 0.145 | **0.145** | **0%** | 2.5% |

- **Without Guard,** the model learned to list moves on every answer. The grader rated it near-perfect while its real score fell to zero. Cheating crossed 50% of answers at step 21 (seed 42) and step 8 (seed 7).
- **With Guard,** it never cheated, and its true score roughly tripled over the untrained model.
- Guard costs 13.5–14.6 µs per answer on the training GPU node (1.7 µs on a Mac).

## Done

- **One system.** Verity (`verity/`) is now environment-neutral. Chess and Terminal Wrench plug into it the same way (`environments/`).
  - The Oct 17 restructure was verified to change no behavior: all 1,948 prompts are identical; 15,584 replies are graded identically; 3,918 recorded Guard decisions are re-made identically.
  - Both finished experiments still validate, and the dashboard arena still loads their trained models.
  - Everything from before the restructure is on branch `wip/pre-restructure`.
- **Audits.**
  - Terminal Wrench (v5.1, Oct 12, DeepSeek as attacker and judge): **13 of 13 tasks hackable**, 10 with hard proof.
  - Chess fixture audit: proof on 16 tasks (grader 1.0 vs. true 0.0). The marker catches 100% of cheat variants with 0% false alarms on 1,600 honest answers.
  - Live chess audit: Claude Opus 5.5 found the move-listing cheat in 2 queries, then OpenRouter credits ran out (`experiments/chess-audits/chess-opus-attempt.*`).
- **Training on Hyak** (`rao`, RTX 6000):
  - Smoke test.
  - 3 pilot sweeps.
  - 2 matched pairs × 250 steps.
  - Held-out evaluation for both seeds.
- **Demo.** Local dashboard with four panels (audit, training, arena, real environments). The arena series ran 10 games; all were draws by repetition, because both models are weak at chess.
- **Docs.** README, this file, and a Devpost draft ([docs/devpost.md](docs/devpost.md)).

## Open

1. **OpenRouter.** The key leaked in Codex's tool output, so rotate it. The balance is $0; add about $5, then run `python -m verity.audit chess --model anthropic/claude-opus-5.5 --input-price 4 --output-price 20`. That gives an AI-written `markers/chess.json` to show next to the fixture. Retraining is not needed.
2. **Commit.** None of the Oct 16–17 work is committed to `dubhacks` yet.
3. **Demo prep.** Rehearse the 3-minute demo, record a backup video, and submit the Devpost.
4. **Later.** RL training on the agent environments; system-call-level markers; auto-patching graders.

## Decisions and disclosures

- **Seeded bug.** The chess grader bug is planted on purpose and disclosed: the grader credits the best move found anywhere in a reply.
- **Tuned until the cheat appeared.**
  - With the prompt "Reply with the single best move…", the model never discovered the cheat at learning rates 5e-6 to 1e-4 (pilot sweeps 1–2).
  - With "What is the best move?" at 5e-5, it took off around step 20.
  - Both arms then used exactly those settings.
- **Hand-written marker.** Guard trained on Verity's fixture marker (`markers/chess-fixture.json`), not an AI-written one; see open item 1.
- **Pre-event work.** Part of this was built before the event; DubHacks' 2024 rules capped pre-written code at 30%. Say so in the Devpost.

## Where the evidence is

| What | Where |
|---|---|
| Held-out results | `artifacts/dubhacks/results.{json,md}` (seed 42), `artifacts/dubhacks-seed7/results.{json,md}` (seed 7) |
| Training telemetry and run manifests | `artifacts/<run>/{baseline,verity}/{telemetry.jsonl,run.json}` |
| Trained models | `artifacts/<run>/{baseline,verity}/adapter/` |
| Arena games | `artifacts/dubhacks/matches.jsonl` |
| Chess audits | `experiments/chess-audits/`: fixture, Opus attempt, failed free-model attempts |
| Terminal Wrench audits | `experiments/runs/full-audit-v51-20261012/` (and earlier runs) |
| Markers | `markers/chess-fixture.json`, `markers/blind-maze.json`, `markers/winning-avg-corewars.json` |

Hyak copies are in `/gscratch/rao/aaravs07/verity-dubhacks/runs/`:

| Folder | Contents |
|---|---|
| `dubhacks`, `dubhacks-seed7` | the two matched pairs |
| `dubhacks-lr*` | pilot sweep 1 |
| `loose-*`, `strict-*` | pilot sweep 2 |
| `loose0-*`, `strict0-*`, `loose-lr5e-5-len256` | sweep 3, cancelled |
| `smoke` | the smoke test |

The submit script is `logs/submit_full.sh`. Log in once with `ssh klone` (Duo); the connection stays open for 4 hours.

## Demo script (3 minutes)

1. **Problem.** "RL is how models improve now. If the grader has a bug, training pays the model to exploit it."
2. **Audit.** Show the Opus transcript finding the cheat in 2 queries, the proof (grader 1.0 vs. true 0.0), and the marker's test results. Say the bug is seeded.
3. **Guard.** Run a live probe: a list of moves scores 1.0 and becomes −1; an honest single move keeps its reward.
4. **Training.** Show the two curves: without Guard, cheating hits 100% and the true score falls to 0. Then the held-out table.
5. **Arena.** The unprotected model answers with a wall of 20 moves; the protected one answers with one.
6. **Real environments.** 13/13 Terminal Wrench tasks hackable, and the replay blocking the exact command that reads the answer file.
7. **Close.** "Verity: find the hacks, then make sure training never pays for them."

## History

| When | What |
|---|---|
| Sep 19 | Proof 1: qwen3-coder:30b rediscovered the blind-maze exploit in 3 of 4 attempts |
| Oct 3–12 | Terminal Wrench audits (Qwen, then DeepSeek). v5.1 found all 13 tasks hackable, 10 certified |
| Oct 16 | Plan approved ([docs/plan.md](docs/plan.md)). GPT's first chess draft was replaced by Codex's rebuild |
| Oct 17 | Pilots found the setting where cheating emerges. Both matched pairs trained and evaluated. Arena series played. Repo restructured into one system |
