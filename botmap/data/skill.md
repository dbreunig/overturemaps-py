---
name: botmap
description: Use when a user's question or task involves places, buildings, roads, addresses, neighborhoods, or other geographic features — even if they don't use geo terms. Examples: "how many coffee shops in Brooklyn", "find hospitals in Manhattan", "show tall buildings in Chicago", "what are the main roads in Berlin", "what's the bounding box of Boston".
---

# Overture Maps CLI

The `botmap` CLI streams open geospatial data (buildings, places, roads,
addresses, administrative divisions) directly from Overture's public S3 bucket.
It is the right tool whenever the user's question implies a place, an area,
or a kind of feature on the map — even when they don't use geo terminology.

## When to reach for this CLI

Triggering phrases (illustrative, not exhaustive):

- *"How many coffee shops are in Brooklyn?"* → `where` + `count -t place --in Brooklyn --where categories.primary=coffee_shop`
- *"Find hospitals in Manhattan"* → `places --in "Manhattan" --category hospital`
- *"Show buildings taller than 100m in Chicago"* → `buildings --in "Chicago, IL" --where 'height>100'`
- *"What highways run through Texas?"* → `roads --in "Texas, USA" --class motorway`
- *"Where are the bike paths in Alameda County?"* → `roads --in "Alameda County, CA" --class cycleway`
- *"Find the lakes near Minneapolis"* → `water --in "Minneapolis, MN" --class lake`
- *"Map residential zoning in Brooklyn"* → `landuse --in "Brooklyn, NY" --class residential`
- *"What admin area is this address in?"* → `containing LAT,LON`
- *"What's at 40.7128, -74.0060?"* → `at 40.7128,-74.0060`
- *"What's the bounding box of Boston?"* → `where "Boston, MA" --json`

**Note on place names:** Overture's divisions index stores `name_primary` in
the local script (e.g. Tokyo is `東京都`, not `Tokyo`). For non-Latin cities,
prefer the local-script form, use `containing LAT,LON` with coordinates, or
fall back to `--bbox`. The `where` command's qualifier syntax is
`"Place, ST"`, `"Place, US-ST"`, `"Place, Full State Name"`, `"Place, CC"`
(alpha-2), `"Place, CCC"` (alpha-3), or `"Place, Country Name"` — e.g. all of
these resolve Boston: `"Boston, MA"`, `"Boston, Massachusetts"`,
`"Boston, US-MA"`, `"Boston, US"`, `"Boston, USA"`, `"Boston, United States"`.

Negative examples (do NOT reach for botmap):

- "Draw a map of the org chart" — not geographic
- "What's the time in Boston?" — geography incidental, no spatial query needed
- "Write a regex for postal codes" — schema knowledge unrelated to map data

## Self-discovery

If you forget the surface, run `botmap --json capabilities`. It returns
a manifest of every subcommand with its parameters.

## Troubleshooting flow (work the problem in this order)

Most place-based questions resolve with this sequence — don't jump straight
to `download`:

1. **Resolve the place.** `botmap --json where "Place, ST"`. Check the
   `name`, `country`, and `region` in the output match what you meant — a
   bare 2-letter qualifier can be ambiguous (e.g. "Santa Barbara, CA"). If it
   resolved to the wrong country/region, re-run with an explicit qualifier
   like `"Santa Barbara, US-CA"`. The `--json` output also carries a
   `candidates` array; `where … --all` lists every match.
2. **Place not in the divisions index?** `where` falls back to a parent and
   prints a yellow `[botmap]` warning on **stderr** naming what it used.
   Read that warning. If the fallback is wrong or too coarse (e.g. a
   neighborhood resolving to a whole country), skip `--in` and pass an
   approximate `--bbox xmin,ymin,xmax,ymax` instead.
3. **Count before pulling.** `botmap --json count -t TYPE --in "…"`.
4. **Too many results?** Add `--where` / `--category` / `--class` filters, or
   narrow the area with a tighter `--bbox`. Too few (or zero)? Widen the
   `--bbox`, drop a filter, or check `categories`/`schema` for the right value.
