import json
import os
import subprocess
import sys
from pathlib import Path

SHIM = Path(__file__).resolve().parent.parent / "evals" / "shim" / "botmap"


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
    assert "botmap" in (proc.stdout + proc.stderr).lower() or proc.stdout.strip()


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
