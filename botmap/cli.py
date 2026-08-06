"""
Overture Maps (overturemaps.org) command line utility.

Currently provides the ability to extract features from an Overture dataset
in a specified bounding box in a few different file formats.

"""

import importlib.metadata
import os
import sys
import uuid
from datetime import datetime, timezone

import click
import orjson
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .changelog import query_changelog_ids, summarize_changelog
from .introspection import list_themes, list_types, flatten_schema
from .intents import bbox_around_point, haversine_meters, DEFAULT_RADIUS_BY_TYPE
from .core import (
    count_rows,
    get_all_overture_types,
    get_available_releases,
    get_latest_release,
    record_batch_reader,
    record_batch_reader_from_gers,
    type_theme_map,
)
from .models import Backend, BBox, PipelineState
from .releases import list_releases, release_exists
from .state import get_state_path, load_state, save_state
from .writers import copy, get_writer
from .filters import parse_where_expr, ParsedFilter
from .geocoding import resolve
from .cache import cache_info, clear_cache, build_index, index_path
from . import skill_installer


def _safe_reader(type_, bbox, release, ct, rt, stac, **kw):
    """Wrap record_batch_reader, converting schema-validation ValueError."""
    try:
        return record_batch_reader(type_, bbox, release, ct, rt, stac, **kw)
    except ValueError as e:
        raise click.UsageError(str(e))


def _safe_count(type_, **kw):
    """Wrap count_rows, converting schema-validation ValueError."""
    try:
        return count_rows(type_, **kw)
    except ValueError as e:
        raise click.UsageError(str(e))


def _parse_latlon(latlon: str) -> tuple[float, float]:
    """Parse 'LAT,LON' string into (lat, lon) floats."""
    parts = [p.strip() for p in latlon.split(",")]
    if len(parts) != 2:
        raise click.UsageError("LATLON must be 'LAT,LON'")
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        raise click.UsageError("LATLON values must be numeric")


def _json_default(value):
    """Fallback serializer for orjson — hex-encode raw bytes."""
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    raise TypeError(f"Type is not JSON serializable: {type(value).__name__}")


def _emit_json(ctx, payload, file=None):
    """Print one JSON document to stdout."""
    out = orjson.dumps(
        payload, default=_json_default, option=orjson.OPT_INDENT_2
    ).decode()
    click.echo(out, file=file)


def _emit_error_json(message, code="error"):
    """Print a JSON error envelope to stderr."""
    err = {"error": {"code": code, "message": message}}
    click.echo(orjson.dumps(err).decode(), err=True)


def _describe_division(d) -> str:
    """One-line label like 'Alameda (locality, US-CA, pop 78,280)'."""
    qual = d.region or d.country or "?"
    pop = f"pop {d.population:,}" if d.population is not None else "pop ?"
    return f"{d.name} ({d.subtype}, {qual}, {pop})"


def _suggest_categories(type_: str, bbox, release, target: str, n: int = 3):
    """Scan `bbox` for `categories.primary` values and return up to `n`
    closest matches to `target`. Used to power 0-result hints — only call
    on the failure path, since this issues a second scan of the bbox.

    Ranking is token-aware: `ferry_terminal` should match `ferry_service`
    via the shared "ferry" token, not `cafeteria` via character overlap.
    """
    reader = record_batch_reader(type_, bbox, release, None, None, True)
    if reader is None:
        return []
    seen: set[str] = set()
    while True:
        try:
            batch = reader.read_next_batch()
        except StopIteration:
            break
        if batch.num_rows == 0:
            continue
        cat_col = batch.column("categories")
        primary = pc.struct_field(cat_col, "primary").to_pylist()
        for v in primary:
            if v is not None:
                seen.add(v)
    if not seen:
        return []

    import difflib
    target_lower = target.lower()
    target_tokens = set(target_lower.replace("_", " ").split())
    matcher = difflib.SequenceMatcher(autojunk=False)
    matcher.set_seq1(target_lower)

    # Inclusion rules (any one passes):
    #   - token overlap >= 1  → "ferry_terminal" ~ "ferry_service"
    #   - substring          → "cafe" ~ "cafeteria"
    #   - ratio >= 0.75      → typo correction ("coffe_shop" ~ "coffee_shop")
    # Anything weaker is noise (e.g. cafeteria ~ ferry_terminal at 0.609).
    scored = []
    for v in seen:
        v_lower = v.lower()
        matcher.set_seq2(v_lower)
        ratio = matcher.ratio()
        v_tokens = set(v_lower.replace("_", " ").split())
        token_overlap = len(target_tokens & v_tokens)
        substring_hit = int(target_lower in v_lower or v_lower in target_lower)
        if token_overlap or substring_hit or ratio >= 0.75:
            score = ratio + 0.2 * token_overlap + 0.15 * substring_hit
            scored.append((score, v))
    if not scored:
        return []
    scored.sort(key=lambda x: x[0], reverse=True)
    return [v for _, v in scored[:n]]


def _no_match_help(query: str) -> str:
    """Build an actionable message when a place query resolves to nothing.

    Retries once with qualifiers dropped (the bare name). If that resolves,
    names the candidate so the caller has a recovery path; otherwise explains
    the neighborhood/parent fallback. Covers the two common agent failures:
    neighborhood names ("Williamsburg, Brooklyn") and over-qualified queries
    ("Brooklyn, New York") that the divisions index can't match directly.
    """
    parts = [p.strip() for p in query.split(",")]
    name = parts[0]
    qualifiers = [p for p in parts[1:] if p]
    base = f"No division found for {query!r}."
    if qualifiers:
        fallback = resolve(name)
        if fallback:
            top = fallback[0]
            return (
                f"{base} The qualifier {', '.join(qualifiers)!r} matched "
                f"nothing, but {name!r} alone resolves to "
                f"{_describe_division(top)}. Use that, `containing LAT,LON`, "
                f"or `--bbox`."
            )
    return (
        f"{base} It may be a neighborhood not in Overture's divisions index. "
        f"Try a parent locality (city → state → country), `containing "
        f"LAT,LON`, or `--bbox`."
    )


def _resolve_in_place(in_place: str):
    """Resolve a --in query to a Division, warning to stderr on ambiguity.

    - Raises click.UsageError if no division matches.
    - On multiple matches, prints a one-line stderr warning naming the
      picked division and the top alternative. Returns the picked one.
    - When a qualifier is a locality name rather than a region code, retries
      the original name scoped to that locality's region, then falls back to
      the locality's own bbox — rather than failing outright.
    - Silent on a single unambiguous match.
    """
    matches = resolve(in_place)
    if not matches:
        # The qualifier may be a locality name ("Brooklyn") instead of a region
        # code ("US-NY"). Try each qualifier as a place; if it resolves, scope
        # the original name to that place's region and retry.
        parts = [p.strip() for p in in_place.split(",")]
        name, qualifiers = parts[0], [p for p in parts[1:] if p]
        for qualifier in qualifiers:
            parent_matches = resolve(qualifier)
            if not parent_matches:
                continue
            parent = parent_matches[0]
            if parent.region:
                scoped = resolve(f"{name}, {parent.region}")
                if scoped:
                    result = scoped[0]
                    click.secho(
                        f"[botmap] {in_place!r} not in divisions index; "
                        f"resolved via parent {qualifier!r} → "
                        f"using {_describe_division(result)}",
                        fg="yellow", err=True,
                    )
                    return result
            # Couldn't find the name in the parent's region — use the parent's
            # bbox directly so the query still runs over a bounded area.
            click.secho(
                f"[botmap] {in_place!r} not in divisions index; "
                f"using parent {qualifier!r} "
                f"({_describe_division(parent)}) bbox instead",
                fg="yellow", err=True,
            )
            return parent
        raise click.UsageError(_no_match_help(in_place))
    picked = matches[0]
    if len(matches) > 1:
        alt = matches[1]
        more = f" (+{len(matches) - 2} more)" if len(matches) > 2 else ""
        click.secho(
            f"[botmap] Ambiguous --in {in_place!r}: picked "
            f"{_describe_division(picked)} over "
            f"{_describe_division(alt)}{more}. "
            f"Run `botmap where {in_place!r} --all` to see all.",
            fg="yellow", err=True,
        )
    return picked


def _limit_reader(reader, n):
    """Yield a new RecordBatchReader emitting at most `n` rows."""
    import pyarrow as pa

    def _batches():
        remaining = n
        while remaining > 0:
            try:
                batch = reader.read_next_batch()
            except StopIteration:
                return
            if batch.num_rows == 0:
                continue
            if batch.num_rows > remaining:
                batch = batch.slice(0, remaining)
            remaining -= batch.num_rows
            yield batch

    return pa.RecordBatchReader.from_batches(reader.schema, _batches())


