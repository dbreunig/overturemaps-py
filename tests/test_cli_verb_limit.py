"""The convenience verbs (places, buildings, roads, ...) honor -n/--limit.

Mirrors the `sample` limit test: a fake reader yields more rows than the
limit, and we assert the geojsonseq output is truncated to N. Using --bbox
avoids the divisions-index/network path; monkeypatching get_latest_release
keeps the release-validation callback offline.
"""

import pytest
from click.testing import CliRunner

from botmap.cli import cli


# WKB POINT(0 0) — minimal valid little-endian POINT geometry
_WKB_POINT_00 = (
    b"\x01\x01\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00"
    b"\x00\x00\x00\x00\x00\x00\x00\x00"
)

_SCHEMA_NAMES = ("id", "name", "geometry")


def _schema():
    import pyarrow as pa
    return pa.schema([
        ("id", pa.string()),
        ("name", pa.string()),
        ("geometry", pa.binary()),
    ])


def _row_batch(start, count, schema):
    import pyarrow as pa
    return pa.RecordBatch.from_pylist(
        [{"id": f"i{i}", "name": f"r{i}", "geometry": _WKB_POINT_00}
         for i in range(start, start + count)],
        schema=schema,
    )


class _NRowReader:
    """A reader that yields one batch of N synthetic rows then stops."""

    def __init__(self, n):
        self.n = n
        self.schema = _schema()
        self._done = False

    def read_next_batch(self):
        if self._done:
            raise StopIteration
        self._done = True
        return _row_batch(0, self.n, self.schema)


class _MultiBatchReader:
    """Yields `nbatches` batches of `per_batch` rows — exercises the
    cross-batch slicing path in _limit_reader (single-batch readers never
    hit the `batch.num_rows > remaining` slice)."""

    def __init__(self, nbatches, per_batch):
        self.schema = _schema()
        self._remaining = nbatches
        self._per_batch = per_batch
        self._emitted = 0

    def read_next_batch(self):
        if self._remaining <= 0:
            raise StopIteration
        self._remaining -= 1
        batch = _row_batch(self._emitted, self._per_batch, self.schema)
        self._emitted += self._per_batch
        return batch


# (verb, extra args) — one representative invocation per convenience verb.
VERB_CASES = [
    ("places", []),
    ("buildings", []),
    ("roads", []),
    ("water", []),
    ("landuse", []),
    ("addresses", []),
]


@pytest.mark.parametrize("verb,extra", VERB_CASES)
def test_verb_respects_limit(monkeypatch, verb, extra):
    monkeypatch.setattr("botmap.cli.get_latest_release",
                        lambda: "2025-12-17.0")
    monkeypatch.setattr("botmap.cli.record_batch_reader",
                        lambda *a, **k: _NRowReader(50))

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, [
            verb, "--bbox", "-71.1,42.3,-71.0,42.4",
            "-n", "5", "-f", "geojsonseq", "-o", "out.jsonl",
            *extra,
        ])
        assert result.exit_code == 0, (result.output, getattr(result, "stderr", ""))
        lines = open("out.jsonl").read().strip().split("\n")
        assert len(lines) == 5  # truncated to N


@pytest.mark.parametrize("verb,extra", VERB_CASES)
def test_verb_unlimited_by_default(monkeypatch, verb, extra):
    """Without -n the verb streams every matching feature (no truncation)."""
    monkeypatch.setattr("botmap.cli.get_latest_release",
                        lambda: "2025-12-17.0")
    monkeypatch.setattr("botmap.cli.record_batch_reader",
                        lambda *a, **k: _NRowReader(50))

    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, [
            verb, "--bbox", "-71.1,42.3,-71.0,42.4",
            "-f", "geojsonseq", "-o", "out.jsonl",
            *extra,
        ])
        assert result.exit_code == 0, (result.output, getattr(result, "stderr", ""))
        lines = open("out.jsonl").read().strip().split("\n")
        assert len(lines) == 50  # all rows


def _run_places(monkeypatch, reader, n):
    """Invoke `places` with a fake reader and a limit; return output lines."""
    monkeypatch.setattr("botmap.cli.get_latest_release",
                        lambda: "2025-12-17.0")
    monkeypatch.setattr("botmap.cli.record_batch_reader",
                        lambda *a, **k: reader)
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli, [
            "places", "--bbox", "-71.1,42.3,-71.0,42.4",
            "-n", str(n), "-f", "geojsonseq", "-o", "out.jsonl",
        ])
        assert result.exit_code == 0, (result.output, getattr(result, "stderr", ""))
        content = open("out.jsonl").read().strip()
        return content.split("\n") if content else []


def test_limit_slices_across_batches(monkeypatch):
    """A limit that lands mid-stream truncates across batch boundaries:
    3 batches of 20, limit 25 -> 20 from the first batch + 5 from the second."""
    lines = _run_places(monkeypatch, _MultiBatchReader(nbatches=3, per_batch=20), 25)
    assert len(lines) == 25


def test_limit_zero_emits_nothing(monkeypatch):
    """-n 0 is honored (limit is not None) and produces an empty result."""
    lines = _run_places(monkeypatch, _NRowReader(50), 0)
    assert lines == []


def test_negative_limit_emits_nothing(monkeypatch):
    """A negative limit degrades gracefully to 0 rows rather than erroring."""
    lines = _run_places(monkeypatch, _NRowReader(50), -1)
    assert lines == []
