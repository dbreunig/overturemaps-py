# Agent-Friendly CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `overturemaps` with place-name geocoding, push-down attribute filters, count/sample previews, intent verbs, JSON metadata output, and a Claude Code / AGENTS.md Skill installer — strictly additive on top of the existing CLI.

**Architecture:** Six new modules (`filters`, `cache`, `geocoding`, `introspection`, `intents`, `skill_installer`) plus a package data file (`data/skill.md`). `cli.py` gains new commands but each remains a thin Click adapter. The existing `_prepare_query → record_batch_reader → copy` pipeline is reused unchanged; new flags compose into the same filter expression. The divisions index is built lazily on first `--in`/`where`/`containing` call and cached under XDG cache home.

**Tech Stack:** Python 3.10+, `click`, `pyarrow` (datasets, compute, parquet), `shapely`, `orjson`, `tqdm`, `pytest` + `CliRunner`. No new runtime dependencies.

**Spec:** `docs/superpowers/specs/2026-05-11-agent-friendly-cli-design.md`

## Phase Layout

This plan is organized into five phases. Each phase ends with a shippable state — pause for review between phases if running via `subagent-driven-development`.

| Phase | Tasks | Outcome |
|---|---|---|
| **1. Foundation** | 1.1 – 1.8 | `download --in "Boston" --where height>50` works. |
| **2. Metadata commands** | 2.1 – 2.10 | Full self-describing surface (`where`, `count`, `themes`, `types`, `schema`, `categories`, `capabilities`, `cache`) with `--json`. |
| **3. Intent verbs** | 3.1 – 3.6 | `places`, `buildings`, `roads`, `at`, `containing`. |
| **4. Skill installer** | 4.1 – 4.3 | `install-skill` writes Claude SKILL.md and/or `AGENTS.md`. |
| **5. Docs + release** | 5.1 – 5.2 | README updated; version bumped to `1.1.0`. |

## Repository Conventions (read once before starting)

- **Tests** use `pytest` with `monkeypatch` (not `pytest-mock` fixtures).
- **CLI tests** use `click.testing.CliRunner` and import `cli` from `overturemaps.cli`.
- **Network-touching tests** set a module-level `pytestmark = pytest.mark.integration` (see `tests/test_changelog.py` and `tests/test_gers.py` for examples).
- **Run unit tests:** `uv run pytest tests/ -m "not integration" -v`
- **Run integration tests:** `uv run pytest tests/ -m integration -v` (slow; needs network)
- **Click commands** live in `overturemaps/cli.py`; the module is the source of truth for the CLI group.
- **stdout = data, stderr = humans.** Banners, warnings, progress, GERS info all go to stderr. JSON metadata output goes to stdout when `--json` is set.
- **Commits** are small and frequent. Each task ends with a commit using the format from recent git history (no co-author trailer in this project; check `git log --oneline -5` before your first commit and match the prevailing style).

---

# Phase 1 — Foundation

End state: `overturemaps download -t building --in "Boston, MA" --where height>50 -f geojsonseq -o out.jsonl` produces buildings ≥ 50 m tall in the Boston, MA bbox.

---

## Task 1.1: Filter expression parser

Parses `--where "KEY OP VALUE"` strings into structured tuples. No schema validation yet (added in Task 1.3).

**Files:**
- Create: `overturemaps/filters.py`
- Create: `tests/test_filters.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_filters.py`:

```python
"""Tests for the --where filter parser."""

import pyarrow as pa
import pyarrow.compute as pc
import pytest

from overturemaps.filters import parse_where_expr, ParsedFilter


class TestParseWhereExpr:
    def test_equality_string(self):
        f = parse_where_expr("categories.primary=restaurant")
        assert f == ParsedFilter(key="categories.primary", op="=", value="restaurant")

    def test_equality_int(self):
        f = parse_where_expr("num_floors=10")
        assert f.value == 10

    def test_equality_float(self):
        f = parse_where_expr("confidence=0.85")
        assert f.value == 0.85

    def test_equality_bool_true(self):
        f = parse_where_expr("has_parts=true")
        assert f.value is True

    def test_equality_bool_false(self):
        f = parse_where_expr("has_parts=false")
        assert f.value is False

    def test_not_equal(self):
        f = parse_where_expr("class!=footway")
        assert f.op == "!="
        assert f.value == "footway"

    def test_gt(self):
        f = parse_where_expr("height>100")
        assert f.op == ">"
        assert f.value == 100

    def test_gte(self):
        f = parse_where_expr("height>=100")
        assert f.op == ">="

    def test_lt(self):
        f = parse_where_expr("height<50")
        assert f.op == "<"

    def test_lte(self):
        f = parse_where_expr("height<=50")
        assert f.op == "<="

    def test_in_list(self):
        f = parse_where_expr("class in [motorway,primary,trunk]")
        assert f.op == "in"
        assert f.value == ["motorway", "primary", "trunk"]

    def test_in_list_with_spaces(self):
        f = parse_where_expr("class in [ motorway , primary ]")
        assert f.value == ["motorway", "primary"]

    def test_in_list_typed(self):
        f = parse_where_expr("num_floors in [1,2,3]")
        assert f.value == [1, 2, 3]

    def test_longest_operator_wins(self):
        # ensure `>=` isn't misread as `>` followed by `=...`
        f = parse_where_expr("height>=100")
        assert f.op == ">="
        assert f.value == 100

    def test_dotted_key(self):
        f = parse_where_expr("bbox.xmin<-70")
        assert f.key == "bbox.xmin"
        assert f.value == -70

    def test_missing_operator_raises(self):
        with pytest.raises(ValueError, match="Could not parse"):
            parse_where_expr("just_a_key")

    def test_empty_value_raises(self):
        with pytest.raises(ValueError, match="empty value"):
            parse_where_expr("key=")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_filters.py -v`
Expected: ImportError or `ModuleNotFoundError: No module named 'overturemaps.filters'`.

- [ ] **Step 3: Write the parser**

Create `overturemaps/filters.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_filters.py -v`
Expected: all 17 tests pass.

- [ ] **Step 5: Commit**

```bash
git add overturemaps/filters.py tests/test_filters.py
git commit -m "Add --where expression parser (filters.py)

Parses 'KEY OP VALUE' strings into ParsedFilter dataclasses with
auto-typed values (int/float/bool/string) and [a,b,c] list literals.
Schema validation and PyArrow translation come next."
```

---

## Task 1.2: PyArrow Expression translation for `ParsedFilter`

Turn a `ParsedFilter` into a `pyarrow.compute.Expression` using nested struct field access.

**Files:**
- Modify: `overturemaps/filters.py`
- Modify: `tests/test_filters.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_filters.py`:

```python
class TestToPyarrowExpression:
    def test_simple_equality(self):
        schema = pa.schema([("class", pa.string())])
        f = ParsedFilter("class", "=", "motorway")
        expr = f.to_pyarrow_expression(schema)
        # Just verify it's an Expression; equality semantics are tested at
        # integration time against a real dataset.
        assert isinstance(expr, pc.Expression)

    def test_nested_field(self):
        schema = pa.schema([
            ("categories", pa.struct([("primary", pa.string())])),
        ])
        f = ParsedFilter("categories.primary", "=", "restaurant")
        expr = f.to_pyarrow_expression(schema)
        assert isinstance(expr, pc.Expression)

    def test_in_operator(self):
        schema = pa.schema([("class", pa.string())])
        f = ParsedFilter("class", "in", ["motorway", "primary"])
        expr = f.to_pyarrow_expression(schema)
        assert isinstance(expr, pc.Expression)

    def test_numeric_comparison(self):
        schema = pa.schema([("height", pa.float64())])
        f = ParsedFilter("height", ">", 100)
        expr = f.to_pyarrow_expression(schema)
        assert isinstance(expr, pc.Expression)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_filters.py::TestToPyarrowExpression -v`
Expected: `AttributeError: 'ParsedFilter' object has no attribute 'to_pyarrow_expression'`.

- [ ] **Step 3: Add the translator**

Replace the `ParsedFilter` dataclass in `overturemaps/filters.py` with this version (keep all other code in the file):

```python
@dataclass(frozen=True)
class ParsedFilter:
    key: str
    op: str
    value: Any

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
        raise ValueError(f"Unsupported operator: {self.op!r}")


def combine(filters: List[ParsedFilter], schema: pa.Schema) -> pc.Expression | None:
    """AND-combine multiple filters into one expression."""
    if not filters:
        return None
    exprs = [f.to_pyarrow_expression(schema) for f in filters]
    result = exprs[0]
    for e in exprs[1:]:
        result = result & e
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_filters.py -v`
Expected: all tests pass (parser + translator).

- [ ] **Step 5: Commit**

```bash
git add overturemaps/filters.py tests/test_filters.py
git commit -m "Translate ParsedFilter to pyarrow.compute.Expression

Supports dotted nested struct keys (e.g. categories.primary) and all
seven operators. Adds combine() to AND-conjunct multiple --where flags."
```

---

## Task 1.3: Schema-aware validation for filters

Reject `--where` expressions that reference fields that don't exist on the type's schema, with a helpful "did you mean" listing.

**Files:**
- Modify: `overturemaps/filters.py`
- Modify: `tests/test_filters.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_filters.py`:

```python
class TestValidateAgainstSchema:
    def test_top_level_field_ok(self):
        schema = pa.schema([("height", pa.float64())])
        f = ParsedFilter("height", ">", 50)
        # Should not raise
        f.validate_against_schema(schema)

    def test_nested_field_ok(self):
        schema = pa.schema([
            ("categories", pa.struct([("primary", pa.string())])),
        ])
        f = ParsedFilter("categories.primary", "=", "restaurant")
        f.validate_against_schema(schema)

    def test_unknown_top_level_raises(self):
        schema = pa.schema([("height", pa.float64())])
        f = ParsedFilter("widht", ">", 50)  # typo
        with pytest.raises(ValueError) as exc:
            f.validate_against_schema(schema)
        assert "widht" in str(exc.value)
        assert "available fields" in str(exc.value).lower()
        assert "height" in str(exc.value)

    def test_unknown_nested_raises(self):
        schema = pa.schema([
            ("categories", pa.struct([("primary", pa.string())])),
        ])
        f = ParsedFilter("categories.banana", "=", "x")
        with pytest.raises(ValueError) as exc:
            f.validate_against_schema(schema)
        assert "categories.banana" in str(exc.value)
        assert "primary" in str(exc.value)

    def test_dotted_into_non_struct_raises(self):
        schema = pa.schema([("height", pa.float64())])
        f = ParsedFilter("height.foo", "=", 1)
        with pytest.raises(ValueError):
            f.validate_against_schema(schema)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_filters.py::TestValidateAgainstSchema -v`
Expected: `AttributeError: 'ParsedFilter' object has no attribute 'validate_against_schema'`.

- [ ] **Step 3: Implement validation**

Add this method inside the `ParsedFilter` class in `overturemaps/filters.py`:

```python
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
        for part in parts[1:]:
            if not pa.types.is_struct(current_type):
                raise ValueError(
                    f"Field {self.key!r} is invalid: "
                    f"{'.'.join(parts[:parts.index(part)])} is not a struct"
                )
            child_names = [current_type.field(i).name for i in range(current_type.num_fields)]
            if part not in child_names:
                raise ValueError(
                    f"Unknown field {self.key!r}. "
                    f"Available subfields: {', '.join(sorted(child_names))}"
                )
            current_type = current_type.field(child_names.index(part)).type
```

Also extend `combine()` to validate first:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_filters.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add overturemaps/filters.py tests/test_filters.py
git commit -m "Validate --where keys against the type's PyArrow schema

Misnamed fields fail before the network call with a 'available fields'
listing, which is the agent's primary safety net against typos."
```

---

## Task 1.4: Cache module — XDG path, info, clear

The on-disk cache stores the divisions index. This task implements path resolution and read-side helpers. Index building comes in Task 1.5.

**Files:**
- Create: `overturemaps/cache.py`
- Create: `tests/test_cache.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cache.py`:

```python
"""Tests for the divisions-index cache."""

import os
from pathlib import Path

import pytest

from overturemaps.cache import (
    cache_dir,
    index_path,
    cache_info,
    clear_cache,
)


def test_cache_dir_respects_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert cache_dir() == tmp_path / "overturemaps"


def test_cache_dir_fallback_when_xdg_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    expected = tmp_path / ".cache" / "overturemaps"
    assert cache_dir() == expected


