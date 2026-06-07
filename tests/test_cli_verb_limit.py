"""The convenience verbs (places, buildings, roads, ...) honor -n/--limit.

Mirrors the `sample` limit test: a fake reader yields more rows than the
limit, and we assert the geojsonseq output is truncated to N. Using --bbox
avoids the divisions-index/network path; monkeypatching get_latest_release
keeps the release-validation callback offline.
"""

import pytest
from click.testing import CliRunner

from botmap.cli import cli


class _NRowReader:
    """A reader that yields one batch of N synthetic rows then stops."""

    def __init__(self, n):
        import pyarrow as pa
        self.n = n
        self.schema = pa.schema([
            ("id", pa.string()),
            ("name", pa.string()),
            ("geometry", pa.binary()),
        ])
        self._done = False

    def read_next_batch(self):
        import pyarrow as pa
        if self._done:
            raise StopIteration
        self._done = True
        # WKB POINT(0 0) — minimal valid little-endian POINT geometry
        wkb_point_00 = (
            b"\x01\x01\x00\x00\x00"
            b"\x00\x00\x00\x00\x00\x00\x00\x00"
            b"\x00\x00\x00\x00\x00\x00\x00\x00"
        )
        return pa.RecordBatch.from_pylist(
            [{"id": f"i{i}", "name": f"r{i}", "geometry": wkb_point_00}
             for i in range(self.n)],
            schema=self.schema,
        )


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
