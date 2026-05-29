from pathlib import Path

from evals.trace import (
    ShimCall,
    Transcript,
    parse_shim_log,
    parse_transcript,
)

FIXTURES = Path(__file__).parent / "eval_fixtures"


def test_parse_shim_log_reads_each_call():
    calls = parse_shim_log(FIXTURES / "shim_sample.log")
    assert len(calls) == 3
    assert calls[0].argv == ["places", "--in", "Brooklyn", "--category", "cafe"]
    assert calls[0].exit_code == 0
    assert "Did you mean: coffee_shop" in calls[0].stderr
    assert calls[2].argv[0] == "download"


def test_shim_call_subcommand_skips_options():
    assert ShimCall(["places", "--in", "X"], 0, "", "").subcommand == "places"
    assert ShimCall(["--json", "where", "Boston"], 0, "", "").subcommand == "where"
    assert ShimCall(["--version"], 0, "", "").subcommand is None


def test_parse_shim_log_missing_file_is_empty():
    assert parse_shim_log(FIXTURES / "does_not_exist.log") == []


def test_parse_transcript_extracts_final_answer():
    t = parse_transcript(FIXTURES / "transcript_sample.jsonl")
    assert isinstance(t, Transcript)
    assert "Brooklyn" in t.final_answer
    assert t.completed is True
    assert t.run_status == "ok"


def test_parse_transcript_missing_file_is_error():
    t = parse_transcript(FIXTURES / "nope.jsonl")
    assert t.completed is False
    assert t.run_status == "error"
