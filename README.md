# BWSSB Borewell Dashboard — IISc

An interactive dashboard for exploring BBMP's 198 wards and the borewell sensors reporting inside them. Built by the Indian Institute of Science team for BWSSB.

- **Live URL** (once deployed): https://\<your-user\>.github.io/bbmp-borewell-dashboard-iisc/
- **Codebase**: static HTML/JS/CSS (this repo). Two data modes: (a) read `data/*.json` shipped with the site, or (b) call a Cloudflare Worker + D1 API. Switch via `js/config.js`.
- **Cost**: free at current scale (GitHub Pages + Cloudflare Workers free tier + D1 free tier).

If you're brand new here, **read [START_HERE.md](./START_HERE.md) first** — it walks through viewing locally, publishing to GitHub Pages, and refreshing data.

## Repo layout

```
Dashboard_IISC_for_BWSSB/
  index.html                        Dashboard entry (map + overlays). Includes Leaflet + Chart.js via CDN.
  css/style.css                     All styles. Slate/teal palette, minimal chrome, no default sidebars.
  js/                               Front-end, split from the former single app.js into ordered
                                    classic scripts that share one global scope (zero build step).
    config.js                       Data source switch: "static" (JSON files) or "api" (Worker).
    session-quality.js              Pump-session quality policy (parity with backend).
    state/store.js                  Single source of truth for mutable state + Store.set/subscribe.
    config/constants.js             Lens guard, trend thresholds, palette, notifyError banner.
    core/api.js                     Data-access layer: urlOf/seriesUrlOf, resilient getJson, analytics loaders.
    core/classification-helpers.js  Ward style/status/colour helpers + shared utils.
    core/groundwater.js             Groundwater criticality (Theil-Sen + linear + Modified MK), lens logic.
    core/data.js                    boot(), loadData, sensor-inventory merge, point-in-polygon.
    ui/selection.js                 Pan / ward selection, choropleth palette, choroColor.
    ui/metrics-query.js             KPI cards, metrics, common query, presets.
    ui/map-layers.js                initMap, ward + sensor rendering, legend.
    ui/controls.js                  Toolbar, overlays, search, filters.
    ui/detail.js                    Ward + sensor detail panels.
    ui/charts.js                    Session reconstruction + water/discharge/modal charts.
    bootstrap.js                    fmtDate, rainfall CSV, global listeners, final boot() call (loads last).
    app.js                          DEPRECATED placeholder (old monolith); no longer loaded.
                                    See docs/ARCHITECTURE.md for the module map and load order.

  data/                             Data files served by the static site.
    manifest.json                   Snapshot summary (KH ZIP name, dates, counts).
    wards.geojson                   198 ward polygons + area, population, sensor counts.
    sensors.json                    Per-sensor metadata (uid, lat, lng, ward, HP, depth, coverage).
    sensor_series/<uid>.json        Per-sensor time series (water level, discharge, cumulative yield).

  backend/                          Python scripts that build data/ from raw sources.
    prepare_data.py                 Reads KH ZIP + ward shapefile + population xlsx -> writes data/*.
    sensors_db_extract.py           Pulls sensors table from your Neon Postgres DB (adds motor HP + full UID list) -> data/sensors_db.json.
    requirements.txt

  worker/                           Cloudflare Worker + D1 (optional, for scale).
    src/index.js                    The Worker itself: JSON API over the D1 database.
    wrangler.toml                   Worker + D1 binding config. Fill in your D1 database_id.
    schema.sql                      D1 tables: wards, sensors, sensor_series, snapshots.
    seed_d1.py                      Reads ../data/*.json and writes seed_generated/*.sql for wrangler to execute.
    seed_generated/                 (git-ignored) chunked SQL files produced by seed_d1.py.
    README.md                       End-to-end D1 deploy steps.

  .github/workflows/deploy.yml      GitHub Actions: on push to main -> deploy static site to GitHub Pages.
  .gitignore                        Excludes _to_delete/, .env, __pycache__, seed_generated/, etc.
  start_server.bat                  One-click local dev server (Windows).
  START_HERE.md                     Quickstart for viewing, publishing, refreshing data.
  README.md                         (this file)
```

## Architecture at a glance

Two swappable data modes, one frontend:

```
                            Static mode (default)
                            ─────────────────────
     ┌────────────┐   fetch ┌───────────────┐
     │  Browser   │────────>│ GitHub Pages  │──> serves data/*.json directly
     └────────────┘         └───────────────┘

                            API mode (Cloudflare + D1)
                            ─────────────────────────
     ┌────────────┐   fetch ┌────────────────┐  SQL   ┌───────┐
     │  Browser   │────────>│ Cloudflare Wkr │───────>│  D1   │
     └────────────┘         └────────────────┘        └───────┘
              (js/config.js chooses which)
```

