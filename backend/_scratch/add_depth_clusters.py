#!/usr/bin/env python3
"""Depth clusters with eps slider, min_samples slider, ward overlay, DEM hillshade toggle."""
import os, sys, json, pickle, math, argparse
from pathlib import Path
from datetime import date
import numpy as np
from sklearn.cluster import DBSCAN
from scipy.spatial import ConvexHull
from shapely.geometry import shape, Point

ROOT    = Path(__file__).resolve().parents[2]
CACHE_W = ROOT / "backend" / "_scratch" / "_water_stats_cache.pkl"
CACHE_K = ROOT / "backend" / "_scratch" / "_kh_meta_cache.pkl"
WARDS   = ROOT / "data" / "wards.geojson"
DEM_META= ROOT / "outputs" / "bangalore_dem_bounds.json"

CLASSES = [
    ("<100 ft",    0,   100, "#1E3A8A"),
    ("100-250 ft", 100, 250, "#14532D"),
    ("250-500 ft", 250, 500, "#7C2D12"),
    (">500 ft",    500, 1e9, "#450A0A"),
]
EPS_LADDER = [100, 200, 300, 500, 750, 1000, 1500, 2000]
MS_LADDER  = [3, 4, 5, 6, 7, 8, 9, 10]

def classify(v):
    if v is None or not np.isfinite(v): return None
    for name, lo, hi, _ in CLASSES:
        if lo <= v < hi: return name
    return None

def compute_clusters(pts, eps_m, min_samples):
    if len(pts) < min_samples:
        return [-1]*len(pts), 0, len(pts)
    X = np.radians(np.array(pts))
    db = DBSCAN(eps=eps_m/6371000.0, min_samples=min_samples, metric="haversine").fit(X)
    labels = db.labels_.tolist()
    return labels, len(set(labels)) - (1 if -1 in labels else 0), sum(1 for l in labels if l == -1)

def hull_for(coords):
    if len(coords) < 3:
        c = coords.mean(axis=0)
        return {"type":"point","center":[float(c[0]),float(c[1])],"n":int(len(coords))}
    try:
        h = ConvexHull(coords)
        return {"type":"polygon",
                "hull":[[float(coords[v,0]), float(coords[v,1])] for v in h.vertices],
                "n": int(len(coords))}
    except Exception:
        c = coords.mean(axis=0)
        return {"type":"point","center":[float(c[0]),float(c[1])],"n":int(len(coords))}

