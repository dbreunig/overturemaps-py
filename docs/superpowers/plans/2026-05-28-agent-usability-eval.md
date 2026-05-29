# Agent-Usability Eval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an `evals/` harness that runs headless Claude Code against a bank of geospatial questions, captures each agent's CLI trace, scores process failures (unnecessary `download`, CLI errors), and synthesizes concrete improvement proposals.

**Architecture:** Standalone Python scripts under `evals/`. A `runner` drives `claude -p` per (question × repeat) inside an isolated working dir with the Overture Skill installed and a logging `PATH` shim first on `$PATH`. Pure modules (`trace`, `taxonomy`, `score`) normalize and score the captured traces and are unit-tested against fixtures. A `synthesize` step aggregates records and asks `claude -p` for ranked, evidence-backed proposals.

**Tech Stack:** Python 3.10+, Click CLI under test, PyYAML for the question bank, `claude` CLI (headless), pytest for the pure modules.

**Spec:** `docs/superpowers/specs/2026-05-28-agent-usability-eval-design.md`

---

## File Structure

```
evals/
  __init__.py            # marks evals a package so `import evals.*` works in tests
  questions.yaml         # the 10-question bank
  shim/overturemaps      # executable PATH shim: logs argv/exit/io, runs real CLI
  trace.py               # ShimCall/Transcript dataclasses + parsers (pure)
  taxonomy.py            # classify_error(call) -> label | None (pure)
  score.py               # Record + score_run() + main() (pure scoring + IO)
  runner.py              # drives claude -p, writes runs/<id>__r<n>/
  synthesize.py          # aggregate records -> report.md + proposals.json
  runs/                  # generated per-run artifacts (gitignored)
  report.md              # generated
  proposals.json         # generated
tests/
  test_eval_trace.py
  test_eval_taxonomy.py
  test_eval_score.py
  test_eval_shim.py
  eval_fixtures/
    shim_sample.log
    transcript_sample.jsonl
```

---

### Task 1: Scaffold the `evals` package, deps, and question bank

**Files:**
- Create: `evals/__init__.py`
- Create: `evals/questions.yaml`
- Modify: `pytest.ini` (add `pythonpath = .`)
- Modify: `.gitignore` (ignore generated eval artifacts)

- [ ] **Step 1: Add PyYAML to the dev dependency group**

Run:
```bash
uv add --group dev pyyaml
```
Expected: `pyproject.toml` gains `pyyaml` under `[dependency-groups] dev`, lockfile updates.

- [ ] **Step 2: Create the package marker**

Create `evals/__init__.py`:
```python
"""Agent-usability eval harness for the Overture CLI. See docs/superpowers/specs/2026-05-28-agent-usability-eval-design.md."""
```

- [ ] **Step 3: Make `import evals` work under pytest**

In `pytest.ini`, add a `pythonpath` line directly under `[pytest]` (after the comment on line 2):
```ini
[pytest]
# Pytest configuration for overturemaps-py

# Make the repo root importable so `import evals.*` resolves in tests
pythonpath = .
```
(Leave the rest of the file unchanged.)

- [ ] **Step 4: Ignore generated artifacts**

Append to `.gitignore`:
```gitignore
# Agent-usability eval artifacts
evals/runs/
evals/report.md
evals/proposals.json
```

- [ ] **Step 5: Write the question bank**

Create `evals/questions.yaml`:
```yaml
# Agent-usability eval question bank.
# Fields:
#   id                     stable slug (no '__' — that delimits run dirs)
#   question               the prompt handed verbatim to `claude -p`
#   tier                   1..5 complexity tier
#   download_is_legitimate true only when no convenience verb covers the type
#   target_type            expected Overture type(s) for the answer
#   place                  optional; resolved by the cost guard to bound query size
#   notes                  ideal path (and, for compound, the expected decomposition)
#   subtasks               optional; expected verb-by-verb decomposition (compound)

- id: coffee-brooklyn-count
  question: "How many coffee shops are in Brooklyn?"
  tier: 1
  download_is_legitimate: false
  target_type: place
  place: "Brooklyn, US-NY"
  notes: "Ideal: count -t place --in Brooklyn --category coffee_shop"

- id: where-boston
  question: "Where is Boston, MA, and what is its bounding box?"
  tier: 1
  download_is_legitimate: false
  target_type: division
  place: "Boston, US-MA"
  notes: "Ideal: where 'Boston, MA' (optionally --json for the bbox)"

- id: tall-buildings-manhattan
  question: "Find buildings taller than 150m in Manhattan."
  tier: 2
  download_is_legitimate: false
  target_type: building
  place: "Manhattan, US-NY"
  notes: "Ideal: buildings --in Manhattan --where 'height>150'"

- id: restaurant-categories-brooklyn
  question: "What restaurant categories exist for places in Brooklyn?"
  tier: 2
  download_is_legitimate: false
  target_type: place
  place: "Brooklyn, US-NY"
  notes: "Ideal: categories -t place --in Brooklyn (then filter to restaurant_* values)"

- id: pois-near-point
  question: "What POIs are within 500m of 40.7128,-74.0060?"
  tier: 3
  download_is_legitimate: false
  target_type: place
  notes: "Ideal: at 40.7128,-74.0060 -t place --radius 500"

- id: containing-point
  question: "Which administrative areas contain the point 40.7128,-74.0060?"
  tier: 3
  download_is_legitimate: false
  target_type: division
  notes: "Ideal: containing 40.7128,-74.0060"

- id: water-downtown-boston
  question: "Get the water features in downtown Boston."
  tier: 4
  download_is_legitimate: true
  target_type: water
  place: "Boston, US-MA"
  notes: "No convenience verb for water; download -t water is correct. Coverage-gap candidate."

- id: landuse-brooklyn
  question: "Get the land-use polygons for a small area of Brooklyn."
  tier: 4
  download_is_legitimate: true
  target_type: land_use
  place: "Brooklyn, US-NY"
  notes: "No convenience verb for land_use; download -t land_use is correct. Coverage-gap candidate."

- id: hardware-near-bikepaths-alameda
  question: "Find all the hardware stores within 200m of bike paths in Alameda County."
  tier: 5
  download_is_legitimate: false
  target_type: [place, segment]
  place: "Alameda County, US-CA"
  notes: "Both layers have verbs; fetch each and join in code. download -t place/segment is a failure."
  subtasks:
    - "places --in 'Alameda County' --category hardware_store"
    - "roads --in 'Alameda County' (filter to cycleway / bike paths)"
    - "spatial join: hardware stores within 200m of a bike path"

- id: busstops-coffee-williamsburg
  question: "How many bus stops have a coffee shop within 100m in Williamsburg, Brooklyn?"
  tier: 5
  download_is_legitimate: false
  target_type: place
  place: "Williamsburg, US-NY"
  notes: "Both layers are places; fetch each and join in code. download -t place is a failure."
  subtasks:
    - "places --in Williamsburg --category bus_stop"
    - "places --in Williamsburg --category coffee_shop"
    - "spatial join + count: bus stops with a coffee shop within 100m"
```

