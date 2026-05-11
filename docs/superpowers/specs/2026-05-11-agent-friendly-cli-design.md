# overturemaps-py — Agent-Friendly CLI Design

**Date:** 2026-05-11
**Status:** Draft for implementation
**Targets:** `overturemaps` ≥ 1.1.0
**Authors:** Drew Breunig (with Claude)

---

## 1. Problem

The current `overturemaps` CLI is geared at geospatial experts. To use it productively today you must:

1. Know a bounding box in `xmin,ymin,xmax,ymax` lon/lat order.
2. Know Overture's vocabulary — that POIs are `type=place`, restaurants live under `categories.primary`, motorways are `segment.class=motorway`, etc.
3. Filter only by bbox; every attribute filter requires downloading first and post-processing.
4. Tolerate human-formatted stdout (banners, ANSI, tqdm) when scripting.
5. Discover the answer to "how big is this query?" only by starting the download.

This blocks two audiences:

- **Non-experts**: a developer who just wants "all coffee shops in SoHo" shouldn't need to know what a bbox is.
- **Coding agents**: an agent reaching for Overture to ground a request (e.g. while building a feature that uses local POIs, neighborhood polygons, or road class) needs predictable, machine-readable behavior and self-describing capabilities.

## 2. Goals

- **Equal-weight design for humans and agents.** Same CLI; humans get plain-English defaults, agents get JSON-shaped output and a self-describing manifest.
- **Place names instead of bboxes.** "Boston" is the input, not `-71.19,42.23,-70.99,42.40`.
- **Push-down attribute filters.** `--where categories.primary=restaurant` runs as a PyArrow predicate, not a post-filter.
- **Cheap previews.** `count` and `sample` answer "is this query reasonable?" before committing to a download.
- **Agent discovery.** Ship a Claude Code Skill (and an AGENTS.md target) so an agent learns *when* to reach for the CLI and *how* to translate layperson questions into commands.
- **Strictly additive.** All existing commands, flags, and outputs continue to work byte-for-byte.

## 3. Non-Goals

- External geocoders (Nominatim/Photon).
- Query by arbitrary polygon (only bbox + point in this round).
- Pluggable agent-framework targets beyond Claude Code + AGENTS.md.
- OR / parentheses in `--where`. Operators are `=`, `!=`, `<`, `<=`, `>`, `>=`, `in`. Multiple `--where` flags AND together.
- Caching of feature-data downloads (only the divisions index is cached on disk).
- Backwards-incompatible reshaping of existing commands.

## 4. CLI Surface

### 4.1 Existing, unchanged

- `gers GERS_ID [-f … -o …]`
- `releases list | latest | check -o FILE | exists RELEASE`
- `changelog query | summary`

### 4.2 Existing, extended

#### `download`

Two new optional flags. Default behavior with neither flag is byte-for-byte identical to today.

| Flag | Notes |
|---|---|
| `--in TEXT` | Resolve a place name to a bbox via the divisions index. Mutually exclusive with `--bbox`. The resolved division is printed to stderr (or attached to JSON output for `download` with `--json` later). |
| `--where K OP V` | Repeatable attribute filter. See §5. |

Example:

```
overturemaps download -t place --in "Boston, MA" \
  --where categories.primary=restaurant \
  --where confidence>0.8 \
  -f geojsonseq -o restaurants.jsonl
```

### 4.3 New commands

| Command | Purpose |
|---|---|
| `where TEXT` | Resolve a place name to a division feature (id, subtype, country/region, bbox, population, parent). |
| `count -t TYPE [--in\|--bbox] [--where …]` | Row count for the query, without download. |
| `sample -t TYPE [--in\|--bbox] [--where …] -n N [-f FMT]` | Emit the first N features. |
| `themes` | List the 6 themes with one-line descriptions. |
| `types [--theme T]` | List the 15 types with their theme + description. |
| `schema -t TYPE` | Column names, types, and one sample feature. |
| `categories -t place [--in …] [--top N]` | Enumerate `categories.primary` values (with counts, optionally scoped to a region). |
| `capabilities` | JSON manifest of subcommands, params, defaults. For agent auto-discovery. |
| `places --in … [--category C] [--where …] -f … [-o …]` | Intent verb for POIs. |
| `buildings --in … [--where …] -f … [-o …]` | Intent verb for buildings. |
| `roads --in … [--class C] [--where …] -f … [-o …]` | Intent verb for `segment`. |
| `at LAT,LON [-t TYPE] [-n N] [-f FMT]` | Nearest-neighbor lookup at a point. `-t` defaults to `place`. |
| `containing LAT,LON` | Which divisions contain this point, innermost outward. Returns a small structured list (id + name + subtype + admin_level + country/region); honors `--json`. |
| `install-skill` | Interactive installer for the agent Skill / AGENTS.md. Also has non-interactive flags. |
| `cache info \| clear \| build` | Manage the on-disk divisions index. |

