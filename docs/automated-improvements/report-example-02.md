# Agent-Usability Eval Report

Total runs scored: **20**

## Per-question rates

| Question | Tier | Runs | Download | Unnecessary DL | Error | Completed | Avg cmds |
|---|---|---|---|---|---|---|---|
| busstops-coffee-williamsburg | 5 | 2 | 100% | 100% | 100% | 100% | 12.5 |
| coffee-brooklyn-count | 1 | 2 | 0% | 0% | 0% | 100% | 1.0 |
| containing-point | 3 | 2 | 0% | 0% | 0% | 100% | 1.0 |
| hardware-near-bikepaths-alameda | 5 | 2 | 50% | 50% | 0% | 50% | 11.5 |
| landuse-brooklyn | 4 | 2 | 0% | 0% | 50% | 100% | 3.5 |
| pois-near-point | 3 | 2 | 0% | 0% | 50% | 100% | 2.5 |
| restaurant-categories-brooklyn | 2 | 2 | 0% | 0% | 0% | 100% | 2.0 |
| tall-buildings-manhattan | 2 | 2 | 0% | 0% | 0% | 100% | 2.0 |
| water-downtown-boston | 4 | 2 | 0% | 0% | 0% | 50% | 4.0 |
| where-boston | 1 | 2 | 0% | 0% | 0% | 100% | 1.0 |

## Ranked error clusters

- **other_error** in `busstops-coffee-williamsburg` ×2
  - `overturemaps --json where Williamsburg, Brooklyn`
  - `overturemaps --json where Williamsburg, Brooklyn`
- **bad_option** in `landuse-brooklyn` ×1
  - `overturemaps --json categories -t land_use --in Brooklyn, NY --top 15`
- **malformed_bbox_or_coords** in `pois-near-point` ×1
  - `overturemaps --json at 40.7128,-74.0060 -t place -r 150`

## Coverage gaps (legitimate downloads)

_None._

## Unnecessary download (agent failures)

- `busstops-coffee-williamsburg`:
  - `overturemaps download -t infrastructure --bbox -73.975,40.700,-73.930,40.730 --where subtype=transit -f geojsonseq -o /tmp/wburg_transit.jsonl`
  - `overturemaps download -t infrastructure --bbox -73.975,40.700,-73.930,40.730 --where subtype=transit --where class=bus_stop -f geojsonseq -o /tmp/wburg_busstops.jsonl`
  - `overturemaps download -t infrastructure --bbox -73.975,40.695,-73.935,40.725 --where subtype=transit --where class=bus_stop -f geojsonseq -o /tmp/bus_stops.jsonl`
- `hardware-near-bikepaths-alameda`:
  - `overturemaps download -t segment --in Alameda County, CA --where class=cycleway -f geojsonseq -o /tmp/bike_paths_alameda.jsonl`
  - `overturemaps download -t division_area --in Alameda County, CA --where subtype=county -f geojsonseq`

## Proposed improvements

### 1. Fix skill recipe #15 — its own bus-stop example uses a neighborhood that never resolves  _(target: skill)_
**Evidence:** busstops-coffee-williamsburg: error_rate 1.0 and unnecessary_download_rate 1.0 across both runs. The failing command (other_error cluster, count 2) is `--json where "Williamsburg, Brooklyn"`. Skill recipe #15 literally instructs `places --in "Williamsburg, Brooklyn" --category bus_stop`, but Williamsburg is a neighborhood absent from Overture's divisions index, so --in fails and the agent fell back to `download -t infrastructure --where subtype=transit --where class=bus_stop`. The skill's own anti-pattern section warns neighborhoods don't resolve, contradicting its example.

Rewrite recipe #15 in overturemaps/data/skill.md so it does not pass a neighborhood to --in. Show the resolvable two-step: `BBOX=$(overturemaps --json where "Brooklyn, NY" | jq -r '.bbox|join(",")')` then `overturemaps places --bbox "$BBOX" --category bus_stop`. Add an inline note: 'Neighborhoods (Williamsburg, SoHo, downtown X) are not in the divisions index — resolve the parent locality's bbox first, never pass the neighborhood to --in.'

### 2. Add a download steering hint for -t infrastructure pointing transit asks to `places`  _(target: hint)_
**Evidence:** busstops-coffee-williamsburg: all unnecessary downloads were `download -t infrastructure --where subtype=transit --where class=bus_stop`. TYPE_TO_VERB (cli.py:277-284) covers place/segment/building/address/water/land_use but NOT infrastructure, so the steering hint at cli.py:445-452 never fires for `-t infrastructure` — the agent gets zero nudge.