def test_index_path_includes_release(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    p = index_path("2025-12-17.0")
    assert p.name == "divisions-index-2025-12-17.0.parquet"
    assert p.parent == tmp_path / "overturemaps"


def test_cache_info_when_no_index(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    info = cache_info(latest_release="2025-12-17.0")
    assert info["index_path"] == str(index_path("2025-12-17.0"))
    assert info["index_release"] is None
    assert info["latest_release"] == "2025-12-17.0"
    assert info["up_to_date"] is False
    assert info["size_bytes"] == 0


def test_cache_info_with_stale_index(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    # Create a fake stale index file
    stale = index_path("2024-01-01.0")
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_bytes(b"hello world")

    info = cache_info(latest_release="2025-12-17.0")
    assert info["index_release"] == "2024-01-01.0"
    assert info["up_to_date"] is False
    assert info["size_bytes"] == len(b"hello world")


def test_cache_info_with_current_index(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    current = index_path("2025-12-17.0")
    current.parent.mkdir(parents=True, exist_ok=True)
    current.write_bytes(b"x" * 100)

    info = cache_info(latest_release="2025-12-17.0")
    assert info["up_to_date"] is True
    assert info["index_release"] == "2025-12-17.0"
    assert info["size_bytes"] == 100


def test_clear_removes_all_index_files(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    a = index_path("2024-01-01.0")
    b = index_path("2025-12-17.0")
    a.parent.mkdir(parents=True, exist_ok=True)
    a.write_bytes(b"a")
    b.write_bytes(b"b")

    removed = clear_cache()
    assert removed == 2
    assert not a.exists()
    assert not b.exists()


def test_clear_when_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert clear_cache() == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cache.py -v`
Expected: `ModuleNotFoundError: No module named 'overturemaps.cache'`.

- [ ] **Step 3: Implement the cache module**

Create `overturemaps/cache.py`:

```python
"""On-disk cache for the divisions geocoding index."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional


_INDEX_FILE_RE = re.compile(r"^divisions-index-(.+)\.parquet$")


def cache_dir() -> Path:
    """Return the overturemaps cache directory, respecting XDG_CACHE_HOME."""
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "overturemaps"
    return Path(os.environ.get("HOME", "~")).expanduser() / ".cache" / "overturemaps"


def index_path(release: str) -> Path:
    """Path to the divisions-index file for a given release."""
    return cache_dir() / f"divisions-index-{release}.parquet"


def _scan_existing_indexes() -> list[tuple[str, Path]]:
    """Return (release, path) for every divisions-index file present on disk."""
    d = cache_dir()
    if not d.exists():
        return []
    out: list[tuple[str, Path]] = []
    for entry in d.iterdir():
        if not entry.is_file():
            continue
        m = _INDEX_FILE_RE.match(entry.name)
        if m:
            out.append((m.group(1), entry))
    return out


def cache_info(latest_release: Optional[str] = None) -> dict:
    """Return a JSON-serializable summary of the current cache state."""
    indexes = _scan_existing_indexes()
    if indexes:
        # Pick the newest (alphabetical sort is fine: release IDs are date-prefixed)
        indexes.sort(key=lambda pair: pair[0], reverse=True)
        current_release, current_path = indexes[0]
        size = current_path.stat().st_size
    else:
        current_release = None
        size = 0

    up_to_date = (
        current_release is not None
        and latest_release is not None
        and current_release == latest_release
    )

    # `index_path` reports where the *current* (latest) index *would* live.
    target_release = latest_release or current_release or ""
    return {
        "index_path": str(index_path(target_release)) if target_release else str(cache_dir()),
        "index_release": current_release,
        "latest_release": latest_release,
        "up_to_date": up_to_date,
        "size_bytes": size,
    }


def clear_cache() -> int:
    """Remove every divisions-index file. Returns the number of files removed."""
    indexes = _scan_existing_indexes()
    for _release, path in indexes:
        path.unlink()
    return len(indexes)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cache.py -v`
Expected: all 8 tests pass.

- [ ] **Step 5: Commit**

```bash
git add overturemaps/cache.py tests/test_cache.py
git commit -m "Add cache module for divisions-index storage

XDG-respecting path resolution, cache_info(), clear_cache(). Index build
arrives in the next task."
```

---

## Task 1.5: Build the divisions index from S3

`build_index` reads `division` + `division_area` from the latest release on S3 and writes a compact parquet file under the cache directory. `ensure_index` checks freshness and triggers a rebuild only when needed.

This task uses real network in an integration test marked appropriately.

**Files:**
- Modify: `overturemaps/cache.py`
- Modify: `tests/test_cache.py`
- Create: `tests/test_cache_integration.py`

- [ ] **Step 1: Write the failing unit test (mocked S3)**

Append to `tests/test_cache.py`:

```python
import pyarrow as pa


class _FakeArrowFn:
    """Hold the return value the fake will produce."""

    def __init__(self, return_value):
        self.return_value = return_value
        self.calls: list = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.return_value


def _fake_division_table():
    return pa.table({
        "id": ["d1", "d2", "d3"],
        "names": pa.array(
            [
                {"primary": "Boston", "common": None},
                {"primary": "Cambridge", "common": None},
                {"primary": "Boston", "common": None},
            ],
            type=pa.struct([("primary", pa.string()), ("common", pa.string())]),
        ),
        "subtype": ["locality", "locality", "locality"],
        "class": [None, None, None],
        "country": ["US", "US", "GB"],
        "region": ["US-MA", "US-MA", "GB-LIN"],
        "admin_level": [8, 8, 8],
        "population": [654776, 118000, 41000],
        "parent_division_id": ["mass", "mass", "lincolnshire"],
    })


def _fake_division_area_table():
    bbox_type = pa.struct(
        [("xmin", pa.float64()), ("ymin", pa.float64()),
         ("xmax", pa.float64()), ("ymax", pa.float64())]
    )
    return pa.table({
        "division_id": ["d1", "d2", "d3"],
        "bbox": pa.array(
            [
                {"xmin": -71.19, "ymin": 42.23, "xmax": -70.99, "ymax": 42.40},
                {"xmin": -71.16, "ymin": 42.36, "xmax": -71.07, "ymax": 42.42},
                {"xmin":   0.00, "ymin": 53.00, "xmax":   0.20, "ymax": 53.10},
            ],
            type=bbox_type,
        ),
    })


def test_build_index_writes_joined_parquet(monkeypatch, tmp_path):
    from overturemaps import cache as cache_mod

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

    # Stub out the S3 reads
    div_table = _fake_division_table()
    area_table = _fake_division_area_table()

    def fake_read_partition(theme, type_, release, columns):
        if type_ == "division":
            return div_table.select(columns)
        if type_ == "division_area":
            return area_table.select(columns)
        raise AssertionError(f"unexpected type {type_}")

    monkeypatch.setattr(cache_mod, "_read_partition_columns", fake_read_partition)

    out_path = cache_mod.build_index("2025-12-17.0")
    assert out_path.exists()
    assert out_path == cache_mod.index_path("2025-12-17.0")

    import pyarrow.parquet as pq
    table = pq.read_table(out_path)
    # 3 input rows, all joined successfully
    assert table.num_rows == 3
    assert set(table.column_names) >= {
        "id", "name_primary", "name_common", "subtype", "country",
        "region", "admin_level", "population", "parent_division_id",
        "bbox_xmin", "bbox_ymin", "bbox_xmax", "bbox_ymax",
    }


def test_ensure_index_skips_when_current(monkeypatch, tmp_path):
    from overturemaps import cache as cache_mod

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    target = cache_mod.index_path("2025-12-17.0")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"fake index")

    called = []
    monkeypatch.setattr(
        cache_mod, "build_index",
        lambda release: called.append(release) or target,
    )

    result = cache_mod.ensure_index("2025-12-17.0")
    assert result == target
    assert called == []  # no rebuild


def test_ensure_index_rebuilds_when_stale(monkeypatch, tmp_path):
    from overturemaps import cache as cache_mod

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    old = cache_mod.index_path("2024-01-01.0")
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_bytes(b"old index")

    called = []
    def fake_build(release):
        called.append(release)
        p = cache_mod.index_path(release)
        p.write_bytes(b"new index")
        return p

    monkeypatch.setattr(cache_mod, "build_index", fake_build)

    result = cache_mod.ensure_index("2025-12-17.0")
    assert result == cache_mod.index_path("2025-12-17.0")
    assert called == ["2025-12-17.0"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cache.py -v`
Expected: `AttributeError: module 'overturemaps.cache' has no attribute 'build_index'`.

- [ ] **Step 3: Implement build_index and ensure_index**

Append to `overturemaps/cache.py`:

```python
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.fs as _fs
import pyarrow.parquet as pq


_S3_BUCKET = "overturemaps-us-west-2"


def _read_partition_columns(theme: str, type_: str, release: str, columns: list[str]) -> pa.Table:
    """Read selected columns from a divisions partition on S3."""
    path = f"{_S3_BUCKET}/release/{release}/theme={theme}/type={type_}/"
    fs = _fs.S3FileSystem(anonymous=True, region="us-west-2")
    dataset = ds.dataset(path, filesystem=fs)
    return dataset.to_table(columns=columns)


def build_index(release: str) -> Path:
    """Read divisions data from S3 for `release` and write the local index parquet."""
    div_cols = [
        "id", "names", "subtype", "class", "country", "region",
        "admin_level", "population", "parent_division_id",
    ]
    area_cols = ["division_id", "bbox"]

    div = _read_partition_columns("divisions", "division", release, div_cols)
    area = _read_partition_columns("divisions", "division_area", release, area_cols)

    # Flatten names struct -> name_primary, name_common
    names_col = div.column("names").combine_chunks()
    name_primary = pc.struct_field(names_col, "primary")
    name_common = pc.struct_field(names_col, "common")

    # Each division can have multiple division_area rows; combine to a single
    # bbox by taking min(xmin), min(ymin), max(xmax), max(ymax) per division_id.
    bbox_struct = area.column("bbox").combine_chunks()
    area_flat = pa.table({
        "division_id": area.column("division_id"),
        "xmin": pc.struct_field(bbox_struct, "xmin"),
        "ymin": pc.struct_field(bbox_struct, "ymin"),
        "xmax": pc.struct_field(bbox_struct, "xmax"),
        "ymax": pc.struct_field(bbox_struct, "ymax"),
    })
    bbox_agg = area_flat.group_by("division_id").aggregate([
        ("xmin", "min"),
        ("ymin", "min"),
        ("xmax", "max"),
        ("ymax", "max"),
    ]).rename_columns([
        "division_id", "bbox_xmin", "bbox_ymin", "bbox_xmax", "bbox_ymax",
    ])

    # Build the flat division table (without the names struct).
    div_flat = pa.table({
        "id": div.column("id"),
        "name_primary": name_primary,
        "name_common": name_common,
        "subtype": div.column("subtype"),
        "class": div.column("class"),
        "country": div.column("country"),
        "region": div.column("region"),
        "admin_level": div.column("admin_level"),
        "population": div.column("population"),
        "parent_division_id": div.column("parent_division_id"),
    })

    # Inner join on id <-> division_id
    joined = div_flat.join(bbox_agg, keys="id", right_keys="division_id", join_type="inner")

    out = index_path(release)
    out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(joined, out)
    return out


def ensure_index(latest_release: str) -> Path:
    """Return the path to a current index, building it if missing or stale."""
    target = index_path(latest_release)
    if target.exists():
        return target
    return build_index(latest_release)
```

Note: `pa.Table.join` is the PyArrow >= 14 API and is supported by the project's pinned `pyarrow>=15.0.2`. If the implementer hits a version surprise, the fallback is to materialize via pandas, but that should not be needed.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cache.py -v`
Expected: all tests pass.

- [ ] **Step 5: Write the integration test**

Create `tests/test_cache_integration.py`:

```python
"""Integration tests for cache: hit real S3, build a real index."""

import pytest

from overturemaps.cache import build_index, index_path
from overturemaps.core import get_latest_release

pytestmark = pytest.mark.integration


def test_build_real_index(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    release = get_latest_release()
    p = build_index(release)
    assert p == index_path(release)
    assert p.stat().st_size > 0

    import pyarrow.parquet as pq
    t = pq.read_table(p)
    # Should contain at least one country-level entry
    assert t.num_rows > 100
    assert "bbox_xmin" in t.column_names
```

Run: `uv run pytest tests/test_cache_integration.py -m integration -v`
Expected: PASS (slow — may take 30–90 seconds; one-time S3 read).

- [ ] **Step 6: Commit**

```bash
git add overturemaps/cache.py tests/test_cache.py tests/test_cache_integration.py
git commit -m "Build the divisions index from S3 on first use

build_index() joins division + division_area, flattens the names struct,
aggregates polygon bboxes per division, and writes a compact parquet to
the cache dir. ensure_index() skips when the cache is already current."
```

---

## Task 1.6: Geocoding lookup

`resolve(name)` returns ranked matches against the cached index; `best_match` is the convenience wrapper that picks one.

**Files:**
- Create: `overturemaps/geocoding.py`
- Create: `tests/test_geocoding.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_geocoding.py`:

```python
"""Tests for the geocoding lookup."""

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from overturemaps.geocoding import resolve, best_match, Division


def _build_index_file(tmp_path: Path) -> Path:
    """Write a tiny fake divisions index parquet at the expected location."""
    table = pa.table({
        "id": ["us", "ma", "boston-ma", "boston-uk", "cambridge-ma", "cambridge-uk"],
        "name_primary": [
            "United States", "Massachusetts", "Boston", "Boston",
            "Cambridge", "Cambridge",
        ],
        "name_common": [None, None, None, None, None, None],
        "subtype": ["country", "region", "locality", "locality", "locality", "locality"],
        "class": [None, None, None, None, None, None],
        "country": ["US", "US", "US", "GB", "US", "GB"],
        "region": [None, "US-MA", "US-MA", "GB-LIN", "US-MA", "GB-CAM"],
        "admin_level": [2, 4, 8, 8, 8, 8],
        "population": [330000000, 7000000, 654776, 41000, 118000, 145000],
        "parent_division_id": [None, "us", "ma", "lincolnshire", "ma", "cambridgeshire"],
        "bbox_xmin": [-180.0, -73.5, -71.19, 0.00, -71.16, 0.10],
        "bbox_ymin": [18.0, 41.2, 42.23, 53.00, 42.36, 52.18],
        "bbox_xmax": [-66.0, -69.9, -70.99, 0.20, -71.07, 0.20],
        "bbox_ymax": [71.0, 42.9, 42.40, 53.10, 42.42, 52.22],
    })
    out = tmp_path / "overturemaps" / "divisions-index-2025-12-17.0.parquet"
    out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out)
    return out


@pytest.fixture
def fake_index(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    _build_index_file(tmp_path)
    # Skip the network lookup that ensure_index would do
    monkeypatch.setattr(
        "overturemaps.geocoding._latest_release", lambda: "2025-12-17.0",
    )
    return tmp_path


def test_resolve_exact_match(fake_index):
    results = resolve("Massachusetts")
    assert len(results) == 1
    assert results[0].name == "Massachusetts"
    assert results[0].subtype == "region"


def test_resolve_case_insensitive(fake_index):
    results = resolve("MASSACHUSETTS")
    assert len(results) == 1


def test_resolve_returns_all_ambiguous_matches(fake_index):
    results = resolve("Boston")
    assert len(results) == 2
    assert all(r.name == "Boston" for r in results)


def test_best_match_prefers_higher_population_on_tie(fake_index):
    # Both Bostons have admin_level=8; pick by population
    pick = best_match("Boston")
    assert pick.region == "US-MA"
    assert pick.population == 654776


def test_best_match_country_disambiguator(fake_index):
    pick = best_match("Boston, GB")
    assert pick.region == "GB-LIN"


def test_best_match_region_disambiguator(fake_index):
    pick = best_match("Boston, US-MA")
    assert pick.region == "US-MA"


def test_best_match_admin_level_tiebreak_innermost_wins(fake_index):
    # "United States" has admin_level=2 (largest area). If we somehow had a
    # nested "United States" locality, admin_level should pick the innermost.
    # Here we just confirm a single match is returned for "United States".
    pick = best_match("United States")
    assert pick.subtype == "country"


def test_best_match_no_match_raises(fake_index):
    with pytest.raises(LookupError):
        best_match("Nonexistentville")


def test_division_bbox_property(fake_index):
    pick = best_match("Massachusetts")
    assert pick.bbox == (-73.5, 41.2, -69.9, 42.9)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_geocoding.py -v`
Expected: `ModuleNotFoundError: No module named 'overturemaps.geocoding'`.

- [ ] **Step 3: Implement geocoding**

Create `overturemaps/geocoding.py`:

```python
"""Resolve place names to Overture division features via the local index."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import pyarrow.compute as pc
import pyarrow.parquet as pq

from .cache import ensure_index


@dataclass(frozen=True)
class Division:
    id: str
    name: str
    subtype: str
    country: Optional[str]
    region: Optional[str]
    admin_level: int
    population: Optional[int]
    parent_division_id: Optional[str]
    bbox: tuple[float, float, float, float]  # xmin, ymin, xmax, ymax

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "subtype": self.subtype,
            "country": self.country,
            "region": self.region,
            "admin_level": self.admin_level,
            "population": self.population,
            "parent_division_id": self.parent_division_id,
            "bbox": list(self.bbox),
        }


def _latest_release() -> str:
    # Imported lazily so tests can monkeypatch without S3 calls.
    from .core import get_latest_release
    return get_latest_release()


def _parse_query(query: str) -> tuple[str, list[str]]:
    """Split 'Boston, US-MA' into ('Boston', ['US-MA'])."""
    parts = [p.strip() for p in query.split(",")]
    name = parts[0]
    qualifiers = [p for p in parts[1:] if p]
    return name, qualifiers


def _load_index_table():
    release = _latest_release()
    path = ensure_index(release)
    return pq.read_table(path)


def resolve(query: str) -> List[Division]:
    """Return all divisions matching the query (name + optional qualifiers).

    Returns a list ordered by best-match-first: lower admin_level (larger area)
    is preferred only after population. Population desc breaks remaining ties.
    Inner divisions (higher admin_level) are preferred when name matches exactly
    at multiple levels.
    """
    name, qualifiers = _parse_query(query)
    table = _load_index_table()

    # Case-insensitive equality on name_primary or name_common
    name_lower = name.lower()
    primary = pc.utf8_lower(table.column("name_primary"))
    common = pc.utf8_lower(table.column("name_common"))
    name_match = pc.or_(
        pc.equal(primary, name_lower),
        pc.equal(common, name_lower),
    )

    filtered = table.filter(name_match)

    # Apply qualifiers: each must match country code (2 chars) or region code
    for q in qualifiers:
        country_match = pc.equal(filtered.column("country"), q)
        region_match = pc.equal(filtered.column("region"), q)
        filtered = filtered.filter(pc.or_(country_match, region_match))

    if filtered.num_rows == 0:
        return []

    # Sort: admin_level desc (innermost first) then population desc
    sort_indices = pc.sort_indices(
        filtered,
        sort_keys=[("admin_level", "descending"), ("population", "descending")],
    )
    filtered = filtered.take(sort_indices)

    rows = filtered.to_pylist()
    return [
        Division(
            id=r["id"],
            name=r["name_primary"],
            subtype=r["subtype"],
            country=r["country"],
            region=r["region"],
            admin_level=r["admin_level"],
            population=r["population"],
            parent_division_id=r["parent_division_id"],
            bbox=(r["bbox_xmin"], r["bbox_ymin"], r["bbox_xmax"], r["bbox_ymax"]),
        )
        for r in rows
    ]


def best_match(query: str) -> Division:
    """Return the single best match for the query. Raises LookupError on no match."""
    results = resolve(query)
    if not results:
        raise LookupError(f"No division found for {query!r}")
    return results[0]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_geocoding.py -v`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add overturemaps/geocoding.py tests/test_geocoding.py
git commit -m "Geocoding lookup: name -> Division via the cached index

resolve() returns ranked matches sorted by admin_level desc then
population desc. best_match() is the single-result convenience wrapper.
Comma-suffix tokens narrow by country or region code."
```

---

## Task 1.7: Wire `--in` and `--where` into `download`

Extend the existing `download` command. No new commands yet — this is the smallest change that makes the new machinery user-visible.

**Files:**
- Modify: `overturemaps/cli.py`
- Modify: `tests/test_cli_download.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_cli_download.py`:

```python
def test_download_with_in_flag_resolves_to_bbox(monkeypatch):
    """`--in` resolves a place to a bbox and feeds it through the pipeline."""
    from overturemaps.geocoding import Division

    captured = {}

    def fake_best_match(query):
        captured["query"] = query
        return Division(
            id="boston-ma", name="Boston", subtype="locality",
            country="US", region="US-MA",
            admin_level=8, population=654776, parent_division_id="ma",
            bbox=(-71.19, 42.23, -70.99, 42.40),
        )

    monkeypatch.setattr("overturemaps.cli.best_match", fake_best_match)
    monkeypatch.setattr("overturemaps.cli.get_latest_release", lambda: "2025-12-17.0")

    def fake_reader(type_, bbox, *args, **kwargs):
        captured["bbox"] = bbox
        captured["type"] = type_
        return _DummyReader()

    monkeypatch.setattr("overturemaps.cli.record_batch_reader", fake_reader)
    monkeypatch.setattr("overturemaps.cli.get_writer", lambda *a, **k: _DummyWriter())
    monkeypatch.setattr("overturemaps.cli.copy", lambda *a, **k: None)
    monkeypatch.setattr("overturemaps.cli.save_state", lambda *a, **k: None)

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(
            cli,
            ["download", "-t", "building", "-f", "geojson",
             "-o", "out.geojson", "--in", "Boston, MA"],
        )
        assert result.exit_code == 0, result.output
        assert captured["query"] == "Boston, MA"
        assert captured["bbox"] == [-71.19, 42.23, -70.99, 42.40]


def test_download_rejects_in_and_bbox_together():
    """`--in` and `--bbox` are mutually exclusive."""
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["download", "-t", "building", "-f", "geojson",
         "-o", "out.geojson", "--in", "Boston", "--bbox", "-71,42,-70,43"],
    )
    assert result.exit_code != 0
    assert "mutually exclusive" in result.output.lower() or "cannot be used together" in result.output.lower()


def test_download_with_where_passes_filter_to_reader(monkeypatch):
    """`--where` is parsed and passed alongside the bbox filter."""
    captured = {}

    monkeypatch.setattr("overturemaps.cli.get_latest_release", lambda: "2025-12-17.0")

    def fake_reader(type_, bbox, release, ct, rt, stac, where_filters=None):
        captured["where_filters"] = where_filters
        return _DummyReader()

    # We're going to overwrite record_batch_reader to accept the new kwarg.
    monkeypatch.setattr("overturemaps.cli.record_batch_reader", fake_reader)
    monkeypatch.setattr("overturemaps.cli.get_writer", lambda *a, **k: _DummyWriter())
    monkeypatch.setattr("overturemaps.cli.copy", lambda *a, **k: None)
    monkeypatch.setattr("overturemaps.cli.save_state", lambda *a, **k: None)

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(
            cli,
            ["download", "-t", "building", "-f", "geojson",
             "-o", "out.geojson", "--bbox", "-71.1,42.3,-71.0,42.4",
             "--where", "height>50"],
        )
        assert result.exit_code == 0, result.output
        assert captured["where_filters"] is not None
        assert len(captured["where_filters"]) == 1
        assert captured["where_filters"][0].key == "height"
        assert captured["where_filters"][0].op == ">"
        assert captured["where_filters"][0].value == 50
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli_download.py -v -k "in_flag or rejects_in_and_bbox or with_where"`
Expected: all three fail (flag not recognized).

- [ ] **Step 3: Extend `core.record_batch_reader` to accept `where_filters`**

In `overturemaps/core.py`, update `_prepare_query` and `record_batch_reader` to accept and apply a list of `ParsedFilter`. Find the function `_prepare_query` (around line 228 of the existing file) and change its signature + body:

```python
def _prepare_query(
    overture_type,
    bbox=None,
    release=None,
    connect_timeout=None,
    request_timeout=None,
    stac=False,
    where_filters=None,
):
    """Resolve the S3 dataset and filter expression for a given query."""
    if release is None:
        release = get_latest_release()
    path = _dataset_path(overture_type, release)
    bbox_obj = _coerce_bbox(bbox)

    intersecting_files = None
    if bbox_obj and stac:
        intersecting_files = _get_files_from_stac(
            type_theme_map[overture_type], overture_type, bbox_obj, release
        )
        if intersecting_files is not None and len(intersecting_files) == 0:
            return None

    filter_expr = None
    if bbox_obj:
        xmin, ymin, xmax, ymax = bbox_obj.as_tuple()
        filter_expr = (
            (pc.field("bbox", "xmin") < xmax)
            & (pc.field("bbox", "xmax") > xmin)
            & (pc.field("bbox", "ymin") < ymax)
            & (pc.field("bbox", "ymax") > ymin)
        )

    dataset = ds.dataset(
        intersecting_files if intersecting_files is not None else path,
        filesystem=fs.S3FileSystem(
            anonymous=True,
            region="us-west-2",
            connect_timeout=connect_timeout,
            request_timeout=request_timeout,
        ),
    )

    if where_filters:
        from .filters import combine
        attr_expr = combine(list(where_filters), dataset.schema)
        if attr_expr is not None:
            filter_expr = attr_expr if filter_expr is None else filter_expr & attr_expr

    return dataset, filter_expr
```

Update `record_batch_reader` and `count_rows` to forward the new kwarg:

```python
def count_rows(
    overture_type, bbox=None, release=None,
    connect_timeout=None, request_timeout=None, stac=False, where_filters=None,
) -> int:
    result = _prepare_query(
        overture_type, bbox, release, connect_timeout, request_timeout, stac,
        where_filters=where_filters,
    )
    if result is None:
        return 0
    dataset, filter_expr = result
    return dataset.count_rows(filter=filter_expr)


def record_batch_reader(
    overture_type, bbox=None, release=None,
    connect_timeout=None, request_timeout=None, stac=False, where_filters=None,
):
    result = _prepare_query(
        overture_type, bbox, release, connect_timeout, request_timeout, stac,
        where_filters=where_filters,
    )
    if result is None:
        return None
    dataset, filter_expr = result
    return _record_batch_reader_from_dataset(dataset, filter_expr=filter_expr)
```

- [ ] **Step 4: Extend the `download` Click command**

In `overturemaps/cli.py`, add this import near the top:

```python
from .filters import parse_where_expr
from .geocoding import best_match
```

Then modify the `download` decorator block to add the two new options. Locate the existing `def download(...)` (around line 179) and update its decorators and signature:

```python
@cli.command()
@click.option("--bbox", required=False, type=BboxParamType())
@click.option("--in", "in_place", required=False, type=str,
              help="Resolve a place name to a bbox via the divisions index.")
@click.option("--where", "where_exprs", multiple=True,
              help="Attribute filter K OP V (repeatable). Example: --where height>50")
@click.option("-f", "output_format",
              type=click.Choice(["geojson", "geojsonseq", "geoparquet"]), required=True)
@click.option("-o", "--output", required=False, type=click.Path())
@click.option("-t", "--type", "type_",
              type=click.Choice(get_all_overture_types()), required=True)
@click.option("-r", "--release", default=None, callback=validate_release, required=False,
              help="Release version (defaults to latest)")
@click.option("--stac/--no-stac", required=False, type=bool, is_flag=True, default=True,
              help="...existing help text...")
@click.option("--connect_timeout", required=False, type=int)
@click.option("--request_timeout", required=False, type=int)
def download(bbox, in_place, where_exprs, output_format, output, type_, release,
             connect_timeout, request_timeout, stac):
    # Mutual exclusion check
    if bbox is not None and in_place is not None:
        raise click.UsageError("--bbox and --in are mutually exclusive")

    # Resolve --in to bbox
    if in_place is not None:
        try:
            division = best_match(in_place)
        except LookupError as e:
            raise click.UsageError(str(e))
        bbox = list(division.bbox)
        click.secho(
            f"Resolved {in_place!r} -> {division.name} "
            f"({division.subtype}, {division.region or division.country}, "
            f"pop {division.population})",
            fg="bright_black", err=True,
        )

    # Parse --where expressions
    where_filters = [parse_where_expr(e) for e in where_exprs] if where_exprs else None

    # ... existing body, but pass where_filters into record_batch_reader ...
    if bbox is None:
        click.secho(
            "Warning: No bounding box provided. Downloading the entire dataset "
            "for this type. The full Overture dataset is approximately "
            "1.2 TB as GeoJSON and 400 GB as GeoParquet.",
            fg="yellow", bold=True, err=True,
        )
    else:
        area = _bbox_area_sq_deg(bbox[0], bbox[1], bbox[2], bbox[3])
        fraction = area / EARTH_AREA_SQ_DEG
        if fraction >= LARGE_BBOX_THRESHOLD:
            pct = fraction * 100
            click.secho(
                f"Warning: The bounding box covers ~{pct:.1f}% of Earth's surface. "
                f"This may take a long time and use significant bandwidth. "
                f"The full Overture dataset is approximately "
                f"1.2 TB as GeoJSON and 400 GB as GeoParquet.",
                fg="yellow", bold=True, err=True,
            )

    if output_format == "geoparquet" and output is None:
        raise click.UsageError(
            "Output file (-o/--output) is required when using geoparquet format"
        )

    output_file = sys.stdout if output is None else output

    reader = record_batch_reader(
        type_, bbox, release, connect_timeout, request_timeout, stac,
        where_filters=where_filters,
    )

    if reader is None:
        return

    with get_writer(output_format, output_file, schema=reader.schema) as writer:
        copy(reader, writer)

    # ... rest of existing state-saving block unchanged ...
    if output is not None:
        output_path = os.path.abspath(os.path.expanduser(output))
        backend = Backend(output_format)
        theme = type_theme_map.get(type_)
        if theme is None:
            click.secho(f"Warning: Could not determine theme for type {type_}",
                        fg="yellow", bold=True, err=True)
            return
        state = PipelineState(
            last_release=release, last_run=datetime.now(timezone.utc).isoformat(),
            theme=theme, type=type_,
            bbox=BBox(xmin=bbox[0], ymin=bbox[1], xmax=bbox[2], ymax=bbox[3])
                 if bbox is not None else None,
            backend=backend, output=output_path,
        )
        state_path = get_state_path(output)
        save_state(state, state_path)
        click.secho(f"State saved to {state_path}", fg="bright_black", err=True)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/ -m "not integration" -v`
Expected: all unit tests pass, including the three new download tests.

- [ ] **Step 6: Smoke-test against real S3 (optional but recommended)**

Run: `uv run overturemaps download -t building --in "Boston, MA" --where height>50 -f geojsonseq -o /tmp/tall.jsonl`
Expected: progress bar prints to stderr; `/tmp/tall.jsonl` contains one feature per line; each has `properties.height > 50`.

- [ ] **Step 7: Commit**

```bash
git add overturemaps/cli.py overturemaps/core.py tests/test_cli_download.py
git commit -m "Wire --in and --where into the download command

--in resolves a place name via the divisions index and feeds the bbox
into the existing pipeline. --where (repeatable) parses K OP V filters
which are validated against the type schema and pushed down to PyArrow.
Mutual exclusion enforced between --in and --bbox."
```

---

## Task 1.8: End-of-phase sanity check

- [ ] **Step 1: Run the full unit test suite**

Run: `uv run pytest tests/ -m "not integration" -v`
Expected: all tests pass with no regressions in the existing download/gers/releases/changelog suites.

- [ ] **Step 2: Run an integration smoke**

Run: `uv run pytest tests/test_cache_integration.py -m integration -v`
Expected: PASS.

- [ ] **Step 3: Verify the `--help` output still renders cleanly**

Run: `uv run overturemaps download --help`
Expected: shows `--in` and `--where` alongside the existing options.

- [ ] **Step 4: Commit anything pending**

```bash
git status
# If only the commits from previous tasks are present, no action needed.
```

🚦 **Phase 1 ship gate:** the download command now accepts place names and attribute filters. This is a valid release boundary if you want to ship before continuing.

---

# Phase 2 — Metadata Commands + JSON

End state: agents can discover every queryable shape (`themes`, `types`, `schema`, `categories`, `capabilities`), preview every query (`count`, `sample`), resolve any place name (`where`), and manage the cache (`cache info|clear|build`). All metadata commands emit JSON when the global `--json` flag is set.

---

## Task 2.1: Global `--json` flag

A top-level flag on the `cli` group, accessible via Click context.

**Files:**
- Modify: `overturemaps/cli.py`
- Create: `tests/test_cli_json.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_json.py`:

```python
"""Tests for the global --json flag."""

from click.testing import CliRunner

from overturemaps.cli import cli


def test_json_flag_sets_context_object():
    """--json sets ctx.obj['json'] = True for child commands."""
    runner = CliRunner()
    # Invoke a command that doesn't actually exist yet but parses the flag.
    # We'll just check that --help still works with --json present.
    result = runner.invoke(cli, ["--json", "--help"])
    assert result.exit_code == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_json.py -v`
Expected: FAIL — flag not yet defined; click prints "No such option: --json".

- [ ] **Step 3: Add the flag**

In `overturemaps/cli.py`, modify the existing `@click.group(...)` block to add the flag and a `pass_context` decorator. Find:

```python
@click.group(invoke_without_command=True)
@click.version_option(
    version=importlib.metadata.version("overturemaps"),
    prog_name="overturemaps",
)
@click.pass_context
def cli(ctx):
    if ctx.invoked_subcommand is None:
        _print_banner()
        click.echo(ctx.get_help())
```

Replace with:

```python
@click.group(invoke_without_command=True)
@click.version_option(
    version=importlib.metadata.version("overturemaps"),
    prog_name="overturemaps",
)
@click.option("--json", "json_output", is_flag=True, default=False,
              help="Emit machine-readable JSON for metadata commands.")
@click.pass_context
def cli(ctx, json_output):
    ctx.ensure_object(dict)
    ctx.obj["json"] = json_output
    if ctx.invoked_subcommand is None:
        _print_banner()
        click.echo(ctx.get_help())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli_json.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add overturemaps/cli.py tests/test_cli_json.py
git commit -m "Add global --json flag

Top-level boolean stored on Click context. Metadata commands added in
subsequent tasks consult ctx.obj['json'] to switch output mode."
```

---

## Task 2.2: `where` command

**Files:**
- Modify: `overturemaps/cli.py`
- Create: `tests/test_cli_where.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_where.py`:

```python
"""Tests for the `where` command."""

import json

import pytest
from click.testing import CliRunner

from overturemaps.cli import cli
from overturemaps.geocoding import Division


@pytest.fixture
def fake_match(monkeypatch):
    captured = {}

    def fake(query):
        captured["query"] = query
        return Division(
            id="boston-ma", name="Boston", subtype="locality",
            country="US", region="US-MA",
            admin_level=8, population=654776, parent_division_id="ma",
            bbox=(-71.19, 42.23, -70.99, 42.40),
        )

    def fake_resolve(query):
        return [fake(query)]

    monkeypatch.setattr("overturemaps.cli.best_match", fake)
    monkeypatch.setattr("overturemaps.cli.resolve", fake_resolve)
    return captured


def test_where_human_output(fake_match):
    runner = CliRunner()
    result = runner.invoke(cli, ["where", "Boston, MA"])
    assert result.exit_code == 0
    assert "Boston" in result.output
    assert "US-MA" in result.output
    assert "654776" in result.output or "654,776" in result.output


def test_where_json_output(fake_match):
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "where", "Boston, MA"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["name"] == "Boston"
    assert data["region"] == "US-MA"
    assert data["bbox"] == [-71.19, 42.23, -70.99, 42.40]
    assert "candidates" in data


def test_where_no_match(monkeypatch):
    monkeypatch.setattr(
        "overturemaps.cli.best_match",
        lambda q: (_ for _ in ()).throw(LookupError(f"No match for {q!r}")),
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["where", "Nonexistentville"])
    assert result.exit_code != 0


def test_where_no_match_json(monkeypatch):
    monkeypatch.setattr(
        "overturemaps.cli.best_match",
        lambda q: (_ for _ in ()).throw(LookupError(f"No match for {q!r}")),
    )
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "where", "Nonexistentville"])
    assert result.exit_code != 0
    # JSON error is printed to stderr by Click's invoke (mixed in result.output by default)
    # Confirm an error structure was emitted somewhere.
    combined = result.output + (result.stderr if hasattr(result, "stderr") else "")
    # When mix_stderr=True (default), stderr is part of output.
    assert "no_match" in combined or "No match" in combined
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_where.py -v`
Expected: FAIL — `where` command doesn't exist.

- [ ] **Step 3: Add a JSON helper, then the command**

Near the top of `overturemaps/cli.py`, add an `orjson`-based emit helper after the existing imports:

```python
import orjson

from .geocoding import best_match, resolve


def _emit_json(ctx, payload, file=None):
    """Print one JSON document to stdout."""
    out = orjson.dumps(payload, option=orjson.OPT_INDENT_2).decode()
    click.echo(out, file=file)


def _emit_error_json(message, code="error"):
    """Print a JSON error envelope to stderr."""
    err = {"error": {"code": code, "message": message}}
    click.echo(orjson.dumps(err).decode(), err=True)
```

Then add the `where` command (after the existing `download` definition):

```python
@cli.command()
@click.argument("query", type=str)
@click.pass_context
def where(ctx, query):
    """Resolve a place name to an Overture division feature."""
    json_mode = ctx.obj.get("json", False)
    try:
        pick = best_match(query)
    except LookupError as e:
        if json_mode:
            _emit_error_json(str(e), code="no_match")
        else:
            click.secho(str(e), fg="red", err=True)
        ctx.exit(1)

    if json_mode:
        all_matches = resolve(query)
        payload = pick.as_dict()
        payload["candidates"] = [d.as_dict() for d in all_matches]
        _emit_json(ctx, payload)
        return

    # Human output
    region_or_country = pick.region or pick.country or "?"
    click.secho(f"{pick.name}, {region_or_country}", bold=True)
    click.echo(f"  subtype: {pick.subtype}")
    click.echo(f"  bbox: {pick.bbox[0]:.4f}, {pick.bbox[1]:.4f}, "
               f"{pick.bbox[2]:.4f}, {pick.bbox[3]:.4f}")
    if pick.population is not None:
        click.echo(f"  population: {pick.population:,}")
    click.echo(f"  id: {pick.id}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli_where.py -v`
Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add overturemaps/cli.py tests/test_cli_where.py
git commit -m "Add `where` command for place-name geocoding

Returns the best-match division as human text or JSON. JSON mode
includes a `candidates` array of all matches sorted by quality."
```

---

## Task 2.3: `count` command

**Files:**
- Modify: `overturemaps/cli.py`
- Create: `tests/test_cli_count.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_count.py`:

```python
"""Tests for the `count` command."""

import json

import pytest
from click.testing import CliRunner

from overturemaps.cli import cli
from overturemaps.geocoding import Division


def test_count_with_bbox(monkeypatch):
    monkeypatch.setattr("overturemaps.cli.get_latest_release",
                        lambda: "2025-12-17.0")
    monkeypatch.setattr("overturemaps.cli.count_rows",
                        lambda *a, **k: 12345)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "count", "-t", "building", "--bbox", "-71.1,42.3,-71.0,42.4",
    ])
    assert result.exit_code == 0
    assert "12345" in result.output or "12,345" in result.output


def test_count_json(monkeypatch):
    monkeypatch.setattr("overturemaps.cli.get_latest_release",
                        lambda: "2025-12-17.0")
    monkeypatch.setattr("overturemaps.cli.count_rows",
                        lambda *a, **k: 12345)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "--json", "count", "-t", "building",
        "--bbox", "-71.1,42.3,-71.0,42.4",
    ])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["count"] == 12345
    assert payload["type"] == "building"
    assert payload["bbox"] == [-71.1, 42.3, -71.0, 42.4]


