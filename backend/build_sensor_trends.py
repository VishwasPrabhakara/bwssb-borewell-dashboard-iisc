#!/usr/bin/env python3
"""Per-borewell water-level trend for each of the four chart filters.

Method (same for every filter): Sen's slope (median of pairwise slopes) +
Mann-Kendall significance with the Hamed & Rao (1998) autocorrelation-corrected
variance. Level is feet BELOW ground, so slope > 0 = deepening = declining.

Series fed to the test, per filter:
  raw      every raw reading (no aggregation)
  cleaned  every cleaned reading (falls back to raw if the sensor has no cleaned arrays)
  static   weekly (ISO) medians of cleaned readings rested >= 8 h
  dow      one median level per pumping session, for each weekday (0=Sun..6=Sat)

Class: p < 0.05 and |slope| > 0.02 ft/week -> declining / rising, else stable;
fewer than 4 points -> none.

Output: data/sensor_trends.json
"""
import datetime as dt
import json
import statistics
import sys
from pathlib import Path

import numpy as np
from scipy.stats import norm, rankdata

from build_critical_ward_groundwater import USABLE_QC, filter_static, sensor_weekly_series

DATA = Path(__file__).resolve().parents[1] / "data"
ALPHA = 0.05
MIN_POINTS = 4
SLOPE_FT_PER_WEEK = 0.02
# shortcut: pairwise tests are O(n^2); thin raw/cleaned to 1500 evenly spaced readings
# (still raw values, none aggregated). Upgrade if per-reading precision is required.
MAX_POINTS = 1500


def _day(t):
    return dt.datetime.fromisoformat(t).timestamp() / 86400.0


def sen_mk_hr(xs, ys):
    """Return (n, sen slope per x-unit, p). xs ascending, same unit as the slope."""
    xs, ys = np.asarray(xs, float), np.asarray(ys, float)
    n = len(xs)
    if n < MIN_POINTS:
        return n, None, None
    i, j = np.triu_indices(n, 1)
    dx = xs[j] - xs[i]
    ok = dx != 0
    slope = float(np.median(((ys[j] - ys[i])[ok]) / dx[ok]))
    s = float(np.sign(ys[j] - ys[i]).sum())
    _, counts = np.unique(ys, return_counts=True)
    var_s = (n * (n - 1) * (2 * n + 5) - (counts * (counts - 1) * (2 * counts + 5)).sum()) / 18.0
    if var_s <= 0:
        return n, slope, 1.0
    # Hamed-Rao: autocorrelation of the ranked, Sen-detrended series, significant lags only
    d = rankdata(ys - slope * xs)
    d = d - d.mean()
    den = float((d * d).sum())
    adj = 1.0  # den == 0: perfectly linear, no residual autocorrelation to correct for
    if den > 0 and n > 2:
        acf = np.correlate(d, d, "full")[n - 1:] / den
        k = np.arange(1, n)
        sig = np.abs(acf[1:]) > norm.ppf(1 - ALPHA / 2) / np.sqrt(n)
        adj = 1 + 2 / (n * (n - 1) * (n - 2)) * float(
            ((n - k) * (n - k - 1) * (n - k - 2) * acf[1:])[sig].sum())
    z = (s - np.sign(s)) / np.sqrt(var_s * max(adj, 1e-6))
    return n, slope, float(2 * (1 - norm.cdf(abs(z))))


def record(xs_days, ys, thin=False):
    """Trend record for one series; xs in days, slope reported in ft/week."""
    if thin and len(xs_days) > MAX_POINTS:
        pick = np.linspace(0, len(xs_days) - 1, MAX_POINTS).round().astype(int)
        xs_days, ys = [xs_days[k] for k in pick], [ys[k] for k in pick]
    n, slope, p = sen_mk_hr(xs_days, ys)
    if slope is None:
        return {"n": n, "s": None, "p": None, "c": "none"}
    s = slope * 7
    cls = "stable" if p >= ALPHA or abs(s) <= SLOPE_FT_PER_WEEK else ("declining" if s > 0 else "rising")
    return {"n": n, "s": round(s, 4), "p": round(p, 4), "c": cls}


def static_weekly(d):
    """{(iso_year, iso_week): median} of cleaned readings rested >= 8 h."""
    times, water = d.get("times") or [], d.get("water_ft") or []
    st_t, st_v = filter_static(d.get("cleaned_times") or times, d.get("cleaned_water_ft") or water)
    return sensor_weekly_series(st_t, st_v)


