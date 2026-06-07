"""The SKILL.md content must be readable via importlib.resources."""

from importlib import resources


def test_skill_md_present():
    files = resources.files("botmap") / "data" / "skill.md"
    text = files.read_text()
    assert "name: botmap" in text
    assert "When to reach for this CLI" in text