def test_count_with_in_and_where(monkeypatch):
    captured = {}
    monkeypatch.setattr("overturemaps.cli.get_latest_release",
                        lambda: "2025-12-17.0")
    monkeypatch.setattr(
        "overturemaps.cli.best_match",
        lambda q: Division(
            id="x", name="Boston", subtype="locality",
            country="US", region="US-MA",
            admin_level=8, population=654776, parent_division_id=None,
            bbox=(-71.2, 42.2, -71.0, 42.4),
        ),
    )

    def fake_count(type_, bbox=None, release=None, **kwargs):
        captured["bbox"] = bbox
        captured["where_filters"] = kwargs.get("where_filters")
        return 42

    monkeypatch.setattr("overturemaps.cli.count_rows", fake_count)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "count", "-t", "building", "--in", "Boston, MA",
        "--where", "height>100",
    ])
    assert result.exit_code == 0, result.output
    assert captured["bbox"] == [-71.2, 42.2, -71.0, 42.4]
    assert len(captured["where_filters"]) == 1
    assert captured["where_filters"][0].key == "height"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_count.py -v`
Expected: FAIL — `count` command doesn't exist.

- [ ] **Step 3: Add `count` to cli.py**

Add this import at the top of `overturemaps/cli.py` (alongside the existing core imports):

```python
from .core import count_rows
```

Then add the command (after `where`):

```python
@cli.command()
@click.option("-t", "--type", "type_",
              type=click.Choice(get_all_overture_types()), required=True)
