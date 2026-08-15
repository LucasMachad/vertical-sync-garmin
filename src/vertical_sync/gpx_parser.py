"""GPX route parsing — course-profile analysis for a *planned* race track.

Unlike a FIT file (a recorded activity, with time / HR / speed), a GPX route
exported from a mapping tool (Openrunner, tracedetrail, …) carries only the
geometry: latitude, longitude, elevation. Any ``<time>`` tags are synthetic
(the tool's planned schedule), so we deliberately ignore them — this module
describes the **terrain**, not a performance, and feeds race pacing.

The public entry point is :func:`analyze_gpx`; it mirrors ``analyze_activity``
in :mod:`.fit_parser` (same vocabulary: ascent, vertical ratio, km-effort) so a
race route and a training run read the same way.
"""

import math
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

# Elevation smoothing window (track points). GPS/DEM elevation is noisy; a small
# rolling mean kills the jitter that would otherwise inflate D+ without erasing
# real climbs. ~5 points ≈ 200 m on a 40 m-spaced track.
_SMOOTH_WINDOW = 5

# A climb/descent must gain/lose at least this much (m) to count as its own
# segment. Smaller counter-moves are absorbed into the surrounding segment, so
# the route reads as a handful of major climbs and descents, not dozens of
# micro-bumps.
_SEGMENT_MIN_M = 25.0

# Net elevation over a whole km at/above which the km is tagged a climb/descent
# (below it in absolute value the km reads as rolling / flat).
_KM_FLAT_BAND_M = 25.0

