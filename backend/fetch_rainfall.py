"""
Rainfall pipeline for the BBMP borewell dashboard — one script, three sources
stitched into one per-ward daily series that runs 2009 -> today.

Sources
-------
1) KWRIS historical daily (2009 -> ~5 weeks behind today)
   https://water.karnataka.gov.in/RainfallAnalytics
   Public POST endpoints on RainfallAnalytics.aspx. No login.
   Stations have (lat, lng); we spatial-join each to its BBMP ward.

2) KSNDMC BBMP live 24 hr rainfall (fills the trailing gap, daily going forward)
   https://ksndmc.org:6443/arcgis/rest/services/BBMP_WEATHER_MAP/MapServer/0
   100 TRG stations, each already tagged with WARD_NO. Poll it once a day
   (Windows Task Scheduler / GitHub Action) and this script appends each poll
   to a rolling per-(date, station) log.

Output for the dashboard
------------------------
- data/rainfall/<ward_no>.json
    { ward_no, ward_name, source, start, end,
      daily:   [ {date, rainfall_mm, source}, ... ],
      monthly: [ {month, rainfall_mm}, ... ],
      annual_total_mm, current_month_mm }
- data/wards.geojson patched with:
    rainfall_mm_annual, rainfall_mm_month_current,
    rainfall_updated_at, rainfall_source

Raw caches (kept out of git; regenerated cheaply)
-------------------------------------------------
- data_raw/kwris_stations.csv               station catalog for Bengaluru Urban
- data_raw/kwris_daily/<GUID>.csv           per-station daily rainfall
- data_raw/kwris_daily/_manifest.json       last fetch summary
- data_raw/ksndmc_daily_bbmp.csv            rolling live-poll log
                                            (date, station_code, ward_no, rainfall_mm)

Usage
-----
    cd backend
    pip install -r requirements.txt

    # one-time bootstrap (slow — scrapes 17+ years for every KWRIS station)
    python fetch_rainfall.py history --start 2009

    # once a day (cron / Task Scheduler) — appends today's KSNDMC values
    python fetch_rainfall.py live

    # rebuild per-ward JSONs from the caches; safe to run any time
    python fetch_rainfall.py build

    # bootstrap + live + build in one shot
    python fetch_rainfall.py all --start 2009

Design notes
------------
- KWRIS returns dates in TWO formats in the same response depending on year:
  2009 -> ~2024 rows come as M/D/Y, 2025+ rows come as D/M/Y. `_parse_date`
  auto-detects using an unambiguous first-token > 12 rule and remembers the
  format for the batch.
- Ward daily = mean of that ward's KWRIS station daily values; wards with no
  KWRIS station in their polygon fall back to KSNDMC ward-tagged average.
- The two sources overlap intentionally: after KWRIS's last date the ward
  series continues from KSNDMC live-log. No deduplication needed — we cut
  cleanly at `kwris_last_date + 1 day`.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# KSNDMC's ArcGIS server (ksndmc.org:6443) presents a cert chain most
# Python trust stores don't accept. Verified via GET in a browser to be the
# real server — safe to skip verification here.
VERIFY_TLS = False

ROOT = Path(__file__).resolve().parent
DATA = ROOT.parent / "data"
RAW = ROOT.parent / "data_raw"
KWRIS_DIR = RAW / "kwris_daily"
KWRIS_STATIONS_CSV = RAW / "kwris_stations.csv"
KWRIS_MANIFEST = KWRIS_DIR / "_manifest.json"
KSNDMC_LOG = RAW / "ksndmc_daily_bbmp.csv"
WARD_RAINFALL_DIR = DATA / "rainfall"
WARDS_GEOJSON = DATA / "wards.geojson"

KWRIS_BASE = "https://water.karnataka.gov.in"
KWRIS_HEADERS = {"Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0 IISc-BWSSB-dashboard"}
KSNDMC_LIVE_URL = ("https://ksndmc.org:6443/arcgis/rest/services/"
                   "BBMP_WEATHER_MAP/MapServer/0/query")

# KWRIS default districts for BBMP-relevant coverage.
# BBMP wards span Bengaluru Urban + Rural + edges of Ramanagara. We fetch
# stations from all of these, dedupe by GUID, and then keep only stations
# that spatially fall inside a BBMP ward polygon.
# Pass different codes via --districts on the CLI. Discoverable at runtime
# via the KWRIS district dropdown endpoint (see _kwris_discover_districts).
KWRIS_DISTRICTS_DEFAULT = [
    "166",  # Bengaluru Urban
    "165",  # Bengaluru Rural
    "184",  # Ramanagara
    "174",  # Chikkaballapura
    "182",  # Kolar
    "191",  # Tumakuru
]
# `datasource=32` = WRD-SMS (the default network on the KWRIS dashboard).
KWRIS_DATASOURCE = "32"

# Dashboard-visible label.
SOURCE_LABEL = "KWRIS historical + KSNDMC BBMP live"

for d in (RAW, KWRIS_DIR, WARD_RAINFALL_DIR):
    d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Point-in-polygon (duplicated small helpers to keep this script standalone)
# ---------------------------------------------------------------------------

def _point_in_ring(pt, ring):
    x, y = pt
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def _point_in_polygon(pt, rings):
    if not rings or not _point_in_ring(pt, rings[0]):
        return False
    return not any(_point_in_ring(pt, hole) for hole in rings[1:])


def _feature_rings(feat):
    """Return list of rings [outer, holes...] from a GeoJSON Polygon/MultiPolygon."""
    g = feat["geometry"]
    if g["type"] == "Polygon":
        return [g["coordinates"]]
    if g["type"] == "MultiPolygon":
        return list(g["coordinates"])
    return []


def assign_ward(lat, lng, wards_geo):
    """Return (ward_no, ward_name) for a point, or (None, None)."""
    pt = (lng, lat)
    for f in wards_geo["features"]:
        for rings in _feature_rings(f):
            if _point_in_polygon(pt, rings):
                p = f["properties"]
                return p.get("ward_no"), p.get("ward_name")
    return None, None


# ---------------------------------------------------------------------------
# KWRIS historical scrape
# ---------------------------------------------------------------------------

def kwris_get_stations_for_district(district, datasource=KWRIS_DATASOURCE, year=None):
    """Fetch KWRIS station catalog for ONE district. Returns list of dicts."""
    year = year or str(dt.date.today().year)
    body = {"Boundary": "1", "District": district, "Taluk": "", "Basin": district,
            "SubBasin": "", "Year": str(year), "Month": "0", "LanguageID": "1",
            "datasource": datasource, "locationguid": ""}
    r = requests.post(f"{KWRIS_BASE}/RainfallAnalytics.aspx/GetRFLocations",
                      headers=KWRIS_HEADERS, data=json.dumps(body),
                      timeout=90, verify=VERIFY_TLS)
    r.raise_for_status()
    raw = r.json().get("d") or "{}"
    fc = json.loads(raw)
    out = []
    for feat in fc.get("features", []):
        p = feat.get("properties", {})
        try:
            lng, lat = feat["geometry"]["coordinates"]
        except (KeyError, TypeError, ValueError):
            lat = p.get("Lat"); lng = p.get("Long")
        out.append({
            "location_guid": p.get("LocationGUID"),
            "location_name": p.get("LocationName"),
            "district_name": p.get("DistrictName"),
            "district_code": district,
            "block_name":    p.get("BlockName"),
            "types":         p.get("Types"),
            "lat": lat, "lng": lng,
        })
    return out


def kwris_get_stations(districts=None, datasource=KWRIS_DATASOURCE, year=None):
    """Fetch stations from multiple districts, dedupe by LocationGUID."""
    districts = districts or KWRIS_DISTRICTS_DEFAULT
    seen = {}
    for d in districts:
        try:
            rows = kwris_get_stations_for_district(d, datasource=datasource, year=year)
        except requests.RequestException as e:
            print(f"  ! district {d}: {e}")
            continue
        added = 0
        for r in rows:
            g = r["location_guid"]
            if g and g not in seen:
                seen[g] = r
                added += 1
        print(f"  district {d}: {len(rows)} rows, {added} new (dedup by GUID)")
    return list(seen.values())


def _parse_date(txt: str, prefer: str | None = None):
    """
    Return (iso_date, detected_format).

    KWRIS returns either M/D/Y or D/M/Y depending on year. Rule:
      - if first token > 12, it must be a day  -> D/M/Y
      - if second token > 12, it must be a day -> M/D/Y
      - else ambiguous: use `prefer` (batch memory) or default to M/D/Y.
    """
    txt = txt.strip()
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", txt)
    if not m:
        return None, prefer
    a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if a > 12 and b <= 12:
        fmt = "%d/%m/%Y"
    elif b > 12 and a <= 12:
        fmt = "%m/%d/%Y"
    else:
        fmt = prefer or "%m/%d/%Y"
    try:
        return dt.datetime.strptime(txt, fmt).date().isoformat(), fmt
    except ValueError:
        return None, prefer


def _detect_batch_format(date_texts):
    """
    Scan every date in the batch and lock the format from the first
    unambiguous row (a token > 12 disambiguates D vs M). Only if every
    single row is ambiguous (all day and month <= 12) do we fall back to
    M/D/Y, which is KWRIS's historical default.
    """
    for txt in date_texts:
        m = re.match(r"^(\d{1,2})/(\d{1,2})/\d{4}$", txt.strip())
        if not m:
            continue
        a, b = int(m.group(1)), int(m.group(2))
        if a > 12 and b <= 12:
            return "%d/%m/%Y"
        if b > 12 and a <= 12:
            return "%m/%d/%Y"
    return "%m/%d/%Y"


def _parse_daily_table(table_html: str):
    rows = re.findall(r"<tr\b[^>]*>(.*?)</tr>", table_html, flags=re.I | re.S)
    parsed_rows = []
    for tr in rows:
        cells = [re.sub(r"<[^>]+>", "", c).strip()
                 for c in re.findall(r"<td\b[^>]*>(.*?)</td>", tr, flags=re.I | re.S)]
        if len(cells) < 3:
            continue
        parsed_rows.append((html.unescape(cells[1]), html.unescape(cells[2])))

    # First pass: look ahead across the whole table to pick the right format.
    batch_fmt = _detect_batch_format(t for t, _ in parsed_rows)

    # Second pass: parse with the batch format, but still let an unambiguous
    # single row override (protects against a batch that mixes years with
    # different formats -- which KWRIS actually does).
    out = []
    for date_txt, rf_txt in parsed_rows:
        iso, _ = _parse_date(date_txt, prefer=batch_fmt)
        if not iso:
            continue
        try:
            rf = float(rf_txt) if rf_txt not in ("", "-", "NA", "null", None) else None
        except ValueError:
            rf = None
        out.append({"date": iso, "rainfall_mm": rf})
    return out


def kwris_get_daily(location_guid, loc_name, year_from, year_to,
                    datasource=KWRIS_DATASOURCE):
    body = {"ValidID": location_guid, "ValidID1": str(year_from),
            "ValidID2": str(year_to), "ValidID3": "1",
            "datasource": datasource, "orderby": "",
            "loc_name": loc_name or ""}
    r = requests.post(f"{KWRIS_BASE}/RainfallAnalytics.aspx/Get_LocationDetails",
                      headers=KWRIS_HEADERS, data=json.dumps(body),
                      timeout=180, verify=VERIFY_TLS)
    r.raise_for_status()
    raw = r.json().get("d") or ""
    parts = raw.split("____")
    if len(parts) < 4 or not parts[3]:
        return []
    return _parse_daily_table(parts[3])


def cmd_history(start_year: int, end_year: int, limit: int = 0, districts=None):
    districts = districts or KWRIS_DISTRICTS_DEFAULT
    print(f"[history] fetching KWRIS stations for districts {districts}...")
    stations = kwris_get_stations(districts=districts)
    print(f"  {len(stations)} unique stations (before spatial filter)")

    # Keep ALL stations. Coverage of KWRIS gauges strictly INSIDE BBMP wards is
    # very sparse (~6 of 198 wards). Instead we IDW from the K nearest stations
    # in the extended Bengaluru area at build time, which gives every ward a
    # real 2009-> history using rainfall's natural spatial correlation.
    # We annotate each row with its containing BBMP ward if it happens to sit in
    # one (informational only), but do not filter.
    wards_geo = json.loads(WARDS_GEOJSON.read_text())
    inside = 0
    for st in stations:
        try:
            lat = float(st["lat"]); lng = float(st["lng"])
        except (TypeError, ValueError):
            st["ward_no"] = ""; st["ward_name"] = ""
            continue
        wno, wname = assign_ward(lat, lng, wards_geo)
        st["ward_no"] = wno if wno is not None else ""
        st["ward_name"] = wname or ""
        if wno is not None:
            inside += 1
    print(f"  ({inside} stations happen to sit inside a BBMP ward; all are kept for IDW.)")

    # Save catalog.
    with KWRIS_STATIONS_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["location_guid", "location_name", "district_name",
                    "district_code", "ward_no", "ward_name",
                    "block_name", "types", "lat", "lng"])
        for st in stations:
            w.writerow([st["location_guid"], st["location_name"], st["district_name"],
                        st.get("district_code", ""),
                        st.get("ward_no", ""), st.get("ward_name", ""),
                        st["block_name"], st["types"], st["lat"], st["lng"]])

    manifest = {"start": start_year, "end": end_year,
                "fetched_at": dt.datetime.utcnow().isoformat() + "Z", "stations": []}
    n = 0
    for s in stations:
        if limit and n >= limit:
            break
        guid = s["location_guid"]
        if not guid:
            continue
        try:
            rows = kwris_get_daily(guid, s["location_name"], start_year, end_year)
        except requests.RequestException as e:
            print(f"  ! {s['location_name']} ({guid}): {e}")
            manifest["stations"].append({"guid": guid, "rows": 0, "error": str(e)})
            continue
        with (KWRIS_DIR / f"{guid}.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["date", "rainfall_mm"])
            for r in rows:
                w.writerow([r["date"], "" if r["rainfall_mm"] is None else r["rainfall_mm"]])
        manifest["stations"].append({"guid": guid, "name": s["location_name"],
                                     "rows": len(rows)})
        print(f"  [{n+1}/{len(stations)}] {s['location_name']}: {len(rows)} daily rows")
        n += 1
        time.sleep(0.2)  # be polite
    KWRIS_MANIFEST.write_text(json.dumps(manifest, indent=2))
    print(f"[history] wrote {n} station CSVs to {KWRIS_DIR}")


# ---------------------------------------------------------------------------
# Discover KWRIS district codes (used by `districts` subcommand).
# ---------------------------------------------------------------------------

def _kwris_discover_districts():
    """Fetch the RainfallAnalytics HTML page and parse the district <select>.
    Returns [{code, name, lat, lng}, ...]."""
    r = requests.get(f"{KWRIS_BASE}/RainfallAnalytics", timeout=30, verify=VERIFY_TLS)
    r.raise_for_status()
    html_txt = r.text
    m = re.search(
        r'<select[^>]*id="ctl00_ContentPlaceHolder1_ddl_district"[^>]*>(.*?)</select>',
        html_txt, flags=re.S)
    if not m:
        return []
    out = []
    for opt in re.finditer(r'<option[^>]*value="([^"]*)"[^>]*>([^<]*)</option>', m.group(1)):
        val, name = opt.group(1), opt.group(2).strip()
        parts = val.split("#")
        if not parts[0]:
            continue
        out.append({"code": parts[0], "name": name,
                    "lat": parts[1] if len(parts) > 1 else "",
                    "lng": parts[2] if len(parts) > 2 else ""})
    return out


def cmd_districts():
    """Print KWRIS district codes so you can pass them to --districts."""
    ds = _kwris_discover_districts()
    if not ds:
        print("[districts] Could not parse the dropdown -- KWRIS page markup may have changed.")
        return
    print(f"[districts] {len(ds)} Karnataka districts from KWRIS:")
    for d in sorted(ds, key=lambda x: x["name"]):
        print(f"  {d['code']:>4}  {d['name']}")


# ---------------------------------------------------------------------------
# KSNDMC live poll (idempotent per date)
# ---------------------------------------------------------------------------

def _ksndmc_query():
    fields = ["TRG_ID", "WARD_NO", "RAIN__MM_", "DATE_"]
    r = requests.get(KSNDMC_LIVE_URL, params={
        "where": "1=1", "outFields": ",".join(fields),
        "returnGeometry": "false", "f": "json"},
        timeout=60, verify=VERIFY_TLS)
    r.raise_for_status()
    j = r.json()
    if "error" in j:
        raise SystemExit(f"KSNDMC ArcGIS error: {j['error']}")
    return j.get("features", [])


def cmd_live():
    """Append today's KSNDMC BBMP live readings to the rolling per-day log."""
    print("[live] polling KSNDMC BBMP_WEATHER_MAP...")
    feats = _ksndmc_query()
    # Group by (date, station); take max rainfall (station may be polled twice/day).
    fresh = {}
    for f in feats:
        a = f["attributes"]
        ms = a.get("DATE_")
        if ms:
            date_iso = dt.datetime.utcfromtimestamp(ms / 1000).date().isoformat()
        else:
            date_iso = dt.date.today().isoformat()
        station = a.get("TRG_ID")
        ward = a.get("WARD_NO")
        rf = a.get("RAIN__MM_")
        if station is None or rf is None:
            continue
        key = (date_iso, str(station))
        prev = fresh.get(key)
        if prev is None or (rf or 0) > (prev["rainfall_mm"] or 0):
            fresh[key] = {"date": date_iso, "station_code": str(station),
                          "ward_no": ward, "rainfall_mm": float(rf)}
    print(f"  {len(fresh)} (date, station) rows from KSNDMC")

    # Append fresh data to the existing log without overwriting old entries
    with KSNDMC_LOG.open("a", newline="", encoding="utf-8") as f:
        # Load station names from the station catalog
        station_names = {}
        with KWRIS_STATIONS_CSV.open("r", encoding="utf-8") as station_file:
            for row in csv.DictReader(station_file):
                station_names[row["location_guid"]] = row["location_name"]

        # Update fieldnames to include station_name
        w = csv.DictWriter(f, fieldnames=["date", "station_code", "station_name", "ward_no", "rainfall_mm"])
        # Write header only if the file is empty
        if f.tell() == 0:
            w.writeheader()
        # Write only new entries
        for k, v in fresh.items():
            station_name = station_names.get(v["station_code"], "Unknown")
            w.writerow({"date": v["date"], "station_code": v["station_code"],
                       "station_name": station_name,
                       "ward_no": "" if v["ward_no"] is None else v["ward_no"],
                       "rainfall_mm": v["rainfall_mm"]})
    # Count the total number of rows in the log file
    total_rows = 0
    with KSNDMC_LOG.open("r", encoding="utf-8") as f:
        for _ in csv.DictReader(f):
            total_rows += 1
    print(f"[live] log now has {total_rows} (date, station) rows -> {KSNDMC_LOG}")