def _describe_param(param: click.Parameter) -> dict:
    """Convert a Click parameter to a JSON-friendly description."""
    out: dict = {
        "name": param.name,
        "required": getattr(param, "required", False),
    }
    if isinstance(param, click.Option):
        out["flags"] = list(param.opts)
        out["help"] = param.help or ""
        out["multiple"] = param.multiple
        out["is_flag"] = param.is_flag
    if param.default is not None and param.default is not False:
        try:
            # Only include defaults that are JSON-serializable
            if isinstance(param.default, (str, int, float, bool, list, tuple, type(None))):
                out["default"] = param.default
        except Exception:
            pass
    if isinstance(param.type, click.Choice):
        out["choices"] = list(param.type.choices)
    return out


def _describe_command(name: str, command: click.Command) -> dict:
    return {
        "name": name,
        "help": command.help or command.short_help or "",
        "params": [_describe_param(p) for p in command.params],
    }


def _walk_group(group: click.Group, prefix: str = "") -> list[dict]:
    out: list[dict] = []
    for name, cmd in group.commands.items():
        full = f"{prefix}{name}" if not prefix else f"{prefix} {name}"
        if isinstance(cmd, click.Group):
            out.extend(_walk_group(cmd, prefix=full))
        else:
            out.append(_describe_command(full, cmd))
    return out


# Earth's total surface area in square degrees (360 * 180).
EARTH_AREA_SQ_DEG = 64800
# Threshold (fraction of Earth) above which we warn about a large bbox.
LARGE_BBOX_THRESHOLD = 0.01  # 1% of Earth

# Overture type -> convenience verb that covers it with friendlier flags.
# Used by `download` to nudge agents toward the higher-level verbs.
TYPE_TO_VERB = {
    "place": "places",
    "segment": "roads",
    "building": "buildings",
    "address": "addresses",
    "water": "water",
    "land_use": "landuse",
    "division_area": "boundary",
}


def _suggest_verb_command(
    verb: str, in_place, bbox, where_exprs, output_format, output
) -> str:
    """Build a concrete ready-to-run verb command from download flags."""
    parts = [f"botmap {verb}"]
    if in_place:
        parts.append(f'--in "{in_place}"')
    elif bbox is not None:
        parts.append(f"--bbox {bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}")
    leftover = []
    for expr in (where_exprs or []):
        if expr.startswith("categories.primary="):
            parts.append(f"--category {expr.split('=', 1)[1]}")
        elif expr.startswith("class="):
            parts.append(f"--class {expr.split('=', 1)[1]}")
        else:
            leftover.append(expr)
    for expr in leftover:
        parts.append(f'--where "{expr}"')
    if output_format:
        parts.append(f"-f {output_format}")
    if output:
        parts.append(f"-o {output}")
    return " ".join(parts)


def _print_banner():
    try:
        import pyfiglet
        banner = pyfiglet.figlet_format("Overture Maps", font="slant")
    except Exception:
        banner = "Overture Maps\n"
    version = importlib.metadata.version("botmap")
    click.secho(banner.rstrip(), fg="blue", bold=True, err=True)
    click.secho(f"  v{version}  |  overturemaps.org\n", fg="bright_blue", err=True)


def _bbox_area_sq_deg(xmin: float, ymin: float, xmax: float, ymax: float) -> float:
    """Return the area of a lon/lat bbox in square degrees."""
    return abs(xmax - xmin) * abs(ymax - ymin)


class BboxParamType(click.ParamType):
    name = "bbox"

    def convert(self, value, param, ctx):
        parts = value.split(",")
        if len(parts) != 4:
            self.fail(
                f"bbox requires exactly 4 values (xmin,ymin,xmax,ymax), "
                f"got {len(parts)}. Example: --bbox -71.10,42.34,-71.05,42.36"
            )

        try:
            bbox = [float(x.strip()) for x in parts]
        except ValueError:
            self.fail(
                f"All bbox values must be numbers. Got '{value}'. "
                f"Example: --bbox -71.10,42.34,-71.05,42.36"
            )

        xmin, ymin, xmax, ymax = bbox

        # Validate longitude range
        if not (-180 <= xmin <= 180 and -180 <= xmax <= 180):
            self.fail(
                f"Longitude values must be between -180 and 180. "
                f"Got xmin={xmin}, xmax={xmax}"
            )

        # Validate latitude range
        if not (-90 <= ymin <= 90 and -90 <= ymax <= 90):
            self.fail(
                f"Latitude values must be between -90 and 90. "
                f"Got ymin={ymin}, ymax={ymax}"
            )

        # Check for swapped min/max
        if xmin > xmax:
            self.fail(
                f"xmin ({xmin}) must be less than or equal to xmax ({xmax}). "
                f"bbox format is: xmin,ymin,xmax,ymax"
            )
        if ymin > ymax:
            self.fail(
                f"ymin ({ymin}) must be less than or equal to ymax ({ymax}). "
                f"bbox format is: xmin,ymin,xmax,ymax"
            )

        return bbox


def validate_release(ctx, param, value):
    """Callback to validate release parameter against available releases."""
    if value is None:
        return get_latest_release()

    available_releases, _ = get_available_releases()
    if value not in available_releases:
        raise click.UsageError(
            f"Release '{value}' is no longer available. Overture keeps only the last "
            f"two monthly releases (~60 days) for GDPR compliance. Older releases are "
            f"automatically deleted from AWS S3 and Azure.\n\n"
            f"Available releases: {', '.join(available_releases)}\n"
            f"See all past release notes at: https://docs.overturemaps.org/release-calendar"
        )
    return value


def validate_gers_id(ctx, param, value):
    """Callback to validate GERS ID is a valid UUID."""
    if not value:
        raise click.BadParameter("GERS ID cannot be empty")

    try:
        parsed_uuid = uuid.UUID(value)
        return str(parsed_uuid)
    except ValueError:
        raise click.BadParameter(f"GERS ID must be a valid UUID. Got: '{value}'")


@click.group(invoke_without_command=True)
@click.version_option(
    version=importlib.metadata.version("botmap"),
    prog_name="botmap",
)
@click.option("--json", "json_output", is_flag=True, default=False,
              help="Emit machine-readable JSON for metadata commands.")
@click.pass_context
def cli(ctx, json_output):
    ctx.ensure_object(dict)
    ctx.obj["json"] = json_output
    if ctx.invoked_subcommand is None:
        _print_banner()
        click.echo(ctx.get_help())


@cli.command()
@click.option("--bbox", required=False, type=BboxParamType())
@click.option("--in", "in_place", required=False, type=str,
              help="Resolve a place name to a bbox via the divisions index.")
@click.option("--where", "where_exprs", multiple=True,
              help="Attribute filter K OP V (repeatable). Example: --where height>50")
