"""CLI entry point for Vertical Sync."""

import json
import sys
from pathlib import Path

import click

from .config import FIT_DIR, PLAN_WEEKS, RACE, get_plan_week
from .fit_parser import (
    analyze_activity,
    compute_gradient_profile,
    compute_week_summary,
    enrich_records,
    find_fit_files,
    format_duration,
    is_quality_session,
    merge_fit_data,
    parse_fit,
)
from .fit_parser import _session_start
from .analysis import assess_activity, assess_week


def parse_date(value: str) -> int:
    """Accept YYYYMMDD or YYYY-MM-DD, return int YYYYMMDD."""
    return int(value.replace("-", ""))


def safe_filename_part(name: str) -> str:
    """Sanitize an activity name for use in a filename (Windows-safe)."""
    import re

    return re.sub(r'[<>:"/\\|?*]', "-", name.replace(" ", "_"))


def _race_label() -> str:
    """Human-readable race goal from the athlete config (e.g. for headers)."""
    parts = []
    if RACE.get("date"):
        parts.append(str(RACE["date"]))
    detail = []
    if RACE.get("distance_km"):
        detail.append(f"{RACE['distance_km']}km")
    if RACE.get("ascent_m"):
        detail.append(f"{RACE['ascent_m']}m D+")
    if detail:
        parts.append(" / ".join(detail))
    if RACE.get("name"):
        parts.append(RACE["name"])
    return " — ".join(parts) if parts else "no race configured"


# ---------------------------------------------------------------------------
# CLI root
# ---------------------------------------------------------------------------

@click.group()
@click.version_option(package_name="vertical-sync")
def cli():
    """Vertical Sync — Trail running training analysis.

    Analyze FIT files day by day, identify strengths/weaknesses,
    and adapt your training plan.

    Use --json on any analysis command for structured AI-readable output.
    """


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------

GARMIN_TOKENSTORE = "~/.garminconnect"


def _garmin_client():
    """Login to Garmin Connect, reusing cached tokens when available."""
    import os

    from dotenv import load_dotenv
    from garminconnect import Garmin

    load_dotenv()
    client = Garmin(
        email=os.environ["GARMIN_EMAIL"],
        password=os.environ["GARMIN_PASSWORD"],
        prompt_mfa=lambda: click.prompt("Garmin MFA code"),
    )
    client.login(GARMIN_TOKENSTORE)
    return client


def _renpho_client():
    """Login to Renpho via its unofficial cloud API.

    Renpho has no official API; this uses the reverse-engineered endpoints from
    the `renpho-api` package. Credentials come from RENPHO_EMAIL / RENPHO_PASSWORD
    in the environment (.env), never hardcoded — same pattern as the Garmin client.
    """
    import os

    from dotenv import load_dotenv
    from renpho import RenphoClient

    load_dotenv()
    try:
        email = os.environ["RENPHO_EMAIL"]
        password = os.environ["RENPHO_PASSWORD"]
    except KeyError as missing:
        click.echo(
            f"Missing {missing} — add RENPHO_EMAIL and RENPHO_PASSWORD to .env",
            err=True,
        )
        sys.exit(1)
    client = RenphoClient(email=email, password=password)
    client.login()
    return client


@cli.command()
def login():
    """Test the Garmin Connect login."""
    client = _garmin_client()
    click.echo(f"Login successful! Garmin user: {client.get_full_name()}")


# ---------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------

def _download_garmin(start_d: int, end_d: int, as_json: bool) -> list[str]:
    """Download all activity FITs from Garmin Connect. Returns filenames."""
    import io
    import zipfile

    from garminconnect import Garmin

    client = _garmin_client()
    to_iso = lambda d: f"{d // 10000:04d}-{d % 10000 // 100:02d}-{d % 100:02d}"
    activities = client.get_activities_by_date(to_iso(start_d), to_iso(end_d))

    downloaded = []
    for a in activities:
        # ORIGINAL format is a zip wrapping the on-watch .fit file
        raw = client.download_activity(
            str(a["activityId"]), dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL
        )
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            fit_members = [n for n in zf.namelist() if n.lower().endswith(".fit")]
            if not fit_members:
                if not as_json:
                    click.echo(f"[SKIP] No FIT for {a.get('activityName')}", err=True)
                continue
            content = zf.read(fit_members[0])

        date = a["startTimeLocal"][:10].replace("-", "")
        name = safe_filename_part(a.get("activityName") or "activity")
        filename = f"{date}_{name}_{a['activityId']}.fit"
        (FIT_DIR / filename).write_bytes(content)
        downloaded.append(filename)
        if not as_json:
            click.echo(f"[OK] {filename}")

    return downloaded


@cli.command()
@click.option("--start", required=True, help="Start date (YYYYMMDD or YYYY-MM-DD)")
@click.option("--end", required=True, help="End date (YYYYMMDD or YYYY-MM-DD)")
@click.option("--json", "as_json", is_flag=True, help="JSON output")
def download(start, end, as_json):
    """Download all activity FIT files for a date range from Garmin Connect."""
    start_d, end_d = parse_date(start), parse_date(end)
    FIT_DIR.mkdir(parents=True, exist_ok=True)

    downloaded = _download_garmin(start_d, end_d, as_json)

    if as_json:
        click.echo(json.dumps({"downloaded": downloaded, "count": len(downloaded)}))
    else:
        click.echo(f"\nDownloaded {len(downloaded)} file(s) to {FIT_DIR}")


