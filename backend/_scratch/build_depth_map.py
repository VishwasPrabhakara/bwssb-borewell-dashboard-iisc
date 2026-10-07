# -*- coding: utf-8 -*-
"""Depth-class map of Bangalore borewells, classified by WATER-LEVEL statistics
(not by physical borewell depth).

Per sensor the ZIP is parsed to extract every water-level reading, from which
four stats are computed:
    min   = shallowest reading       (water highest = smallest ft-below-ground)
    max   = deepest reading          (water lowest  = largest ft-below-ground)
    mean  = arithmetic mean
    median = statistical median

The borewell physical depth from the KH dashboard is also kept as a fifth
option. In the HTML a dropdown lets the viewer switch which metric is used
to colour the dots; a clickable legend toggles each depth class on/off.

Four depth bins (ft):
    < 100            Shallow     (sky blue)
    100 - 250        Medium      (bright green)
    250 - 500        Deep        (bright orange)
    > 500            Very deep   (bright red)

Outputs:
    outputs/depth_map_<DATE>.html       -- interactive Leaflet, OSM tiles
    outputs/depth_map_<DATE>.png        -- static matplotlib (classified by default metric)

Run:
    python backend/_scratch/build_depth_map.py              # fresh/missing scrape, picks metric from --metric
    python backend/_scratch/build_depth_map.py --metric max
    python backend/_scratch/build_depth_map.py --refresh    # force full KH refetch and water-stat recompute
"""
import os, re, sys, json, html, pickle, zipfile, argparse, statistics
import datetime as dt
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from python_calamine import CalamineWorkbook
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MplPoly
from matplotlib.collections import PatchCollection

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from _kh_meta import fetch_for_uids

ROOT = HERE.parents[1]
DATA = ROOT / "data"
TODAY = dt.datetime.now().strftime("%Y%m%d")
OUT_HTML = ROOT / "outputs" / f"depth_map_{TODAY}.html"
OUT_PNG  = ROOT / "outputs" / f"depth_map_{TODAY}.png"
OUT_HTML.parent.mkdir(parents=True, exist_ok=True)

KH_CACHE    = HERE / "_kh_meta_cache.pkl"
WATER_CACHE = HERE / "_water_stats_cache.pkl"

ZIP_PATH = Path(os.environ.get("KH_ZIP",
                               Path.home() / "Downloads" / "borewell_data_05.10.26.zip"))

MAX_WATER_LEVEL_FT = 1500   # drop obviously impossible readings only

# ---------------- bins ----------------
BINS = [
    (">500",   float("inf"),  500,  "Very deep (> 500 ft)",   "#450A0A"),
    ("250-500",500,           250,  "Deep (250 - 500 ft)",    "#7C2D12"),
    ("100-250",250,           100,  "Medium (100 - 250 ft)",  "#14532D"),
    ("<100",   100,           0,    "Shallow (< 100 ft)",     "#1E3A8A"),
]
LABEL = {k: lbl for k, _, _, lbl, _ in BINS}
COLOR = {k: col for k, _, _, _, col in BINS}

def classify(d):
    if d is None or d <= 0: return None
    for key, hi, lo, _, _ in BINS:
        if lo < d <= hi: return key
    return None

# ---------------- water-level per sensor from ZIP ----------------
def _parse_one(args):
    """Worker: parse one xlsx -> (uid, {min,max,mean,median,n})."""
    name, data = args
    try:
        wb = CalamineWorkbook.from_filelike(__import__("io").BytesIO(data))
        rows = wb.get_sheet_by_index(0).to_python()
        uid = None
        if rows:
            for cell in rows[0]:
                if cell is None: continue
                s = str(cell)
                m = re.match(r"UID:(.+)", s)
                if m: uid = m.group(1).strip(); break
        if uid is None:
            uid = Path(name).stem.split("_")[0]
        vals = []
        for r in rows[2:]:
            if not r or r[0] is None: continue
            w = r[1] if len(r) > 1 else None
            if not isinstance(w, (int, float)): continue
            if w < 0 or w > MAX_WATER_LEVEL_FT: continue
            vals.append(float(w))
        if not vals:
            return uid, None
        return uid, {
            "min":    min(vals),
            "max":    max(vals),
            "mean":   round(sum(vals) / len(vals), 2),
            "median": round(statistics.median(vals), 2),
            "n":      len(vals),
        }
    except Exception as e:
        return None, None