@click.option(
    "-f",
    "output_format",
    type=click.Choice(["geojson", "geojsonseq", "geoparquet"]),
    required=True,
)
@click.option("-o", "--output", required=False, type=click.Path())
@click.option(
    "-t",
    "--type",
    "type_",
    type=click.Choice(get_all_overture_types()),
    required=True,
)
@click.option(
    "-r",
    "--release",
    default=None,
    callback=validate_release,
    required=False,
    help="Release version (defaults to latest)",
)
@click.option(
    "--stac/--no-stac",
    required=False,
    type=bool,
    is_flag=True,
    default=True,
    help="By default, uses the STAC catalog to limit which Parquet files are downloaded. Pass --no-stac to skip the catalog and read the full S3 dataset directly.",
)
@click.option("--connect_timeout", required=False, type=int)
@click.option("--request_timeout", required=False, type=int)
def download(
    bbox, in_place, where_exprs, output_format, output, type_, release,
    connect_timeout, request_timeout, stac,
):
    # Mutual exclusion check
    if bbox is not None and in_place is not None:
        raise click.UsageError("--bbox and --in are mutually exclusive")

    # Steering hints: nudge toward the right tool when download isn't needed.
    verb = TYPE_TO_VERB.get(type_)
    if verb is not None:
        suggestion = _suggest_verb_command(
            verb, in_place, bbox, where_exprs, output_format, output
        )
        if type_ == "division_area":
            raise click.UsageError(
                f"division_area is not downloadable this way — "
                f"for a boundary polygon run: {suggestion}"
            )
        click.secho(
            f"[botmap] Tip: try instead: {suggestion}",
            fg="bright_black", err=True,
        )
    elif type_ == "infrastructure":
        _TRANSIT_CLASSES = {"bus_stop", "bus_station", "train_station", "transit"}
        is_transit = any(
            (e.startswith("class=") and e.split("=", 1)[1].strip() in _TRANSIT_CLASSES)
            or e.startswith("subtype=transit")
            for e in (where_exprs or [])
        )
        if is_transit:
            loc_flag = (
                f'--in "{in_place}"' if in_place
                else (f"--bbox {bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}" if bbox else "--in <place>")
            )
            raise click.UsageError(
                f"Transit stops are `place` features, not infrastructure — run: "
                f"botmap places --category bus_stop {loc_flag}"
            )
        click.secho(
            "[botmap] Tip: transit stops (bus_stop, bus_station, "
            "train_station) are `place` features — use "
            "`botmap places --category bus_stop`. "
            "For non-transit infrastructure, download is correct.",
            fg="bright_black", err=True,
        )

    # Resolve --in to bbox
    if in_place is not None:
        division = _resolve_in_place(in_place)
        bbox = list(division.bbox)
        loc = division.region or division.country or "unknown"
        pop = f"pop {division.population:,}" if division.population is not None else "pop unknown"
        click.secho(
            f"Resolved {in_place!r} -> {division.name} "
            f"({division.subtype}, {loc}, {pop})",
            fg="bright_black", err=True,
        )

    # Parse --where expressions
    where_filters = None
    if where_exprs:
        try:
            where_filters = [parse_where_expr(e) for e in where_exprs]
        except ValueError as e:
            raise click.UsageError(str(e))

    if bbox is None:
        click.secho(
            "Warning: No bounding box provided. Downloading the entire dataset "
            "for this type. The full Overture dataset is approximately "
            "1.2 TB as GeoJSON and 400 GB as GeoParquet.",
            fg="yellow",
            bold=True,
            err=True,
        )
    else:
        area = _bbox_area_sq_deg(bbox[0], bbox[1], bbox[2], bbox[3])
        fraction = area / EARTH_AREA_SQ_DEG
        if fraction >= LARGE_BBOX_THRESHOLD:
            pct = fraction * 100
            click.secho(
                f"Warning: The bounding box covers ~{pct:.1f}% of Earth's surface. "
                f"This may take a long time and use significant bandwidth. "
                f"The full Overture dataset is approximately "
                f"1.2 TB as GeoJSON and 400 GB as GeoParquet.",
                fg="yellow",
                bold=True,
                err=True,
            )

    if output_format == "geoparquet" and output is None:
        raise click.UsageError(
            "Output file (-o/--output) is required when using geoparquet format"
        )

    output_file = sys.stdout if output is None else output

    reader = _safe_reader(
        type_, bbox, release, connect_timeout, request_timeout, stac,
        where_filters=where_filters,
    )

    if reader is None:
        return

    with get_writer(output_format, output_file, schema=reader.schema) as writer:
        copy(reader, writer)

    if output is not None:
        output_path = os.path.abspath(os.path.expanduser(output))
        backend = Backend(output_format)
        theme = type_theme_map.get(type_)
        if theme is None:
            click.secho(
                f"Warning: Could not determine theme for type {type_}",
                fg="yellow",
                bold=True,
                err=True,
            )
            return

        state = PipelineState(
            last_release=release,
            last_run=datetime.now(timezone.utc).isoformat(),
            theme=theme,
            type=type_,
            bbox=(
                BBox(xmin=bbox[0], ymin=bbox[1], xmax=bbox[2], ymax=bbox[3])
                if bbox is not None
                else None
            ),
            backend=backend,
            output=output_path,
        )

        state_path = get_state_path(output)
        save_state(state, state_path)
        click.secho(f"State saved to {state_path}", fg="bright_black", err=True)


@cli.command()
@click.argument("gers_id", required=True, callback=validate_gers_id)
@click.option(
    "-f",
    "output_format",
    type=click.Choice(["geojson", "geojsonseq", "geoparquet"]),
    default=None,
    required=False,
    help="Output format. If not specified, only registry information will be displayed.",
)
@click.option("-o", "--output", required=False, type=click.Path())
@click.option("--connect_timeout", required=False, type=int)
@click.option("--request_timeout", required=False, type=int)
@click.pass_context
def gers(ctx, gers_id, output_format, output, connect_timeout, request_timeout):
    """
    Query the GERS registry for a feature by its GERS ID.

    By default, this command only queries the registry and displays
    information about the feature (version, filepath, bbox, etc.) without
    downloading the feature data.

    To download the actual feature data, specify an output format using -f/--format.
    """
    from .core import query_gers_registry

    result = query_gers_registry(gers_id)

    if result is None:
        ctx.exit(1)

    if output_format is None:
        click.secho(
            f"\nRegistry lookup complete for GERS ID: {gers_id}", fg="bright_black", err=True
        )
        click.secho(
            "To download the feature data, use -f/--format option.", fg="bright_black", err=True
        )
        return

    if output_format == "geoparquet" and output is None:
        raise click.UsageError(
            "Output file (-o/--output) is required when using geoparquet format"
        )

    if output is None:
        output = sys.stdout

    reader = record_batch_reader_from_gers(
        gers_id, connect_timeout, request_timeout, registry_result=result
    )

    if reader is None:
        click.secho(
            f"Could not fetch feature data for GERS ID '{gers_id}'",
            fg="red",
            err=True,
        )
        ctx.exit(1)

    with get_writer(output_format, output, schema=reader.schema) as writer:
        copy(reader, writer)


@cli.command()
@click.argument("query", type=str)
@click.option("--all", "show_all", is_flag=True, default=False,
              help="Show all matching divisions, not just the best one.")
@click.option("--geometry", "--geojson", "geometry", is_flag=True, default=False,
              help="Emit the division's polygon as a GeoJSON Feature on stdout "
                   "(for clipping/spatial joins). Replaces `download -t "
                   "division_area`.")
@click.pass_context
def where(ctx, query, show_all, geometry):
    """Resolve a place name to an Overture division feature."""
    json_mode = ctx.obj.get("json", False)
    matches = resolve(query)
    if not matches:
        try:
            pick = _resolve_in_place(query)
            matches = [pick]
        except click.UsageError as e:
            msg = str(e)
            if json_mode:
                _emit_error_json(msg, code="no_match")
            else:
                click.secho(msg, fg="red", err=True)
            ctx.exit(1)
            return
    pick = matches[0]

    if geometry:
        _emit_division_geometry(ctx, pick)
        return

    if json_mode:
        payload = pick.as_dict()
        payload["candidates"] = [d.as_dict() for d in matches]
        _emit_json(ctx, payload)
        return

    # Human output
    def _print_one(d, prefix=""):
        qual = d.region or d.country or "?"
        click.secho(f"{prefix}{d.name}, {qual}", bold=True)
        click.echo(f"  subtype: {d.subtype}")
        click.echo(f"  bbox: {d.bbox[0]:.4f}, {d.bbox[1]:.4f}, "
                   f"{d.bbox[2]:.4f}, {d.bbox[3]:.4f}")
        if d.population is not None:
            click.echo(f"  population: {d.population:,}")
        click.echo(f"  id: {d.id}")

    if show_all:
        for i, d in enumerate(matches, start=1):
            _print_one(d, prefix=f"[{i}] ")
    else:
        _print_one(pick)
        if len(matches) > 1:
            click.secho(
                f"\n  ({len(matches) - 1} other match"
                f"{'es' if len(matches) - 1 != 1 else ''}; "
                f"rerun with --all to see them.)",
                fg="yellow",
            )


@cli.command()
@click.argument("query", type=str)
@click.pass_context
def boundary(ctx, query):
    """Emit a division's polygon as a GeoJSON Feature (for clipping/spatial joins).

    QUERY is a place name, e.g. 'Alameda County, CA' or 'Brooklyn, NY'.
    Outputs the division_area polygon on stdout — pipe to files or spatial tools.
    """
    division = _resolve_in_place(query)
    _emit_division_geometry(ctx, division)


@cli.command()
@click.option("-t", "--type", "type_",
              type=click.Choice(get_all_overture_types()), required=True)
@click.option("--bbox", required=False, type=BboxParamType())
@click.option("--in", "in_place", required=False, type=str)
@click.option("--where", "where_exprs", multiple=True)
@click.option("-r", "--release", default=None, callback=validate_release,
              required=False)
@click.pass_context
def count(ctx, type_, bbox, in_place, where_exprs, release):
    """Count features for a query without downloading them."""
    if bbox is not None and in_place is not None:
        raise click.UsageError("--bbox and --in are mutually exclusive")
    if in_place is not None:
        division = _resolve_in_place(in_place)
        bbox = list(division.bbox)

    try:
        where_filters = (
            [parse_where_expr(e) for e in where_exprs] if where_exprs else None
        )
    except ValueError as e:
        raise click.UsageError(str(e))

    n = _safe_count(
        type_, bbox=bbox, release=release, stac=True, where_filters=where_filters,
    )

    if ctx.obj.get("json"):
        _emit_json(ctx, {
            "type": type_,
            "bbox": bbox,
            "where": [{"key": f.key, "op": f.op, "value": f.value} for f in (where_filters or [])],
            "release": release,
            "count": n,
        })
    else:
        click.echo(f"{n:,}")


