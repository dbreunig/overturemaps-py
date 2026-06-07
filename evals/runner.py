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
SHIM_PATH = EVALS_DIR / "shim" / "botmap"
DEFAULT_QUESTIONS = EVALS_DIR / "questions.yaml"
DEFAULT_RUNS_DIR = EVALS_DIR / "runs"
CLAUDE_TIMEOUT_S = 900


def venv_python() -> str:
    """Prefer the project venv (uv sync); fall back to the current interpreter."""
    candidate = REPO_ROOT / ".venv" / "bin" / "python"
    return str(candidate) if candidate.exists() else sys.executable


def packaged_skill_text() -> str:
    return (resources.files("botmap") / "data" / "skill.md").read_text()


def load_questions(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, list):
        raise SystemExit(f"{path} must contain a YAML list of questions")
    return data


def ensure_cache(python: str) -> None:
    """Warm the divisions index once so runs don't measure index-build time."""
    probe = subprocess.run(
        [python, "-m", "botmap", "where", "Brooklyn, US-NY", "--json"],
        capture_output=True,
        text=True,
    )
    if probe.returncode == 0:
        return
    print("[eval] warming divisions cache (one-time)...", file=sys.stderr)
    build = subprocess.run([python, "-m", "botmap", "cache", "build"])
    if build.returncode != 0:
        raise SystemExit("[eval] cache build failed; aborting batch")


def install_skill(workdir: Path) -> None:
    target = workdir / ".claude" / "skills" / "botmap" / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(packaged_skill_text())


def cost_guard(question: dict, python: str, max_deg2: float, strict: bool) -> bool:
    """Return True to run the question; False to skip it (strict mode only)."""
    place = question.get("place")
    if not place:
        return True
    res = subprocess.run(
        [python, "-m", "botmap", "where", place, "--json"],
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

        if shim_log.exists():
            shutil.copy(shim_log, out_dir / "shim.log")
        else:
            (out_dir / "shim.log").write_text("")
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