- [ ] **Step 6: Verify the bank parses**

Run:
```bash
uv run python -c "import yaml; d=yaml.safe_load(open('evals/questions.yaml')); print(len(d), 'questions', [q['id'] for q in d])"
```
Expected: `10 questions [...]` with all 10 ids, no traceback.

- [ ] **Step 7: Commit**

```bash
git add evals/__init__.py evals/questions.yaml pytest.ini .gitignore pyproject.toml uv.lock
git commit -m "feat(evals): scaffold eval package, deps, and question bank"
```

---

### Task 2: `trace.py` — normalized trace dataclasses + parsers

**Files:**
- Create: `evals/trace.py`
- Test: `tests/test_eval_trace.py`
- Test fixtures: `tests/eval_fixtures/shim_sample.log`, `tests/eval_fixtures/transcript_sample.jsonl`

- [ ] **Step 1: Write the fixtures**

Create `tests/eval_fixtures/shim_sample.log` (JSON-lines, one call per line):
```
{"argv": ["places", "--in", "Brooklyn", "--category", "cafe"], "exit_code": 0, "stdout": "", "stderr": "[overturemaps] 0 rows. No place has categories.primary='cafe' in this bbox. Did you mean: coffee_shop? Run `overturemaps categories -t place --bbox …` to see the full list.\n", "duration": 1.2}
{"argv": ["places", "--in", "Brooklyn", "--category", "coffee_shop"], "exit_code": 0, "stdout": "{\"type\":\"FeatureCollection\"}\n", "stderr": "", "duration": 1.4}
{"argv": ["download", "-t", "water", "--bbox", "-71.07,42.35,-71.05,42.36"], "exit_code": 0, "stdout": "", "stderr": "", "duration": 3.1}
```

Create `tests/eval_fixtures/transcript_sample.jsonl` (stream-json events; only `result` matters):
```
{"type": "system", "subtype": "init"}
{"type": "assistant", "message": {"content": [{"type": "text", "text": "Let me check."}]}}
{"type": "result", "subtype": "success", "is_error": false, "result": "There are about 1,200 coffee shops in Brooklyn."}
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_eval_trace.py`:
```python
from pathlib import Path

from evals.trace import (
    ShimCall,
    Transcript,
    parse_shim_log,
    parse_transcript,
)

FIXTURES = Path(__file__).parent / "eval_fixtures"


def test_parse_shim_log_reads_each_call():
    calls = parse_shim_log(FIXTURES / "shim_sample.log")
    assert len(calls) == 3
    assert calls[0].argv == ["places", "--in", "Brooklyn", "--category", "cafe"]
    assert calls[0].exit_code == 0
    assert "Did you mean: coffee_shop" in calls[0].stderr
    assert calls[2].argv[0] == "download"


def test_shim_call_subcommand_skips_options():
    assert ShimCall(["places", "--in", "X"], 0, "", "").subcommand == "places"
    assert ShimCall(["--json", "where", "Boston"], 0, "", "").subcommand == "where"
    assert ShimCall(["--version"], 0, "", "").subcommand is None


def test_parse_shim_log_missing_file_is_empty():
    assert parse_shim_log(FIXTURES / "does_not_exist.log") == []


def test_parse_transcript_extracts_final_answer():
    t = parse_transcript(FIXTURES / "transcript_sample.jsonl")
    assert isinstance(t, Transcript)
    assert "Brooklyn" in t.final_answer
    assert t.completed is True
    assert t.run_status == "ok"


def test_parse_transcript_missing_file_is_error():
    t = parse_transcript(FIXTURES / "nope.jsonl")
    assert t.completed is False
    assert t.run_status == "error"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_eval_trace.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'evals.trace'`.

- [ ] **Step 4: Implement `evals/trace.py`**

Create `evals/trace.py`:
```python
"""Parse raw eval artifacts (shim log + claude transcript) into typed records."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ShimCall:
    """One `overturemaps` invocation captured by the PATH shim.

    `argv` is the argument list passed to the CLI (program name excluded),
    e.g. ["places", "--in", "Brooklyn"].
    """

    argv: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration: float = 0.0

    @property
    def subcommand(self) -> str | None:
        """First non-option token — the Click subcommand (e.g. 'places')."""
        for tok in self.argv:
            if not tok.startswith("-"):
                return tok
        return None


@dataclass
class Transcript:
    """The agent-level outcome parsed from the claude stream-json transcript."""

    final_answer: str
    completed: bool
    run_status: str  # "ok" | "error"


def parse_shim_log(path) -> list[ShimCall]:
    path = Path(path)
    if not path.exists():
        return []
    calls: list[ShimCall] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        calls.append(
            ShimCall(
                argv=list(d.get("argv", [])),
                exit_code=int(d.get("exit_code", 0)),
                stdout=d.get("stdout", ""),
                stderr=d.get("stderr", ""),
                duration=float(d.get("duration", 0.0)),
            )
        )
    return calls


def parse_transcript(path) -> Transcript:
    path = Path(path)
    if not path.exists():
        return Transcript(final_answer="", completed=False, run_status="error")
    final_answer = ""
    run_status = "error"
    completed = False
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            continue
        if evt.get("type") == "result":
            final_answer = evt.get("result", "") or ""
            is_error = bool(evt.get("is_error", False))
            run_status = "error" if is_error else "ok"
            completed = (not is_error) and bool(final_answer)
    return Transcript(final_answer=final_answer, completed=completed, run_status=run_status)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_eval_trace.py -v`
