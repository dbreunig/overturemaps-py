"""Parser and PyArrow translator for the --where filter flag."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Union

import pyarrow as pa
import pyarrow.compute as pc


# Operators ordered longest-first so the splitter doesn't mistake `>=` for `>`.
_OPERATORS = ["<=", ">=", "!=", " in ", "=", "<", ">"]


@dataclass(frozen=True)
class ParsedFilter:
    key: str
    op: str  # one of: =, !=, <, <=, >, >=, in
    value: Any  # str | int | float | bool | list


def _coerce_scalar(raw: str) -> Union[str, int, float, bool]:
    s = raw.strip()
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    # strip optional surrounding quotes
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return s


def _parse_list_value(raw: str) -> List[Any]:
    s = raw.strip()
    if not (s.startswith("[") and s.endswith("]")):
        raise ValueError(f"`in` value must be a [a,b,c] list, got: {raw!r}")
    inner = s[1:-1]
    parts = [p for p in (chunk.strip() for chunk in inner.split(",")) if p]
    return [_coerce_scalar(p) for p in parts]


def parse_where_expr(expr: str) -> ParsedFilter:
    """Parse a single --where expression of the form 'KEY OP VALUE'."""
    # Locate the leftmost occurrence of any operator, preferring longer matches.
    best_idx = -1
    best_op = None
    for op in _OPERATORS:
        idx = expr.find(op)
        if idx == -1:
            continue
        # Prefer earlier-starting matches; on tie, prefer longer operator
        if best_idx == -1 or idx < best_idx or (idx == best_idx and len(op) > len(best_op)):
            best_idx = idx
            best_op = op

    if best_op is None:
        raise ValueError(f"Could not parse --where expression: {expr!r}")

    key = expr[:best_idx].strip()
    value_raw = expr[best_idx + len(best_op):].strip()
    op = best_op.strip()  # ' in ' -> 'in'

    if not key:
        raise ValueError(f"--where expression has empty key: {expr!r}")
    if not value_raw:
        raise ValueError(f"--where expression has empty value: {expr!r}")

    if op == "in":
        value = _parse_list_value(value_raw)
    else:
        value = _coerce_scalar(value_raw)

    return ParsedFilter(key=key, op=op, value=value)
