# Designing CLI Interfaces for Data Products (in the LLM Era)

A field log from forking [`overturemaps-py`](https://github.com/OvertureMaps/overturemaps-py)
and reshaping its CLI so an LLM agent can use it without being a GIS expert.
The same redesign was friendlier for humans, too — but agents were the
forcing function. What follows is what we tried, what we got wrong, and the
design rules we'd carry into the next data-product CLI.

---

## What we started with

The pre-fork CLI was a thin wrapper over Overture's S3 Parquet:

```bash
overturemaps download --bbox xmin,ymin,xmax,ymax -t TYPE -f geojson -o out.geojson
```

That's the entire surface. It assumes you:

- Know which **type** (`place`, `building`, `segment`, `division_area`, …) holds your answer
- Have a **bounding box** in WGS84 already on hand
- Are comfortable post-filtering with `jq` or a notebook
- Will look at the Overture schema docs in another tab to figure out which
  field names are real

It's a perfectly fine pipe-builder for someone who already lives in QGIS.
It's a brick wall for everyone else — including LLM agents, which now make
up a meaningful share of "everyone else."

## The reframe: design for the question, not the data

The pre-fork CLI is *data-shaped*. Its verbs and flags mirror the storage
layout — types, bounding boxes, columns. To use it you have to translate
your question into that shape first.

Agents (and human newcomers) ask *question-shaped* things:

- "How many coffee shops are in Brooklyn?"
- "Find hospitals in Manhattan"
- "Coffee shops within 1km of this point"
- "What's at 40.7128, -74.0060?"
- "What's the bounding box of Boston?"

None of those naturally produce a `--bbox xmin,ymin,xmax,ymax -t place`.
They produce a verb (`count`, `find`, `near`), an entity ("coffee shops",
"hospitals"), and a place ("Brooklyn", "this point").

So the redesign goal became: **expose verbs that read like the question,
and resolve the data-shaped bits internally.**

## The new surface — what shipped

A small family of commands, grouped by what they do for the user, not by
what file they read:

**Intent verbs** — one per common entity type. Each takes either `--in
"Place Name"` or `--bbox …`:

| Command | Question it answers |
|---|---|
| `places --in X --category Y` | "POIs of type Y in place X" |
| `buildings --in X --where height>150` | "Tall buildings in X" |
| `roads --in X --class motorway` | "Highways through X" |
| `addresses --in X --street "Main"` | "Main St addresses in X" |

**Spatial primitives** — answer location-based questions directly:

| Command | Question it answers |
|---|---|
| `at LAT,LON -t TYPE --radius M --where …` | "What's near this point, filtered" |
| `containing LAT,LON` | "Which admin areas contain this point?" |
| `where "Boston, MA"` | "Where is X / what's its bbox?" |

**Discovery primitives** — let an agent learn the data without docs:

| Command | Purpose |
|---|---|
| `themes` / `types` | Top-level catalog |
| `schema -t TYPE` | Field list + sample row |
| `categories -t place --top 200` | Enumerate the place taxonomy |
| `sample -t TYPE …` | Preview N rows |
| `count -t TYPE …` | How big is this query? (cheap) |
| `capabilities` | JSON manifest of every subcommand for self-discovery |

**Friction-removers**:

- A global `--json` flag turns every metadata command into a clean pipe source
- `where` resolves "Boston, MA", "Boston, US-MA", "Boston, USA", "Boston,
  United States" — qualifier syntax matches how people actually write it
- `where --all` enumerates every candidate for an ambiguous query
- Every data command prints a one-line stderr warning when `--in` is
  ambiguous, naming both the picked match and the top alternative
- `places --category X` (and `--where categories.primary=X`) emits a
  stderr hint with up to 3 near-matches when no rows come back, so the
  agent doesn't have to round-trip through `categories` to find the
  right value
- A bundled **Skill** (`install-skill`) registers the CLI with Claude Code
  and other agent harnesses so the model loads the right context on the
  right kinds of question

What we *removed* (or demoted): nothing — `download` still exists, because
some types (`address`, `division`, `water`, `land_use`) didn't get a
convenience verb. But for the four covered types, `download` became the
escape hatch rather than the front door.

## The big design moves, condensed

1. **Verbs over types.** `places` instead of `download -t place`. The
   command name carries half the user's intent.
2. **Lookups, not coordinates.** `--in "Brooklyn"` instead of asking the
   user (or agent) to hand-build a bbox. We index Overture's divisions
   layer locally and resolve names → bboxes on the fly.
3. **Shortcuts for the long tail of common asks.** `--category coffee_shop`
   beats `--where categories.primary=coffee_shop`. Eighty percent of
   questions hit a tiny vocabulary; pay for that.
4. **Discovery is a first-class command, not docs.** `schema`,
   `categories`, `capabilities` mean an agent never has to leave the CLI
   to figure out the next argument.
5. **`--json` everywhere.** Every metadata command emits a machine-readable
   variant. Data commands emit structured GeoJSON / GeoParquet. No
   human-prose stdout to parse.
6. **A Skill alongside the binary.** The CLI ships with a markdown brief
   that primes any agent harness with the verbs, the patterns, and the
   anti-patterns. Documentation that the model actually reads.

---

## Testing through usage, not assertion

Unit tests prove the parser doesn't crash. They don't prove an LLM can
answer "5 coffee shops near my house in Alameda" without falling off a
cliff. The honest way to test an agent-facing CLI is to *put an agent in
front of it* and watch.

What we did:

- Wrote real questions in fresh conversations. No leading prompts, no
  babying the model into the "right" tool.
- Examples used:
  - *"Find 5 coffee shops near 1234 Main Street, Alameda CA, with a table of name, address, lat/lng, distance."*
  - *"List all ferry terminals in Alameda, California."*
  - *"Find coffee shops near the Target in Alameda and present them as a table sorted by distance."*
- Each ran in a temporary directory with no pre-loaded context except the
  installed CLI and its Skill.
- We let the conversation finish, then went back and read the trace.

Trace reading turned out to be the highest-leverage debugging tool we have.
A passing test tells you the code works. A trace tells you whether the
*interface* works — whether the verb name matched what the model reached
for, whether the error message taught it anything, whether it gave up and
fell back to a worse path.

We did this against **9 traces** across multiple project directories on the
same machine, including dry-run sessions from skill development and live
sessions from real ad-hoc questions.

## What the traces revealed

Grouping the stumbles by theme, with the actual commands the agent tried:

### A. The "fall back to `download`" failure mode

The most common pattern. Even with `places`, `buildings`, `roads`, and `at`
all present, agents repeatedly ended up at `download --bbox` for any query
that combined a point or area with a filter:

```bash
# What the agent eventually ran:
overturemaps download --bbox=-122.295,37.778,-122.265,37.800 -t place \
  --where "categories.primary=coffee_shop"
```

…to answer "coffee shops within 1.5km of this point." The right primitive
was `at LAT,LON -t place --radius 1500 --where …` — but it didn't exist at
the time of that trace. Once we added `--radius` and `--where` to `at`, the
fallback should have stopped. It didn't, fully — because the *Skill* still
led with `--in`-style recipes, so the agent didn't know the new shape existed.

**Lesson:** New flags ≠ new behavior. The Skill is the model's de-facto
documentation; it has to be updated in lockstep with the CLI surface,
otherwise the new affordance is invisible.

### B. Place-name ambiguity

`--in "Alameda, CA"` resolved to **Alameda County, Saskatchewan, Canada**
instead of Alameda, California. The agent didn't notice — it queried for
Target stores in the (wrong) place, got zero results, assumed the query
was the problem rather than the place, and went hunting in `--where` land.

```bash
overturemaps --json where "Alameda, CA"
# {"name":"Alameda County","subtype":"region","country":"CA",...}
```

The agent eventually discovered `"Alameda, US-CA"` works, but only after
several wrong-side-of-the-border queries.

**Lesson:** *Silent* resolution is the worst kind. If a single string
matches multiple plausible places, the CLI has to either (a) refuse and
ask, or (b) loudly print the chosen interpretation to stderr so the agent
can sanity-check. Default-and-pray gets you Saskatchewan.

**Fixes shipped (three layers):**

1. *Country alias normalization.* Qualifiers like `USA`, `United States`,
   alpha-3 codes, etc. are mapped to alpha-2 (`US`) before matching, so
   the common human forms resolve consistently.
2. *Smarter default ranking.* The old sort was `(admin_level desc,
   population desc)`. In this index `admin_level` is mostly null while
   `population` is set for places people actually search for. A
   tiny-county outranking a major city was a direct consequence of
   that. New ranking: `(has_population desc, population desc,
   admin_level desc)` — real places with measured inhabitants outrank
   thinly-documented administrative areas.
3. *Ambiguity warnings on stderr.* Every data command now prints a
   one-line warning when `--in` resolves to multiple candidates,
   naming the picked match and the top alternative and pointing at
   `where --all` for full inspection. Combined with `where --all`
   (a new flag that lists every candidate), agents can disambiguate
   without leaving the CLI.

The warning fires even after the smarter ranking — because correct
default ≠ unambiguous query. The warning is the audit trail that
turns a silent judgment call into a visible one.

### C. Address lookup unusable in practice

The trace that prompted this entire effort:

```bash
# Five attempts, all returning 0 rows:
overturemaps download -t address --in "Alameda, US-CA" --where 'number="1208"' --where 'street="Fountain Street"'
overturemaps download -t address --in "Alameda, US-CA" --where "number=1208" --where "street=Fountain Street"
overturemaps download -t address --in "Alameda, US-CA" --where 'street="FOUNTAIN ST"' --where 'number="1208"'
# Agent gives up, hand-sources coordinates from external geocoding.
```

The stored value was `"FOUNTAIN ST"` (uppercase, abbreviated). Three things
conspired against the agent:

1. The street field is sparsely covered, so retries felt like noise
2. The canonical form (`"FOUNTAIN ST"`) was undiscoverable without sampling
3. `--where street="…"` is exact-match only

**Lesson:** When a field's *storage form* doesn't match any sane *input
form*, the user (human or agent) cannot fix it with retries — they need a
new primitive. We added `addresses --street Fountain` (case-insensitive
substring) and the query that previously took six attempts now returns the
right row on the first try.

### D. Category names had to be guessed

```bash
overturemaps --json count -t place --in "Alameda, US-CA" --where categories.primary=ferry_terminal
# 0 rows
# Agent then runs:
overturemaps --json categories -t place --in "Alameda, US-CA" --top 200 | grep -i ferry
# Discovers ferry_terminal, ferry_service, ferry_boat_company all exist
overturemaps sample -t place --in "Alameda, US-CA" --where "categories.primary in [ferry_terminal,ferry_service,ferry,ferries]" -n 10
```

The discovery loop *worked* — `categories` is exactly the right command
for this — but the agent had to *know to reach for it*. A zero-result
response from a `categories.primary=X` filter is the perfect place to
suggest *"try `overturemaps categories -t place --search ferry`"*.

**Lesson:** Zero-result responses are an unused communication channel.
Empty stdout is the most expensive failure mode because it tells the model
nothing about what to do next.

**Fix shipped.** `places` now detects the failure case (zero rows + a
`categories.primary` filter), runs a one-shot scan of the bbox to enumerate
the *actual* category vocabulary present, ranks candidates against the
user's input, and emits up to three near-matches on stderr:

```
[overturemaps] 0 rows. No place has categories.primary='ferry_terminal' in
this bbox. Did you mean: ferry_boat_company? …
```

Ranking is token-overlap + character-similarity + substring containment —
a 4-component score that handles three classes of mistake together:
semantic misses (`ferry_terminal` → `ferry_boat_company`), single-character
typos (`coffe_shop` → `coffee_shop`), and short prefixes (`cafe` →
`cafeteria`). A complete-nonsense input falls through to a "not present"
fallback that points at `categories`. The scan only runs on the failure
path, so successful queries pay nothing.

### E. Progress bar polluting stdout

```python
# What the agent literally wrote to defend against our CLI:
data = json.loads(sys.stdin.read().split('Downloading')[0])
```

The download progress bar was emitted to stdout, *inline with the JSON
stream*. Every pipe broke. Agents either learned to surgically strip the
header, or invented Python workarounds for what should have been
`overturemaps places --bbox=… -f geojsonseq | jq ...`.

**Lesson:** stdout is for data, stderr is for human chatter. This is so
basic that it's easy to overlook — but the moment another tool wants to
pipe your output, any non-data byte on stdout becomes a bug.

**Fix shipped.** Investigating revealed the progress bar was *already*
going to stderr; the actual issue was twofold. First, `tqdm` uses `\r`
to redraw — which produces multi-line text dumps when stderr is captured
to a file. Second, agent invocations frequently merge streams (`2>&1`).
The one-line fix was `disable=None` in the `tqdm` constructor, which
auto-suppresses progress when stderr isn't a TTY. Interactive users
still see the bar; pipes, log capture, and `2>&1` get clean data.

### F. Agents silenced stderr aggressively

Most agent runs invoked the CLI as `… 2>/dev/null`. Helpful resolution
warnings (when they existed) went to `/dev/null`. The agent saw an empty
result and no signal as to why.

**Lesson:** This is a two-sided problem. The CLI should keep stderr
"reading-worthy" (don't spam progress to it), and the Skill should
instruct the agent not to silence it. The Skill update added that
guidance explicitly.

### G. Agents preferred `download` even when verbs existed

Old reflex from the pre-fork CLI: when in doubt, type `download`. Several
traces show `overturemaps download -t place --bbox … --where …` running
when an identical `overturemaps places --bbox … --where …` would have
worked. The two are functionally equivalent; the verb form is just nicer.

**Lesson:** Adding new verbs doesn't replace the muscle memory for the old
ones unless you actively retire them. We didn't remove `download` (yet) —
but we did demote it in the Skill and explicitly mark *"don't reach for
`download` first"* in the anti-patterns. Behavior should shift.

---

## The improvement loop

Every fix in this branch came from one of three sources:

1. **A trace where the agent gave up** — like the address case. A primitive
   was missing.
2. **A trace where the agent succeeded but ugly** — like the substring-strip
   defense against the progress bar. The interface was making the agent
   work harder than it should.
3. **A trace where the agent was wrong but didn't know it** — like the
   Alameda-Canada case. The error wasn't an error; it was silence.

The fix shape varied by category:

| Failure source | Fix shape |
|---|---|
| Missing primitive | New subcommand or flag (`addresses`, `at --radius`, `at --where`) |
| Ugly success | Internals change (substring op, progress to stderr) |
| Silent wrongness | Loud stderr warning + Skill guidance |

The pattern is consistent: **agents almost never read documentation, but
they always read tool output.** So the highest-leverage move is to put the
guidance in the output stream, not in a separate doc.

## Lessons we'd carry to another data-product CLI

These are the rules we'd write down before designing the next one.

### 1. Question shape, not data shape

Your verbs should match how someone *asks* about your data, not how the
data is laid out on disk. `places --in "Brooklyn" --category cafe` reads
like the question. `download --bbox … -t place --where categories.primary=cafe`
reads like the schema.

### 2. Cheap convenience for the head of the distribution

A handful of questions cover 80% of the requests. Wire shortcuts:
`--category`, `--class`, `--street`, `--radius`. The general filter
syntax (`--where K OP V`) stays for the long tail.

### 3. Resolve, don't require

If the user can type "Brooklyn" and you can resolve it to a bbox, do that.
The cost is one local index lookup; the gain is the user never has to
leave the question's vocabulary.

### 4. Make ambiguity loud

Silent default-resolution kills agent workflows in the most expensive way:
zero results, no signal, retry storm. If your resolver hit a fork, *say
so on stderr*. Better yet, refuse and ask.

### 5. Discovery is a command, not a manual

`schema`, `categories`, `capabilities`, `sample` — every one is a first-class
verb. An agent that can self-discover the shape of your data is an agent
that doesn't escalate to its human.

### 6. stdout for data; stderr for humans

If a byte that isn't data lands on stdout, somebody's pipe is going to
break. The progress bar argument always loses to the JSON parsing
argument.

### 7. Don't make the user guess the canonical form

If your field stores `"FOUNTAIN ST"` but users will type `"Fountain Street"`,
`"fountain st"`, or `"FOUNTAIN STREET"`, the *CLI* should bridge the gap.
Case-insensitive substring matching turned a six-attempt failure into a
single-shot success.

### 8. Empty results are a teaching opportunity

`0 rows` is the worst response you can give an agent. If you have any
signal about *why* — a near-miss in `categories`, a wrong region in
`--in` — emit it. Don't leave the agent to invent an explanation.

### 9. Test by watching the agent, not by writing more asserts

Unit tests will never catch the wrong-shaped verb name. Putting a real
question to a real agent and reading the trace will. Build a habit of
"trace review" the way you'd build a habit of code review.

### 10. Ship a Skill (or whatever your harness calls it)

If your data product is going to be used by LLM-driven agents in any
meaningful percentage of cases, ship the agent-facing brief alongside the
binary. Treat the Skill file as production code — versioned, tested,
updated in lockstep with the CLI surface.

---

## What shipped, end-to-end

Every failure mode the trace synthesis surfaced now has a corresponding
fix in the CLI, the Skill, or both:

| Failure mode (trace letter) | Status | Fix |
|---|---|---|
| A. Reflexive `download` fallback | Shipped | Skill re-ordered to lead with `places --bbox` and `at --where --radius` |
| B. Place-name ambiguity | Shipped | Country normalization + smarter ranking + stderr warning + `where --all` |
| C. Address lookup unusable | Shipped | New `addresses` subcommand with case-insensitive substring street matching |
| D. Category names guessed by trial | Shipped | Zero-result hints with token + similarity + substring scoring |
| E. Progress bar pollution | Shipped | `tqdm(disable=None)` auto-suppresses on non-TTY stderr |
| F. Agents silencing stderr | Skill | Anti-pattern added to the Skill; warnings now worth reading |
| G. Verb-vs-`download` muscle memory | Skill | Anti-patterns demote `download` to the escape hatch |

## What's still open

These didn't surface in any trace yet but are natural next moves:

- **Generalize zero-result hints beyond `places`.** `roads --class X`,
  `buildings --where class=X`, and `addresses --postcode X` could all
  use the same mechanism. Wait until traces show agents getting stuck
  there before generalizing — the `places --category` case was the loud
  one and it's resolved.
- **Structured stderr in JSON mode.** When `--json` is set, warnings
  currently still emit as prose lines on stderr. A JSON envelope
  (`{"warning": {...}}`) would let strict pipelines consume them. Low
  priority; agents read the prose just fine.
- **`download` deprecation for the four covered types.** `places`,
  `buildings`, `roads`, and `addresses` now do everything `download`
  does (plus more) for their respective types. A polite deprecation
  warning when `download -t place|building|segment|address` is invoked
  would close out trace G mechanically rather than depending on the
  Skill to retrain muscle memory.
- **Multilingual place names.** Overture stores `name_primary` in the
  local script (`東京都` for Tokyo). The Skill mentions this but the
  CLI doesn't help — there's no `name_common` fallback in the index
  (a pre-existing PyArrow constraint). Worth revisiting if it becomes
  a friction point.

## Closing

The path from "the CLI exists" to "the CLI is actually useful to an agent"
runs through traces. There's no shortcut. But every fix you put in front
of an agent is a fix you no longer have to put in a docs page, and the
agent will read your fix every time.

The biggest surprise of this exercise wasn't any single failure mode —
it was how cheaply the highest-impact fixes shipped once we had the
traces in hand. Most were one-line: a `disable=None`, a sort-key swap,
a stderr warning. The expensive part was knowing *which* line. That
knowledge lives in the traces, not in the documentation, not in the
test suite, not in the design doc. Treat trace review like you'd treat
code review.