Expected: PASS (5 passed).

- [ ] **Step 6: Commit**

```bash
git add evals/trace.py tests/test_eval_trace.py tests/eval_fixtures/
git commit -m "feat(evals): trace dataclasses and parsers"
```

---

### Task 3: `taxonomy.py` — error classifier

**Files:**
- Create: `evals/taxonomy.py`
- Test: `tests/test_eval_taxonomy.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_eval_taxonomy.py`:
```python
from evals.taxonomy import classify_error
from evals.trace import ShimCall


def _call(exit_code=0, stderr=""):
    return ShimCall(argv=["places"], exit_code=exit_code, stdout="", stderr=stderr)


def test_clean_success_is_none():
    assert classify_error(_call(0, "")) is None


def test_ambiguous_warning_is_not_an_error():
    # The CLI prints this on exit 0 as an informational warning.
    stderr = "[overturemaps] Ambiguous --in 'Springfield': picked Springfield, US-IL"
    assert classify_error(_call(0, stderr)) is None


def test_bad_category_value_from_did_you_mean_hint():
    stderr = "[overturemaps] 0 rows. No place has categories.primary='cafe'. Did you mean: coffee_shop?"
    assert classify_error(_call(0, stderr)) == "bad_category_value"


def test_bad_category_value_from_not_present_hint():
    stderr = "[overturemaps] 0 rows. categories.primary='zzz' is not present in this bbox."
    assert classify_error(_call(0, stderr)) == "bad_category_value"


def test_unknown_command():
    assert classify_error(_call(2, "Error: No such command 'plces'.")) == "unknown_command"


def test_bad_option():
    assert classify_error(_call(2, "Error: No such option: --categ")) == "bad_option"


def test_usage_error_is_bad_option():
    stderr = "Usage: overturemaps places [OPTIONS]\nError: --bbox and --in are mutually exclusive"
    assert classify_error(_call(2, stderr)) == "bad_option"


def test_malformed_coords():
    assert classify_error(_call(2, "Error: LATLON must be 'LAT,LON'")) == "malformed_bbox_or_coords"


def test_wrong_type():
    assert classify_error(_call(1, "Error: No features available for type 'place'")) == "wrong_type_for_question"


def test_traceback():
    stderr = "Traceback (most recent call last):\n  File ...\nValueError: boom"
    assert classify_error(_call(1, stderr)) == "traceback"


def test_unrecognized_nonzero_is_other_error():
    assert classify_error(_call(1, "segmentation fault")) == "other_error"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_eval_taxonomy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'evals.taxonomy'`.

- [ ] **Step 3: Implement `evals/taxonomy.py`**

Create `evals/taxonomy.py`:
```python
"""Classify a captured CLI call into an error-taxonomy label.

Returns None for a clean call (success with no recognized soft-failure
signal). The classifier is best-effort: it fingerprints exit codes and
stderr patterns the Overture CLI actually emits. Order matters — the most
specific patterns are checked first.
"""

from __future__ import annotations

from .trace import ShimCall

_TRACEBACK = "Traceback (most recent call last)"


def classify_error(call: ShimCall) -> str | None:
    err = call.stderr or ""
    low = err.lower()

    # Exit 0: only a recognized soft failure counts. The ambiguous-`--in`
    # warning is informational and deliberately NOT treated as an error.
    if call.exit_code == 0:
        if "did you mean:" in low:
            return "bad_category_value"
        if "0 rows" in low and "categories.primary" in low:
            return "bad_category_value"
        return None

    # Nonzero exit: a hard failure. Most specific patterns first.
    if _TRACEBACK in err:
        return "traceback"
    if "no such command" in low:
        return "unknown_command"
    if "no such option" in low:
        return "bad_option"
    if "latlon" in low or "lat,lon" in low or "must be numeric" in low:
        return "malformed_bbox_or_coords"
    if "bbox" in low and ("invalid" in low or "must" in low or "expected" in low):
        return "malformed_bbox_or_coords"
    if "no features available for type" in low:
        return "wrong_type_for_question"
    if "usage:" in low and "error:" in low:
        return "bad_option"
    return "other_error"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_eval_taxonomy.py -v`
Expected: PASS (11 passed).

- [ ] **Step 5: Commit**

```bash
git add evals/taxonomy.py tests/test_eval_taxonomy.py
git commit -m "feat(evals): error taxonomy classifier"
```

---

### Task 4: `score.py` — per-run scoring