### 4.4 Global flags

- `--json` — affects metadata commands (`where`, `count`, `themes`, `types`, `schema`, `categories`, `capabilities`, `containing`, `releases *`, `cache info`). Data commands (`download`, `places`, `buildings`, `roads`, `sample`, `at`, `gers`) already emit structured data and ignore it.

## 5. Filter Syntax (`--where`)

### 5.1 Grammar

`--where KEY OP VALUE`, repeatable. Each flag is one comparison; multiple flags AND together.

| Element | Form |
|---|---|
| `KEY` | Dot-path into the type's PyArrow schema (e.g. `categories.primary`, `names.primary`, `height`, `bbox.xmin`). |
| `OP` | One of `=`, `!=`, `<`, `<=`, `>`, `>=`, `in`. |
| `VALUE` | Auto-typed: integer, float, `true`/`false`, or string. For `in`: `[a,b,c]`. Quoting only required when value contains spaces or commas. |

### 5.2 Translation

Each `(KEY, OP, VALUE)` becomes a `pyarrow.compute.Expression`. The conjunction of all `--where` flags is passed to `_prepare_query` alongside the bbox filter. Filters push down to row-group level via existing PyArrow machinery where the column has Parquet statistics; the S3 read path is unchanged.

List-valued fields (e.g. `categories.alternate`, which is `list<string>`) are out of scope for `--where` in v1; agents filter on scalar fields like `categories.primary` instead. A `contains` operator on list fields can be added later without breaking existing usage.

### 5.3 Validation

Before the network call: walk the type's schema and confirm every `KEY` resolves. On miss, error with the available field paths listed. This is the agent's primary safety net against misnamed columns.

### 5.4 Examples

```
--where categories.primary=restaurant
--where height>100
--where "names.primary in [Starbucks,Dunkin']"
--where class=motorway --where surface=paved
```

## 6. Geocoding

### 6.1 Approach

Lazy on-disk index of Overture's `divisions/division` joined with `divisions/division_area` for polygon bbox.

### 6.2 Build

- Triggered automatically on the first command that needs it (`where`, `--in`, `containing`).
- Reads the `division` and `division_area` partitions with column projection: `id`, `names.primary`, `names.common`, `subtype`, `class`, `country`, `region`, `admin_level`, `population`, `parent_division_id`, plus the polygon bbox from `division_area`.
- Joined on the canonical `division ↔ division_area` link (the foreign-key column on `division_area` referencing `division.id`; exact column name resolved by implementation against the live schema).
- Writes one parquet file at `$XDG_CACHE_HOME/overturemaps/divisions-index-<release>.parquet` (fallback `~/.cache/overturemaps/`). Expected size: 10–30 MB.

### 6.3 Invalidation

- Index file embeds the release ID in its filename.
- Lookup compares cached release to `releases latest`; if different, rebuild.
- `cache clear` removes the index.
- `cache build` forces a rebuild against the current latest release.

### 6.4 Lookup

- Inputs: `"Boston"`, `"Boston, MA"`, `"Boston, US"`, `"Boston, US-MA"`. Comma-separated tokens after the name narrow by region or country code.
- Match strategy: case-insensitive equality against `names.primary` and `names.common`. Substring match is **out of scope** for v1 — agents prefer exact behavior, and substring matches without ranking are noisy.
- Disambiguation: best match by `admin_level` (innermost wins when both match) then `population` desc. If multiple `--in` results tie, the first by stable sort wins.
- Reporting: the picked division prints to stderr; other matches listed. JSON mode includes a `candidates` array on `where`.

### 6.5 Errors

| Condition | Behavior |
|---|---|
| No match | Exit non-zero; in JSON mode `{"error":{"code":"no_match","message":...,"query":"…"}}`. |
| Index build fails | Exit non-zero; clear, actionable message. |
| Network unreachable on first build | Exit non-zero; suggest `--bbox` as fallback. |

## 7. JSON Output Convention

When `--json` is set on a metadata command:

- Stdout is exactly one JSON document (no trailing newline required by spec, present in practice).
- Stderr is silent unless there's an error.
- Errors: `{"error": {"code": "...", "message": "..."}}` on stderr; non-zero exit.
- Stable keys; new keys may be added but existing keys won't be removed without a major version bump.
- Exit codes match the human-mode command.

### 7.1 Output shapes

| Command | Shape |
|---|---|
| `where` | Object with `name`, `subtype`, `country`, `region`, `bbox`, `population`, `id`, `admin_level`, `parent_division_id`, `candidates`. |
| `count` | Object with `type`, `bbox`, `where`, `count`. |
| `themes` | Array of `{name, description, types}`. |
| `types` | Array of `{name, theme, description}`. |
| `schema` | Object with `type`, `fields` (array of `{name, type, doc}`), `example` (one feature). |
| `categories` | Array of `{value, count}` sorted by count desc. |
| `capabilities` | Object with `version`, `commands` (each with `name`, `params`, `description`). |
| `releases list` | Array of release strings, newest first. |
| `releases latest` | Object with `release`. |
| `cache info` | Object with `index_path`, `index_release`, `latest_release`, `up_to_date`, `size_bytes`. |
| `containing` | Array of `{id, name, subtype, admin_level, country, region}`, innermost first. |

## 8. Intent Verbs

Each verb is a thin Click command that translates user input through `geocoding` and `filters`, then calls the existing `_prepare_query → record_batch_reader → copy` pipeline.

- **`places`** — adds `-t place`. `--category VAL` desugars to `--where categories.primary=VAL`.
- **`buildings`** — adds `-t building`.
- **`roads`** — adds `-t segment` (not `connector`). `--class VAL` desugars to `--where class=VAL`.
- **`at LAT,LON [-t TYPE]`** — `-t` defaults to `place`. Builds a small bbox around the point using a per-type radius (100 m for `place`, 50 m for `building`, 25 m for `address`; configurable via `--radius`). Reads features, sorts by haversine distance to the query point in Python, limits to N (default 10). Output format follows `-f` (default `geojsonseq` to stdout when no `-o`); same writer pipeline as `download`.
- **`containing LAT,LON`** — uses the divisions index to find candidate divisions whose bbox contains the point, then loads each candidate's polygon (via the `division_area` shard hinted by the index row) and uses Shapely `contains` to filter to true contains. Emits results in order of decreasing `admin_level` (innermost first).

## 9. Skill Installer + Skill Content

### 9.1 Installer command

`overturemaps install-skill`

Interactive flow:

1. Prompts (multi-select) for targets:
   - **Claude Code, user scope** — `~/.claude/skills/overturemaps/SKILL.md`
   - **Claude Code, project scope** — `./.claude/skills/overturemaps/SKILL.md`
   - **AGENTS.md** — `./AGENTS.md` (appends a clearly-delimited section if file exists)
2. Confirms overwrite if a Claude target file already exists.
3. For `AGENTS.md`: inserts content between `<!-- overturemaps:start -->` and `<!-- overturemaps:end -->` markers (creating the file if needed, replacing the marked block if the markers already exist).

Non-interactive form for scripted installs:

```
overturemaps install-skill --target claude-user --target agents-md --yes
```

### 9.2 Skill content

Lives at `overturemaps/data/skill.md`. Same content drives both Claude SKILL.md output and AGENTS.md section output (with target-specific frontmatter wrapping). Sections:

1. **Frontmatter (Claude target only).**
   - `name: overturemaps`
   - `description:` — tuned for high agent recall. Draft text:
     > *"Use when a user's question or task involves places, buildings, roads, addresses, neighborhoods, or other geographic features — even if they don't use geo terms. Examples: 'how many coffee shops in SoHo', 'find hospitals in Brooklyn', 'show tall buildings in Manhattan', 'what neighborhoods make up Queens'."*

2. **Trigger heuristics.** Concrete layperson phrasings that should fire the skill, and negative examples that look geospatial but aren't (e.g. "draw a map of the org chart").

3. **Recipe catalog (~12 worked examples).** Each recipe shows a layperson question → the CLI calls → how to interpret the output. Patterns covered:
   - "Where is X" → `where`
   - "How many X in Y" → `where` + `count`
   - "Show me X in Y" → intent verb with `--in`
   - "What's at this point" → `at`
   - "Which neighborhoods make up Y" → `containing` + `where` of children
   - Chaining: `where --json` → extract bbox → feed to `download`
   - When to use `sample` before `download`
   - When to drop to raw `download -t` with `--where` for advanced filters

