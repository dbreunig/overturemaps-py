# Agent-Usability Eval Report

Total runs scored: **20**

## Per-question rates

| Question | Tier | Runs | Download | Unnecessary DL | Error | Completed | Avg cmds |
|---|---|---|---|---|---|---|---|
| busstops-coffee-williamsburg | 5 | 2 | 100% | 100% | 100% | 100% | 12.5 |
| coffee-brooklyn-count | 1 | 2 | 0% | 0% | 0% | 100% | 1.0 |
| containing-point | 3 | 2 | 0% | 0% | 0% | 0% | 0.5 |
| hardware-near-bikepaths-alameda | 5 | 2 | 50% | 50% | 0% | 100% | 10.5 |
| landuse-brooklyn | 4 | 2 | 100% | 0% | 0% | 100% | 3.0 |
| pois-near-point | 3 | 2 | 0% | 0% | 0% | 100% | 3.0 |
| restaurant-categories-brooklyn | 2 | 2 | 0% | 0% | 50% | 100% | 4.5 |
| tall-buildings-manhattan | 2 | 2 | 0% | 0% | 100% | 100% | 5.0 |
| water-downtown-boston | 4 | 2 | 100% | 0% | 0% | 100% | 3.0 |
| where-boston | 1 | 2 | 0% | 0% | 0% | 100% | 1.0 |

## Ranked error clusters

- **other_error** in `busstops-coffee-williamsburg` ×2
  - `overturemaps --json where Williamsburg, Brooklyn`
  - `overturemaps --json where Williamsburg, Brooklyn`
- **bad_option** in `tall-buildings-manhattan` ×2
  - `overturemaps --json count -t building --in Manhattan --where height`
  - `overturemaps --json count -t building --in Manhattan --where height`
- **bad_option** in `restaurant-categories-brooklyn` ×1
  - `overturemaps --json categories -t place --in Brooklyn, New York --top 50`
- **other_error** in `restaurant-categories-brooklyn` ×1
  - `overturemaps --json where Brooklyn, New York`

## Coverage gaps (legitimate downloads)

- type `land_use` — wanted by: landuse-brooklyn
- type `water` — wanted by: water-downtown-boston

## Unnecessary download (agent failures)

- `busstops-coffee-williamsburg`:
  - `overturemaps download -t infrastructure --bbox -73.975,40.700,-73.930,40.730 --where subtype=transit -f geojsonseq -o /tmp/wburg_transit.jsonl`
  - `overturemaps download -t infrastructure --bbox -73.975,40.700,-73.930,40.730 --where subtype=transit --where class=bus_stop -f geojsonseq -o /tmp/wburg_busstops.jsonl`
  - `overturemaps download -t infrastructure --bbox -73.975,40.695,-73.935,40.725 --where subtype=transit --where class=bus_stop -f geojsonseq -o /tmp/bus_stops.jsonl`
- `hardware-near-bikepaths-alameda`:
  - `overturemaps download -t segment --in Alameda County, CA --where class=cycleway -f geojsonseq -o /tmp/bike_paths_alameda.jsonl`
  - `overturemaps download -t division_area --in Alameda County, CA --where subtype=county -f geojsonseq`

## Proposed improvements

### 1. Add a water convenience verb  _(target: cli)_
**Evidence:** coverage_gaps.water (water-downtown-boston): download_rate 1.0, unnecessary_download_rate 0.0 — a legitimate `download -t water` because no verb covers the base/water theme.

Add a `water` command in overturemaps/cli.py modeled exactly on the existing buildings command (lines 986-1025): same --in/--bbox/--where/-f/-o/-r options, calling safe_reader with type='water'. Add a --class shortcut (ocean, lake, river, ...) mirroring the roads --class pattern (lines 1033/1058-1059) so `overturemaps water --in "Boston, MA" --class river` replaces `download -t water`.

### 2. Add a landuse convenience verb  _(target: cli)_
**Evidence:** coverage_gaps.land_use (landuse-brooklyn): download_rate 1.0, unnecessary_download_rate 0.0 — a legitimate `download -t land_use` because no verb covers the base/land_use theme.

Add a `landuse` command in overturemaps/cli.py cloning the buildings command body but with type_='land_use', plus a --class shortcut (commercial, residential, recreation, agriculture, ...) like roads --class. Then `overturemaps landuse --in "Brooklyn" --class residential` fully replaces `download -t land_use`.

### 3. Emit a steering hint from download when a verb covers the requested type  _(target: hint)_
**Evidence:** unnecessary_download in busstops-coffee-williamsburg (`download -t infrastructure`) and hardware-near-bikepaths-alameda (`download -t segment`). Agents reach for download even when place/segment/building verbs exist.

In the download command (overturemaps/cli.py:395), after resolving type_, look it up in a TYPE_TO_VERB map (`{'place':'places','segment':'roads','building':'buildings','address':'addresses','water':'water','land_use':'landuse'}`) and, when present, print a one-line stderr nudge before streaming: e.g. `[overturemaps] Tip: 'overturemaps roads --in ... --class cycleway' covers -t segment with friendlier flags and the same output.` Non-fatal, so existing pipelines still work.

