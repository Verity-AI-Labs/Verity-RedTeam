# Verity audit pipeline

The `audit` command runs the full before-training audit for one task. The attacker
receives the task instructions plus `prompts/audit_attack.md`. The runner first
validates the task and grader, then runs the adversarial attempts and records
trace-based evidence. A separate judge evaluates passing attempts.

```text
tasks.json + selected task
          ↓
build and inspect Docker image
          ↓
oracle, grader, and validity checks
          ↓
four fresh adversarial attempts (up to 40 turns each)
          ↓
replay, evidence analysis, and judge verdicts
          ↓
experiments/runs/<task>-<timestamp>-audit/report.json
```

Run the full audit with:

```bash
python3 src/runner.py audit --task blind-maze \
  --attacker qwen3-coder:30b --judge qwen3-coder:30b
```

The `check` command is a no-model control run for blind-maze; it is not a
separate adversarial-agent mode. See [audit.md](audit.md) for the report format,
evidence rules, and all ten task IDs.
