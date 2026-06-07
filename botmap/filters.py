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
    # User-facing ops from --where: =, !=, <, <=, >, >=, in
    # Internal ops synthesized by subcommands (not parsed from --where):
    #   ~ : case-insensitive substring match (used by `addresses --street`)
    op: str
    value: Any  # str | int | float | bool | list

    def to_pyarrow_expression(self, schema: pa.Schema) -> pc.Expression:
        """Build a pc.Expression resolving dotted keys against `schema`."""
        # Walk dotted path -> pc.field(...) with nested struct access.
        parts = self.key.split(".")
        field_ref = pc.field(*parts) if len(parts) > 1 else pc.field(parts[0])

        if self.op == "=":
            return field_ref == self.value
        if self.op == "!=":
            return field_ref != self.value
        if self.op == "<":
            return field_ref < self.value
        if self.op == "<=":
            return field_ref <= self.value
        if self.op == ">":
            return field_ref > self.value
        if self.op == ">=":
            return field_ref >= self.value
        if self.op == "in":
            return field_ref.isin(self.value)
        if self.op == "~":
            return pc.match_substring(field_ref, self.value, ignore_case=True)
        raise ValueError(f"Unsupported operator: {self.op!r}")

    def validate_against_schema(self, schema: pa.Schema) -> None:
        """Verify self.key resolves to a field in `schema`; raise ValueError otherwise."""
        parts = self.key.split(".")
        # Top-level lookup
        top = parts[0]
        if top not in schema.names:
            raise ValueError(
                f"Unknown field {self.key!r}. Available fields: "
                f"{', '.join(sorted(schema.names))}"
            )
        # Walk into nested struct types if dotted
        current_type = schema.field(top).type
        for i, part in enumerate(parts[1:], start=1):
            if not pa.types.is_struct(current_type):
                raise ValueError(
                    f"Field {self.key!r} is invalid: "
                    f"{'.'.join(parts[:i])} is not a struct"
                )
            child_names = [current_type.field(j).name for j in range(current_type.num_fields)]
            if part not in child_names:
                raise ValueError(
                    f"Unknown field {self.key!r}. "
                    f"Available subfields: {', '.join(sorted(child_names))}"
                )
            current_type = current_type.field(child_names.index(part)).type


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
        raise ValueError(
            f"Filter {expr!r} has no operator. Use K OP V, e.g. "
            f"--where 'height>150'. If you typed an unquoted > or <, your "
            f"shell redirected it to a file — wrap the whole expression in "
            f"single quotes."
        )

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


def combine(filters: List[ParsedFilter], schema: pa.Schema) -> pc.Expression | None:
    """AND-combine filters, validating each against the schema."""
    if not filters:
        return None
    for f in filters:
        f.validate_against_schema(schema)
    exprs = [f.to_pyarrow_expression(schema) for f in filters]
    result = exprs[0]
    for e in exprs[1:]:
        result = result & e
    return result
