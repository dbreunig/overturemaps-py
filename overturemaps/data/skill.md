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
