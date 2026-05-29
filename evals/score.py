"""Score a single eval run from its captured trace, and batch-score runs/."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
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