@click.option("--bbox", required=False, type=BboxParamType())
@click.option("--in", "in_place", required=False, type=str)
@click.option("--where", "where_exprs", multiple=True)
@click.option("-r", "--release", default=None, callback=validate_release,
              required=False)
@click.pass_context
def count(ctx, type_, bbox, in_place, where_exprs, release):
    """Count features for a query without downloading them."""
    if bbox is not None and in_place is not None:
        raise click.UsageError("--bbox and --in are mutually exclusive")
    if in_place is not None:
        try:
            division = best_match(in_place)
        except LookupError as e:
            raise click.UsageError(str(e))
        bbox = list(division.bbox)

    where_filters = (
        [parse_where_expr(e) for e in where_exprs] if where_exprs else None
    )

    n = count_rows(
        type_, bbox=bbox, release=release, where_filters=where_filters,
    )

    if ctx.obj.get("json"):
        _emit_json(ctx, {
            "type": type_,
            "bbox": bbox,
            "where": [f"{f.key}{f.op}{f.value}" for f in (where_filters or [])],
            "release": release,
            "count": n,
        })
    else:
        click.echo(f"{n:,}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli_count.py -v`
Expected: all 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add overturemaps/cli.py tests/test_cli_count.py
git commit -m "Add `count` command for cheap query previews

count -t TYPE [--in|--bbox] [--where ...] returns the row count without
streaming data. Human output is the integer; JSON output includes the
query echo."
```

---

## Task 2.4: `sample` command

**Files:**
- Modify: `overturemaps/cli.py`
- Create: `tests/test_cli_sample.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_sample.py`:

```python
"""Tests for the `sample` command."""

import pytest
from click.testing import CliRunner

from overturemaps.cli import cli


class _NRowReader:
    """A reader that yields one batch of N synthetic rows then stops."""

    def __init__(self, n):
        import pyarrow as pa
        self.n = n
        self.schema = pa.schema([("id", pa.string()), ("name", pa.string())])
        self._done = False

    def read_next_batch(self):
        import pyarrow as pa
        if self._done:
            raise StopIteration
        self._done = True
        return pa.RecordBatch.from_pylist(
            [{"id": f"i{i}", "name": f"r{i}"} for i in range(self.n)],
            schema=self.schema,
        )


def test_sample_respects_n_limit(monkeypatch, tmp_path):
    monkeypatch.setattr("overturemaps.cli.get_latest_release",
                        lambda: "2025-12-17.0")

    def fake_reader(type_, *a, **k):
        return _NRowReader(50)

    monkeypatch.setattr("overturemaps.cli.record_batch_reader", fake_reader)

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, [
            "sample", "-t", "place",
            "--bbox", "-71.1,42.3,-71.0,42.4",
            "-n", "5", "-f", "geojsonseq",
            "-o", "out.jsonl",
        ])
        assert result.exit_code == 0, result.output
        lines = open("out.jsonl").read().strip().split("\n")
        assert len(lines) == 5  # truncated to N
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_sample.py -v`
Expected: FAIL — `sample` command doesn't exist.

- [ ] **Step 3: Add a helper that wraps the reader with a row limit, then the command**

In `overturemaps/cli.py`, add a small helper near the existing copy import:

```python
def _limit_reader(reader, n):
    """Yield a new RecordBatchReader emitting at most `n` rows."""
    import pyarrow as pa

    def _batches():
        remaining = n
        while remaining > 0:
            try:
                batch = reader.read_next_batch()
            except StopIteration:
                return
            if batch.num_rows == 0:
                continue
            if batch.num_rows > remaining:
                batch = batch.slice(0, remaining)
            remaining -= batch.num_rows
            yield batch

    return pa.RecordBatchReader.from_batches(reader.schema, _batches())
```

Add the command:

```python
@cli.command()
@click.option("-t", "--type", "type_",
              type=click.Choice(get_all_overture_types()), required=True)
@click.option("--bbox", required=False, type=BboxParamType())
@click.option("--in", "in_place", required=False, type=str)
@click.option("--where", "where_exprs", multiple=True)
@click.option("-n", default=10, show_default=True, type=int,
              help="Maximum number of features to emit.")
@click.option("-f", "output_format",
              type=click.Choice(["geojson", "geojsonseq", "geoparquet"]),
              default="geojsonseq", show_default=True)
@click.option("-o", "--output", required=False, type=click.Path())
@click.option("-r", "--release", default=None, callback=validate_release,
              required=False)
def sample(type_, bbox, in_place, where_exprs, n, output_format, output, release):
    """Emit the first N features matching the query."""
    if bbox is not None and in_place is not None:
        raise click.UsageError("--bbox and --in are mutually exclusive")
    if in_place is not None:
        try:
            division = best_match(in_place)
        except LookupError as e:
            raise click.UsageError(str(e))
        bbox = list(division.bbox)

    where_filters = (
        [parse_where_expr(e) for e in where_exprs] if where_exprs else None
    )

    reader = record_batch_reader(
        type_, bbox, release, None, None, True,
        where_filters=where_filters,
    )
    if reader is None:
        return

    if output_format == "geoparquet" and output is None:
        raise click.UsageError("Output file (-o/--output) is required for geoparquet")

    output_file = sys.stdout if output is None else output
    limited = _limit_reader(reader, n)

    with get_writer(output_format, output_file, schema=limited.schema) as writer:
        copy(limited, writer)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli_sample.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add overturemaps/cli.py tests/test_cli_sample.py
git commit -m "Add `sample` command for fixed-N previews

Streams at most N features through the existing writer pipeline.
Default format is geojsonseq for line-streamable output."
```

---

## Task 2.5: Introspection module (themes + types)

`themes` and `types` are static data on Overture's schema. Put the catalog in a new module so other commands can reuse it.

**Files:**
- Create: `overturemaps/introspection.py`
- Create: `tests/test_introspection.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_introspection.py`:

```python
"""Tests for the introspection module."""

import pytest

from overturemaps.introspection import (
    list_themes,
    list_types,
    THEME_DESCRIPTIONS,
    TYPE_DESCRIPTIONS,
)


def test_list_themes_returns_six():
    themes = list_themes()
    assert len(themes) == 6
    names = {t["name"] for t in themes}
    assert names == {
        "addresses", "base", "buildings",
        "divisions", "places", "transportation",
    }


def test_list_themes_has_descriptions():
    themes = list_themes()
    for t in themes:
        assert "description" in t
        assert "types" in t
        assert isinstance(t["types"], list)


def test_list_types_full():
    types = list_types()
    assert len(types) == 15
    names = {t["name"] for t in types}
    assert names == {
        "address", "bathymetry", "building", "building_part",
        "division", "division_area", "division_boundary",
        "place", "segment", "connector",
        "infrastructure", "land", "land_cover", "land_use", "water",
    }


def test_list_types_filtered_by_theme():
    types = list_types(theme="buildings")
    assert {t["name"] for t in types} == {"building", "building_part"}


def test_list_types_unknown_theme_raises():
    with pytest.raises(ValueError):
        list_types(theme="not-a-theme")


def test_every_type_has_a_description():
    types = list_types()
    for t in types:
        assert t["description"], f"Missing description for {t['name']}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_introspection.py -v`
Expected: `ModuleNotFoundError: No module named 'overturemaps.introspection'`.

- [ ] **Step 3: Implement the catalog**

Create `overturemaps/introspection.py`:

```python
"""Static catalog of Overture themes and types, plus runtime schema helpers."""

from __future__ import annotations

from typing import List, Optional

from .core import type_theme_map, get_all_overture_types


THEME_DESCRIPTIONS: dict[str, str] = {
    "addresses": "Address features with street, number, postcode, and country.",
    "base": "Base layers: land, water, land use/cover, infrastructure, bathymetry.",
    "buildings": "Building footprints with height, floors, class, and roof attributes.",
    "divisions": "Administrative divisions (countries, regions, counties, localities) and their polygons.",
    "places": "Categorized point features for businesses, services, and amenities (POIs).",
    "transportation": "Road network as segments with class, surface, speed limits, and connector junctions.",
}


TYPE_DESCRIPTIONS: dict[str, str] = {
    "address": "Address point with street, number, postcode, country code.",
    "bathymetry": "Underwater terrain features.",
    "building": "Building footprint with height, floor count, class, subtype.",
    "building_part": "Sub-component of a building when it has variable height/material.",
    "division": "Point representation of an administrative division (country, region, locality...).",
    "division_area": "Polygon area for a division.",
    "division_boundary": "Linear boundary between divisions.",
    "place": "Categorized POI with names, brand, categories, contact info.",
    "segment": "Road or rail segment with class, subclass, surface, speed limits.",
    "connector": "Junction or endpoint where segments meet.",
    "infrastructure": "Linear or point infrastructure features (bridges, tunnels, towers, etc.).",
    "land": "Natural land features (forest, beach, glacier, etc.).",
    "land_cover": "Land cover surface (forest, grassland, water, etc.).",
    "land_use": "Predominant human use of an area (commercial, residential, recreation, etc.).",
    "water": "Water bodies (river, lake, ocean).",
}


def list_themes() -> List[dict]:
    """List the six themes with descriptions and member types."""
    out = []
    for theme in sorted(THEME_DESCRIPTIONS.keys()):
        members = sorted(t for t, th in type_theme_map.items() if th == theme)
        out.append({
            "name": theme,
            "description": THEME_DESCRIPTIONS[theme],
            "types": members,
        })
    return out


def list_types(theme: Optional[str] = None) -> List[dict]:
    """List all types, optionally filtered to a single theme."""
    if theme is not None and theme not in THEME_DESCRIPTIONS:
        raise ValueError(
            f"Unknown theme {theme!r}. Available: "
            f"{', '.join(sorted(THEME_DESCRIPTIONS.keys()))}"
        )
    out = []
    for type_name in sorted(get_all_overture_types()):
        type_theme = type_theme_map[type_name]
        if theme is not None and type_theme != theme:
            continue
        out.append({
            "name": type_name,
            "theme": type_theme,
            "description": TYPE_DESCRIPTIONS.get(type_name, ""),
        })
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_introspection.py -v`
Expected: all 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add overturemaps/introspection.py tests/test_introspection.py
git commit -m "Add introspection catalog of themes and types

