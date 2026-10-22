# Front-end architecture

The dashboard is a zero-build static site (HTML + CSS + plain JS, Leaflet + Chart.js
from CDN). It deploys to GitHub Pages by uploading the repo root, so there is no
bundler and no npm step at runtime.

## Why ordered classic scripts (not ES modules)

`js/app.js` was a single 2,043-line file. It is now split into focused files that
load as **ordered classic `<script>`s sharing one global scope** — exactly how the
single file behaved, so the split is behavior-identical (verified: the concatenation
of the slices in load order is byte-for-byte the original app.js). No `import`/`export`
rewiring and no global-to-state rewrite were needed, which keeps regression risk ~zero
on a live, government-facing tool. **Order matters** — see `index.html`.

## Load order and module map

Loaded before the dashboard:
1. `js/config.js` — `window.DASHBOARD_CONFIG` (static vs api data source).
2. `js/session-quality.js` — pump-session quality policy (parity with backend `session_quality.py`).

Then, in order:

| # | File | Responsibility |
|---|------|----------------|
| 1 | `js/config/constants.js` | lens guard, `CONFIG`, analytics API base, `urlOf`/`seriesUrlOf`, core state vars (part 1) |
| 2 | `js/core/classification-helpers.js` | ward style/status/colour helpers, `normalizeWardNo`/`isYes`, trend thresholds, previous-consumption ward set |
| 3 | `js/core/groundwater.js` | groundwater criticality (Theil-Sen + linear + Modified Mann-Kendall), lens classification, labels, ward summaries |
| 4 | `js/ui/selection.js` | pan-to-ward, ward selection, interaction state (part 2), choropleth palette, `choroColor`/`computeBreaks` |
| 5 | `js/core/data.js` | `boot()`, `loadData`, `loadAnalyticsData`, sensor inventory merge, point-in-polygon ward assignment |
| 6 | `js/ui/metrics-query.js` | KPI cards, metrics, lens counts, common query, query presets |
| 7 | `js/ui/map-layers.js` | `initMap`, ward choropleth render, sensor render, legend |
| 8 | `js/ui/controls.js` | toolbar, overlays, search, filters |
| 9 | `js/ui/detail.js` | ward detail panel, sensor detail panel |
| 10 | `js/ui/charts.js` | session reconstruction, quality datasets, water/discharge/modal charts, rested-level methodology |
| 11 | `js/bootstrap.js` | `fmtDate`, rainfall CSV, `switchCriticalityMethod`, global listeners, and the final `boot()` call - MUST stay last |

`js/app.js` is now an inert placeholder (the old monolith). It is no longer loaded;
safe to delete once file-deletion is permitted on the device.

## Data pipeline (where the JSON comes from)

`backend/prepare_data.py` reads a KH borewell Excel ZIP + the BBMP ward shapefile +
population, and writes `data/*.json` (served by the site). The large source ZIPs live
in `data_archive/` (git-ignored, not deployed). See `data_archive/README.md`.

## Verifying a change

There is no build. To smoke-test locally: serve the repo root
(`python -m http.server`) and open it; the map should render ~1,000 ward polygons,
the KPI strip should fill (e.g. "574 / 105/198 / ..."), and the browser console should
be free of JavaScript errors (blocked map-tile / analytics-API requests are expected
offline and are not errors).

## State & data access (added in the cleanup)

- **`js/state/store.js`** is the single source of truth for mutable state and loads
  first. Reads use the bare globals (e.g. `currentLens`); interaction-state WRITES
  go through `Store.set(key, value)`, which performs the assignment in one place and
  notifies `Store.subscribe` listeners. Load-time data (wards, sensors, the lookup
  Maps) is assigned directly by `core/data.js`.
- **`js/core/api.js`** is the only module that knows where data comes from. It owns
  `CONFIG`, `ANALYTICS_API_BASE` (overridable via `window.DASHBOARD_CONFIG.analyticsApiBase`),
  `urlOf`/`seriesUrlOf`, a resilient `getJson(url, {fallback,label,onError})`, and the
  analytics/criticality loaders. UI and logic modules never call `fetch` directly.
- **Error handling**: `getJson` degrades to a fallback and logs; required layers
  (wards/sensors/manifest) throw, and `boot()` catches and shows the `notifyError`
  banner (`config/constants.js`) plus the About panel.

## Naming conventions

- JS identifiers (functions, local vars, Store keys) are **camelCase**.
- **snake_case appears only where it mirrors data/JSON keys** from the backend
  (`ward_no`, `sensor_with_data`, `last_data_at`), so the shapes match the payloads
  1:1 — do not rename those.
- Shared singletons use **PascalCase** (`Store`); module-private helpers may keep a
  leading underscore (`_guardLens`).

## Large source data

`data_archive/` (git-ignored, not deployed) holds the ~1 GB of source borewell/water
ZIPs consolidated from Downloads and the sibling repos. See `data_archive/README.md`.
