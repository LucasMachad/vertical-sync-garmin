"""Quality-session analysis: reps, targets and execution KPIs.

Works on Garmin structured workouts, where every lap is one workout step and
carries its ``intensity`` (warmup / active / recovery / rest / cooldown) and
``wkt_step_index``. The laps are trusted as-is: an ``active`` lap is a rep, and
reps that come from the same workout step form a set (2x8x30/30 -> 2 sets).

The pace target of a rep is read from its workout step (custom speed range).
When the step has no target ("open"), each rep is placed against the VDOT
training paces instead (nearest of M/T/I/R) — the LLM judges which was meant.

Pace comparisons use the raw lap pace on flat reps and the grade-adjusted pace
(GAP, from the lap's barometric net grade) on hilly ones, where raw pace says
nothing about the effort.
"""

import numpy as np
import pandas as pd

from .config import HR_MAX
from .fit_parser import FLAT_COST, compute_hr_zones, format_duration, minetti_cost
from .vdot import format_pace_s, training_paces


ACTIVE = "active"
HILLY_VERT_PCT = 2.0       # (D+ + D-) / distance above which a rep is compared on GAP
END_HR_WINDOW_S = 10       # HR at the end of a rep = mean of its last N seconds
HRR_MIN_RECOVERY_S = 90    # HR drop over 60 s only measured on recoveries this long
HRR_WINDOW_S = 60


def _pace_s(speed_ms: float | None) -> float | None:
    return 1000 / speed_ms if speed_ms and speed_ms > 0 else None


def _fmt(pace_s: float | None) -> str | None:
    return format_pace_s(pace_s) if pace_s else None


def _hr_range(lo, hi) -> tuple[int, int] | None:
    """Custom HR target bounds -> (low, high) bpm.

    Garmin Connect writes plain bpm (seen: 150-175 on a threshold step, and
    1-145 for "below 145") rather than the FIT spec's bpm+100 offset. Bounds
    <= 100 alongside a bpm high bound mean "no lower bound"; when both are
    <= 100 they are % of HR max."""
    if not hi:
        return None
    if hi > 100:
        return (int(lo) if lo and lo > 100 else 0), int(hi)
    if HR_MAX:
        return round((lo or 0) / 100 * HR_MAX), round(hi / 100 * HR_MAX)
    return None


def step_target(step: dict | None) -> dict | None:
    """Target of a workout step: a pace range or HR range, or None if open."""
    if not step:
        return None
    ttype = str(step.get("target_type") or "open")
    if ttype == "speed":
        lo = step.get("custom_target_speed_low") or step.get("custom_target_value_low")
        hi = step.get("custom_target_speed_high") or step.get("custom_target_value_high")
        if not lo or not hi:
            return None
        # Unresolved subfield -> raw mm/s.
        if hi > 50:
            lo, hi = lo / 1000, hi / 1000
        fast, slow = 1000 / hi, 1000 / lo
        return {"type": "pace", "fast_s_per_km": round(fast), "slow_s_per_km": round(slow),
                "range": f"{format_pace_s(fast)}-{format_pace_s(slow)}"}
    if ttype == "heart_rate":
        bounds = _hr_range(step.get("custom_target_heart_rate_low"),
                           step.get("custom_target_heart_rate_high"))
        if bounds:
            lo, hi = bounds
            return {"type": "hr", "low_bpm": lo, "high_bpm": hi,
                    "range": f"{lo}-{hi} bpm" if lo else f"<{hi} bpm"}
    return None


def _slice(df: pd.DataFrame, start, duration_s: float) -> pd.DataFrame:
    if "timestamp_dt" not in df.columns or start is None:
        return df.iloc[0:0]
    t0 = pd.Timestamp(start)
    t1 = t0 + pd.Timedelta(seconds=duration_s)
    return df[(df["timestamp_dt"] >= t0) & (df["timestamp_dt"] < t1)]


