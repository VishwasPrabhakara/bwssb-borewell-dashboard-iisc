# Free daily collector for KSNDMC dense rain-gauge network.
# Pulls the public "Realized Rainfall (24h)" JSON per Bengaluru district,
# maps each station to a BBMP ward (via the telemetric rain-gauge KML),
# and appends to data_raw/ksndmc/ksndmc_daily_rainfall.csv (de-duplicated).
# Run once a day (after ~09:30 IST). No login, no payment.
#   python backend/collect_ksndmc_daily.py
import json, csv, os, re, glob, urllib.request
from xml.etree import ElementTree as ET

URL = "https://ksndmc.org/en/DailyReport/getRainFallReport?drpVal={:02d}"
OUT_DIR = "data_raw/ksndmc"   # yearly files: ksndmc_<YYYY>.csv
HEADERS = {"User-Agent": "Mozilla/5.0", "X-Requested-With": "XMLHttpRequest"}

def norm(s):
    s = re.sub(r"\(.*?\)", "", str(s))   # remove parenthetical
    s = re.sub(r"-\d+$", "", s)            # remove trailing -N  (Bellandur-2)
    s = s.replace("_", " ")                 # underscore -> space
    s = re.sub(r"\.(?=[a-zA-Z])", " ", s)   # dots between letters -> space
    s = s.replace(".", "")                   # remaining dots removed
    return re.sub(r"\s+", " ", s).strip().lower()

# ---- build station-name -> (lat,lng) from the KML ----
name2xy = {}
for path in glob.glob("data_raw/ksndmc/*.kml"):
    root = ET.parse(path).getroot()
    ns = root.tag.split("}")[0].strip("{") if "}" in root.tag else ""
    T = (lambda n: "{%s}%s" % (ns, n)) if ns else (lambda n: n)
    for pm in root.iter(T("Placemark")):
        nm = None
        for sd in pm.iter(T("SimpleData")):
            if sd.get("name") == "TM_RainGauge_LocationName":
                nm = sd.text
        co = next((c.text.strip().split()[0] for c in pm.iter() if c.tag.endswith("coordinates") and c.text), None)
        if nm and co:
            lng, lat = float(co.split(",")[0]), float(co.split(",")[1])
            name2xy[norm(nm)] = (lat, lng)

# ---- wards (outer ring + bbox) + ray-cast point-in-polygon ----
wards = []
for f in json.load(open("data/wards.geojson"))["features"]:
    g = f["geometry"]; ring = g["coordinates"][0] if g["type"] == "Polygon" else g["coordinates"][0][0]
    xs = [c[0] for c in ring]; ys = [c[1] for c in ring]
    wards.append((f["properties"]["ward_no"], ring, min(xs), max(xs), min(ys), max(ys)))

def inpoly(x, y, ring):
    c = False; n = len(ring); j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]; xj, yj = ring[j][0], ring[j][1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi): c = not c
        j = i
    return c

def ward_of(lat, lng):
    for wno, ring, x0, x1, y0, y1 in wards:
        if x0 <= lng <= x1 and y0 <= lat <= y1 and inpoly(lng, lat, ring): return wno
    return ""

# ---- fetch all Bengaluru districts ----
rows = []
for d in range(1, 36):
    try:
        req = urllib.request.Request(URL.format(d), headers=HEADERS)
        data = json.loads(urllib.request.urlopen(req, timeout=60).read())
        if isinstance(data, str): data = json.loads(data)
    except Exception:
        continue
    for r in data:
        if "BENGALURU" not in str(r.get("DISTRICT", "")).upper(): continue
        nm = norm(r.get("HOBLINAME"))
        xy = name2xy.get(nm)
        ward = ward_of(*xy) if xy else ""
        date = str(r.get("RAINDATE", ""))[:10]
        rows.append([date, r.get("RAINGAUGE"), r.get("HOBLINAME"), r.get("TALUKNAME"),
                     r.get("DISTRICT"), r.get("RAIN"), ward])

os.makedirs(OUT_DIR, exist_ok=True)
HEADER = ["date", "gauge_id", "station", "taluk", "district", "rain_mm", "ward_no"]

# ---- append per YEAR (ksndmc_<YYYY>.csv), de-duplicated on (gauge_id, date) ----
# Yearly rotation keeps each file small; a row is filed by the year in its own date.
by_year = {}
for r in rows:
    yr = str(r[0])[:4]
    if yr.isdigit():
        by_year.setdefault(yr, []).append(r)

new = []
for yr, yr_rows in by_year.items():
    out = os.path.join(OUT_DIR, f"ksndmc_{yr}.csv")
    seen = set()
    if os.path.exists(out):
        with open(out) as fh:
            for row in csv.reader(fh):
                if len(row) >= 2: seen.add((row[1], row[0]))
    fresh = [r for r in yr_rows if (str(r[1]), r[0]) not in seen]
    write_header = not os.path.exists(out)
    with open(out, "a", newline="") as fh:
        w = csv.writer(fh)
        if write_header: w.writerow(HEADER)
        w.writerows(fresh)
    new.extend(fresh)

# ---- summary ----
mapped = sum(1 for r in rows if r[6] != "")
in_ward_kml = sum(1 for n, (lat, lng) in name2xy.items() if ward_of(lat, lng))
mapped_names = {r[2] for r in rows if r[6] != ""}
print(f"fetched rows: {len(rows)} | mapped to a ward: {mapped} ({len(mapped_names)}/{in_ward_kml} in-ward gauges reported today)")
print(f"new rows added: {len(new)} -> {OUT_DIR}/ksndmc_<year>.csv  (years: {sorted(by_year)})")