def sensor_trends(d):
    times, water = d.get("times") or [], d.get("water_ft") or []
    ct, cw = d.get("cleaned_times") or times, d.get("cleaned_water_ft") or water
    raw = [(_day(t), v) for t, v in zip(times, water) if v is not None]
    cln = [(_day(t), v) for t, v in zip(ct, cw) if v is not None]
    weekly = sorted(static_weekly(d).items())
    wk = [(dt.date.fromisocalendar(y, w, 1).toordinal(), v) for (y, w), v in weekly]
    by_dow = {k: [] for k in range(7)}
    for se in d.get("sessions") or []:
        vals = [v for v in water[se["start"]:se["stop"] + 1] if v is not None]
        if vals:
            t = dt.datetime.fromisoformat(times[se["start"]])
            by_dow[(t.weekday() + 1) % 7].append((_day(times[se["start"]]), statistics.median(vals)))
    xy = lambda pts: ([p[0] for p in pts], [p[1] for p in pts])
    return {
        "raw": record(*xy(raw), thin=True),
        "cleaned": record(*xy(cln), thin=True),
        "static": record(*xy(wk)),
        "dow": {str(k): record(*xy(sorted(v))) for k, v in by_dow.items()},
    }


WARD_CATEGORY = {"declining": "Critical: Ward-average groundwater decline",
                 "rising": "Confirmed groundwater rise",
                 "stable": "Stable groundwater trend",
                 "none": "Insufficient data"}
WARD_STATUS = {"declining": ("Critical", "Declining", "Yes"), "rising": ("Normal", "Improving", "No"),
               "stable": ("Normal", "Stable", "No"), "none": ("Insufficient data", "Not computed", "No")}


def update_ward_summary(weekly_by_ward):
    """Classify every ward with the same fixed method, in place in the ward summary the dashboard reads."""
    path = DATA / "critical_groundwater_ward_summary.json"
    payload = json.load(open(path))
    for row in payload["wards"]:
        wk = weekly_by_ward.get(str(row["wardNo"]), {})
        weeks = sorted(wk)
        r = record([dt.date.fromisocalendar(y, w, 1).toordinal() for y, w in weeks],
                   [statistics.median(wk[k]) for k in weeks])
        status, direction, action = WARD_STATUS[r["c"]]
        sig = r["p"] is not None and r["p"] < ALPHA
        row.update({
            "linearSlopeFtPerWeek": r["s"], "senSlopeFtPerWeek": r["s"], "declineStrengthFtPerWeek": r["s"],
            "mannKendallP": r["p"], "mannKendallVerdict": "Yes" if sig else "No", "mkConfirmed": sig,
            "usableWeeklyValues": r["n"], "groundwaterStatus": status, "groundwaterDirection": direction,
            "dashboardAction": action, "dashboardMapCategory": WARD_CATEGORY[r["c"]],
            "slopeReliable": r["s"] is None or abs(r["s"] * 52 * 0.3048) <= 5.0,
            "stepChangeDetected": "No",  # Pettitt is not part of the fixed method
        })
    payload["method"] = "Sen slope + Mann-Kendall (Hamed-Rao) on ward weekly medians of static >=8h readings"
    json.dump(payload, open(path, "w"), separators=(",", ":"))
    print("wards", {c: sum(r["dashboardMapCategory"] == v for r in payload["wards"]) for c, v in WARD_CATEGORY.items()})


def main():
    out = {}
    sensors = {str(x["uid"]): x for x in json.load(open(DATA / "sensors.json"))}
    qc = {str(x["uid"]): x["qc_status"] for x in json.load(open(DATA / "sensor_qc.json"))["sensors"]}
    weekly_by_ward = {}
    files = sorted((DATA / "sensor_series").glob("*.json"))
    for n, fp in enumerate(files, 1):
        d = json.load(open(fp))
        out[fp.stem] = sensor_trends(d)
        wn = (sensors.get(fp.stem) or {}).get("ward_no")
        if wn not in (None, "") and qc.get(fp.stem) in USABLE_QC:
            for wk, v in static_weekly(d).items():
                weekly_by_ward.setdefault(str(int(wn)), {}).setdefault(wk, []).append(v)
        if n % 100 == 0:
            print(f"  {n}/{len(files)}", file=sys.stderr)
    payload = {"generated_at": dt.datetime.utcnow().isoformat() + "Z", "alpha": ALPHA,
               "slope_ft_per_week": SLOPE_FT_PER_WEEK, "sensors": out}
    json.dump(payload, open(DATA / "sensor_trends.json", "w"), separators=(",", ":"))
    update_ward_summary(weekly_by_ward)
    for m in ("raw", "cleaned", "static"):
        c = {}
        for r in out.values():
            c[r[m]["c"]] = c.get(r[m]["c"], 0) + 1
        print(m, c)


if __name__ == "__main__":
    main()
