# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Existing, zero-build static site: HTML + plain CSS + ordered classic-script JS
(no bundler), Leaflet + Chart.js from CDN, deployed to GitHub Pages (repo root).
A Python pipeline (`backend/`) builds `data/*.json`; an optional Cloudflare
Worker + D1 API is the scale path. The redesign must stay within this zero-build
constraint.

## Users

Primary: BWSSB (Bangalore Water Supply & Sewerage Board) officials and zonal/ward
engineers reviewing groundwater status across Bengaluru. Secondary: IISc (Indian
Institute of Science) researchers and reviewers. They open it on desktops and on
large control-room screens to see where groundwater is deep or declining by ward,
and to drill into individual borewell sensors.

## Product Purpose

An operational monitoring dashboard that maps ~865 reporting borewell sensors
across BBMP's 198 wards, classifies each ward's groundwater trend (deepening /
rising / stable / insufficient), and lets an official move from a city-wide map to
one ward's sensors to a single sensor's water-level and discharge time-series.
Success = an official can answer "which wards are deepening, and how sure are we"
at a glance, then verify it in the detail.

## Positioning

The densest near-real-time, borewell-derived groundwater picture for Bengaluru at
ward granularity, with the trend statistics and per-ward confidence shown openly
rather than hidden behind a single headline number.

## Operating Context

Map-first. Clicking a ward isolates it and lists its sensors; clicking a sensor
opens its detail and charts. Used on normal desktops and wide control-room
displays (a "TV mode" exists). Data comes from KH Projects' cleaned Excel
snapshots processed by the Python pipeline; the current record is a ~15-month,
monsoon-biased window, and that caveat is shown on screen.

## Capabilities and Constraints

- Ward choropleth coloured by groundwater-decline class (red deepening / green
  rising / yellow stable / grey insufficient); sensor markers; legend.
- Seven analysis lenses (groundwater decline is primary; volumetric deficit,
  extraction, volume-normalized drawdown, previous-consumption, specific capacity,
  current stress are secondary/advanced); classification-method and trend-method
  choosers; a common-query; ward/sensor status filters; quick views; search; TV mode.
- Per-ward confidence badges (High/Med/Low by well count; MK-confirmed / watchlist
  / sparse); per-sensor water-level + discharge charts.
- Constraints: zero-build static; deploy as repo root via GitHub Pages; Leaflet +
  Chart.js from CDN; preserve the element IDs the JS wires to (or update wiring in
  lockstep). Live, government-facing tool — do not break existing behavior.

## Brand Commitments

Wordmark: "BWSSB Borewell Dashboard — Indian Institute of Science." A government /
academic monitoring tool: the register is calm, authoritative, and legible. No
palette, typography, or style has been pinned by the user; those are delegated to
the design pass. No logo asset has been provided.

## Evidence on Hand

Real data under `data/` (wards.geojson = 198 ward polygons with population + sensor
counts; sensors.json = ~865 reporting of 1,715; analytics summaries for groundwater
criticality, current stress, pumping performance, volumetric deficit; per-sensor
time-series). Source zips consolidated in `data_archive/`. No testimonials,
customers, prices, or benchmarks exist; none are to be fabricated.

## Product Principles

- Accuracy and transparency over flash: show confidence and method, never a bare
  colour with no basis.
- Glanceable first, deep on demand: the city-wide ward status reads instantly;
  full analytical controls are available but not in the way.
- Works at control-room scale: legible and uncluttered from a laptop to a wide
  wall display, never overlapping or clipping.
- Preserve the analysis: every existing lens and control stays reachable.

## Accessibility & Inclusion

Must stay legible at control-room viewing distance and across desktop widths; no
reliance on colour alone for ward status (labels/legend accompany the choropleth).
