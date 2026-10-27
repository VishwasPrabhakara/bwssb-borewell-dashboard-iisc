// ui/selection.js
// Part of the BWSSB/IISc borewell dashboard front-end.
// Split from the former single app.js; loaded as an ordered classic <script>,
// sharing one global scope with its sibling files. Order matters: see index.html.


function panToLatLngLeftHalf(lat, lng, zoom) {
  // Shift the pan target east by half the panel width in screen space, so the
  // actual point ends up centered inside the visible left slice of the map.
  const z = zoom != null ? zoom : map.getZoom();
  const half = panelWidthPx() / 2;
  const src = map.project([lat, lng], z);
  const shifted = src.add([half, 0]);
  const target = map.unproject(shifted, z);
  if (zoom != null) map.setView(target, zoom);
  else map.panTo(target);
}

function updateClearFilterChip() {
  const chip = document.getElementById("clear-filter-chip");
  if (!chip) return;
  chip.hidden = (selectedWardNo == null && selectedSensorUid == null);
}

function setSelectedWard(wardNo, feat) {
  selectedWardNo = wardNo;
  selectedSensorUid = null;
  if (wardLayer) wardLayer.setStyle(defaultWardStyle);
  renderSensors();
  buildLegend();
  updateClearFilterChip();
  if (feat) {
    const bbox = L.geoJSON(feat).getBounds();
    // Pretend the right panel is padding so the polygon fits into the visible left half.
    const rightPad = Math.ceil(panelWidthPx()) + 40;
    map.fitBounds(bbox, {
      paddingTopLeft: [60, 60],
      paddingBottomRight: [rightPad, 60],
      maxZoom: 15,
    });
  }
}

function clearWardSelection() {
  selectedWardNo = null;
  selectedSensorUid = null;
  if (wardLayer) wardLayer.setStyle(defaultWardStyle);
  renderSensors();
  document.getElementById("detail").hidden = true;
  buildLegend();
  updateClearFilterChip();
  // Zoom back to the whole city view.
  map.setView([12.972, 77.594], 11, { animate: true });
}

// NOTE: interaction state (currentLens, criticalityMethod, filters, charts,
// currentRange, selectedSensorUid, ...) now lives in js/state/store.js.

// ---------- Palette (choropleth: light -> dark, distinct by lens) ----------
const CHORO = ["#f0f9fb", "#d3ecf1", "#a8d8e2", "#79c1d1", "#4ea6bd", "#2f8ba3", "#1c6e88", "#0e5670"];
const CRITICALITY_COLORS = { critical: "#c23a2e", rise: "#2f9c69", stable: "#cf9a2c", none: "#d4dadd" };
const BASE_WARD_COLOR = { fill: "#a8d8e2", stroke: "#5a7a86", opacity: 0.22 };
const SENSOR_COLORS = { with_data: "#0e7490", no_data: "#64748b" };
// Water-level depth bands for individual borewells (coloured on the map).
// Shallow water table = water near surface (green); deepening to red.
// Depth is a legend filter (not a marker colour), so the swatches are one neutral ramp.
const DEPTH_BAND_ORDER = ["d0", "d100", "d250", "d500", "d750", "d1000"];
const DEPTH_BAND_LABELS = { d0: "0–100 ft", d100: "100–250 ft", d250: "250–500 ft", d500: "500–750 ft", d750: "750–1000 ft", d1000: ">1000 ft" };
const DEPTH_BAND_COLORS = Object.fromEntries(DEPTH_BAND_ORDER.map((k, i) => [k, CHORO[i + 2]]));
// Borewell marker colour = Sen/Mann-Kendall (Hamed-Rao) trend class from data/sensor_trends.json.
const TREND_ORDER = ["declining", "stable", "rising", "none"];
const TREND_COLORS = { declining: CRITICALITY_COLORS.critical, stable: CRITICALITY_COLORS.stable, rising: CRITICALITY_COLORS.rise, none: "#64748b" };
const TREND_LABELS = { declining: "Declining", stable: "Stable", rising: "Rising", none: "No trend" };
const TREND_MODE_LABELS = { raw: "raw readings", cleaned: "cleaned readings", static: "static ≥8h weekly medians", dow: "day-of-week session medians" };
const GROUNDWATER_MIN_SLOPE_WEEKS = 4;

function choroColor(value, breaks) {
  if (value == null || value === 0) return "#e2e8ec";
  for (let i = 0; i < breaks.length; i++) {
    if (value <= breaks[i]) return CHORO[i];
  }
  return CHORO[CHORO.length - 1];
}

function computeBreaks(values) {
  // Use quantile-ish breaks so shading spreads even for skewed distributions.
  const clean = values.filter(v => v != null && v > 0).sort((a, b) => a - b);
  if (clean.length === 0) return [1];
  const n = CHORO.length;
  const breaks = [];
  for (let i = 1; i <= n; i++) {
    const idx = Math.min(clean.length - 1, Math.round((i / n) * (clean.length - 1)));
    breaks.push(clean[idx]);
  }
  // De-dupe consecutive equal breaks
  return breaks.filter((v, i) => i === 0 || v > breaks[i - 1]);
}