@cli.command()
@click.option("-t", "--type", "type_",
              type=click.Choice(get_all_overture_types()), required=True)
@click.option("--bbox", required=False, type=BboxParamType())
@click.option("--in", "in_place", required=False, type=str)
@click.option("--where", "where_exprs", multiple=True)
@click.option("-n", default=10, show_default=True, type=int,
              help="Maximum number of features to emit.")
@click.option("-f", "output_format",
              type=click.Choice(["geojson", "geojsonseq", "geoparquet"]),
              default="geojsonseq", show_default=True)
@click.option("-o", "--output", required=False, type=click.Path())
@click.option("-r", "--release", default=None, callback=validate_release,
              required=False)
def sample(type_, bbox, in_place, where_exprs, n, output_format, output, release):
    """Emit the first N features matching the query."""
    if bbox is not None and in_place is not None:
        raise click.UsageError("--bbox and --in are mutually exclusive")
    if in_place is not None:
        division = _resolve_in_place(in_place)
        bbox = list(division.bbox)

    try:
        where_filters = (
            [parse_where_expr(e) for e in where_exprs] if where_exprs else None
        )
    except ValueError as e:
        raise click.UsageError(str(e))

    reader = _safe_reader(
        type_, bbox, release, None, None, True,
        where_filters=where_filters,
    )
    if reader is None:
        return

    if output_format == "geoparquet" and output is None:
        raise click.UsageError("Output file (-o/--output) is required for geoparquet")

    output_file = sys.stdout if output is None else output
    limited = _limit_reader(reader, n)

    with get_writer(output_format, output_file, schema=limited.schema) as writer:
        copy(limited, writer)


@cli.command()
@click.pass_context
def themes(ctx):
    """List the six Overture themes."""
    data = list_themes()
    if ctx.obj.get("json"):
        _emit_json(ctx, data)
        return
    for t in data:
        click.secho(t["name"], bold=True, fg="cyan")
        click.echo(f"  {t['description']}")
        click.echo(f"  types: {', '.join(t['types'])}")
        click.echo()


@cli.command()
@click.option("--theme", required=False, type=str,
              help="Filter to types belonging to this theme.")
@click.pass_context
def types(ctx, theme):
    """List Overture feature types."""
    try:
        data = list_types(theme=theme)
    except ValueError as e:
        raise click.UsageError(str(e))
    if ctx.obj.get("json"):
        _emit_json(ctx, data)
        return
    for t in data:
        click.secho(t["name"], bold=True, fg="cyan")
        click.echo(f"  theme: {t['theme']}")
        click.echo(f"  {t['description']}")
        click.echo()


@cli.command()
@click.option("-t", "--type", "type_",
              type=click.Choice(get_all_overture_types()), required=True)
@click.option("-r", "--release", default=None, callback=validate_release,
              required=False)
@click.pass_context
def schema(ctx, type_, release):
    """Show the schema and a sample feature for an Overture type.

    Uses a tiny bbox over a known-populated area to keep this fast.
    """
    # A bbox over Manhattan, NYC — densely populated for any type.
    sample_bbox = [-74.020, 40.700, -73.930, 40.800]
    reader = record_batch_reader(
        type_, sample_bbox, release, None, None, True,
    )
    if reader is None:
        raise click.ClickException(f"No features available for type {type_!r}")

    sample = None
    try:
        batch = reader.read_next_batch()
        if batch.num_rows > 0:
            sample = batch.slice(0, 1).to_pylist()[0]
    except StopIteration:
        pass

    if sample is not None and isinstance(sample.get("geometry"), (bytes, bytearray)):
        # Convert WKB to a GeoJSON geometry dict so agents can read it.
        try:
            import shapely
            import shapely.wkb
            geom = shapely.wkb.loads(sample["geometry"])
            sample["geometry"] = orjson.loads(shapely.to_geojson(geom))
        except Exception:
            sample["geometry"] = sample["geometry"].hex()

    fields = flatten_schema(reader.schema)
    payload = {
        "type": type_,
        "fields": fields,
        "example": sample,
    }

    if ctx.obj.get("json"):
        _emit_json(ctx, payload)
        return

    click.secho(f"Schema for type {type_!r}", bold=True)
    for f in fields:
        click.echo(f"  {f['name']}: {f['type']}")
    click.echo()
    if sample is not None:
        click.secho("Example feature:", bold=True)
        click.echo(
            orjson.dumps(
                sample, default=_json_default, option=orjson.OPT_INDENT_2
            ).decode()
        )


@cli.command()
@click.option("-t", "--type", "type_",
              type=str, default="place", show_default=True,
              help="Feature type to enumerate. Only `place` is supported; "
                   "other types use `class` — see `botmap schema -t TYPE`.")
@click.option("--bbox", required=False, type=BboxParamType())
@click.option("--in", "in_place", required=False, type=str)
@click.option("--top", default=20, show_default=True, type=int)
@click.option("-r", "--release", default=None, callback=validate_release,
              required=False)
@click.pass_context
def categories(ctx, type_, bbox, in_place, top, release):
    """Enumerate `categories.primary` values, sorted by count desc."""
    if type_ != "place":
        verb = TYPE_TO_VERB.get(type_)
        if verb:
            raise click.UsageError(
                f"`categories` enumerates `categories.primary` for place features. "
                f"For `{type_}`, the classifying field is `class` — run "
                f"`botmap --json schema -t {type_}` to see available values, "
                f"or filter directly with `botmap {verb} --class <value>`."
            )
        raise click.UsageError(
            f"`categories` only enumerates `categories.primary` for place features. "
            f"Run `botmap --json schema -t {type_}` to inspect available fields."
        )
    if bbox is not None and in_place is not None:
        raise click.UsageError("--bbox and --in are mutually exclusive")
    if in_place is not None:
        division = _resolve_in_place(in_place)
        bbox = list(division.bbox)

    if bbox is None:
        raise click.UsageError("Provide --bbox or --in; global enumeration is too costly.")

    reader = record_batch_reader(type_, bbox, release, None, None, True)
    if reader is None:
        if ctx.obj.get("json"):
            _emit_json(ctx, [])
        return

    import pyarrow.compute as pc

    counts: dict[str, int] = {}
    while True:
        try:
            batch = reader.read_next_batch()
        except StopIteration:
            break
        if batch.num_rows == 0:
            continue
        cat_col = batch.column("categories")
        primary = pc.struct_field(cat_col, "primary")
        for item in pc.value_counts(primary).to_pylist():
            val = item["values"]
            if val is None:
                continue
            counts[val] = counts.get(val, 0) + item["counts"]

    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:top]
    payload = [{"value": v, "count": c} for v, c in ranked]

    if ctx.obj.get("json"):
        _emit_json(ctx, payload)
        return
    for row in payload:
        click.echo(f"  {row['count']:>8,}  {row['value']}")


@cli.command()
@click.pass_context
def capabilities(ctx):
    """Emit a machine-readable manifest of all subcommands."""
    payload = {
        "version": importlib.metadata.version("botmap"),
        "commands": _walk_group(cli),
    }
    if ctx.obj.get("json"):
        _emit_json(ctx, payload)
        return
    # Human mode just prints command names.
    for c in payload["commands"]:
        click.secho(c["name"], bold=True)
        if c["help"]:
            click.echo(f"  {c['help']}")


@cli.group()
def cache():
    """Manage the on-disk divisions index."""
    pass


@cache.command("info")
@click.pass_context
def cache_info_cmd(ctx):
    """Show cache location, current release, and up-to-date status."""
    latest = get_latest_release()
    data = cache_info(latest_release=latest)
    if ctx.obj.get("json"):
        _emit_json(ctx, data)
        return
    click.echo(f"Cache path:      {data['index_path']}")
    click.echo(f"Cached release:  {data['index_release'] or '(none)'}")
    click.echo(f"Latest release:  {data['latest_release']}")
    click.echo(f"Up to date:      {data['up_to_date']}")
    click.echo(f"Size:            {data['size_bytes']:,} bytes")


@cache.command("clear")
def cache_clear_cmd():
    """Remove all divisions-index files from the cache."""
    n = clear_cache()
    click.echo(f"Removed {n} cache file(s).")


@cache.command("build")
def cache_build_cmd():
    """Force a rebuild of the divisions index against the latest release."""
    release = get_latest_release()
    click.secho(f"Building divisions index for release {release}...",
                fg="bright_black", err=True)
    p = build_index(release)
    click.secho(f"Wrote {p}", fg="green", err=True)


