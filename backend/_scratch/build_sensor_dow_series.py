#!/usr/bin/env python3
"""Parse the 05.10.26 ZIP into per-sensor on-level session series."""
import os, sys, json, zipfile, io
from pathlib import Path
from datetime import datetime, timedelta
from concurrent.futures import ProcessPoolExecutor
import python_calamine as pc

ZIP_PATH = os.environ.get("KH_ZIP", "")
ROOT     = Path(__file__).resolve().parents[2]
OUT_DIR  = ROOT / "outputs" / "sensor_dow"
GAP_HOURS = 8

TS_FORMATS = ('%d%m%y %H%M%S', '%d%m%Y %H%M%S',
              '%Y-%m-%d %H:%M:%S', '%d/%m/%Y %H:%M:%S', '%d-%m-%Y %H:%M:%S',
              '%Y-%m-%dT%H:%M:%S')

def parse_ts(ts):
    if isinstance(ts, datetime): return ts
    if isinstance(ts, (int, float)):
        return datetime(1899, 12, 30) + timedelta(days=float(ts))
    if not isinstance(ts, str): return None
    s = ts.strip()
    if not s: return None
    for fmt in TS_FORMATS:
        try: return datetime.strptime(s, fmt)
        except ValueError: continue
    try: return datetime.fromisoformat(s.replace('/', '-'))
    except Exception: return None

def parse_sheet(name, raw):
    uid = Path(name).stem.split("_")[0]
    try:
        wb = pc.CalamineWorkbook.from_filelike(io.BytesIO(raw))
        sheet = wb.get_sheet_by_index(0).to_python()
    except Exception:
        return uid, []
    # KH files: row 0 = metadata (UID: ..., Downloaded: ...), row 1 = headers, row 2+ = data
    header_i = None
    for i, row in enumerate(sheet[:10]):
        joined = ' | '.join(str(c).lower() for c in row if c is not None)
        if 'water level' in joined or ('timestamp' in joined and 'water' in joined):
            header_i = i; break
    if header_i is None:
        return uid, []
    header = [str(c).lower() if c is not None else "" for c in sheet[header_i]]
    ti = wi = None
    for i, c in enumerate(header):
        if ti is None and ('timestamp' in c or 'date' in c or 'time' in c): ti = i
        if wi is None and 'water' in c: wi = i
    if ti is None or wi is None:
        return uid, []
    rows = []
    for r in sheet[header_i + 1:]:
        if ti >= len(r) or wi >= len(r): continue
        ts = parse_ts(r[ti])
        if ts is None: continue
        try: wv = float(r[wi])
        except (TypeError, ValueError): continue
        if not (-500.0 < wv < 2000.0): continue
        rows.append((ts, wv))
    rows.sort(key=lambda x: x[0])
    return uid, rows

def segment(rows):
    out, cur = [], []
    for ts, wv in rows:
        if cur and (ts - cur[-1][0]).total_seconds() / 3600.0 >= GAP_HOURS:
            if len(cur) >= 2: out.append(cur)
            cur = []
        cur.append((ts, wv))
    if len(cur) >= 2: out.append(cur)
    return out

def worker(args):
    name, raw = args
    uid, rows = parse_sheet(name, raw)
    if not rows: return uid, []
    sessions = segment(rows)
    series = []
    for s in sessions:
        wvs = sorted(x[1] for x in s)
        series.append({
            "ts": s[0][0].isoformat(timespec="seconds"),
            "weekday": s[0][0].weekday(),
            "med_water_ft": round(wvs[len(wvs)//2], 2),
            "n": len(s)
        })
    return uid, series

def main():
    if not ZIP_PATH or not os.path.exists(ZIP_PATH):
        raise SystemExit("Set KH_ZIP env var to the ZIP path")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ZIP_PATH) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(('.xlsx', '.xls'))]
        print(f"[dow] {len(names)} sheets in ZIP")
        payload = [(n, zf.read(n)) for n in names]
    done = ok = 0
    with ProcessPoolExecutor(max_workers=max(1, os.cpu_count() - 1)) as ex:
        for uid, series in ex.map(worker, payload, chunksize=4):
            if series:
                (OUT_DIR / f"{uid}.json").write_text(json.dumps(series, separators=(",", ":")))
                ok += 1
            done += 1
            if done % 50 == 0:
                print(f"  {done}/{len(payload)}  ok={ok}")
    print(f"[dow] wrote {ok} sensor files -> {OUT_DIR}")

if __name__ == "__main__":
    main()
