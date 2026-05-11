"""Install the agent Skill / AGENTS.md content to user-chosen targets."""

from __future__ import annotations

import os
import re
from importlib import resources
from pathlib import Path


AGENTS_START_MARKER = "<!-- overturemaps:start -->"
AGENTS_END_MARKER = "<!-- overturemaps:end -->"


def _skill_content() -> str:
    """Return the canonical SKILL.md content shipped with the package."""
    return (resources.files("overturemaps") / "data" / "skill.md").read_text()


def _claude_user_dir() -> Path:
    return Path(os.environ.get("HOME", "~")).expanduser() / ".claude" / "skills" / "overturemaps"


def _claude_project_dir() -> Path:
    return Path.cwd() / ".claude" / "skills" / "overturemaps"


def install_claude_user() -> Path:
    """Write the SKILL.md to the user-scope Claude Code skills dir."""
    target = _claude_user_dir() / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_skill_content())
    return target


def install_claude_project() -> Path:
    """Write the SKILL.md to the project-scope Claude Code skills dir."""
    target = _claude_project_dir() / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_skill_content())
    return target


def _agents_md_section() -> str:
    body = _skill_content()
    # Strip the YAML frontmatter for the AGENTS.md target — humans there
    # don't need it and it confuses some markdown viewers.
    if body.startswith("---"):
        end = body.find("\n---", 3)
        if end != -1:
            body = body[end + len("\n---"):].lstrip("\n")
    return f"{AGENTS_START_MARKER}\n{body}\n{AGENTS_END_MARKER}\n"


def install_agents_md() -> Path:
    """Insert or replace the overturemaps section in ./AGENTS.md."""
    target = Path.cwd() / "AGENTS.md"
    section = _agents_md_section()
    if not target.exists():
        target.write_text(section)
        return target

    existing = target.read_text()
    pattern = re.compile(
        re.escape(AGENTS_START_MARKER) + r".*?" + re.escape(AGENTS_END_MARKER) + r"\n?",
        re.DOTALL,
    )
    if pattern.search(existing):
        new = pattern.sub(section, existing)
    else:
        sep = "" if existing.endswith("\n") else "\n"
        new = existing + sep + "\n" + section
    target.write_text(new)
    return target