The static mode has zero moving parts and is fine while total data stays under a few hundred MB. The API mode scales to years of historical data on Cloudflare's free tier (5 GB D1, 100k Worker requests/day) and is the path for when you want the dashboard to answer live queries.

## Where the data comes from

- **Raw source**: KH Projects sends a cleaned Excel ZIP (e.g. `borewell_water_level_200826.zip`) with one .xlsx per sensor for the current reporting window (currently 15 Jul – 20 Aug 2026, 579 sensors).
- **Ward boundaries**: BBMP's `bbmpwards.zip` shapefile (198 wards, sits in the sibling `bbmp-borewell-dashboard` repo).
- **Population / area**: `population_source.xlsx` in the sibling `bbmp-borewell-backend/exports/`.
- **Motor HP / borewell depth / full sensor list**: your Neon Postgres `sensors` table, populated by the existing `sync_kh_device_metadata.py` script that scrapes KH.

`backend/prepare_data.py` orchestrates all of the above and writes the JSON files under `data/`.

## Session quality and dashboard inclusion

Sessions start at any cumulative-yield decrease or a time gap above 30 minutes.
All readings and sessions are retained, including single-reading sessions and
sessions with missing water-level endpoints. The four KH flags are fewer than
3 readings, no yield advance, a water-level step above 20 ft, and negative
endpoint drawdown. Zero drawdown is not a net level rise. Missing water/yield
readings are additional dashboard checks.

The following inclusion policy is our dashboard interpretation, not a claim
that KH deletes these sessions:

- **OK**: no quality flags.
- **Flagged**: requires review, including possible re-lock and net level rise.
- **Excluded from default analysis**: fewer than 3 readings, missing/nonpositive
  endpoint volume, or missing endpoint water level. Still visible and downloadable.

Eligibility is separate from status. Volume requires at least two readings,
complete yield readings and positive yield advance. Drawdown requires at least
three readings, complete levels, no >20 ft jump and positive endpoint drawdown.
Specific-capacity eligibility requires both. An excluded two-reading session can
still have measurable volume. Eligibility is a screening decision, not calibration
or proof that a sensor measurement is accurate. No automatic corrections occur.

The sensor table lists every session, reasons, observed values and eligible
calculations, with pagination and CSV download. All retained sessions are shown
initially; the selector can show OK only, flagged only, or excluded only in the
table and charts. Counts cover the full history; chart range buttons only limit
chart dates. Chart colours indicate session status. Discharge comes directly
from flow_lpm, not water level or yield differences; flow accuracy is not separately
validated. Yield-counter problems do not establish that discharge is zero.

`backend/session_quality.py` is the versioned policy shared by the build and the
JSON refresh. `js/session-quality.js` provides a tested equivalent fallback for
older data/API responses. Backend annotations take precedence at matching version.
Run `python backend/refresh_session_quality.py` to refresh the existing snapshot
without rereading the Excel ZIP. It verifies raw arrays remain unchanged and writes
`data/session_quality_summary.json`. Run `python backend/test_session_quality.py`
for policy and backend/browser parity tests.

## Dashboard behaviour

- Full-screen map on load; **no sidebars**. Toolbar (top-right) has search / filters / about.
- Wards choropleth-shaded by sensors-with-data. Toggle in Filters to shade by total sensors or population.
- 579 sensors visible by default. Bottom-right toggle reveals no-data sensors (once `sensors_db_extract.py` has run, this includes KH's full inventory beyond the 579).
- Hover a ward → tooltip (population, area, sensor counts).
- Click a ward → right-side panel with stats + full UID list. Click a UID → jumps to that sensor.
- Click a sensor → metadata (ward, motor HP, depth, first/last reading, reading count) + two charts (water level, discharge). Each chart has 1W / 1M / 3M / All range chips and an expand button for a full-window view.
- Search (Ctrl-K or the magnifier icon): ward name, ward number, or UID. Quick views: top / bottom / no-sensor wards.

## Publishing & refreshing

Both flows are one push. Full commands in [START_HERE.md](./START_HERE.md).

```
# refresh data snapshot
cd backend && python sensors_db_extract.py && python prepare_data.py

# publish
cd ..
git add . && git commit -m "Refresh snapshot" && git push
# GitHub Actions deploys to Pages in ~1 minute
```

## Contact

Original build: Vishwas Prabhakara (IISc), assisted by Claude Code. See individual READMEs in `backend/` and `worker/` for the finer details.