# ---------------------------------------------------------------------------
# Build per-ward JSONs by merging KWRIS + KSNDMC
# ---------------------------------------------------------------------------

import math

def _haversine_km(lat1, lng1, lat2, lng2):
    R = 6371.0
    phi1 = math.radians(lat1); phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1); dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlmb/2)**2
    return 2 * R * math.asin(math.sqrt(a))


def _load_kwris_station_catalog():
    """Return list of {guid, name, lat, lng} for every station in the CSV."""
    if not KWRIS_STATIONS_CSV.exists():
        return []
    out = []
    with KWRIS_STATIONS_CSV.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                lat = float(row["lat"]); lng = float(row["lng"])
            except (TypeError, ValueError):
                continue
            out.append({
                "guid": row["location_guid"],
                "name": row["location_name"],
                "ward_no": row["ward_no"],
                "ward_name": row["ward_name"],
                "lat": lat, "lng": lng
            })
    return out


def _load_station_daily(guid):
    """Return list of {date, rainfall_mm} for one KWRIS station."""
    path = KWRIS_DIR / f"{guid}.csv"
    if not path.exists():
        return []
    out = []
    with path.open("r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                rf = float(row["rainfall_mm"]) if row["rainfall_mm"] not in ("", None) else None
            except ValueError:
                rf = None
            out.append({"date": row["date"], "rainfall_mm": rf})
    return out


def _load_ksndmc_log():
    """Return {(date, ward_no): [rainfall_mm, ...]}."""
    if not KSNDMC_LOG.exists():
        return {}
    by = defaultdict(list)
    with KSNDMC_LOG.open("r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                wno = int(row["ward_no"]) if row["ward_no"] not in ("", None) else None
                rf = float(row["rainfall_mm"])
            except (TypeError, ValueError):
                continue
            if wno is None:
                continue
            by[(row["date"], wno)].append(rf)
    return by


def _ward_daily_from_kwris_idw(centroid_lat, centroid_lng, k, catalog, daily_cache):
    """
    IDW-average daily rainfall across the K nearest KWRIS stations.
    - centroid_lat/lng: ward centroid.
    - k: number of nearest stations to blend.
    - catalog: full station catalog (list of {guid, name, lat, lng}).
    - daily_cache: {guid: [{date, rainfall_mm}]} shared across all wards.
    Returns {date: rainfall_mm}.
    """
    if not catalog:
        return {}
    # Rank stations by distance to the ward centroid.
    ranked = sorted(
        catalog,
        key=lambda st: _haversine_km(centroid_lat, centroid_lng, st["lat"], st["lng"])
    )[:max(k, 1)]
    # Precompute weights and load daily series for each.
    weighted = []
    for st in ranked:
        dist = _haversine_km(centroid_lat, centroid_lng, st["lat"], st["lng"])
        # Guard against div/0 for a station exactly at the centroid.
        w = 1.0 / max(dist, 0.01) ** 2
        if st["guid"] not in daily_cache:
            daily_cache[st["guid"]] = _load_station_daily(st["guid"])
        weighted.append((w, daily_cache[st["guid"]]))
    # Blend per-date.
    num = defaultdict(float)   # sum of w_i * rainfall_i
    den = defaultdict(float)   # sum of w_i (only where the station has a value)
    for w, series in weighted:
        for r in series:
            if r["rainfall_mm"] is None:
                continue
            num[r["date"]] += w * r["rainfall_mm"]
            den[r["date"]] += w
    return {d: num[d] / den[d] for d in num if den[d] > 0}


def _monthly_totals(daily_rows):
    m = defaultdict(float)
    for r in daily_rows:
        if r["rainfall_mm"] is None:
            continue
        m[r["date"][:7]] += r["rainfall_mm"]
    return [{"month": k, "rainfall_mm": round(v, 1)} for k, v in sorted(m.items())]


def cmd_build():
    print("[build] merging KWRIS + KSNDMC into per-ward JSON...")
    wards_geo = json.loads(WARDS_GEOJSON.read_text())
    catalog = _load_kwris_station_catalog()
    ksndmc = _load_ksndmc_log()
    daily_cache = {}  # {guid: [{date, rainfall_mm}]} shared across wards

    ksndmc_dates = sorted({d for (d, _) in ksndmc.keys()})
    print(f"  KWRIS catalog: {len(catalog)} stations (IDW from 3 nearest for each ward)")
    print(f"  KSNDMC live log covers {len(ksndmc_dates)} distinct dates")

    today = dt.date.today().isoformat()
    now_utc = dt.datetime.utcnow().isoformat() + "Z"
    n_written = 0
    K_NEAREST = 3

    for feat in wards_geo["features"]:
        p = feat["properties"]
        wno = p["ward_no"]
        centroid = p.get("centroid") or [None, None]
        c_lng, c_lat = centroid[0], centroid[1]
        if c_lat is None or c_lng is None:
            kwris_daily = {}
        else:
            kwris_daily = _ward_daily_from_kwris_idw(c_lat, c_lng, K_NEAREST, catalog, daily_cache)
        kwris_last = max(kwris_daily) if kwris_daily else None

        # Assemble daily rows. KWRIS covers up to kwris_last; KSNDMC fills after.
        merged = []
        for d in sorted(kwris_daily):
            merged.append({"date": d,
                           "rainfall_mm": round(kwris_daily[d], 1),
                           "source": "KWRIS"})
        for d in ksndmc_dates:
            if kwris_last and d <= kwris_last:
                continue
            vals = ksndmc.get((d, wno))
            if not vals:
                continue
            # Extract station code from KSNDMC log entries for station name lookup
        station_code = None
        for row in csv.DictReader(KSNDMC_LOG.open("r", encoding="utf-8")):
            if row["date"] == d and int(row["ward_no"]) == wno:
                station_code = row.get("station_code")
                break

        station_name = station_info.get(station_code, "Unknown")
        merged.append({"date": d,
                           "rainfall_mm": round(sum(vals) / len(vals), 1),
                           "source": "KSNDMC",
                           "station_name": station_name})

        # Ward stats.
        annual_cutoff = (dt.date.fromisoformat(today) - dt.timedelta(days=365)).isoformat()
        annual = sum(r["rainfall_mm"] for r in merged
                     if r["rainfall_mm"] is not None and r["date"] >= annual_cutoff)
        cur_month_prefix = today[:7]
        cur_month = sum(r["rainfall_mm"] for r in merged
                        if r["rainfall_mm"] is not None and r["date"].startswith(cur_month_prefix))

        # Patch the geojson feature.
        p["rainfall_mm_annual"] = round(annual, 1)
        p["rainfall_mm_month_current"] = round(cur_month, 1)
        p["rainfall_updated_at"] = now_utc
        p["rainfall_source"] = SOURCE_LABEL

        # Ensure station names are included in the merged data for each entry
        station_info = {}
        for station in catalog:
            if station["ward_no"] == wno:
                station_info[station["guid"]] = station["name"]
                break  # Use the first station name for simplicity if multiple stations belong to the same ward

        # Update daily entries to include station names
        daily_with_station = []
        for entry in merged:
            date = entry["date"]
            source = entry["source"]
            rf_mm = entry["rainfall_mm"]
            station_name = station_info.get(entry.get("station_code"), "Unknown") if source == "KSNDMC" else station_info.get(entry.get("station_guid"), "Unknown")
            daily_with_station.append({
                "date": date,
                "rainfall_mm": rf_mm,
                "source": source,
                "station_name": station_name
            })

        payload = {
            "ward_no": wno,
            "ward_name": p.get("ward_name"),
            "source": SOURCE_LABEL,
            "start": merged[0]["date"] if merged else None,
            "end":   merged[-1]["date"] if merged else None,
            "daily": daily_with_station,
            "monthly": _monthly_totals(daily_with_station),
            "annual_total_mm": round(annual, 1),
            "current_month_mm": round(cur_month, 1),
        }
        (WARD_RAINFALL_DIR / f"{wno}.json").write_text(json.dumps(payload))
        n_written += 1

    WARDS_GEOJSON.write_text(json.dumps(wards_geo))
    print(f"[build] wrote {n_written} per-ward JSONs and patched wards.geojson")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    def _dlist(v): return [x.strip() for x in v.split(",") if x.strip()]

    p_hist = sub.add_parser("history", help="Scrape KWRIS daily history (slow, one-time).")
    p_hist.add_argument("--start", type=int, default=2009)
    p_hist.add_argument("--end",   type=int, default=dt.date.today().year)
    p_hist.add_argument("--limit", type=int, default=0, help="Testing: stop after N stations.")
    p_hist.add_argument("--districts", type=_dlist, default=KWRIS_DISTRICTS_DEFAULT,
                        help="Comma-separated KWRIS district codes (default: 166,165,167).")

    sub.add_parser("districts", help="List KWRIS district codes (dumps whatever the dropdown API returns).")
    sub.add_parser("live", help="Poll KSNDMC BBMP live and append to rolling log.")
    sub.add_parser("build", help="Merge caches into data/rainfall/<ward>.json.")

    p_all = sub.add_parser("all", help="history + live + build.")
    p_all.add_argument("--start", type=int, default=2009)
    p_all.add_argument("--end",   type=int, default=dt.date.today().year)
    p_all.add_argument("--limit", type=int, default=0)
    p_all.add_argument("--districts", type=_dlist, default=KWRIS_DISTRICTS_DEFAULT)

    args = p.parse_args()
    if args.cmd == "history":
        cmd_history(args.start, args.end, args.limit, districts=args.districts)
    elif args.cmd == "districts":
        cmd_districts()
    elif args.cmd == "live":
        cmd_live()
    elif args.cmd == "build":
        cmd_build()
    elif args.cmd == "all":
        cmd_history(args.start, args.end, args.limit, districts=args.districts)
        cmd_live()
        cmd_build()


if __name__ == "__main__":
    main()
