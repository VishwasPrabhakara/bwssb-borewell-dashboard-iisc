"""Apply row-level cleaning to every data/sensor_series/*.json in place.

Adds three arrays alongside the raw ones:
    cleaned_times      -- ISO timestamps after dedup + range clamp + Hampel + flatline
    cleaned_water_ft   -- parallel water-level values
    cleaned_flow_lpm   -- parallel flow values
Also records a clean_summary block (counts of raw vs cleaned).

Rules match backend/build_sensor_qc.py exactly (same imports).
Dashboard reads these arrays when the user selects "Cleaned" or "Static" mode.
"""
import json, glob
from pathlib import Path

from build_sensor_qc import (
    dedup_timestamps, range_clamp, hampel_filter, flatline_mask, apply_mask,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

sensors = {str(s["uid"]): s for s in json.load(open(DATA / "sensors.json"))}
files = sorted(glob.glob(str(DATA / "sensor_series" / "*.json")))

n_total = n_patched = 0
raw_sum = clean_sum = 0

for fp in files:
    n_total += 1
    d = json.load(open(fp))
    uid = str(d.get("uid"))
    times = list(d.get("times") or [])
    water = list(d.get("water_ft") or [])
    flow  = list(d.get("flow_lpm") or [])
    if not times:
        d["cleaned_times"] = []
        d["cleaned_water_ft"] = []
        d["cleaned_flow_lpm"] = []
        d["clean_summary"] = {"n_raw": 0, "n_clean": 0, "removed_pct": 0.0}
        json.dump(d, open(fp, "w"))
        continue
    n_raw = len(times)
    bd = (sensors.get(uid) or {}).get("borewell_depth")

    # 1. Dedup
    times, water, flow = dedup_timestamps(times, water, flow)
    # 2. Range clamp
    times, water, flow = range_clamp(times, water, flow, bd)
    # 3. Hampel on water level (keep flow aligned)
    km = hampel_filter(water)
    times, water, flow = apply_mask(km, times, water, flow)
    # 4. Flatline on water
    fm = flatline_mask(water)
    times, water, flow = apply_mask(fm, times, water, flow)

    n_clean = len(times)
    d["cleaned_times"]    = times
    d["cleaned_water_ft"] = water
    d["cleaned_flow_lpm"] = flow
    d["clean_summary"]    = {
        "n_raw": n_raw,
        "n_clean": n_clean,
        "removed_pct": round(100.0 * (n_raw - n_clean) / max(n_raw, 1), 2),
    }
    json.dump(d, open(fp, "w"))
    n_patched += 1
    raw_sum += n_raw; clean_sum += n_clean
    if n_patched % 100 == 0:
        print(f"  patched {n_patched}/{len(files)}")

print(f"\ndone: patched {n_patched}/{n_total} sensor_series files")
print(f"total rows: raw={raw_sum:,}  cleaned={clean_sum:,}  removed={raw_sum-clean_sum:,} ({100*(raw_sum-clean_sum)/max(raw_sum,1):.2f}%)")
