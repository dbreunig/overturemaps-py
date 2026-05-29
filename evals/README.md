# Agent-Usability Eval

Measures whether an agent can answer geospatial questions with the Overture
CLI without falling back to `download` and without CLI errors. See the design
spec: `docs/superpowers/specs/2026-05-28-agent-usability-eval-design.md`.

## Run it

```bash
just eval                 # full batch (10 questions x 2 repeats), sonnet
just eval opus            # same, with opus as the agent model
just eval-smoke           # one question, one repeat
```

Requires the `claude` CLI on PATH and network access to Overture S3. The
first run warms the divisions index cache (one-time, slow).

## Pieces

- `questions.yaml` — the question bank (tiers 1-5, tagged `download_is_legitimate`).
- `shim/overturemaps` — PATH shim logging every CLI call.
- `runner.py` — drives `claude -p` per (question x repeat) -> `runs/<id>__r<n>/`.
- `score.py` — `runs/*/record.json` (download usage, error taxonomy, recovery, completion).
- `synthesize.py` — `report.md` + `proposals.json`.

## Reading the output

`report.md` ranks failure clusters and lists coverage gaps. Every `download`
counts: when `download_is_legitimate: false` it's an agent failure; when
`true` it's a coverage-gap candidate for a new verb. The goal is to drive
total download usage toward zero.
