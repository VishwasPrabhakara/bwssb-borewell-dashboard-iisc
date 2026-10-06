# -*- coding: utf-8 -*-
"""In-memory KH metadata fetch for a given set of UIDs.

Logs into https://khprojects.in/reports with KH_EMAIL / KH_PASSWORD from
<repo>/.env, downloads the uid-lat-long CSV (bulk), then hits
filter_uid?uid=<UID> for each UID requested (threaded) to collect
motor_hp / borewell_depth / pump_name.

Nothing is cached to disk. Call once per run from a build script:

    from _kh_meta import fetch_for_uids
    meta = fetch_for_uids(["86099...", "86593...", ...])
    # meta[uid] = {"lat": ..., "lng": ..., "borewell_depth": ..., "motor_hp": ..., "pump_name": ...}
"""
import csv, html.parser, http.cookiejar, io, json, os, sys, threading, time
import urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOTENV = REPO / ".env"

BASE          = "https://khprojects.in/reports"
LOGIN_PAGE    = f"{BASE}/admin/login"
LOGIN_POST    = f"{BASE}/login"
REPORTS_PAGE  = f"{BASE}/admin/reports"
LATLNG_CSV    = f"{BASE}/admin/download/uid-lat-long-data"
FILTER_UID    = f"{BASE}/admin/admin/reports/filter_uid"

TIMEOUT = 180
UA = "Mozilla/5.0 IISc-BWSSB in-memory KH fetch"


def _load_dotenv(path: Path):
    if not path.exists(): return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.strip().startswith("#") or "=" not in line: continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class _HiddenInput(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(); self.inputs = {}
    def handle_starttag(self, tag, attrs):
        if tag.lower() != "input": return
        d = dict(attrs); n = d.get("name")
        if n: self.inputs[n] = d.get("value", "")


def _req(opener, url, data=None, headers=None):
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


def _login():
    _load_dotenv(DOTENV)
    email = os.environ.get("KH_EMAIL")
    pw    = os.environ.get("KH_PASSWORD")
    if not email or not pw:
        print("ERROR: KH_EMAIL / KH_PASSWORD missing in .env", file=sys.stderr); sys.exit(2)
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar),
                                         urllib.request.HTTPRedirectHandler())
    r = _req(opener, LOGIN_PAGE)
    txt = r.read().decode("utf-8", errors="replace")
    p = _HiddenInput(); p.feed(txt); token = p.inputs.get("_token", "")
    if not token:
        print("ERROR: no CSRF _token", file=sys.stderr); sys.exit(3)
    r = _req(opener, LOGIN_POST,
             data={"_token": token, "email": email, "password": pw},
             headers={"Origin": "https://khprojects.in", "Referer": LOGIN_PAGE})
    final = r.geturl(); body = r.read().decode("utf-8", errors="replace")
    if "/admin/login" in final or "Sign in with your login credentials" in body:
        print("ERROR: KH login failed", file=sys.stderr); sys.exit(4)
    _req(opener, REPORTS_PAGE, headers={"Referer": final}).read()
    return opener


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
    except Exception: return None


def _bulk_latlng(opener):
    r = _req(opener, LATLNG_CSV, headers={"Referer": REPORTS_PAGE})
    data = r.read().decode("utf-8-sig", errors="replace")
    out = {}
    for row in csv.DictReader(io.StringIO(data)):
        uid = _pick(row, "UID", "uid", "id")
        if not uid: continue
        out[str(uid).strip()] = {
            "lat": _num(_pick(row, "Latitude", "lat")),
            "lng": _num(_pick(row, "Longitude", "lng", "long", "lon")),
        }
    return out


def fetch_for_uids(uids, workers=5, sleep_s=0.06, verbose=True):
    """Return dict uid -> {lat, lng, borewell_depth, motor_hp, pump_name}.
    Only the UIDs in `uids` are kept. UIDs the dashboard does not know are
    returned with lat/lng/depth/HP = None."""
    uids = {str(u).strip() for u in uids}
    if verbose: print(f"[kh_meta] fresh login + fetch for {len(uids)} UIDs ...")
    opener = _login()

    if verbose: print("[kh_meta]   downloading uid-lat-long CSV ...")
    bulk = _bulk_latlng(opener)
    if verbose: print(f"[kh_meta]   CSV has {len(bulk)} rows; keeping {len(uids & bulk.keys())} that match ZIP UIDs")

    out = {}
    lock = threading.Lock()
    done = 0; hits = 0; fails = 0
    total = len(uids)

    def _one(uid):
        nonlocal done, hits, fails
        rec = {"lat": None, "lng": None, "borewell_depth": None,
               "motor_hp": None, "pump_name": None}
        rec.update(bulk.get(uid) or {})
        try:
            q = urllib.parse.urlencode({"dateFrom": "", "dateTo": "", "uid": uid})
            r = _req(opener, f"{FILTER_UID}?{q}",
                     headers={"Referer": f"{BASE}/admin/reports-uid?uid={uid}"})
            info = (json.loads(r.read().decode("utf-8", errors="replace"))
                    .get("deviceInfo") or {})
            if _num(info.get("motor_hp")) is not None:
                rec["motor_hp"] = _num(info.get("motor_hp"))
            if _num(info.get("borewell_depth")) is not None:
                rec["borewell_depth"] = _num(info.get("borewell_depth"))
            if info.get("pump_name"):
                rec["pump_name"] = str(info["pump_name"]).strip()
            with lock:
                out[uid] = rec
                if rec["motor_hp"] is not None or rec["borewell_depth"] is not None or rec["pump_name"]:
                    hits += 1
        except Exception as e:
            with lock:
                out[uid] = rec
                fails += 1
        finally:
            if sleep_s: time.sleep(sleep_s)
            with lock:
                done += 1
                if verbose and (done % 100 == 0 or done == total):
                    print(f"[kh_meta]   fetched {done}/{total}  hits={hits}  failures={fails}",
                          flush=True)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for uid in sorted(uids):
            ex.submit(_one, uid)

    if verbose:
        with_latlng = sum(1 for v in out.values() if v["lat"] is not None and v["lng"] is not None)
        with_depth  = sum(1 for v in out.values() if v["borewell_depth"] is not None)
        print(f"[kh_meta] done: {len(out)} UIDs  (with lat/lng: {with_latlng}, with depth: {with_depth})")
    return out
