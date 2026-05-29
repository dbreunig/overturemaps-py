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
