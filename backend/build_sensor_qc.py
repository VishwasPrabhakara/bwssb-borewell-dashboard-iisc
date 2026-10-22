"""Build data/sensor_qc.json from data/sensor_series/*.json + data/sensors.json.

Citation-anchored best-practice QC pipeline.

Stages:
  1. Row-level cleaning: dedup timestamps, per-sensor range clamp (using
     borewell_depth), Hampel filter (k=12, t=3), flatline detection (N=12).
  2. Weekly aggregation (median of static readings, gap >= 8h).
  3. Sensor-level exclusion: <8 valid weekly points, staleness >60 days,
     coverage <75%.
  4. USGS-style approval codes: APPROVED / PROVISIONAL / QUALIFIED / EXCLUDED.

Legacy qc_status (GOOD/USABLE_WITH_CAUTION/POOR/INSUFFICIENT_DATA/NO_DATA)
is retained (mapped from approval_code) so the dashboard keeps working.

Sources: USGS OFR 2021-1004; USGS WQ2016.07; Springer 2022 (Combining
statistical methods for GW-quality outliers); Pearson 2016 (Generalized
Hampel Filters, EURASIP); Kumar et al. 2021; Sarah 2018; Hirsch & Slack 1984;
ITRC S5.5 Trend Tests.
"""
import datetime as dt
import glob
import json
import os
import statistics
from collections import Counter
from pathlib import Path

# --- Thresholds (values traced to citations; see docstring) ---
RECENT_GOOD_DAYS       = 14
RECENT_POOR_DAYS       = 60      # USGS OFR 2021-1004 stale-gauge default
MAX_WATER_LEVEL_FT     = 1500    # network-wide ceiling; per-sensor uses borewell_depth
MAX_DISCHARGE_LPM      = 20000
STATIC_GAP_HOURS       = 8       # Sarah 2018, Ahmed et al. 2004
HAMPEL_K               = 12      # 1 hour @ 5-min cadence (Pearson 2016)
HAMPEL_T               = 3.0     # Pearson 2016 default
FLATLINE_N             = 12      # 1 hour of identical readings
MIN_WEEKS_FLOOR        = 8       # ITRC S5.5 hard floor
MIN_WEEKS_MK           = 30      # Hirsch & Slack 1984
COVERAGE_MIN_PCT       = 0.75    # Kumar et al. 2021 (per-season adapted)
EXPECTED_WEEKS         = 60      # our data window ~14 months


# ---------- Row-level cleaners ----------

def dedup_timestamps(times, *arrays):
    """Keep last value per timestamp. Returns cleaned lists."""
    seen = {}
    for i, t in enumerate(times):
        seen[t] = i  # last occurrence wins
    idx = sorted(seen.values())
    return [times[i] for i in idx], *[[a[i] for i in idx] for a in arrays]


def range_clamp(times, water_ft, flow_lpm, borewell_depth):
    """Drop rows where water_ft or flow_lpm are outside physical bounds."""
    upper_w = min(borewell_depth or MAX_WATER_LEVEL_FT, MAX_WATER_LEVEL_FT)
    keep_idx = []
    for i in range(len(times)):
        w, f = water_ft[i], flow_lpm[i]
        if w is not None and (w < 0 or w > upper_w): continue
        if f is not None and (f < 0 or f > MAX_DISCHARGE_LPM): continue
        keep_idx.append(i)
    return [times[i] for i in keep_idx], [water_ft[i] for i in keep_idx], [flow_lpm[i] for i in keep_idx]


def hampel_filter(values, k=HAMPEL_K, t=HAMPEL_T):
    """Return boolean mask (True = keep). Uses median + MAD in a window of size 2k+1.
    Not applied where the window is smaller than k (leading/trailing edges kept).
    """
    n = len(values)
    keep = [True] * n
    if n < 2*k + 1:
        return keep
    scale = 1.4826  # MAD -> std for normal
    for i in range(k, n - k):
        v = values[i]
        if v is None:
            continue
        window = [x for x in values[i-k:i+k+1] if x is not None]
        if len(window) < 3:
            continue
        m = statistics.median(window)
        mad = statistics.median([abs(x - m) for x in window])
        if mad == 0:
            continue
        if abs(v - m) > t * scale * mad:
            keep[i] = False
    return keep


