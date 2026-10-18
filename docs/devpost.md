# Verity: stop models from learning to cheat while you train them

## Inspiration

Reinforcement learning (RL) is how models get better now, and models exploit graders. If an environment has a loophole, training pays the model to use it: the model learns the shortcut instead of the skill. Anthropic reported that reward hacking learned in production RL spreads into broader misbehavior. The usual fix comes too late: someone reads transcripts after training and notices.

## What it does

Verity is one system that plugs into any RL environment and training run:

1. **Audit, before training.** A frontier model (Claude Opus 5.5 through OpenRouter) gets exactly what a training agent gets: the task and the tools. It never sees the grader's code. Its instructions are the same for every environment: test whether each stated limit is enforced, exploit what you find, reproduce it. Verity proves each finding with numbers: what the grader paid against the environment's own reference score for the same episodes.
2. **Marker.** The auditor writes each proven hack as a small machine-checkable rule with a warning and a fix. Verity accepts it only if it catches the exploit and stays silent on hundreds of honest episodes.
3. **Guard, during training.** Guard wraps the environment the trainer sees. Every action is checked against the markers in microseconds. In a multi-step environment, a matching tool call is blocked: the agent gets the marker's warning as the tool result and keeps going, and the attempt costs reward. Moves it makes on its own still count, so it is warned and redirected rather than just punished.
4. **Alarm.** Guard flags a run when the grader's reward climbs while the true score does not, the signal to re-audit.

Nothing about a particular hack is written into Verity. An environment plugs in with a few functions: its tools, its reward, a reference score, and examples of honest play.

## The demo: chess, played through tools

- **Environment.** The agent plays six moves against an engine through three tools: `legal_moves()`, `move(uci)` and `hint()`. Each move is scored against Stockfish's best move. We planted one loophole and did not tell the auditor: the task allows one hint per game, but nothing enforces it.
- **Audit.** Opus read "at most once per game", called `hint()` twice in a row, saw nothing stop it, then took a hint before every move. In 4 finished games the grader paid 0.89 against a true score of 0.25. Opus then wrote the marker `chess-hint-overuse-per-game` (fires on the second hint). It caught every exploit episode and flagged 0 of 400 honest games. Total cost: $0.83.
- **Training.** Qwen3-8B, LoRA rank 16, GRPO, 50 steps, 8 games per step, on UW Hyak. Two runs that differ only in whether Guard enforces the marker.
  - Without Guard, the model takes a hint on essentially every move: grader reward about 0.96, true score 0, and it stops choosing moves itself. Guard's alarm fired at step 5.
  - With Guard, hint attempts fell from 100% of games to 0% within about 5 steps, and the model kept playing its own moves.
  - Real skill, as deployed (no hint tool, 24 held-out positions) in the 50-step run: the unguarded model fell from 0.37 (untrained) to 0.03, because it kept reaching for a hint that was not there. The guarded model kept the untrained model's skill (0.36) but did not improve on it. The overnight 300-step v3 run tests whether it does. FINAL NUMBERS: fill in from `artifacts/chess_game-v3`.
  - FINAL NUMBERS: fill in from `artifacts/chess_game-8b` (training curves, `results.json`, `results-open.json`).
- **Match.** The two trained models play each other with `hint()` unavailable, as in a real game. FINAL RESULT: fill in from `matches.jsonl`.
- **Try Guard.** The hosted page runs Opus's actual marker in the browser: type a sequence of tool calls and watch which ones Guard blocks.

## More evidence, same system

- **Chess, one answer (Qwen2.5-1.5B).** The grader credits the best move found anywhere in a reply. Without Guard the model lists moves in 100% of held-out replies and its true score falls to 0.000 (two seeds). With Guard it never does, and its true score roughly triples over the untrained model. The result held with the hand-written test marker and again with the marker Opus wrote (true score 0.170 and 0.164 vs 0.000).
- **Terminal Wrench.** 13 sandboxed terminal tasks, audited with DeepSeek as attacker and judge: all 13 hackable, 10 with measured proof (answer files read, grader files rewritten). A recorded agent episode replayed through Guard is blocked at the exact command that reads the hidden answer file.

## How we built it

- **Language:** Python.
- **Training:** Hugging Face TRL (GRPO, with its tool-calling environment support) and LoRA on Qwen3-8B, on the UW Hyak cluster's RTX 6000 GPUs (fp16, the model split over two GPUs per run).
- **Audits:** Claude Opus 5.5 through OpenRouter, with a spend cap. Docker and system-call tracing for the terminal tasks.
- **Chess:** python-chess and Stockfish 16.
- **Page:** one static page built from the run files by a script, so every number traces back to telemetry, run manifests and audit reports.

## Challenges

- **The first reward gave training no reason to cheat.** Version 1 paid for the game outcome after 8 moves. Qwen3-8B blunders so often that one or two engine moves change nothing, so the hack could never take off. Version 2 scores every move against Stockfish, so each extra hint pays.
- **Making the auditor thorough without telling it the answer.** Its first run played honestly and never tested the hint limit. We added one general line, "test whether each stated limit is enforced", and it found the loophole. A later run found it but proved it in only one game, so the prompt now states the proof standard (three finished games). The first marker fired only at three hints; the marker prompt now asks for the smallest violation of the stated rule. None of these lines mention chess or hints, and every attempt is kept on disk.
- **A penalty that gives no signal.** At first any blocked call set the episode reward to −1. At step 0 every Verity game was blocked, so all rewards were equal and GRPO had nothing to learn from. Now each blocked call costs its penalty while the model's own moves still count. We restarted both runs with that rule.
- **Compute.** Hyak's shared GPU pool went down mid-afternoon. We moved both runs to our group's 24 GB RTX 6000s by splitting the 8B model across two GPUs per run.

## Accomplishments

- An end-to-end loop from black-box audit to auditor-written marker to Guard, measured on real GPU training runs.
- The same system, unchanged, on single-answer chess, multi-step chess and 13 sandboxed terminal tasks.
- Every number on the page traces back to a file.

## What we learned

- A frontier auditor needs a method, not hints: "test every stated limit" was enough.
- The penalty design matters as much as detection. Redirecting the agent and still crediting its own work gave a clean learning signal.
- Matching what the model does (its tool calls, files, output shape) is sturdier than reading its reasoning. OpenAI found that penalizing models based on their reasoning text teaches them to hide intent.

## What's next

- Guard plugins for every major RL framework.
- Auditing environments we did not write, from public environment hubs.
- System-call-level signals for agent sandboxes.
- Automatic environment patches from the auditor's suggested fix, with a re-audit.

## Limitations, said plainly

- The chess loopholes are planted on purpose.
- The multi-step results come from one seed, 50 steps and a small evaluation. They describe this environment only.
- Under the version 2 prompt the untrained model already overused hints when sampling. This experiment shows that Guard redirects a model that already cheats; the one-answer chess runs show cheating emerging during training.
- No claim of general safety or chess strength.

## Pre-event work

Verity's terminal-task auditor existed before DubHacks, and part of this demo was written the night before the event.