def load_wards():
    gj = json.loads(WARDS.read_text(encoding="utf-8"))
    out = []
    for f in gj.get("features", []):
        props = f.get("properties", {})
        name  = (props.get("ward_name") or props.get("WARD_NAME") or
                 props.get("name") or props.get("KGISWardName") or
                 props.get("Ward_Name") or f"Ward {props.get('ward_no') or props.get('WARD_NO') or '?'}")
        num   = (props.get("ward_no") or props.get("WARD_NO") or props.get("ward_number") or "")
        geom  = shape(f["geometry"]) if f.get("geometry") else None
        if geom is None or geom.is_empty: continue
        out.append({"name": str(name), "num": str(num), "geom": geom, "raw": f})
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--metric", default="median", choices=["min","max","mean","median"])
    args = ap.parse_args()

    _raw  = pickle.load(open(CACHE_W, "rb"))
    water = _raw.get("data", _raw)
    water = {str(k): v for k, v in water.items()}
    kh    = {str(k): v for k, v in pickle.load(open(CACHE_K, "rb")).items()}
    wards = load_wards()
    print(f"[wards] loaded {len(wards)}")

    rows = []
    for uid, m in kh.items():
        if uid not in water: continue
        lat, lng = m.get("lat"), m.get("lng")
        if lat is None or lng is None: continue
        v = water[uid].get(args.metric)
        c = classify(v)
        if c is None: continue
        pt = Point(lng, lat)
        ward_name = ""
        for w in wards:
            if w["geom"].contains(pt):
                ward_name = f"{w['name']}" + (f" (#{w['num']})" if w['num'] else "")
                break
        rows.append((uid, float(lat), float(lng), float(v), c, ward_name))
    print(f"[clusters] usable points: {len(rows)}")

    class_counts = {c[0]: 0 for c in CLASSES}
    hulls = {}; stats = {}
    for eps in EPS_LADDER:
        hulls[eps] = {}; stats[eps] = {}
        for ms in MS_LADDER:
            hulls[eps][ms] = {}; stats[eps][ms] = {}
            for cname, _, _, _ in CLASSES:
                pts = [(r[1], r[2]) for r in rows if r[4] == cname]
                class_counts[cname] = len(pts)
                labels, n_clu, n_noi = compute_clusters(pts, eps, ms)
                h_list = []
                for lab in sorted(set(labels)):
                    if lab == -1: continue
                    idx = [i for i,l in enumerate(labels) if l == lab]
                    coords = np.array([pts[i] for i in idx])
                    h_list.append(hull_for(coords))
                hulls[eps][ms][cname] = h_list
                stats[eps][ms][cname] = {"points": len(pts), "clusters": n_clu, "noise": n_noi}
        print(f"  eps={eps:5d}m computed for min_samples 3..10")

    palette = {c[0]: c[3] for c in CLASSES}
    today   = date.today().strftime("%Y%m%d")
    out_dir = ROOT / "outputs"; out_dir.mkdir(exist_ok=True)

    csv_lines = ["eps_m,min_samples,class,points,clusters,noise"]
    for eps in EPS_LADDER:
        for ms in MS_LADDER:
            for cname, _, _, _ in CLASSES:
                s = stats[eps][ms][cname]
                csv_lines.append(f"{eps},{ms},{cname},{s['points']},{s['clusters']},{s['noise']}")
    (out_dir / f"depth_cluster_stats_{today}.csv").write_text("\n".join(csv_lines))

    wards_slim = {"type":"FeatureCollection","features":[
        {"type":"Feature",
         "properties":{"ward_name": w["name"], "ward_num": w["num"]},
         "geometry": w["raw"]["geometry"]}
        for w in wards
    ]}

    # DEM metadata (optional - toggle is disabled if missing)
    dem_info = None
    if DEM_META.exists():
        dem_info = json.loads(DEM_META.read_text())
        print(f"[dem] overlay enabled -> {dem_info['image']}")
    else:
        print("[dem] no bounds file found; DEM toggle will be disabled. Run prepare_dem.py first.")

    tmpl = r"""<!doctype html><html><head><meta charset="utf-8">
<title>Depth Clusters - Bangalore Groundwater</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
 html,body,#map{margin:0;height:100%;background:#0a0a0a;font-family:system-ui,-apple-system,sans-serif}
 #map{filter:saturate(0.78)}
 .panel{position:absolute;background:#111;color:#eee;border-radius:10px;z-index:1000;
       box-shadow:0 4px 20px rgba(0,0,0,0.6);padding:14px 16px;font-size:13px}
 .title{top:12px;left:12px;max-width:360px}
 .title h1{margin:0 0 4px 0;font-size:16px;font-weight:600}
 .title .sub{color:#999;font-size:12px;line-height:1.5}
 .controls{top:12px;right:12px;min-width:260px}
 .controls .lbl{display:flex;justify-content:space-between;font-size:12px;color:#aaa;margin-bottom:4px;margin-top:12px}
 .controls .lbl:first-of-type{margin-top:0}
 .controls .lbl b{color:#fff;font-weight:600}
 .controls input[type=range]{width:100%;accent-color:#f59e0b}
 .controls .ticks{display:flex;justify-content:space-between;font-size:10px;color:#666;margin-top:2px}
 .controls .toggle{margin-top:10px;display:flex;align-items:center;gap:6px;font-size:12px;color:#aaa;cursor:pointer}
 .controls .toggle input{accent-color:#f59e0b}
 .controls .toggle.disabled{opacity:0.4;cursor:not-allowed}
 .legend{bottom:12px;right:12px;min-width:220px}
 .legend h2{margin:0 0 8px 0;font-size:13px;font-weight:600;color:#fff}
 .lg{display:flex;align-items:center;gap:8px;padding:5px 6px;cursor:pointer;border-radius:5px}
 .lg:hover{background:#1e1e1e}
 .lg.off{opacity:0.35}
 .lg .sw{width:16px;height:16px;background:var(--c);border:1px solid #000;flex-shrink:0;border-radius:3px}
 .lg .lbl2{flex:1;color:#eee}
 .lg .ct{color:#888;font-size:11px;min-width:70px;text-align:right}
 .stats{bottom:12px;left:12px;min-width:300px;max-width:340px}
 .stats h2{margin:0 0 8px 0;font-size:13px;font-weight:600;color:#fff}
 .srow{display:grid;grid-template-columns:14px 1fr 70px 50px;gap:8px;font-size:12px;padding:3px 0;color:#ccc;align-items:center}
 .srow.head{color:#888;font-size:10px;text-transform:uppercase;border-bottom:1px solid #333;padding-bottom:4px;margin-bottom:4px}
 .srow b{color:#fff;font-weight:600}
 .srow.clickable{cursor:pointer;border-radius:4px;padding:4px 6px;margin:1px -6px}
 .srow.clickable:hover{background:#1e1e1e}
 .srow.clickable.iso{background:#1f2937;outline:1px solid #f59e0b}
 .reset-btn{margin-top:8px;background:#1e1e1e;color:#f59e0b;border:1px solid #333;border-radius:4px;padding:5px 10px;font-size:11px;cursor:pointer;width:100%;display:none}
 .reset-btn.show{display:block}
 .ward-label{background:rgba(0,0,0,0.75);color:#fff;border:none;padding:2px 6px;border-radius:3px;font-size:11px;font-weight:500}
 .dem-opacity{margin-top:6px;display:none}
 .dem-opacity.show{display:block}
</style></head><body>
<div id="map"></div>

<div class="panel title">
  <h1>Depth clusters by water level (median)</h1>
  <div class="sub">Dots = borewell sensors colored by median water level. Polygons = DBSCAN clusters. Adjust radius and the minimum wells needed to form a cluster. Toggle terrain for elevation context.</div>
</div>

<div class="panel controls">
  <div class="lbl"><span>Cluster radius</span><b id="epsLabel">500 m</b></div>
  <input id="epsSlider" type="range" min="0" max="7" step="1" value="3">
  <div class="ticks"><span>100m</span><span>500m</span><span>1km</span><span>2km</span></div>

  <div class="lbl"><span>Min wells per cluster</span><b id="msLabel">3</b></div>
  <input id="msSlider" type="range" min="0" max="7" step="1" value="0">
  <div class="ticks"><span>3</span><span>5</span><span>7</span><span>10</span></div>

  <label class="toggle"><input type="checkbox" id="wardToggle" checked>Show ward boundaries</label>
  <label class="toggle"><input type="checkbox" id="wardNameToggle">Show ward names</label>
  <label class="toggle" id="demToggleWrap"><input type="checkbox" id="demToggle">Show elevation (terrain)</label>
  <div class="dem-opacity" id="demOpWrap">
    <div class="lbl"><span>Terrain opacity</span><b id="demOpLabel">60%</b></div>
    <input id="demOpSlider" type="range" min="0" max="100" step="5" value="60">
  </div>
</div>

<div class="panel legend">
  <h2>Depth classes <span style="color:#666;font-weight:400;font-size:11px">(click to toggle)</span></h2>
  <div id="legendRows"></div>
</div>

<div class="panel stats">
  <h2>Clusters at <span id="statsEps">500 m</span> / min <span id="statsMs">3</span>
    <span style="color:#666;font-weight:400;font-size:11px">(click to isolate)</span></h2>
  <div class="srow head"><span></span><span>Class</span><span>Clusters</span><span>Noise</span></div>
  <div id="statsRows"></div>
  <button id="resetBtn" class="reset-btn">Show all classes</button>
</div>

<script>
const PAL      = __PAL__;
const COUNTS   = __COUNTS__;
const POINTS   = __POINTS__;
const HULLS    = __HULLS__;
const STATS    = __STATS__;
const EPS_LAD  = __EPS__;
const MS_LAD   = __MS__;
const CLASSES  = __CLASSES__;
const WARDS_GJ = __WARDS__;
const DEM      = __DEM__;
const hidden   = new Set();

const map = L.map('map', {zoomControl: true}).setView([12.97, 77.59], 11);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  attribution:'&copy; OpenStreetMap | BBMP wards | DEM: ISRO/USGS', maxZoom: 18
}).addTo(map);

// DEM overlay (hillshade) - loaded only if DEM metadata exists
let demOverlay = null;
if (DEM) {
  demOverlay = L.imageOverlay(DEM.image, [[DEM.south, DEM.west], [DEM.north, DEM.east]],
                              {opacity: 0.6, interactive: false});
} else {
  const el = document.getElementById('demToggleWrap');
  el.classList.add('disabled');
  el.querySelector('input').disabled = true;
  el.title = 'Run prepare_dem.py to enable';
}

const wardLayer = L.geoJSON(WARDS_GJ, {
  style: {color: '#1f2937', weight: 1.8, opacity: 0.9, fillColor: '#ffffff', fillOpacity: 0},
  onEachFeature: (feature, layer) => {
    const n = feature.properties.ward_name, no = feature.properties.ward_num;
    const label = no ? n + ' (#' + no + ')' : n;
    layer.bindTooltip(label, {sticky: true, className: 'ward-label'});
    layer.on('mouseover', e => e.target.setStyle({weight: 3, color: '#f59e0b', fillOpacity: 0.08}));
    layer.on('mouseout',  e => wardLayer.resetStyle(e.target));
  }
}).addTo(map);

const wardNameLayer = L.layerGroup();
let hullLayer = L.layerGroup().addTo(map);
let pointLayer = L.layerGroup().addTo(map);

function fmtEps(m) { return m >= 1000 ? (m/1000).toFixed(m%1000?1:0)+' km' : m+' m'; }
function curEps() { return EPS_LAD[document.getElementById('epsSlider').value]; }
function curMs()  { return MS_LAD[document.getElementById('msSlider').value]; }

function drawPoints() {
  pointLayer.clearLayers();
  for (const p of POINTS) {
    if (hidden.has(p.cls)) continue;
    const tip = '<b>' + p.uid + '</b><br>' + p.cls + ' - ' + p.v.toFixed(1) + ' ft median' +
                (p.ward ? '<br><span style="color:#fde047">' + p.ward + '</span>' : '');
    L.circleMarker([p.lat, p.lng], {radius: 4, color:'#000', weight:1,
      fillColor: PAL[p.cls], fillOpacity: 0.95}).bindTooltip(tip).addTo(pointLayer);
  }
}

function drawHulls(eps, ms) {
  hullLayer.clearLayers();
  for (const cls of CLASSES) {
    if (hidden.has(cls)) continue;
    const col = PAL[cls];
    const items = (HULLS[eps] && HULLS[eps][ms] && HULLS[eps][ms][cls]) || [];
    for (const it of items) {
      if (it.type === 'polygon') {
        L.polygon(it.hull, {color: col, weight: 2.5, opacity: 0.9,
          fillColor: col, fillOpacity: 0.45, dashArray: '6,4'})
         .bindTooltip('<b>' + cls + '</b> cluster<br>' + it.n + ' wells').addTo(hullLayer);
      } else {
        L.circle(it.center, {radius: 250, color: col, weight: 2.5, opacity: 0.9,
          fillColor: col, fillOpacity: 0.45, dashArray: '6,4'})
         .bindTooltip('<b>' + cls + '</b> mini-cluster<br>' + it.n + ' wells').addTo(hullLayer);
      }
    }
  }
}

function renderStats(eps, ms) {
  const el = document.getElementById('statsRows');
  el.innerHTML = CLASSES.map(c => {
    const s = STATS[eps][ms][c];
    const iso = (hidden.size === CLASSES.length - 1 && !hidden.has(c)) ? ' iso' : '';
    return '<div class="srow clickable' + iso + '" data-c="' + c + '">' +
           '<span style="color:' + PAL[c] + '">&#9679;</span>' +
           '<span>' + c + '</span><b>' + s.clusters + '</b><span>' + s.noise + '</span></div>';
  }).join('');
  el.querySelectorAll('.srow.clickable').forEach(r => r.addEventListener('click', () => {
    const c = r.dataset.c;
    if (hidden.size === CLASSES.length - 1 && !hidden.has(c)) { hidden.clear(); }
    else { hidden.clear(); CLASSES.filter(x => x !== c).forEach(x => hidden.add(x)); }
    document.getElementById('resetBtn').classList.toggle('show', hidden.size > 0);
    refresh();
  }));
  document.getElementById('statsEps').textContent = fmtEps(eps);
  document.getElementById('statsMs').textContent  = ms;
}

function renderLegend() {
  const el = document.getElementById('legendRows');
  el.innerHTML = CLASSES.map(c =>
    '<div class="lg' + (hidden.has(c) ? ' off' : '') + '" data-c="' + c + '" style="--c:' + PAL[c] + '">' +
    '<span class="sw"></span><span class="lbl2">' + c + '</span><span class="ct">' + COUNTS[c] + ' wells</span></div>'
  ).join('');
  el.querySelectorAll('.lg').forEach(r => r.addEventListener('click', () => {
    const c = r.dataset.c;
    if (hidden.has(c)) hidden.delete(c); else hidden.add(c);
    document.getElementById('resetBtn').classList.toggle('show', hidden.size > 0);
    refresh();
  }));
}

function refresh() {
  const e = curEps(), m = curMs();
  renderLegend();
  renderStats(e, m);
  drawHulls(e, m);
  drawPoints();
}

document.getElementById('wardToggle').addEventListener('change', e => {
  if (e.target.checked) wardLayer.addTo(map); else map.removeLayer(wardLayer);
});
document.getElementById('wardNameToggle').addEventListener('change', e => {
  if (e.target.checked) {
    wardNameLayer.clearLayers();
    WARDS_GJ.features.forEach(f => {
      try {
        const c = L.geoJSON(f).getBounds().getCenter();
        L.marker(c, {icon: L.divIcon({className:'ward-label', html:f.properties.ward_name, iconSize:null})}).addTo(wardNameLayer);
      } catch(e) {}
    });
    wardNameLayer.addTo(map);
  } else map.removeLayer(wardNameLayer);
});
document.getElementById('demToggle').addEventListener('change', e => {
  if (!demOverlay) return;
  const op = document.getElementById('demOpWrap');
  if (e.target.checked) { demOverlay.addTo(map); op.classList.add('show'); }
  else { map.removeLayer(demOverlay); op.classList.remove('show'); }
});
document.getElementById('demOpSlider').addEventListener('input', e => {
  const v = +e.target.value / 100;
  document.getElementById('demOpLabel').textContent = e.target.value + '%';
  if (demOverlay) demOverlay.setOpacity(v);
});
document.getElementById('resetBtn').addEventListener('click', () => {
  hidden.clear();
  document.getElementById('resetBtn').classList.remove('show');
  refresh();
});

const eSl = document.getElementById('epsSlider');
const mSl = document.getElementById('msSlider');
eSl.max = EPS_LAD.length - 1;
mSl.max = MS_LAD.length - 1;
eSl.addEventListener('input', () => {
  document.getElementById('epsLabel').textContent = fmtEps(curEps());
  refresh();
});
mSl.addEventListener('input', () => {
  document.getElementById('msLabel').textContent = curMs();
  refresh();
});

document.getElementById('epsLabel').textContent = fmtEps(curEps());
document.getElementById('msLabel').textContent  = curMs();
refresh();
</script></body></html>"""

    html = (tmpl
            .replace("__PAL__",     json.dumps(palette))
            .replace("__COUNTS__",  json.dumps(class_counts))
            .replace("__POINTS__",  json.dumps([{"uid":r[0],"lat":r[1],"lng":r[2],"v":r[3],"cls":r[4],"ward":r[5]} for r in rows]))
            .replace("__HULLS__",   json.dumps(hulls))
            .replace("__STATS__",   json.dumps(stats))
            .replace("__EPS__",     json.dumps(EPS_LADDER))
            .replace("__MS__",      json.dumps(MS_LADDER))
            .replace("__CLASSES__", json.dumps([c[0] for c in CLASSES]))
            .replace("__WARDS__",   json.dumps(wards_slim))
            .replace("__DEM__",     json.dumps(dem_info) if dem_info else "null"))

    out = out_dir / f"depth_clusters_{today}.html"
    out.write_text(html, encoding="utf-8")
    print(f"[clusters] html -> {out} ({len(html)//1024} KB)")

if __name__ == "__main__":
    main()