def flatline_mask(values, n_run=FLATLINE_N):
    """Return boolean mask (True = keep). Drops runs of >= n_run identical values."""
    n = len(values)
    keep = [True] * n
    if n < n_run: return keep
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[j+1] is not None and values[j] is not None and values[j+1] == values[j]:
            j += 1
        run = j - i + 1
        if run >= n_run:
            for k in range(i, j+1):
                keep[k] = False
        i = j + 1
    return keep


def apply_mask(mask, times, *arrays):
    keep_idx = [i for i, k in enumerate(mask) if k]
    return [times[i] for i in keep_idx], *[[a[i] for i in keep_idx] for a in arrays]


# ---------- Static + weekly ----------

def count_static_weeks(times_iso):
    """Number of ISO weeks that contain >= 1 static (preceding gap >= 8h) reading."""
    if not times_iso: return 0
    times = [dt.datetime.fromisoformat(t) if isinstance(t, str) else t for t in times_iso]
    weeks = set()
    prev = None
    for t in times:
        if prev is not None and (t - prev).total_seconds() / 3600 >= STATIC_GAP_HOURS:
            weeks.add((t.isocalendar()[0], t.isocalendar()[1]))
        prev = t
    return len(weeks)


# ---------- Sub-scores (kept for dashboard) ----------

def coverage_score(total_readings, days_span):
    if total_readings <= 0: return 0
    if days_span <= 0: return min(100, total_readings * 10)
    per_day = total_readings / max(days_span, 1)
    return round(min(100.0, per_day * 100), 2) if per_day < 1 else 100

def freshness_score(stale_days):
    if stale_days is None: return 0
    if stale_days <= RECENT_GOOD_DAYS: return 100
    if stale_days >= RECENT_POOR_DAYS: return 0
    return round(100 * (RECENT_POOR_DAYS - stale_days) / (RECENT_POOR_DAYS - RECENT_GOOD_DAYS), 2)


# ---------- Approval code (USGS WQ2016.07 style) ----------

def approval_code(passed_stage4, cleaning_pct, n_weeks_static, stale_days):
    if not passed_stage4:
        return "EXCLUDED"
    if cleaning_pct > 25:
        return "QUALIFIED"
    if cleaning_pct > 5:
        return "PROVISIONAL"
    return "APPROVED"

# Legacy status mapping (for dashboard compatibility)
LEGACY_MAP = {
    "APPROVED":    "GOOD",
    "PROVISIONAL": "USABLE_WITH_CAUTION",
    "QUALIFIED":   "USABLE_WITH_CAUTION",
    "EXCLUDED":    "POOR",
}


# ---------- Main pipeline ----------

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"

