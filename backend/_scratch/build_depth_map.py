# -*- coding: utf-8 -*-
"""Depth-class map of Bangalore borewells.

Four depth bins (ft):
    < 100            Shallow
    100 - 250        Medium
    250 - 500        Deep
    > 500            Very deep

Reads sensors_db.json (lat/lng/borewell_depth, from the KH admin dashboard).
Uses wards.geojson for ward polygons as a background reference layer only.

Outputs:
    outputs/depth_map_<DATE>.html       -- Leaflet, interactive (zoom + hover + click)
    outputs/depth_map_<DATE>.png        -- static matplotlib, drop-in for slides

Run:
    cd "C:\\Users\\ADMIN\\OneDrive - Indian Institute of Science\\Dashboard_IISC_for_BWSSB"
    C:\\Python314\\python.exe backend\\_scratch\\build_depth_map.py
"""
import json, datetime as dt, html
from pathlib import Path
from collections import Counter

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import Polygon as MplPoly
from matplotlib.collections import PatchCollection

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
TODAY = dt.datetime.now().strftime("%Y%m%d")
OUT_HTML = ROOT / "outputs" / f"depth_map_{TODAY}.html"
OUT_PNG  = ROOT / "outputs" / f"depth_map_{TODAY}.png"
OUT_HTML.parent.mkdir(parents=True, exist_ok=True)

# ---------- bins ----------
BINS = [
    (">500",   float("inf"),  500,  "Very deep (> 500 ft)",   "#DC2626"),  # bright red
    ("250-500",500,           250,  "Deep (250 - 500 ft)",    "#F97316"),  # bright orange
    ("100-250",250,           100,  "Medium (100 - 250 ft)",  "#22C55E"),  # bright green
    ("<100",   100,           0,    "Shallow (< 100 ft)",     "#0EA5E9"),  # bright sky blue
]

def classify(d):
    if d is None or d <= 0: return None
    for key, hi, lo, label, color in BINS:
        if lo < d <= hi: return key
    return None

LABEL = {k: lbl for k, _, _, lbl, _ in BINS}
COLOR = {k: col for k, _, _, _, col in BINS}

# ---------- load UIDs from current ZIP, fetch meta FRESH from KH ----------
import os as _os, re as _re, zipfile as _zipfile
ZIP_PATH = Path(_os.environ.get("KH_ZIP",
    Path.home() / "Downloads" / "borewell_data_05.10.26.zip"))
print(f"Reading UIDs from ZIP: {ZIP_PATH}")
with _zipfile.ZipFile(ZIP_PATH) as _z:
    uid_set = set()
    for n in _z.namelist():
        if not n.lower().endswith(".xlsx"): continue
        m = _re.match(r"(\d+)", Path(n).stem.split("_")[0])
        if m: uid_set.add(m.group(1))
print(f"  {len(uid_set)} unique UIDs in ZIP")

import sys as _sys, pickle as _pickle, argparse as _argparse
_sys.path.insert(0, str(Path(__file__).parent))
from _kh_meta import fetch_for_uids

_ap = _argparse.ArgumentParser()
_ap.add_argument("--refresh", action="store_true",
                 help="Ignore cache and re-fetch every UID from KH.")
_args, _ = _ap.parse_known_args()

CACHE_PATH = Path(__file__).parent / "_kh_meta_cache.pkl"
cached = {}
if CACHE_PATH.exists() and not _args.refresh:
    try:
        cached = _pickle.loads(CACHE_PATH.read_bytes())
        print(f"  loaded KH meta cache: {len(cached)} UIDs")
    except Exception as _e:
        print(f"  cache unreadable ({_e}); starting fresh")
        cached = {}

missing = sorted(set(uid_set) - set(cached.keys()))
if missing:
    print(f"  {len(missing)} UID(s) missing from cache -- fetching only those")
    fresh = fetch_for_uids(missing, workers=12, sleep_s=0.03)
    cached.update(fresh)
    CACHE_PATH.write_bytes(_pickle.dumps(cached))
    print(f"  cache updated: now {len(cached)} UIDs on disk at {CACHE_PATH.name}")
else:
    print("  all UIDs already in cache -- skipping KH fetch")

