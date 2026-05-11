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
