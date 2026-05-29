# Agent-Usability Eval for the Overture CLI — Design

**Date:** 2026-05-28
**Status:** Approved for planning

## Problem

The Overture CLI was redesigned around a thesis (see
`designing_cli_interfaces_for_data_products.md`): expose *question-shaped*
verbs (`places`, `buildings`, `roads`, `addresses`, `at`, `containing`,
`where`, `count`, `categories`, `schema`, `capabilities`) and treat
`download` as the escape hatch for the four types that lack a convenience
verb. We have no objective measurement of whether that thesis holds when a
real agent uses the CLI.

This eval measures an agent's ability to answer geospatial questions with
the CLI, and surfaces the two failure modes the redesign was meant to kill:

1. **Unnecessary `download` fallback** — the agent reaches for `download`
   when a convenience verb already covers the question. Signals an
   affordance/communication failure: the right verb existed but wasn't
   discovered or trusted.
2. **CLI errors** — wrong commands, bad flags, invalid category values,
   malformed coordinates, tracebacks. Signals a learnability failure.

Failures are aggregated and synthesized into concrete proposed improvements
(stderr hints, Skill edits, help wording, missing verbs/aliases).

## Scope and key decisions

- **Agent harness:** real Claude Code headless (`claude -p`), which
  auto-loads the bundled Overture Skill — the intended deployment.
- **Model:** Sonnet is the default; a `--model` flag passes through to
  `claude -p` so a run can use Opus (or another model) when desired.
- **Data backend:** live Overture S3, restricted to small queries (small
  bboxes / small named places) to keep downloads cheap and fast.
- **Scoring:** process signals only (download usage + CLI errors +
  efficiency). No answer-correctness judging in v1; the final answer text is
  recorded so correctness can be layered in later without re-running.
- **Synthesis:** LLM step that turns clustered failures into concrete,
  evidence-backed change proposals (text, not auto-applied).
- **Trace capture:** hybrid — transcript for the agent's final answer and
  give-up signal, plus a `PATH` shim for clean command/error/download
  signals.
- **Skill state:** Skill-installed only in v1 (no naked-CLI control arm).
- **Scale (v1):** 10 questions × 2 repeats = 20 runs.

Out of scope for v1: without-Skill control arm, answer-correctness scoring,
multi-model comparison, CI integration, auto-applied patches.

## Architecture

Five components under a new top-level `evals/` directory (distinct from
`benchmarks/`, which is performance-focused). Standalone scripts, **not**
pytest — these are slow, networked, and cost money, so they are opt-in.

```
evals/
  questions.yaml      # the question bank
  shim/overturemaps   # PATH shim wrapping the real CLI (logs argv/exit/io)
  runner.py           # drives claude -p per (question x repeat), captures runs
  score.py            # normalizes traces -> per-run metric records
  synthesize.py       # aggregates + LLM-proposes improvements
  runs/<id>-<n>/      # per-run artifacts (transcript, shim log, record)
  report.md           # generated, human-readable, ranked
  proposals.json      # generated, structured change proposals
```

A `just eval` recipe runs runner → score → synthesize.

### 1. Question bank (`evals/questions.yaml`)

Hand-authored, question-shaped, tagged. Schema per entry:

```yaml
- id: coffee-brooklyn-count
  question: "How many coffee shops are in Brooklyn?"
  tier: 1                          # 1..4, see below
  download_is_legitimate: false    # true only when no verb covers the type
  target_type: place               # expected Overture type for the answer
  notes: "Ideal path: count -t place --in Brooklyn --category coffee_shop"
```

`download_is_legitimate` is the field that keeps the metric honest:
`download` usage is only counted as a failure when it is `false`.

**Complexity tiers** and the v1 set of 10 (2 per tier):

- **T1 — single verb.**
  1. "How many coffee shops are in Brooklyn?" (`count`/`places`)
  2. "Where is Boston, MA, and what is its bounding box?" (`where`)
- **T2 — discovery / filter needed.**
  3. "Find buildings taller than 150m in Manhattan." (`buildings --where height>150`)
  4. "What restaurant categories exist for places in Brooklyn?" (`categories` → `places`)
- **T3 — spatial / multi-step.**
  5. "What POIs are within 500m of 40.7128,-74.0060?" (`at ... --radius 500`)
  6. "Which administrative areas contain 40.7128,-74.0060?" (`containing`)
- **T4 — legitimate escape hatch** (`download_is_legitimate: true`).
  7. "Get the water features in downtown Boston." (`download -t water`)
  8. "Get the land-use polygons for a small area of Brooklyn." (`download -t land_use`)
- **T5 — compound / cross-layer.** Questions that combine multiple layers
  (places + roads/segments, places + places) plus a spatial relationship.
  These primarily exercise *decomposition and verb-chaining*, not the
  download penalty: a cross-layer spatial join has no single verb, so
  `download` is marked legitimate (`download_is_legitimate: true`) and the
  scoring focus shifts to error rate, wasted commands, and whether the agent
  completed. Each carries a `subtasks` list in its `notes` describing the
  expected decomposition.
  9. "Find all the hardware stores within 200m of bike paths in Alameda
     County." (places `hardware_store` + roads/segments cycleways + 200m
     proximity join)
  10. "How many bus stops have a coffee shop within 100m in Williamsburg,
      Brooklyn?" (places `bus_stop` + places `coffee_shop` + 100m proximity
      count)

