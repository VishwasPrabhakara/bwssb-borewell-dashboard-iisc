#!/usr/bin/env python3
"""Add DBSCAN cluster hulls per depth class on top of the depth map.
Reads the same water_stats + kh_meta caches build_depth_map.py uses.
Writes outputs/depth_clusters_YYYYMMDD.{html,png} and a cluster_stats.csv.
"""
import os, sys, json, pickle, math, argparse
from pathlib import Path
from datetime import date
from collections import defaultdict
import numpy as np
from sklearn.cluster import DBSCAN
from scipy.spatial import ConvexHull

ROOT = Path(__file__).resolve().parents[2]
CACHE_W = ROOT / "backend" / "_scratch" / "_water_stats_cache.pkl"
CACHE_K = ROOT / "backend" / "_scratch" / "_kh_meta_cache.pkl"

CLASSES = [
    ("<100 ft",    0,   100, "#1E3A8A"),
    ("100-250 ft", 100, 250, "#14532D"),
    ("250-500 ft", 250, 500, "#7C2D12"),
    (">500 ft",    500, 1e9, "#450A0A"),
]

def classify(v):
    if v is None or not np.isfinite(v): return None
    for name, lo, hi, _ in CLASSES:
        if lo <= v < hi: return name
    return None

def haversine_rad(a, b):
    # a, b in radians
    lat1, lon1 = a; lat2, lon2 = b
    dlat = lat2 - lat1; dlon = lon2 - lon1
    h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 2 * 6371000 * math.asin(math.sqrt(h))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--metric", default="median", choices=["min","max","mean","median","borewell_depth"])
    ap.add_argument("--eps-m", type=float, default=500.0, help="DBSCAN radius in meters")
    ap.add_argument("--min-samples", type=int, default=5)
    args = ap.parse_args()

    _raw = pickle.load(open(CACHE_W, "rb"))
    water = _raw.get("data", _raw)
    water = {str(k): v for k, v in water.items()}
    kh    = {str(k): v for k, v in pickle.load(open(CACHE_K, "rb")).items()}
    # water: {uid: {min,max,mean,median,n}}; kh: {uid: {lat,lng,borewell_depth,...}}
    # pick the cache value - adjust to match your cache shape

    rows = []
    for uid, m in kh.items():
        if uid not in water: continue
        lat, lng = m.get("lat"), m.get("lng")
        if lat is None or lng is None: continue
        if args.metric == "borewell_depth":
            v = m.get("borewell_depth")
        else:
            v = water[uid].get(args.metric)
        c = classify(v)
        if c is None: continue
        rows.append((uid, float(lat), float(lng), float(v), c))

    print(f"[clusters] usable points: {len(rows)}")

    # DBSCAN per class, haversine metric
    eps_rad = args.eps_m / 6371000.0
    class_hulls = defaultdict(list)   # class -> list of (hull_latlng_list, n_points)
    class_counts = {c[0]: 0 for c in CLASSES}
    stats_lines = ["class,eps_m,min_samples,points,clusters,noise"]

    for cname, _, _, _ in CLASSES:
        pts = [(r[1], r[2], r[0]) for r in rows if r[4] == cname]
        class_counts[cname] = len(pts)
        if len(pts) < args.min_samples:
            stats_lines.append(f"{cname},{args.eps_m},{args.min_samples},{len(pts)},0,{len(pts)}")
            continue
        X = np.radians(np.array([(p[0], p[1]) for p in pts]))
        db = DBSCAN(eps=eps_rad, min_samples=args.min_samples, metric="haversine").fit(X)
        labels = db.labels_
        n_clu = len(set(labels)) - (1 if -1 in labels else 0)
        n_noise = int(np.sum(labels == -1))
        stats_lines.append(f"{cname},{args.eps_m},{args.min_samples},{len(pts)},{n_clu},{n_noise}")
        for lab in sorted(set(labels)):
            if lab == -1: continue
            idx = np.where(labels == lab)[0]
            if len(idx) < 3:
                # too few for a hull - just a circle around centroid
                lat = float(np.mean([pts[i][0] for i in idx]))
                lng = float(np.mean([pts[i][1] for i in idx]))
                class_hulls[cname].append({"type":"point","center":[lat,lng],"n":int(len(idx))})
                continue
            coords = np.array([(pts[i][0], pts[i][1]) for i in idx])
            try:
                hull = ConvexHull(coords)
                poly = [[float(coords[v,0]), float(coords[v,1])] for v in hull.vertices]
                class_hulls[cname].append({"type":"polygon","hull":poly,"n":int(len(idx))})
            except Exception:
                pass

    today = date.today().strftime("%Y%m%d")
    out_dir = ROOT / "outputs"
    out_dir.mkdir(exist_ok=True)
    csv_path = out_dir / f"depth_cluster_stats_{today}.csv"
    csv_path.write_text("\n".join(stats_lines))
    print(f"[clusters] stats -> {csv_path}")

    # Build HTML with hulls + points
    points_js = json.dumps([{"uid": r[0], "lat": r[1], "lng": r[2], "v": r[3], "cls": r[4]} for r in rows])
    hulls_js  = json.dumps({k: v for k, v in class_hulls.items()})
    palette = {c[0]: c[3] for c in CLASSES}
    legend_rows = "".join(
        f'<div class="lg" style="--c:{c[3]}"><span class="sw"></span>{c[0]}<span class="ct">({class_counts[c[0]]})</span></div>'
        for c in CLASSES
    )

    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Depth clusters ({args.metric})</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
 html,body,#map{{margin:0;height:100%;background:#111}}
 .legend{{position:absolute;top:12px;right:12px;background:#1a1a1a;color:#eee;padding:10px 12px;border-radius:8px;font:13px system-ui;z-index:1000}}
 .lg{{display:flex;align-items:center;gap:8px;padding:3px 0;cursor:pointer}}
 .lg .sw{{width:14px;height:14px;background:var(--c);border:1px solid #000}}
 .lg .ct{{color:#999;margin-left:4px;font-size:11px}}
 .title{{position:absolute;top:12px;left:12px;background:#1a1a1a;color:#eee;padding:8px 12px;border-radius:8px;font:13px system-ui;z-index:1000}}
</style></head><body>
<div id="map"></div>
<div class="title">Depth clusters Â· DBSCAN eps={args.eps_m:.0f} m Â· min_samples={args.min_samples}<br>metric: {args.metric}</div>
<div class="legend">{legend_rows}</div>
<script>
const PAL = {json.dumps(palette)};
const POINTS = {points_js};
const HULLS  = {hulls_js};
const map = L.map('map').setView([12.97, 77.59], 11);
L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{attribution:'OSM'}}).addTo(map);
// Draw hulls underneath
for (const [cls, items] of Object.entries(HULLS)) {{
  const col = PAL[cls];
  for (const it of items) {{
    if (it.type === 'polygon') {{
      L.polygon(it.hull, {{color: col, weight: 2, fillColor: col, fillOpacity: 0.18, dashArray: '4,3'}})
        .bindTooltip(`${{cls}} cluster Â· n=${{it.n}}`).addTo(map);
    }} else {{
      L.circle(it.center, {{radius: 300, color: col, weight: 2, fillColor: col, fillOpacity: 0.18, dashArray: '4,3'}})
        .bindTooltip(`${{cls}} mini-cluster Â· n=${{it.n}}`).addTo(map);
    }}
  }}
}}
// Draw points on top
for (const p of POINTS) {{
  L.circleMarker([p.lat, p.lng], {{radius: 4, color:'#000', weight: 1, fillColor: PAL[p.cls], fillOpacity: 0.95}})
    .bindTooltip(`${{p.uid}}<br>${{p.cls}} Â· ${{p.v.toFixed(1)}} ft`).addTo(map);
}}
</script></body></html>"""
    html_path = out_dir / f"depth_clusters_{today}.html"
    html_path.write_text(html, encoding="utf-8")
    print(f"[clusters] html  -> {html_path}")

if __name__ == "__main__":
    main()