5. **Preview, then pull.** `sample -n 5` (or any verb with `-n`) to confirm
   shape, then run the verb to get the full set.

## Limiting and proximity

- **`-n` / `--limit` works on every data verb** — `sample`, `at`, and all the
  convenience verbs (`places`, `buildings`, `roads`, `water`, `landuse`,
  `addresses`). `botmap places --in "…" -n 20` emits at most 20 features.
  Without `-n`, verbs stream **all** matches (pipe or `-o` them).
- **"Near a point" → use `at`, not a verb.** `botmap at LAT,LON -t place -n 10`
  returns the N features actually *closest* to the point, sorted by distance.
  The `--in` / `--bbox` verbs filter by bounding box, so "near" there means
  "inside the box" — results are **not** distance-ordered.

## Recipes

### 1. Resolve a place name to a bbox
```bash
botmap --json where "Boston, MA"
# {"name":"Boston","bbox":[-71.19,42.23,-70.99,42.40], ...}
```

### 2. Count before downloading (always check first)
```bash
botmap --json count -t building --in "Manhattan"
# {"count": 50000+, ...}
# Add filters to narrow if the count is too large.
```

### 3. Sample to confirm shape before committing
```bash
botmap sample -t place --in "Boston, MA" --where categories.primary=restaurant -n 5
```

### 4. POIs by category
```bash
botmap places --in "Brooklyn" --category cafe \
  -f geojsonseq -o cafes.jsonl
```

### 5. Tall buildings
```bash
# Single-quote any --where with < or > so the shell doesn't treat it as a
# redirection (see Anti-patterns).
botmap buildings --in "Manhattan" --where 'height>150' \
  -f geojsonseq -o tall.jsonl
```

### 6. Transportation segments (cars, bikes, foot)
```bash
# `roads` returns ALL transportation segments, not just car roads. Use
# --class to pick motorway / primary / residential / footway / path / cycleway.
botmap roads --in "Texas, US" --class motorway \
  -f geojsonseq -o tx_highways.jsonl

# Bike paths / cycleways:
botmap roads --in "Alameda County, CA" --class cycleway \
  -f geojsonseq -o bikepaths.jsonl
```

### 7. What's near a point
```bash
botmap at 40.7484,-73.9857 -t place -n 10
```

### 8. Which admin areas contain a point
```bash
botmap --json containing 40.7484,-73.9857
# [{"name":"New York","subtype":"locality",...}, {"name":"New York","subtype":"region",...}, ...]
```

### 9. Discover what categories exist in a place
```bash
botmap --json categories -t place --in "Brooklyn" --top 20
```

### 10. Discover what's queryable on a type
```bash
botmap --json schema -t building
# Lists every field name and a sample feature.
```

### 11. Compose where + download
```bash
BBOX=$(botmap --json where "Berlin" | jq -r '.bbox | join(",")')
botmap download -t place --bbox "$BBOX" \
  --where categories.primary=hotel \
  -f geojsonseq -o berlin_hotels.jsonl
```

### 12. Cache management
```bash
botmap --json cache info       # is the divisions index current?
botmap cache build             # force rebuild against latest release
botmap cache clear             # nuke local cache
```

### 13. Water features (oceans, lakes, rivers)
```bash
botmap water --in "Minneapolis, MN" --class lake \
  -f geojsonseq -o lakes.jsonl
```

### 14. Land use (zoning-style polygons)
```bash
botmap landuse --in "Brooklyn, NY" --class residential \
  -f geojsonseq -o residential.jsonl
```

### 15. Bus stops and other transit POIs
```bash
# Transit stops are PLACES (categories.primary), not infrastructure.
botmap places --in "Williamsburg, NY" --category bus_stop \
  -f geojsonseq -o busstops.jsonl
```