def _lap_base(lap: dict, recs: pd.DataFrame) -> dict:
    duration = lap.get("total_timer_time") or 0
    distance = lap.get("total_distance") or 0
    speed = lap.get("enhanced_avg_speed") or lap.get("avg_speed") or (distance / duration if duration else 0)

    # Slope from the lap's barometric D+/D-, not the per-record GPS grade: over a
    # 30-60 s rep that grade is noisy, and the convex Minetti cost turns noise
    # into a much-too-fast GAP. Net grade is exact on a steady hill and slightly
    # under-corrects a rolling rep — the safe side.
    ascent, descent = lap.get("total_ascent") or 0, lap.get("total_descent") or 0
    vert_pct = round((ascent + descent) / distance * 100, 1) if distance else None
    gap_speed = None
    if speed and distance:
        net_grade = (ascent - descent) / distance
        gap_speed = speed * minetti_cost(net_grade) / FLAT_COST

    end_hr = None
    if "heart_rate" in recs.columns and len(recs):
        last = recs[recs["timestamp_dt"] >= recs["timestamp_dt"].max() - pd.Timedelta(seconds=END_HR_WINDOW_S)]
        hr = last["heart_rate"].dropna()
        end_hr = round(float(hr.mean())) if len(hr) else None

    return {
        "duration_s": round(duration),
        "duration": format_duration(duration),
        "distance_m": round(distance),
        "pace_s_per_km": round(_pace_s(speed)) if _pace_s(speed) else None,
        "pace": _fmt(_pace_s(speed)),
        "gap_s_per_km": round(_pace_s(gap_speed)) if _pace_s(gap_speed) else None,
        "gap": _fmt(_pace_s(gap_speed)),
        "vert_pct": vert_pct,
        "avg_hr": lap.get("avg_heart_rate"),
        "max_hr": lap.get("max_heart_rate"),
        "end_hr": end_hr,
        "ascent_m": ascent,
        "descent_m": descent,
    }


def _compared_pace(rep: dict) -> tuple[float | None, str]:
    """Pace to judge a rep on: GAP if hilly, raw lap pace otherwise."""
    if rep["vert_pct"] is not None and rep["vert_pct"] >= HILLY_VERT_PCT and rep["gap_s_per_km"]:
        return rep["gap_s_per_km"], "gap"
    return rep["pace_s_per_km"], "pace"


def _against_target(rep: dict, target: dict | None, vdot_paces: dict | None) -> None:
    pace, basis = _compared_pace(rep)
    rep["compared_on"] = basis
    rep["target"] = target
    rep["in_target"] = None
    rep["target_delta_s_per_km"] = None
    rep["nearest_vdot_zone"] = None

    if target and target["type"] == "pace" and pace:
        if pace < target["fast_s_per_km"]:
            delta = pace - target["fast_s_per_km"]      # negative: too fast
        elif pace > target["slow_s_per_km"]:
            delta = pace - target["slow_s_per_km"]      # positive: too slow
        else:
            delta = 0
        rep["target_delta_s_per_km"] = round(delta)
        rep["in_target"] = delta == 0
    elif target and target["type"] == "hr" and rep["avg_hr"]:
        rep["in_target"] = target["low_bpm"] <= rep["avg_hr"] <= target["high_bpm"]
    elif vdot_paces and pace:
        refs = {k: vdot_paces[k]["s_per_km"] for k in ("M", "T", "I", "R")}
        zone = min(refs, key=lambda k: abs(refs[k] - pace))
        rep["nearest_vdot_zone"] = {"zone": zone, "zone_pace": format_pace_s(refs[zone]),
                                    "delta_s_per_km": round(pace - refs[zone])}


def _hr_recovery(recs: pd.DataFrame, duration_s: float) -> dict:
    """HR at the start of a recovery and its drop over the first 60 s."""
    if duration_s < HRR_MIN_RECOVERY_S or "heart_rate" not in recs.columns or not len(recs):
        return {"hr_start": None, "hr_drop_60s": None}
    t0 = recs["timestamp_dt"].min()
    hr = recs.set_index("timestamp_dt")["heart_rate"].dropna()
    start = hr[hr.index < t0 + pd.Timedelta(seconds=5)]
    at60 = hr[(hr.index >= t0 + pd.Timedelta(seconds=HRR_WINDOW_S - 3))
              & (hr.index <= t0 + pd.Timedelta(seconds=HRR_WINDOW_S + 3))]
    if not len(start) or not len(at60):
        return {"hr_start": None, "hr_drop_60s": None}
    return {"hr_start": round(float(start.mean())),
            "hr_drop_60s": round(float(start.mean() - at60.mean()))}