### 4. Quote --where expressions containing < or > in every recipe  _(target: skill)_
**Evidence:** bad_option in tall-buildings-manhattan (both runs): `count -t building --in Manhattan --where height` — the trailing `>150` was eaten by the shell as a redirection because skill recipe #5 shows `--where height>150` UNQUOTED.

In overturemaps/data/skill.md, quote every filter that contains an operator: recipe #5 -> `--where 'height>150'`, the filter-syntax block (lines 139-143) -> `--where 'height>100'`, and add an Anti-pattern line: 'Always single-quote --where expressions with < or > so the shell does not treat them as redirection (writes a file named after the number and truncates the filter to just the key).' Mirror the same quoting in README.md recipes.

### 5. Detect operator-less --where and explain the shell-redirection trap  _(target: cli)_
**Evidence:** bad_option in tall-buildings-manhattan: `--where height` reached the CLI with no operator/value because the shell consumed `>150`. parse_where_expr currently just errors generically.

In overturemaps/filters.py parse_where_expr, when an expression has no recognized operator (=,!=,<,<=,>,>=,in), raise a ValueError with: "Filter 'height' has no operator. Use K OP V, e.g. --where 'height>150'. If you typed an unquoted > or <, your shell redirected it to a file — wrap the whole expression in single quotes." This surfaces via the existing click.UsageError wrappers (_safe_reader/_safe_count, cli.py:42-55).

### 6. Make where/--in no-match errors actionable for neighborhoods and city/state qualifiers  _(target: cli)_
**Evidence:** other_error in busstops-coffee-williamsburg (where "Williamsburg, Brooklyn") and restaurant-categories-brooklyn (where "Brooklyn, New York"). The geocoder (geocoding.py:144-154) rejects neighborhood names and city/full-state-name qualifiers, and the no_match path (cli.py:570-576) gives no recovery path.

In where and in _resolve_in_place (cli.py:152-174/566-576), on zero matches retry once with qualifiers dropped to the country level (re-resolve the bare name) and, if that yields hits, name them in the error: "No division for 'Williamsburg, Brooklyn'. Williamsburg is a neighborhood not in the divisions index; nearest resolvable parent: 'Brooklyn' (locality). Use that, or containing LAT,LON, or --bbox." Include this same suggestion in the JSON no_match envelope so agents can act on it programmatically.

### 7. Accept full US state names as --in/where qualifiers  _(target: cli)_
**Evidence:** other_error/bad_option in restaurant-categories-brooklyn: where "Brooklyn, New York" and categories ... --in "Brooklyn, New York" fail because _normalize_qualifier (geocoding.py:97-100) only aliases country names; 'New York' is not matched against region 'US-NY'.

Add a US-state (and optionally global subdivision) name->code map in overturemaps/geocoding.py and extend _normalize_qualifier so 'New York'->'NY', 'California'->'CA', etc., then keep the existing region_suffix_match on '-NY'. This makes where "Brooklyn, New York" resolve the same as "Brooklyn, NY" / "Brooklyn, US-NY".

### 8. Document that transit stops (bus stops) live in place, not infrastructure  _(target: skill)_
**Evidence:** unnecessary_download in busstops-coffee-williamsburg: all runs did `download -t infrastructure --where subtype=transit --where class=bus_stop`, when the intended path is `places --category bus_stop`.

In overturemaps/data/skill.md schema cheatsheet place row, note that transit POIs are places: add bus_stop, bus_station, train_station to the categories.primary examples, and add a Recipe: `overturemaps places --in "Williamsburg, Brooklyn" --category bus_stop`. Add an Anti-pattern: 'Bus stops, stations, and most named transit points are place features (categories.primary=bus_stop) — use places --category, not download -t infrastructure.'

### 9. Show that roads covers bike paths/footways via --class cycleway  _(target: skill)_
**Evidence:** unnecessary_download in hardware-near-bikepaths-alameda: a run used `download -t segment --where class=cycleway` instead of the roads verb, which already supports --class and maps to the segment type (cli.py:1042-1072).

In overturemaps/data/skill.md, broaden recipe #6 and the segment cheatsheet row to state roads returns ALL transportation segments, not just cars, and add a bike-path recipe: `overturemaps roads --in "Alameda County, CA" --class cycleway -f geojsonseq -o bikepaths.jsonl`. List footway/path/cycleway among the --class values so agents stop falling back to `download -t segment`.

### 10. Provide division polygon geometry without download -t division_area  _(target: cli)_
**Evidence:** unnecessary_download in hardware-near-bikepaths-alameda: `download -t division_area --in "Alameda County" --where subtype=county` — the agent needed the county polygon for a spatial join, but where only returns a bbox (cli.py:586-600) and no verb emits division geometry.

Add a --geometry/--geojson flag to the where command (cli.py:561) that, when set, fetches and emits the division_area polygon as a GeoJSON Feature (reuse the _prefetch_polygons / division_area S3 path already in cli.py:1289-1330). Document it as the supported way to get a boundary for clipping/joins, e.g. `overturemaps where "Alameda County, CA" --geometry > county.geojson`, removing the need for `download -t division_area`.
