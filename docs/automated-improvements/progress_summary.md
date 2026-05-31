# Agent-Usability Eval: Progress Summary

Three eval rounds, each 20 runs (10 questions × 2 agents), spanning the addition of new verbs, friendlier errors, and doc fixes. Rounds are labeled by the report file that produced the current round's proposals.

---

## Fleet-wide aggregate trends

| Metric | Report 01 | Report 02 | Report 03 | Change (01→03) |
|---|---|---|---|---|
| Download rate (avg across questions) | 35.0% | 15.0% | 15.0% | **−20 pp** |
| Unnecessary download rate | 15.0% | 15.0% | 15.0% | flat |
| Error rate | 25.0% | 20.0% | 20.0% | **−5 pp** |
| Completion rate | 90.0% | 90.0% | 85.0% | −5 pp |
| Avg commands per run | 4.40 | 4.10 | 3.45 | **−22%** |

The headline number is the download-rate drop from 35% to 15%: agents stopped resorting to raw `download` for 20 percentage points worth of questions once `water` and `landuse` verbs existed. The command count improvement (−22%) is the other standout: agents are finding the answer faster even on questions where completion rates wobbled.

---

## Per-question trends

| Question | Tier | R01 done / err | R02 done / err | R03 done / err | Direction |
|---|---|---|---|---|---|
| busstops-coffee-williamsburg | 5 | 100% / **100%** | 100% / **100%** | 50% / 50% | errors down, completion regressed |
| coffee-brooklyn-count | 1 | 100% / 0% | 100% / 0% | 100% / 0% | stable ✓ |
| containing-point | 3 | **0%** / 0% | 100% / 0% | 50% / 0% | fixed then regressed |
| hardware-near-bikepaths-alameda | 5 | 100% / 0% | 50% / 0% | 50% / **50%** | completion dropped; tracebacks appeared |
| landuse-brooklyn | 4 | 100% / 0% | 100% / **50%** | 100% / 50% | new error emerged, persists |
| pois-near-point | 3 | 100% / 0% | 100% / **50%** | 100% / 50% | new error emerged, persists |
| restaurant-categories-brooklyn | 2 | 100% / **50%** | 100% / 0% | 100% / 0% | fixed ✓ |
| tall-buildings-manhattan | 2 | 100% / **100%** | 100% / 0% | 100% / 0% | fixed ✓ |
| water-downtown-boston | 4 | 100% / 0% | 50% / 0% | 100% / 0% | wobbly but recovered |
| where-boston | 1 | 100% / 0% | 100% / 0% | 100% / 0% | stable ✓ |

---

## What drove each round's changes

### Report 01 → Report 02

**Fixes deployed:** `water` and `landuse` verbs; `--where` quoting in docs/skill; full US state names in geocoder; `-r`/`--release` collision fixed on `at`; actionable steering hints.

| Metric | Before | After |
|---|---|---|
| Fleet download rate | 35% | 15% |
| water download rate | 100% | 0% |
| landuse download rate | 100% | 0% |
| tall-buildings error rate | 100% | 0% |
| restaurant error rate | 50% | 0% |
| containing completion | 0% | 100% |

The two coverage-gap types (`water`, `land_use`) accounted for the entire 20 pp download-rate drop — agents immediately used the verbs once they existed.

**New problems exposed:** Adding the verbs revealed a latent path through `categories -t land_use` (now a recognized type, but `categories` still rejects non-place types → 50% error on `landuse-brooklyn`). The `-r` fix on `at` also unmasked `--json` as a group-level-only flag; agents moved from a release-collision error to a bad-option error on `pois-near-point`.

---

### Report 02 → Report 03

**Fixes deployed:** Concrete ready-to-run tip suggestions from `download`; transit-stop anti-pattern documented in skill; `where --geometry` flag for division polygons.

| Metric | Before | After |
|---|---|---|
| busstops error rate | 100% | 50% |
| busstops avg commands | 12.5 | 6.0 |
| fleet avg commands | 4.10 | 3.45 |

The improved `download` hints and the `--geometry` flag helped — busstops errors halved and command counts dropped substantially. But two new failure modes appeared: `download -t division_area` no longer printed just a tip but proceeded and crashed (traceback), and the `--json` bad-option persisted.

---

## Unnecessary download rate: why it's stuck at 15%

Every unnecessary download across all three rounds traces to two questions:

- **busstops-coffee-williamsburg**: agents consistently reach for `download -t infrastructure --where subtype=transit --where class=bus_stop`. Transit POIs are `place` features; the soft tip didn't deter them.
- **hardware-near-bikepaths-alameda**: agents reach for `download -t division_area` to get a county boundary for clipping. No dedicated verb existed; the tip didn't redirect them.

The 15% floor is these two questions each contributing ~50% unnecessary download across their 2 runs. Nothing else pollutes this metric.

---

## Expected impact of the changes just deployed

The current branch implements the six proposals from Report 03:

| Proposal | Target error / behavior |
|---|---|
| P1: `division_area` → hard `UsageError` | hardware tracebacks → clean error + redirect |
| P2: `boundary` verb | hardware agent has a real verb; TYPE_TO_VERB routes division_area to it |
| P3: transit infrastructure → hard `UsageError` | busstops unnecessary downloads become instant redirects |
| P5: `--json` no-op on data verbs | pois-near-point bad_option error eliminated |
| P6: `where` uses `_resolve_in_place` fallback | busstops `where Williamsburg, Brooklyn` resolves to Brooklyn bbox instead of exiting 1 |

If these work as expected:
- `pois-near-point` error rate: **50% → 0%** (P5)
- `busstops` error rate: **50% → ≤0%** (P3 blocks the infra download; P6 fixes the `where` failure)
- `hardware` traceback errors: **50% → 0%** (P1 turns them into directed UsageErrors)
- Unnecessary download rate: **15% → ~0%** (P2/P3 close both remaining sources)

The remaining stubborn issue is `landuse-brooklyn` `categories -t land_use` — it now gets a better error message (redirect to `schema` and the `landuse` verb), but the proposal to *actually enumerate* the `class` field for non-place types (P4) was not implemented. That's the one remaining actionable proposal.

---

## Persistent problem questions

### busstops-coffee-williamsburg (tier 5)
The hardest question across all three rounds. Two failure modes compound:
1. `where "Williamsburg, Brooklyn"` fails — neighborhood not in divisions index.
2. Agent falls back to `download -t infrastructure --where subtype=transit`.

P3 blocks path 2; P6 handles path 1 by falling back to the Brooklyn bbox. The combination should break the error loop. However, even if both fixes land cleanly, this is a complex multi-step task (find bus stops, find coffee shops, find co-located pairs) and tier-5 difficulty may mean some incompletions persist.

### hardware-near-bikepaths-alameda (tier 5)
Requires: get county boundary → filter bike paths to county → find hardware stores near bike paths. The agent consistently needed a division polygon and had no clean path to get one. P1 + P2 together give it `boundary "Alameda County, CA"` as a direct verb. This is the change most likely to improve completion rate from 50%.

### landuse-brooklyn (tier 4)
Agents trying `categories -t land_use` to discover class values is exactly the right behavior — the error is the CLI refusing them. P4 (full enumeration) remains unimplemented. Until then, the better UsageError message at least points at `overturemaps schema -t land_use` and `overturemaps landuse --class <value>`.