def water_stats_from_zip(zip_path, refresh=False):
    """Return dict uid -> {min,max,mean,median,n}. Cached by ZIP mtime."""
    key = f"{zip_path.resolve()}::{zip_path.stat().st_mtime_ns}::{zip_path.stat().st_size}"
    if WATER_CACHE.exists() and not refresh:
        try:
            saved = pickle.loads(WATER_CACHE.read_bytes())
            if saved.get("key") == key:
                data = saved.get("data") or {}
                print(f"  water-stats cache hit: {len(data)} sensors (ZIP unchanged)")
                return data
            else:
                print(f"  water-stats cache stale (ZIP changed); recomputing")
        except Exception as e:
            print(f"  cache unreadable ({e}); recomputing")

    print(f"  parsing ZIP for water levels: {zip_path.name}")
    with zipfile.ZipFile(zip_path) as z:
        names = sorted(n for n in z.namelist() if n.lower().endswith(".xlsx"))
        payloads = [(n, z.read(n)) for n in names]
    workers = max(1, (os.cpu_count() or 2) - 1)
    print(f"    {len(payloads)} xlsx; parsing with {workers} worker(s) ...")
    out = {}
    done = 0
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_parse_one, p): p[0] for p in payloads}
        for fut in as_completed(futs):
            uid, stats = fut.result()
            if uid and stats:
                out[uid] = stats
            done += 1
            if done % 100 == 0 or done == len(payloads):
                print(f"    parsed {done}/{len(payloads)}")
    WATER_CACHE.write_bytes(pickle.dumps({"key": key, "data": out}))
    print(f"  water-stats cached to {WATER_CACHE.name} ({len(out)} sensors)")
    return out

# ---------------- KH meta (lat/lng/HP/depth) with incremental cache ----------------
def kh_meta_for(uids, refresh=False):
    cached = {}
    if KH_CACHE.exists() and not refresh:
        try: cached = pickle.loads(KH_CACHE.read_bytes())
        except Exception: cached = {}
    missing = sorted(set(uids) - set(cached.keys()))
    if missing:
        print(f"  KH meta: fetching {len(missing)} missing UID(s) ...")
        fresh = fetch_for_uids(missing, workers=12, sleep_s=0.03)
        cached.update(fresh)
        KH_CACHE.write_bytes(pickle.dumps(cached))
        print(f"  KH meta cache now holds {len(cached)} UIDs")
    else:
        print(f"  KH meta cache covers all {len(uids)} UIDs; no fetch")
    return {uid: cached[uid] for uid in uids if uid in cached}