In download() (cli.py), add a special-case branch when type_ == 'infrastructure': emit `[overturemaps] Tip: transit stops (bus_stop, bus_station, train_station) are `place` features — use `overturemaps places --category bus_stop`. For non-transit infrastructure, download is the right tool.` This catches the exact subtype=transit/class=bus_stop pattern that drove every busstops download.

### 3. Add a download steering hint for -t division_area pointing to `where … --geometry`  _(target: hint)_
**Evidence:** hardware-near-bikepaths-alameda: unnecessary download `download -t division_area --in "Alameda County, CA" --where subtype=county` (fetching a boundary for the spatial join). division_area is not in TYPE_TO_VERB (cli.py:277), so no hint fires, even though `where … --geometry` is the supported boundary path (recipe #16 / anti-pattern at skill.md:217).

In download() add a special-case for type_ == 'division_area': emit `[overturemaps] Tip: to get a boundary polygon use `overturemaps where "<place>" --geometry` (emits a GeoJSON Feature for clipping/joins) instead of `download -t division_area`.` Reuse the --in value verbatim in the suggested command when present.

### 4. Make the download steering hint print a ready-to-run verb command, not just '--help'  _(target: hint)_
**Evidence:** hardware-near-bikepaths-alameda: `download -t segment --in "Alameda County, CA" --where class=cycleway` was used in 50% of runs even though segment→roads IS in TYPE_TO_VERB and a hint fires. The current hint (cli.py:447-452) only says 'See `overturemaps roads --help`', which the agent ignored on stderr rather than re-deriving the equivalent invocation.

Rewrite the hint to translate the user's actual flags into the verb form and print it as a copy-pasteable command, e.g. `Try instead: overturemaps roads --in "Alameda County, CA" --class cycleway -f geojsonseq`. Map `--where class=VAL`→`--class`, `--where categories.primary=VAL`→`--category`, and carry through --in/--bbox/-f/-o. A concrete equivalent command is far more likely to be acted on than a pointer to --help.

### 5. Let `categories` accept land_use/water/segment (or fail with a pointer) instead of an invalid-choice error  _(target: cli)_
**Evidence:** landuse-brooklyn: error_rate 0.5, bad_option cluster — `categories -t land_use --in "Brooklyn, NY" --top 15`. The categories command's -t is `click.Choice(["place"])` (cli.py:852-854), so land_use is rejected with a raw Click invalid-choice error. The agent was trying to discover land_use `class` values to filter on.

Extend categories() in cli.py to accept land_use/water/segment and enumerate the relevant classifying field (`class`) rather than `categories.primary`, branching on type_. If keeping it place-only, replace the Choice failure with a click.UsageError: 'categories enumerates place categories. For land_use/water/segment the field is `class` — run `overturemaps --json schema -t land_use` or filter directly with `overturemaps landuse --class <value>`.' Either change removes the error and the resulting download fallback.

### 6. Give `--radius` the `-r` short flag on `at` (release collision drives a fake bad-coords error)  _(target: cli)_
**Evidence:** pois-near-point: error_rate 0.5 — `at 40.7128,-74.0060 -t place -r 150`. The agent used `-r 150` intending a 150 m radius, but on `at` (cli.py:1308) `-r` is bound to --release, so '150' is validated as a release and fails; --radius (cli.py:1299) has no short flag, so `-r` is the natural-but-wrong reach on a proximity command.

On the `at` command only, bind `-r`/`--radius` to the radius option and make release long-only (`--release`, drop its `-r` alias). Radius is the core parameter for a nearest-neighbor command; release is rarely set there. This matches agent intuition and eliminates the error class. As a backstop, have validate_release emit a targeted hint when the value is a bare small integer ('looks like a radius — did you mean --radius?').

### 7. Make verbs fall back to the parent locality on a 'Neighborhood, Parent' query instead of hard-failing  _(target: cli)_
**Evidence:** busstops-coffee-williamsburg (where "Williamsburg, Brooklyn" exits non-zero, agent improvises a download) and water-downtown-boston (completion_rate 0.5, no download/error logged — a 'downtown Boston' neighborhood that likely didn't resolve and the run stalled). `_no_match_help` (cli.py:152-179) already retries the bare name and suggests a parent/bbox, but the agent still didn't recover and either errored or gave up.

When `--in`/`where` gets a 'Name, Qualifier' query that doesn't resolve but the parent qualifier (or bare name) does, auto-resolve to that parent's bbox, proceed, and warn on stderr (`[overturemaps] 'Williamsburg, Brooklyn' not in divisions; using parent 'Brooklyn' bbox`). For the verbs (places/at/water/etc.) this means a neighborhood query degrades to a valid bounded query instead of exiting non-zero — so the agent neither errors nor reaches for download.