@cli.command()
@click.option("--in", "in_place", required=False, type=str,
              help="Resolve a place name to a bbox via the divisions index.")
@click.option("--bbox", required=False, type=BboxParamType(),
              help="Bounding box xmin,ymin,xmax,ymax. Mutually exclusive with --in.")
@click.option("--category", required=False, type=str,
              help="Shortcut for --where categories.primary=VAL")
@click.option("--where", "where_exprs", multiple=True)
@click.option("-n", "--limit", "limit", default=None, type=int,
              help="Maximum number of features to emit (default: all matches).")
@click.option("-f", "output_format",
              type=click.Choice(["geojson", "geojsonseq", "geoparquet"]),
              default="geojsonseq", show_default=True)
@click.option("-o", "--output", required=False, type=click.Path())
@click.option("-r", "--release", default=None, callback=validate_release,
              required=False)
@click.option("--json", "json_no_op", is_flag=True, default=False, hidden=True)
def places(in_place, bbox, category, where_exprs, limit, output_format, output, release, json_no_op):
    """Download POIs in a named place. Filter by --category for common asks."""
    if bbox is not None and in_place is not None:
        raise click.UsageError("--bbox and --in are mutually exclusive")
    if bbox is None and in_place is None:
        raise click.UsageError("Provide --in or --bbox")
    if in_place is not None:
        division = _resolve_in_place(in_place)
        bbox = list(division.bbox)
    else:
        bbox = list(bbox)

    try:
        filters = [parse_where_expr(e) for e in where_exprs]
    except ValueError as e:
        raise click.UsageError(str(e))
    if category is not None:
        filters.append(ParsedFilter(
            key="categories.primary", op="=", value=category,
        ))

    if output_format == "geoparquet" and output is None:
        raise click.UsageError("Output file (-o/--output) is required for geoparquet")

    output_file = sys.stdout if output is None else output

    reader = _safe_reader(
        "place", bbox, release, None, None, True,
        where_filters=filters or None,
    )
    if reader is None:
        return
    if limit is not None:
        reader = _limit_reader(reader, limit)

    with get_writer(output_format, output_file, schema=reader.schema) as writer:
        rows_written = copy(reader, writer)

    if rows_written == 0:
        # Zero-result hint: was a categories.primary filter the cause?
        # If so, suggest near-match values from the bbox's actual category list.
        cat_filters = [
            f for f in filters
            if f.key == "categories.primary" and f.op in ("=", "in")
        ]
        if cat_filters:
            target = cat_filters[0].value
            if isinstance(target, list):
                target = target[0] if target else None
            if target:
                hits = _suggest_categories("place", bbox, release, str(target))
                if hits:
                    click.secho(
                        f"[botmap] 0 rows. No place has "
                        f"categories.primary={target!r} in this bbox. "
                        f"Did you mean: {', '.join(hits)}? "
                        f"Run `botmap categories -t place --bbox …` "
                        f"to see the full list.",
                        fg="yellow", err=True,
                    )
                else:
                    click.secho(
                        f"[botmap] 0 rows. categories.primary={target!r} "
                        f"is not present in this bbox. Run "
                        f"`botmap categories -t place --bbox …` "
                        f"to see what's available.",
                        fg="yellow", err=True,
                    )


@cli.command()
@click.option("--in", "in_place", required=False, type=str,
              help="Resolve a place name to a bbox via the divisions index.")
@click.option("--bbox", required=False, type=BboxParamType(),
              help="Bounding box xmin,ymin,xmax,ymax. Mutually exclusive with --in.")
@click.option("--where", "where_exprs", multiple=True)
@click.option("-n", "--limit", "limit", default=None, type=int,
              help="Maximum number of features to emit (default: all matches).")
@click.option("-f", "output_format",
              type=click.Choice(["geojson", "geojsonseq", "geoparquet"]),
              default="geojsonseq", show_default=True)
@click.option("-o", "--output", required=False, type=click.Path())
@click.option("-r", "--release", default=None, callback=validate_release,
              required=False)
@click.option("--json", "json_no_op", is_flag=True, default=False, hidden=True)
def buildings(in_place, bbox, where_exprs, limit, output_format, output, release, json_no_op):
    """Download buildings in a named place."""
    if bbox is not None and in_place is not None:
        raise click.UsageError("--bbox and --in are mutually exclusive")
    if bbox is None and in_place is None:
        raise click.UsageError("Provide --in or --bbox")
    if in_place is not None:
        division = _resolve_in_place(in_place)
        bbox = list(division.bbox)
    else:
        bbox = list(bbox)

    try:
        filters = [parse_where_expr(e) for e in where_exprs] or None
    except ValueError as e:
        raise click.UsageError(str(e))

    if output_format == "geoparquet" and output is None:
        raise click.UsageError("Output file (-o/--output) is required for geoparquet")
    output_file = sys.stdout if output is None else output

    reader = _safe_reader(
        "building", bbox, release, None, None, True, where_filters=filters,
    )
    if reader is None:
        return
    if limit is not None:
        reader = _limit_reader(reader, limit)
    with get_writer(output_format, output_file, schema=reader.schema) as writer:
        copy(reader, writer)


@cli.command()
@click.option("--in", "in_place", required=False, type=str,
              help="Resolve a place name to a bbox via the divisions index.")
@click.option("--bbox", required=False, type=BboxParamType(),
              help="Bounding box xmin,ymin,xmax,ymax. Mutually exclusive with --in.")
@click.option("--class", "road_class", required=False, type=str,
              help="Shortcut for --where class=VAL (e.g. motorway, primary)")
@click.option("--where", "where_exprs", multiple=True)
@click.option("-n", "--limit", "limit", default=None, type=int,
              help="Maximum number of features to emit (default: all matches).")
@click.option("-f", "output_format",
              type=click.Choice(["geojson", "geojsonseq", "geoparquet"]),
              default="geojsonseq", show_default=True)
@click.option("-o", "--output", required=False, type=click.Path())
@click.option("-r", "--release", default=None, callback=validate_release,
              required=False)
@click.option("--json", "json_no_op", is_flag=True, default=False, hidden=True)
def roads(in_place, bbox, road_class, where_exprs, limit, output_format, output, release, json_no_op):
    """Download road segments in a named place."""
    if bbox is not None and in_place is not None:
        raise click.UsageError("--bbox and --in are mutually exclusive")
    if bbox is None and in_place is None:
        raise click.UsageError("Provide --in or --bbox")
    if in_place is not None:
        division = _resolve_in_place(in_place)
        bbox = list(division.bbox)
    else:
        bbox = list(bbox)

    try:
        filters = [parse_where_expr(e) for e in where_exprs]
    except ValueError as e:
        raise click.UsageError(str(e))
    if road_class is not None:
        filters.append(ParsedFilter(key="class", op="=", value=road_class))

    if output_format == "geoparquet" and output is None:
        raise click.UsageError("Output file (-o/--output) is required for geoparquet")
    output_file = sys.stdout if output is None else output

    reader = _safe_reader(
        "segment", bbox, release, None, None, True,
        where_filters=filters or None,
    )
    if reader is None:
        return
    if limit is not None:
        reader = _limit_reader(reader, limit)
    with get_writer(output_format, output_file, schema=reader.schema) as writer:
        copy(reader, writer)


@cli.command()
@click.option("--in", "in_place", required=False, type=str,
              help="Resolve a place name to a bbox via the divisions index.")
@click.option("--bbox", required=False, type=BboxParamType(),
              help="Bounding box xmin,ymin,xmax,ymax. Mutually exclusive with --in.")
@click.option("--class", "water_class", required=False, type=str,
              help="Shortcut for --where class=VAL (e.g. ocean, lake, river, stream)")
@click.option("--where", "where_exprs", multiple=True)
@click.option("-n", "--limit", "limit", default=None, type=int,
              help="Maximum number of features to emit (default: all matches).")
@click.option("-f", "output_format",
              type=click.Choice(["geojson", "geojsonseq", "geoparquet"]),
              default="geojsonseq", show_default=True)
@click.option("-o", "--output", required=False, type=click.Path())
@click.option("-r", "--release", default=None, callback=validate_release,
              required=False)
