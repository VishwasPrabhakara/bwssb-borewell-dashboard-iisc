#!/usr/bin/env python3
"""Depth clusters with eps/min_samples sliders, ward overlay, per-cluster stats,
inline sensor chart with weekday filter + fullscreen, polished panel CSS."""
import os, sys, json, pickle, argparse
from pathlib import Path
from datetime import date
import numpy as np
from sklearn.cluster import DBSCAN
from scipy.spatial import ConvexHull
from shapely.geometry import shape, Point

ROOT     = Path(__file__).resolve().parents[2]
CACHE_W  = ROOT / "backend" / "_scratch" / "_water_stats_cache.pkl"
CACHE_K  = ROOT / "backend" / "_scratch" / "_kh_meta_cache.pkl"
WARDS    = ROOT / "data" / "wards.geojson"
DEM_META = ROOT / "outputs" / "bangalore_dem_tiles_meta.json"

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
    for n, lo, hi, _ in CLASSES:
        if lo <= v < hi: return n
    return None

def compute(pts, eps_m, ms):
    if len(pts) < ms: return [-1]*len(pts), 0, len(pts)
    X = np.radians(np.array(pts))
    d = DBSCAN(eps=eps_m/6371000.0, min_samples=ms, metric="haversine").fit(X)
    L = d.labels_.tolist()
    return L, len(set(L)) - (1 if -1 in L else 0), sum(1 for l in L if l == -1)

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
        p = f.get("properties", {})
        name = (p.get("ward_name") or p.get("WARD_NAME") or p.get("name") or
                f"Ward {p.get('ward_no') or p.get('WARD_NO') or '?'}")
        num  = p.get("ward_no") or p.get("WARD_NO") or p.get("ward_number") or ""
        geom = shape(f["geometry"]) if f.get("geometry") else None
        if geom is None or geom.is_empty: continue
        swd = int(p.get("sensor_with_data") or 0)
        out.append({"name": str(name), "num": str(num), "geom": geom, "raw": f, "swd": swd})
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
        pt = Point(lng, lat); wn = ""
        for w in wards:
            if w["geom"].contains(pt):
                wn = f"{w['name']}" + (f" (#{w['num']})" if w['num'] else "")
                break
        rows.append((uid, float(lat), float(lng), float(v), c, wn))
    print(f"[clusters] usable points: {len(rows)}")

    class_counts = {c[0]: 0 for c in CLASSES}
    hulls, stats = {}, {}
    for eps in EPS_LADDER:
        hulls[eps] = {}; stats[eps] = {}
        for ms in MS_LADDER:
            hulls[eps][ms] = {}; stats[eps][ms] = {}
            for cname, _, _, _ in CLASSES:
                pts_rows = [r for r in rows if r[4] == cname]
                pts = [(r[1], r[2]) for r in pts_rows]
                class_counts[cname] = len(pts)
                labels, nc, nn = compute(pts, eps, ms)
                hl = []
                for lab in sorted(set(labels)):
                    if lab == -1: continue
                    idx = [i for i, l in enumerate(labels) if l == lab]
                    coords = np.array([pts[i] for i in idx])
                    vals = np.array([pts_rows[i][3] for i in idx], dtype=float)
                    hd = hull_for(coords)
                    hd["mean"]   = float(np.mean(vals))
                    hd["median"] = float(np.median(vals))
                    hd["min"]    = float(np.min(vals))
                    hd["max"]    = float(np.max(vals))
                    hd["std"]    = float(np.std(vals))
                    if hd.get("type") == "polygon" and len(hd["hull"]) >= 3:
                        lats = [pp[0] for pp in hd["hull"]]
                        kmy = 111.0
                        kmx = 111.0 * np.cos(np.radians(float(np.mean(lats))))
                        xs = [pp[1]*kmx for pp in hd["hull"]]
                        ys = [pp[0]*kmy for pp in hd["hull"]]
                        a = 0.0
                        for ii in range(len(xs)):
                            jj = (ii + 1) % len(xs)
                            a += xs[ii]*ys[jj] - xs[jj]*ys[ii]
                        hd["area_km2"] = float(abs(a) / 2.0)
                    else:
                        hd["area_km2"] = 0.0
                    hl.append(hd)
                hulls[eps][ms][cname] = hl
                stats[eps][ms][cname] = {"points": len(pts), "clusters": nc, "noise": nn}
        print(f"  eps={eps:5d}m x min_samples 3..10 done")

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

    wards_slim = {"type":"FeatureCollection","features":[]}
    n_hs = 0
    for w in wards:
        try:
            c = w["geom"].representative_point()
            cx, cy = float(c.x), float(c.y)
        except Exception:
            cx, cy = None, None
        hs = (w.get("swd", 0) > 0)
        if hs: n_hs += 1
        wards_slim["features"].append({
            "type":"Feature",
            "properties":{"ward_name": w["name"], "ward_num": w["num"], "cx": cx, "cy": cy, "has_sensors": hs},
            "geometry": w["raw"]["geometry"]
        })
    print(f"[wards] {n_hs} wards host sensors")

    dem_info = json.loads(DEM_META.read_text()) if DEM_META.exists() else None

    tmpl = r"""<!doctype html><html><head><meta charset="utf-8">
<title>Depth Clusters - Bangalore</title>
<link href="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.css" rel="stylesheet">
<script src="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
<style>
:root{
  --edge: 28px;
  --scale: clamp(1, calc(0.6 + 0.5vw/16), 2.2);
  --base: calc(14px * var(--scale));
  --h1:   calc(17px * var(--scale));
  --h2:   calc(14px * var(--scale));
  --small:calc(12px * var(--scale));
  --tiny: calc(11px * var(--scale));
  --rad:  12px;
  --padx: 22px;
  --pady: 20px;
}
@media (min-width: 2200px){ :root{ --scale: 1.6; --edge: 36px; --padx: 28px; --pady: 24px; } }
@media (min-width: 3000px){ :root{ --scale: 2.4; --edge: 48px; --padx: 36px; --pady: 32px; } }
@media (min-width: 4000px){ :root{ --scale: 3.4; --edge: 64px; --padx: 46px; --pady: 42px; } }
 html,body,#map{margin:0;height:100%;background:#0a0a0a;font-family:system-ui,-apple-system,sans-serif;font-size:var(--base)}
 .panel{position:absolute;background:rgba(17,17,17,0.9);color:#f3f4f6;border-radius:var(--rad);z-index:5;
       box-shadow:0 10px 35px rgba(0,0,0,0.55), 0 2px 6px rgba(0,0,0,0.4);
       border:1px solid rgba(251,191,36,0.18);
       backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);
       padding:var(--pady) var(--padx);
       font-size:var(--base);font-weight:600;box-sizing:border-box}

 .title{top:var(--edge);left:var(--edge);max-width:440px}
 .title h1{margin:0 0 8px 0;font-size:var(--h1);font-weight:800;color:#fef3c7;letter-spacing:-0.01em;line-height:1.25}
 .title .sub{color:#d1d5db;font-size:var(--small);line-height:1.55;font-weight:500}

 .controls{top:var(--edge);right:var(--edge);min-width:320px}
 .controls .lbl{display:flex;justify-content:space-between;font-size:11px;color:#d1d5db;margin-bottom:8px;margin-top:18px;font-weight:800;text-transform:uppercase;letter-spacing:0.8px}
 .controls .lbl:first-of-type{margin-top:0}
 .controls .lbl b{color:#fbbf24;font-weight:800;letter-spacing:0}
 .controls input[type=range]{width:100%;accent-color:#f59e0b;height:22px;margin:2px 0}
 .controls .ticks{display:flex;justify-content:space-between;font-size:11px;color:#9ca3af;margin-top:4px;font-weight:600;padding:0 4px}
 .controls .divider{height:1px;background:rgba(255,255,255,0.08);margin:18px 0 6px}
 .controls .toggle{margin-top:12px;display:flex;align-items:center;gap:12px;font-size:13px;color:#f3f4f6;cursor:pointer;font-weight:700;padding:6px 2px;border-radius:6px;transition:background 0.15s}
 .controls .toggle:hover{background:rgba(251,191,36,0.08)}
 .controls .toggle input{accent-color:#f59e0b;width:16px;height:16px}

 .legend{bottom:var(--edge);right:var(--edge);min-width:260px}
 .legend h2{margin:0 0 12px 0;font-size:var(--h2);font-weight:800;color:#fef3c7;letter-spacing:-0.01em;padding-bottom:10px;border-bottom:1px solid rgba(255,255,255,0.08)}
 .lg{display:flex;align-items:center;gap:14px;padding:8px 10px;cursor:pointer;border-radius:7px;font-weight:700;transition:background 0.15s;border-left:3px solid transparent}
 .lg:hover{background:rgba(255,255,255,0.04);border-left-color:#fbbf24}
 .lg.off{opacity:0.35}
 .lg .sw{width:20px;height:20px;background:var(--c);border:1.5px solid rgba(0,0,0,0.8);flex-shrink:0;border-radius:5px;box-shadow:inset 0 0 0 1px rgba(255,255,255,0.12), 0 1px 2px rgba(0,0,0,0.6)}
 .lg .lbl2{flex:1;color:#f9fafb;font-size:14px}
 .lg .ct{color:#d1d5db;font-size:12px;min-width:72px;text-align:right;font-weight:600}

 .stats{bottom:var(--edge);left:var(--edge);min-width:380px;max-width:440px}
 .stats h2{margin:0 0 12px 0;font-size:var(--h2);font-weight:800;color:#fef3c7;letter-spacing:-0.01em;padding-bottom:10px;border-bottom:1px solid rgba(255,255,255,0.08)}
 .srow{display:grid;grid-template-columns:18px 1fr 70px 50px;gap:12px;font-size:13px;padding:5px 2px;color:#e5e7eb;align-items:center;font-weight:700}
 .srow.head{color:#9ca3af;font-size:10.5px;text-transform:uppercase;border-bottom:1px solid rgba(255,255,255,0.08);padding-bottom:6px;margin-bottom:6px;font-weight:700;letter-spacing:0.5px}
 .srow b{color:#fbbf24;font-weight:800;font-size:14px}
 .srow.clickable{cursor:pointer;border-radius:6px;padding:7px 8px;margin:2px -8px;transition:background 0.15s}
 .srow.clickable:hover{background:rgba(255,255,255,0.04)}
 .srow.clickable.iso{background:rgba(251,191,36,0.12);box-shadow:inset 0 0 0 1px rgba(251,191,36,0.5)}
 .subrow{font-size:11px;color:#9ca3af;font-weight:500;margin-top:4px;grid-column:1/-1;line-height:1.55;padding:4px 0 2px 14px;border-left:2px solid rgba(251,191,36,0.22);margin-left:4px;word-break:break-word}
 .reset-btn{margin-top:12px;background:#1f2937;color:#fbbf24;border:1px solid #4b5563;border-radius:6px;padding:8px 12px;font-size:12px;cursor:pointer;width:100%;display:none;font-weight:800}
 .reset-btn.show{display:block}

 .ward-num-marker{background:rgba(0,0,0,0.85);color:#fde047;font-weight:800;font-size:12px;padding:3px 7px;border-radius:4px;border:1px solid #fde047;pointer-events:none;white-space:nowrap;transform:translate(-50%, -50%)}
 .maplibregl-popup-content{background:#111 !important;color:#fff !important;font-weight:600;border-radius:6px;padding:9px 11px;pointer-events:none}
 .maplibregl-popup{pointer-events:none !important}
 .maplibregl-popup-tip{border-top-color:#111 !important;border-bottom-color:#111 !important}

 .chartpanel{position:absolute;left:50%;bottom:var(--edge);transform:translateX(-50%);
             width:min(46vw, 800px);background:rgba(17,17,17,0.95);border-radius:var(--rad);z-index:20;
             border:1px solid rgba(251,191,36,0.18);
             padding:var(--pady) var(--padx);box-shadow:0 20px 50px rgba(0,0,0,0.75);display:none;max-height:72vh;overflow:auto;font-size:var(--base);box-sizing:border-box;
             backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px)}
 .chartpanel.show{display:block}
 .chartpanel.fullscreen{top:0;left:0;right:0;bottom:0;transform:none;width:100vw;height:100vh;max-height:100vh;max-width:100vw;border-radius:0;padding:calc(var(--padx) * 1.3);display:flex;flex-direction:column;overflow:hidden}
 .chartpanel.fullscreen .cph{flex:0 0 auto;margin-bottom:calc(10px * var(--scale))}
 .chartpanel.fullscreen .chartbox2{flex:1 1 auto;height:auto !important;min-height:0;padding:calc(14px * var(--scale))}
 .chartpanel.fullscreen .cstats{flex:0 0 auto;grid-template-columns:repeat(5, 1fr);gap:calc(10px * var(--scale));margin-top:calc(12px * var(--scale))}
 .chartpanel.fullscreen .cstat{padding:calc(10px * var(--scale)) calc(14px * var(--scale))}
 .chartpanel.fullscreen .cstat .v{font-size:calc(22px * var(--scale))}
 .cph{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;gap:12px;flex-wrap:wrap}
 .cph h3{margin:0;font-size:var(--h1);font-weight:800;color:#fef3c7}
 .cph .sub2{color:#9ca3af;font-size:var(--small);font-weight:600;margin-top:2px}
 .cph select{background:#1f2937;color:#fff;border:1px solid #374151;border-radius:5px;padding:6px 10px;font-size:var(--small);font-weight:700}
 .cph .close,.cph .expand{background:#1f2937;color:#fbbf24;border:1px solid #4b5563;border-radius:5px;padding:6px 14px;font-size:var(--small);cursor:pointer;font-weight:800;margin-left:6px}
 .cph .close:hover,.cph .expand:hover{background:#374151}
 .chartbox2{position:relative;height:calc(360px * var(--scale));background:#0f0f0f;border-radius:8px;padding:12px}
 .cstats{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin-top:12px}
 .cstat{background:#0f0f0f;border:1px solid #1f2937;border-radius:7px;padding:10px 14px}
 .cstat .k{color:#9ca3af;font-size:var(--tiny);text-transform:uppercase;font-weight:700;letter-spacing:0.5px}
 .cstat .v{color:#fbbf24;font-size:calc(16px * var(--scale));font-weight:800;margin-top:3px}
</style></head><body>
<div id="map"></div>

<div class="panel title">
  <h1>Depth clusters by water level (median)</h1>
  <div class="sub">Dots = borewell sensors. Polygons = DBSCAN clusters. Click a sensor for its day-of-week chart; hover a cluster for its statistics.</div>
</div>

<div class="panel controls">
  <div class="lbl"><span>Cluster radius</span><b id="epsLabel">500 m</b></div>
  <input id="epsSlider" type="range" min="0" max="7" step="1" value="3">
  <div class="ticks"><span>100m</span><span>500m</span><span>1km</span><span>2km</span></div>

  <div class="lbl"><span>Min wells per cluster</span><b id="msLabel">3</b></div>
  <input id="msSlider" type="range" min="0" max="7" step="1" value="0">
  <div class="ticks"><span>3</span><span>5</span><span>7</span><span>10</span></div>

  <div class="divider"></div>
  <label class="toggle"><input type="checkbox" id="wardToggle" checked>Show ward boundaries</label>
  <label class="toggle"><input type="checkbox" id="wardNumToggle">Show ward numbers</label>
</div>

<div class="panel legend">
  <h2>Depth classes <span style="color:#9ca3af;font-weight:600;font-size:11px">(click to toggle)</span></h2>
  <div id="legendRows"></div>
</div>

<div class="panel stats">
  <h2>Clusters at <span id="statsEps" style="color:#fbbf24">500 m</span> / min <span id="statsMs" style="color:#fbbf24">3</span>
    <span style="color:#9ca3af;font-weight:600;font-size:11px">(click to isolate)</span></h2>
  <div class="srow head"><span></span><span>Class</span><span>Clusters</span><span>Noise</span></div>
  <div id="statsRows"></div>
  <button id="resetBtn" class="reset-btn">Show all classes</button>
</div>

<div class="chartpanel" id="chartPanel">
  <div class="cph">
    <div>
      <h3 id="cTitle">Sensor</h3>
      <div class="sub2" id="cSub"></div>
    </div>
    <div style="display:flex;gap:10px;align-items:center">
      <label style="font-size:12px;color:#e5e7eb;font-weight:700">Day:
        <select id="cDow">
          <option value="all">All days</option>
          <option value="0">Mon</option><option value="1">Tue</option>
          <option value="2">Wed</option><option value="3" selected>Thu</option>
          <option value="4">Fri</option><option value="5">Sat</option>
          <option value="6">Sun</option>
        </select>
      </label>
      <button class="expand" id="cExpand">Expand</button>
      <button class="close" id="cClose">Close</button>
    </div>
  </div>
  <div class="chartbox2"><canvas id="cChart"></canvas></div>
  <div class="cstats" id="cStats"></div>
</div>

<script>
const PAL=__PAL__, COUNTS=__COUNTS__, POINTS=__POINTS__, HULLS=__HULLS__, STATS=__STATS__;
const EPS_LAD=__EPS__, MS_LAD=__MS__, CLASSES=__CLASSES__, WARDS_GJ=__WARDS__, DEM=__DEM__;
const hidden = new Set();
const wardNumMarkers = [];

const style = {
  version: 8,
  sources: {
    osm: { type: 'raster', tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
           tileSize: 256, attribution: '© OpenStreetMap | BBMP wards', maxzoom: 19 }
  },
  layers: [{ id: 'osm', type: 'raster', source: 'osm' }]
};
const map = new maplibregl.Map({ container:'map', style, center:[77.59, 12.97], zoom:11 });
map.addControl(new maplibregl.NavigationControl(), 'bottom-right');

map.on('load', () => {
  map.addSource('wards', {type:'geojson', data: WARDS_GJ});
  map.addLayer({id:'ward-fill', type:'fill', source:'wards',
    paint:{'fill-color':'#000', 'fill-opacity':0}});
  map.addLayer({id:'ward-line', type:'line', source:'wards',
    paint:{'line-color':'#111827', 'line-width':2.2, 'line-opacity':0.9}});

  map.addSource('hulls', {type:'geojson', data:{type:'FeatureCollection', features:[]}});
  map.addLayer({id:'hull-fill', type:'fill', source:'hulls',
    paint:{'fill-color':['get','color'],'fill-opacity':0.42}});
  map.addLayer({id:'hull-line', type:'line', source:'hulls',
    paint:{'line-color':['get','color'],'line-width':3,'line-dasharray':[3,2],'line-opacity':1.0}});

  map.addSource('points', {type:'geojson', data:{type:'FeatureCollection', features:[]}});
  map.addLayer({id:'point-circle', type:'circle', source:'points',
    paint:{'circle-radius':5,'circle-color':['get','color'],'circle-stroke-color':'#000','circle-stroke-width':1.5}});

  const popup = new maplibregl.Popup({closeButton:false, closeOnClick:false, offset:8});

  map.on('mouseenter','point-circle', e => {
    popup.remove(); map.getCanvas().style.cursor='pointer';
    const p = e.features[0].properties;
    popup.setLngLat(e.lngLat).setHTML(
      '<b>'+p.uid+'</b><br>'+p.cls+' - '+(+p.v).toFixed(1)+' ft median' +
      (p.ward ? '<br><span style="color:#fde047">'+p.ward+'</span>' : '')).addTo(map);
  });
  map.on('mouseleave','point-circle', ()=>{ map.getCanvas().style.cursor=''; popup.remove(); });
  map.on('click','point-circle', e => {
    const p = e.features[0].properties;
    openSensorChart(p.uid, p.cls || '', p.ward || '');
  });

  map.on('click','hull-fill', e => {
    if (map.getCanvas().style.cursor === 'pointer') return;
    const p = e.features[0].properties;
    popup.setLngLat(e.lngLat).setHTML(
      '<b>'+p.cls+'</b> cluster<br>'+p.n+' wells &nbsp;&middot;&nbsp; '+(+p.area_km2).toFixed(2)+' km&sup2;'+
      '<br>Median: <b>'+(+p.median).toFixed(1)+' ft</b>'+
      '<br>Mean: '+(+p.mean).toFixed(1)+' ft (std '+(+p.std).toFixed(1)+')'+
      '<br>Range: '+(+p.min).toFixed(1)+' - '+(+p.max).toFixed(1)+' ft'
    ).addTo(map);
  });
  // hull popup stays open until user clicks elsewhere
  map.on('mouseenter','ward-fill', e => {
    const p = e.features[0].properties;
    popup.setLngLat(e.lngLat).setHTML(p.ward_num ? p.ward_name+' (#'+p.ward_num+')' : p.ward_name).addTo(map);
  });
  map.on('mouseleave','ward-fill', ()=>{ popup.remove(); });
  map.on('mouseenter','hull-fill', ()=>{ if (map.getCanvas().style.cursor !== 'pointer') map.getCanvas().style.cursor='help'; });
  map.on('mouseleave','hull-fill', ()=>{ if (map.getCanvas().style.cursor === 'help') map.getCanvas().style.cursor=''; });

  refresh();
  map.on('click', e => {
    const hits = map.queryRenderedFeatures(e.point, {layers:['hull-fill','point-circle']});
    if (hits.length === 0) popup.remove();
  });
});

function fmtEps(m){ return m>=1000 ? (m/1000).toFixed(m%1000?1:0)+' km' : m+' m'; }
function curEps(){ return EPS_LAD[+document.getElementById('epsSlider').value]; }
function curMs(){  return MS_LAD[+document.getElementById('msSlider').value]; }

function hullFeatures(eps, ms) {
  const out = [];
  for (const cls of CLASSES) {
    if (hidden.has(cls)) continue;
    const items = (HULLS[eps] && HULLS[eps][ms] && HULLS[eps][ms][cls]) || [];
    for (const it of items) {
      const props = {color: PAL[cls], cls, n: it.n,
                     mean: it.mean, median: it.median, min: it.min, max: it.max,
                     std: it.std, area_km2: it.area_km2};
      if (it.type === 'polygon') {
        const coords = it.hull.map(p => [p[1], p[0]]);
        coords.push(coords[0]);
        out.push({type:'Feature', properties: props, geometry:{type:'Polygon', coordinates:[coords]}});
      } else if (it.type === 'point') {
        const [lat, lng] = it.center;
        const r = 0.002;
        const ring = [];
        for (let k = 0; k < 24; k++) {
          const a = k * Math.PI * 2 / 24;
          ring.push([lng + r*Math.cos(a)/Math.cos(lat*Math.PI/180), lat + r*Math.sin(a)]);
        }
        ring.push(ring[0]);
        out.push({type:'Feature', properties: props, geometry:{type:'Polygon', coordinates:[ring]}});
      }
    }
  }
  return {type:'FeatureCollection', features: out};
}

function pointFeatures() {
  return {type:'FeatureCollection', features: POINTS.filter(p => !hidden.has(p.cls)).map(p => ({
    type:'Feature',
    properties:{color: PAL[p.cls], uid: p.uid, cls: p.cls, v: p.v, ward: p.ward || ''},
    geometry:{type:'Point', coordinates:[p.lng, p.lat]}
  }))};
}

function refresh() {
  const e = curEps(), m = curMs();
  if (map.getSource('hulls'))  map.getSource('hulls').setData(hullFeatures(e, m));
  if (map.getSource('points')) map.getSource('points').setData(pointFeatures());
  renderStats(e, m); renderLegend();
}

function renderStats(eps, ms) {
  const el = document.getElementById('statsRows');
  const agg = {};
  for (const c of CLASSES) {
    const hs = (HULLS[eps] && HULLS[eps][ms] && HULLS[eps][ms][c]) || [];
    if (!hs.length) { agg[c] = null; continue; }
    const meds = hs.map(h => +h.median), areas = hs.map(h => +h.area_km2);
    const wells = hs.reduce((a,h) => a + (+h.n || 0), 0);
    agg[c] = { wells, avgMed: meds.reduce((a,b)=>a+b,0) / meds.length,
               minMed: Math.min(...meds), maxMed: Math.max(...meds),
               totalArea: areas.reduce((a,b)=>a+b,0) };
  }
  el.innerHTML = CLASSES.map(c => {
    const s = STATS[eps][ms][c], a = agg[c];
    const iso = (hidden.size === CLASSES.length-1 && !hidden.has(c)) ? ' iso' : '';
    const sub = a
      ? ('<div class="subrow">'+a.wells+' wells in clusters &middot; avg median '+a.avgMed.toFixed(1)+' ft ('+a.minMed.toFixed(1)+'-'+a.maxMed.toFixed(1)+') &middot; '+a.totalArea.toFixed(1)+' km&sup2;</div>')
      : '';
    return '<div class="srow clickable'+iso+'" data-c="'+c+'">'+
      '<span style="color:'+PAL[c]+';font-size:18px">&#9679;</span>'+
      '<span>'+c+'</span><b>'+s.clusters+'</b><span>'+s.noise+'</span>'+ sub +'</div>';
  }).join('');
  el.querySelectorAll('.srow.clickable').forEach(r => r.addEventListener('click', () => {
    const c = r.dataset.c;
    if (hidden.size === CLASSES.length-1 && !hidden.has(c)) hidden.clear();
    else { hidden.clear(); CLASSES.filter(x=>x!==c).forEach(x=>hidden.add(x)); }
    document.getElementById('resetBtn').classList.toggle('show', hidden.size > 0);
    refresh();
  }));
  document.getElementById('statsEps').textContent = fmtEps(eps);
  document.getElementById('statsMs').textContent  = ms;
}

function renderLegend() {
  const el = document.getElementById('legendRows');
  el.innerHTML = CLASSES.map(c =>
    '<div class="lg'+(hidden.has(c)?' off':'')+'" data-c="'+c+'" style="--c:'+PAL[c]+'">'+
    '<span class="sw"></span><span class="lbl2">'+c+'</span><span class="ct">'+COUNTS[c]+' wells</span></div>'
  ).join('');
  el.querySelectorAll('.lg').forEach(r => r.addEventListener('click', () => {
    const c = r.dataset.c;
    if (hidden.has(c)) hidden.delete(c); else hidden.add(c);
    document.getElementById('resetBtn').classList.toggle('show', hidden.size > 0);
    refresh();
  }));
}

function setWardNums(on) {
  wardNumMarkers.forEach(m => m.remove()); wardNumMarkers.length = 0;
  if (!on) return;
  WARDS_GJ.features.forEach(f => {
    const p = f.properties;
    if (!p.cx || !p.cy || !p.ward_num) return;
    if (!p.has_sensors) return;
    const el = document.createElement('div');
    el.className = 'ward-num-marker'; el.textContent = p.ward_num;
    wardNumMarkers.push(new maplibregl.Marker({element: el, anchor:'center'}).setLngLat([p.cx, p.cy]).addTo(map));
  });
}

document.getElementById('wardToggle').addEventListener('change', e => {
  const v = e.target.checked ? 'visible' : 'none';
  map.setLayoutProperty('ward-line', 'visibility', v);
  map.setLayoutProperty('ward-fill', 'visibility', v);
});
document.getElementById('wardNumToggle').addEventListener('change', e => setWardNums(e.target.checked));
document.getElementById('resetBtn').addEventListener('click', () => {
  hidden.clear(); document.getElementById('resetBtn').classList.remove('show'); refresh();
  map.on('click', e => {
    const hits = map.queryRenderedFeatures(e.point, {layers:['hull-fill','point-circle']});
    if (hits.length === 0) popup.remove();
  });
});

const eSl = document.getElementById('epsSlider');
const mSl = document.getElementById('msSlider');
eSl.max = EPS_LAD.length - 1; mSl.max = MS_LAD.length - 1;
eSl.addEventListener('input', () => { document.getElementById('epsLabel').textContent = fmtEps(curEps()); refresh(); });
mSl.addEventListener('input', () => { document.getElementById('msLabel').textContent = curMs(); refresh(); });
document.getElementById('epsLabel').textContent = fmtEps(curEps());
document.getElementById('msLabel').textContent  = curMs();

// --- Inline sensor chart ---
let cChart = null, cSeries = [];
async function openSensorChart(uid, cls, ward) {
  document.getElementById('cTitle').textContent = 'Sensor ' + uid;
  document.getElementById('cSub').textContent   = [cls, ward].filter(Boolean).join(' - ');
  document.getElementById('chartPanel').classList.add('show');
  try {
    const r = await fetch('sensor_dow/' + uid + '.json');
    if (!r.ok) throw new Error('No data file (' + r.status + ')');
    cSeries = await r.json(); cRender();
  } catch (e) {
    document.getElementById('cStats').innerHTML = '<div class="cstat"><div class="k">Error</div><div class="v">' + e.message + '</div></div>';
    if (cChart) { cChart.destroy(); cChart = null; }
  }
}
function cOls(xs, ys) {
  const n = xs.length; if (n < 2) return null;
  const mx = xs.reduce((a,b)=>a+b,0)/n, my = ys.reduce((a,b)=>a+b,0)/n;
  let num=0, den=0, ssr=0, sst=0;
  for (let i=0;i<n;i++){ num+=(xs[i]-mx)*(ys[i]-my); den+=(xs[i]-mx)**2; }
  const slope = den ? num/den : 0, intercept = my - slope*mx;
  for (let i=0;i<n;i++){ const yh=slope*xs[i]+intercept; ssr+=(ys[i]-yh)**2; sst+=(ys[i]-my)**2; }
  return {slope, intercept, r2: sst?1-ssr/sst:0, n};
}
function cRender() {
  const dow = document.getElementById('cDow').value;
  let data = (dow === 'all') ? cSeries.slice() : cSeries.filter(s => s.weekday == +dow);
  data.sort((a,b)=> new Date(a.ts) - new Date(b.ts));
  const pts = data.map(s => ({x: new Date(s.ts), y: s.med_water_ft, n: s.n}));
  const t0 = pts.length ? pts[0].x.getTime() : 0;
  const xs = pts.map(p => (p.x.getTime() - t0) / 86400000);
  const ys = pts.map(p => p.y);
  const fit = cOls(xs, ys);
  const datasets = [{
    label: 'Session median water level (ft)',
    data: pts, showLine: false, pointRadius: 3, pointHoverRadius: 6,
    pointBackgroundColor: '#fbbf24', pointBorderColor: '#000', pointBorderWidth: 1
  }];
  if (fit && pts.length >= 2) {
    const xMinT = pts[0].x.getTime(), xMaxT = pts[pts.length-1].x.getTime();
    const xMaxD = (xMaxT - xMinT) / 86400000;
    datasets.push({
      label: 'OLS fit', type: 'line',
      data: [{x:new Date(xMinT), y:fit.intercept}, {x:new Date(xMaxT), y:fit.intercept + fit.slope*xMaxD}],
      borderColor: fit.slope > 0 ? '#ef4444' : '#22c55e',
      borderWidth: 2.5, borderDash: [6,4], pointRadius: 0, fill: false
    });
  }
  if (cChart) cChart.destroy();
  cChart = new Chart(document.getElementById('cChart'), {
    type: 'scatter', data: {datasets},
    options: {
      responsive: true, maintainAspectRatio: false,
      scales: {
        x: {type:'time', time:{unit:'week'}, ticks:{color:'#9ca3af', maxRotation: 90, minRotation: 90, autoSkip: true, autoSkipPadding: 10}, grid:{color:'#1f2937'}},
        y: {reverse: true, ticks:{color:'#9ca3af'}, grid:{color:'#1f2937'},
            title:{display:true, text:'Water level (ft below surface)', color:'#d1d5db', font:{weight:'700'}}}
      },
      plugins: {legend: {labels: {color:'#e5e7eb', font:{weight:'700'}}}}
    }
  });
  const el = document.getElementById('cStats');
  if (!fit) { el.innerHTML = '<div class="cstat"><div class="k">No sessions</div><div class="v">-</div></div>'; return; }
  const dayLabel = (dow === 'all') ? 'All days' : ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'][+dow];
  const slopePerYear = fit.slope * 365.25;
  const sclr = fit.slope > 0 ? '#f87171' : (fit.slope < 0 ? '#4ade80' : '#fbbf24');
  const sign = fit.slope > 0 ? 'declining' : (fit.slope < 0 ? 'rising' : 'flat');
  el.innerHTML =
    '<div class="cstat"><div class="k">Day filter</div><div class="v">' + dayLabel + '</div></div>' +
    '<div class="cstat"><div class="k">Sessions</div><div class="v">' + fit.n + '</div></div>' +
    '<div class="cstat"><div class="k">OLS slope ft/day</div><div class="v" style="color:'+sclr+'">' + fit.slope.toFixed(4) + ' ('+sign+')</div></div>' +
    '<div class="cstat"><div class="k">Annualized ft/yr</div><div class="v" style="color:'+sclr+'">' + slopePerYear.toFixed(2) + '</div></div>' +
    '<div class="cstat"><div class="k">R-squared</div><div class="v">' + fit.r2.toFixed(3) + '</div></div>';
}
document.getElementById('cDow').addEventListener('change', cRender);
document.getElementById('cClose').addEventListener('click', () => {
  const panel = document.getElementById('chartPanel');
  panel.classList.remove('show'); panel.classList.remove('fullscreen');
  document.getElementById('cExpand').textContent = 'Expand';
  if (cChart) { cChart.destroy(); cChart = null; }
});
document.getElementById('cExpand').addEventListener('click', () => {
  const panel = document.getElementById('chartPanel');
  panel.classList.toggle('fullscreen');
  document.getElementById('cExpand').textContent = panel.classList.contains('fullscreen') ? 'Restore' : 'Expand';
  if (cChart) setTimeout(() => cChart.resize(), 50);
});
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  const panel = document.getElementById('chartPanel');
  if (panel.classList.contains('fullscreen')) {
    panel.classList.remove('fullscreen');
    document.getElementById('cExpand').textContent = 'Expand';
    if (cChart) setTimeout(() => cChart.resize(), 50);
  } else if (panel.classList.contains('show')) {
    panel.classList.remove('show');
    if (cChart) { cChart.destroy(); cChart = null; }
  }
});
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
