from evals.score import score_run, record_to_dict
from evals.trace import ShimCall, Transcript


def _ok(t):
    return Transcript(final_answer="answer", completed=True, run_status="ok")


def test_unnecessary_download_flagged_when_verb_exists():
    q = {"id": "q1", "tier": 1, "download_is_legitimate": False}
    calls = [ShimCall(["download", "-t", "place"], 0, "", "")]
    rec = score_run(q, calls, _ok(None), repeat=1)
    assert rec.download_used is True
    assert rec.unnecessary_download is True
    assert rec.legitimate_download is False
    assert rec.downloaded_types == ["place"]


def test_legitimate_download_is_coverage_gap_not_failure():
    q = {"id": "q7", "tier": 4, "download_is_legitimate": True}
    calls = [ShimCall(["download", "-t", "water"], 0, "", "")]
    rec = score_run(q, calls, _ok(None), repeat=1)
    assert rec.download_used is True
    assert rec.unnecessary_download is False
    assert rec.legitimate_download is True
    assert rec.downloaded_types == ["water"]


def test_error_count_and_recovery():
    q = {"id": "q1", "tier": 2, "download_is_legitimate": False}
    calls = [
        ShimCall(["places", "--in", "X", "--category", "cafe"], 0, "", "Did you mean: coffee_shop?"),
        ShimCall(["places", "--in", "X", "--category", "coffee_shop"], 0, "{}", ""),
    ]
    rec = score_run(q, calls, _ok(None), repeat=2)
    assert rec.cli_error_count == 1
    assert rec.errors[0].taxonomy == "bad_category_value"
    assert rec.recovered is True
    assert rec.command_count == 2
    assert rec.wasted_commands == 1
    assert rec.repeat == 2


def test_no_recovery_when_no_success_follows_error():
    q = {"id": "q1", "tier": 1, "download_is_legitimate": False}
    calls = [ShimCall(["plces"], 2, "", "Error: No such command 'plces'.")]
    rec = score_run(q, calls, _ok(None), repeat=1)
    assert rec.cli_error_count == 1
    assert rec.recovered is False


def test_record_to_dict_is_json_serializable():
    import json
    q = {"id": "q1", "tier": 1, "download_is_legitimate": False}
    calls = [ShimCall(["download", "-t", "place"], 0, "", "")]
    rec = score_run(q, calls, _ok(None), repeat=1)
    d = record_to_dict(rec)
    json.dumps(d)  # must not raise
    assert d["question_id"] == "q1"
    assert d["errors"] == []