# Gradient bands for the distance distribution (matches fit_parser.GRADIENT_BINS
# vocabulary so terrain buckets are comparable run-vs-route).
_GRADE_BINS = [
    (-1.00, -0.20, "< -20%"),
    (-0.20, -0.10, "-20% a -10%"),
    (-0.10, -0.05, "-10% a -5%"),
    (-0.05, 0.00, "-5% a 0%"),
    (0.00, 0.05, "0% a 5%"),
    (0.05, 0.10, "5% a 10%"),
    (0.10, 0.20, "10% a 20%"),
    (0.20, 1.00, "> 20%"),
]


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lon points, in metres."""
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def parse_gpx(path: Path) -> list[tuple[float, float, float]]:
    """Parse a GPX file into a list of ``(lat, lon, ele)`` track points.

    Namespace-agnostic (handles GPX 1.0 and 1.1). Points missing lat/lon are
    skipped; a point missing elevation inherits the previous point's.
    """
    root = ET.parse(str(path)).getroot()
    pts: list[tuple[float, float, float]] = []
    last_ele = 0.0
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]  # strip namespace
        if tag != "trkpt":
            continue
        lat, lon = el.get("lat"), el.get("lon")
        if lat is None or lon is None:
            continue
        ele = None
        for child in el:
            if child.tag.rsplit("}", 1)[-1] == "ele":
                try:
                    ele = float(child.text)
                except (TypeError, ValueError):
                    ele = None
                break
        if ele is None:
            ele = last_ele
        last_ele = ele
        pts.append((float(lat), float(lon), ele))
    return pts


def _smooth(values: np.ndarray, window: int) -> np.ndarray:
    """Centered rolling mean (edge-safe) to filter elevation noise."""
    if window <= 1 or len(values) < window:
        return values
    kernel = np.ones(window) / window
    smoothed = np.convolve(values, kernel, mode="same")
    # np.convolve tapers the ends toward zero; keep the raw values there.
    half = window // 2
    smoothed[:half] = values[:half]
    smoothed[-half:] = values[-half:]
    return smoothed


def _segments(cum_m: np.ndarray, ele: np.ndarray) -> list[dict]:
    """Split the smoothed profile into alternating climb / descent segments.

    A zig-zag (swing) filter: we track the running extremum and only commit a
    turning point once the profile has reversed by ``_SEGMENT_MIN_M``. This
    merges small counter-moves so the output is the handful of climbs and
    descents that actually shape the race, not every GPS wobble.
    """
    n = len(ele)
    if n < 2:
        return []

    turns = [0]
    trend = 0  # 0 unknown, +1 rising, -1 falling
    hi = lo = ele[0]
    hi_i = lo_i = 0

    for i in range(1, n):
        z = ele[i]
        if z > hi:
            hi, hi_i = z, i
        if z < lo:
            lo, lo_i = z, i
        # A rising (or undecided) trend that has since dropped _SEGMENT_MIN_M
        # off its high confirms a peak; the mirror case confirms a trough. On
        # each confirmation we reset the opposite tracker from the turning point.
        if trend >= 0 and hi - z >= _SEGMENT_MIN_M:
            turns.append(hi_i)
            trend, lo, lo_i = -1, z, i
        elif trend <= 0 and z - lo >= _SEGMENT_MIN_M:
            turns.append(lo_i)
            trend, hi, hi_i = 1, z, i
    if turns[-1] != n - 1:
        turns.append(n - 1)

    segs = []
    for a, b in zip(turns, turns[1:]):
        if b <= a:
            continue
        dist = float(cum_m[b] - cum_m[a])
        if dist <= 0:
            continue
        net = float(ele[b] - ele[a])
        gain = loss = 0.0
        for k in range(a + 1, b + 1):
            dz = float(ele[k] - ele[k - 1])
            if dz > 0:
                gain += dz
            else:
                loss += -dz
        seg_type = "climb" if net >= 0 else "descent"
        segs.append({
            "type": seg_type,
            "start_km": round(cum_m[a] / 1000, 1),
            "end_km": round(cum_m[b] / 1000, 1),
            "distance_km": round(dist / 1000, 2),
            "ascent_m": round(gain),
            "descent_m": round(loss),
            "net_m": round(net),
            "avg_grade_pct": round(net / dist * 100, 1),
        })
    return segs


def analyze_gpx(path: Path) -> dict | None:
    """Analyze a GPX race route into a structured terrain profile.

    Returns distance, total D+/D-, vertical ratio, km-effort, an elevation
    range, a per-kilometre ascent/descent table, the major climb/descent
    segments, and the distance distribution by gradient band. ``None`` if the
    file has fewer than two usable track points.
    """
    path = Path(path)
    pts = parse_gpx(path)
    if len(pts) < 2:
        return None

    lat = np.array([p[0] for p in pts])
    lon = np.array([p[1] for p in pts])
    ele_raw = np.array([p[2] for p in pts], dtype=float)
    ele = _smooth(ele_raw, _SMOOTH_WINDOW)

    # Cumulative distance (m) along the track.
    step = np.zeros(len(pts))
    for i in range(1, len(pts)):
        step[i] = _haversine(lat[i - 1], lon[i - 1], lat[i], lon[i])
    cum = np.cumsum(step)
    total_m = float(cum[-1])
    if total_m <= 0:
        return None

    dz = np.diff(ele)
    ascent = float(dz[dz > 0].sum())
    descent = float(-dz[dz < 0].sum())
    distance_km = total_m / 1000

    # Per-kilometre ascent / descent.
    per_km: list[dict] = []
    n_km = int(total_m // 1000) + 1
    km_bucket = (cum // 1000).astype(int)
    for k in range(n_km):
        mask = km_bucket[1:] == k  # dz[i] belongs to the km of point i+1
        up = float(dz[mask][dz[mask] > 0].sum())
        dn = float(-dz[mask][dz[mask] < 0].sum())
        net = up - dn
        if net > _KM_FLAT_BAND_M:
            tag = "climb"
        elif net < -_KM_FLAT_BAND_M:
            tag = "descent"
        else:
            tag = "flat"
        per_km.append({
            "km": k,
            "ascent_m": round(up),
            "descent_m": round(dn),
            "net_m": round(net),
            "type": tag,
        })

    # Distance distribution by gradient band.
    grade = np.divide(dz, step[1:], out=np.zeros_like(dz), where=step[1:] > 0.5)
    grade = np.clip(grade, -1.0, 1.0)
    dist_by_band = []
    for low, high, label in _GRADE_BINS:
        m = (grade >= low) & (grade < high)
        d = float(step[1:][m].sum())
        if d <= 0:
            continue
        dist_by_band.append({
            "range": label,
            "distance_m": round(d),
            "pct": round(d / total_m * 100, 1),
        })

    return {
        "source": path.name,
        "points": len(pts),
        "distance_km": round(distance_km, 2),
        "ascent_m": round(ascent),
        "descent_m": round(descent),
        "vertical_ratio_m_km": round(ascent / distance_km) if distance_km else 0,
        "km_effort": round(distance_km + ascent / 100, 1),
        "elevation": {"min": round(float(ele.min())), "max": round(float(ele.max()))},
        "per_km": per_km,
        "segments": _segments(cum, ele),
        "gradient_distribution": dist_by_band,
    }
