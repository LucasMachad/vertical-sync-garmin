"""VDOT (Jack Daniels) — race-time predictions and training pace zones.

Uses the Daniels–Gilbert equations:

- oxygen cost of running at velocity v (m/min):
  ``VO2 = -4.60 + 0.182258 v + 0.000104 v²``
- fraction of VO2max sustainable for a race lasting t minutes:
  ``%max = 0.8 + 0.1894393 e^(-0.012778 t) + 0.2989558 e^(-0.1932605 t)``
- ``VDOT = VO2 / %max`` for a race performance.

Training paces are the velocities whose oxygen cost is a fixed fraction of
VDOT (E, T, I), or predicted race paces (M = marathon, R = mile). Values match
Daniels' published tables within a few seconds per km.
"""

import math


# Fractions of VDOT for the training intensities (calibrated on Daniels' tables).
EASY_FRACTION = (0.62, 0.70)   # E — easy / long runs (range)
THRESHOLD_FRACTION = 0.88      # T — ~1 h race effort
INTERVAL_FRACTION = 0.975      # I — ~3-5 min reps at VO2max

MILE_M = 1609.344
MARATHON_M = 42195.0

# Distances for the prediction table: label -> metres.
PREDICTION_DISTANCES = {
    "1500m": 1500.0,
    "mile": MILE_M,
    "3k": 3000.0,
    "5k": 5000.0,
    "10k": 10000.0,
    "half": 21097.5,
    "marathon": MARATHON_M,
}

# Accepted --distance aliases (besides a plain number of km).
DISTANCE_ALIASES = {
    **PREDICTION_DISTANCES,
    "semi": 21097.5,
    "21k": 21097.5,
    "42k": MARATHON_M,
}


def vo2_cost(v_m_min: float) -> float:
    """Oxygen cost (ml/kg/min) of running at v metres per minute."""
    return -4.60 + 0.182258 * v_m_min + 0.000104 * v_m_min ** 2


def velocity_for_vo2(vo2: float) -> float:
    """Inverse of vo2_cost: velocity (m/min) whose oxygen cost is vo2."""
    a, b, c = 0.000104, 0.182258, -4.60 - vo2
    return (-b + math.sqrt(b * b - 4 * a * c)) / (2 * a)


def pct_vo2max(t_min: float) -> float:
    """Fraction of VO2max sustainable for an effort of t minutes."""
    return (0.8 + 0.1894393 * math.exp(-0.012778 * t_min)
            + 0.2989558 * math.exp(-0.1932605 * t_min))


def vdot_from_performance(distance_m: float, time_s: float) -> float:
    """VDOT from a race (or all-out time trial) performance."""
    t_min = time_s / 60
    return vo2_cost(distance_m / t_min) / pct_vo2max(t_min)


def vdot_from_threshold_pace(pace_s_per_km: float) -> float:
    """VDOT implied by a threshold (T) pace — an estimate, not a race result."""
    v = 1000 / (pace_s_per_km / 60)
    return vo2_cost(v) / THRESHOLD_FRACTION


def predict_time(vdot: float, distance_m: float) -> float:
    """Predicted race time (s) over distance_m for a given VDOT (bisection)."""
    lo, hi = 60.0, 60.0 * 60 * 10  # 1 min .. 10 h
    for _ in range(100):
        mid = (lo + hi) / 2
        if vdot_from_performance(distance_m, mid) > vdot:
            lo = mid  # too fast for this VDOT -> needs more time
        else:
            hi = mid
    return (lo + hi) / 2


def _pace_s_per_km(v_m_min: float) -> float:
    return 60000 / v_m_min


def training_paces(vdot: float) -> dict:
    """Daniels training paces (seconds per km) for a VDOT."""
    e_slow = _pace_s_per_km(velocity_for_vo2(EASY_FRACTION[0] * vdot))
    e_fast = _pace_s_per_km(velocity_for_vo2(EASY_FRACTION[1] * vdot))
    return {
        "E": {"fast_s_per_km": round(e_fast), "slow_s_per_km": round(e_slow)},
        "M": {"s_per_km": round(predict_time(vdot, MARATHON_M) / (MARATHON_M / 1000))},
        "T": {"s_per_km": round(_pace_s_per_km(velocity_for_vo2(THRESHOLD_FRACTION * vdot)))},
        "I": {"s_per_km": round(_pace_s_per_km(velocity_for_vo2(INTERVAL_FRACTION * vdot)))},
        "R": {"s_per_km": round(predict_time(vdot, MILE_M) / (MILE_M / 1000))},
    }


def parse_distance(value: str) -> float:
    """'10k', 'semi', 'marathon', '5000m' or a plain km number -> metres."""
    key = value.strip().lower()
    if key in DISTANCE_ALIASES:
        return DISTANCE_ALIASES[key]
    if key.endswith("km"):
        return float(key[:-2]) * 1000
    if key.endswith("m"):
        return float(key[:-1])
    if key.endswith("k"):
        return float(key[:-1]) * 1000
    return float(key) * 1000


def parse_time(value: str) -> float:
    """'41:21', '1:31:35' or seconds -> seconds."""
    parts = [float(p) for p in value.strip().split(":")]
    secs = 0.0
    for p in parts:
        secs = secs * 60 + p
    return secs


def format_pace_s(pace_s: float) -> str:
    """Seconds per km -> 'M:SS'."""
    m, s = divmod(round(pace_s), 60)
    return f"{m}:{s:02d}"


def format_time_s(total_s: float) -> str:
    """Seconds -> 'H:MM:SS' or 'M:SS'."""
    total = round(total_s)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def vdot_report(vdot: float, source: str,
                threshold_pace_s_per_km: float | None = None) -> dict:
    """Full VDOT report: predictions, training paces, threshold cross-check."""
    predictions = []
    for label, dist in PREDICTION_DISTANCES.items():
        t = predict_time(vdot, dist)
        predictions.append({
            "distance": label,
            "distance_m": dist,
            "time_s": round(t),
            "time": format_time_s(t),
            "pace": format_pace_s(t / (dist / 1000)),
        })

    paces = training_paces(vdot)
    for zone in paces.values():
        for key in list(zone):
            zone[key.replace("s_per_km", "pace")] = format_pace_s(zone[key])

    report = {
        "vdot": round(vdot, 1),
        "source": source,
        "predictions": predictions,
        "paces": paces,
        "threshold_check": None,
    }
    # The config threshold pace comes from a field test; the VDOT T pace from a
    # race. A large gap means one of the two is stale — the LLM decides which.
    if threshold_pace_s_per_km:
        t_pace = paces["T"]["s_per_km"]
        report["threshold_check"] = {
            "config_threshold_s_per_km": threshold_pace_s_per_km,
            "config_threshold_pace": format_pace_s(threshold_pace_s_per_km),
            "vdot_t_pace": format_pace_s(t_pace),
            "diff_s_per_km": round(threshold_pace_s_per_km - t_pace),
            "vdot_implied_by_config": round(vdot_from_threshold_pace(threshold_pace_s_per_km), 1),
        }
    return report
