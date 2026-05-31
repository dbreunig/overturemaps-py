# Agent-Usability Eval Report

Total runs scored: **20**

## Per-question rates

| Question | Tier | Runs | Download | Unnecessary DL | Error | Completed | Avg cmds |
|---|---|---|---|---|---|---|---|
| busstops-coffee-williamsburg | 5 | 2 | 50% | 50% | 50% | 50% | 6.0 |
| coffee-brooklyn-count | 1 | 2 | 0% | 0% | 0% | 100% | 1.0 |
| containing-point | 3 | 2 | 0% | 0% | 0% | 50% | 0.0 |
| hardware-near-bikepaths-alameda | 5 | 2 | 100% | 100% | 50% | 50% | 11.5 |
| landuse-brooklyn | 4 | 2 | 0% | 0% | 50% | 100% | 3.5 |
| pois-near-point | 3 | 2 | 0% | 0% | 50% | 100% | 2.5 |
| restaurant-categories-brooklyn | 2 | 2 | 0% | 0% | 0% | 100% | 2.0 |
| tall-buildings-manhattan | 2 | 2 | 0% | 0% | 0% | 100% | 4.0 |
| water-downtown-boston | 4 | 2 | 0% | 0% | 0% | 100% | 3.0 |
| where-boston | 1 | 2 | 0% | 0% | 0% | 100% | 1.0 |

## Ranked error clusters

- **traceback** in `hardware-near-bikepaths-alameda` ×2
  - `overturemaps download -t division_area --bbox -122.37383,37.45391,-121.46908,37.90669 -f geojsonseq`
  - `overturemaps download -t division_area --bbox -122.37383,37.45391,-121.46908,37.90669 --where division_id=9695a507-d4d5-4230-91e3-604f70c9315f -f geojsonseq`
- **malformed_bbox_or_coords** in `busstops-coffee-williamsburg` ×1
  - `overturemaps --json where Williamsburg, Brooklyn`
- **bad_option** in `landuse-brooklyn` ×1
  - `overturemaps --json categories -t land_use --in Brooklyn, NY --top 20`
- **bad_option** in `pois-near-point` ×1
  - `overturemaps at 40.7128,-74.0060 -t place --radius 150 --json`

## Coverage gaps (legitimate downloads)

_None._

## Unnecessary download (agent failures)

- `busstops-coffee-williamsburg`:
  - `overturemaps download -t infrastructure --bbox -73.975,40.695,-73.928,40.724 -f geojsonseq`
  - `overturemaps download -t infrastructure --bbox -73.975,40.695,-73.928,40.724 --where subtype=transit --where class=bus_stop -f geojsonseq -o /tmp/bus_stops.jsonl`
  - `overturemaps download -t infrastructure --bbox -73.975,40.695,-73.928,40.724 --where subtype=transit --where class=bus_stop -f geojsonseq -o /tmp/bus_stops.jsonl`
- `hardware-near-bikepaths-alameda`:
  - `overturemaps download -t division_area --bbox -122.374,37.454,-121.469,37.907 --where division_id=9695a507-d4d5-4230-91e3-604f70c9315f -f geojsonseq`
  - `overturemaps download -t division_area --bbox -122.37383,37.45391,-121.46908,37.90669 -f geojsonseq`
  - `overturemaps download -t division_area --bbox -122.37383,37.45391,-121.46908,37.90669 --where division_id=9695a507-d4d5-4230-91e3-604f70c9315f -f geojsonseq`

## Proposed improvements

### 1. Stop `download -t division_area` from raising a traceback; redirect to `where --geometry`  _(target: cli)_
**Evidence:** hardware-near-bikepaths-alameda: 2 tracebacks from `download -t division_area --bbox ... -f geojsonseq` (with and without --where division_id). download_rate 1.0, error_rate 0.5. A steering tip already prints for division_area (cli.py:521-528) but the download still proceeds and crashes.

