"""
Prepare data files for the IISc BBMP Borewell Dashboard.

Inputs (paths configurable via env vars or edit CONFIG below):
  - BBMP ward shapefile ZIP (bbmpwards.zip)
  - Population/area xlsx (population_source.xlsx)
  - KH cleaned Excel ZIP (borewell_water_level_200826.zip)  -- the current data snapshot
  - Optional: sensors DB extract JSON (sensors_db.json) with motor_hp/depth for all KH-known UIDs.
    If missing, sensors are limited to those present in the current KH ZIP.

Outputs (into <OUT_DIR>/data/):
  - wards.geojson              -- ward polygons + properties (population, area, sensor counts)
  - sensors.json               -- one entry per sensor (uid, lat, lng, ward, meta, first/last, count, has_data)
  - sensor_series/<uid>.json   -- per-UID time series (water level + discharge, both in native units)
  - manifest.json              -- summary counts + build timestamp
"""
import datetime as dt
import io
import json
import math
import os
import re
import statistics
import tempfile
import zipfile
from collections import defaultdict
from pathlib import Path

import shapefile
from openpyxl import load_workbook
from session_quality import build_sessions, summarize, POLICY_VERSION

# KH marks pump-run boundaries with cell fill colours in col A of the .xlsx.
# Blue-tinted row = pump-START, red-tinted row = pump-STOP.
# Ported from bbmp_analysis/pass2_parse_ward.py.
KH_START_FILLS = {"FFDCE6F5", "DCE6F5"}
KH_END_FILLS   = {"FFFBE0DE", "FBE0DE"}
KH_JUMP_FT     = 20.0  # KH sensor re-lock rule


CONFIG = {
    "wards_zip": os.environ.get("WARDS_ZIP", "bbmpwards.zip"),
    "population_xlsx": os.environ.get("POPULATION_XLSX", "population_source.xlsx"),
    "kh_zip": os.environ.get("KH_ZIP", "borewell_water_level_200826.zip"),
    "sensors_db_json": os.environ.get("SENSORS_DB_JSON", str(Path(__file__).resolve().parent.parent / "data" / "sensors_db.json")),
    "out_dir": os.environ.get("OUT_DIR", str(Path(__file__).resolve().parent.parent / "data")),
}


# ---------- shapefile / geo helpers ----------

