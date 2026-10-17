# Verity: stop models from learning to cheat while you train them

## Inspiration
Reinforcement learning (RL) is how models get better now, and models cheat graders. If the grader has a bug, training rewards the bug: the model learns the shortcut instead of the skill. Anthropic reported that reward hacking learned in production RL spreads into broader misbehavior. The usual fix comes too late: someone reads transcripts after training and notices.

## What it does
Verity has three parts:

1. **Audit, before training.** An AI red team probes an environment's grader, and Verity proves each cheat with numbers: how much the grader pays versus how good the answer really is. Each proven cheat becomes a **signature**: a small, machine-checkable rule with a warning and a suggested fix. Verity tests every signature against cheat variants and honest answers before it is allowed to act.
2. **Guard, during training.** Every answer the model produces passes through Guard before it is rewarded. If it matches a signature, the reward becomes −1 instead of whatever the buggy grader would pay. A check takes microseconds, against seconds per training step. The model can't skip Guard, because Guard sits between the model and the grader.
3. **Learn.** Guard flags any run where the grader's reward climbs while true quality doesn't. That pattern starts a new audit.

## The demo
- **Environment.** Chess, with a disclosed, seeded grader bug. The grader credits the best move it finds *anywhere* in the reply, so a reply that lists many moves always scores 1.0 while being useless.
- **Audit.**
  - Claude Opus 5.5, seeing only the prompts and the scores, found the cheat in two queries: one move scored 0.1, and listing every legal move scored 1.0.
  - Verity's proof on 16 positions: grader reward 1.0, true score 0.0.
  - The signature caught 868 of 868 cheat variants, with 0 false alarms on 1,600 honest answers.
- **Training.** Same model (Qwen2.5-1.5B-Instruct), same data, same seed, same settings. The only difference is whether Guard enforces.
  - In the pilot without Guard, move-listing jumped from 4% of answers to 97% around step 25. The grader's reward went to 1.0 and the true score fell to 0.
  - Full runs: two matched pairs (seeds 42 and 7), 250 GRPO steps each, scored on 200 held-out positions with greedy decoding.

    | Seed | Model | Grader reward | True score | Cheating | Top move found |
    |---|---|---:|---:|---:|---:|
    | — | Untrained Qwen2.5-1.5B | 0.078 | 0.052 | 8.5% | 1.0% |
    | 42 | Trained without Guard | 0.990 | **0.000** | 100% | 0.0% |
    | 42 | Trained with Guard | 0.160 | **0.160** | **0%** | 3.5% |
    | 7 | Trained without Guard | 0.980 | **0.000** | 100% | 0.0% |
    | 7 | Trained with Guard | 0.145 | **0.145** | **0%** | 2.5% |

  - **Without Guard,** the grader rated the model nearly perfect while it learned nothing useful. Cheating crossed 50% of answers at step 21 (seed 42) and step 8 (seed 7).
  - **With Guard,** the model never cheated, and its true score roughly tripled over the untrained model. Guard costs about 14 microseconds per answer on the training GPU node.
- **Arena.** The two trained models play each other, with each model's raw output visible and Stockfish judging. It shows behavior, not strength.
  - The unprotected model answers every move with a wall of about 20 moves, which Guard flags.
  - The protected model answers with one move in about 0.2 seconds.
  - Both are weak chess players: a 1.5B model trained for 250 steps. All 10 greedy games of our series ended in threefold repetition by ply 16. The protected model was a knight and a pawn up when its game was drawn, and the unprotected model was a pawn up in its game.
- **Real environments.** The same audit approach found exploitable graders in **13 of 13** Terminal Wrench tasks (10 with hard proof), using Docker, system-call tracing and counterfactual regrading. A recorded agent episode is replayed through Guard and blocked at the exact command that reads the hidden answer file.

## How we built it
- **Language:** Python.
- **Training:** Hugging Face TRL (GRPO) with LoRA on Qwen2.5-1.5B-Instruct, run on UW Hyak GPUs.
- **Chess:** Stockfish 19 labels the positions and judges the games.
- **Audits:** OpenRouter for the AI auditor; Docker and strace for the Terminal Wrench audits.
- **Dashboard:** a dependency-free local web app, so the demo works without Wi-Fi.

## Challenges
At first, the cheat would not appear on its own. With a prompt asking for "the single best move", a 1.5B model almost never lists moves (0–2% of answers), so training never discovers the shortcut. We ran pilot sweeps over learning rate and prompt wording. With "What is the best move?", the cheat took off within about 25 steps at learning rate 5e-5. We then used those exact settings for both models. **We disclose this tuning.**

## Accomplishments
- An end-to-end loop from audit to signature to Guard, measured on real GPU training runs rather than mockups.
- A clean, replicated result. Same model, data and settings: without Guard the model learns only the cheat (true score 0.000 on both seeds); with Guard it learns the task (0.160 and 0.145, against 0.052 for the untrained model).
- Every number in the demo traces back to a file: telemetry, run manifests and audit reports.

## What we learned
- Prompt wording decides whether a model even finds a grader bug.
- Matching what the model does (the output's shape, which files it touches) is sturdier than reading its reasoning. OpenAI found that penalizing models based on their reasoning text teaches them to hide intent.

## What's next
- Guard plugins for every major RL framework.
- System-call level signals for agent sandboxes.
- Automatic grader patches with re-audit.
- A shared signature feed, like antivirus definitions for RL environments.

## Limitations, said plainly
- The chess bug is seeded.
- The prompt and learning rate were tuned in pilots until the unprotected model cheated.
- The signature that trains the Guard comes from Verity's deterministic fixture. The live Opus audit found the cheat but ran out of credits before writing its own signature.
- These results do not claim general safety or general chess strength.

## Pre-event work
Verity's Terminal Wrench auditor existed before DubHacks. Part of this demo was also written the night before the event.