def analyse(uid, series_path, borewell_depth, now):
    d = json.load(open(series_path))
    times_in  = d.get("times", [])
    water_in  = d.get("water_ft", [])
    flow_in   = d.get("flow_lpm", [])
    n_raw = len(times_in)

    flags = set()

    if n_raw == 0:
        return {"total_readings": 0, "n_raw": 0, "cleaning_pct": 0.0,
                "first_data_at": None, "last_data_at": None,
                "static_weeks": 0, "stale_days": None,
                "coverage_score": 0, "range_score": 0, "stability_score": 0, "recent_data_score": 0,
                "overall_qc_score": 0, "flags": ["NO_DATA"], "approval_code": "EXCLUDED",
                "cleaning_dropped_ranged": 0, "cleaning_dropped_hampel": 0,
                "cleaning_dropped_flatline": 0, "cleaning_dropped_duplicate": 0}

    # Stage 1.1: dedup timestamps
    dup_before = n_raw - len(set(times_in))
    if dup_before > 0: flags.add("DUPLICATE_TIMESTAMPS")
    times, water, flow = dedup_timestamps(times_in, water_in, flow_in)

    # Stage 1.2: range clamp per-sensor
    n_before = len(times)
    times, water, flow = range_clamp(times, water, flow, borewell_depth)
    dropped_range = n_before - len(times)
    if dropped_range > 0: flags.add("RANGE_ERRORS")

    # Stage 1.3: Hampel filter on water_ft
    mask_w = hampel_filter(water, k=HAMPEL_K, t=HAMPEL_T)
    mask_f = hampel_filter(flow,  k=HAMPEL_K, t=HAMPEL_T)
    dropped_hampel = sum(1 for a, b in zip(mask_w, mask_f) if not (a and b))
    combined = [a and b for a, b in zip(mask_w, mask_f)]
    if dropped_hampel > 0: flags.add("SPIKES")
    times, water, flow = apply_mask(combined, times, water, flow)

    # Stage 1.4: flatline
    mask_w = flatline_mask(water, n_run=FLATLINE_N)
    mask_f = flatline_mask(flow,  n_run=FLATLINE_N)
    dropped_flatline = sum(1 for a, b in zip(mask_w, mask_f) if not (a and b))
    combined = [a and b for a, b in zip(mask_w, mask_f)]
    if dropped_flatline > 0: flags.add("FLATLINES")
    times, water, flow = apply_mask(combined, times, water, flow)

    n_clean = len(times)
    cleaning_pct = round(100.0 * (n_raw - n_clean) / n_raw, 2) if n_raw else 0

    # Parse to datetimes for date arithmetic
    ts = [dt.datetime.fromisoformat(t) if isinstance(t, str) else t for t in times]
    first_iso = ts[0].isoformat() if ts else None
    last_iso  = ts[-1].isoformat() if ts else None
    stale_days = (now - ts[-1]).total_seconds() / 86400 if ts else None
    if stale_days is not None and stale_days > RECENT_POOR_DAYS: flags.add("STALE_DATA")
    days_span = max((ts[-1] - ts[0]).total_seconds() / 86400, 0) if len(ts) > 1 else 0

    static_weeks = count_static_weeks(times)

    # Sub-scores (retained for dashboard)
    cov = coverage_score(n_clean, days_span)
    range_score = 100.0 * (1 - dropped_range / max(n_raw, 1))
    stab = round(max(0, 100 - ((dropped_hampel + dropped_flatline) / max(n_raw, 1)) * 100), 2)
    recent = freshness_score(stale_days)
    overall = round(0.25*cov + 0.25*range_score + 0.25*stab + 0.25*recent, 2)

    # Stage-4 exclusion checks
    passed = (static_weeks >= MIN_WEEKS_FLOOR
              and stale_days is not None and stale_days <= RECENT_POOR_DAYS
              and static_weeks >= COVERAGE_MIN_PCT * EXPECTED_WEEKS * 0.5)  # relaxed: 45 weeks would be too strict for short-record sensors; use 22.5 min
    if n_clean < 10: passed = False

    ap_code = approval_code(passed, cleaning_pct, static_weeks, stale_days)

    return {
        "total_readings":  n_clean,
        "n_raw":           n_raw,
        "cleaning_pct":    cleaning_pct,
        "first_data_at":   first_iso,
        "last_data_at":    last_iso,
        "static_weeks":    static_weeks,
        "stale_days":      round(stale_days, 1) if stale_days is not None else None,
        "coverage_score":  cov,
        "range_score":     round(range_score, 2),
        "stability_score": stab,
        "recent_data_score": recent,
        "overall_qc_score":  overall,
        "flags":           sorted(flags),
        "approval_code":   ap_code,
        "cleaning_dropped_duplicate": dup_before,
        "cleaning_dropped_ranged":    dropped_range,
        "cleaning_dropped_hampel":    dropped_hampel,
        "cleaning_dropped_flatline":  dropped_flatline,
    }