# ---------------------------------------------------------------------------
# recovery (resting HR + overnight HRV from Garmin wellness data)
# ---------------------------------------------------------------------------

def _rhr_value(client, day: str) -> int | None:
    """Daily resting heart rate (bpm) for an ISO date, or None if unavailable."""
    try:
        data = client.get_rhr_day(day) or {}
        arr = (data.get("allMetrics", {}).get("metricsMap", {})
               .get("WELLNESS_RESTING_HEART_RATE", []))
        val = arr[0].get("value") if arr else None
        return int(val) if val is not None else None
    except Exception:
        return None


def _hrv_value(client, day: str) -> tuple[int | None, str | None]:
    """Overnight HRV avg (ms) + Garmin status for an ISO date. HRV exists only
    on nights the watch was worn, so both may be None."""
    try:
        summary = (client.get_hrv_data(day) or {}).get("hrvSummary") or {}
        avg = summary.get("lastNightAvg")
        return (int(avg) if avg is not None else None), summary.get("status")
    except Exception:
        return None, None


@cli.command()
@click.option("--start", default=None, help="Start date (YYYYMMDD or YYYY-MM-DD)")
@click.option("--end", default=None, help="End date (YYYYMMDD or YYYY-MM-DD)")
@click.option("--json", "as_json", is_flag=True, help="JSON output")
def recovery(start, end, as_json):
    """Resting HR + overnight HRV history from Garmin Connect (default: last 14 days).

    RHR is recorded daily; HRV only on nights the watch was worn. Both are *raw*
    signals: interpret them against lifestyle confounds — a late night or alcohol
    inflates RHR and suppresses HRV independently of training load, so an isolated
    spike is not necessarily accumulated fatigue.
    """
    from datetime import date, datetime, timedelta

    end_d = (datetime.strptime(str(parse_date(end)), "%Y%m%d").date()
             if end else date.today())
    start_d = (datetime.strptime(str(parse_date(start)), "%Y%m%d").date()
               if start else end_d - timedelta(days=13))
    if start_d > end_d:
        click.echo("start must be on or before end.", err=True)
        sys.exit(1)

    client = _garmin_client()

    days = []
    d = start_d
    while d <= end_d:
        ds = d.isoformat()
        rhr = _rhr_value(client, ds)
        hrv, status = _hrv_value(client, ds)
        days.append({"date": ds, "rhr": rhr, "hrv": hrv, "hrv_status": status})
        d += timedelta(days=1)

    rhrs = sorted(x["rhr"] for x in days if x["rhr"] is not None)
    summary = {}
    if rhrs:
        n = len(rhrs)
        median = rhrs[n // 2] if n % 2 else (rhrs[n // 2 - 1] + rhrs[n // 2]) / 2
        summary = {
            "rhr_min": rhrs[0],
            "rhr_median": median,
            "rhr_max": rhrs[-1],
            "days_with_rhr": n,
            "days_with_hrv": sum(1 for x in days if x["hrv"] is not None),
        }

    if as_json:
        click.echo(json.dumps({"days": days, "summary": summary}, default=str, indent=2))
        return

    click.echo("\n  RECUPERATION - RHR (quotidien) + VFC (nuits montre)")
    click.echo(f"  {start_d.isoformat()} -> {end_d.isoformat()}")
    click.echo(f"  {'-' * 40}")
    click.echo(f"  {'Date':<12} {'RHR':>4} {'VFC':>5} {'Statut':<10}")
    for x in days:
        rhr = str(x["rhr"]) if x["rhr"] is not None else "-"
        hrv = str(x["hrv"]) if x["hrv"] is not None else "-"
        click.echo(f"  {x['date']:<12} {rhr:>4} {hrv:>5} {(x['hrv_status'] or ''):<10}")
    if summary:
        click.echo(f"  {'-' * 40}")
        click.echo(
            f"  RHR min {summary['rhr_min']} / median {summary['rhr_median']} / "
            f"max {summary['rhr_max']}  ({summary['days_with_rhr']}j RHR, "
            f"{summary['days_with_hrv']}j VFC)"
        )


# ---------------------------------------------------------------------------
# weight (body composition from a Renpho smart scale)
# ---------------------------------------------------------------------------

def _measurement_date(m: dict) -> str | None:
    """ISO date (YYYY-MM-DD) from a Renpho measurement timestamp (seconds or ms)."""
    from datetime import datetime

    ts = m.get("timeStamp") or m.get("time_stamp")
    if ts is None:
        return None
    ts = int(ts)
    if ts > 1e12:
        ts //= 1000
    return datetime.fromtimestamp(ts).date().isoformat()


def _weekly_aggregate(rows: list) -> list:
    """Group daily rows into ISO-week buckets (Monday start), mean per metric.

    A daily weigh-in fluctuates ±1 kg with water/glycogen/sodium; the weekly mean
    is the honest trend signal. Each week averages only the values present."""
    from collections import defaultdict
    from datetime import datetime, timedelta

    buckets: dict = defaultdict(list)
    for r in rows:
        d = datetime.strptime(r["date"], "%Y-%m-%d").date()
        monday = (d - timedelta(days=d.weekday())).isoformat()
        buckets[monday].append(r)

    def _mean(group, key):
        vals = [g[key] for g in group if g[key] is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    weeks = []
    for monday in sorted(buckets):
        g = buckets[monday]
        weeks.append({
            "week_start": monday,
            "n": len(g),
            "weight_kg": _mean(g, "weight_kg"),
            "bodyfat_pct": _mean(g, "bodyfat_pct"),
            "muscle_pct": _mean(g, "muscle_pct"),
            "lean_mass_kg": _mean(g, "lean_mass_kg"),
            "fat_mass_kg": _mean(g, "fat_mass_kg"),
        })
    return weeks


@cli.command()
@click.option("--start", default=None, help="Start date (YYYYMMDD or YYYY-MM-DD)")
@click.option("--end", default=None, help="End date (YYYYMMDD or YYYY-MM-DD)")
@click.option("--weekly", is_flag=True,
              help="Aggregate by ISO week (mean per week) — smooths daily noise")
@click.option("--json", "as_json", is_flag=True, help="JSON output")
def weight(start, end, weekly, as_json):
    """Weight + body composition from a Renpho smart scale (default: last 90 days).

    Pulls via the unofficial Renpho cloud API (needs RENPHO_EMAIL / RENPHO_PASSWORD
    in .env). Consumer bioimpedance body-fat / muscle % are reliable as a *trend*
    on the same scale and conditions, not as absolute values — read the direction,
    not the exact number. Lean mass (kg) is the best muscle-preservation signal in
    a deficit. Use --weekly for the honest trend (means daily noise away).
    """
    from datetime import date, datetime, timedelta

    end_d = (datetime.strptime(str(parse_date(end)), "%Y%m%d").date()
             if end else date.today())
    start_d = (datetime.strptime(str(parse_date(start)), "%Y%m%d").date()
               if start else end_d - timedelta(days=90))
    if start_d > end_d:
        click.echo("start must be on or before end.", err=True)
        sys.exit(1)

    client = _renpho_client()
    raw = client.get_all_measurements()

    # Measurements come newest-first: keep the first seen per day (the latest of
    # that day), filtered to the window, then re-sort oldest -> newest for reading.
    seen: set = set()
    rows = []
    for m in raw:
        ds = _measurement_date(m)
        if ds is None or ds in seen:
            continue
        d = datetime.strptime(ds, "%Y-%m-%d").date()
        if not (start_d <= d <= end_d):
            continue
        seen.add(ds)
        w = m.get("weight")
        bf = m.get("bodyfat")
        rows.append({
            "date": ds,
            "weight_kg": round(w, 1) if w else None,
            "bodyfat_pct": round(bf, 1) if bf else None,
            "muscle_pct": round(m["muscle"], 1) if m.get("muscle") else None,
            "lean_mass_kg": round(m["sinew"], 1) if m.get("sinew") else None,
            "fat_mass_kg": round(w * bf / 100, 1) if (w and bf) else None,
        })
    rows.sort(key=lambda r: r["date"])

    display = _weekly_aggregate(rows) if weekly else rows
    date_key = "week_start" if weekly else "date"

    summary = {}
    pts = [x for x in display if x["weight_kg"] is not None]
    if len(pts) >= 2:
        first, last = pts[0], pts[-1]
        span = (datetime.strptime(last[date_key], "%Y-%m-%d").date()
                - datetime.strptime(first[date_key], "%Y-%m-%d").date()).days or 1
        dw = round(last["weight_kg"] - first["weight_kg"], 1)
        summary = {
            "first": first[date_key],
            "last": last[date_key],
            "weight_delta_kg": dw,
            "weight_per_month_kg": round(dw / span * 30, 2),
            "points": len(pts),
        }
        if first["fat_mass_kg"] is not None and last["fat_mass_kg"] is not None:
            summary["fat_mass_delta_kg"] = round(
                last["fat_mass_kg"] - first["fat_mass_kg"], 1)
        if first["lean_mass_kg"] is not None and last["lean_mass_kg"] is not None:
            summary["lean_mass_delta_kg"] = round(
                last["lean_mass_kg"] - first["lean_mass_kg"], 1)

    if as_json:
        key = "weeks" if weekly else "measurements"
        click.echo(json.dumps(
            {key: display, "summary": summary}, default=str, indent=2))
        return

    header = "POIDS + COMPOSITION (Renpho)" + (" — moyennes hebdo" if weekly else "")
    click.echo(f"\n  {header}")
    click.echo(f"  {start_d.isoformat()} -> {end_d.isoformat()}")
    click.echo(f"  {'-' * 58}")
    label = "Semaine" if weekly else "Date"
    click.echo(f"  {label:<12}{' n' if weekly else '':>3} {'Poids':>6} {'%MG':>5} "
               f"{'%Mus':>5} {'Maigre':>7} {'MG kg':>6}")
    for r in display:
        def _f(v):
            return str(v) if v is not None else "-"
        n_col = f"{r['n']:>3}" if weekly else "   "
        click.echo(
            f"  {r[date_key]:<12}{n_col} {_f(r['weight_kg']):>6} "
            f"{_f(r['bodyfat_pct']):>5} {_f(r['muscle_pct']):>5} "
            f"{_f(r['lean_mass_kg']):>7} {_f(r['fat_mass_kg']):>6}"
        )
    if summary:
        click.echo(f"  {'-' * 58}")
        click.echo(
            f"  Delta poids {summary['weight_delta_kg']:+g} kg "
            f"({summary['weight_per_month_kg']:+g}/mois) "
            f"sur {summary['first']} -> {summary['last']}"
        )
        extra = []
        if "fat_mass_delta_kg" in summary:
            extra.append(f"graisse {summary['fat_mass_delta_kg']:+g} kg")
        if "lean_mass_delta_kg" in summary:
            extra.append(f"masse maigre {summary['lean_mass_delta_kg']:+g} kg")
        if extra:
            click.echo("  Delta " + " / ".join(extra))
    elif not display:
        click.echo("  Aucune mesure sur la periode.")


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

@cli.command("list")
@click.option("--start", default=None, help="Start date (YYYYMMDD or YYYY-MM-DD)")
@click.option("--end", default=None, help="End date (YYYYMMDD or YYYY-MM-DD)")
@click.option("--json", "as_json", is_flag=True, help="JSON output")
def list_files(start, end, as_json):
    """List available FIT files."""
    s = parse_date(start) if start else None
    e = parse_date(end) if end else None
    files = find_fit_files(s, e)

    if as_json:
        items = [{"filename": f.name, "path": str(f)} for f in files]
        click.echo(json.dumps({"files": items, "count": len(items)}, indent=2))
    else:
        if not files:
            click.echo("No FIT files found.")
            return
        click.echo(f"{len(files)} FIT file(s):")
        for f in files:
            click.echo(f"  {f.name}")


# ---------------------------------------------------------------------------
# analyze (single activity)
# ---------------------------------------------------------------------------

def _resolve_targets(targets: tuple[str, ...]) -> list[Path]:
    """Resolve analyze targets (file paths or dates) to FIT file paths.

    A file path resolves to itself; a date resolves to every FIT file that day.
    Deduped, preserving first-seen order."""
    paths: list[Path] = []
    for t in targets:
        p = Path(t)
        if p.exists():
            paths.append(p)
            continue
        date_int = parse_date(t)
        found = find_fit_files(start=date_int, end=date_int)
        if not found:
            click.echo(f"No FIT file found for {t}", err=True)
            sys.exit(1)
        paths.extend(found)
    seen, unique = set(), []
    for p in paths:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


@cli.command()
@click.argument("targets", nargs=-1, required=True)
@click.option("--merge", "do_merge", is_flag=True,
              help="Merge all resolved files into one activity (a run split "
                   "across files, e.g. the watch stopped mid-session)")
@click.option("--json", "as_json", is_flag=True, help="JSON output")
def analyze(targets, do_merge, as_json):
    """Analyze an activity.

    TARGETS is one or more dates (YYYYMMDD or YYYY-MM-DD) or file paths. With
    --merge, all resolved files are stitched into a single continuous activity
    (for a run split into multiple files); otherwise the first is analyzed.
    """
    paths = _resolve_targets(targets)

    if do_merge:
        parsed = [(p, parse_fit(p)) for p in paths]
        # Order chronologically so distance/time stitch correctly.
        parsed.sort(key=lambda pp: _session_start(pp[1]) or pp[0].name)
        merged = merge_fit_data([pp[1] for pp in parsed])
        # Name after the first part; note how many were merged.
        name = parsed[0][0].name
        metrics = analyze_activity(merged, name)
        if metrics:
            metrics["merged_from"] = [pp[0].name for pp in parsed]
    else:
        path = paths[0]
        metrics = analyze_activity(parse_fit(path), path.name)

    if not metrics:
        click.echo("No session data in this FIT file.", err=True)
        sys.exit(1)

    assessment = assess_activity(metrics)

    if as_json:
        click.echo(json.dumps({"activity": metrics, "assessment": assessment}, default=str, indent=2))
    else:
        if metrics.get("merged_from"):
            click.echo(f"  [merged {len(metrics['merged_from'])} files: "
                       f"{', '.join(metrics['merged_from'])}]")
        _print_activity(metrics)
        if assessment:
            click.echo("")
            _print_assessment(assessment)


# ---------------------------------------------------------------------------
# week
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--start", required=True, help="Week start date (YYYYMMDD or YYYY-MM-DD)")
@click.option("--end", required=True, help="Week end date (YYYYMMDD or YYYY-MM-DD)")
@click.option("--json", "as_json", is_flag=True, help="JSON output")
def week(start, end, as_json):
    """Analyze a full week of training with summary and assessment."""
    start_d, end_d = parse_date(start), parse_date(end)
    files = find_fit_files(start_d, end_d)

    if not files:
        click.echo(f"No FIT files for {start}..{end}", err=True)
        sys.exit(1)

    activities = []
    for f in files:
        data = parse_fit(f)
        m = analyze_activity(data, f.name)
        if m:
            activities.append(m)

    summary = compute_week_summary(activities)
    act_assessments = [{"date": a["date"], "assessment": assess_activity(a)} for a in activities]
    week_obs = assess_week(summary, activities, start_d)

    if as_json:
        click.echo(json.dumps({
            "period": {"start": start_d, "end": end_d},
            "plan_week": get_plan_week(start_d),
            "summary": summary,
            "activities": activities,
            "activity_assessments": act_assessments,
            "week_assessment": week_obs,
        }, default=str, indent=2))
    else:
        for a in activities:
            _print_activity(a)

        _print_week_summary(summary, start_d)

        for aa in act_assessments:
            if aa["assessment"]:
                click.echo(f"\n  [{aa['date']}]")
                _print_assessment(aa["assessment"], indent=4)

        if week_obs:
            click.echo(f"\n{'─' * 60}")
            click.echo("  BILAN HEBDOMADAIRE")
            click.echo(f"{'─' * 60}")
            _print_assessment(week_obs)


# ---------------------------------------------------------------------------
# assess
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--start", default=None, help="Start date (YYYYMMDD or YYYY-MM-DD)")
@click.option("--end", default=None, help="End date (YYYYMMDD or YYYY-MM-DD)")
@click.option("--json", "as_json", is_flag=True, help="JSON output")
def assess(start, end, as_json):
    """Identify strengths and weaknesses from training data.

    Without --start/--end, analyzes all available FIT files.
    """
    s = parse_date(start) if start else None
    e = parse_date(end) if end else None
    files = find_fit_files(s, e)

    if not files:
        click.echo("No FIT files found.", err=True)
        sys.exit(1)

    activities = []
    for f in files:
        data = parse_fit(f)
        m = analyze_activity(data, f.name)
        if m:
            activities.append(m)

    all_assessments = []
    for a in activities:
        obs = assess_activity(a)
        if obs:
            all_assessments.append({
                "date": a["date"],
                "filename": a["filename"],
                "observations": obs,
            })

    summary = compute_week_summary(activities)
    week_obs = assess_week(summary, activities, s or 0)

    if as_json:
        click.echo(json.dumps({
            "summary": summary,
            "activities": all_assessments,
            "global": week_obs,
        }, default=str, indent=2))
    else:
        for aa in all_assessments:
            click.echo(f"\n{'=' * 55}")
            click.echo(f"  {aa['date']} — {aa['filename']}")
            click.echo(f"{'=' * 55}")
            _print_assessment(aa["observations"])

        if week_obs:
            click.echo(f"\n{'#' * 55}")
            click.echo("  BILAN GLOBAL")
            click.echo(f"{'#' * 55}")
            _print_assessment(week_obs)


# ---------------------------------------------------------------------------
# profile (gradient performance)
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("target")
@click.option("--json", "as_json", is_flag=True, help="JSON output")
def profile(target, as_json):
    """Show gradient performance profile for an activity.

    TARGET can be a date (YYYYMMDD or YYYY-MM-DD) or a file path.
    Buckets records by slope gradient and shows pace, GAP, and HR per bucket.
    """
    import pandas as pd

    path = Path(target)
    if not path.exists():
        date_int = parse_date(target)
        files = find_fit_files(start=date_int, end=date_int)
        if not files:
            click.echo(f"No FIT file found for {target}", err=True)
            sys.exit(1)
        path = files[0]

    fit_data = parse_fit(path)
    sport = str(fit_data["sessions"][0].get("sport") or "unknown") if fit_data["sessions"] else "unknown"
    is_cycling = sport == "cycling"

    records_df = pd.DataFrame(fit_data["records"])
    enriched = enrich_records(records_df)
    grad_profile = compute_gradient_profile(enriched)

    if not grad_profile:
        click.echo("Not enough data to compute gradient profile.", err=True)
        sys.exit(1)

    if is_cycling:
        # GAP is a running energy-cost model (Minetti) and isn't valid for cycling.
        for b in grad_profile:
            b["avg_gap"] = "N/A"

    if as_json:
        click.echo(json.dumps({
            "filename": path.name,
            "sport": sport,
            "gradient_profile": grad_profile,
        }, indent=2))
    else:
        click.echo(f"\n  PROFIL PAR GRADIENT — {path.name}")
        if is_cycling:
            click.echo("  (velo detecte — GAP non calcule, modele Minetti specifique a la course a pied)")
        click.echo(f"  {'─' * 68}")
        click.echo(
            f"  {'Pente':<14} {'Allure':>8} {'GAP':>8} {'FC':>5} "
            f"{'Temps':>8} {'Dist':>7}"
        )
        click.echo(f"  {'─' * 68}")
        for b in grad_profile:
            hr_str = f"{b['avg_hr']}" if b["avg_hr"] else "—"
            click.echo(
                f"  {b['gradient_range']:<14} {b['avg_pace']:>7}/km "
                f"{b['avg_gap']:>7}/km {hr_str:>5} "
                f"{b['time']:>8} {b['distance_m']:>6}m"
            )


# ---------------------------------------------------------------------------
# efficiency (GAP/HR trend over easy footing runs)
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--start", default=None, help="Start date (YYYYMMDD or YYYY-MM-DD)")
@click.option("--end", default=None, help="End date (YYYYMMDD or YYYY-MM-DD)")
@click.option("--min-km", type=float, default=6.0, show_default=True, help="Min distance (km)")
@click.option("--max-km", type=float, default=20.0, show_default=True, help="Max distance (km)")
@click.option("--json", "as_json", is_flag=True, help="JSON output")
def efficiency(start, end, min_km, max_km, as_json):
    """Efficiency factor (GAP/HR) trend over easy footing runs.

    Keeps only running activities in the [min-km, max-km] range and drops
    threshold / interval sessions, so the trend reflects aerobic efficiency
    on comparable footings rather than workout intensity.
    """
    from collections import defaultdict

    s = parse_date(start) if start else None
    e = parse_date(end) if end else None
    files = find_fit_files(s, e)
    if not files:
        click.echo("No FIT files found.", err=True)
        sys.exit(1)

    runs, excluded = [], 0
    for f in files:
        data = parse_fit(f)
        m = analyze_activity(data, f.name)
        if not m or m["sport"] != "running":
            continue
        if not (min_km <= m["distance_km"] <= max_km):
            continue
        if is_quality_session(m):
            excluded += 1
            continue
        if m.get("efficiency_factor") is None:
            continue
        runs.append({
            "date": m["date"],
            "efficiency_factor": m["efficiency_factor"],
            "distance_km": m["distance_km"],
            "avg_hr": m["avg_hr"],
            "avg_gap": m["avg_gap"],
            "filename": m["filename"],
        })

    runs.sort(key=lambda r: r["date"])

    buckets = defaultdict(list)
    for r in runs:
        buckets[r["date"][:7]].append(r["efficiency_factor"])
    monthly = [
        {"month": mo, "runs": len(v), "avg_ef": round(sum(v) / len(v), 3)}
        for mo, v in sorted(buckets.items())
    ]

    summary = {}
    if monthly:
        first, last = monthly[0], monthly[-1]
        summary = {
            "runs": len(runs),
            "excluded_quality": excluded,
            "first_month": first["month"],
            "first_avg_ef": first["avg_ef"],
            "last_month": last["month"],
            "last_avg_ef": last["avg_ef"],
            "change_pct": round((last["avg_ef"] - first["avg_ef"]) / first["avg_ef"] * 100, 1)
            if first["avg_ef"] else 0,
        }

    if as_json:
        click.echo(json.dumps(
            {"runs": runs, "monthly": monthly, "summary": summary}, default=str, indent=2))
        return

    if not runs:
        click.echo("No footing runs matched the filters.", err=True)
        sys.exit(1)

    click.echo(f"\n  EFFICIENCY FACTOR (GAP/FC) — footings {min_km:.0f}-{max_km:.0f} km")
    click.echo(f"  {len(runs)} sortie(s) retenue(s), {excluded} seance(s) seuil/fractionne exclue(s)")
    click.echo(f"  {'-' * 52}")
    click.echo(f"  {'Mois':<9} {'EF moy':>7} {'Sorties':>9}")
    for mo in monthly:
        click.echo(f"  {mo['month']:<9} {mo['avg_ef']:>7.3f} {mo['runs']:>9}")

    if summary:
        arrow = "+" if summary["change_pct"] >= 0 else ""
        click.echo(f"  {'-' * 52}")
        click.echo(
            f"  Tendance: {summary['first_avg_ef']:.3f} ({summary['first_month']}) "
            f"-> {summary['last_avg_ef']:.3f} ({summary['last_month']}) "
            f"[{arrow}{summary['change_pct']:.1f}%]"
        )


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--week", "week_num", type=int, default=None, help="Show a specific week (1-9)")
@click.option("--json", "as_json", is_flag=True, help="JSON output")
def plan(week_num, as_json):
    """Show training plan targets.

    Reads the plan from coach/plan/*.md. Without --week, shows the full overview.
    """
    if not PLAN_WEEKS:
        msg = "No plan configured. Add weekly files under coach/plan/ (see templates/plan-week.md)."
        if as_json:
            click.echo(json.dumps({"weeks": [], "race": _race_label()}))
        else:
            click.echo(msg, err=True)
        return

    if week_num:
        pw = next((w for w in PLAN_WEEKS if w["week"] == week_num), None)
        if not pw:
            click.echo(f"No week {week_num} in plan.", err=True)
            sys.exit(1)
        data = pw
    else:
        data = {"weeks": PLAN_WEEKS, "race": _race_label()}

    if as_json:
        click.echo(json.dumps(data, indent=2))
    else:
        if week_num:
            pw = data
            click.echo(f"\n  Semaine {pw['week']} — {pw['phase']}")
            click.echo(f"  {pw['start']} → {pw['end']}")
            click.echo(f"  Volume:   {pw['target_hours']:.1f}h")
            click.echo(f"  D+:       {pw['target_dplus']}m")
            click.echo(f"  Seances:  {pw['target_sessions']}")
        else:
            click.echo(f"\n  PLAN {len(PLAN_WEEKS)} SEMAINES — {_race_label()}")
            click.echo(f"  {'─' * 52}")
            click.echo(f"  {'Sem':<4} {'Phase':<17} {'Debut':>10} {'Heures':>7} {'D+':>6} {'Seances':>8}")
            click.echo(f"  {'─' * 52}")
            for pw in PLAN_WEEKS:
                click.echo(
                    f"  {pw['week']:<4} {pw['phase']:<17} {pw['start']:>10} "
                    f"{pw['target_hours']:>6.1f}h {pw['target_dplus']:>5}m {pw['target_sessions']:>7}"
                )


# ---------------------------------------------------------------------------
# pdf
# ---------------------------------------------------------------------------

_PLAN_CSS = """
      @page { size: A4 portrait; margin: 1cm; }
      body { font-family: -apple-system, "Helvetica Neue", Arial, sans-serif;
             font-size: 9.5pt; line-height: 1.4; color: #1a1a1a; }
      h1 { font-size: 16pt; border-bottom: 2px solid #2d5a27; color: #2d5a27; }
      h2 { font-size: 12pt; color: #2d5a27; page-break-after: avoid; }
      h2 + blockquote, h2 + blockquote + table { page-break-before: avoid; }
      table { width: 100%; border-collapse: collapse; font-size: 8.5pt; page-break-inside: avoid; }
      th { background-color: #2d5a27; color: white; padding: 4px 6px; text-align: left; }
      td { padding: 3px 6px; border-bottom: 1px solid #ddd; }
      tr:nth-child(even) td { background-color: #f5f5f5; }
      blockquote { padding: 4px 10px; border-left: 3px solid #2d5a27;
                   background-color: #f0f7ee; font-style: italic; font-size: 8.5pt; }
      blockquote p { margin: 0; }
      strong { color: #2d5a27; }
"""


def _find_chromium() -> str | None:
    """Locate a Chromium-family browser to render HTML -> PDF (no GTK needed).

    Prefers one on PATH, then common Windows/macOS install locations. Returns
    None if none is found — the caller then leaves the HTML for manual Ctrl+P."""
    import shutil

    for name in ("msedge", "chrome", "google-chrome", "chromium",
                 "chromium-browser", "brave"):
        found = shutil.which(name)
        if found:
            return found
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ]
    for path in candidates:
        if Path(path).exists():
            return path
    return None


@cli.command()
def pdf():
    """Generate the training plan as HTML + PDF from the coach/plan/*.md files.

    Markdown -> styled HTML (pure Python), then HTML -> PDF via a headless
    Chromium browser (Edge/Chrome) that Windows/macOS already ship — no pandoc,
    no WeasyPrint/GTK. If no browser is found the HTML is still written; open it
    and use Ctrl+P -> Save as PDF (A4)."""
    import subprocess
    import tempfile

    import markdown as md_lib

    from .config import COACH_DIR, load_plan_markdown

    plan_md = load_plan_markdown()
    if not plan_md.strip():
        click.echo("No plan to render. Add weekly files under coach/plan/.", err=True)
        sys.exit(1)

    COACH_DIR.mkdir(parents=True, exist_ok=True)
    body = md_lib.markdown(plan_md, extensions=["tables", "fenced_code", "sane_lists"])
    styled_html = (
        '<!doctype html>\n<html lang="fr"><head><meta charset="utf-8">\n'
        "<title>Plan</title>\n<style>" + _PLAN_CSS + "</style>\n</head><body>\n"
        + body + "\n</body></html>\n"
    )

    html_path = COACH_DIR / "plan.html"
    html_path.write_text(styled_html, encoding="utf-8")

    pdf_path = COACH_DIR / "plan.pdf"
    browser = _find_chromium()
    if not browser:
        click.echo(f"HTML generated: {html_path}")
        click.echo(
            "No Chromium browser found for automatic PDF rendering. "
            "Open the HTML and use Ctrl+P -> Save as PDF (A4).",
            err=True,
        )
        return

    with tempfile.TemporaryDirectory() as tmp:
        result = subprocess.run(
            [
                browser, "--headless=new", "--disable-gpu",
                "--no-pdf-header-footer", f"--user-data-dir={tmp}",
                f"--print-to-pdf={pdf_path}", html_path.as_uri(),
            ],
            capture_output=True, text=True,
        )
    if result.returncode != 0 or not pdf_path.exists():
        click.echo(f"HTML generated: {html_path}")
        click.echo(
            f"Automatic PDF rendering failed (exit {result.returncode}). "
            "Open the HTML and use Ctrl+P -> Save as PDF (A4).",
            err=True,
        )
        return

    click.echo(f"PDF generated: {pdf_path}")
    click.echo(f"HTML also written: {html_path}")


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _print_activity(m: dict):
    """Human-readable single activity output."""
    is_cycling = m.get("sport") == "cycling"
    click.echo(f"\n{'=' * 60}")
    click.echo(f"  {m['filename']}")
    click.echo(f"  {m['date']}" + (f" — {m['sport']}" if m.get("sport") else ""))
    click.echo(f"{'=' * 60}")
    click.echo(f"  Distance:      {m['distance_km']:.1f} km")
    click.echo(f"  Duree:         {m['duration']}")
    click.echo(f"  D+:            {m['ascent_m']} m")
    click.echo(f"  D-:            {m['descent_m']} m")
    if is_cycling:
        # Pace (min/km) and GAP (running energy-cost model) don't apply to cycling.
        click.echo(f"  Vitesse moy:   {m['avg_speed_kmh']} km/h")
    else:
        click.echo(f"  Allure moy:    {m['avg_pace']} /km")
        if m.get("avg_gap") and m["avg_gap"] != "N/A":
            click.echo(f"  GAP:           {m['avg_gap']} /km")
        if m.get("efficiency_factor") is not None:
            click.echo(f"  Eff. factor:   {m['efficiency_factor']} m/battement (GAP/FC)")
    click.echo(f"  FC moy:        {m['avg_hr']} bpm")
    click.echo(f"  FC max:        {m['max_hr']} bpm")
    click.echo(f"  Cadence:       {m['avg_cadence']} {'rpm' if is_cycling else 'spm'}")
    click.echo(f"  D+ horaire:    {m['ascent_rate_m_h']} m/h")
    click.echo(f"  Km-effort:     {m['km_effort']}")
    if m.get("elevation"):
        click.echo(f"  Altitude:      {m['elevation']['min']}m — {m['elevation']['max']}m")
    if m.get("cardiac_drift_pct") is not None:
        click.echo(f"  Derive card.:  {m['cardiac_drift_pct']:+.1f}%")
    if m.get("hr_zones"):
        click.echo("  Zones FC:")
        for z, info in m["hr_zones"].items():
            bar_len = int(info["pct"] / 5)
            bar = "\u2588" * bar_len + "\u2591" * (20 - bar_len)
            click.echo(f"    {z} {info['name']:<12} {bar} {info['pct']:5.1f}%")
    if m.get("laps"):
        click.echo(f"  Laps:")
        if is_cycling:
            click.echo(f"    {'#':<4} {'Dist':>6} {'Duree':>8} {'Vitesse':>9} {'FC':>4} {'D+':>5}")
            for lap in m["laps"]:
                click.echo(
                    f"    {lap['lap']:<4} {lap['distance_km']:>5.1f}k "
                    f"{lap['duration']:>8} {lap['speed_kmh']:>6.1f}km/h "
                    f"{lap['avg_hr']:>3} {lap['ascent_m']:>4}m"
                )
        else:
            click.echo(f"    {'#':<4} {'Dist':>6} {'Duree':>8} {'Allure':>8} {'FC':>4} {'D+':>5}")
            for lap in m["laps"]:
                click.echo(
                    f"    {lap['lap']:<4} {lap['distance_km']:>5.1f}k "
                    f"{lap['duration']:>8} {lap['pace']:>7}/km "
                    f"{lap['avg_hr']:>3} {lap['ascent_m']:>4}m"
                )


def _print_week_summary(summary: dict, start_date: int):
    """Human-readable weekly summary."""
    plan_w = get_plan_week(start_date)
    click.echo(f"\n{'━' * 60}")
    click.echo("  RESUME HEBDOMADAIRE")
    if plan_w:
        click.echo(f"  Semaine {plan_w['week']} — {plan_w['phase']}")
    click.echo(f"{'━' * 60}")
    click.echo(f"  Seances:       {summary['runs']}")
    click.echo(f"  Distance:      {summary['total_km']:.1f} km")
    click.echo(f"  D+:            {summary['total_dplus']} m")
    click.echo(f"  Temps total:   {summary['total_time']} ({summary['total_time_h']:.1f}h)")
    click.echo(f"  FC moyenne:    {summary['avg_hr']} bpm")
    click.echo(f"  Ratio vert.:   {summary['vertical_ratio']} m/km")
    click.echo(f"  Km-effort:     {summary['km_effort']}")

    if plan_w:
        click.echo(f"\n  vs Plan:")
        click.echo(f"    Temps:   {summary['total_time_h']:.1f}h / {plan_w['target_hours']:.1f}h")
        click.echo(f"    D+:      {summary['total_dplus']}m / {plan_w['target_dplus']}m")
        click.echo(f"    Seances: {summary['runs']} / {plan_w['target_sessions']}")


def _print_assessment(observations: list[dict], indent: int = 2):
    """Print colored assessment observations."""
    prefix = " " * indent
    icons = {"strength": "+", "weakness": "!", "info": "*"}
    colors = {"strength": "green", "weakness": "red", "info": "blue"}

    for obs in observations:
        icon = icons.get(obs["type"], "*")
        color = colors.get(obs["type"])
        click.echo(click.style(
            f"{prefix}[{icon}] [{obs['category']}] {obs['detail']}", fg=color,
        ))
        if obs.get("implication"):
            click.echo(f"{prefix}    -> {obs['implication']}")
