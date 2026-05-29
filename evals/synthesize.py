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
        (
            {"taxonomy": tax, "question_id": qid, "count": len(ex), "examples": ex[:3]}
            for (tax, qid), ex in error_clusters.items()
        ),
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
    print(
        f"[synthesize] wrote {args.report} and {args.proposals} "
        f"({len(proposals)} proposal(s) from {summary['total_runs']} run(s))"
    )


if __name__ == "__main__":
    main()