4. **Schema cheatsheet.** Themes/types table + key properties:
   - `place` → `categories.primary`, `names.primary`, `confidence`, `addresses`
   - `building` → `height`, `num_floors`, `class`, `subtype`
   - `segment` → `class` (motorway/primary/…), `subclass`, `surface`, `speed_limits`
   - `division` → `subtype` (country/region/county/locality/…), `admin_level`, `population`
   - `address` → `street`, `number`, `postcode`, `country`

5. **Anti-patterns.**
   - Don't download globally (no bbox, no `--in`).
   - Always `count` before downloading anything large.
   - Prefer `--where` push-down over post-filtering downloaded GeoJSON.
   - Don't invent place names not in Overture's divisions; use `where` first to verify.
   - Don't parse the human stdout — use `--json` for metadata commands.

## 10. Module Organization

New files:

- `overturemaps/geocoding.py` — `resolve(name) -> list[Division]`, `best_match(name) -> Division`, supporting types.
- `overturemaps/cache.py` — `index_path(release)`, `ensure_index(release)`, `build_index(release)`, `clear()`, `info()`.
- `overturemaps/filters.py` — `parse_where(field, op, value) -> pc.Expression`, schema-aware validation, `combine(filters) -> pc.Expression`.
- `overturemaps/introspection.py` — `list_themes()`, `list_types()`, `get_schema(type_)`, `sample_feature(type_)`, `enumerate_categories(type_, bbox=None, top=None)`.
- `overturemaps/intents.py` — shared helpers for the intent verbs (radius defaults, distance sort, etc.).
- `overturemaps/skill_installer.py` — Claude-Code + AGENTS.md writers; reads from `data/skill.md`.
- `overturemaps/data/skill.md` — Skill content (single source of truth).

`cli.py` gains new commands but each remains thin (10–40 lines), all delegating to the modules above.

Public Python API additions in `overturemaps/__init__.py`: `resolve`, `count_rows` (already exists internally, exposed here), `enumerate_categories`. Existing exports unchanged.

## 11. Backwards Compatibility

- All existing commands + flags + outputs identical.
- New flags on `download` are optional; default behavior unchanged.
- `.state` sidecar format unchanged.
- Public Python API: additions only, no removals or signature changes.
- The CLI banner and `--help` output for existing commands stay byte-identical.

## 12. Testing Requirements

### 12.1 Unit

- `filters.parse_where`: all 7 operators; auto-typing; `in` lists; schema-miss error path includes available fields.
- `geocoding.best_match`: tie-break by admin_level then population; comma-suffix narrowing; case-insensitive match.
- `cache`: build creates file at the right path; invalidation triggers on release change; `clear` removes file.
- `introspection`: each listing helper returns the expected shape; `--json` output passes a strict schema check.
- `intents`: `places --category X` desugars to the same query as `--where categories.primary=X`.
- `skill_installer`: Claude target writes correct path; AGENTS.md target inserts between markers; non-interactive `--yes` works.

### 12.2 Integration (network)

- `where "Boston, MA"` returns a US-MA locality with `population > 100000`.
- `count -t place --in "Boston, MA"` returns a positive integer.
- `categories -t place --in "Boston, MA" --top 5` returns 5 plausible categories.
- `at LAT,LON -t place` (with known coordinates) returns the expected nearby place.
- `containing LAT,LON` for a known city center returns its locality, county, region, country in order.

### 12.3 Smoke

- `overturemaps --json capabilities` parses as JSON; every listed command has at least a name + description.
- `overturemaps install-skill --target agents-md --yes` creates `AGENTS.md` in CWD with delimited content; second run replaces only the delimited block.

## 13. Open Questions

- Should `where` accept multiple results when ambiguity is genuine (e.g. `--all`)? Default is best-match; an `--all` flag could be added later without breaking anything.
- Should `count` also report estimated download bytes (helpful for agents picking format)? Could be a v1.2 follow-up.
- Should `capabilities` include a JSON Schema for each command's params? Useful for strict agent frameworks; deferable.

## 14. Migration Plan

This change is additive. Rollout:

1. Implement modules + commands behind the existing CLI group (no flag-gating needed — new commands are simply new).
2. Update README to highlight the new commands (especially `where`, `places`, `install-skill`) at the top of the "Usage" section; existing examples stay below.
3. Cut a minor release (`1.1.0`). Standalone binary build picks up new modules automatically.
4. Announce the Skill / AGENTS.md installer in release notes — that's the primary signal to agents that this tool is available to them.
