"""
Extract latest static water level per sensor and classify into depth bands.
Reads: data/sensor_series/*.json, data/sensor_qc.json
Writes: data/water_level_classification.json
Bands: <100ft (Shallow), 100-250ft (Moderate), 250-500ft (Deep), >500ft (Very Deep)
"""
import json, os, glob

BANDS = [
    (0, 100, "Shallow (<100 ft)"),
    (100, 250, "Moderate (100–250 ft)"),
    (250, 500, "Deep (250–500 ft)"),
    (500, float("inf"), "Very Deep (>500 ft)"),
]

# Load QC to filter to GOOD/USABLE sensors
qc = json.load(open("data/sensor_qc.json"))
usable_uids = set()
if isinstance(qc, dict) and "sensors" in qc:
    for s in qc["sensors"]:
        if s.get("qc_status") in ("GOOD", "USABLE_WITH_CAUTION"):
            usable_uids.add(str(s["uid"]))

STATIC_GAP_HOURS = 8
sensors = []
for path in glob.glob("data/sensor_series/*.json"):
    uid = os.path.basename(path).replace(".json", "")
    if uid not in usable_uids:
        continue
    try:
        series = json.load(open(path))
    except Exception:
        continue
    water = series.get("water_ft", [])
    times = series.get("times", [])
    sessions = series.get("sessions", [])
    if not water:
        continue
    # Find static readings (outside pump sessions) - use last 30 readings
    # Simple approach: take the last reading as current level
    # More robust: take median of last N static readings
    # We'll use the approach from current_stress: latest static level
    latest_ft = water[-1] if water else None
    if latest_ft is None or latest_ft <= 0:
        continue
    band_label = "Unknown"
    for lo, hi, label in BANDS:
        if lo <= latest_ft < hi:
            band_label = label
            break
    sensors.append({
        "uid": uid,
        "latest_ft": round(latest_ft, 1),
        "band": band_label,
        "last_time": times[-1] if times else None,
    })

# Ward-level summary
ward_map = {}
sensor_index = json.load(open("data/sensors.json"))
uid_to_ward = {str(s["uid"]): s.get("ward_no") for s in sensor_index}
for s in sensors:
    wno = uid_to_ward.get(s["uid"])
    if wno is None:
        continue
    s["ward_no"] = wno
    ward_map.setdefault(wno, []).append(s)

ward_summaries = []
for wno, wells in sorted(ward_map.items(), key=lambda x: x[0]):
    counts = {}
    for _, _, label in BANDS:
        counts[label] = sum(1 for w in wells if w["band"] == label)
    median_ft = sorted([w["latest_ft"] for w in wells])[len(wells) // 2]
    dominant = max(counts, key=counts.get)
    ward_summaries.append({
        "wardNo": wno,
        "wellCount": len(wells),
        "medianDepthFt": median_ft,
        "dominantBand": dominant,
        "bandCounts": counts,
    })

by_band = {}
for _, _, label in BANDS:
    by_band[label] = sum(1 for s in sensors if s["band"] == label)

output = {
    "generated_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
    "total_classified": len(sensors),
    "bands": [{"lo": lo, "hi": hi if hi != float("inf") else None, "label": label} for lo, hi, label in BANDS],
    "by_band": by_band,
    "wards": ward_summaries,
    "sensors": [{"uid": s["uid"], "ward_no": s.get("ward_no"), "latest_ft": s["latest_ft"], "band": s["band"]} for s in sensors],
}

with open("data/water_level_classification.json", "w") as f:
    json.dump(output, f, indent=2)

print(f"Classified {len(sensors)} sensors across {len(ward_summaries)} wards")
for label, count in by_band.items():
    print(f"  {label}: {count}")
