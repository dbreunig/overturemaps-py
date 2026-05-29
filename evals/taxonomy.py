"""Classify a captured CLI call into an error-taxonomy label.

Returns None for a clean call (success with no recognized soft-failure
signal). The classifier is best-effort: it fingerprints exit codes and
stderr patterns the Overture CLI actually emits. Order matters — the most
specific patterns are checked first.
"""

from __future__ import annotations

from .trace import ShimCall

_TRACEBACK = "Traceback (most recent call last)"


def classify_error(call: ShimCall) -> str | None:
    err = call.stderr or ""
    low = err.lower()

    # Exit 0: only a recognized soft failure counts. The ambiguous-`--in`
    # warning is informational and deliberately NOT treated as an error.
    if call.exit_code == 0:
        if "did you mean:" in low:
            return "bad_category_value"
        if "0 rows" in low and "categories.primary" in low:
            return "bad_category_value"
        return None

    # Nonzero exit: a hard failure. Most specific patterns first.
    if _TRACEBACK in err:
        return "traceback"
    if "no such command" in low:
        return "unknown_command"
    if "no such option" in low:
        return "bad_option"
    if "latlon" in low or "lat,lon" in low or "must be numeric" in low:
        return "malformed_bbox_or_coords"
    if "bbox" in low and ("invalid" in low or "must" in low or "expected" in low):
        return "malformed_bbox_or_coords"
    if "no features available for type" in low:
        return "wrong_type_for_question"
    if "usage:" in low and "error:" in low:
        return "bad_option"
    return "other_error"
