"""Tests for the Skill installer module."""

from pathlib import Path

import pytest

from botmap.skill_installer import (
    install_claude_user,
    install_claude_project,
    install_agents_md,
    AGENTS_START_MARKER,
    AGENTS_END_MARKER,
)


def test_install_claude_user_writes_skill_md(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    target = install_claude_user()
    assert target == tmp_path / ".claude" / "skills" / "botmap" / "SKILL.md"
    assert target.exists()
    text = target.read_text()
    assert "name: botmap" in text


def test_install_claude_project_uses_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = install_claude_project()
    assert target == tmp_path / ".claude" / "skills" / "botmap" / "SKILL.md"
    assert target.exists()


def test_install_agents_md_creates_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = install_agents_md()
    assert target == tmp_path / "AGENTS.md"
    text = target.read_text()
    assert AGENTS_START_MARKER in text
    assert AGENTS_END_MARKER in text
    assert "Overture Maps CLI" in text


def test_install_agents_md_replaces_existing_block(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agents = tmp_path / "AGENTS.md"
    agents.write_text(
        "# Project agent guide\n\n"
        "Some pre-existing content.\n\n"
        f"{AGENTS_START_MARKER}\n"
        "old skill content\n"
        f"{AGENTS_END_MARKER}\n\n"
        "More after.\n"
    )

    install_agents_md()

    text = agents.read_text()
    assert "Some pre-existing content" in text
    assert "More after" in text
    assert "old skill content" not in text
    assert "Overture Maps CLI" in text


def test_install_agents_md_appends_when_no_marker(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agents = tmp_path / "AGENTS.md"
    agents.write_text("# Project guide\n\nExisting prose.\n")

    install_agents_md()

    text = agents.read_text()
    assert "Existing prose" in text
    assert AGENTS_START_MARKER in text
    assert "Overture Maps CLI" in text
