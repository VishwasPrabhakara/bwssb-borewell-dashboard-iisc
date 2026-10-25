#!/usr/bin/env python3
"""
build_sensor_depth_bands.py
Classify every REPORTING borewell (sensor with data in sensors.json) into one of
four water-level depth bands, using the latest valid water-level reading from its
series file.

Bands (depth to water, feet below ground):
    Shallow        < 100 ft
    Moderate       100 - 250 ft
    Deep           250 - 500 ft
    Very Deep      > 500 ft

Output: data/sensor_depth_bands.json
    {
      "generated_at": ...,
      "bands": [ {key,label,min,max}, ... ],
      "by_band": {shallow: n, ...},
      "sensors": [ {uid, ward_no, latest_ft, band}, ... ]   # one per reporting sensor
    }

Only sensors present in the uploaded data (sensors.json has_data=true) are counted.
Nothing here uses any remote/Neon source.
"""
import json, os, glob, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SENSORS = os.path.join(ROOT, "data", "sensors.json")
SERIES_DIR = os.path.join(ROOT, "data", "sensor_series")
OUT = os.path.join(ROOT, "data", "sensor_depth_bands.json")

BANDS = [
    {"key": "shallow",  "label": "Shallow (<100 ft)",      "min": 0,    "max": 100},
    {"key": "moderate", "label": "Moderate (100–250 ft)",  "min": 100,  "max": 250},
    {"key": "deep",     "label": "Deep (250–500 ft)",      "min": 250,  "max": 500},
    {"key": "verydeep", "label": "Very Deep (>500 ft)",    "min": 500,  "max": None},
]

def band_for(ft):
    if ft < 100:  return "shallow"
    if ft < 250:  return "moderate"
    if ft < 500:  return "deep"
    return "verydeep"

def latest_valid(water):
    for i in range(len(water) - 1, -1, -1):
        v = water[i]
        if v is None:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv == fv and fv > 0:   # finite, positive
            return fv
    return None

def main():
    sensors = json.load(open(SENSORS))
    reporting = [s for s in sensors if s.get("has_data")]

    out_sensors = []
    by_band = {b["key"]: 0 for b in BANDS}
    missing = 0
    for s in reporting:
        uid = str(s["uid"])
        p = os.path.join(SERIES_DIR, uid + ".json")
        if not os.path.exists(p):
            missing += 1
            continue
        d = json.load(open(p))
        lv = latest_valid(d.get("water_ft") or [])
        if lv is None:
            missing += 1
            continue
        b = band_for(lv)
        by_band[b] += 1
        out_sensors.append({
            "uid": uid,
            "ward_no": s.get("ward_no"),
            "latest_ft": round(lv, 1),
            "band": b,
        })

    payload = {
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "bands": BANDS,
        "by_band": by_band,
        "total": len(out_sensors),
        "sensors": out_sensors,
    }
    json.dump(payload, open(OUT, "w"), indent=0)
    print(f"reporting sensors: {len(reporting)} | classified: {len(out_sensors)} | missing/no-reading: {missing}")
    print("by band:", by_band)
    print("->", OUT)

if __name__ == "__main__":
    main()