**Files:**
- Create: `evals/score.py`
- Test: `tests/test_eval_score.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_eval_score.py`:
```python
from evals.score import score_run, record_to_dict
from evals.trace import ShimCall, Transcript


def _ok(t):
    return Transcript(final_answer="answer", completed=True, run_status="ok")


def test_unnecessary_download_flagged_when_verb_exists():
    q = {"id": "q1", "tier": 1, "download_is_legitimate": False}
    calls = [ShimCall(["download", "-t", "place"], 0, "", "")]
    rec = score_run(q, calls, _ok(None), repeat=1)
    assert rec.download_used is True
    assert rec.unnecessary_download is True
    assert rec.legitimate_download is False
    assert rec.downloaded_types == ["place"]


def test_legitimate_download_is_coverage_gap_not_failure():
    q = {"id": "q7", "tier": 4, "download_is_legitimate": True}
    calls = [ShimCall(["download", "-t", "water"], 0, "", "")]
    rec = score_run(q, calls, _ok(None), repeat=1)
    assert rec.download_used is True
    assert rec.unnecessary_download is False
    assert rec.legitimate_download is True
    assert rec.downloaded_types == ["water"]


def test_error_count_and_recovery():
    q = {"id": "q1", "tier": 2, "download_is_legitimate": False}
    calls = [
        ShimCall(["places", "--in", "X", "--category", "cafe"], 0, "", "Did you mean: coffee_shop?"),
        ShimCall(["places", "--in", "X", "--category", "coffee_shop"], 0, "{}", ""),
    ]
    rec = score_run(q, calls, _ok(None), repeat=2)
    assert rec.cli_error_count == 1
    assert rec.errors[0].taxonomy == "bad_category_value"
    assert rec.recovered is True
    assert rec.command_count == 2
    assert rec.wasted_commands == 1
    assert rec.repeat == 2


def test_no_recovery_when_no_success_follows_error():
    q = {"id": "q1", "tier": 1, "download_is_legitimate": False}
    calls = [ShimCall(["plces"], 2, "", "Error: No such command 'plces'.")]
    rec = score_run(q, calls, _ok(None), repeat=1)
    assert rec.cli_error_count == 1
    assert rec.recovered is False


def test_record_to_dict_is_json_serializable():
    import json
    q = {"id": "q1", "tier": 1, "download_is_legitimate": False}
    calls = [ShimCall(["download", "-t", "place"], 0, "", "")]
    rec = score_run(q, calls, _ok(None), repeat=1)
    d = record_to_dict(rec)
    json.dumps(d)  # must not raise
    assert d["question_id"] == "q1"
    assert d["errors"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_eval_score.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'evals.score'`.

- [ ] **Step 3: Implement `evals/score.py`**

Create `evals/score.py`:
```python
"""Score a single eval run from its captured trace, and batch-score runs/."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from .taxonomy import classify_error
from .trace import ShimCall, Transcript, parse_shim_log, parse_transcript

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_QUESTIONS = REPO_ROOT / "evals" / "questions.yaml"
DEFAULT_RUNS_DIR = REPO_ROOT / "evals" / "runs"


@dataclass
class ErrorRecord:
    argv: list[str]
    taxonomy: str


@dataclass
class Record:
    question_id: str
    tier: int
    repeat: int
    download_used: bool
    unnecessary_download: bool
    legitimate_download: bool
    downloaded_types: list[str]
    download_argvs: list[list[str]]
    cli_error_count: int
    errors: list[ErrorRecord]
    recovered: bool
    command_count: int
    wasted_commands: int
    completed: bool
    run_status: str
    final_answer: str


def _downloaded_types(calls: list[ShimCall]) -> list[str]:
    types: list[str] = []
    for c in calls:
        if c.subcommand != "download":
            continue
        for i, tok in enumerate(c.argv):
            if tok in ("-t", "--type") and i + 1 < len(c.argv):
                types.append(c.argv[i + 1])
    return types


def score_run(question: dict, calls: list[ShimCall], transcript: Transcript, repeat: int) -> Record:
    download_calls = [c for c in calls if c.subcommand == "download"]
    download_used = bool(download_calls)
    legit = bool(question.get("download_is_legitimate", False))

    errors: list[ErrorRecord] = []
    first_error_idx: int | None = None
    for idx, c in enumerate(calls):
        label = classify_error(c)
        if label is not None:
            errors.append(ErrorRecord(argv=c.argv, taxonomy=label))
            if first_error_idx is None:
                first_error_idx = idx

    recovered = False
    if first_error_idx is not None:
        for c in calls[first_error_idx + 1:]:
            if c.exit_code == 0 and classify_error(c) is None:
                recovered = True
                break

    return Record(
        question_id=question["id"],
        tier=int(question.get("tier", 0)),
        repeat=repeat,
        download_used=download_used,
        unnecessary_download=download_used and not legit,
        legitimate_download=download_used and legit,
        downloaded_types=_downloaded_types(calls),
        download_argvs=[c.argv for c in download_calls],
        cli_error_count=len(errors),
        errors=errors,
        recovered=recovered,
        command_count=len(calls),
        wasted_commands=len(errors),
        completed=transcript.completed,
        run_status=transcript.run_status,
        final_answer=transcript.final_answer,
    )


def record_to_dict(record: Record) -> dict:
    return asdict(record)


def _question_map(path: Path) -> dict:
    data = yaml.safe_load(path.read_text())
    return {q["id"]: q for q in data}


def main() -> None:
    p = argparse.ArgumentParser(description="Score captured eval runs into record.json files.")
    p.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    p.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    args = p.parse_args()

    qmap = _question_map(args.questions)
    scored = 0
    for run_dir in sorted(args.runs_dir.glob("*__r*")):
        qid, _, rep = run_dir.name.partition("__r")
        question = qmap.get(qid)
        if question is None:
            print(f"[score] no question for {qid!r}; skipping {run_dir.name}", file=sys.stderr)
            continue
        calls = parse_shim_log(run_dir / "shim.log")
        transcript = parse_transcript(run_dir / "transcript.jsonl")
        record = score_run(question, calls, transcript, int(rep) if rep.isdigit() else 0)
        (run_dir / "record.json").write_text(json.dumps(record_to_dict(record), indent=2))
        print(
            f"[score] {run_dir.name}: download={record.download_used} "
            f"unnecessary={record.unnecessary_download} errors={record.cli_error_count} "
            f"completed={record.completed}"
        )
        scored += 1
    print(f"[score] scored {scored} run(s)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_eval_score.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add evals/score.py tests/test_eval_score.py
git commit -m "feat(evals): per-run scoring with download + error metrics"
```