kh_meta = {uid: cached[uid] for uid in uid_set if uid in cached}

print("Loading wards.geojson (reference layer only) ...")
wards = json.load(open(DATA / "wards.geojson"))

rows = []
no_depth = 0; no_latlng = 0
for uid in sorted(uid_set):
    m = kh_meta.get(uid, {})
    lat = m.get("lat"); lng = m.get("lng"); d = m.get("borewell_depth")
    if lat is None or lng is None: no_latlng += 1; continue
    if d is None: no_depth += 1; continue
    cls = classify(d)
    if cls is None: continue
    rows.append({
        "uid": uid, "lat": float(lat), "lng": float(lng),
        "depth_ft": float(d),
        "cls": cls, "label": LABEL[cls], "color": COLOR[cls],
        "motor_hp": m.get("motor_hp"), "pump_name": m.get("pump_name"),
    })

print(f"  total UIDs from ZIP:  {len(uid_set)}")
print(f"  skipped (no lat/lng): {no_latlng}")
print(f"  skipped (no depth):   {no_depth}")
print(f"  plotted:              {len(rows)}")

counts = Counter(r["cls"] for r in rows)
print()
print("Class breakdown:")
for k, _, _, lbl, _ in BINS:
    print(f"  {lbl:30s} {counts.get(k, 0):4d}")

# ---------- Leaflet HTML ----------
print(f"\nWriting Leaflet HTML -> {OUT_HTML.name} ...")
pts_js = "\n".join(
    "{{uid:'{uid}', lat:{lat:.6f}, lng:{lng:.6f}, depth:{d:.0f}, cls:'{cls}', color:'{c}', label:'{lbl}', hp:{hp}, name:'{nm}'}},".format(
        uid=r["uid"], lat=r["lat"], lng=r["lng"], d=r["depth_ft"],
        cls=r["cls"], c=r["color"], lbl=html.escape(r["label"]),
        hp=("null" if r["motor_hp"] is None else r["motor_hp"]),
        nm=html.escape((r.get("pump_name") or "")[:60]).replace("'", "&#39;"),
    ) for r in rows)

legend_rows = "\n".join(
    f"<div class='legend-row'><span class='sw' style='background:{COLOR[k]}'></span>{html.escape(LABEL[k])} &middot; <b>{counts.get(k,0)}</b></div>"
    for k, _, _, _, _ in BINS)

wards_json_min = json.dumps({"type":"FeatureCollection",
    "features":[{"type":"Feature","geometry":f["geometry"],
                 "properties":{"ward_no":f["properties"].get("ward_no"),
                               "ward_name":f["properties"].get("ward_name")}}
                for f in wards["features"]]})