@click.option("--json", "json_no_op", is_flag=True, default=False, hidden=True)
def water(in_place, bbox, water_class, where_exprs, limit, output_format, output, release, json_no_op):
    """Download water features (oceans, lakes, rivers, ...) in a named place."""
    if bbox is not None and in_place is not None:
        raise click.UsageError("--bbox and --in are mutually exclusive")
    if bbox is None and in_place is None:
        raise click.UsageError("Provide --in or --bbox")
    if in_place is not None:
        division = _resolve_in_place(in_place)
        bbox = list(division.bbox)
    else:
        bbox = list(bbox)

    try:
        filters = [parse_where_expr(e) for e in where_exprs]
    except ValueError as e:
        raise click.UsageError(str(e))
    if water_class is not None:
        filters.append(ParsedFilter(key="class", op="=", value=water_class))

    if output_format == "geoparquet" and output is None:
        raise click.UsageError("Output file (-o/--output) is required for geoparquet")
    output_file = sys.stdout if output is None else output

    reader = _safe_reader(
        "water", bbox, release, None, None, True,
        where_filters=filters or None,
    )
    if reader is None:
        return
    if limit is not None:
        reader = _limit_reader(reader, limit)
    with get_writer(output_format, output_file, schema=reader.schema) as writer:
        copy(reader, writer)


@cli.command()
@click.option("--in", "in_place", required=False, type=str,
              help="Resolve a place name to a bbox via the divisions index.")
@click.option("--bbox", required=False, type=BboxParamType(),
              help="Bounding box xmin,ymin,xmax,ymax. Mutually exclusive with --in.")
@click.option("--class", "landuse_class", required=False, type=str,
              help="Shortcut for --where class=VAL "
                   "(e.g. commercial, residential, recreation, agriculture)")
@click.option("--where", "where_exprs", multiple=True)
@click.option("-n", "--limit", "limit", default=None, type=int,
              help="Maximum number of features to emit (default: all matches).")
@click.option("-f", "output_format",
              type=click.Choice(["geojson", "geojsonseq", "geoparquet"]),
              default="geojsonseq", show_default=True)
@click.option("-o", "--output", required=False, type=click.Path())
@click.option("-r", "--release", default=None, callback=validate_release,
              required=False)
@click.option("--json", "json_no_op", is_flag=True, default=False, hidden=True)
def landuse(in_place, bbox, landuse_class, where_exprs, limit, output_format, output, release, json_no_op):
    """Download land-use polygons (residential, commercial, ...) in a named place."""
    if bbox is not None and in_place is not None:
        raise click.UsageError("--bbox and --in are mutually exclusive")
    if bbox is None and in_place is None:
        raise click.UsageError("Provide --in or --bbox")
    if in_place is not None:
        division = _resolve_in_place(in_place)
        bbox = list(division.bbox)
    else:
        bbox = list(bbox)

    try:
        filters = [parse_where_expr(e) for e in where_exprs]
    except ValueError as e:
        raise click.UsageError(str(e))
    if landuse_class is not None:
        filters.append(ParsedFilter(key="class", op="=", value=landuse_class))

    if output_format == "geoparquet" and output is None:
        raise click.UsageError("Output file (-o/--output) is required for geoparquet")
    output_file = sys.stdout if output is None else output

    reader = _safe_reader(
        "land_use", bbox, release, None, None, True,
        where_filters=filters or None,
    )
    if reader is None:
        return
    if limit is not None:
        reader = _limit_reader(reader, limit)
    with get_writer(output_format, output_file, schema=reader.schema) as writer:
        copy(reader, writer)


@cli.command()
@click.option("--in", "in_place", required=False, type=str,
              help="Resolve a place name to a bbox via the divisions index.")
@click.option("--bbox", required=False, type=BboxParamType(),
              help="Bounding box xmin,ymin,xmax,ymax. Mutually exclusive with --in.")
@click.option("--street", required=False, type=str,
              help="Street name (case-insensitive substring). Example: --street Fountain")
@click.option("--number", required=False, type=str,
              help="House/building number (exact match; field is a string, so \"1208\" or \"1208A\").")
@click.option("--postcode", required=False, type=str,
              help="Postal code (exact match).")
@click.option("--where", "where_exprs", multiple=True)
@click.option("-n", "--limit", "limit", default=None, type=int,
              help="Maximum number of features to emit (default: all matches).")
@click.option("-f", "output_format",
              type=click.Choice(["geojson", "geojsonseq", "geoparquet"]),
              default="geojsonseq", show_default=True)
@click.option("-o", "--output", required=False, type=click.Path())
@click.option("-r", "--release", default=None, callback=validate_release,
              required=False)
@click.option("--json", "json_no_op", is_flag=True, default=False, hidden=True)
def addresses(in_place, bbox, street, number, postcode, where_exprs, limit,
              output_format, output, release, json_no_op):
    """Find addresses in a named place or bbox.

    --street uses case-insensitive substring match ("Fountain" matches "Fountain
    St", "Fountain Ave", "E Fountain Blvd"). --number and --postcode are exact.
    For other fields, use --where.

    Requires --in or --bbox to keep queries bounded; the global address dataset
    is too large to scan unfiltered.
    """
    if bbox is not None and in_place is not None:
        raise click.UsageError("--bbox and --in are mutually exclusive")
    if bbox is None and in_place is None:
        raise click.UsageError("Provide --in or --bbox")
    if in_place is not None:
        division = _resolve_in_place(in_place)
        bbox = list(division.bbox)
    else:
        bbox = list(bbox)

    try:
        filters = [parse_where_expr(e) for e in where_exprs]
    except ValueError as e:
        raise click.UsageError(str(e))
    if street is not None:
        filters.append(ParsedFilter(key="street", op="~", value=street))
    if number is not None:
        filters.append(ParsedFilter(key="number", op="=", value=str(number)))
    if postcode is not None:
        filters.append(ParsedFilter(key="postcode", op="=", value=str(postcode)))

    if output_format == "geoparquet" and output is None:
        raise click.UsageError("Output file (-o/--output) is required for geoparquet")
    output_file = sys.stdout if output is None else output

    reader = _safe_reader(
        "address", bbox, release, None, None, True,
        where_filters=filters or None,
    )
    if reader is None:
        return
    if limit is not None:
        reader = _limit_reader(reader, limit)
    with get_writer(output_format, output_file, schema=reader.schema) as writer:
        copy(reader, writer)


@cli.command()
@click.argument("latlon", type=str)
@click.option("-t", "--type", "type_",
              type=click.Choice(get_all_overture_types()), default="place",
              show_default=True)
@click.option("-n", default=10, show_default=True, type=int)
@click.option("-r", "--radius", type=int, required=False,
              help="Radius in meters; defaults per type.")
@click.option("--where", "where_exprs", multiple=True,
              help="Attribute filter K OP V (repeatable). "
                   "Example: --where categories.primary=coffee_shop")
@click.option("-f", "output_format",
              type=click.Choice(["geojson", "geojsonseq", "geoparquet"]),
              default="geojsonseq", show_default=True)
@click.option("-o", "--output", required=False, type=click.Path())
@click.option("--release", default=None, callback=validate_release,
              required=False)
@click.option("--json", "json_no_op", is_flag=True, default=False, hidden=True)
def at(latlon, type_, n, radius, where_exprs, output_format, output, release, json_no_op):
    """Nearest-neighbor lookup. LATLON is 'LAT,LON' (lat first, geographic)."""
    lat, lon = _parse_latlon(latlon)

    if radius is None:
        radius = DEFAULT_RADIUS_BY_TYPE.get(type_, 100)
    bbox = list(bbox_around_point(lat, lon, radius))

    try:
        where_filters = (
            [parse_where_expr(e) for e in where_exprs] if where_exprs else None
        )
    except ValueError as e:
        raise click.UsageError(str(e))

    reader = _safe_reader(
        type_, bbox, release, None, None, True, where_filters=where_filters,
    )
    if reader is None:
        return

    # Collect all matching features, compute distance, sort, keep top N.
    import shapely.wkb
    import pyarrow as pa

    rows: list[tuple[float, dict, bytes]] = []
    while True:
        try:
            batch = reader.read_next_batch()
        except StopIteration:
            break
        if batch.num_rows == 0:
            continue
        geom_blobs = batch.column("geometry").to_pylist()
        prop_cols = [c for c in batch.schema.names if c not in ("geometry", "bbox")]
        prop_rows = batch.select(prop_cols).to_pylist()
        for blob, prop in zip(geom_blobs, prop_rows):
            try:
                geom = shapely.wkb.loads(blob)
                centroid = geom.centroid
                d = haversine_meters(lat, lon, centroid.y, centroid.x)
            except Exception:
                continue
            rows.append((d, prop, blob))

    rows.sort(key=lambda x: x[0])
    rows = rows[:n]

    if output_format == "geoparquet" and output is None:
        raise click.UsageError("Output file (-o/--output) is required for geoparquet")
    output_file = sys.stdout if output is None else output

    # Build a fresh reader over the kept rows and stream through the writer.
    if not rows:
        return

    sample_batch_props = [r[1] for r in rows]
    sample_batch_geoms = [r[2] for r in rows]
    kept_names = [n for n in reader.schema.names if n not in ("bbox",)]
    new_schema = pa.schema(
        [(name, reader.schema.field(name).type) for name in kept_names]
    )
    if reader.schema.metadata:
        new_schema = new_schema.with_metadata(reader.schema.metadata)

    # Build one batch carrying back properties + geometry; rely on existing writer.
    out_rows = []
    for prop, blob in zip(sample_batch_props, sample_batch_geoms):
        row = dict(prop)
        row["geometry"] = blob
        out_rows.append(row)
    batch = pa.RecordBatch.from_pylist(out_rows, schema=new_schema)
    one_shot = pa.RecordBatchReader.from_batches(new_schema, iter([batch]))

    with get_writer(output_format, output_file, schema=new_schema) as writer:
        copy(one_shot, writer)