---

### Task 5: `shim/overturemaps` — logging PATH shim

**Files:**
- Create: `evals/shim/overturemaps` (executable)
- Test: `tests/test_eval_shim.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_eval_shim.py`:
```python
import json
import os
import subprocess
import sys
from pathlib import Path

SHIM = Path(__file__).resolve().parent.parent / "evals" / "shim" / "overturemaps"


def _run(args, env_extra):
    env = dict(os.environ)
    env["OVERTURE_EVAL_PYTHON"] = sys.executable
    env.update(env_extra)
    return subprocess.run([sys.executable, str(SHIM), *args], env=env, capture_output=True, text=True)


def test_shim_passes_through_stdout_and_exit_code(tmp_path):
    # `--version` is fully offline and trivial.
    log = tmp_path / "shim.log"
    proc = _run(["--version"], {"OVERTURE_EVAL_LOG": str(log)})
    assert proc.returncode == 0
    assert "overturemaps" in (proc.stdout + proc.stderr).lower() or proc.stdout.strip()


def test_shim_logs_the_invocation(tmp_path):
    log = tmp_path / "shim.log"
    _run(["--version"], {"OVERTURE_EVAL_LOG": str(log)})
    lines = [ln for ln in log.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["argv"] == ["--version"]
    assert entry["exit_code"] == 0
    assert "stdout" in entry and "stderr" in entry


def test_shim_fails_open_without_log_env(tmp_path):
    env = dict(os.environ)
    env["OVERTURE_EVAL_PYTHON"] = sys.executable
    env.pop("OVERTURE_EVAL_LOG", None)
    proc = subprocess.run([sys.executable, str(SHIM), "--version"], env=env, capture_output=True, text=True)
    assert proc.returncode == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_eval_shim.py -v`
Expected: FAIL — shim file does not exist (`FileNotFoundError` / nonzero).

- [ ] **Step 3: Implement the shim**

Create `evals/shim/overturemaps`:
```python
#!/usr/bin/env python3
"""PATH shim: logs every `overturemaps` invocation, then runs the real CLI.

Placed first on PATH as `overturemaps`. Runs the real CLI via
`<python> -m overturemaps` (module form, so it never re-invokes this shim),
captures argv / exit code / stdout / stderr to the JSON-lines file named by
$OVERTURE_EVAL_LOG, then reproduces stdout/stderr and the exit code
unchanged. Fails open: if logging breaks, the real CLI still runs and the
agent sees normal output.

Env:
  OVERTURE_EVAL_PYTHON  python interpreter that has `overturemaps` installed
                        (defaults to this script's interpreter)
  OVERTURE_EVAL_LOG     append-target for the JSON-lines call log (optional)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time


def main() -> int:
    argv = sys.argv[1:]
    python = os.environ.get("OVERTURE_EVAL_PYTHON") or sys.executable

    start = time.monotonic()
    proc = subprocess.run(
        [python, "-m", "overturemaps", *argv],
        capture_output=True,
        text=True,
    )
    duration = time.monotonic() - start

    log_path = os.environ.get("OVERTURE_EVAL_LOG")
    if log_path:
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(
                    json.dumps(
                        {
                            "argv": argv,
                            "exit_code": proc.returncode,
                            "stdout": proc.stdout,
                            "stderr": proc.stderr,
                            "duration": duration,
                        }
                    )
                    + "\n"
                )
        except OSError:
            pass  # fail open — logging must never break the agent's run

    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Make it executable**

Run:
```bash
chmod +x evals/shim/overturemaps
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_eval_shim.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git update-index --chmod=+x evals/shim/overturemaps 2>/dev/null || true
git add evals/shim/overturemaps tests/test_eval_shim.py
git commit -m "feat(evals): logging PATH shim for the CLI"
```

---

### Task 6: `runner.py` — drive headless Claude Code

**Files:**
- Create: `evals/runner.py`

This module performs live, networked work (cache warm + `claude -p`), so it is verified by a smoke run rather than unit tests, per the spec's testing strategy.

- [ ] **Step 1: Implement `evals/runner.py`**

Create `evals/runner.py`:
```python
#!/usr/bin/env python3
"""Drive `claude -p` over the question bank, capturing per-run traces.

For each (question x repeat): set up an isolated working dir with the
Overture Skill installed project-scope and the logging shim first on PATH,
invoke headless Claude Code, and persist the transcript + shim log under
evals/runs/<id>__r<n>/.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from importlib import resources
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
EVALS_DIR = REPO_ROOT / "evals"
SHIM_PATH = EVALS_DIR / "shim" / "overturemaps"
DEFAULT_QUESTIONS = EVALS_DIR / "questions.yaml"
DEFAULT_RUNS_DIR = EVALS_DIR / "runs"
CLAUDE_TIMEOUT_S = 900


def venv_python() -> str:
    """Prefer the project venv (uv sync); fall back to the current interpreter."""
    candidate = REPO_ROOT / ".venv" / "bin" / "python"
    return str(candidate) if candidate.exists() else sys.executable


def packaged_skill_text() -> str:
    return (resources.files("overturemaps") / "data" / "skill.md").read_text()


def load_questions(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, list):
        raise SystemExit(f"{path} must contain a YAML list of questions")
    return data


def ensure_cache(python: str) -> None:
    """Warm the divisions index once so runs don't measure index-build time."""
    probe = subprocess.run(
        [python, "-m", "overturemaps", "where", "Brooklyn, US-NY", "--json"],
        capture_output=True,
        text=True,
    )
    if probe.returncode == 0:
        return
    print("[eval] warming divisions cache (one-time)...", file=sys.stderr)
    build = subprocess.run([python, "-m", "overturemaps", "cache", "build"])
    if build.returncode != 0:
        raise SystemExit("[eval] cache build failed; aborting batch")