### 16. Get a division's boundary polygon (for clipping / spatial joins)
```bash
# Emits a GeoJSON Feature with the division_area polygon — the supported way
# to get a boundary. Don't use `download -t division_area` for this.
botmap where "Alameda County, CA" --geometry > county.geojson
```

## Schema cheatsheet

| Type | Theme | Key properties |
|---|---|---|
| `place` | places | `categories.primary` (hotel, restaurant, cafe, hospital, **bus_stop, bus_station, train_station**, ...), `names.primary`, `confidence`, `addresses` |
| `building` | buildings | `height` (meters), `num_floors`, `class`, `subtype`, `roof_shape` |
| `segment` | transportation | `class` — covers ALL segments, not just car roads: motorway, primary, secondary, residential, **footway, path, cycleway**, sidewalk; plus `subclass`, `surface`, `speed_limits`. Use the `roads` verb with `--class`. |
| `division` | divisions | `subtype` (country, region, county, locality, neighborhood, ...), `admin_level`, `population`. Use `where … --geometry` for the boundary polygon. |
| `address` | addresses | `street`, `number`, `postcode`, `country` |
| `land_use` | base | `class` (commercial, residential, recreation, agriculture, ...). Use the `landuse` verb with `--class`. |
| `water` | base | `class` (ocean, lake, river, stream, ...). Use the `water` verb with `--class`. |

Run `botmap --json schema -t TYPE` for the full field list of any type.

## Filter expression syntax

Operators: `=`, `!=`, `<`, `<=`, `>`, `>=`, `in`. Keys are dot-paths into the
type's schema. Multiple `--where` flags AND together.

```
--where categories.primary=restaurant
--where 'height>100'
--where "class in [motorway,primary,trunk]"
```

**Always single-quote any `--where` expression containing `<` or `>`.**
Unquoted, the shell treats `>` as a redirection: `--where height>150` writes a
file named `150` and passes only `height` to the CLI (which then errors with
"has no operator").

## Anti-patterns

- **Don't download globally.** Always pass `--in` or `--bbox`. Global queries
  download hundreds of GB.
- **Always `count` before downloading anything large.** If the count is in
  the millions, narrow your filters before committing.
- **Prefer `--where` over post-filtering.** Filters push down to PyArrow and
  Parquet metadata; post-filtering GeoJSON is wasteful.
- **Don't invent place names.** If `where` returns no match, the place isn't
  in Overture's divisions. Try a parent (city → state → country). For
  non-English cities, the local-script form is often the canonical name
  (e.g. `東京都` for Tokyo). For neighborhoods that don't resolve at all
  (many US neighborhoods like SoHo, Greenwich Village, Chelsea are not in
  the divisions data), fall back to the parent locality or use `--bbox`.
- **Don't parse human stdout.** Use `--json` for metadata commands. Data
  commands always emit structured GeoJSON / GeoParquet.
- **Don't ignore the `--in` warning on stderr.** It tells you which Boston
  you actually got. If wrong, narrow with `--in "Boston, US-MA"`.
- **Quote `--where` filters with `<` or `>`.** Always single-quote them so the
  shell does not treat them as redirection (which writes a file named after the
  number and truncates the filter to just the key): `--where 'height>150'`.
- **Bus stops and transit points are `place` features.** Use
  `places --category bus_stop` (also bus_station, train_station) — not
  `download -t infrastructure`.
- **`roads` covers bikes and footpaths too.** It returns every transportation
  segment. Use `roads --class cycleway` (or footway/path) instead of
  `download -t segment --where class=cycleway`.
- **Prefer the convenience verbs over `download -t TYPE`.** `places`, `roads`,
  `buildings`, `addresses`, `water`, and `landuse` all wrap `download` with
  friendlier flags (`--class`, `--category`) and the same output.
- **Get boundaries with `where … --geometry`, not `download -t division_area`.**
  It emits the division polygon as a GeoJSON Feature for clipping or joins.