def _get_division_area_files(release: str, candidate_bbox: tuple) -> list:
    """Return S3 paths for division_area files that intersect candidate_bbox.

    Uses the STAC collections parquet (cached per process) to restrict the
    file list so we only open the 1-3 files relevant to a given candidate
    instead of all 8 in the partition.

    candidate_bbox is (xmin, ymin, xmax, ymax).
    """
    import io
    import pyarrow.parquet as _pq
    import pyarrow.compute as _pc
    from urllib.request import urlopen

    stac_cache = _get_division_area_files._stac_cache  # type: ignore[attr-defined]
    stac_table = stac_cache.get(release)
    if stac_table is None:
        stac_url = f"https://stac.overturemaps.org/{release}/collections.parquet"
        try:
            with urlopen(stac_url) as response:
                buf = io.BytesIO(response.read())
            stac_table = _pq.read_table(buf)
        except Exception:
            stac_table = None
        stac_cache[release] = stac_table

    if stac_table is None:
        # STAC unavailable — return None to signal fallback to full partition
        return []

    xmin, ymin, xmax, ymax = candidate_bbox
    type_filter = (
        (_pc.field("collection") == "division_area")
        & (_pc.field("type") == "Feature")
    )
    bbox_filter = (
        (_pc.field("bbox", "xmin") < xmax)
        & (_pc.field("bbox", "xmax") > xmin)
        & (_pc.field("bbox", "ymin") < ymax)
        & (_pc.field("bbox", "ymax") > ymin)
    )
    rows = stac_table.filter(type_filter & bbox_filter).to_pylist()
    result = []
    for r in rows:
        try:
            href = r["assets"]["aws"]["alternate"]["s3"]["href"]
            result.append(href[len("s3://"):])
        except (KeyError, TypeError):
            continue
    return result


_get_division_area_files._stac_cache = {}  # type: ignore[attr-defined]


def _prefetch_polygons(
    division_ids: list[str],
    lon: float,
    lat: float,
    release: str,
) -> None:
    """Batch-fetch division_area polygons into the _polygon_contains cache."""
    import shapely.wkb
    import pyarrow.dataset as _ds
    import pyarrow.fs as _fs
    import pyarrow.compute as _pc
    from shapely.ops import unary_union

    cache = _polygon_cache
    uncached = [did for did in division_ids if did not in cache]
    if not uncached:
        return

    fs = _fs.S3FileSystem(
        anonymous=True, region="us-west-2",
        connect_timeout=30, request_timeout=120,
    )
    path = (
        f"overturemaps-us-west-2/release/{release}"
        "/theme=divisions/type=division_area/"
    )
    dataset = _ds.dataset(path, filesystem=fs)
    filter_expr = _pc.field("division_id").isin(uncached)
    table = dataset.to_table(columns=["division_id", "geometry"], filter=filter_expr)

    by_id: dict[str, list] = {did: [] for did in uncached}
    for row in table.to_pylist():
        by_id.setdefault(row["division_id"], []).append(row["geometry"])

    for did in uncached:
        geom_blobs = by_id.get(did, [])
        if geom_blobs:
            geoms = [shapely.wkb.loads(b) for b in geom_blobs]
            cache[did] = unary_union(geoms)
        else:
            cache[did] = None


def _polygon_contains(
    division_id: str,
    lon: float,
    lat: float,
    candidate_bbox: tuple | None = None,
    geometry_wkb: bytes | None = None,
) -> bool:
    """True if the division_area polygon for `division_id` contains the point.

    When `geometry_wkb` is supplied (pre-unioned WKB from a local cache),
    no S3 I/O is performed — the polygon is decoded from the bytes directly.

    When `geometry_wkb` is absent the function fetches from the `division_area`
    S3 partition, using STAC-based file selection via `candidate_bbox` to limit
    the number of Parquet files opened.

    Results are cached per-process by `division_id`.
    """
    import shapely.wkb

    cache = _polygon_cache
    if division_id in cache:
        poly = cache[division_id]
    elif geometry_wkb is not None:
        # Fast path: geometry provided by caller (e.g. from local geom cache).
        poly = shapely.wkb.loads(geometry_wkb)
        cache[division_id] = poly
    else:
        # Fetch from S3, using STAC to restrict which files to open.
        import pyarrow.dataset as ds
        import pyarrow.fs as _fs
        import pyarrow.compute as _pc

        release = get_latest_release()
        fs = _fs.S3FileSystem(
            anonymous=True, region="us-west-2",
            connect_timeout=30, request_timeout=120,
        )

        if candidate_bbox is not None:
            file_paths = _get_division_area_files(release, candidate_bbox)
        else:
            file_paths = []

        if file_paths:
            dataset = ds.dataset(file_paths, filesystem=fs, format="parquet")
        else:
            path = (
                f"overturemaps-us-west-2/release/{release}"
                "/theme=divisions/type=division_area/"
            )
            dataset = ds.dataset(path, filesystem=fs)

        filter_expr = (
            (_pc.field("division_id") == division_id)
            & (_pc.field("bbox", "xmin") <= lon)
            & (_pc.field("bbox", "xmax") >= lon)
            & (_pc.field("bbox", "ymin") <= lat)
            & (_pc.field("bbox", "ymax") >= lat)
        )
        table = dataset.to_table(columns=["geometry"], filter=filter_expr)
        if table.num_rows == 0:
            cache[division_id] = None
            return False
        geoms = [shapely.wkb.loads(b) for b in table.column("geometry").to_pylist()]
        from shapely.ops import unary_union
        poly = unary_union(geoms)
        cache[division_id] = poly

    if poly is None:
        return False
    from shapely.geometry import Point
    return poly.contains(Point(lon, lat))


_polygon_cache: dict[str, object] = {}


def _emit_division_geometry(ctx, division) -> None:
    """Fetch `division`'s division_area polygon and print it as a GeoJSON
    Feature on stdout. Raises click.UsageError if no polygon is available.

    Reuses the same `_prefetch_polygons` / division_area S3 path as
    `containing`, so callers get a boundary for clipping or spatial joins
    without resorting to `download -t division_area`.
    """
    from shapely.geometry import mapping

    release = get_latest_release()
    _prefetch_polygons([division.id], 0.0, 0.0, release)
    poly = _polygon_cache.get(division.id)
    if poly is None:
        raise click.UsageError(
            f"No division_area polygon found for "
            f"{_describe_division(division)} (id {division.id})."
        )
    feature = {
        "type": "Feature",
        "geometry": mapping(poly),
        "properties": {
            "id": division.id,
            "name": division.name,
            "subtype": division.subtype,
            "country": division.country,
            "region": division.region,
        },
    }
    _emit_json(ctx, feature)


@cli.command()
@click.argument("latlon", type=str)
@click.pass_context
def containing(ctx, latlon):
    """Which divisions contain this point? Innermost (highest admin_level) first."""
    lat, lon = _parse_latlon(latlon)

    release = get_latest_release()
    from .cache import ensure_index
    index_p = ensure_index(release)
    table = pq.read_table(index_p)

    # bbox-contains-point candidates
    expr = (
        (pc.field("bbox_xmin") <= lon)
        & (pc.field("bbox_xmax") >= lon)
        & (pc.field("bbox_ymin") <= lat)
        & (pc.field("bbox_ymax") >= lat)
    )
    candidates = table.filter(expr).to_pylist()

    # Pre-fetch all candidate polygons in a single S3 call, then test containment.
    _prefetch_polygons(
        [c["id"] for c in candidates], lon, lat, release,
    )
    matches = [
        c for c in candidates
        if _polygon_contains(
            c["id"], lon, lat,
            candidate_bbox=(
                c["bbox_xmin"], c["bbox_ymin"], c["bbox_xmax"], c["bbox_ymax"]
            ),
            geometry_wkb=c.get("geometry_wkb"),
        )
    ]
    # Innermost (highest admin_level) first; treat None as 0
    matches.sort(key=lambda c: c["admin_level"] or 0, reverse=True)

    payload = [
        {
            "id": c["id"],
            "name": c["name_primary"],
            "subtype": c["subtype"],
            "admin_level": c["admin_level"],
            "country": c["country"],
            "region": c["region"],
        }
        for c in matches
    ]

    if ctx.obj.get("json"):
        _emit_json(ctx, payload)
        return
    for row in payload:
        loc = row["region"] or row["country"] or "?"
        click.secho(f"  {row['name']} ({row['subtype']}, {loc})", fg="cyan")


