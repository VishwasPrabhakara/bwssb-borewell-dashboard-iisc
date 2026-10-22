# -*- coding: utf-8 -*-
"""Fetch the full KH device catalogue (lat, lng, motor_hp, borewell_depth,
pump_name) directly from the KH admin dashboard, in a single fresh-login run.

Two endpoints are called, with the same session:

  1. GET /admin/download/uid-lat-long-data
         Bulk CSV with UID + Latitude + Longitude for every device KH knows about.
         This is the authoritative device roster.

  2. GET /admin/admin/reports/filter_uid?uid=<UID>
         Per-UID JSON (deviceInfo) with motor_hp, borewell_depth, pump_name,
         among other fields. The lat/long CSV does not carry these three
         columns, so we have to hit filter_uid per device. Same scrape path
         that backend/fetch_kh_metadata.py has used all along.

Output: data/sensors_db.json  (merged, in the shape prepare_data.py expects).
prepare_data.py takes it from here -- sensors.json, sensor_series/*.json,
wards.geojson, spatial join, sensor counts.

Credentials: KH_EMAIL / KH_PASSWORD from <repo>/.env (same as the other scripts).

Usage:
    python fetch_kh_latlng.py                # full run
    python fetch_kh_latlng.py --limit 5      # test on 5 UIDs
    python fetch_kh_latlng.py --sleep 0.1    # throttle between filter_uid calls
    python fetch_kh_latlng.py --workers 4    # parallel filter_uid workers
"""
import argparse
import csv
import html.parser
import http.cookiejar
import io
import json
import os
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
SENSORS_DB = REPO / "data" / "sensors_db.json"
DOTENV     = REPO / ".env"

BASE         = "https://khprojects.in/reports"
LOGIN_PAGE   = f"{BASE}/admin/login"
LOGIN_POST   = f"{BASE}/login"
REPORTS_PAGE = f"{BASE}/admin/reports"
LATLNG_CSV   = f"{BASE}/admin/download/uid-lat-long-data"
FILTER_UID   = f"{BASE}/admin/admin/reports/filter_uid"

TIMEOUT = 180
UA = "Mozilla/5.0 IISc-BWSSB dashboard fetch_kh_latlng"


# ---------- .env ----------
def load_dotenv(path: Path):
    if not path.exists(): return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.strip().startswith("#") or "=" not in line: continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# ---------- HTTP ----------
class HiddenInputParser(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(); self.inputs = {}
    def handle_starttag(self, tag, attrs):
        if tag.lower() != "input": return
        d = dict(attrs); name = d.get("name")
        if name: self.inputs[name] = d.get("value", "")


def _request(opener, url, data=None, headers=None):
    hdr = {"User-Agent": UA, "Accept": "text/html,application/json,text/csv,*/*"}
    if headers: hdr.update(headers)
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        hdr.setdefault("Content-Type", "application/x-www-form-urlencoded")
    req = urllib.request.Request(url, data=body,
                                 method=("POST" if body is not None else "GET"),
                                 headers=hdr)
    return opener.open(req, timeout=TIMEOUT)


def login():
    """Fresh KH login. Returns a cookie-bearing urllib opener."""
    load_dotenv(DOTENV)
    email    = os.environ.get("KH_EMAIL")
    password = os.environ.get("KH_PASSWORD")
    if not email or not password:
        print("ERROR: KH_EMAIL / KH_PASSWORD missing in .env", file=sys.stderr); sys.exit(2)
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar),
                                         urllib.request.HTTPRedirectHandler())
    print(f"  GET  {LOGIN_PAGE}")
    r = _request(opener, LOGIN_PAGE)
    txt = r.read().decode("utf-8", errors="replace")
    p = HiddenInputParser(); p.feed(txt)
    token = p.inputs.get("_token", "")
    if not token:
        print("ERROR: no CSRF _token on login page", file=sys.stderr); sys.exit(3)
    print(f"  POST {LOGIN_POST}  (as {email})")
    r = _request(opener, LOGIN_POST,
                 data={"_token": token, "email": email, "password": password},
                 headers={"Origin": "https://khprojects.in", "Referer": LOGIN_PAGE})
    final = r.geturl()
    body  = r.read().decode("utf-8", errors="replace")
    if "/admin/login" in final or "Sign in with your login credentials" in body:
        print("ERROR: KH login failed. Check KH_EMAIL and KH_PASSWORD.", file=sys.stderr); sys.exit(4)
    _request(opener, REPORTS_PAGE, headers={"Referer": final}).read()  # some endpoints need this
    print("  login OK")
    return opener


# ---------- lat/lng CSV ----------
def _pick(row, *keys):
    for k in keys:
        for col, v in row.items():
            if col and col.strip().lower() == k.lower():
                if v is None: continue
                vs = str(v).strip()
                if vs: return vs
    return None

def _num(v):
    if v is None: return None
    try: return float(v)
    except (ValueError, TypeError): return None