In the `download` command, when type_ == 'division_area', short-circuit before reading S3: raise a clean click.UsageError that names the exact replacement, e.g. `division_area is not downloadable this way — for a boundary polygon run: overturemaps where "<place>" --geometry`. This converts an uncaught traceback into a directed, zero-cost error and forces the agent onto the supported verb instead of retrying the failing download.

### 2. Add a `boundary` verb aliasing `where --geometry`  _(target: cli)_
**Evidence:** hardware-near-bikepaths-alameda: agent needed a county polygon to clip 'hardware near bike paths' and reached for `download -t division_area` (download_rate 1.0, avg_commands 11.5). `where --geometry` exists and is documented (skill recipe 16) but the agent never found a verb matching the noun 'boundary'/'polygon'.

Register a `boundary <place>` command that internally calls the same code path as `where <place> --geometry` (emit the division_area polygon as a GeoJSON Feature on stdout). Add 'division_area' -> 'boundary' to TYPE_TO_VERB (cli.py:310-317) so the download tip names a real verb. A noun-shaped verb is far more discoverable to an agent than a flag on an unrelated command.

### 3. Hard-redirect transit-class infrastructure downloads to `places --category`  _(target: cli)_
**Evidence:** busstops-coffee-williamsburg: agent ran `download -t infrastructure --where subtype=transit --where class=bus_stop` (unnecessary_download_rate 0.5, tier 5). The infrastructure tip (cli.py:513-520) prints but the download still runs.

In `download`, when type_ == 'infrastructure' and a --where targets class in {bus_stop, bus_station, train_station, transit, ...} or subtype=transit, raise a click.UsageError that prints the ready-to-run replacement (`overturemaps places --category bus_stop --in/--bbox ...`) instead of emitting an advisory tip and proceeding. Turning the soft nudge into a blocking redirect removes the unnecessary download entirely.

### 4. Make `categories` enumerate `class` for non-place types instead of erroring  _(target: cli)_
**Evidence:** landuse-brooklyn: `categories -t land_use --in "Brooklyn, NY" --top 20` produced a bad_option/UsageError (cli.py:940-952). The agent was doing exactly the right discovery step (what classes exist here?) but the command refuses any non-place type.

Generalize `categories`: for type_ == 'place' enumerate categories.primary (current behavior); for other types (land_use, water, segment, building) enumerate the `class` field value_counts over the same bbox/--in. This serves the agent's discovery intent and eliminates the error class rather than just rewording it.

### 5. Accept `--json` as a no-op on data subcommands so trailing `--json` doesn't error  _(target: cli)_
**Evidence:** pois-near-point: `at 40.7128,-74.0060 -t place --radius 150 --json` failed as bad_option. `--json` is a group-level flag (cli.py:446) defined only on the root group, so placing it after a data subcommand yields 'no such option: --json'.

Add a hidden, no-op `--json` flag to the data verbs (`at`, `places`, `roads`, `buildings`, `water`, `landuse`, `addresses`, `sample`) that is silently accepted (these already emit structured GeoJSON). This absorbs the extremely common agent habit of appending `--json` everywhere and prevents the whole bad_option class for data commands.

### 6. Give `where` the same parent/neighborhood fallback as `--in`  _(target: cli)_
**Evidence:** busstops-coffee-williamsburg: `where "Williamsburg, Brooklyn"` errored (count 1, completion_rate 0.5). The `where` command calls resolve() directly + _no_match_help and exits 1 (cli.py:700-707), while `_resolve_in_place` (cli.py:182-237) already retries the bare name, scopes to the parent's region, and falls back to the parent bbox.

Refactor `where` to reuse the _resolve_in_place fallback chain: on no direct match, retry the bare name, try scoping each qualifier as a parent locality, and surface the parent division (with a stderr note) instead of exiting 1. Neighborhood+borough queries like 'Williamsburg, Brooklyn' then resolve to a usable bbox rather than dead-ending.

