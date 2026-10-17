# Local demo

From the repository root, with `requirements.txt` installed:

```sh
python -m dashboard.server
```

Open **http://127.0.0.1:8765**. The server binds only to localhost. It needs no frontend build, hosted assets, or network connection for fixture proofs and recorded replay. Stockfish must be installed for live probes and arena play; set `VERITY_STOCKFISH_PATH` or pass `--stockfish` if it is not on PATH.

All four panels identify their evidence source:

- **Audit:** loads `markers/chess.json` and its report (`experiments/chess-audits/chess.json`) when present. If the pack is absent, explicitly labeled `chess-fixture.json` proves the disclosed seeded bug. The incomplete frontier attempt remains separately inspectable. Live probes rank the starting position with Stockfish, score the actual reply with both graders, and run the active Guard.
- **Training:** reads `artifacts/dubhacks/{baseline,verity}/{run.json,telemetry.jsonl}`. It draws real telemetry only. Files older than two minutes, or completed runs, are labeled recorded. Each chart and caught count covers the latest 1,000 rollout batches. `results.json` adds the held-out three-model comparison.
- **Arena:** Stockfish vs Stockfish is an explicitly labeled engine preview. Trained mode requires two completed, matched manifests and adapters in `{baseline,verity}/adapter`. It checks equal configurations, model revision, package versions, precision and training source, plus unchanged chess prompt/environment. One base loads both named adapters and warms up before play. Raw output, legal move count, Guard matches, per-move latency and Stockfish evaluation remain visible. The first legal move is played; no legal move forfeits. No clock forfeits. At 160 plies Stockfish adjudicates: white wins at ≥+1 pawn, black at ≤−1 pawn, otherwise draw. Each series has an even number of games with colors swapped. Matches are appended to the selected run directory's `matches.jsonl`.
- **Real envs:** reads the recorded v5.1 audit in `experiments/runs/full-audit-v51-20261012`. Final verdicts determine the 13/13 and 10-certified totals; the table's hack counts are the original attempts, which can differ from later certified mechanisms. The blind-maze replay checks recorded commands through Guard and stops at the first block. No commands are executed and no redirected continuation is invented.

Use `--run-dir /path/to/copied/hyak/output` for a different training run; `--model` must match its manifests. Use `--reports-dir`, `--signature`, and `--port` for alternate local sources. Restart the server after updating model weights; telemetry and signature files reload while it runs.

## Local API

All responses are JSON except `/`. POST requests must be same-origin and use a localhost Host header. The UI accepts no remote paths or shell commands.

| Method | Path | Behavior |
|---|---|---|
| GET | `/api/status` | Component availability, adapter readiness and reason |
| GET | `/api/audit` | Active pack, report, provenance and frontier attempt |
| POST | `/api/audit/probe` | `{kind:"spray"\|"honest"\|"custom", reply?, fen?}` scores a reply live |
| GET | `/api/training` | Last 1,000 rollout rows per arm, examples and evaluation |
| GET | `/api/real-envs` | Recorded environment verdicts and original attempt counts |
| POST | `/api/real-envs/replay` | Recheck blind-maze attempt 1 through Guard |
| GET | `/api/arena` | Position, raw completions, evaluation and series |
| POST | `/api/arena/start` | `{mode:"engine"\|"trained", games:2\|4\|6\|8\|10}` |
| POST | `/api/arena/step` | Generate one move, or advance a finished game |
| POST | `/api/arena/reset` | Clear in-memory series; saved match log remains |

Validation: `python -m unittest tests.test_dashboard` covers data provenance, mismatched arms, incomplete JSONL writes, final report verdicts, and local request boundaries. `node --check` can check the inline script after extraction. Full Stockfish preview series can be played without installing the ML extras.