Static module so themes/types commands and the capabilities manifest can
share one source of truth for descriptions."
```

---

## Task 2.6: `themes` and `types` commands

**Files:**
- Modify: `overturemaps/cli.py`
- Create: `tests/test_cli_introspection.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_introspection.py`:

```python
"""Tests for the introspection CLI commands."""

import json

from click.testing import CliRunner

from overturemaps.cli import cli


def test_themes_human():
    runner = CliRunner()
    result = runner.invoke(cli, ["themes"])
    assert result.exit_code == 0
    assert "buildings" in result.output
    assert "transportation" in result.output


def test_themes_json():
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "themes"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 6
    assert all("name" in t and "description" in t and "types" in t for t in data)


def test_types_all_human():
    runner = CliRunner()
    result = runner.invoke(cli, ["types"])
    assert result.exit_code == 0
    assert "building" in result.output
    assert "segment" in result.output


def test_types_filtered_human():
    runner = CliRunner()
    result = runner.invoke(cli, ["types", "--theme", "buildings"])
    assert result.exit_code == 0
    assert "building" in result.output
    assert "building_part" in result.output
    assert "segment" not in result.output


def test_types_json():
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "types"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data) == 15
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_introspection.py -v`
Expected: FAIL — commands don't exist.

- [ ] **Step 3: Add the commands**

In `overturemaps/cli.py`, add the import:

```python
from .introspection import list_themes, list_types
```

Add the commands:

```python
@cli.command()
@click.pass_context
def themes(ctx):
    """List the six Overture themes."""
    data = list_themes()
    if ctx.obj.get("json"):
        _emit_json(ctx, data)
        return
    for t in data:
        click.secho(t["name"], bold=True, fg="cyan")
        click.echo(f"  {t['description']}")
        click.echo(f"  types: {', '.join(t['types'])}")
        click.echo()


@cli.command()
@click.option("--theme", required=False, type=str,
              help="Filter to types belonging to this theme.")
@click.pass_context
def types(ctx, theme):
    """List Overture feature types."""
    try:
        data = list_types(theme=theme)
    except ValueError as e:
        raise click.UsageError(str(e))
    if ctx.obj.get("json"):
        _emit_json(ctx, data)
        return
    for t in data:
        click.secho(t["name"], bold=True, fg="cyan")
        click.echo(f"  theme: {t['theme']}")
        click.echo(f"  {t['description']}")
        click.echo()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli_introspection.py -v`
Expected: all 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add overturemaps/cli.py tests/test_cli_introspection.py
git commit -m "Add `themes` and `types` introspection commands"
```

---

## Task 2.7: `schema` command

Emits the PyArrow schema of a type plus one sample feature.

**Files:**
- Modify: `overturemaps/introspection.py`
- Modify: `overturemaps/cli.py`
- Modify: `tests/test_introspection.py`
- Create: `tests/test_cli_schema.py`

- [ ] **Step 1: Write the failing test for the helper**

Append to `tests/test_introspection.py`:

```python
def test_flatten_schema_top_level():
    import pyarrow as pa
    from overturemaps.introspection import flatten_schema

    schema = pa.schema([
        ("id", pa.string()),
        ("height", pa.float64()),
    ])
    fields = flatten_schema(schema)
    assert {"name": "id", "type": "string"} in fields
    assert {"name": "height", "type": "double"} in fields


def test_flatten_schema_nested():
    import pyarrow as pa
    from overturemaps.introspection import flatten_schema

    schema = pa.schema([
        ("categories", pa.struct([
            ("primary", pa.string()),
            ("alternate", pa.list_(pa.string())),
        ])),
    ])
    fields = flatten_schema(schema)
    names = {f["name"] for f in fields}
    assert "categories.primary" in names
    assert "categories.alternate" in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_introspection.py -v -k flatten`
Expected: FAIL — `flatten_schema` doesn't exist.

- [ ] **Step 3: Implement the helper**

Append to `overturemaps/introspection.py`:

```python
import pyarrow as pa


def flatten_schema(schema: pa.Schema) -> List[dict]:
    """Flatten a (possibly nested) Arrow schema into a list of dotted field rows.

    Each row is {"name": "categories.primary", "type": "string"}.
    """
    out: List[dict] = []

    def _walk(prefix: str, type_):
        if pa.types.is_struct(type_):
            for i in range(type_.num_fields):
                child = type_.field(i)
                _walk(f"{prefix}.{child.name}" if prefix else child.name, child.type)
        elif pa.types.is_list(type_) or pa.types.is_large_list(type_):
            out.append({"name": prefix, "type": str(type_)})
        else:
            out.append({"name": prefix, "type": str(type_)})

    for field in schema:
        _walk(field.name, field.type)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_introspection.py -v`
Expected: all tests pass.

- [ ] **Step 5: Write the CLI test**

Create `tests/test_cli_schema.py`:

```python
"""Tests for the `schema` command."""

import json

import pyarrow as pa
from click.testing import CliRunner

from overturemaps.cli import cli


def test_schema_command_json(monkeypatch):
    """`schema` returns flattened schema + sample feature."""

    schema = pa.schema([
        ("id", pa.string()),
        ("height", pa.float64()),
        ("categories", pa.struct([("primary", pa.string())])),
    ])

    class _Reader:
        def __init__(self):
            self.schema = schema
            self._done = False

        def read_next_batch(self):
            if self._done:
                raise StopIteration
            self._done = True
            return pa.RecordBatch.from_pylist(
                [{"id": "abc", "height": 50.0, "categories": {"primary": "hotel"}}],
                schema=schema,
            )

    monkeypatch.setattr("overturemaps.cli.record_batch_reader",
                        lambda *a, **k: _Reader())
    monkeypatch.setattr("overturemaps.cli.get_latest_release",
                        lambda: "2025-12-17.0")

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "schema", "-t", "place"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["type"] == "place"
    field_names = {f["name"] for f in payload["fields"]}
    assert "id" in field_names
    assert "categories.primary" in field_names
    assert "example" in payload
```

- [ ] **Step 6: Run test to verify it fails, then add the command**

Run: `uv run pytest tests/test_cli_schema.py -v`
Expected: FAIL — `schema` command doesn't exist.

Add to `overturemaps/cli.py`:

```python
from .introspection import flatten_schema
```

```python
@cli.command()
@click.option("-t", "--type", "type_",
              type=click.Choice(get_all_overture_types()), required=True)
@click.option("-r", "--release", default=None, callback=validate_release,
              required=False)
@click.pass_context
def schema(ctx, type_, release):
    """Show the schema and a sample feature for an Overture type.

    Uses a tiny bbox over a known-populated area to keep this fast.
    """
    # A bbox over Manhattan, NYC — densely populated for any type.
    sample_bbox = [-74.020, 40.700, -73.930, 40.800]
    reader = record_batch_reader(
        type_, sample_bbox, release, None, None, True,
    )
    if reader is None:
        raise click.ClickException(f"No features available for type {type_!r}")

    sample = None
    try:
        batch = reader.read_next_batch()
        if batch.num_rows > 0:
            sample = batch.slice(0, 1).to_pylist()[0]
    except StopIteration:
        pass

    fields = flatten_schema(reader.schema)
    payload = {
        "type": type_,
        "fields": fields,
        "example": sample,
    }

    if ctx.obj.get("json"):
        _emit_json(ctx, payload)
        return

    click.secho(f"Schema for type {type_!r}", bold=True)
    for f in fields:
        click.echo(f"  {f['name']}: {f['type']}")
    click.echo()
    if sample is not None:
        click.secho("Example feature:", bold=True)
        click.echo(orjson.dumps(sample, option=orjson.OPT_INDENT_2).decode())
```

- [ ] **Step 7: Run tests to verify**

Run: `uv run pytest tests/test_cli_schema.py tests/test_introspection.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add overturemaps/cli.py overturemaps/introspection.py \
        tests/test_cli_schema.py tests/test_introspection.py
git commit -m "Add `schema` command emitting flat field list and sample feature"
```

---

## Task 2.8: `categories` command

Enumerates `categories.primary` values for `place`. Optional `--in` scope and `--top N`.

**Files:**
- Modify: `overturemaps/introspection.py`
- Modify: `overturemaps/cli.py`
- Create: `tests/test_cli_categories.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_categories.py`:

```python
"""Tests for the `categories` command."""

import json

import pyarrow as pa
from click.testing import CliRunner

from overturemaps.cli import cli


def test_categories_returns_top_values(monkeypatch):
    """Stub a reader that yields a batch with category values; verify top-N counting."""

    schema = pa.schema([
        ("categories", pa.struct([("primary", pa.string())])),
    ])

    rows = (
        [{"categories": {"primary": "restaurant"}}] * 10 +
        [{"categories": {"primary": "cafe"}}] * 7 +
        [{"categories": {"primary": "bar"}}] * 3 +
        [{"categories": {"primary": "hotel"}}] * 1
    )

    class _Reader:
        def __init__(self):
            self.schema = schema
            self._done = False

        def read_next_batch(self):
            if self._done:
                raise StopIteration
            self._done = True
            return pa.RecordBatch.from_pylist(rows, schema=schema)

    monkeypatch.setattr("overturemaps.cli.record_batch_reader",
                        lambda *a, **k: _Reader())
    monkeypatch.setattr("overturemaps.cli.get_latest_release",
                        lambda: "2025-12-17.0")

    runner = CliRunner()
    result = runner.invoke(cli, [
        "--json", "categories", "-t", "place",
        "--bbox", "-71.1,42.3,-71.0,42.4", "--top", "3",
    ])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data == [
        {"value": "restaurant", "count": 10},
        {"value": "cafe", "count": 7},
        {"value": "bar", "count": 3},
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_categories.py -v`
Expected: FAIL — `categories` command doesn't exist.

- [ ] **Step 3: Add the command**

In `overturemaps/cli.py`:

```python
@cli.command()
@click.option("-t", "--type", "type_",
              type=click.Choice(["place"]), default="place", show_default=True,
              help="Currently only `place` is supported.")
@click.option("--bbox", required=False, type=BboxParamType())
@click.option("--in", "in_place", required=False, type=str)
@click.option("--top", default=20, show_default=True, type=int)
@click.option("-r", "--release", default=None, callback=validate_release,
              required=False)
@click.pass_context
def categories(ctx, type_, bbox, in_place, top, release):
    """Enumerate `categories.primary` values, sorted by count desc."""
    if bbox is not None and in_place is not None:
        raise click.UsageError("--bbox and --in are mutually exclusive")
    if in_place is not None:
        try:
            division = best_match(in_place)
        except LookupError as e:
            raise click.UsageError(str(e))
        bbox = list(division.bbox)

    if bbox is None:
        raise click.UsageError("Provide --bbox or --in; global enumeration is too costly.")

    reader = record_batch_reader(type_, bbox, release, None, None, True)
    if reader is None:
        if ctx.obj.get("json"):
            _emit_json(ctx, [])
        return

    import pyarrow.compute as pc

    counts: dict[str, int] = {}
    while True:
        try:
            batch = reader.read_next_batch()
        except StopIteration:
            break
        if batch.num_rows == 0:
            continue
        cat_col = batch.column("categories").combine_chunks()
        primary = pc.struct_field(cat_col, "primary")
        for item in pc.value_counts(primary).to_pylist():
            val = item["values"]
            if val is None:
                continue
            counts[val] = counts.get(val, 0) + item["counts"]

    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:top]
    payload = [{"value": v, "count": c} for v, c in ranked]

    if ctx.obj.get("json"):
        _emit_json(ctx, payload)
        return
    for row in payload:
        click.echo(f"  {row['count']:>8,}  {row['value']}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli_categories.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add overturemaps/cli.py tests/test_cli_categories.py
git commit -m "Add `categories` command enumerating place taxonomy

Scoped via --in or --bbox; returns top-N values by count. Global
enumeration is rejected to avoid full-scan cost."
```

---

## Task 2.9: `capabilities` command

Introspects the Click app and emits a JSON manifest.

**Files:**
- Modify: `overturemaps/cli.py`
- Create: `tests/test_cli_capabilities.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_capabilities.py`:

```python
"""Tests for the `capabilities` command."""

import json

from click.testing import CliRunner

from overturemaps.cli import cli


def test_capabilities_json_structure():
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "capabilities"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "version" in data
    assert "commands" in data
    cmd_names = {c["name"] for c in data["commands"]}
    # Spot check: a few expected commands are present
    for expected in ("download", "where", "count", "themes", "types",
                     "schema", "categories", "capabilities"):
        assert expected in cmd_names, f"missing command {expected}"


def test_capabilities_command_has_params():
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "capabilities"])
    data = json.loads(result.output)
    download = next(c for c in data["commands"] if c["name"] == "download")
    param_names = {p["name"] for p in download["params"]}
    assert "type" in param_names or "type_" in param_names
    assert "bbox" in param_names
    assert "in" in param_names or "in_place" in param_names
    assert "where" in param_names or "where_exprs" in param_names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_capabilities.py -v`
Expected: FAIL — `capabilities` command doesn't exist.

- [ ] **Step 3: Implement the introspector**

Add to `overturemaps/cli.py`:

```python
import importlib.metadata as _ilm


def _describe_param(param: click.Parameter) -> dict:
    """Convert a Click parameter to a JSON-friendly description."""
    out: dict = {
        "name": param.name,
        "required": getattr(param, "required", False),
    }
    if isinstance(param, click.Option):
        out["flags"] = list(param.opts)
        out["help"] = param.help or ""
        out["multiple"] = param.multiple
        out["is_flag"] = param.is_flag
    if param.default is not None and param.default is not False:
        try:
            out["default"] = param.default
        except Exception:
            pass
    if isinstance(param.type, click.Choice):
        out["choices"] = list(param.type.choices)
    return out


def _describe_command(name: str, command: click.Command) -> dict:
    return {
        "name": name,
        "help": command.help or command.short_help or "",
        "params": [_describe_param(p) for p in command.params],
    }


def _walk_group(group: click.Group, prefix: str = "") -> list[dict]:
    out: list[dict] = []
    for name, cmd in group.commands.items():
        full = f"{prefix}{name}" if not prefix else f"{prefix} {name}"
        if isinstance(cmd, click.Group):
            out.extend(_walk_group(cmd, prefix=full))
        else:
            out.append(_describe_command(full, cmd))
    return out


@cli.command()
@click.pass_context
def capabilities(ctx):
    """Emit a machine-readable manifest of all subcommands."""
    payload = {
        "version": _ilm.version("overturemaps"),
        "commands": _walk_group(cli),
    }
    if ctx.obj.get("json"):
        _emit_json(ctx, payload)
        return
    # Human mode just prints command names.
    for c in payload["commands"]:
        click.secho(c["name"], bold=True)
        if c["help"]:
            click.echo(f"  {c['help']}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli_capabilities.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add overturemaps/cli.py tests/test_cli_capabilities.py
git commit -m "Add `capabilities` command — JSON manifest of subcommands

Walks the Click group tree, describing each command's params (flags,
help, defaults, choices) so agents can auto-discover the CLI surface."
```

---

## Task 2.10: `cache` command group

`cache info`, `cache clear`, `cache build`.

**Files:**
- Modify: `overturemaps/cli.py`
- Create: `tests/test_cli_cache.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_cache.py`:

```python
"""Tests for the `cache` command group."""

import json
from pathlib import Path

from click.testing import CliRunner

from overturemaps.cli import cli


def test_cache_info_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr("overturemaps.cli.get_latest_release",
                        lambda: "2025-12-17.0")

    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "cache", "info"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["latest_release"] == "2025-12-17.0"
    assert data["up_to_date"] is False
    assert data["index_release"] is None


def test_cache_clear_removes_files(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    from overturemaps.cache import index_path
    p = index_path("2025-12-17.0")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")

    runner = CliRunner()
    result = runner.invoke(cli, ["cache", "clear"])
    assert result.exit_code == 0
    assert not p.exists()
    assert "1" in result.output or "Removed" in result.output


def test_cache_build_invokes_build_index(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr("overturemaps.cli.get_latest_release",
                        lambda: "2025-12-17.0")

    called = []
    def fake_build(release):
        called.append(release)
        from overturemaps.cache import index_path
        p = index_path(release)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"built")
        return p
    monkeypatch.setattr("overturemaps.cli.build_index", fake_build)

    runner = CliRunner()
    result = runner.invoke(cli, ["cache", "build"])
    assert result.exit_code == 0, result.output
    assert called == ["2025-12-17.0"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_cache.py -v`
Expected: FAIL — `cache` command group doesn't exist.

- [ ] **Step 3: Add the command group**

In `overturemaps/cli.py`:

```python
from .cache import cache_info, clear_cache, build_index, index_path


@cli.group()
def cache():
    """Manage the on-disk divisions index."""
    pass


@cache.command("info")
@click.pass_context
def cache_info_cmd(ctx):
    """Show cache location, current release, and up-to-date status."""
    latest = get_latest_release()
    data = cache_info(latest_release=latest)
    if ctx.obj.get("json"):
        _emit_json(ctx, data)
        return
    click.echo(f"Cache path:      {data['index_path']}")
    click.echo(f"Cached release:  {data['index_release'] or '(none)'}")
    click.echo(f"Latest release:  {data['latest_release']}")
    click.echo(f"Up to date:      {data['up_to_date']}")
    click.echo(f"Size:            {data['size_bytes']:,} bytes")


@cache.command("clear")
def cache_clear_cmd():
    """Remove all divisions-index files from the cache."""
    n = clear_cache()
    click.echo(f"Removed {n} cache file(s).")


@cache.command("build")
def cache_build_cmd():
    """Force a rebuild of the divisions index against the latest release."""
    release = get_latest_release()
    click.secho(f"Building divisions index for release {release}...",
                fg="bright_black", err=True)
    p = build_index(release)
    click.secho(f"Wrote {p}", fg="green", err=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli_cache.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add overturemaps/cli.py tests/test_cli_cache.py
git commit -m "Add `cache info|clear|build` command group"
```

---

## Phase 2 ship gate

- [ ] Run: `uv run pytest tests/ -m "not integration" -v` → all pass.
- [ ] Run: `uv run overturemaps --json capabilities | jq '.commands[].name'` (if `jq` available) — should list all commands.
- [ ] Run: `uv run overturemaps where "Boston, MA"` and verify human + JSON modes.

🚦 **Phase 2 ship gate:** every metadata command is JSON-discoverable. Agents can introspect the CLI without docs.

---

# Phase 3 — Intent Verbs

Five thin commands: `places`, `buildings`, `roads`, `at`, `containing`. Each is sugar over the existing pipeline.

---

## Task 3.1: `intents.py` shared helpers

Helpers: bbox-from-point-with-radius (meters → degrees), haversine distance, common-options decorator.

**Files:**
- Create: `overturemaps/intents.py`
- Create: `tests/test_intents.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_intents.py`:

```python
"""Tests for intent-verb helpers."""

import math

import pytest

from overturemaps.intents import bbox_around_point, haversine_meters


class TestBboxAroundPoint:
    def test_small_radius_at_equator(self):
        bbox = bbox_around_point(0.0, 0.0, radius_meters=111320)
        # 1 deg at the equator ~= 111.32 km. Radius of 111320 m -> bbox ~= 1 deg each side.
        assert abs((bbox[2] - bbox[0]) - 2.0) < 0.05
        assert abs((bbox[3] - bbox[1]) - 2.0) < 0.05

    def test_radius_shrinks_with_latitude(self):
        bbox_equator = bbox_around_point(0.0, 0.0, radius_meters=10000)
        bbox_polar = bbox_around_point(0.0, 60.0, radius_meters=10000)
        eq_width = bbox_equator[2] - bbox_equator[0]
        polar_width = bbox_polar[2] - bbox_polar[0]
        # At 60° N, cos(60°) = 0.5, so longitude span doubles for the same radius
        assert polar_width > 1.5 * eq_width


class TestHaversineMeters:
    def test_zero_distance(self):
        assert haversine_meters(42.0, -71.0, 42.0, -71.0) == 0

    def test_known_distance_short(self):
        # Approximately 111 m for 0.001 degrees of latitude at the equator
        d = haversine_meters(0.0, 0.0, 0.001, 0.0)
        assert 100 < d < 130

    def test_symmetry(self):
        d1 = haversine_meters(42.0, -71.0, 42.5, -70.5)
        d2 = haversine_meters(42.5, -70.5, 42.0, -71.0)
        assert math.isclose(d1, d2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_intents.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

Create `overturemaps/intents.py`:

```python
"""Shared helpers for the intent verbs (places, buildings, roads, at, containing)."""

from __future__ import annotations

import math

# Meters per degree at the equator.
_M_PER_DEG = 111_320.0