# ---------------- main ----------------
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--metric", choices=["mean", "median", "min", "max", "borewell_depth"],
                    default="median",
                    help="Which value to classify by (default: median water level).")
    ap.add_argument("--refresh", action="store_true",
                    help="Ignore both caches, force re-scrape and re-parse.")
    args = ap.parse_args()

    print(f"ZIP: {ZIP_PATH}")
    with zipfile.ZipFile(ZIP_PATH) as z:
        uid_set = set()
        for n in z.namelist():
            if not n.lower().endswith(".xlsx"): continue
            m = re.match(r"(\d+)", Path(n).stem.split("_")[0])
            if m: uid_set.add(m.group(1))
    print(f"  {len(uid_set)} unique UIDs in ZIP")

    water = water_stats_from_zip(ZIP_PATH, refresh=args.refresh)
    kh    = kh_meta_for(sorted(uid_set), refresh=args.refresh)
    wards = json.load(open(DATA / "wards.geojson"))

    # Build the per-sensor table with all five metrics stored
    rows = []
    for uid in sorted(uid_set):
        w = water.get(uid) or {}
        m = kh.get(uid) or {}
        lat, lng = m.get("lat"), m.get("lng")
        if lat is None or lng is None: continue
        metrics = {
            "min":    w.get("min"),
            "max":    w.get("max"),
            "mean":   w.get("mean"),
            "median": w.get("median"),
            "borewell_depth": m.get("borewell_depth"),
        }
        rows.append({
            "uid": uid, "lat": float(lat), "lng": float(lng),
            "metrics": metrics,
            "n": w.get("n"),
            "motor_hp": m.get("motor_hp"), "pump_name": m.get("pump_name"),
        })

    print(f"  sensors with lat/lng: {len(rows)}")
    for metric in ["min", "max", "mean", "median", "borewell_depth"]:
        c = Counter(classify(r["metrics"].get(metric)) for r in rows)
        print(f"    by {metric:15s}  : " + "  ".join(f"{k or 'n/a'}:{v}" for k, v in c.most_common()))

    # ---------- HTML ----------
    print(f"\nWriting Leaflet HTML -> {OUT_HTML.name} ...")
    # Precompute class per metric per row (so JS doesn't need the thresholds)
    def _cls_js(d):
        c = classify(d)
        return "null" if c is None else f"'{c}'"
    pts_js = ",\n".join(
        "{{uid:'{uid}', lat:{lat:.6f}, lng:{lng:.6f}, n:{n}, hp:{hp}, name:'{nm}', "
        "metrics:{{min:{mn}, max:{mx}, mean:{mean}, median:{med}, borewell_depth:{bd}}}, "
        "cls:{{min:{cmin}, max:{cmax}, mean:{cmean}, median:{cmed}, borewell_depth:{cbd}}} }}".format(
            uid=r["uid"], lat=r["lat"], lng=r["lng"],
            n=(r["n"] or 0),
            hp=("null" if r["motor_hp"] is None else r["motor_hp"]),
            nm=html.escape((r.get("pump_name") or "")[:60]).replace("'", "&#39;"),
            mn=("null" if r["metrics"]["min"]    is None else r["metrics"]["min"]),
            mx=("null" if r["metrics"]["max"]    is None else r["metrics"]["max"]),
            mean=("null" if r["metrics"]["mean"]   is None else r["metrics"]["mean"]),
            med=("null" if r["metrics"]["median"] is None else r["metrics"]["median"]),
            bd=("null" if r["metrics"]["borewell_depth"] is None else r["metrics"]["borewell_depth"]),
            cmin=_cls_js(r["metrics"]["min"]), cmax=_cls_js(r["metrics"]["max"]),
            cmean=_cls_js(r["metrics"]["mean"]), cmed=_cls_js(r["metrics"]["median"]),
            cbd=_cls_js(r["metrics"]["borewell_depth"]),
        ) for r in rows)

    bins_js = ", ".join(
        f"{{key:'{k}', color:'{COLOR[k]}', label:'{html.escape(LABEL[k])}'}}"
        for k, _, _, _, _ in BINS)

    wards_min = json.dumps({"type":"FeatureCollection",
        "features":[{"type":"Feature","geometry":f["geometry"],
                     "properties":{"ward_no":f["properties"].get("ward_no"),
                                   "ward_name":f["properties"].get("ward_name")}}
                    for f in wards["features"]]})

    METRIC_LABELS = {
        "min":    "Min water level (ft)",
        "max":    "Max water level (ft)",
        "mean":   "Mean water level (ft)",
        "median": "Median water level (ft)",
        "borewell_depth": "Borewell depth (ft, KH)",
    }
    metric_options = "\n".join(
        f"<option value='{k}'{' selected' if k == args.metric else ''}>{v}</option>"
        for k, v in METRIC_LABELS.items())

    html_doc = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>BWSSB Borewell depth clusters</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css"/>
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<style>
  html,body,#map{{height:100%;margin:0;font:14px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;color:#0b3d4c}}
  .brand{{position:absolute;z-index:400;top:14px;left:14px;background:#fff;border:1px solid #e3e8ef;border-left:3px solid #ea580c;border-radius:10px;padding:10px 14px;box-shadow:0 4px 20px rgba(15,23,42,.07);max-width:380px}}
  .brand h1{{margin:0;font-size:15px;color:#0b3d4c}}
  .brand p{{margin:4px 0 0;color:#64748b;font-size:12px}}
  .ctrl{{position:absolute;z-index:400;top:14px;right:14px;background:#fff;border:1px solid #e3e8ef;border-radius:10px;padding:10px 14px;box-shadow:0 4px 20px rgba(15,23,42,.07);font-size:12.5px;min-width:220px}}
  .ctrl h2{{margin:0 0 6px;font-size:13px;color:#0b3d4c}}
  .ctrl label{{display:block;color:#475569;font-size:11px;letter-spacing:.3px;text-transform:uppercase;margin-bottom:4px}}
  .ctrl select{{width:100%;padding:5px 8px;border:1px solid #cbd5e1;border-radius:6px;background:#fff;color:#0b3d4c;font:inherit;cursor:pointer}}
  .legend{{position:absolute;z-index:400;bottom:18px;left:14px;background:#fff;border:1px solid #e3e8ef;border-radius:10px;padding:10px 14px;box-shadow:0 4px 20px rgba(15,23,42,.07);font-size:12.5px;min-width:220px}}
  .legend h2{{margin:0 0 6px;font-size:13px;color:#0b3d4c}}
  .legend-row{{display:flex;align-items:center;gap:8px;margin:2px 0;color:#1f2937;cursor:pointer;padding:3px 4px;border-radius:6px;transition:background 90ms}}
  .legend-row:hover{{background:#f1f5f9}}
  .legend-row.off{{opacity:.35}}
  .sw{{display:inline-block;width:18px;height:18px;border-radius:50%;border:2px solid #ffffff;box-shadow:0 0 0 1px #0b3d4c33}}
  .pop{{font:13px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;color:#0b3d4c}}
  .pop b{{color:#0b3d4c}}
</style>
</head><body>
<div id="map"></div>
<div class="brand">
  <h1>BWSSB borewell depth clusters</h1>
  <p>{len(rows)} sensors &middot; classified by water-level statistic (change below) &middot; ward polygons for reference</p>
</div>
<div class="ctrl">
  <h2>Classify by</h2>
  <label for="metric">Depth metric</label>
  <select id="metric">
    {metric_options}
  </select>
</div>
<div class="legend" id="legend">
  <h2>Depth class</h2>
</div>
<script>
const PTS = [
{pts_js}
];
const BINS = [{bins_js}];
const WARDS = {wards_min};
const META = {json.dumps(METRIC_LABELS)};

const map = L.map('map',{{zoomControl:true}}).setView([12.9716, 77.5946], 11);
L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{
  attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  maxZoom:19
}}).addTo(map);

L.geoJSON(WARDS,{{
  style:()=>({{color:'#64748b',weight:0.6,fillOpacity: 0.92,fillColor:'#64748b'}})
}}).addTo(map);

let currentMetric = document.getElementById('metric').value;
let hidden = new Set();     // class keys currently toggled off

const markers = [];          // {{m, p}}
for (const p of PTS){{
  const m = L.circleMarker([p.lat,p.lng],{{radius:7,color:'#ffffff',fillColor:'#999',fillOpacity: 0.92,weight:1.5}});
  m.bindPopup(''); // populated on open
  m.on('popupopen', () => {{
    const metric = currentMetric;
    const v = p.metrics[metric];
    const label = META[metric];
    m.setPopupContent(
      "<div class='pop'><b>UID "+p.uid+"</b><br/>"
      +label+": <b>"+(v===null?'n/a':v)+"</b> ft<br/>"
      +"min: "+(p.metrics.min===null?'-':p.metrics.min)+"  "
      +"max: "+(p.metrics.max===null?'-':p.metrics.max)+"<br/>"
      +"mean: "+(p.metrics.mean===null?'-':p.metrics.mean)+"  "
      +"median: "+(p.metrics.median===null?'-':p.metrics.median)+"<br/>"
      +"borewell depth: "+(p.metrics.borewell_depth===null?'-':p.metrics.borewell_depth)+" ft<br/>"
      +"readings: "+p.n
      +(p.hp!==null?"<br/>Motor HP: "+p.hp:"")
      +(p.name?"<br/>"+p.name:"")
      +"</div>"
    );
  }});
  m.addTo(map);
  markers.push({{m, p}});
}}

function refresh(){{
  const counts = {{}};
  for (const b of BINS) counts[b.key] = 0;
  for (const {{m, p}} of markers){{
    const cls = p.cls[currentMetric];
    const visible = cls && !hidden.has(cls);
    const b = BINS.find(x => x.key === cls);
    if (visible){{
      m.setStyle({{fillColor: b.color, opacity: 1, fillOpacity: 0.92}});
      m.addTo(map);
      counts[cls] = (counts[cls] || 0) + 1;
    }} else {{
      map.removeLayer(m);
    }}
  }}
  // rebuild legend
  const el = document.getElementById('legend');
  const metricLabel = META[currentMetric];
  el.innerHTML = "<h2>Depth class</h2><div style='font-size:11px;color:#64748b;margin-bottom:4px'>by "+metricLabel+"</div>";
  for (const b of BINS){{
    const off = hidden.has(b.key);
    const row = document.createElement('div');
    row.className = 'legend-row' + (off ? ' off' : '');
    row.innerHTML = "<span class='sw' style='background:"+b.color+"'></span>"
                  + b.label + " &middot; <b>"+(counts[b.key]||0)+"</b>";
    row.onclick = () => {{
      if (hidden.has(b.key)) hidden.delete(b.key); else hidden.add(b.key);
      refresh();
    }};
    el.appendChild(row);
  }}
}}

document.getElementById('metric').addEventListener('change', (e) => {{
  currentMetric = e.target.value;
  refresh();
}});
refresh();
</script>
</body></html>
"""
    OUT_HTML.write_text(html_doc, encoding="utf-8")
    print(f"  wrote {OUT_HTML}")

    # ---------- static PNG (uses the --metric chosen on CLI) ----------
    print(f"\nRendering static PNG (classified by {args.metric}) -> {OUT_PNG.name} ...")
    fig, ax = plt.subplots(figsize=(13, 11))
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
    for key, _, _, label, color in BINS:
        subs = [r for r in rows if classify(r["metrics"].get(args.metric)) == key]
        if not subs: continue
        xs = [r["lng"] for r in subs]; ys = [r["lat"] for r in subs]
        ax.scatter(xs, ys, s=42, c=color, alpha=0.95, edgecolors="white", linewidths=0.9,
                   label=f"{label} ({len(subs)})")
    ax.set_xlabel("Longitude", fontsize=12); ax.set_ylabel("Latitude", fontsize=12)
    ax.set_title(f"BWSSB borewell depth clusters -- classified by {METRIC_LABELS[args.metric]}\n"
                 "Four depth classes shown; ward polygons for reference only",
                 fontsize=14, fontweight="bold")
    ax.set_aspect("equal", adjustable="box"); ax.grid(True, alpha=0.3)
    leg = ax.legend(loc="lower left", fontsize=12, title="Depth class", frameon=True, framealpha=0.95)
    leg.get_frame().set_edgecolor("#CBD5E1")
    if rows:
        xs = [r["lng"] for r in rows]; ys = [r["lat"] for r in rows]
        xmin, xmax = min(xs), max(xs); ymin, ymax = min(ys), max(ys)
        xpad = (xmax - xmin) * 0.03 or 0.01
        ypad = (ymax - ymin) * 0.03 or 0.01
        ax.set_xlim(xmin - xpad, xmax + xpad); ax.set_ylim(ymin - ypad, ymax + ypad)
    plt.tight_layout(); plt.savefig(OUT_PNG, dpi=110, bbox_inches="tight"); plt.close()
    print(f"  wrote {OUT_PNG}")
    print("\nDone.")