html_doc = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>BWSSB Borewell depth clusters</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css"/>
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<style>
  html,body,#map{{height:100%;margin:0;font:14px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;color:#0b3d4c}}
  .brand{{position:absolute;z-index:400;top:14px;left:14px;background:#fff;border:1px solid #e3e8ef;border-left:3px solid #ea580c;border-radius:10px;padding:10px 14px;box-shadow:0 4px 20px rgba(15,23,42,.07);max-width:360px}}
  .brand h1{{margin:0;font-size:15px;color:#0b3d4c}}
  .brand p{{margin:4px 0 0;color:#64748b;font-size:12px}}
  .legend{{position:absolute;z-index:400;bottom:18px;left:14px;background:#fff;border:1px solid #e3e8ef;border-radius:10px;padding:10px 14px;box-shadow:0 4px 20px rgba(15,23,42,.07);font-size:12.5px}}
  .legend h2{{margin:0 0 6px;font-size:13px;color:#0b3d4c}}
  .legend-row{{display:flex;align-items:center;gap:8px;margin:2px 0;color:#1f2937}}
  .sw{{display:inline-block;width:18px;height:18px;border-radius:50%;border:2px solid #ffffff;box-shadow:0 0 0 1px #0b3d4c33}}
  .pop{{font:13px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;color:#0b3d4c}}
  .pop b{{color:#0b3d4c}}
</style>
</head><body>
<div id="map"></div>
<div class="brand">
  <h1>BWSSB borewell depth clusters</h1>
  <p>Bangalore &middot; {len(rows)} sensors classed by physical depth from the KH dashboard &middot; ward polygons shown as reference only</p>
</div>
<div class="legend">
  <h2>Depth class</h2>
  {legend_rows}
</div>
<script>
const PTS = [
{pts_js}
];
const WARDS = {wards_json_min};
const map = L.map('map',{{zoomControl:true}}).setView([12.9716, 77.5946], 11);
L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{
  attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  maxZoom:19
}}).addTo(map);
const wardLayer = L.geoJSON(WARDS,{{
  style:()=>({{color:'#64748b',weight:0.6,fillOpacity:0.03,fillColor:'#64748b'}})
}}).addTo(map);
const groups = {{}};
for (const p of PTS){{
  const m = L.circleMarker([p.lat,p.lng],{{radius:7,color:'#ffffff',fillColor:p.color,fillOpacity:0.95,weight:1.5,className:'dot-'+p.cls}});
  m.bindPopup("<div class='pop'><b>UID "+p.uid+"</b><br/>"+p.label+"<br/>Depth: <b>"+p.depth+"</b> ft"+(p.hp!==null?"<br/>Motor HP: "+p.hp:"")+(p.name?"<br/>"+p.name:"")+"</div>");
  if (!groups[p.cls]) groups[p.cls] = L.layerGroup();
  m.addTo(groups[p.cls]);
}}
for (const k in groups) groups[k].addTo(map);
const overlays = {{}};
{chr(10).join([f"overlays['{LABEL[k]}'] = groups['{k}'];" for k,_,_,_,_ in BINS])}
L.control.layers(null, overlays, {{collapsed:false, position:'topright'}}).addTo(map);
</script>
</body></html>
"""
OUT_HTML.write_text(html_doc, encoding="utf-8")
print(f"  wrote {OUT_HTML}")

# ---------- static PNG ----------
print(f"\nRendering static PNG -> {OUT_PNG.name} ...")
fig, ax = plt.subplots(figsize=(13, 11))

# Draw ward polygons as background
patches = []
for feat in wards["features"]:
    geom = feat["geometry"]
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    for poly in polys:
        if not poly: continue
        outer = poly[0]
        patches.append(MplPoly([(x, y) for x, y in outer], closed=True))
coll = PatchCollection(patches, facecolor="#F1F5F9", edgecolor="#CBD5E1", linewidths=0.3)
ax.add_collection(coll)

# Plot borewells by class
for key, _, _, label, color in BINS:
    subs = [r for r in rows if r["cls"] == key]
    if not subs: continue
    xs = [r["lng"] for r in subs]
    ys = [r["lat"] for r in subs]
    ax.scatter(xs, ys, s=42, c=color, alpha=0.95, edgecolors="white", linewidths=0.9,
               label=f"{label} ({len(subs)})")

ax.set_xlabel("Longitude", fontsize=12)
ax.set_ylabel("Latitude",  fontsize=12)
ax.set_title("BWSSB borewell depth clusters -- 05.10.26 roster\n"
             "Four depth classes shown; ward polygons for reference only",
             fontsize=15, fontweight="bold")
ax.set_aspect("equal", adjustable="box")
ax.grid(True, alpha=0.3)

# Legend bottom-left outside the plot area for clean look
leg = ax.legend(loc="lower left", fontsize=12, title="Depth class",
                title_fontsize=12, frameon=True, framealpha=0.95)
leg.get_frame().set_edgecolor("#CBD5E1")

# Fit nicely to Bangalore extent of plotted points
if rows:
    xs = [r["lng"] for r in rows]; ys = [r["lat"] for r in rows]
    xmin, xmax = min(xs), max(xs); ymin, ymax = min(ys), max(ys)
    xpad = (xmax - xmin) * 0.03 or 0.01
    ypad = (ymax - ymin) * 0.03 or 0.01
    ax.set_xlim(xmin - xpad, xmax + xpad)
    ax.set_ylim(ymin - ypad, ymax + ypad)

plt.tight_layout()
plt.savefig(OUT_PNG, dpi=110, bbox_inches="tight")
plt.close()
print(f"  wrote {OUT_PNG}")

print("\nDone.")