def fetch_latlng(opener):
    print(f"  GET  {LATLNG_CSV}  (fresh CSV)")
    r = _request(opener, LATLNG_CSV, headers={"Referer": REPORTS_PAGE})
    data = r.read()
    if "/admin/login" in r.geturl() or (b"<html" in data[:500].lower() and b"login" in data[:2000].lower()):
        print("ERROR: lat-long download redirected to login", file=sys.stderr); sys.exit(5)
    text = data.decode("utf-8-sig", errors="replace")
    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        uid = _pick(row, "UID", "uid", "id", "sensor")
        if not uid: continue
        out[str(uid).strip()] = {
            "lat": _num(_pick(row, "Latitude", "lat", "y")),
            "lng": _num(_pick(row, "Longitude", "lon", "lng", "long", "x")),
        }
    print(f"  CSV rows: {len(out)}")
    return out


# ---------- per-UID metadata via filter_uid ----------
def fetch_device_info(opener, uid):
    """One UID: hit filter_uid JSON endpoint, return deviceInfo dict (or {})."""
    q = urllib.parse.urlencode({"dateFrom": "", "dateTo": "", "uid": uid})
    r = _request(opener, f"{FILTER_UID}?{q}",
                 headers={"Referer": f"{BASE}/admin/reports-uid?uid={uid}"})
    payload = json.loads(r.read().decode("utf-8", errors="replace"))
    return payload.get("deviceInfo") or {}


def fetch_metadata_bulk(opener, uids, workers, sleep_s):
    """For every UID, call filter_uid and extract motor_hp, borewell_depth,
    pump_name. Threaded (threads share the login cookies via opener).

    Returns dict uid -> {motor_hp, borewell_depth, pump_name}.
    """
    out = {}
    lock = threading.Lock()
    done = 0
    hits = 0
    fails = 0
    total = len(uids)
    print(f"  per-UID metadata via {FILTER_UID}")
    print(f"    {total} UIDs, {workers} worker thread(s), {sleep_s:.2f} s between calls per worker")

    def _one(uid):
        nonlocal done, hits, fails
        try:
            info = fetch_device_info(opener, uid)
            rec = {
                "motor_hp":       _num(info.get("motor_hp")),
                "borewell_depth": _num(info.get("borewell_depth")),
                "pump_name":      (str(info.get("pump_name")).strip()
                                   if info.get("pump_name") else None),
            }
            with lock:
                out[uid] = rec
                if rec["motor_hp"] is not None or rec["borewell_depth"] is not None or rec["pump_name"]:
                    hits += 1
        except Exception as e:
            with lock:
                out[uid] = {"motor_hp": None, "borewell_depth": None, "pump_name": None,
                            "_error": str(e)[:180]}
                fails += 1
        finally:
            if sleep_s:
                time.sleep(sleep_s)
            with lock:
                done += 1
                if done % 100 == 0 or done == total:
                    print(f"    fetched {done}/{total}  hits={hits}  failures={fails}", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for uid in uids:
            ex.submit(_one, uid)
    return out


# ---------- main ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit",   type=int,   default=0, help="Stop after N UIDs (test).")
    ap.add_argument("--sleep",   type=float, default=0.08, help="Seconds between filter_uid calls per worker.")
    ap.add_argument("--workers", type=int,   default=5, help="Parallel filter_uid worker threads.")
    args = ap.parse_args()

    print("Fetching lat/lng/meta from KH dashboard (fresh login every run) ...")
    opener = login()

    latlng = fetch_latlng(opener)
    uids = sorted(latlng.keys())
    if args.limit:
        uids = uids[: args.limit]
        print(f"  --limit {args.limit}: scraping metadata for {len(uids)} of {len(latlng)} UIDs")

    meta = fetch_metadata_bulk(opener, uids, args.workers, args.sleep)

    # Merge and write sensors_db.json
    print("Writing data/sensors_db.json ...")
    rows = []
    for uid in sorted(set(latlng) | set(meta)):
        ll = latlng.get(uid, {})
        md = meta.get(uid, {})
        rec = {"uid": uid}
        if ll.get("lat") is not None:            rec["lat"] = ll["lat"]
        if ll.get("lng") is not None:            rec["lng"] = ll["lng"]
        if md.get("motor_hp") is not None:       rec["motor_hp"] = md["motor_hp"]
        if md.get("borewell_depth") is not None: rec["borewell_depth"] = md["borewell_depth"]
        if md.get("pump_name"):                  rec["pump_name"] = md["pump_name"]
        rows.append(rec)
    SENSORS_DB.parent.mkdir(parents=True, exist_ok=True)
    json.dump(rows, open(SENSORS_DB, "w"), indent=2)

    # Summary
    with_latlng = sum(1 for r in rows if r.get("lat") is not None and r.get("lng") is not None)
    with_depth  = sum(1 for r in rows if r.get("borewell_depth") is not None)
    with_hp     = sum(1 for r in rows if r.get("motor_hp") is not None)
    with_name   = sum(1 for r in rows if r.get("pump_name"))
    print(f"  wrote {len(rows)} rows to {SENSORS_DB}")
    print(f"    with lat/lng:        {with_latlng}")
    print(f"    with motor HP:       {with_hp}")
    print(f"    with borewell depth: {with_depth}")
    print(f"    with pump name:      {with_name}")
    print("Done. Now run backend/prepare_data.py to merge this catalogue with the KH ZIP.")


if __name__ == "__main__":
    main()