def bbox_around_point(
    lat: float, lon: float, radius_meters: float
) -> tuple[float, float, float, float]:
    """Return (xmin, ymin, xmax, ymax) for a bbox around a point.

    Note: caller passes lat then lon (geographic convention) but bbox is
    returned in lon/lat order to match the rest of the codebase.
    """
    dlat = radius_meters / _M_PER_DEG
    cos_lat = max(math.cos(math.radians(lat)), 1e-6)
    dlon = radius_meters / (_M_PER_DEG * cos_lat)
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points in meters."""
    R = 6_371_000  # mean Earth radius in meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


DEFAULT_RADIUS_BY_TYPE = {
    "place": 100,
    "building": 50,
    "address": 25,
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_intents.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add overturemaps/intents.py tests/test_intents.py
git commit -m "Add intents.py helpers: bbox_around_point, haversine_meters

Plus DEFAULT_RADIUS_BY_TYPE for `at`. Used by the intent verbs in
subsequent tasks."
```

---

## Task 3.2: `places` command

**Files:**
- Modify: `overturemaps/cli.py`
- Create: `tests/test_cli_intents_places.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_intents_places.py`:

```python
"""Tests for the `places` intent verb."""

import pytest
from click.testing import CliRunner

from overturemaps.cli import cli
from overturemaps.geocoding import Division


class _DummyWriter:
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _DummyReader:
    schema = object()


def _setup(monkeypatch):
    monkeypatch.setattr("overturemaps.cli.get_latest_release",
                        lambda: "2025-12-17.0")
    monkeypatch.setattr(
        "overturemaps.cli.best_match",
        lambda q: Division(
            id="boston", name="Boston", subtype="locality",
            country="US", region="US-MA",
            admin_level=8, population=654776, parent_division_id=None,
            bbox=(-71.19, 42.23, -70.99, 42.40),
        ),
    )
    monkeypatch.setattr("overturemaps.cli.get_writer",
                        lambda *a, **k: _DummyWriter())
    monkeypatch.setattr("overturemaps.cli.copy", lambda *a, **k: None)
    monkeypatch.setattr("overturemaps.cli.save_state", lambda *a, **k: None)


def test_places_with_category(monkeypatch):
    _setup(monkeypatch)
    captured = {}

    def fake_reader(type_, bbox, *a, where_filters=None, **k):
        captured["type"] = type_
        captured["bbox"] = bbox
        captured["where_filters"] = where_filters
        return _DummyReader()

    monkeypatch.setattr("overturemaps.cli.record_batch_reader", fake_reader)

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, [
            "places", "--in", "Boston, MA",
            "--category", "restaurant",
            "-f", "geojson", "-o", "out.geojson",
        ])
        assert result.exit_code == 0, result.output
        assert captured["type"] == "place"
        assert captured["bbox"] == [-71.19, 42.23, -70.99, 42.40]
        cats = [f for f in captured["where_filters"]
                if f.key == "categories.primary"]
        assert len(cats) == 1
        assert cats[0].value == "restaurant"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_intents_places.py -v`
Expected: FAIL.

- [ ] **Step 3: Add the command**

In `overturemaps/cli.py`:

```python
from .filters import ParsedFilter


@cli.command()
@click.option("--in", "in_place", required=True, type=str)
@click.option("--category", required=False, type=str,
              help="Shortcut for --where categories.primary=VAL")
@click.option("--where", "where_exprs", multiple=True)
@click.option("-f", "output_format",
              type=click.Choice(["geojson", "geojsonseq", "geoparquet"]),
              default="geojsonseq", show_default=True)
@click.option("-o", "--output", required=False, type=click.Path())
@click.option("-r", "--release", default=None, callback=validate_release,
              required=False)
def places(in_place, category, where_exprs, output_format, output, release):
    """Download POIs in a named place. Filter by --category for common asks."""
    try:
        division = best_match(in_place)
    except LookupError as e:
        raise click.UsageError(str(e))
    bbox = list(division.bbox)

    filters = [parse_where_expr(e) for e in where_exprs]
    if category is not None:
        filters.append(ParsedFilter(
            key="categories.primary", op="=", value=category,
        ))

    if output_format == "geoparquet" and output is None:
        raise click.UsageError("Output file (-o/--output) is required for geoparquet")

    output_file = sys.stdout if output is None else output

    reader = record_batch_reader(
        "place", bbox, release, None, None, True,
        where_filters=filters or None,
    )
    if reader is None:
        return

    with get_writer(output_format, output_file, schema=reader.schema) as writer:
        copy(reader, writer)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli_intents_places.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add overturemaps/cli.py tests/test_cli_intents_places.py
git commit -m "Add `places` intent verb

--in resolves to bbox; --category desugars to where categories.primary=VAL;
--where is additionally available for advanced filters."
```

---

## Task 3.3: `buildings` command

**Files:**
- Modify: `overturemaps/cli.py`
- Create: `tests/test_cli_intents_buildings.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_intents_buildings.py`:

```python
"""Tests for the `buildings` intent verb."""

import pytest
from click.testing import CliRunner

from overturemaps.cli import cli
from overturemaps.geocoding import Division


class _DummyWriter:
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _DummyReader:
    schema = object()


def test_buildings_passes_where_through(monkeypatch):
    monkeypatch.setattr("overturemaps.cli.get_latest_release",
                        lambda: "2025-12-17.0")
    monkeypatch.setattr(
        "overturemaps.cli.best_match",
        lambda q: Division(
            id="nyc", name="New York", subtype="locality",
            country="US", region="US-NY",
            admin_level=8, population=8000000, parent_division_id=None,
            bbox=(-74.05, 40.6, -73.9, 40.9),
        ),
    )
    monkeypatch.setattr("overturemaps.cli.get_writer", lambda *a, **k: _DummyWriter())
    monkeypatch.setattr("overturemaps.cli.copy", lambda *a, **k: None)

    captured = {}

    def fake_reader(type_, bbox, *a, where_filters=None, **k):
        captured["type"] = type_
        captured["bbox"] = bbox
        captured["where_filters"] = where_filters
        return _DummyReader()

    monkeypatch.setattr("overturemaps.cli.record_batch_reader", fake_reader)

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, [
            "buildings", "--in", "New York, US-NY",
            "--where", "height>100",
            "-f", "geojsonseq", "-o", "out.jsonl",
        ])
        assert result.exit_code == 0, result.output
        assert captured["type"] == "building"
        assert captured["where_filters"][0].key == "height"
        assert captured["where_filters"][0].op == ">"
        assert captured["where_filters"][0].value == 100
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_intents_buildings.py -v`
Expected: FAIL.

- [ ] **Step 3: Add the command**

```python
@cli.command()
@click.option("--in", "in_place", required=True, type=str)
@click.option("--where", "where_exprs", multiple=True)
@click.option("-f", "output_format",
              type=click.Choice(["geojson", "geojsonseq", "geoparquet"]),
              default="geojsonseq", show_default=True)
@click.option("-o", "--output", required=False, type=click.Path())
@click.option("-r", "--release", default=None, callback=validate_release,
              required=False)
def buildings(in_place, where_exprs, output_format, output, release):
    """Download buildings in a named place."""
    try:
        division = best_match(in_place)
    except LookupError as e:
        raise click.UsageError(str(e))
    bbox = list(division.bbox)
    filters = [parse_where_expr(e) for e in where_exprs] or None

    if output_format == "geoparquet" and output is None:
        raise click.UsageError("Output file (-o/--output) is required for geoparquet")
    output_file = sys.stdout if output is None else output

    reader = record_batch_reader(
        "building", bbox, release, None, None, True, where_filters=filters,
    )
    if reader is None:
        return
    with get_writer(output_format, output_file, schema=reader.schema) as writer:
        copy(reader, writer)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli_intents_buildings.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add overturemaps/cli.py tests/test_cli_intents_buildings.py
git commit -m "Add `buildings` intent verb"
```

---

## Task 3.4: `roads` command

**Files:**
- Modify: `overturemaps/cli.py`
- Create: `tests/test_cli_intents_roads.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_intents_roads.py`:

```python
"""Tests for the `roads` intent verb."""

import pytest
from click.testing import CliRunner

from overturemaps.cli import cli
from overturemaps.geocoding import Division


class _DummyWriter:
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _DummyReader:
    schema = object()


def test_roads_class_shortcut(monkeypatch):
    monkeypatch.setattr("overturemaps.cli.get_latest_release",
                        lambda: "2025-12-17.0")
    monkeypatch.setattr(
        "overturemaps.cli.best_match",
        lambda q: Division(
            id="tx", name="Texas", subtype="region",
            country="US", region="US-TX",
            admin_level=4, population=30000000, parent_division_id=None,
            bbox=(-106.6, 25.8, -93.5, 36.5),
        ),
    )
    monkeypatch.setattr("overturemaps.cli.get_writer", lambda *a, **k: _DummyWriter())
    monkeypatch.setattr("overturemaps.cli.copy", lambda *a, **k: None)

    captured = {}

    def fake_reader(type_, bbox, *a, where_filters=None, **k):
        captured["type"] = type_
        captured["where_filters"] = where_filters
        return _DummyReader()

    monkeypatch.setattr("overturemaps.cli.record_batch_reader", fake_reader)

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, [
            "roads", "--in", "Texas, US",
            "--class", "motorway",
            "-f", "geojsonseq", "-o", "out.jsonl",
        ])
        assert result.exit_code == 0, result.output
        assert captured["type"] == "segment"
        klass = [f for f in captured["where_filters"] if f.key == "class"]
        assert klass and klass[0].value == "motorway"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_intents_roads.py -v`
Expected: FAIL.

- [ ] **Step 3: Add the command**

Note: Click reserves `class` as a Python keyword and won't bind to a `class` parameter directly. Use `road_class` as the Python variable and `--class` as the flag.

```python
@cli.command()
@click.option("--in", "in_place", required=True, type=str)
@click.option("--class", "road_class", required=False, type=str,
              help="Shortcut for --where class=VAL (e.g. motorway, primary)")
@click.option("--where", "where_exprs", multiple=True)
@click.option("-f", "output_format",
              type=click.Choice(["geojson", "geojsonseq", "geoparquet"]),
              default="geojsonseq", show_default=True)
@click.option("-o", "--output", required=False, type=click.Path())
@click.option("-r", "--release", default=None, callback=validate_release,
              required=False)
def roads(in_place, road_class, where_exprs, output_format, output, release):
    """Download road segments in a named place."""
    try:
        division = best_match(in_place)
    except LookupError as e:
        raise click.UsageError(str(e))
    bbox = list(division.bbox)

    filters = [parse_where_expr(e) for e in where_exprs]
    if road_class is not None:
        filters.append(ParsedFilter(key="class", op="=", value=road_class))

    if output_format == "geoparquet" and output is None:
        raise click.UsageError("Output file (-o/--output) is required for geoparquet")
    output_file = sys.stdout if output is None else output

    reader = record_batch_reader(
        "segment", bbox, release, None, None, True,
        where_filters=filters or None,
    )
    if reader is None:
        return
    with get_writer(output_format, output_file, schema=reader.schema) as writer:
        copy(reader, writer)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli_intents_roads.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add overturemaps/cli.py tests/test_cli_intents_roads.py
git commit -m "Add `roads` intent verb (segment, with --class shortcut)"
```

---

## Task 3.5: `at` command

**Files:**
- Modify: `overturemaps/cli.py`
- Create: `tests/test_cli_at.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_at.py`:

```python
"""Tests for the `at` command."""

import pyarrow as pa
import pytest
from click.testing import CliRunner

from overturemaps.cli import cli


def _features_reader(rows):
    """Build a one-shot RecordBatchReader from a list of dicts."""
    schema = pa.schema([
        ("id", pa.string()),
        ("name", pa.string()),
        ("geometry", pa.binary()),
    ])
    return _Reader(schema, rows)


class _Reader:
    def __init__(self, schema, rows):
        self.schema = schema
        self._rows = rows
        self._done = False

    def read_next_batch(self):
        if self._done:
            raise StopIteration
        self._done = True
        return pa.RecordBatch.from_pylist(self._rows, schema=self.schema)


def test_at_sorts_by_distance(monkeypatch, tmp_path):
    """`at` should keep the N closest features, sorted by distance ascending."""
    import shapely
    from shapely.geometry import Point

    # Three points: the third is closest, the first is farthest.
    pts = [
        ("p_far", "Far Cafe", shapely.wkb.dumps(Point(-71.060, 42.360))),
        ("p_mid", "Mid Cafe", shapely.wkb.dumps(Point(-71.061, 42.360))),
        ("p_near", "Near Cafe", shapely.wkb.dumps(Point(-71.0615, 42.360))),
    ]
    rows = [{"id": pid, "name": name, "geometry": wkb}
            for pid, name, wkb in pts]

    monkeypatch.setattr("overturemaps.cli.get_latest_release",
                        lambda: "2025-12-17.0")
    monkeypatch.setattr("overturemaps.cli.record_batch_reader",
                        lambda *a, **k: _features_reader(rows))

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, [
            "at", "42.360,-71.0617", "-t", "place", "-n", "2",
            "-f", "geojsonseq", "-o", "out.jsonl",
        ])
        assert result.exit_code == 0, result.output
        lines = open("out.jsonl").read().strip().split("\n")
        assert len(lines) == 2
        # First line should be the nearest feature
        assert '"id":"p_near"' in lines[0]
        assert '"id":"p_mid"' in lines[1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_at.py -v`
Expected: FAIL.

- [ ] **Step 3: Add the command**

```python
from .intents import bbox_around_point, haversine_meters, DEFAULT_RADIUS_BY_TYPE


@cli.command()
@click.argument("latlon", type=str)
@click.option("-t", "--type", "type_",
              type=click.Choice(get_all_overture_types()), default="place",
              show_default=True)
@click.option("-n", default=10, show_default=True, type=int)
@click.option("--radius", type=int, required=False,
              help="Radius in meters; defaults per type.")
@click.option("-f", "output_format",
              type=click.Choice(["geojson", "geojsonseq", "geoparquet"]),
              default="geojsonseq", show_default=True)
@click.option("-o", "--output", required=False, type=click.Path())
@click.option("-r", "--release", default=None, callback=validate_release,
              required=False)
def at(latlon, type_, n, radius, output_format, output, release):
    """Nearest-neighbor lookup. LATLON is 'LAT,LON' (lat first, geographic)."""
    parts = [p.strip() for p in latlon.split(",")]
    if len(parts) != 2:
        raise click.UsageError("LATLON must be 'LAT,LON'")
    try:
        lat = float(parts[0])
        lon = float(parts[1])
    except ValueError:
        raise click.UsageError("LATLON values must be numeric")

    if radius is None:
        radius = DEFAULT_RADIUS_BY_TYPE.get(type_, 100)
    bbox = list(bbox_around_point(lat, lon, radius))

    reader = record_batch_reader(type_, bbox, release, None, None, True)
    if reader is None:
        return

    # Collect all matching features, compute distance, sort, keep top N.
    import shapely.wkb
    import pyarrow as pa

    rows: list[tuple[float, dict, bytes]] = []
    while True:
        try:
            batch = reader.read_next_batch()
        except StopIteration:
            break
        if batch.num_rows == 0:
            continue
        geom_blobs = batch.column("geometry").to_pylist()
        prop_cols = [c for c in batch.schema.names if c not in ("geometry", "bbox")]
        prop_rows = batch.select(prop_cols).to_pylist()
        for blob, prop in zip(geom_blobs, prop_rows):
            try:
                geom = shapely.wkb.loads(blob)
                centroid = geom.centroid
                d = haversine_meters(lat, lon, centroid.y, centroid.x)
            except Exception:
                continue
            rows.append((d, prop, blob))

    rows.sort(key=lambda x: x[0])
    rows = rows[:n]

    if output_format == "geoparquet" and output is None:
        raise click.UsageError("Output file (-o/--output) is required for geoparquet")
    output_file = sys.stdout if output is None else output

    # Build a fresh reader over the kept rows and stream through the writer.
    if not rows:
        return

    sample_batch_props = [r[1] for r in rows]
    sample_batch_geoms = [r[2] for r in rows]
    new_schema = pa.schema(
        [(name, reader.schema.field(name).type) for name in reader.schema.names]
    )

    # Build one batch carrying back properties + geometry; rely on existing writer.
    out_rows = []
    for prop, blob in zip(sample_batch_props, sample_batch_geoms):
        row = dict(prop)
        row["geometry"] = blob
        out_rows.append(row)
    batch = pa.RecordBatch.from_pylist(out_rows, schema=new_schema)
    one_shot = pa.RecordBatchReader.from_batches(new_schema, iter([batch]))

    with get_writer(output_format, output_file, schema=new_schema) as writer:
        copy(one_shot, writer)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli_at.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add overturemaps/cli.py tests/test_cli_at.py
git commit -m "Add `at LAT,LON` nearest-neighbor command

Builds a small bbox around the point (per-type default radius), sorts
matching features by haversine distance to the centroid, keeps top N."
```

---

## Task 3.6: `containing` command

**Files:**
- Modify: `overturemaps/cli.py`
- Create: `tests/test_cli_containing.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_containing.py`:

```python
"""Tests for the `containing` command."""

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pyarrow.compute as pc
import pytest
from click.testing import CliRunner

from overturemaps.cli import cli


def _write_index(tmp_path):
    """Write a fake divisions index with three nested divisions."""
    table = pa.table({
        "id": ["us", "ma", "boston"],
        "name_primary": ["United States", "Massachusetts", "Boston"],
        "name_common": [None, None, None],
        "subtype": ["country", "region", "locality"],
        "class": [None, None, None],
        "country": ["US", "US", "US"],
        "region": [None, "US-MA", "US-MA"],
        "admin_level": [2, 4, 8],
        "population": [330000000, 7000000, 654776],
        "parent_division_id": [None, "us", "ma"],
        # All three bboxes contain Boston's downtown
        "bbox_xmin": [-180.0, -73.5, -71.19],
        "bbox_ymin": [18.0, 41.2, 42.23],
        "bbox_xmax": [-66.0, -69.9, -70.99],
        "bbox_ymax": [71.0, 42.9, 42.40],
    })
    p = tmp_path / "overturemaps" / "divisions-index-2025-12-17.0.parquet"
    p.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, p)


def test_containing_returns_innermost_first(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr("overturemaps.cli.get_latest_release",
                        lambda: "2025-12-17.0")
    # We don't need real polygon checks for this test; the bbox-only test
    # below verifies the bbox filter logic. The CLI is expected to confirm
    # polygon containment via shapely as well; for now we stub that.
    monkeypatch.setattr(
        "overturemaps.cli._polygon_contains",
        lambda division_id, lon, lat: True,
    )

    _write_index(tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli, [
        "--json", "containing", "42.360,-71.060",
    ])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    # Innermost (highest admin_level) first
    assert data[0]["subtype"] == "locality"
    assert data[1]["subtype"] == "region"
    assert data[2]["subtype"] == "country"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_containing.py -v`
Expected: FAIL — `containing` command doesn't exist.

- [ ] **Step 3: Add the command**

Add to `overturemaps/cli.py`:

```python
def _polygon_contains(division_id: str, lon: float, lat: float) -> bool:
    """True if the division_area polygon for `division_id` contains the point.

    Reads the polygon from the `division_area` S3 partition for the latest
    release, on demand. Cached per-process via the module-level dict.
    """
    import pyarrow.dataset as ds
    import pyarrow.fs as _fs
    import pyarrow.compute as _pc
    import shapely.wkb

    cache = _polygon_contains._cache  # type: ignore[attr-defined]
    if division_id in cache:
        poly = cache[division_id]
    else:
        release = get_latest_release()
        path = f"overturemaps-us-west-2/release/{release}/theme=divisions/type=division_area/"
        fs = _fs.S3FileSystem(anonymous=True, region="us-west-2")
        dataset = ds.dataset(path, filesystem=fs)
        filter_expr = _pc.field("division_id") == division_id
        table = dataset.to_table(columns=["geometry"], filter=filter_expr)
        if table.num_rows == 0:
            cache[division_id] = None
            return False
        # union the polygons (a division can have multiple division_area rows)
        geoms = [shapely.wkb.loads(b) for b in table.column("geometry").to_pylist()]
        from shapely.ops import unary_union
        poly = unary_union(geoms)
        cache[division_id] = poly

    if poly is None:
        return False
    from shapely.geometry import Point
    return poly.contains(Point(lon, lat))


_polygon_contains._cache = {}  # type: ignore[attr-defined]


@cli.command()
@click.argument("latlon", type=str)
@click.pass_context
def containing(ctx, latlon):
    """Which divisions contain this point? Innermost (highest admin_level) first."""
    parts = [p.strip() for p in latlon.split(",")]
    if len(parts) != 2:
        raise click.UsageError("LATLON must be 'LAT,LON'")
    try:
        lat = float(parts[0])
        lon = float(parts[1])
    except ValueError:
        raise click.UsageError("LATLON values must be numeric")

    release = get_latest_release()
    from .cache import ensure_index
    index_p = ensure_index(release)
    table = pq.read_table(index_p)

    # bbox-contains-point candidates
    expr = (
        (pc.field("bbox_xmin") <= lon)
        & (pc.field("bbox_xmax") >= lon)
        & (pc.field("bbox_ymin") <= lat)
        & (pc.field("bbox_ymax") >= lat)
    )
    candidates = table.filter(expr).to_pylist()

    # Confirm via polygon containment
    matches = [
        c for c in candidates
        if _polygon_contains(c["id"], lon, lat)
    ]
    # Innermost (highest admin_level) first
    matches.sort(key=lambda c: c["admin_level"], reverse=True)

    payload = [
        {
            "id": c["id"],
            "name": c["name_primary"],
            "subtype": c["subtype"],
            "admin_level": c["admin_level"],
            "country": c["country"],
            "region": c["region"],
        }
        for c in matches
    ]

    if ctx.obj.get("json"):
        _emit_json(ctx, payload)
        return
    for row in payload:
        loc = row["region"] or row["country"] or "?"
        click.secho(f"  {row['name']} ({row['subtype']}, {loc})", fg="cyan")
```

The import `pyarrow.parquet as pq` and `pyarrow.compute as pc` are needed at the top of cli.py; add them alongside existing imports if not already present.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli_containing.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add overturemaps/cli.py tests/test_cli_containing.py
git commit -m "Add `containing LAT,LON` command

Filters the divisions index by bbox-contains-point, then confirms via
shapely polygon containment against division_area features. Returns
divisions innermost-first."
```

🚦 **Phase 3 ship gate:** all intent verbs available. Run `uv run pytest tests/ -m "not integration" -v` to confirm.

---

# Phase 4 — Skill Installer

---

## Task 4.1: Author the SKILL.md content

This is a content task, not a code task. The file becomes both Claude's SKILL.md and (with a wrapper) the AGENTS.md section.

**Files:**
- Create: `overturemaps/data/skill.md`
- Modify: `pyproject.toml` (add `data/*.md` to package data)

- [ ] **Step 1: Create the package data directory and skill content**

Create `overturemaps/data/__init__.py` (empty file to ensure the directory ships):

```python
```

Create `overturemaps/data/skill.md`:

````markdown
---
name: overturemaps
description: Use when a user's question or task involves places, buildings, roads, addresses, neighborhoods, or other geographic features — even if they don't use geo terms. Examples: "how many coffee shops in SoHo", "find hospitals in Brooklyn", "show tall buildings in Manhattan", "what neighborhoods make up Queens", "what's the bounding box for Tokyo".
---

# Overture Maps CLI

The `overturemaps` CLI streams open geospatial data (buildings, places, roads,
addresses, administrative divisions) directly from Overture's public S3 bucket.
It is the right tool whenever the user's question implies a place, an area,
or a kind of feature on the map — even when they don't use geo terminology.

## When to reach for this CLI

Triggering phrases (illustrative, not exhaustive):

- *"How many coffee shops are in SoHo?"* → `where` + `count -t place --where categories.primary=coffee_shop`
- *"Find hospitals in Brooklyn"* → `places --in "Brooklyn" --category hospital`
- *"Show buildings taller than 100m in Manhattan"* → `buildings --in "Manhattan" --where height>100`
- *"What highways run through Texas?"* → `roads --in "Texas, US" --class motorway`
- *"What neighborhood is this address in?"* → `containing LAT,LON`
- *"What's at 40.7128, -74.0060?"* → `at 40.7128,-74.0060`
- *"What's the bounding box of Boston?"* → `where "Boston, MA" --json`

Negative examples (do NOT reach for overturemaps):

- "Draw a map of the org chart" — not geographic
- "What's the time in Boston?" — geography incidental, no spatial query needed
- "Write a regex for postal codes" — schema knowledge unrelated to map data

## Self-discovery

If you forget the surface, run `overturemaps --json capabilities`. It returns
a manifest of every subcommand with its parameters.

## Recipes

### 1. Resolve a place name to a bbox
```bash
overturemaps --json where "Boston, MA"
# {"name":"Boston","bbox":[-71.19,42.23,-70.99,42.40], ...}
```

### 2. Count before downloading (always check first)
```bash
overturemaps --json count -t building --in "Tokyo"
# {"count": 8_754_321, ...}
# That's too many. Add filters or pick a smaller place.
```

### 3. Sample to confirm shape before committing
```bash
overturemaps sample -t place --in "Boston, MA" --where categories.primary=restaurant -n 5
```

### 4. POIs by category
```bash
overturemaps places --in "SoHo, US-NY" --category cafe \
  -f geojsonseq -o cafes.jsonl
```

### 5. Tall buildings
```bash
overturemaps buildings --in "Manhattan" --where height>150 \
  -f geojsonseq -o tall.jsonl
```

### 6. Highways in a state
```bash
overturemaps roads --in "Texas, US" --class motorway \
  -f geojsonseq -o tx_highways.jsonl
```

### 7. What's near a point
```bash
overturemaps at 40.7484,-73.9857 -t place -n 10
```

### 8. Which admin areas contain a point
```bash
overturemaps --json containing 40.7484,-73.9857
# [{"name":"New York","subtype":"locality",...}, {"name":"New York","subtype":"region",...}, ...]
```

### 9. Discover what categories exist in a place
```bash
overturemaps --json categories -t place --in "Brooklyn" --top 20
```

### 10. Discover what's queryable on a type
```bash
overturemaps --json schema -t building
# Lists every field name and a sample feature.
```

### 11. Compose where + download
```bash
BBOX=$(overturemaps --json where "Berlin" | jq -r '.bbox | join(",")')
overturemaps download -t place --bbox "$BBOX" \
  --where categories.primary=hotel \
  -f geojsonseq -o berlin_hotels.jsonl
```

### 12. Cache management
```bash
overturemaps --json cache info       # is the divisions index current?
overturemaps cache build             # force rebuild against latest release
overturemaps cache clear             # nuke local cache
```

## Schema cheatsheet

| Type | Theme | Key properties |
|---|---|---|
| `place` | places | `categories.primary` (hotel, restaurant, cafe, hospital, ...), `names.primary`, `confidence`, `addresses` |
| `building` | buildings | `height` (meters), `num_floors`, `class`, `subtype`, `roof_shape` |
| `segment` | transportation | `class` (motorway, primary, secondary, residential, footway), `subclass`, `surface`, `speed_limits` |
| `division` | divisions | `subtype` (country, region, county, locality, neighborhood, ...), `admin_level`, `population` |
| `address` | addresses | `street`, `number`, `postcode`, `country` |
| `land_use` | base | `class` (commercial, residential, recreation, agriculture, ...) |
| `water` | base | `class` (ocean, lake, river, ...) |

Run `overturemaps --json schema -t TYPE` for the full field list of any type.

## Filter expression syntax

Operators: `=`, `!=`, `<`, `<=`, `>`, `>=`, `in`. Keys are dot-paths into the
type's schema. Multiple `--where` flags AND together.

```
--where categories.primary=restaurant
--where height>100
--where "class in [motorway,primary,trunk]"
```

## Anti-patterns

- **Don't download globally.** Always pass `--in` or `--bbox`. Global queries
  download hundreds of GB.
- **Always `count` before downloading anything large.** If the count is in
  the millions, narrow your filters before committing.
- **Prefer `--where` over post-filtering.** Filters push down to PyArrow and
  Parquet metadata; post-filtering GeoJSON is wasteful.
- **Don't invent place names.** If `where` returns no match, the place isn't
  in Overture's divisions. Try a parent (city → state → country).
- **Don't parse human stdout.** Use `--json` for metadata commands. Data
  commands always emit structured GeoJSON / GeoParquet.
- **Don't ignore the `--in` warning on stderr.** It tells you which Boston
  you actually got. If wrong, narrow with `--in "Boston, US-MA"`.
````

- [ ] **Step 2: Make the file ship with the package**

Edit `pyproject.toml`. Find the existing `[build-system]` block. Add a hatch tool block after the `[project.scripts]` block:

```toml
[tool.hatch.build.targets.wheel]
packages = ["overturemaps"]

[tool.hatch.build.targets.wheel.force-include]
"overturemaps/data" = "overturemaps/data"
```

(If a `[tool.hatch.*]` block already exists, merge instead of duplicating.)

- [ ] **Step 3: Confirm the file is reachable via importlib.resources**

Create `tests/test_skill_resource.py`:

```python
"""The SKILL.md content must be readable via importlib.resources."""

from importlib import resources


def test_skill_md_present():
    files = resources.files("overturemaps") / "data" / "skill.md"
    text = files.read_text()
    assert "name: overturemaps" in text
    assert "When to reach for this CLI" in text
```

Run: `uv run pytest tests/test_skill_resource.py -v`
Expected: PASS in dev install (`uv sync` makes the package importable in-place).

- [ ] **Step 4: Commit**

```bash
git add overturemaps/data/__init__.py overturemaps/data/skill.md \
        pyproject.toml tests/test_skill_resource.py
git commit -m "Author the agent Skill content as a package data file

Single source of truth for both Claude Code's SKILL.md and the
AGENTS.md section. Includes trigger language, recipe catalog, schema
cheatsheet, and anti-patterns."
```

---

## Task 4.2: skill_installer.py module

Writers for Claude Code and AGENTS.md targets.

**Files:**
- Create: `overturemaps/skill_installer.py`
- Create: `tests/test_skill_installer.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_skill_installer.py`:

```python
"""Tests for the Skill installer module."""

from pathlib import Path

import pytest

from overturemaps.skill_installer import (
    install_claude_user,
    install_claude_project,
    install_agents_md,
    AGENTS_START_MARKER,
    AGENTS_END_MARKER,
)


def test_install_claude_user_writes_skill_md(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    target = install_claude_user()
    assert target == tmp_path / ".claude" / "skills" / "overturemaps" / "SKILL.md"
    assert target.exists()
    text = target.read_text()
    assert "name: overturemaps" in text


def test_install_claude_project_uses_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = install_claude_project()
    assert target == tmp_path / ".claude" / "skills" / "overturemaps" / "SKILL.md"
    assert target.exists()


def test_install_agents_md_creates_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = install_agents_md()
    assert target == tmp_path / "AGENTS.md"
    text = target.read_text()
    assert AGENTS_START_MARKER in text
    assert AGENTS_END_MARKER in text
    assert "Overture Maps CLI" in text


def test_install_agents_md_replaces_existing_block(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agents = tmp_path / "AGENTS.md"
    agents.write_text(
        "# Project agent guide\n\n"
        "Some pre-existing content.\n\n"
        f"{AGENTS_START_MARKER}\n"
        "old skill content\n"
        f"{AGENTS_END_MARKER}\n\n"
        "More after.\n"
    )

    install_agents_md()

    text = agents.read_text()
    assert "Some pre-existing content" in text
    assert "More after" in text
    assert "old skill content" not in text
    assert "Overture Maps CLI" in text


def test_install_agents_md_appends_when_no_marker(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agents = tmp_path / "AGENTS.md"
    agents.write_text("# Project guide\n\nExisting prose.\n")

    install_agents_md()

    text = agents.read_text()
    assert "Existing prose" in text
    assert AGENTS_START_MARKER in text
    assert "Overture Maps CLI" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_skill_installer.py -v`
Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement the installer**

Create `overturemaps/skill_installer.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_skill_installer.py -v`
Expected: all 5 tests pass.

- [ ] **Step 5: Commit**

```bash
git add overturemaps/skill_installer.py tests/test_skill_installer.py
git commit -m "Add Skill installer writers for Claude Code and AGENTS.md

User/project Claude scopes write SKILL.md verbatim. AGENTS.md target
inserts between markers, replacing the marked block on rerun and
appending if the file exists without markers."
```

---

## Task 4.3: `install-skill` command

**Files:**
- Modify: `overturemaps/cli.py`
- Create: `tests/test_cli_install_skill.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_install_skill.py`:

```python
"""Tests for the `install-skill` CLI command."""

from pathlib import Path

import pytest
from click.testing import CliRunner

from overturemaps.cli import cli


def test_install_skill_non_interactive_claude_user(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, [
            "install-skill", "--target", "claude-user", "--yes",
        ])
        assert result.exit_code == 0, result.output
        expected = tmp_path / ".claude" / "skills" / "overturemaps" / "SKILL.md"
        assert expected.exists()


def test_install_skill_multiple_targets(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, [
            "install-skill",
            "--target", "claude-user",
            "--target", "agents-md",
            "--yes",
        ])
        assert result.exit_code == 0, result.output
        assert (tmp_path / ".claude" / "skills" / "overturemaps" / "SKILL.md").exists()
        # Project-CWD AGENTS.md (isolated_filesystem changes CWD into a temp dir)
        # We can't easily check the temp dir from here; just verify exit code.


def test_install_skill_rejects_unknown_target():
    runner = CliRunner()
    result = runner.invoke(cli, [
        "install-skill", "--target", "not-a-target", "--yes",
    ])
    assert result.exit_code != 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_install_skill.py -v`
Expected: FAIL — `install-skill` command doesn't exist.

- [ ] **Step 3: Add the command**

In `overturemaps/cli.py`:

```python
from . import skill_installer


_INSTALL_TARGETS = ("claude-user", "claude-project", "agents-md")


@cli.command("install-skill")
@click.option("--target", "targets", multiple=True,
              type=click.Choice(_INSTALL_TARGETS),
              help="Target to install to. Repeat for multiple. "
                   "Omit to be prompted.")
@click.option("--yes", "skip_confirm", is_flag=True, default=False,
              help="Skip overwrite confirmations.")
def install_skill_cmd(targets, skip_confirm):
    """Install the Overture agent Skill (Claude Code / AGENTS.md)."""
    if not targets:
        targets = []
        click.echo("Where should the Skill be installed?")
        for t in _INSTALL_TARGETS:
            if click.confirm(f"  Install to {t}?", default=(t == "claude-user")):
                targets.append(t)
        if not targets:
            click.secho("No targets selected; nothing to do.", fg="yellow")
            return

    for t in targets:
        if t == "claude-user":
            target_path = skill_installer._claude_user_dir() / "SKILL.md"
            if target_path.exists() and not skip_confirm:
                if not click.confirm(f"Overwrite {target_path}?", default=True):
                    continue
            p = skill_installer.install_claude_user()
            click.secho(f"Wrote {p}", fg="green")
        elif t == "claude-project":
            target_path = skill_installer._claude_project_dir() / "SKILL.md"
            if target_path.exists() and not skip_confirm:
                if not click.confirm(f"Overwrite {target_path}?", default=True):
                    continue
            p = skill_installer.install_claude_project()
            click.secho(f"Wrote {p}", fg="green")
        elif t == "agents-md":
            p = skill_installer.install_agents_md()
            click.secho(f"Updated {p}", fg="green")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_cli_install_skill.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add overturemaps/cli.py tests/test_cli_install_skill.py
git commit -m "Add `install-skill` command

Interactive (Click confirms) or non-interactive (--target ... --yes).
Multi-select via repeated --target. Confirms overwrites for Claude
targets; AGENTS.md is updated in place via the marker block."
```

🚦 **Phase 4 ship gate:** the Skill is installable.

---

# Phase 5 — Docs and Release

---

## Task 5.0: Integration smoke tests (real S3)

Add the integration tests required by §12.2 of the spec. These hit real S3, so they're slow and marked accordingly.

**Files:**
- Create: `tests/test_cli_integration.py`

- [ ] **Step 1: Write the integration tests**

Create `tests/test_cli_integration.py`:

```python
"""Integration smoke tests for the new agent-facing commands."""

import json

import pytest
from click.testing import CliRunner

from overturemaps.cli import cli

pytestmark = pytest.mark.integration


def test_where_boston_ma():
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "where", "Boston, US-MA"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["region"] == "US-MA"
    assert data["subtype"] == "locality"
    assert data["population"] > 100_000
    xmin, ymin, xmax, ymax = data["bbox"]
    assert -72 < xmin < -70 and 41 < ymin < 43


def test_count_places_in_boston():
    runner = CliRunner()
    result = runner.invoke(cli, [
        "--json", "count", "-t", "place", "--in", "Boston, US-MA",
    ])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["count"] > 100


def test_categories_in_boston_top_5():
    runner = CliRunner()
    result = runner.invoke(cli, [
        "--json", "categories", "-t", "place",
        "--in", "Boston, US-MA", "--top", "5",
    ])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert isinstance(data, list)
    assert len(data) == 5
    # Each entry has a value + positive count
    for row in data:
        assert row["count"] > 0
        assert isinstance(row["value"], str)


def test_at_nearby_place():
    # Empire State Building
    runner = CliRunner()
    result = runner.invoke(cli, [
        "at", "40.7484,-73.9857", "-t", "place", "-n", "3",
        "-f", "geojsonseq",
    ])
    assert result.exit_code == 0, result.output
    lines = [l for l in result.output.strip().split("\n") if l.startswith("{")]
    assert 1 <= len(lines) <= 3


def test_containing_known_point():
    # A point in central Boston
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "containing", "42.3601,-71.0589"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    subtypes = [d["subtype"] for d in data]
    assert "country" in subtypes
    assert "region" in subtypes or "locality" in subtypes
    # Innermost-first ordering
    levels = [d["admin_level"] for d in data]
    assert levels == sorted(levels, reverse=True)
```

- [ ] **Step 2: Run the integration tests**

Run: `uv run pytest tests/test_cli_integration.py -m integration -v`
Expected: all 5 tests pass. Allow ~1–2 minutes — the first run builds the divisions index.

- [ ] **Step 3: Commit**

```bash
git add tests/test_cli_integration.py
git commit -m "Add integration tests for the new agent-facing commands

Covers where, count, categories, at, containing against real S3 per
spec §12.2. Marked with pytestmark = pytest.mark.integration so they
opt out of the default unit-test run."
```

---

## Task 5.1: Update README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a "Quick start for agents" section near the top**

Open `README.md`. After the existing "Quick Start" block (around line 22), insert a new section before the "Usage" section:

```markdown
## Quick Start for Coding Agents

Install the Skill so an agent can discover this CLI automatically:

```bash
overturemaps install-skill
```

Self-introspect:

```bash
overturemaps --json capabilities    # list every subcommand + parameters
overturemaps --json themes          # list themes
overturemaps --json types           # list types
overturemaps --json schema -t place # fields + a sample feature
```

Resolve a place, count, then download:

```bash
overturemaps --json where "Boston, MA"
overturemaps --json count -t place --in "Boston, MA" --where categories.primary=restaurant
overturemaps places --in "Boston, MA" --category restaurant -f geojsonseq -o out.jsonl
```
```

- [ ] **Step 2: Add a "New in 1.1.0" subsection in the Usage section**

After the existing `#### download` block, add a new subsection covering the new commands. Keep entries terse — full reference is in `--help` and `--json capabilities`:

```markdown
#### `where TEXT`

Resolve a place name to a division feature. Returns bbox, subtype, population, hierarchy.

#### `count`, `sample`

Cheap previews of any query (`-t TYPE --in PLACE --where FILTER`).

#### `themes`, `types`, `schema`, `categories`, `capabilities`

Introspect what's queryable. `--json` produces machine-readable output.

#### `places`, `buildings`, `roads`

Intent verbs that wrap `download` with a familiar shape:
```bash
overturemaps places --in "Brooklyn" --category hospital -f geojsonseq -o out.jsonl
overturemaps buildings --in "Manhattan" --where height>100 -f geojsonseq -o out.jsonl
overturemaps roads --in "Texas, US" --class motorway -f geojsonseq -o out.jsonl
```

#### `at LAT,LON`, `containing LAT,LON`

Point queries. `at` is nearest-neighbor; `containing` lists admin divisions
that contain the point.

#### `install-skill`

Install the agent-discoverable Skill for Claude Code and/or write an
`AGENTS.md` section.

#### `cache info|clear|build`

Manage the on-disk divisions index (used by `--in` and `containing`).
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Document the new agent-friendly commands in README"
```

---

## Task 5.2: Version bump

- [ ] **Step 1: Edit `pyproject.toml`**

Change `version = "1.0.0"` to `version = "1.1.0"`.

- [ ] **Step 2: Run the full test suite**

Run: `uv run pytest tests/ -m "not integration" -v`
Expected: all pass.

- [ ] **Step 3: Run an integration smoke**

Run: `uv run pytest tests/ -m integration -v`
Expected: all pass.

- [ ] **Step 4: Manually exercise the new surface**

```bash
uv run overturemaps install-skill --target claude-user --yes
uv run overturemaps --json where "Boston, MA"
uv run overturemaps --json count -t place --in "Boston, MA" --where categories.primary=cafe
uv run overturemaps places --in "Boston, MA" --category cafe -f geojsonseq -o /tmp/cafes.jsonl
head -1 /tmp/cafes.jsonl | jq .properties.categories
```

Verify each step produces sensible output.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml
git commit -m "Bump version to 1.1.0"
```

🚦 **Phase 5 ship gate:** ready to tag and release per the project's standard `release` skill.

---

# Appendix: Quick reference for an executor

- **Unit tests:** `uv run pytest tests/ -m "not integration" -v`
- **Integration tests:** `uv run pytest tests/ -m integration -v`
- **Run one file:** `uv run pytest tests/<file>.py -v`
- **Run one test:** `uv run pytest tests/<file>.py::<test_name> -v`
- **Manual CLI test:** `uv run overturemaps ...`
- **Spec reference:** `docs/superpowers/specs/2026-05-11-agent-friendly-cli-design.md`

Phase 1 (Tasks 1.1 – 1.8) is the longest and highest-risk phase; pause and verify before continuing into Phase 2. The remaining phases are largely additive surface area on the foundation built in Phase 1.