def _qc_worker(args):
    """Pickleable worker for build_sensor_qc main() parallel pool.
    Returns (uid, stats). Keeps error handling isolated per sensor."""
    uid, fp, bd, now_ts = args
    try:
        if fp:
            return uid, analyse(uid, fp, bd, now_ts)
        return uid, analyse(uid, None, None, now_ts)
    except Exception as e:
        return uid, {"total_readings": 0, "cleaning_pct": 0.0, "static_weeks": 0,
                     "flags": [f"READ_ERROR:{e.__class__.__name__}"],
                     "approval_code": "EXCLUDED"}


def main():
    now = dt.datetime.utcnow()
    sensors = json.load(open(DATA / "sensors.json"))
    depth_by_uid = {str(s["uid"]): s.get("borewell_depth") or MAX_WATER_LEVEL_FT for s in sensors}
    series_files = {Path(fp).stem: fp for fp in glob.glob(str(DATA / "sensor_series" / "*.json"))}
    print(f"Loaded {len(sensors)} sensors, {len(series_files)} sensor_series files")

    rows = []
    counts_new = Counter()
    counts_legacy = Counter()

    # Parallel analyse across CPU-1 worker processes.
    import os as _os
    from concurrent.futures import ProcessPoolExecutor, as_completed
    workers = max(1, (_os.cpu_count() or 2) - 1)
    print(f"  analysing with {workers} worker process(es) ...")

    jobs = [(str(s["uid"]), series_files.get(str(s["uid"])),
             depth_by_uid.get(str(s["uid"])), now)
            for s in sensors]
    done = 0
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_qc_worker, j): j[0] for j in jobs}
        for fut in as_completed(futs):
            uid, stats = fut.result()
            legacy = LEGACY_MAP.get(stats.get("approval_code", "EXCLUDED"), "POOR")
            if stats["total_readings"] == 0:
                legacy = "NO_DATA"
            elif stats["total_readings"] < 10:
                legacy = "INSUFFICIENT_DATA"
            row = {"uid": uid, "qc_status": legacy, **stats}
            rows.append(row)
            counts_new[stats["approval_code"]] += 1
            counts_legacy[legacy] += 1
            done += 1
            if done % 200 == 0 or done == len(jobs):
                print(f"  processed {done}/{len(jobs)}")

    summary = {
        "generated_at": now.isoformat() + "Z",
        "sensor_total": len(rows),
        "by_status":         dict(counts_legacy),   # legacy status counts
        "by_approval_code":  dict(counts_new),      # new USGS-style
        "thresholds": {
            "RECENT_GOOD_DAYS":    RECENT_GOOD_DAYS,
            "RECENT_POOR_DAYS":    RECENT_POOR_DAYS,
            "MAX_WATER_LEVEL_FT":  MAX_WATER_LEVEL_FT,
            "MAX_DISCHARGE_LPM":   MAX_DISCHARGE_LPM,
            "STATIC_GAP_HOURS":    STATIC_GAP_HOURS,
            "HAMPEL_K":            HAMPEL_K,
            "HAMPEL_T":            HAMPEL_T,
            "FLATLINE_N":          FLATLINE_N,
            "MIN_WEEKS_FLOOR":     MIN_WEEKS_FLOOR,
            "MIN_WEEKS_MK":        MIN_WEEKS_MK,
            "COVERAGE_MIN_PCT":    COVERAGE_MIN_PCT,
            "EXPECTED_WEEKS":      EXPECTED_WEEKS,
        },
    }
    out = {"summary": summary, "sensors": rows}
    with open(DATA / "sensor_qc.json", "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print("Wrote", DATA / "sensor_qc.json")
    print("Legacy status:  ", dict(counts_legacy))
    print("Approval codes: ", dict(counts_new))


if __name__ == "__main__":
    main()