def point_in_ring(pt, ring):
    x, y = pt
    inside = False
    if len(ring) < 3:
        return False
    prev = ring[-1]
    for cur in ring:
        xi, yi = cur
        xj, yj = prev
        if ((yi > y) != (yj > y)) and (x < ((xj - xi) * (y - yi)) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
        prev = cur
    return inside


def point_in_polygon(pt, rings):
    if not rings or not point_in_ring(pt, rings[0]):
        return False
    return not any(point_in_ring(pt, hole) for hole in rings[1:])


def shape_to_rings(shape):
    pts = shape.points
    parts = list(shape.parts) + [len(pts)]
    return [pts[parts[i]:parts[i + 1]] for i in range(len(parts) - 1)]


def load_ward_features(wards_zip_path):
    features = []
    with tempfile.TemporaryDirectory() as td:
        with zipfile.ZipFile(wards_zip_path) as z:
            z.extractall(td)
        shp = next(Path(td).glob("*.shp"))
        reader = shapefile.Reader(str(shp))
        try:
            fields = [f[0] for f in reader.fields[1:]]
            for record, shape in zip(reader.records(), reader.shapes()):
                props = dict(zip(fields, record))
                rings = shape_to_rings(shape)
                features.append({
                    "props": props,
                    "rings": rings,
                    "bbox": shape.bbox,
                })
        finally:
            reader.close()
    return features


def pick(props, *names):
    lookup = {str(k).strip().lower(): k for k in props.keys()}
    for n in names:
        k = lookup.get(str(n).lower())
        if k is not None and props.get(k) not in (None, ""):
            return props.get(k)
    return None


def rings_to_geojson_coords(rings):
    # First ring = outer, rest = holes. Ensure closed rings.
    out = []
    for ring in rings:
        r = list(ring)
        if r and (r[0] != r[-1]):
            r.append(r[0])
        out.append([[float(x), float(y)] for x, y in r])
    return out


def polygon_centroid(rings):
    # Simple area-weighted centroid of outer ring (Shoelace)
    if not rings:
        return None
    ring = rings[0]
    n = len(ring)
    if n < 3:
        return None
    a = cx = cy = 0.0
    for i in range(n):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        a += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    a *= 0.5
    if a == 0:
        return [float(sum(x for x, _ in ring)) / n, float(sum(y for _, y in ring)) / n]
    return [cx / (6 * a), cy / (6 * a)]


def assign_ward(lat, lng, features):
    if lat is None or lng is None:
        return None
    pt = (lng, lat)
    for f in features:
        xmin, ymin, xmax, ymax = f["bbox"]
        if not (xmin <= lng <= xmax and ymin <= lat <= ymax):
            continue
        if point_in_polygon(pt, f["rings"]):
            return f
    return None


# ---------- KH ZIP parsing ----------

def parse_number(v):
    try:
        if v is None or v == "":
            return None
        p = float(str(v).replace(",", "").strip())
        return p if math.isfinite(p) else None
    except Exception:
        return None


def parse_ts(v):
    if isinstance(v, dt.datetime):
        return v.replace(microsecond=0)
    s = str(v or "").strip()
    for fmt in ("%d%m%y %H%M%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            pass
    return None


def parse_meta_row(row):
    meta = {}
    for cell in row:
        v = cell.value if hasattr(cell, "value") else cell
        t = str(v or "").strip()
        if ":" not in t:
            continue
        k, val = t.split(":", 1)
        meta[k.strip().lower()] = val.strip()
    return meta


def uid_from_filename(name):
    m = re.search(r"(\d{12,18})", Path(name).stem)
    return m.group(1) if m else None


def _kh_sessions(times, water, flow, yield_):
    return build_sessions(times, water, flow, yield_)


# Try fast calamine parser; fall back to openpyxl.
try:
    from python_calamine import CalamineWorkbook as _CalamineWorkbook
    _HAS_CALAMINE = True
except Exception:
    _HAS_CALAMINE = False


def parse_kh_file(name, raw_bytes):
    """Parse one KH .xlsx into rows + KH-official sessions.
    Uses python-calamine when available (5-10x faster), falls back to openpyxl."""
    if _HAS_CALAMINE:
        wb = _CalamineWorkbook.from_filelike(io.BytesIO(raw_bytes))
        rows = wb.get_sheet_by_index(0).to_python(skip_empty_area=True)
        meta_row = rows[0] if rows else None
        meta = parse_meta_row(meta_row) if meta_row else {}
        uid = meta.get("uid", uid_from_filename(name))
        lat = parse_number(meta.get("lat"))
        lng = parse_number(meta.get("long"))
        times, water, flow, yield_ = [], [], [], []
        for row in rows[2:]:
            if not row: continue
            when = parse_ts(row[0] if len(row) > 0 else None)
            if not when: continue
            times.append(when)
            water.append(parse_number(row[1] if len(row) > 1 else None))
            flow.append(parse_number(row[2] if len(row) > 2 else None))
            yield_.append(parse_number(row[3] if len(row) > 3 else None))
    else:
        wb = load_workbook(io.BytesIO(raw_bytes), data_only=True, read_only=True)
        ws = wb[wb.sheetnames[0]]
        rows_iter = ws.iter_rows()
        meta_row = next(rows_iter, None)
        _header_row = next(rows_iter, None)
        meta = parse_meta_row(meta_row) if meta_row else {}
        uid = meta.get("uid", uid_from_filename(name))
        lat = parse_number(meta.get("lat"))
        lng = parse_number(meta.get("long"))
        times, water, flow, yield_ = [], [], [], []
        for row in rows_iter:
            vals = [c.value for c in row]
            when = parse_ts(vals[0] if len(vals) > 0 else None)
            if not when: continue
            times.append(when)
            water.append(parse_number(vals[1] if len(vals) > 1 else None))
            flow.append(parse_number(vals[2] if len(vals) > 2 else None))
            yield_.append(parse_number(vals[3] if len(vals) > 3 else None))
        wb.close()

    if not times:
        return {"uid": uid, "lat": lat, "lng": lng, "n": 0}

    sessions = _kh_sessions(times, water, flow, yield_)
    return {
        "uid": uid,
        "lat": lat,
        "lng": lng,
        "times": times,
        "water_ft": water,
        "flow_lpm": flow,
        "yield_kl": yield_,
        "sessions": sessions,
        "first": min(times),
        "last": max(times),
        "n": len(times),
    }


# ---------- main build ----------

def main():
    out_dir = Path(CONFIG["out_dir"])
    (out_dir / "sensor_series").mkdir(parents=True, exist_ok=True)

    print("Loading BBMP ward shapefile...")
    features = load_ward_features(CONFIG["wards_zip"])
    print(f"  {len(features)} ward polygons")

    print("Loading population/area xlsx...")
    pop_wb = load_workbook(CONFIG["population_xlsx"], data_only=True, read_only=True)
    pop_ws = pop_wb[pop_wb.sheetnames[0]]
    pop_rows = list(pop_ws.iter_rows(values_only=True))
    pop_hdr = [str(x or "").strip().lower() for x in pop_rows[0]]
    def col(name):
        for i, h in enumerate(pop_hdr):
            if h == name.lower():
                return i
        return None
    i_ward = col("Ward_NO")
    i_area = col("Area_km2")
    i_pop_2001 = col("Population_2001")
    i_pop_2011 = col("Population_2011")
    i_pop_2024 = col("Projected_Population_2024")
    i_pop_2026 = col("Projected_Population_2026") or col("Projected_Population_2024") or col("Population_2011")
    i_pop_now = i_pop_2026
    i_hh = col("Projected_Households_2024") or col("Households_2011")
    pop_by_ward = {}
    for r in pop_rows[1:]:
        if r[i_ward] is None:
            continue
        try:
            wn = int(float(r[i_ward]))
        except Exception:
            continue
        def _pick(idx):
            if idx is None or r[idx] is None: return None
            try: return float(r[idx])
            except Exception: return None
        pop_by_ward[wn] = {
            "area_km2": _pick(i_area),
            "population_2001": _pick(i_pop_2001),
            "population_2011": _pick(i_pop_2011),
            "population_2024": _pick(i_pop_2024),
            "population_2026": _pick(i_pop_2026),
            "population": _pick(i_pop_now),   # kept for backward compat
            "households": _pick(i_hh),
        }
    print(f"  {len(pop_by_ward)} wards with population data")

    print("Parsing KH ZIP with KH-official session detection (yield-reset + 30 min gap)...")
    import os as _os
    from concurrent.futures import ProcessPoolExecutor, as_completed
    zip_sensor_data = {}
    with zipfile.ZipFile(CONFIG["kh_zip"]) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".xlsx")]
        # Read all file bytes into memory first (one-time cost; enables parallel parse).
        print(f"  loading {len(names)} xlsx into memory ...")
        payloads = [(name, z.read(name)) for name in names]
    workers = max(1, (_os.cpu_count() or 2) - 1)
    print(f"  parsing with {workers} worker process(es) ...")
    done = 0
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(parse_kh_file, name, raw): name for name, raw in payloads}
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                parsed = fut.result()
                if parsed.get("uid"):
                    zip_sensor_data[parsed["uid"]] = parsed
            except Exception as e:
                print(f"  parse fail {name}: {e}")
            done += 1
            if done % 100 == 0 or done == len(names):
                print(f"  parsed {done}/{len(names)}")
    print(f"  {len(zip_sensor_data)} sensors with data in ZIP")

    print("Optional: reading sensors_db.json for extra metadata (motor_hp, depth, extra UIDs)...")
    db_sensors = {}
    db_path = Path(CONFIG["sensors_db_json"])
    if db_path.exists():
        for s in json.load(open(db_path)):
            db_sensors[str(s["uid"])] = s
        print(f"  {len(db_sensors)} sensors from DB")
    else:
        print(f"  {db_path} not found - proceeding with ZIP sensors only")

    # ---- Build per-sensor records ----
    all_uids = set(zip_sensor_data) | set(db_sensors)
    sensors_out = []
    per_ward_counts = defaultdict(lambda: {"total": 0, "with_data": 0})
    for uid in sorted(all_uids):
        z_ = zip_sensor_data.get(uid, {})
        d_ = db_sensors.get(uid, {})
        lat = z_.get("lat") if z_.get("lat") is not None else d_.get("lat")
        lng = z_.get("lng") if z_.get("lng") is not None else d_.get("lng")
        feat = assign_ward(lat, lng, features)
        ward_no = None
        ward_name = None
        if feat:
            wn_raw = pick(feat["props"], "WARD_NO", "ward_no", "ward", "WARD")
            try:
                ward_no = int(float(wn_raw)) if wn_raw is not None else None
            except Exception:
                ward_no = None
            ward_name = pick(feat["props"], "WARD_NAME", "ward_name", "name", "NAME")
        elif d_.get("ward_no") is not None:
            try:
                ward_no = int(float(d_["ward_no"]))
            except Exception:
                ward_no = None
            ward_name = d_.get("ward_name")
        has_data = uid in zip_sensor_data and zip_sensor_data[uid].get("n", 0) > 0
        first = z_.get("first")
        last = z_.get("last")
        rec = {
            "uid": uid,
            "lat": lat,
            "lng": lng,
            "ward_no": ward_no,
            "ward_name": ward_name,
            "motor_hp": d_.get("motor_hp"),
            "borewell_depth": d_.get("borewell_depth"),
            "pump_name": d_.get("pump_name"),
            "has_data": has_data,
            "reading_count": z_.get("n", 0) or (d_.get("total_readings") if d_ else 0),
            "first_data_at": first.isoformat() if isinstance(first, dt.datetime) else d_.get("first_data_at"),
            "last_data_at": last.isoformat() if isinstance(last, dt.datetime) else d_.get("last_data_at"),
        }
        sensors_out.append(rec)
        if ward_no is not None:
            per_ward_counts[ward_no]["total"] += 1
            if has_data:
                per_ward_counts[ward_no]["with_data"] += 1

    json.dump(sensors_out, open(out_dir / "sensors.json", "w"))
    print(f"Wrote {out_dir/'sensors.json'} ({len(sensors_out)} sensors)")

    # ---- Per-UID time-series files ----
    for uid, z_ in zip_sensor_data.items():
        if not z_.get("times"):
            continue
        series = {
            "uid": uid,
            "unit_water": "ft below surface",
            "unit_flow": "L/min",
            "unit_yield": "KL",
            "times": [t.isoformat() for t in z_["times"]],
            "water_ft": z_["water_ft"],
            "flow_lpm": z_["flow_lpm"],
            "yield_kl": z_["yield_kl"],
            "sessions": z_.get("sessions", []),
            "quality_policy_version": POLICY_VERSION,
            "quality_summary": summarize(z_.get("sessions", [])),
        }
        with open(out_dir / "sensor_series" / f"{uid}.json", "w") as f:
            json.dump(series, f)
    print(f"Wrote per-UID series for {len(list((out_dir/'sensor_series').glob('*.json')))} sensors")

    # ---- GeoJSON ----
    ward_features_out = []
    for feat in features:
        wn_raw = pick(feat["props"], "WARD_NO", "ward_no", "ward", "WARD")
        try:
            ward_no = int(float(wn_raw)) if wn_raw is not None else None
        except Exception:
            ward_no = None
        ward_name = pick(feat["props"], "WARD_NAME", "ward_name", "name", "NAME") or ""
        counts = per_ward_counts.get(ward_no, {"total": 0, "with_data": 0})
        pop = pop_by_ward.get(ward_no, {})
        centroid = polygon_centroid(feat["rings"])
        ward_features_out.append({
            "type": "Feature",
            "properties": {
                "ward_no": ward_no,
                "ward_name": ward_name,
                "area_km2": pop.get("area_km2"),
                "population_2001": pop.get("population_2001"),
                "population_2011": pop.get("population_2011"),
                "population_2024": pop.get("population_2024"),
                "population_2026": pop.get("population_2026"),
                "sensor_total": counts["total"],
                "sensor_with_data": counts["with_data"],
                "centroid": centroid,
            },
            "geometry": {"type": "Polygon", "coordinates": rings_to_geojson_coords(feat["rings"])},
        })
    geojson = {"type": "FeatureCollection", "features": ward_features_out}
    json.dump(geojson, open(out_dir / "wards.geojson", "w"))
    print(f"Wrote {out_dir/'wards.geojson'} ({len(ward_features_out)} wards)")

    # ---- Manifest ----
    manifest = {
        "generated_at": dt.datetime.utcnow().isoformat() + "Z",
        "kh_zip": Path(CONFIG["kh_zip"]).name,
        "sensor_total": len(sensors_out),
        "sensor_with_data": sum(1 for s in sensors_out if s["has_data"]),
        "wards": len(ward_features_out),
        "wards_with_data": sum(1 for f in ward_features_out if f["properties"]["sensor_with_data"] > 0),
        "period_start": min((s["first_data_at"] for s in sensors_out if s.get("first_data_at")), default=None),
        "period_end": max((s["last_data_at"] for s in sensors_out if s.get("last_data_at")), default=None),
    }
    json.dump(manifest, open(out_dir / "manifest.json", "w"), indent=2)
    print("Manifest:", json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