All places/bboxes are deliberately small to bound download cost; the
compound questions use small named areas (Williamsburg) or accept that
county-scale queries (Alameda County) are the most expensive in the set and
sit at the edge of the cost guard.

### 2. Runner (`evals/runner.py`)

For each (question × repeat):

1. Create an isolated temp working directory for outputs.
2. Use a **pre-warmed shared divisions cache** so runs measure agent
   behavior, not one-time index-build time/failures. The runner warms the
   cache once before the batch.
3. Put the **shim** first on `PATH` so every `overturemaps` invocation is
   logged with argv, exit code, stdout, and stderr.
4. Invoke `claude -p "<question>" --model <model> --output-format
   stream-json` with bash permitted, in the Skill-installed environment.
   `<model>` defaults to Sonnet and is overridable via the runner's
   `--model` flag (e.g. `--model opus`).
5. Persist the transcript (stream-json) and the shim log to
   `evals/runs/<id>-<n>/`.

Runs are independent; a single batch is 10 × 2 = 20 invocations.

### 3. Trace capture (hybrid)

- **Shim (`evals/shim/overturemaps`)** — a thin wrapper (~30 lines) that
  appends a JSON line per call: `{argv, exit_code, stdout, stderr,
  duration}`, then exec's the real CLI transparently (output passes through
  unchanged so the agent sees normal behavior). This is the schema-stable
  source of truth for command-level metrics.
- **Transcript (stream-json)** — parsed for the agent's final answer text
  and whether it completed vs gave up. Used for `completed` and to attach
  the answer to the record; not relied on for command/error counting.

### 4. Scorer (`evals/score.py`)

Normalizes shim log + transcript into one record per run and computes
**process metrics**:

- `unnecessary_download` — used `download` while `download_is_legitimate`
  is `false`.
- `cli_error_count` and **error taxonomy** per failing call, classified from
  exit code + stderr patterns:
  - `unknown_command` (e.g. `No such command`)
  - `bad_option` (e.g. `no such option` / Click usage error)
  - `bad_category_value` (empty result + near-match hint, or invalid value)
  - `malformed_bbox_or_coords`
  - `wrong_type_for_question` (queried a type that can't answer it)
  - `traceback` (unhandled Python exception)
- `recovered` — a later call succeeded after an error. The CLI's stderr
  hints are *meant* to enable recovery, so recovery is a positive signal;
  repeated unrecovered errors are the negative one.
- `command_count`, `wasted_commands` (failed + redundant), `completed`.
- `final_answer` (recorded, not scored).

Output: `evals/runs/<id>-<n>/record.json`.

### 5. Synthesizer (`evals/synthesize.py`)

Aggregates all records, clusters failures by `(taxonomy × question/tier)`,
ranks clusters by frequency × severity, and runs an LLM step that converts
each cluster into a **concrete, evidence-backed proposal** tied to the
offending traces. Examples of proposal shapes:

- "2/2 runs guessed `--category cafe`; add `cafe`→`coffee_shop` to the
  near-match hint."
- "Agents never discovered `containing`; add a spatial example to the
  Skill."
- "`download -t water` runs took N commands to find the type name; surface
  `types` more prominently."

Outputs:

- `evals/report.md` — ranked, human-readable, one section per failure
  cluster with example traces and the proposed change.
- `evals/proposals.json` — structured proposals (cluster id, evidence run
  ids, target artifact, suggested change). Not auto-applied.

## Data flow

```
questions.yaml ─┐
                ├─> runner.py ──> runs/<id>-<n>/{transcript, shim.log}
shim + cache ───┘                     │
                                      v
                              score.py ──> runs/<id>-<n>/record.json
                                      │
                                      v
                          synthesize.py ──> report.md + proposals.json
```

## Error handling

- **Run-level failures** (claude CLI nonzero, timeout, network outage) are
  recorded as `run_status: error` and excluded from metric aggregation but
  listed in the report so they aren't silently dropped.
- **Cache warm failure** aborts the batch with a clear message rather than
  letting every run fail on index build.
- **Shim transparency:** the shim must pass through exit codes and
  stdout/stderr unchanged; a shim crash must not alter what the agent sees
  (fail open to the real CLI).
- **Cost guard:** the runner refuses questions whose resolved bbox exceeds a
  configured area threshold, to prevent a malformed question from triggering
  a huge live download.

## Testing strategy

- Unit-test the scorer and taxonomy classifier against **recorded fixture
  traces** (saved shim logs + transcripts) — deterministic, no network.
- Unit-test the shim's pass-through behavior (exit code, stdout, stderr
  preserved) against a fake CLI.
- The runner and synthesizer are exercised by a single live smoke run
  (`just eval --smoke`, 1 question × 1 repeat), not by unit tests.

## Success criteria

- `just eval` produces `report.md` + `proposals.json` from a live 20-run
  batch.
- The scorer correctly classifies every error in the fixture set.
- The report ranks failure clusters and each cluster carries at least one
  example trace and one concrete proposal.
