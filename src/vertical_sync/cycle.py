"""Training-cycle tracking: weekly intensity distribution, quality volume and
progression across a block (e.g. a 10 km speed cycle).

Per ISO week (Monday start), running only:
- volume (runs, km, hours, D+) and the longest run (trail maintenance);
- intensity distribution: time in Z1-Z2 / Z3 / Z4-Z5 (from measured HR);
- quality volume: rep work time of structured sessions, bucketed by the VDOT
  training pace each rep was run at (M / T / I / R);
- EF (GAP/HR) of easy footings — the aerobic-economy signal.

Plus one row per quality session — pace, HR and EF on the reps, target
compliance, HR recovery — so like-for-like sessions can be compared over the
block. The CLI computes; judging the trend is the LLM's job.
"""

import datetime as dt
from pathlib import Path

from .fit_parser import analyze_activity, format_duration, is_quality_session, parse_fit
from .session import analyze_session
from .vdot import format_pace_s, training_paces


EASY_EF_KM = (6.0, 20.0)   # same footing window as `vs efficiency`
SLOWER_THAN_M_S = 15       # reps this much slower than M pace aren't "quality"
QUALITY_ZONES = ("R", "I", "T", "M")


def _week_start(date_int: int) -> dt.date:
    d = dt.datetime.strptime(str(date_int), "%Y%m%d").date()
    return d - dt.timedelta(days=d.weekday())


def _zone_of(pace_s: float, paces: dict) -> str | None:
    """VDOT zone a rep was run at: nearest of M/T/I/R, None if slower than M."""
    if pace_s > paces["M"]["s_per_km"] + SLOWER_THAN_M_S:
        return None
    return min(QUALITY_ZONES, key=lambda z: abs(paces[z]["s_per_km"] - pace_s))


def _rep_pace(rep: dict) -> float | None:
    return rep["gap_s_per_km"] if rep["compared_on"] == "gap" else rep["pace_s_per_km"]


def _quality_row(m: dict, s: dict) -> dict:
    """One quality session, summarised for comparison across the block."""
    reps = s["reps"]
    work = sum(r["duration_s"] for r in reps) or 1
    # Time-weighted mean pace (as speed) and HR over the reps.
    speed = sum(1000 / _rep_pace(r) * r["duration_s"] for r in reps if _rep_pace(r)) / work
    hrs = [(r["avg_hr"], r["duration_s"]) for r in reps if r["avg_hr"]]
    hr = sum(h * d for h, d in hrs) / sum(d for _, d in hrs) if hrs else None
    sm = s["summary"]
    return {
        "date": m["date"],
        "workout": s["workout"] or m["filename"],
        "reps": sm["reps"],
        "work_time": sm["work_time"],
        "work_time_s": sm["work_time_s"],
        "rep_pace": format_pace_s(1000 / speed) if speed else None,
        "rep_avg_hr": round(hr) if hr else None,
        # Same units as the footing EF (m per beat), on the reps only.
        "rep_ef": round(speed * 60 / hr, 3) if speed and hr else None,
        "targets": sm["targets"],
        "in_target": sm["in_target"],
        "with_target": sm["with_target"],
        "fade_pct": [st["fade_pct"] for st in s["sets"] if st["reps"] >= 3],
        "hr_drop_60s_mean": sm["hr_drop_60s_mean"],
    }


def compute_cycle(files: list[Path], vdot: float | None) -> dict:
    """Weekly cycle table + per-quality-session progression rows."""
    paces = training_paces(vdot) if vdot else None
    weeks: dict[dt.date, dict] = {}
    quality = []

    for f in files:
        data = parse_fit(f)
        m = analyze_activity(data, f.name)
        if not m or m["sport"] != "running" or not m["date_int"]:
            continue

        ws = _week_start(m["date_int"])
        w = weeks.setdefault(ws, {
            "runs": 0, "km": 0.0, "time_s": 0.0, "dplus": 0,
            "z12_s": 0.0, "z3_s": 0.0, "z45_s": 0.0,
            "long_run": None, "quality_sessions": 0,
            "quality_s": {z: 0 for z in QUALITY_ZONES}, "easy_ef": [],
        })
        w["runs"] += 1
        w["km"] += m["distance_km"]
        w["time_s"] += m["duration_s"]
        w["dplus"] += m["ascent_m"]

        zones = m.get("hr_zones") or {}
        pct = lambda *zs: sum(zones.get(z, {}).get("pct", 0.0) for z in zs) / 100
        w["z12_s"] += m["duration_s"] * pct("Z1", "Z2")
        w["z3_s"] += m["duration_s"] * pct("Z3")
        w["z45_s"] += m["duration_s"] * pct("Z4", "Z5")

        if not w["long_run"] or m["duration_s"] > w["long_run"]["duration_s"]:
            w["long_run"] = {"date": m["date"], "duration_s": m["duration_s"],
                             "duration": m["duration"], "km": m["distance_km"],
                             "dplus": m["ascent_m"]}

        s = analyze_session(data, f.name, vdot)
        if s:
            w["quality_sessions"] += 1
            quality.append(_quality_row(m, s))
            if paces:
                for r in s["reps"]:
                    pace = _rep_pace(r)
                    zone = _zone_of(pace, paces) if pace else None
                    if zone:
                        w["quality_s"][zone] += r["duration_s"]
        elif is_quality_session(m):
            # Hard run without workout laps: counted, reps not measurable.
            w["quality_sessions"] += 1
        elif (EASY_EF_KM[0] <= m["distance_km"] <= EASY_EF_KM[1]
              and m.get("efficiency_factor") is not None):
            w["easy_ef"].append(m["efficiency_factor"])

    rows = []
    for ws in sorted(weeks):
        w = weeks[ws]
        total = w["time_s"] or 1
        q_total = sum(w["quality_s"].values())
        rows.append({
            "week_start": ws.isoformat(),
            "runs": w["runs"],
            "km": round(w["km"], 1),
            "time": format_duration(w["time_s"]),
            "time_h": round(w["time_s"] / 3600, 2),
            "dplus": w["dplus"],
            "z12_pct": round(w["z12_s"] / total * 100, 1),
            "z3_pct": round(w["z3_s"] / total * 100, 1),
            "z45_pct": round(w["z45_s"] / total * 100, 1),
            "quality_sessions": w["quality_sessions"],
            "quality_time_s": q_total,
            "quality_time": format_duration(q_total),
            "quality_by_zone_s": dict(w["quality_s"]),
            "long_run": w["long_run"],
            "easy_ef": round(sum(w["easy_ef"]) / len(w["easy_ef"]), 3) if w["easy_ef"] else None,
            "easy_runs_ef": len(w["easy_ef"]),
        })

    return {
        "vdot": round(vdot, 1) if vdot else None,
        "paces": {z: format_pace_s(paces[z]["s_per_km"]) for z in QUALITY_ZONES} if paces else None,
        "weeks": rows,
        "quality_sessions": quality,
    }