def install_skill(workdir: Path) -> None:
    target = workdir / ".claude" / "skills" / "overturemaps" / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(packaged_skill_text())


def cost_guard(question: dict, python: str, max_deg2: float, strict: bool) -> bool:
    """Return True to run the question; False to skip it (strict mode only)."""
    place = question.get("place")
    if not place:
        return True
    res = subprocess.run(
        [python, "-m", "overturemaps", "where", place, "--json"],
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        return True
    try:
        feat = json.loads(res.stdout)
        bbox = feat.get("bbox") or feat["features"][0]["bbox"]
        xmin, ymin, xmax, ymax = bbox
        area = abs((xmax - xmin) * (ymax - ymin))
    except (KeyError, IndexError, ValueError, TypeError, json.JSONDecodeError):
        return True
    if area > max_deg2:
        msg = f"[eval] {question['id']}: bbox area {area:.2f} deg^2 exceeds guard {max_deg2}"
        if strict:
            print(msg + " -> SKIPPED", file=sys.stderr)
            return False
        print(msg + " -> running anyway (use --strict-cost-guard to skip)", file=sys.stderr)
    return True


def run_one(question: dict, repeat: int, model: str, runs_dir: Path, python: str) -> None:
    out_dir = runs_dir / f"{question['id']}__r{repeat}"
    out_dir.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix=f"eval-{question['id']}-"))
    try:
        install_skill(workdir)
        shim_log = workdir / "shim.log"
        env = dict(os.environ)
        env["OVERTURE_EVAL_LOG"] = str(shim_log)
        env["OVERTURE_EVAL_PYTHON"] = python
        env["PATH"] = os.pathsep.join(
            [str(SHIM_PATH.parent), str(Path(python).parent), env.get("PATH", "")]
        )

        transcript = out_dir / "transcript.jsonl"
        claude_err = out_dir / "claude_stderr.log"
        cmd = [
            "claude", "-p", question["question"],
            "--output-format", "stream-json", "--verbose",
            "--model", model,
            "--permission-mode", "bypassPermissions",
            "--allowedTools", "Bash",
        ]
        with open(transcript, "w") as tf, open(claude_err, "w") as ef:
            try:
                subprocess.run(cmd, cwd=workdir, env=env, stdout=tf, stderr=ef, timeout=CLAUDE_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                ef.write(f"\n[eval] claude run timed out after {CLAUDE_TIMEOUT_S}s\n")
            except FileNotFoundError:
                raise SystemExit("[eval] `claude` CLI not found on PATH; install Claude Code first")

        shutil.copy(shim_log, out_dir / "shim.log") if shim_log.exists() else (out_dir / "shim.log").write_text("")
        print(f"[eval] captured {out_dir.relative_to(REPO_ROOT)}", file=sys.stderr)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main() -> None:
    p = argparse.ArgumentParser(description="Run the Overture CLI agent-usability eval.")
    p.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    p.add_argument("--repeats", type=int, default=2)
    p.add_argument("--model", default="sonnet", help="Model alias passed to `claude -p` (e.g. sonnet, opus).")
    p.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    p.add_argument("--smoke", action="store_true", help="Run only the first question once.")
    p.add_argument("--max-bbox-deg2", type=float, default=2.0)
    p.add_argument("--strict-cost-guard", action="store_true")
    args = p.parse_args()

    questions = load_questions(args.questions)
    repeats = args.repeats
    if args.smoke:
        questions = questions[:1]
        repeats = 1

    python = venv_python()
    os.chmod(SHIM_PATH, 0o755)
    ensure_cache(python)
    args.runs_dir.mkdir(parents=True, exist_ok=True)

    for q in questions:
        if not cost_guard(q, python, args.max_bbox_deg2, args.strict_cost_guard):
            continue
        for r in range(1, repeats + 1):
            run_one(q, r, args.model, args.runs_dir, python)
    print(f"[eval] done. Artifacts under {args.runs_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-test the runner end-to-end (live, networked)**

Run:
```bash
uv run python -m evals.runner --smoke --model sonnet
```
Expected: a one-question run completes; `evals/runs/coffee-brooklyn-count__r1/` contains a non-empty `transcript.jsonl` and a `shim.log` with at least one JSON line whose `argv[0]` is an Overture subcommand. If `claude` is not installed, you get the clear "`claude` CLI not found" error — install Claude Code and retry.

- [ ] **Step 3: Verify the smoke run scores**

Run:
```bash
uv run python -m evals.score
```
Expected: prints a `[score] coffee-brooklyn-count__r1: ...` line and writes `evals/runs/coffee-brooklyn-count__r1/record.json`.

- [ ] **Step 4: Commit**

```bash
git add evals/runner.py
git commit -m "feat(evals): headless Claude Code runner with cache warm + cost guard"
```

---

### Task 7: `synthesize.py` — aggregate + propose improvements

**Files:**
- Create: `evals/synthesize.py`

Like the runner, this calls `claude -p`; it is verified by running it over scored fixture/smoke records. It degrades gracefully (writes the aggregate report without proposals) when `claude` is unavailable or returns unparseable output.

- [ ] **Step 1: Implement `evals/synthesize.py`**

Create `evals/synthesize.py`:
```python
#!/usr/bin/env python3
"""Aggregate scored runs into a ranked report + concrete change proposals.

Reads evals/runs/*__r*/record.json, builds per-question rates and failure
clusters, asks `claude -p` to turn the clusters into concrete, evidence-backed
proposals, and writes evals/report.md + evals/proposals.json. Treats ALL
download usage as material: unnecessary downloads are agent failures;
legitimate downloads are coverage-gap candidates for new verbs/aliases/hints.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUNS_DIR = REPO_ROOT / "evals" / "runs"
DEFAULT_REPORT = REPO_ROOT / "evals" / "report.md"
DEFAULT_PROPOSALS = REPO_ROOT / "evals" / "proposals.json"


def load_records(runs_dir: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(runs_dir.glob("*__r*/record.json"))]


def aggregate(records: list[dict]) -> dict:
    by_q: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_q[r["question_id"]].append(r)

    per_question = {}
    for qid, runs in by_q.items():
        n = len(runs)
        per_question[qid] = {
            "tier": runs[0]["tier"],
            "runs": n,
            "download_rate": sum(r["download_used"] for r in runs) / n,
            "unnecessary_download_rate": sum(r["unnecessary_download"] for r in runs) / n,
            "error_rate": sum(r["cli_error_count"] > 0 for r in runs) / n,
            "completion_rate": sum(r["completed"] for r in runs) / n,
            "avg_commands": sum(r["command_count"] for r in runs) / n,
        }

    error_clusters: dict[tuple, list] = defaultdict(list)   # (taxonomy, qid) -> [argv,...]
    unnecessary_clusters: dict[str, list] = defaultdict(list)  # qid -> [argv,...]
    coverage_gaps: dict[str, list] = defaultdict(list)      # downloaded_type -> [qid,...]
    for r in records:
        for e in r["errors"]:
            error_clusters[(e["taxonomy"], r["question_id"])].append(e["argv"])
        if r["unnecessary_download"]:
            unnecessary_clusters[r["question_id"]].extend(r["download_argvs"])
        if r["legitimate_download"]:
            for t in (r["downloaded_types"] or ["<unknown>"]):
                coverage_gaps[t].append(r["question_id"])

    ranked_errors = sorted(
        ({"taxonomy": tax, "question_id": qid, "count": len(ex), "examples": ex[:3]}
         for (tax, qid), ex in error_clusters.items()),
        key=lambda c: c["count"],
        reverse=True,
    )
    return {
        "total_runs": len(records),
        "per_question": per_question,
        "ranked_error_clusters": ranked_errors,
        "unnecessary_download": {qid: ex[:3] for qid, ex in unnecessary_clusters.items()},
        "coverage_gaps": {t: sorted(set(qids)) for t, qids in coverage_gaps.items()},
    }


_PROPOSAL_PROMPT = """You are improving a geospatial CLI (`overturemaps`) so an AI agent never needs the low-level `download` command and never errors.

Below is aggregated evidence from an agent-usability eval. Produce concrete, specific improvement proposals. For each, name the target artifact and the exact change.

Return ONLY a JSON array (no prose, no code fences). Each element:
{"title": str, "target": "cli" | "skill" | "docs" | "hint", "evidence": str, "proposal": str}

Guidance:
- "unnecessary_download" clusters = the agent used `download` when a verb existed. Propose what would have steered it to the verb (clearer skill example, a hint, a better verb name).
- "coverage_gaps" = legitimate downloads by type. Propose a new convenience verb (or alias/hint) so the agent can avoid `download` for that type entirely.
- error clusters = propose a wording/affordance fix that prevents the error class.

EVIDENCE:
"""


def propose(summary: dict, model: str) -> list[dict]:
    prompt = _PROPOSAL_PROMPT + json.dumps(summary, indent=2)
    try:
        proc = subprocess.run(
            ["claude", "-p", prompt, "--output-format", "json", "--model", model],
            capture_output=True, text=True, timeout=300,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        print(f"[synthesize] proposals skipped ({exc}); writing aggregate-only report", file=sys.stderr)
        return []
    if proc.returncode != 0:
        print(f"[synthesize] claude returned {proc.returncode}; proposals skipped", file=sys.stderr)
        return []
    try:
        outer = json.loads(proc.stdout)
        text = outer.get("result", proc.stdout) if isinstance(outer, dict) else proc.stdout
    except json.JSONDecodeError:
        text = proc.stdout
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("["):]
    try:
        proposals = json.loads(text[text.find("["): text.rfind("]") + 1])
        return proposals if isinstance(proposals, list) else []
    except (json.JSONDecodeError, ValueError):
        print("[synthesize] could not parse proposals JSON; proposals skipped", file=sys.stderr)
        return []


def render_report(summary: dict, proposals: list[dict]) -> str:
    lines = ["# Agent-Usability Eval Report", ""]
    lines.append(f"Total runs scored: **{summary['total_runs']}**")
    lines.append("")
    lines.append("## Per-question rates")
    lines.append("")
    lines.append("| Question | Tier | Runs | Download | Unnecessary DL | Error | Completed | Avg cmds |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for qid, s in sorted(summary["per_question"].items()):
        lines.append(
            f"| {qid} | {s['tier']} | {s['runs']} | {s['download_rate']:.0%} | "
            f"{s['unnecessary_download_rate']:.0%} | {s['error_rate']:.0%} | "
            f"{s['completion_rate']:.0%} | {s['avg_commands']:.1f} |"
        )
    lines.append("")
    lines.append("## Ranked error clusters")
    lines.append("")
    if not summary["ranked_error_clusters"]:
        lines.append("_None._")
    for c in summary["ranked_error_clusters"]:
        lines.append(f"- **{c['taxonomy']}** in `{c['question_id']}` ×{c['count']}")
        for ex in c["examples"]:
            lines.append(f"  - `overturemaps {' '.join(ex)}`")
    lines.append("")
    lines.append("## Coverage gaps (legitimate downloads)")
    lines.append("")
    if not summary["coverage_gaps"]:
        lines.append("_None._")
    for t, qids in sorted(summary["coverage_gaps"].items()):
        lines.append(f"- type **{t}** — wanted by: {', '.join(qids)}")
    lines.append("")
    lines.append("## Unnecessary download (agent failures)")
    lines.append("")
    if not summary["unnecessary_download"]:
        lines.append("_None._")
    for qid, exs in sorted(summary["unnecessary_download"].items()):
        lines.append(f"- `{qid}`:")
        for ex in exs:
            lines.append(f"  - `overturemaps {' '.join(ex)}`")
    lines.append("")
    lines.append("## Proposed improvements")
    lines.append("")
    if not proposals:
        lines.append("_No proposals generated (claude unavailable or no failures)._")
    for i, p in enumerate(proposals, 1):
        lines.append(f"### {i}. {p.get('title', 'Untitled')}  _(target: {p.get('target', '?')})_")
        lines.append(f"**Evidence:** {p.get('evidence', '')}")
        lines.append("")
        lines.append(p.get("proposal", ""))
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> None:
    p = argparse.ArgumentParser(description="Synthesize eval records into a report + proposals.")
    p.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    p.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    p.add_argument("--proposals", type=Path, default=DEFAULT_PROPOSALS)
    p.add_argument("--model", default="opus", help="Model for the synthesis step.")
    args = p.parse_args()

    records = load_records(args.runs_dir)
    if not records:
        raise SystemExit(f"[synthesize] no record.json under {args.runs_dir}; run scorer first")
    summary = aggregate(records)
    proposals = propose(summary, args.model)
    args.report.write_text(render_report(summary, proposals))
    args.proposals.write_text(json.dumps(proposals, indent=2))
    print(f"[synthesize] wrote {args.report} and {args.proposals} "
          f"({len(proposals)} proposal(s) from {summary['total_runs']} run(s))")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Smoke-test synthesis over the scored smoke run**

Run:
```bash
uv run python -m evals.synthesize --model sonnet
```
Expected: writes `evals/report.md` (with the per-question table) and `evals/proposals.json`. If `claude` is reachable, the report's "Proposed improvements" section is populated; otherwise it degrades to the aggregate-only report without error.

- [ ] **Step 3: Commit**

```bash
git add evals/synthesize.py
git commit -m "feat(evals): aggregate records into ranked report + proposals"
```

---

### Task 8: `just eval` recipe + README + self-review

**Files:**
- Modify: `justfile`
- Create: `evals/README.md`

- [ ] **Step 1: Add the `eval` recipe**

Append to `justfile` (match the existing recipe style — tab/space indentation as in the file):
```just
# Run the agent-usability eval (live: drives claude -p against Overture S3)
[group('eval')]
eval model="sonnet":
    uv run python -m evals.runner --model {{ model }}
    uv run python -m evals.score
    uv run python -m evals.synthesize --model opus
    @echo "Report: evals/report.md  |  Proposals: evals/proposals.json"

# Single-question eval smoke test (one run, sonnet)
[group('eval')]
eval-smoke:
    uv run python -m evals.runner --smoke --model sonnet
    uv run python -m evals.score
    @echo "Smoke run captured under evals/runs/"
```

- [ ] **Step 2: Write `evals/README.md`**

Create `evals/README.md`:
```markdown
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
```

- [ ] **Step 3: Verify the recipe is wired up**

Run:
```bash
just --list | grep -A1 eval
```
Expected: `eval` and `eval-smoke` recipes appear under the `eval` group.

- [ ] **Step 4: Run the full unit-test suite (no network)**

Run:
```bash
uv run pytest tests/test_eval_trace.py tests/test_eval_taxonomy.py tests/test_eval_score.py tests/test_eval_shim.py -v
```
Expected: all eval unit tests pass.

- [ ] **Step 5: Confirm the whole non-integration suite still passes**

Run:
```bash
uv run pytest -m "not integration"
```
Expected: green (no regressions from the `pytest.ini` / package additions).

- [ ] **Step 6: Commit**

```bash
git add justfile evals/README.md
git commit -m "feat(evals): just eval recipe and README"
```

---

## Self-Review

**Spec coverage:**
- Question bank (§1) → Task 1 (`questions.yaml`, 10 questions, all tiers incl. T5 compound with `download_is_legitimate: false` and `subtasks`). ✓
- Runner (§2): isolated workdir, pre-warmed shared cache, shim on PATH, `claude -p --model … stream-json`, persisted artifacts → Task 6. ✓
- Hybrid trace capture (§3): shim (Task 5) + transcript parsing (Task 2). ✓
- Scorer (§4): `download_used` / `unnecessary_download` / `legitimate_download`, error taxonomy, `recovered`, command counts, `completed`, recorded `final_answer` → Tasks 3 + 4. ✓
- Synthesizer (§5): aggregate, cluster, rank, treat all downloads as material (coverage gaps + agent failures), `claude -p` proposals → `report.md` + `proposals.json` → Task 7. ✓
- Orchestration (§"Orchestration"): `just eval`, `evals/` separate from `benchmarks/`, standalone (not pytest) → Task 8. ✓
- Error handling (§"Error handling"): run-level failures captured (timeout/`claude` missing handled in runner, `run_status` via transcript), cache-warm abort, shim fail-open, cost guard → Tasks 5, 6. ✓
- Testing strategy (§"Testing"): scorer/taxonomy/shim unit-tested against fixtures; runner+synthesizer via smoke → Tasks 2-5 (unit), 6-7 (smoke). ✓

**Model decision (§Scope):** Sonnet default, `--model` flag pass-through → Task 6 (`--model`, default `sonnet`) and `just eval model="sonnet"`. ✓

**Placeholder scan:** No TBD/TODO; every code step contains complete, runnable code. ✓

**Type consistency:** `ShimCall`, `Transcript`, `Record`, `ErrorRecord`, `classify_error`, `score_run`, `record_to_dict`, `parse_shim_log`, `parse_transcript` are defined once and used with identical signatures across tasks. Run-dir convention `<id>__r<n>` is written by `runner.run_one` and parsed by `score.main` / `synthesize.load_records` consistently. Shim env vars `OVERTURE_EVAL_LOG` / `OVERTURE_EVAL_PYTHON` match between shim and runner. ✓
