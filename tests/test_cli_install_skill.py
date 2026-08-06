"""Tests for the `install-skill` CLI command."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from botmap.cli import cli


def test_install_skill_non_interactive_claude_user(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, [
            "install-skill", "--target", "claude-user", "--yes",
        ])
        assert result.exit_code == 0, result.output
        expected = tmp_path / ".claude" / "skills" / "botmap" / "SKILL.md"
        assert expected.exists()


def test_install_skill_multiple_targets(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, [
            "install-skill",
            "--target", "claude-user",
            "--target", "agents-md",
            "--yes",
        ])
        assert result.exit_code == 0, result.output
        assert (tmp_path / ".claude" / "skills" / "botmap" / "SKILL.md").exists()


def test_install_skill_pi_user(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, [
            "install-skill", "--target", "pi-user", "--yes",
        ])
        assert result.exit_code == 0, result.output
        expected = tmp_path / ".pi" / "agent" / "skills" / "botmap" / "SKILL.md"
        assert expected.exists()
        assert "name: botmap" in expected.read_text()


def test_install_skill_pi_project(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, [
            "install-skill", "--target", "pi-project", "--yes",
        ])
        assert result.exit_code == 0, result.output
        expected = Path.cwd() / ".pi" / "skills" / "botmap" / "SKILL.md"
        assert expected.exists()
        assert "name: botmap" in expected.read_text()


def test_install_skill_rejects_unknown_target():
    runner = CliRunner()
    result = runner.invoke(cli, [
        "install-skill", "--target", "not-a-target", "--yes",
    ])
    assert result.exit_code != 0
