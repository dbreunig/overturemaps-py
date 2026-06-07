[![PyPi](https://img.shields.io/pypi/v/botmap.svg)](https://pypi.python.org/pypi/botmap)

# overturemaps-py

Official Python command-line tool of the [Overture Maps Foundation](https://overturemaps.org)

Overture Maps provides free and open geospatial map data, from many different sources and normalized to a
[common schema](https://github.com/OvertureMaps/schema). This tool helps to download Overture data
within a region of interest and converts it to a few different file formats. For more information about accessing
Overture Maps data, see our official documentation site <https://docs.overturemaps.org>.

Note: This repository and project are experimental. Things are likely change including the user interface
until a stable release, but we will keep the documentation here up-to-date.

## Quick Start

Download the building footprints for the specific bounding box as GeoJSON and save to a file named "boston.geojson"

```bash
botmap download --bbox=-71.068,42.353,-71.058,42.363 -f geojson --type=building -o boston.geojson
```

## Quick Start for Coding Agents

Install the Skill so an agent can discover this CLI automatically:

```bash
botmap install-skill
```

Self-introspect:

```bash
botmap --json capabilities    # list every subcommand + parameters
botmap --json themes          # list themes
botmap --json types           # list types
botmap --json schema -t place # fields + a sample feature
```

Resolve a place, count, then download:

```bash
botmap --json where "Boston, MA"
botmap --json count -t place --in "Boston, MA" --where categories.primary=restaurant
botmap places --in "Boston, MA" --category restaurant -f geojsonseq -o out.jsonl
```

## Examples

### Finding POIs

```bash
# All hospitals in Brooklyn
botmap places --in "Brooklyn" --category hospital -f geojsonseq -o hospitals.jsonl

# Coffee shops in Brooklyn, with high source confidence
botmap places --in "Brooklyn" --category coffee_shop --where 'confidence>0.8' \
  -f geojsonseq -o brooklyn_coffee.jsonl

# Hotels in Berlin (using a country code qualifier)
botmap places --in "Berlin, DE" --category hotel -f geojsonseq -o berlin_hotels.jsonl

# Pharmacies near the Empire State Building (~250m)
botmap at 40.7484,-73.9857 -t place --category pharmacy --radius 250 -n 20
```

### Discovering before downloading

```bash
# What categories exist in Brooklyn? (cheap; reads only places in the bbox)
botmap categories -t place --in "Brooklyn" --top 30

# How many buildings in Manhattan are at least 100m tall? Decide before downloading.
botmap count -t building --in "Manhattan" --where 'height>=100'

# Peek at five matching features before committing to the full pull
botmap sample -t building --in "Manhattan" --where 'height>=100' -n 5
```

### Buildings with attributes

```bash
# Tall buildings in Manhattan, as GeoParquet for analytics
botmap buildings --in "Manhattan" --where 'height>150' -f geoparquet -o tall.parquet

# Skyscrapers (≥40 floors) in Chicago
botmap buildings --in "Chicago, IL" --where 'num_floors>=40' -f geojsonseq -o skyscrapers.jsonl

# Buildings of a specific subtype
botmap buildings --in "Boston, MA" --where subtype=education -f geojsonseq -o schools.jsonl
```

### Roads and transportation

```bash
# Highways in Texas
botmap roads --in "Texas, USA" --class motorway -f geojsonseq -o tx_highways.jsonl

# Main roads (primary or secondary) in Berlin
botmap roads --in "Berlin, DE" --where "class in [primary,secondary]" \
  -f geojsonseq -o berlin_main.jsonl

# Footways and cycleways in central Amsterdam
botmap roads --in "Amsterdam, NL" --where "class in [footway,cycleway]" \
  -f geojsonseq -o amsterdam_paths.jsonl

# `roads` covers every transportation segment — use --class for bike paths too
botmap roads --in "Alameda County, CA" --class cycleway \
  -f geojsonseq -o bikepaths.jsonl
```

### Water and land use

```bash
# Lakes near Minneapolis
botmap water --in "Minneapolis, MN" --class lake -f geojsonseq -o lakes.jsonl

# Residential land-use polygons in Brooklyn
botmap landuse --in "Brooklyn, NY" --class residential \
  -f geojsonseq -o residential.jsonl
```

Both `water` and `landuse` mirror `roads`: pass `--class` (e.g. `ocean`,
`lake`, `river` for water; `commercial`, `residential`, `recreation`,
`agriculture` for land use) or any `--where` filter.

### Boundary polygons

```bash
# Get a division's polygon as a GeoJSON Feature (for clipping / spatial joins)
botmap boundary "Alameda County, CA" > county.geojson

# Longer form: where --geometry does the same thing
botmap where "Alameda County, CA" --geometry > county.geojson
```

`boundary` is the dedicated verb for fetching a division's polygon. It accepts
any place name that `where` resolves, including neighborhood+city forms like
`"Brooklyn, NY"`. `where --geometry` (alias `--geojson`) is an equivalent
long-form. Using `download -t division_area` will now error with a redirect.

### Address lookups

```bash
# Find a specific address (case-insensitive substring on street;
# --number / --postcode are exact). --in or --bbox is required.
botmap addresses --in "Alameda, US-CA" \
  --street Fountain --number 1208

# All "Main St" addresses in a city
botmap addresses --in "Brookline, MA" --street "Main St"

# All addresses inside a small bbox over Beacon Hill
botmap addresses --bbox=-71.075,42.355,-71.060,42.365 \
  -f geojsonseq -o beacon_hill_addresses.jsonl

# Address density in a neighborhood
botmap count -t address --in "Brookline, MA"
```

The `addresses` command requires `--in` or `--bbox` so queries stay
bounded — the global address dataset is too large to scan unfiltered.
`--street` is a case-insensitive substring match (so `Fountain` will
match `Fountain St`, `Fountain Avenue`, and `E Fountain Blvd`).
Overture's address coverage is uneven; if a known address returns no
rows, the data simply isn't there for that area yet.

### Point queries

```bash
# What's at a given lat/lon (defaults to nearest POIs)
botmap at 51.5074,-0.1278 -n 5

# Which admin divisions contain this point? (innermost-first)
botmap containing 35.6762,139.6503
```

### Composing commands

`--json` makes any metadata command pipeable. Use this for ad-hoc workflows or
when scripting against the CLI.

```bash
# Resolve a bbox, then download with it
BBOX=$(botmap --json where "Berlin, DE" | jq -r '.bbox | join(",")')
botmap download -t place --bbox "$BBOX" \
  --where categories.primary=hotel \
  -f geojsonseq -o berlin_hotels.jsonl

# Top-3 categories in a place, then dump features for each
for cat in $(botmap --json categories -t place --in "Brooklyn" --top 3 | jq -r '.[].value'); do
  botmap places --in "Brooklyn" --category "$cat" \
    -f geojsonseq -o "brooklyn_${cat}.jsonl"
done

# Bbox of a country, then count of all roads
COUNT=$(botmap --json count -t segment --in "Iceland" | jq '.count')
echo "Iceland has $COUNT road segments"
```

### Multi-step agent workflow

A typical sequence an agent runs when given a layperson question like
*"how many coffee shops are in Brooklyn?"*:

```bash
# 1. Confirm the place resolves
botmap --json where "Brooklyn"
# > {"name": "Brooklyn", "subtype": "locality", "region": "US-NY", "population": 2736074, ...}

# 2. Discover the right category name
botmap --json categories -t place --in "Brooklyn" --top 50 | jq -r '.[].value' | grep -i coffee
# > coffee_shop

# 3. Count
botmap --json count -t place --in "Brooklyn" --where categories.primary=coffee_shop
# > {"count": 412, ...}

# 4. Download if needed
botmap places --in "Brooklyn" --category coffee_shop \
  -f geojsonseq -o brooklyn_coffee.jsonl
```

## Usage

#### `download`

Download Overture Maps data with an optional bounding box into the specified file format.
When specifying a bounding box, only the minimum data is transferred. The result is streamed out and
can handle arbitrarily large bounding boxes.

Command-line options:

- `--bbox` (optional): west, south, east, north longitude and latitude coordinates. When omitted the
  entire dataset for the specified type will be downloaded
- `-f` (required: one of "geojson", "geojsonseq", "geoparquet"): output format
- `--output`/`-o` (optional): Location of output file. When omitted output will be written to stdout.
- `--type`/`-t` (required): The Overture map data type to be downloaded. Examples of types are `building`
  for building footprints, `place` for POI places data, etc. Run `botmap download --help` for the
  complete list of allowed types
- `--connect_timeout` (optional): Socket connection timeout, in seconds. If omitted, the AWS SDK default value is used (typically 1 second).
- `--request_timeout` (optional): Socket read timeouts on Windows and macOS, in seconds. If omitted, the AWS SDK default value is used (typically 3 seconds). This option is ignored on non-Windows, non-macOS systems.
- `--stac/--no-stac` (optional): By default, the reader uses Overture's [STAC catalog](https://stac.overturemaps.org/) to speed up queries to the latest release. If the `--no-stac` flag is present, the CLI will use the S3 path for the latest release directly.

This downloads data directly from Overture's S3 bucket without interacting with any other servers.
By including bounding box extents on each row in the Overture distribution, the underlying Parquet
readers use the Parquet summary statistics to download the minimum amount of data
necessary to extract data from the desired region.

To help find bounding boxes of interest, we like this [bounding box tool](https://boundingbox.klokantech.com/)
from [Klokantech](https://www.klokantech.com/). Choose the CSV format and copy the value directly into
the `--bbox` field here.

#### `where TEXT`

Resolve a place name to a division feature. Returns the matched division's id,
subtype, country/region, bbox, population, and parent. `--json` emits a
candidates array so an ambiguous query can be re-narrowed.

Qualifier syntax: `"Place, ST"`, `"Place, US-ST"`, `"Place, CC"`,
`"Place, CCC"`, or `"Place, Country Name"` — e.g. all of these resolve to
Boston, US-MA: `"Boston, MA"`, `"Boston, US-MA"`, `"Boston, US"`,
`"Boston, USA"`, `"Boston, United States"`.

```bash
botmap where "Boston, MA"
botmap where "Alameda, CA" --all              # list every candidate
botmap --json where "Walnut Creek, CA, USA" | jq '.bbox'
botmap --json where "Cambridge" | jq '.candidates | length'   # how many Cambridges?
```

Best match is picked by:
1. presence of population data (real places people search for outrank
   thinly-documented administrative areas),
2. higher population,
3. innermost `admin_level` as a final tiebreaker.

When more than one candidate matches, every data command (`places`,
`buildings`, `roads`, `addresses`, `count`, `sample`, …) prints a one-line
stderr warning naming the picked division and the top alternative, pointing
at `where --all` for full inspection. Do not silence stderr — that warning
is the only signal that the resolver made a judgment call.

`where` (and all data commands) support neighborhood+city names like
`"Brooklyn, NY"`: when the exact string isn't in the divisions index, the
resolver retries scoped to the parent locality's region, or falls back to
the parent's bbox with a yellow stderr note.

#### `boundary TEXT`

Emit a division's polygon as a GeoJSON Feature on stdout, for clipping or
spatial joins. Accepts the same place names as `where`.

```bash
botmap boundary "Alameda County, CA" > county.geojson
botmap boundary "Brooklyn, NY" | jq '.properties'
```

`download -t division_area` is no longer supported — `boundary` is the
replacement.

#### `count`

Row count for a query without downloading. The cheap preview that should
precede any `download`.

```bash
botmap count -t place --in "Boston, MA"
botmap --json count -t place --in "Boston, MA" --where categories.primary=restaurant
```

#### `sample`

Emit the first N features matching a query. Defaults to `geojsonseq` and N=10.

```bash
botmap sample -t building --in "Brooklyn" --where 'height>100' -n 5
botmap sample -t place --in "Brooklyn" --where categories.primary=coffee_shop -n 3
```

#### `themes`, `types`, `schema`

Introspect what's queryable.

```bash
botmap themes                       # 6 themes with one-line descriptions
botmap types --theme buildings      # 2 types in this theme
botmap --json schema -t place       # full field list + a sample feature
```

#### `categories -t place`

Enumerate `categories.primary` values (with counts) for a place-scoped region.

```bash
botmap categories -t place --in "Brooklyn" --top 20
botmap --json categories -t place --in "Manhattan" --top 50 | jq -r '.[] | "\(.count)\t\(.value)"'
```

#### `capabilities`

Emit a machine-readable manifest of all subcommands with their parameters.
Agents read this once to learn the CLI surface.

```bash
botmap --json capabilities | jq '.commands[].name'
```

#### `places`, `buildings`, `roads`, `addresses`, `water`, `landuse`

Intent verbs that wrap `download` with a familiar shape. Each accepts either
`--in "Place Name"` (resolved via the divisions index) or `--bbox xmin,ymin,xmax,ymax`.
`--category` / `--class` / `--street` desugar to common `--where` filters,
and `--where` is still available for advanced predicates. `water` and `landuse`
take `--class` just like `roads`. Running `download -t TYPE` for a type covered
by one of these verbs prints a one-line stderr tip pointing at the verb. All
data verbs accept a trailing `--json` flag silently (they already emit GeoJSON).

Transit stops (`bus_stop`, `bus_station`, `train_station`) are `place` features —
`download -t infrastructure --where class=bus_stop` will error and redirect to
`places --category bus_stop`.

```bash
# POIs by category (named place)
botmap places --in "Brooklyn" --category hospital -f geojsonseq -o hospitals.jsonl

# POIs by category (manual bbox — skip the named-place lookup)
botmap places --bbox=-122.295,37.778,-122.265,37.800 --category coffee_shop

# Buildings filtered by attribute
botmap buildings --in "Manhattan" --where 'height>150' -f geojsonseq -o tall.jsonl
botmap buildings --in "Boston, MA" --where 'num_floors>=10' --where 'height>30' -f geoparquet -o tall.parquet

# Roads by class
botmap roads --in "Texas, US" --class motorway -f geojsonseq -o tx_highways.jsonl
botmap roads --in "Berlin, DE" --where "class in [primary,secondary]" -f geojsonseq -o berlin_main.jsonl

# Addresses by street (case-insensitive substring on --street; --number / --postcode are exact)
botmap addresses --in "Alameda, US-CA" --street Fountain --number 1234
botmap addresses --in "Brookline, MA" --street "Main St"

# Water and land use by class
botmap water --in "Minneapolis, MN" --class lake -f geojsonseq -o lakes.jsonl
botmap landuse --in "Brooklyn, NY" --class residential -f geojsonseq -o zoning.jsonl
```

`places` includes a zero-result hint: when `--category X` (or
`--where categories.primary=X`) returns 0 rows AND that value isn't
present in the bbox, the CLI scans the bbox once for the live category
list and emits a stderr suggestion of up to 3 near-matches drawn from
what's actually there. So `--category ferry_terminal` in a bbox where
only `ferry_boat_company` exists yields:

```
[botmap] 0 rows. No place has categories.primary='ferry_terminal' in
this bbox. Did you mean: ferry_boat_company? Run `botmap categories
-t place --bbox …` to see the full list.
```

This means agents typically don't need to round-trip through `categories`
themselves; the hint surfaces the right value automatically.

#### `at LAT,LON`

Nearest-neighbor lookup at a point. Defaults to `-t place` and `-n 10`. The
`--radius` (meters) controls how far out to search; per-type defaults are
100 m for `place`, 50 m for `building`, 25 m for `address`. `--where`
filters apply just like the intent verbs, so this is the right command for
"X near a point."

```bash
botmap at 40.7484,-73.9857                          # POIs near the Empire State Building
botmap at 37.8270,-122.4230 -t place \
  --radius 1500 --where "categories.primary=restaurant" -n 5
botmap at 51.5074,-0.1278 -t building -n 3
```

Use `at … --where …` instead of constructing a manual bbox + `download`.
It's the dedicated proximity primitive and returns features sorted by
distance.

#### `containing LAT,LON`

Which admin divisions contain this point, innermost-first.

```bash
botmap containing 42.3601,-71.0589
botmap --json containing 35.6762,139.6503 | jq -r '.[] | "\(.subtype)\t\(.name)"'
```

#### `install-skill`

Install the agent-discoverable Skill for Claude Code and/or write an
`AGENTS.md` section so coding agents will reach for this CLI when a user's
question implies geospatial data.

```bash
botmap install-skill                              # interactive
botmap install-skill --target claude-user --yes   # scripted
botmap install-skill --target agents-md --yes     # writes ./AGENTS.md
```

#### `cache info|clear|build`

The first `--in` or `containing` call builds an on-disk divisions index under
`$XDG_CACHE_HOME/botmap/` (default `~/.cache/botmap/`). The index
is keyed by Overture release and rebuilds automatically when the latest
release changes; these commands let you inspect or force the lifecycle.

```bash
botmap cache info                # path, current release, up-to-date status
botmap cache build               # force a rebuild against the latest release
botmap cache clear               # remove all cached index files
```

#### `gers [UUID]`

Look up an ID in the GERS Registry. If the feature is present in the latest release, it will download the feature and write it out in the specified format.

Command-line options:

- `-f` ("geojson", "geojsonseq", "geoparquet"): output format, defaults to geojsonseq for a single feature on one line.
- `--output`/`-o` (optional): Location of output file. When omitted output will be written to stdout.
- `--connect_timeout` (optional): Socket connection timeout, in seconds. If omitted, the AWS SDK default value is used (typically 1 second).
- `--request_timeout` (optional): Socket read timeouts on Windows and macOS, in seconds. If omitted, the AWS SDK default value is used (typically 3 seconds). This option is ignored on non-Windows, non-macOS systems.

## Python API

`botmap` is also a Python library. Import directly from `botmap` to query Overture data
without using the CLI.

#### Place-name geocoding

`resolve(name)` returns all matching divisions; `best_match(name)` returns the top
pick. Both read a small on-disk index that builds lazily on first call.

```python
from botmap import best_match, resolve

pick = best_match("Boston, MA")
print(pick.name, pick.region, pick.bbox)
# Boston US-MA (-71.19, 42.23, -70.80, 42.40)

# Disambiguate manually
all_bostons = resolve("Boston")
for d in all_bostons:
    print(d.name, d.region, d.population)
```

#### Counting before downloading

`count_rows` returns the row count for a query without streaming data.

```python
from botmap import best_match, count_rows

division = best_match("Brooklyn")
n = count_rows("place", bbox=division.bbox, stac=True)
print(f"Brooklyn has {n:,} places")
```

#### Arrow / pyarrow

`record_batch_reader` returns a `pyarrow.RecordBatchReader` — a streaming cursor over the data.
This is the lowest-level entry point and works with any Arrow-compatible tool.

```python
from botmap import record_batch_reader

bbox = (-71.068, 42.353, -71.058, 42.363)  # xmin, ymin, xmax, ymax
reader = record_batch_reader("building", bbox=bbox)

if reader is not None:
    table = reader.read_all()
    print(table.schema)
```

`record_batch_reader` also accepts attribute filters that push down to PyArrow.
Build them by parsing CLI-style expressions or constructing `ParsedFilter`
instances directly:

```python
from botmap import record_batch_reader, best_match
from botmap.filters import parse_where_expr

bbox = best_match("Manhattan").bbox
filters = [parse_where_expr("height>100"), parse_where_expr("num_floors>=10")]
reader = record_batch_reader("building", bbox=bbox, where_filters=filters, stac=True)
table = reader.read_all()
```

#### GeoDataFrame (geopandas)

`geodataframe` loads data directly into a `geopandas.GeoDataFrame`. Requires `geopandas` to be
installed (`pip install botmap[geopandas]` or `pip install geopandas`).

```python
from botmap import geodataframe, best_match

bbox = best_match("Boston, MA").bbox
gdf = geodataframe("building", bbox=bbox)
print(gdf.head())
```

#### Writing to a file format

Use `get_writer` and `copy` from `botmap.writers` to write data to GeoJSON, GeoJSONSeq, or
GeoParquet without the CLI:

```python
from botmap import record_batch_reader
from botmap.writers import copy, get_writer

bbox = (-71.068, 42.353, -71.058, 42.363)
reader = record_batch_reader("building", bbox=bbox)

with get_writer("geojson", "boston.geojson", schema=reader.schema) as writer:
    copy(reader, writer)
```

Supported format strings: `"geojson"`, `"geojsonseq"`, `"geoparquet"`.

## Installation

botmap is available via [Homebrew](https://brew.sh/):

```bash
brew install botmap
```

To install botmap from [PyPi](https://pypi.org/project/botmap/) using pip:

```bash
pip install botmap
```

botmap is also on [conda-forge](https://anaconda.org/conda-forge/botmap) and can be installed using conda, mamba, or pixi. To install botmap using conda:

```bash
conda install -c conda-forge botmap
```

If you have [uv](https://docs.astral.sh/uv/) installed, you can run botmap [with uvx](https://docs.astral.sh/uv/guides/tools/#running-tools) without installing it:

```bash
uvx botmap download --bbox=-71.068,42.353,-71.058,42.363 -f geojson --type=building -o boston.parquet
```

## Performance

Benchmarks using synthetic data on Apple M-series hardware:

| Output format | Geometry | Rows | Time |
|---|---|---|---|
| GeoJSON | Points | 10 000 | 31 ms |
| GeoJSON | Polygons | 10 000 | 44 ms |
| GeoParquet | — | — | network/disk bound |

To run the benchmarks locally:

```bash
uv sync --group dev
pytest benchmarks/ -v
```

## Agent-Usability Evals

The eval suite measures whether an AI agent can answer real geospatial questions
using the CLI's high-level verbs — without falling back to the low-level `download`
command and without triggering CLI errors. The goal is to drive `download` usage
toward zero for any question a convenience verb already covers.

### Running the evals

Requires the `claude` CLI on PATH and network access to Overture S3. The first run
warms the divisions index cache (one-time, ~30 seconds).

```bash
# Full batch: 10 questions × 2 repeats
uv run python -m evals.runner --model sonnet
uv run python -m evals.score
uv run python -m evals.synthesize --model opus

# Single-question smoke test (cheap sanity check)
uv run python -m evals.runner --smoke --model sonnet
uv run python -m evals.score
```

Each run produces three artifacts:

| Artifact | What it contains |
|---|---|
| `evals/runs/<id>__r<n>/transcript.jsonl` | Full Claude Code session transcript |
| `evals/runs/<id>__r<n>/shim.log` | Every `botmap` call with exit codes |
| `evals/runs/<id>__r<n>/record.json` | Scored metrics for that run |
| `evals/report.md` | Ranked failure clusters + per-question rates |
| `evals/proposals.json` | Concrete CLI/skill/docs improvement proposals |

### Question bank

Questions live in `evals/questions.yaml` and are organized into five tiers of
increasing complexity:

| Tier | What it tests |
|---|---|
| 1 | Single-verb lookups (`where`, `count`) |
| 2 | Filtered downloads with attribute predicates |
| 3 | Point-query primitives (`at`, `containing`) |
| 4 | Types with no convenience verb — `download` is the right answer |
| 5 | Multi-layer spatial joins requiring two verbs plus in-process computation |

Each question carries a `download_is_legitimate` flag. When `false`, any
`download` call is scored as an agent failure. When `true` (tier 4 questions
with no convenience verb), a `download` is a coverage-gap candidate — a signal
to add a new verb rather than a failure to penalize the agent.

### Adding questions

Add an entry to `evals/questions.yaml`:

```yaml
- id: my-new-question          # stable slug, no '__'
  question: "Natural-language prompt handed verbatim to the agent"
  tier: 2
  download_is_legitimate: false
  target_type: place
  place: "Brooklyn, US-NY"     # optional; used by the cost guard to bound S3 reads
  notes: "Ideal path: ..."
```

### Reading the output

`evals/report.md` summarises every run after `just eval` completes. The key
columns in the per-question table:

- **Download** — fraction of runs where any `download` was issued
- **Unnecessary DL** — fraction where `download` was used when a verb existed
- **Error** — fraction where at least one CLI call exited non-zero
- **Completed** — fraction where the agent produced a final answer

`evals/proposals.json` contains LLM-generated, evidence-backed suggestions
(targeting `cli`, `skill`, `docs`, or `hint`) derived from the failure clusters.

### How it works

The runner sets up an isolated working directory per run, installs the Overture
skill so the agent can discover the CLI, and puts a logging shim first on PATH.
The shim intercepts every `botmap` call, records the arguments and exit
code to `shim.log`, then forwards the call to the real binary. After all runs
complete, the scorer reads each `shim.log` and transcript to produce
`record.json`, and the synthesizer aggregates those records into the final report
and proposals.

## Development

```bash
uv sync
uv run pytest tests/
```