def _mean(values: list) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def _set_summary(reps: list[dict]) -> dict:
    """Execution KPIs over one set of reps (regularity, fade, HR cost)."""
    paces = [_compared_pace(r)[0] for r in reps]
    speeds = [1000 / p for p in paces if p]
    n = len(reps)
    # Fade compares the first and last reps (2 each when there are >= 4) — one
    # rep alone is too noisy on 30 s efforts.
    k = 2 if n >= 4 else 1
    first_speed = _mean([1000 / p for p in paces[:k] if p])
    last_speed = _mean([1000 / p for p in paces[-k:] if p])
    first_hr = _mean([r["end_hr"] for r in reps[:k]])
    last_hr = _mean([r["end_hr"] for r in reps[-k:]])
    with_target = [r for r in reps if r["in_target"] is not None]

    return {
        "reps": n,
        "work_time_s": sum(r["duration_s"] for r in reps),
        "mean_pace": _fmt(_mean(paces)),
        "pace_cv_pct": round(float(np.std(speeds) / np.mean(speeds) * 100), 1) if len(speeds) > 1 else None,
        "fade_pct": round((last_speed - first_speed) / first_speed * 100, 1)
        if n > 1 and first_speed and last_speed else None,
        "end_hr_drift_bpm": round(last_hr - first_hr) if n > 1 and first_hr and last_hr else None,
        "in_target": sum(1 for r in with_target if r["in_target"]) if with_target else None,
        "with_target": len(with_target) if with_target else None,
    }


def analyze_session(fit_data: dict, filename: str, vdot: float | None = None) -> dict | None:
    """Break a structured workout into warmup / sets of reps / recoveries /
    cooldown, with per-rep target compliance and per-set execution KPIs.
    Returns None if the file has no active laps (not a structured session)."""
    laps = fit_data.get("laps") or []
    if not any(str(l.get("intensity")) == ACTIVE for l in laps):
        return None

    records = pd.DataFrame(fit_data.get("records") or [])
    if "timestamp" in records.columns:
        records["timestamp_dt"] = pd.to_datetime(records["timestamp"])
    steps = {s.get("message_index"): s for s in fit_data.get("workout_steps") or []}
    vdot_paces = training_paces(vdot) if vdot else None

    reps, recoveries, other = [], [], []
    set_order: list = []
    prev_rep = None
    for lap in laps:
        intensity = str(lap.get("intensity") or "active")
        duration = lap.get("total_elapsed_time") or lap.get("total_timer_time") or 0
        recs = _slice(records, lap.get("start_time"), duration)
        row = _lap_base(lap, recs)
        step_idx = lap.get("wkt_step_index")

        if intensity == ACTIVE:
            if step_idx not in set_order:
                set_order.append(step_idx)
            row["n"] = len(reps) + 1
            row["set"] = set_order.index(step_idx) + 1
            _against_target(row, step_target(steps.get(step_idx)), vdot_paces)
            reps.append(row)
            prev_rep = row
        elif intensity in ("recovery", "rest"):
            row.update(_hr_recovery(recs, duration))
            row["after_rep"] = prev_rep["n"] if prev_rep else None
            recoveries.append(row)
        else:
            row["phase"] = intensity
            other.append(row)

    sets = []
    for i in range(1, len(set_order) + 1):
        summary = _set_summary([r for r in reps if r["set"] == i])
        summary["set"] = i
        sets.append(summary)

    drops = [r["hr_drop_60s"] for r in recoveries if r["hr_drop_60s"] is not None]
    session = (fit_data.get("sessions") or [{}])[0]
    duration = session.get("total_timer_time") or 0
    zones = compute_hr_zones(records["heart_rate"]) if "heart_rate" in records.columns else {}
    z45_pct = sum(zones.get(z, {}).get("pct", 0.0) for z in ("Z4", "Z5"))
    with_target = [r for r in reps if r["in_target"] is not None]

    return {
        "filename": filename,
        "workout": (fit_data.get("workout") or {}).get("wkt_name"),
        "vdot": round(vdot, 1) if vdot else None,
        "duration": format_duration(duration),
        "reps": reps,
        "recoveries": recoveries,
        "warmup_cooldown": other,
        "sets": sets,
        "summary": {
            "reps": len(reps),
            "sets": len(sets),
            "work_time_s": sum(r["duration_s"] for r in reps),
            "work_time": format_duration(sum(r["duration_s"] for r in reps)),
            "targets": "pace" if any(r["target"] and r["target"]["type"] == "pace" for r in reps)
            else "hr" if any(r["target"] for r in reps) else "open",
            "in_target": sum(1 for r in with_target if r["in_target"]) if with_target else None,
            "with_target": len(with_target) if with_target else None,
            "hr_drop_60s_first": drops[0] if drops else None,
            "hr_drop_60s_last": drops[-1] if drops else None,
            "hr_drop_60s_mean": round(_mean(drops)) if drops else None,
            "z4_z5_time_s": round(duration * z45_pct / 100),
            "z4_z5_pct": round(z45_pct, 1),
        },
    }
