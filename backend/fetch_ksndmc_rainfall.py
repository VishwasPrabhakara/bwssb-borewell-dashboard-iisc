# Pulls Bangalore telemetry rainfall from the India-WRIS / NWDP CKAN API
# (official KSNDMC/WRD mirror), rolls hourly -> daily per ward.
# Run from the repo root:  python backend/fetch_ksndmc_rainfall.py
import json, time, urllib.request, urllib.parse, csv
from datetime import datetime
from collections import defaultdict

API = "https://nwdp.nwic.gov.in/api/3/action/datastore_search"
RES = ["18434c62-2ed5-4083-8060-cac00be166c9",  # 2021-2025
       "b803cd63-c4c1-4df5-868a-b40a95f54f34"]  # 2026-2030
TIME = "Data Acquisition Time"
RAIN = "Telemetry Hourly Rainfall (mm)"

def fetch(res):
    off, out = 0, []
    while True:
        q = urllib.parse.urlencode({"resource_id": res, "q": "Bangalore",
                                    "limit": 1000, "offset": off})
        with urllib.request.urlopen(f"{API}?{q}", timeout=60) as r:
            d = json.load(r)["result"]
        out += d["records"]; off += len(d["records"])
        if off >= d["total"] or not d["records"]:
            break
        time.sleep(0.3)
    return out

rows = []
for res in RES:
    rows += fetch(res)

daily, meta = defaultdict(float), {}
for r in rows:
    if not str(r.get("District", "")).startswith("Bangalore"):
        continue
    try:
        dt = datetime.strptime(r[TIME], "%d-%m-%Y %H:%M").date()
        lat = float(r["Latitude"]); lng = float(r["Longitude"]); mm = float(r[RAIN] or 0)
    except Exception:
        continue
    daily[(r["Station"], dt)] += mm
    meta[r["Station"]] = (lat, lng)
print(f"records:{len(rows)} stations:{len(meta)} station-days:{len(daily)}")

wj = json.load(open("data/wards.geojson"))
wards = []
for f in wj["features"]:
    g = f["geometry"]
    ring = g["coordinates"][0] if g["type"] == "Polygon" else g["coordinates"][0][0]
    xs = [c[0] for c in ring]; ys = [c[1] for c in ring]
    wards.append((f["properties"]["ward_no"], ring, min(xs), max(xs), min(ys), max(ys)))

def inpoly(x, y, ring):
    c = False; n = len(ring); j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]; xj, yj = ring[j][0], ring[j][1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            c = not c
        j = i
    return c

def ward_of(lat, lng):
    for wno, ring, x0, x1, y0, y1 in wards:
        if x0 <= lng <= x1 and y0 <= lat <= y1 and inpoly(lng, lat, ring):
            return wno
    return None

st_ward = {st: ward_of(la, ln) for st, (la, ln) in meta.items()}
covered = {w for w in st_ward.values() if w is not None}
print(f"stations in-ward:{sum(1 for w in st_ward.values() if w)} "
      f"wards covered:{len(covered)}/{len(wards)}")

agg = defaultdict(list)
for (st, dt), mm in daily.items():
    w = st_ward.get(st)
    if w is not None:
        agg[(w, dt)].append(mm)

with open("data_raw/ksndmc/ward_daily_rainfall.csv", "w", newline="") as fh:
    wtr = csv.writer(fh)
    wtr.writerow(["ward_no", "date", "rainfall_mm", "n_stations"])
    for (w, dt), v in sorted(agg.items(), key=lambda x: (str(x[0][0]), x[0][1])):
        wtr.writerow([w, dt.isoformat(), round(sum(v) / len(v), 2), len(v)])
print("wrote data_raw/ksndmc/ward_daily_rainfall.csv")