_INSTALL_TARGETS = ("claude-user", "claude-project", "pi-user", "pi-project", "agents-md")


@cli.command("install-skill")
@click.option("--target", "targets", multiple=True,
              type=click.Choice(_INSTALL_TARGETS),
              help="Target to install to. Repeat for multiple. "
                   "Omit to be prompted.")
@click.option("--yes", "skip_confirm", is_flag=True, default=False,
              help="Skip overwrite confirmations.")
def install_skill_cmd(targets, skip_confirm):
    """Install the Overture agent Skill (Claude Code / AGENTS.md)."""
    if not targets:
        targets = []
        click.echo("Where should the Skill be installed?")
        for t in _INSTALL_TARGETS:
            if click.confirm(f"  Install to {t}?", default=(t == "claude-user")):
                targets.append(t)
        if not targets:
            click.secho("No targets selected; nothing to do.", fg="yellow")
            return

    for t in targets:
        if t == "claude-user":
            target_path = skill_installer._claude_user_dir() / "SKILL.md"
            if target_path.exists() and not skip_confirm:
                if not click.confirm(f"Overwrite {target_path}?", default=True):
                    continue
            p = skill_installer.install_claude_user()
            click.secho(f"Wrote {p}", fg="green")
        elif t == "claude-project":
            target_path = skill_installer._claude_project_dir() / "SKILL.md"
            if target_path.exists() and not skip_confirm:
                if not click.confirm(f"Overwrite {target_path}?", default=True):
                    continue
            p = skill_installer.install_claude_project()
            click.secho(f"Wrote {p}", fg="green")
        elif t == "pi-user":
            target_path = skill_installer._pi_user_dir() / "SKILL.md"
            if target_path.exists() and not skip_confirm:
                if not click.confirm(f"Overwrite {target_path}?", default=True):
                    continue
            p = skill_installer.install_pi_user()
            click.secho(f"Wrote {p}", fg="green")
        elif t == "pi-project":
            target_path = skill_installer._pi_project_dir() / "SKILL.md"
            if target_path.exists() and not skip_confirm:
                if not click.confirm(f"Overwrite {target_path}?", default=True):
                    continue
            p = skill_installer.install_pi_project()
            click.secho(f"Wrote {p}", fg="green")
        elif t == "agents-md":
            p = skill_installer.install_agents_md()
            click.secho(f"Updated {p}", fg="green")


@cli.group()
def releases():
    """Manage and query Overture Maps releases."""
    pass


@releases.command(name="list")
def releases_list():
    """List all available Overture Maps releases."""
    all_releases = list_releases()
    if not all_releases:
        click.secho("No releases found.", fg="red", err=True)
        return
    for i, release in enumerate(all_releases):
        if i == 0:
            click.secho(release, fg="cyan", bold=True)  # latest
        else:
            click.echo(release)


@releases.command(name="latest")
def releases_latest():
    """Show the latest Overture Maps release."""
    latest = get_latest_release()
    click.secho(latest, fg="cyan", bold=True)


@cli.group()
def changelog():
    """Query the GERS changelog for feature changes."""
    pass


@changelog.command(name="query")
@click.option("--bbox", required=True, type=BboxParamType())
@click.option("--theme", required=False, type=str)
@click.option("--type", "type_", required=False, type=str)
@click.option(
    "-r",
    "--release",
    default=None,
    callback=validate_release,
    required=False,
    help="Release version (defaults to latest)",
)
def changelog_query(bbox, theme, type_, release):
    """Query changelog for changes within a bounding box.

    Examples:
        botmap changelog query --bbox=-97.8,30.2,-97.6,30.4 --theme=buildings --type=building
        botmap changelog query --bbox=-97.8,30.2,-97.6,30.4 --theme=buildings
    """
    bbox_obj = BBox(xmin=bbox[0], ymin=bbox[1], xmax=bbox[2], ymax=bbox[3])

    if theme and type_:
        if type_ not in type_theme_map:
            raise click.BadParameter(f"Unknown type '{type_}'", param_hint="--type")
        themes_types = [(theme, type_)]
    elif theme:
        types = [t for t, th in type_theme_map.items() if th == theme]
        themes_types = [(theme, t) for t in types]
    elif type_:
        if type_ not in type_theme_map:
            raise click.BadParameter(f"Unknown type '{type_}'", param_hint="type")
        theme = type_theme_map[type_]
        themes_types = [(theme, type_)]
    else:
        raise click.UsageError("Must specify at least --theme or --type")

    total_added = 0
    total_modified = 0
    total_deleted = 0

    click.secho(f"Querying changelog for release {release}...", fg="bright_black")
    click.echo()

    for theme_name, type_name in themes_types:
        changes = query_changelog_ids(release, theme_name, type_name, bbox_obj)

        added = len(changes.get("added", set()))
        modified = len(changes.get("data_changed", set()))
        deleted = len(changes.get("removed", set()))

        total_added += added
        total_modified += modified
        total_deleted += deleted

        if added + modified + deleted > 0:
            click.secho(f"{theme_name}/{type_name}:", bold=True)
            click.secho(f"  Added:    {added}", fg="green")
            click.secho(f"  Modified: {modified}", fg="yellow")
            click.secho(f"  Deleted:  {deleted}", fg="red")
            click.echo()

    if len(themes_types) > 1:
        click.secho("Total:", bold=True)
        click.secho(f"  Added:    {total_added}", fg="green", bold=True)
        click.secho(f"  Modified: {total_modified}", fg="yellow", bold=True)
        click.secho(f"  Deleted:  {total_deleted}", fg="red", bold=True)


@changelog.command(name="summary")
@click.option("--theme", required=False, type=str)
@click.option("--type", "type_", required=False, type=str)
@click.option(
    "-r",
    "--release",
    default=None,
    callback=validate_release,
    required=False,
    help="Release version (defaults to latest)",
)
def changelog_summary(theme, type_, release):
    """Get aggregate statistics for changelog without bbox filtering.

    Examples:
        botmap changelog summary --theme=buildings
        botmap changelog summary --type=building
        botmap changelog summary  # All themes/types
    """
    click.secho(f"Summarizing changelog for release {release}...", fg="bright_black")
    click.echo()

    try:
        results = summarize_changelog(release, theme, type_)
    except ValueError as e:
        raise click.BadParameter(str(e))

    grand_totals = {}

    for theme_name, types_data in results.items():
        for type_name, change_counts in types_data.items():
            click.secho(f"{theme_name}/{type_name}:", bold=True)
            for change_type, count in sorted(change_counts.items()):
                fg = {"added": "green", "data_changed": "yellow", "removed": "red"}.get(
                    change_type
                )
                click.secho(f"  {change_type}: {count}", fg=fg)
                grand_totals[change_type] = grand_totals.get(change_type, 0) + count
            click.echo()

    if len(results) > 1 or (len(results) == 1 and len(list(results.values())[0]) > 1):
        click.secho("Grand Total:", bold=True)
        for change_type, count in sorted(grand_totals.items()):
            fg = {"added": "green", "data_changed": "yellow", "removed": "red"}.get(
                change_type
            )
            click.secho(f"  {change_type}: {count}", fg=fg, bold=True)


@releases.command(name="check")
@click.option("-o", "--output", required=True, type=click.Path(exists=True))
@click.pass_context
def releases_check(ctx, output):
    """Check if a local file is up to date with the latest release."""
    state_path = get_state_path(output)
    state = load_state(state_path)

    if state is None:
        click.secho(f"No state file found at {state_path}", fg="red", err=True)
        click.secho("Cannot determine current release version.", fg="red", err=True)
        ctx.exit(1)

    latest = get_latest_release()

    click.echo(
        "Current release: " + click.style(state.last_release, fg="cyan", bold=True)
    )
    click.echo("Latest release:  " + click.style(latest, fg="cyan", bold=True))

    if state.last_release == latest:
        click.secho("✓ Up to date", fg="green", bold=True)
        ctx.exit(0)
    else:
        click.secho("✗ Update available", fg="yellow", bold=True)
        ctx.exit(1)


@releases.command(name="exists")
@click.argument("release")
def releases_exists(release):
    """Check whether a release exists."""
    if not release_exists(release):
        raise click.ClickException(f"Release '{release}' not found")
    click.secho("true", fg="green")


if __name__ == "__main__":
    cli()
