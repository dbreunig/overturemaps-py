from evals.taxonomy import classify_error
from evals.trace import ShimCall


def _call(exit_code=0, stderr=""):
    return ShimCall(argv=["places"], exit_code=exit_code, stdout="", stderr=stderr)


def test_clean_success_is_none():
    assert classify_error(_call(0, "")) is None


def test_ambiguous_warning_is_not_an_error():
    # The CLI prints this on exit 0 as an informational warning.
    stderr = "[botmap] Ambiguous --in 'Springfield': picked Springfield, US-IL"
    assert classify_error(_call(0, stderr)) is None


def test_bad_category_value_from_did_you_mean_hint():
    stderr = "[botmap] 0 rows. No place has categories.primary='cafe'. Did you mean: coffee_shop?"
    assert classify_error(_call(0, stderr)) == "bad_category_value"


def test_bad_category_value_from_not_present_hint():
    stderr = "[botmap] 0 rows. categories.primary='zzz' is not present in this bbox."
    assert classify_error(_call(0, stderr)) == "bad_category_value"


def test_unknown_command():
    assert classify_error(_call(2, "Error: No such command 'plces'.")) == "unknown_command"


def test_bad_option():
    assert classify_error(_call(2, "Error: No such option: --categ")) == "bad_option"


def test_usage_error_is_bad_option():
    stderr = "Usage: botmap places [OPTIONS]\nError: --bbox and --in are mutually exclusive"
    assert classify_error(_call(2, stderr)) == "bad_option"


def test_malformed_coords():
    assert classify_error(_call(2, "Error: LATLON must be 'LAT,LON'")) == "malformed_bbox_or_coords"


def test_wrong_type():
    assert classify_error(_call(1, "Error: No features available for type 'place'")) == "wrong_type_for_question"


def test_traceback():
    stderr = "Traceback (most recent call last):\n  File ...\nValueError: boom"
    assert classify_error(_call(1, stderr)) == "traceback"


def test_unrecognized_nonzero_is_other_error():
    assert classify_error(_call(1, "segmentation fault")) == "other_error"
