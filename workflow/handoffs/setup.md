# Workflow setup handoff

- Product implementation remains owned by the user's existing Claude session. No tracked application files were edited by this workflow setup.
- Added AGENTS.md / CLAUDE.md coordination guidance, workflow/tasks.json, workflow/README.md, and a ready handoff prompt at workflow/prompts/claude-handoff.md.
- Added scripts/agent_flow.py: locked task transitions, isolated worktree preparation, bounded Codex/Claude execution, retained logs, output/scope checks, explicit review states, dependency handling, and no automatic merge or external actions.
- Prepared flow/pitch and flow/customer worktrees at the existing committed snapshot. Packets include a current progress snapshot and read-only references to main-checkout artifacts.
- Started a bounded two-task Codex queue: pitch/claims reconciliation first, customer-discovery kit second, 15 minutes maximum per task. Use the status command for live state. QA remains dependent on the existing implementation owner's concrete handoff.
- Verified installed Codex and Claude CLI options; the Claude extension binary is discoverable even though claude is not on PATH.
- Six workflow tests passed with fake workers: dependency gating, preserving dirty main-checkout files, successful output to review, out-of-scope blocking, nonzero-exit blocking without retry, and refusing to restart an external owner. Actual Codex worker startup and tool activity were observed.
- Current limits: this is a bounded local runner, not an always-on supervisor; it does not infer completion of external chats/jobs. Codex dollar spend is not capped by this wrapper. Authentication/quotas are inherited from the configured CLI. A successful worker output still needs integration review.

Useful commands:

```bash
python3 scripts/agent_flow.py status
python3 scripts/agent_flow.py packet pitch
python3 scripts/agent_flow.py doctor
python3 -m unittest tests.test_agent_flow
```

Founder action now: record a timed three-minute demo and note confusing clicks/claims; then speak with relevant RL/environment builders about actual grader incidents. The queue's purpose is to free that time.
