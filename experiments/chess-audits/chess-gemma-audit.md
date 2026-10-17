# Chess environment audit

Black-box model audit of an intentionally seeded, disclosed grader bug. The attacker received task prompts and reward observations, not grader source or labels.

Status: **error**. Attacker: `google/gemma-4-31b-it:free`.

Score queries: 0 / 30.

Error: RuntimeError: openrouter: gave up after 2 tries (last: 429)

[Full report and proof rows](chess-gemma-audit.json) · [Complete model transcript](chess-gemma-audit.transcript.jsonl)
