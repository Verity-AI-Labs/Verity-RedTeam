# Verity agent coordination

Read `PROGRESS.md`, `README.md`, and `workflow/README.md` before starting. The repo was restructured: current code lives in `verity/`, `environments/`, `dashboard/`, `train.py`, and `evaluate.py`; old `hackathon/` paths are historical.

- The user's active Claude session owns implementation in the main checkout. Other implementation tasks use isolated Git worktrees and explicitly assigned files. Do not revert, stash, overwrite, or commit another agent's changes.
- Use `python3 scripts/agent_flow.py status` for task ownership and `packet TASK` for the scoped brief. This board records coordination; it does not automatically know when another chat or GPU job finishes.
- Complete the assigned outcome, run relevant checks, and leave a concise evidence-backed handoff. If blocked, record the exact failure and next independent task. Do not loop on quota/authentication errors.
- Current artifacts and reproducible checks outrank old chat summaries. Never mix fixture-marker training with AI-marker training. Keep seeded bugs, prompt tuning, weak chess play, and pre-event work disclosed.
- No external messages, submissions, deployments, new paid audits, or GPU jobs without authorization for that action. Existing authorized work may continue. Routine local fixes/checks need no extra confirmation.
- Keep code flat. Do not build an orchestration framework when a task file and one command suffice.
