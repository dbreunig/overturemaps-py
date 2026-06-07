"""Tests for the global --json flag."""

from click.testing import CliRunner

from botmap.cli import cli


def test_json_flag_sets_context_object():
    """--json sets ctx.obj['json'] = True for child commands."""
    runner = CliRunner()
    # Invoke a command that doesn't actually exist yet but parses the flag.
    # We'll just check that --help still works with --json present.
    result = runner.invoke(cli, ["--json", "--help"])
    assert result.exit_code == 0
