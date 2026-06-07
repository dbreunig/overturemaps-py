"""Tests for botmap/__main__.py (PyInstaller / python -m botmap entry point)."""

import runpy
import sys
from unittest.mock import patch

import pytest


def test_main_invokes_cli():
    """__main__.py calls cli() when executed as __main__."""
    with patch("botmap.cli.cli") as mock_cli:
        mock_cli.side_effect = SystemExit(0)
        with pytest.raises(SystemExit):
            runpy.run_module("botmap", run_name="__main__", alter_sys=True)
        mock_cli.assert_called_once()


def test_main_importable():
    """botmap.__main__ is importable without side effects."""
    import importlib

    mod = importlib.import_module("botmap.__main__")
    assert hasattr(mod, "cli")


def test_python_m_botmap_help(capsys):
    """python -m botmap --help exits 0 and prints usage."""
    with pytest.raises(SystemExit) as exc_info:
        with patch("sys.argv", ["botmap", "--help"]):
            runpy.run_module("botmap", run_name="__main__", alter_sys=True)
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "Usage" in captured.out or "Usage" in captured.err
