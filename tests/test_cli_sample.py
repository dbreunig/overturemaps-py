"""Tests for the `sample` command."""

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


def test_sample_respects_n_limit(monkeypatch, tmp_path):
    monkeypatch.setattr("botmap.cli.get_latest_release",
                        lambda: "2025-12-17.0")

    def fake_reader(type_, *a, **k):
        return _NRowReader(50)

    monkeypatch.setattr("botmap.cli.record_batch_reader", fake_reader)

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
