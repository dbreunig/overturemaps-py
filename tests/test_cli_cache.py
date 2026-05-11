"""Tests for the `cache` command group."""

import json
from pathlib import Path

from click.testing import CliRunner

from overturemaps.cli import cli


def test_cache_info_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr("overturemaps.cli.get_latest_release",
                        lambda: "2025-12-17.0")

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "cache", "info"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["latest_release"] == "2025-12-17.0"
    assert data["up_to_date"] is False
    assert data["index_release"] is None


def test_cache_clear_removes_files(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    from overturemaps.cache import index_path
    p = index_path("2025-12-17.0")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")

    runner = CliRunner()
    result = runner.invoke(cli, ["cache", "clear"])
    assert result.exit_code == 0
    assert not p.exists()
    assert "1" in result.output or "Removed" in result.output


def test_cache_build_invokes_build_index(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr("overturemaps.cli.get_latest_release",
                        lambda: "2025-12-17.0")

    called = []
    def fake_build(release):
        called.append(release)
        from overturemaps.cache import index_path
        p = index_path(release)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"built")
        return p
    monkeypatch.setattr("overturemaps.cli.build_index", fake_build)

    runner = CliRunner()
    result = runner.invoke(cli, ["cache", "build"])
    assert result.exit_code == 0, result.output
    assert called == ["2025-12-17.0"]
