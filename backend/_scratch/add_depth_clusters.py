#!/usr/bin/env python3
"""Depth clusters on MapLibre GL JS with native 3D terrain from DEM tiles."""
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
                p.get("KGISWardName") or p.get("Ward_Name") or
                f"Ward {p.get('ward_no') or p.get('WARD_NO') or '?'}")
        num  = (p.get("ward_no") or p.get("WARD_NO") or p.get("ward_number") or "")
        geom = shape(f["geometry"]) if f.get("geometry") else None
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
                pts = [(r[1], r[2]) for r in rows if r[4] == cname]
                class_counts[cname] = len(pts)
                labels, nc, nn = compute(pts, eps, ms)
                hl = []
                for lab in sorted(set(labels)):
                    if lab == -1: continue
                    idx = [i for i,l in enumerate(labels) if l == lab]
                    hl.append(hull_for(np.array([pts[i] for i in idx])))
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

    # Ward GeoJSON with centroid lng/lat so JS can place number labels without recomputing
    wards_slim = {"type":"FeatureCollection","features":[]}
    for w in wards:
        try:
            c = w["geom"].representative_point()
            cx, cy = float(c.x), float(c.y)
        except Exception:
            cx, cy = None, None
        wards_slim["features"].append({
            "type":"Feature",
            "properties":{"ward_name": w["name"], "ward_num": w["num"], "cx": cx, "cy": cy},
            "geometry": w["raw"]["geometry"]
        })

    dem_info = None
    if DEM_META.exists():
        dem_info = json.loads(DEM_META.read_text())
        print(f"[dem_tiles] 3D terrain enabled -> z{dem_info['minzoom']}-{dem_info['maxzoom']}")
    else:
        print("[dem_tiles] no tile meta found; 3D toggle will be disabled. Run prepare_dem_tiles.py first.")

    tmpl = r"""<!doctype html><html><head><meta charset="utf-8">
<title>Depth Clusters - Bangalore 3D</title>
<link href="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.css" rel="stylesheet">
<script src="https://unpkg.com/maplibre-gl@3.6.2/dist/maplibre-gl.js"></script>
<style>
 html,body,#map{margin:0;height:100%;background:#0a0a0a;font-family:system-ui,-apple-system,sans-serif}
 .panel{position:absolute;background:rgba(10,10,10,0.92);color:#fff;border-radius:10px;z-index:5;
       box-shadow:0 4px 20px rgba(0,0,0,0.7);padding:14px 16px;font-size:14px;font-weight:600}
 .title{top:12px;left:12px;max-width:380px}
 .title h1{margin:0 0 4px 0;font-size:17px;font-weight:800}
 .title .sub{color:#d1d5db;font-size:13px;line-height:1.5;font-weight:500}
 .controls{top:12px;right:12px;min-width:280px}
 .controls .lbl{display:flex;justify-content:space-between;font-size:13px;color:#e5e7eb;margin-bottom:4px;margin-top:14px;font-weight:700}
 .controls .lbl:first-of-type{margin-top:0}
 .controls .lbl b{color:#fbbf24;font-weight:800}
 .controls input[type=range]{width:100%;accent-color:#f59e0b}
 .controls .ticks{display:flex;justify-content:space-between;font-size:11px;color:#9ca3af;margin-top:2px;font-weight:600}
 .controls .toggle{margin-top:10px;display:flex;align-items:center;gap:8px;font-size:13px;color:#f3f4f6;cursor:pointer;font-weight:700}
 .controls .toggle input{accent-color:#f59e0b;width:16px;height:16px}
 .controls .toggle.disabled{opacity:0.4;cursor:not-allowed}
 .legend{bottom:12px;right:12px;min-width:240px}
 .legend h2{margin:0 0 8px 0;font-size:14px;font-weight:800;color:#fff}
 .lg{display:flex;align-items:center;gap:10px;padding:6px 8px;cursor:pointer;border-radius:5px;font-weight:700}
 .lg:hover{background:#1f2937}
 .lg.off{opacity:0.35}
 .lg .sw{width:18px;height:18px;background:var(--c);border:2px solid #000;flex-shrink:0;border-radius:3px}
 .lg .lbl2{flex:1;color:#f9fafb;font-size:14px}
 .lg .ct{color:#d1d5db;font-size:12px;min-width:70px;text-align:right;font-weight:600}
 .stats{bottom:12px;left:12px;min-width:320px;max-width:360px}
 .stats h2{margin:0 0 10px 0;font-size:14px;font-weight:800;color:#fff}
 .srow{display:grid;grid-template-columns:16px 1fr 70px 50px;gap:10px;font-size:13px;padding:4px 0;color:#e5e7eb;align-items:center;font-weight:700}
 .srow.head{color:#9ca3af;font-size:11px;text-transform:uppercase;border-bottom:1px solid #374151;padding-bottom:5px;margin-bottom:5px;font-weight:700}
 .srow b{color:#fbbf24;font-weight:800;font-size:14px}
 .srow.clickable{cursor:pointer;border-radius:5px;padding:5px 7px;margin:2px -7px}
 .srow.clickable:hover{background:#1f2937}
 .srow.clickable.iso{background:#1f2937;outline:2px solid #f59e0b}
 .reset-btn{margin-top:10px;background:#1f2937;color:#fbbf24;border:1px solid #4b5563;border-radius:5px;padding:7px 12px;font-size:12px;cursor:pointer;width:100%;display:none;font-weight:800}
 .reset-btn.show{display:block}
 .ward-num-marker{background:rgba(0,0,0,0.85);color:#fde047;font-weight:800;font-size:12px;padding:3px 7px;border-radius:4px;border:1px solid #fde047;pointer-events:none;white-space:nowrap;transform:translate(-50%, -50%)}
 .ml-popup-content{background:#111;color:#fff;padding:8px 10px;border-radius:6px;font-weight:600}
 .maplibregl-popup-content{background:#111 !important;color:#fff !important;font-weight:600;border-radius:6px;padding:8px 10px}
 .maplibregl-popup-tip{border-top-color:#111 !important;border-bottom-color:#111 !important}
</style></head><body>
<div id="map"></div>

<div class="panel title">
  <h1>Depth clusters by water level (median)</h1>
  <div class="sub">Dots = borewell sensors. Polygons = DBSCAN clusters. Toggle 3D terrain for elevation context. Adjust radius and minimum wells per cluster.</div>
</div>

<div class="panel controls">
  <div class="lbl"><span>Cluster radius</span><b id="epsLabel">500 m</b></div>
  <input id="epsSlider" type="range" min="0" max="7" step="1" value="3">
  <div class="ticks"><span>100m</span><span>500m</span><span>1km</span><span>2km</span></div>

  <div class="lbl"><span>Min wells per cluster</span><b id="msLabel">3</b></div>
  <input id="msSlider" type="range" min="0" max="7" step="1" value="0">
  <div class="ticks"><span>3</span><span>5</span><span>7</span><span>10</span></div>

  <label class="toggle"><input type="checkbox" id="wardToggle" checked>Show ward boundaries</label>
  <label class="toggle"><input type="checkbox" id="wardNumToggle">Show ward numbers</label>
  <label class="toggle" id="terrWrap" style="display:none"><input type="checkbox" id="terrainToggle">3D terrain</label>

  <div id="exagWrap" style="display:none !important">
    <div class="lbl"><span>Terrain exaggeration</span><b id="exagLabel">1.5x</b></div>
    <input id="exagSlider" type="range" min="1" max="4" step="0.25" value="1.5">
  </div>
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
const wardNumMarkers = [];

const style = {
  version: 8,
  glyphs: "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
  sources: {
    osm: { type: 'raster', tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
           tileSize: 256, attribution: '&copy; OpenStreetMap | BBMP wards | DEM: ISRO/USGS', maxzoom: 19 }
  },
  layers: [{ id: 'osm', type: 'raster', source: 'osm' }]
};
if (DEM) {
  style.sources.dem = { type: 'raster-dem', tiles: [DEM.tile_url],
    tileSize: 256, encoding: 'mapbox', minzoom: DEM.minzoom, maxzoom: DEM.maxzoom };
}

const map = new maplibregl.Map({
  container: 'map', style, center: [77.59, 12.97], zoom: 11, pitch: 0, bearing: 0, maxPitch: 75
});
map.addControl(new maplibregl.NavigationControl({visualizePitch: true}), 'bottom-right');

if (!DEM) {
  const el = document.getElementById('terrWrap');
  el.classList.add('disabled');
  el.querySelector('input').disabled = true;
  el.title = 'Run prepare_dem_tiles.py to enable';
}

map.on('load', () => {
  // Hillshade (always available if DEM is loaded)
  if (DEM) {
    map.addLayer({ id: 'hillshade', type: 'hillshade', source: 'dem',
      paint: {'hillshade-exaggeration': 0.6, 'hillshade-shadow-color':'#000',
              'hillshade-highlight-color':'#fff', 'hillshade-accent-color':'#555'},
      layout: {visibility: 'none'}});
  }
  // Wards
  map.addSource('wards', {type:'geojson', data: WARDS_GJ});
  map.addLayer({id:'ward-fill', type:'fill', source:'wards',
    paint:{'fill-color':'#000', 'fill-opacity':0.0}});
  map.addLayer({id:'ward-line', type:'line', source:'wards',
    paint:{'line-color':'#111827', 'line-width':2, 'line-opacity':0.95}});
  // Cluster hulls (data refreshed on slider change)
  map.addSource('hulls', {type:'geojson', data:{type:'FeatureCollection', features:[]}});
  map.addLayer({id:'hull-fill', type:'fill', source:'hulls',
    paint:{'fill-color':['get','color'],'fill-opacity':0.5}});
  map.addLayer({id:'hull-line', type:'line', source:'hulls',
    paint:{'line-color':['get','color'],'line-width':3,'line-dasharray':[2,1.5],'line-opacity':0.95}});
  // Points
  map.addSource('points', {type:'geojson', data:{type:'FeatureCollection', features:[]}});
  map.addLayer({id:'point-circle', type:'circle', source:'points',
    paint:{'circle-radius':5,'circle-color':['get','color'],
           'circle-stroke-color':'#000','circle-stroke-width':1.5}});
  // Hover popups for points
  const popup = new maplibregl.Popup({closeButton:false, closeOnClick:false, offset:8});
  map.on('mouseenter','point-circle', e => {
    map.getCanvas().style.cursor='pointer';
    const p = e.features[0].properties;
    popup.setLngLat(e.lngLat).setHTML(
      '<b>'+p.uid+'</b><br>'+p.cls+' - '+(+p.v).toFixed(1)+' ft median' +
      (p.ward ? '<br><span style="color:#fde047">'+p.ward+'</span>' : '')).addTo(map);
  });
  map.on('mouseleave','point-circle', ()=>{ map.getCanvas().style.cursor=''; popup.remove(); });
  map.on('mouseenter','hull-fill', e => {
    map.getCanvas().style.cursor='pointer';
    const p = e.features[0].properties;
    popup.setLngLat(e.lngLat).setHTML('<b>'+p.cls+'</b> cluster<br>'+p.n+' wells').addTo(map);
  });
  map.on('mouseleave','hull-fill', ()=>{ map.getCanvas().style.cursor=''; popup.remove(); });
  map.on('mouseenter','ward-fill', e => {
    const p = e.features[0].properties;
    const txt = p.ward_num ? p.ward_name+' (#'+p.ward_num+')' : p.ward_name;
    popup.setLngLat(e.lngLat).setHTML(txt).addTo(map);
  });
  map.on('mouseleave','ward-fill', ()=>{ popup.remove(); });

  refresh();
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
      if (it.type === 'polygon') {
        // hull is [[lat,lng],...] - MapLibre wants [lng,lat]
        const coords = it.hull.map(p => [p[1], p[0]]);
        coords.push(coords[0]);  // close ring
        out.push({type:'Feature',
          properties:{color: PAL[cls], cls, n: it.n},
          geometry:{type:'Polygon', coordinates:[coords]}});
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
  renderStats(e, m);
  renderLegend();
}

function renderStats(eps, ms) {
  const el = document.getElementById('statsRows');
  el.innerHTML = CLASSES.map(c => {
    const s = STATS[eps][ms][c];
    const iso = (hidden.size === CLASSES.length-1 && !hidden.has(c)) ? ' iso' : '';
    return '<div class="srow clickable'+iso+'" data-c="'+c+'">'+
      '<span style="color:'+PAL[c]+';font-size:16px">&#9679;</span>'+
      '<span>'+c+'</span><b>'+s.clusters+'</b><span>'+s.noise+'</span></div>';
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
  wardNumMarkers.forEach(m => m.remove());
  wardNumMarkers.length = 0;
  if (!on) return;
  WARDS_GJ.features.forEach(f => {
    const p = f.properties;
    if (!p.cx || !p.cy || !p.ward_num) return;
    const el = document.createElement('div');
    el.className = 'ward-num-marker';
    el.textContent = p.ward_num;
    wardNumMarkers.push(new maplibregl.Marker({element: el, anchor:'center'})
      .setLngLat([p.cx, p.cy]).addTo(map));
  });
}

document.getElementById('wardToggle').addEventListener('change', e => {
  const v = e.target.checked ? 'visible' : 'none';
  map.setLayoutProperty('ward-line', 'visibility', v);
  map.setLayoutProperty('ward-fill', 'visibility', v);
});
document.getElementById('wardNumToggle').addEventListener('change', e => setWardNums(e.target.checked));

document.getElementById('terrainToggle').addEventListener('change', e => {
  if (!DEM) return;
  const wrap = document.getElementById('exagWrap');
  if (e.target.checked) {
    const ex = +document.getElementById('exagSlider').value;
    map.setTerrain({source:'dem', exaggeration: ex});
    map.setLayoutProperty('hillshade', 'visibility', 'visible');
    map.easeTo({pitch: 60, bearing: -17, duration: 1000});
    wrap.style.display = 'block';
  } else {
    map.setTerrain(null);
    map.setLayoutProperty('hillshade', 'visibility', 'none');
    map.easeTo({pitch: 0, bearing: 0, duration: 1000});
    wrap.style.display = 'none';
  }
});
document.getElementById('exagSlider').addEventListener('input', e => {
  document.getElementById('exagLabel').textContent = (+e.target.value).toFixed(2)+'x';
  if (document.getElementById('terrainToggle').checked)
    map.setTerrain({source:'dem', exaggeration: +e.target.value});
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
