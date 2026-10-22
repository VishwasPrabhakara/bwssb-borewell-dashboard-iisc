// core/classification-helpers.js
// Part of the BWSSB/IISc borewell dashboard front-end.
// Split from the former single app.js; loaded as an ordered classic <script>,
// sharing one global scope with its sibling files. Order matters: see index.html.


function defaultWardStyle(feat) {
  const p = feat.properties;
  const isSelected = selectedWardNo != null && p.ward_no === selectedWardNo;
  const status = wardStatusKey(p);
  const filterDimmed = wardStatusFilter && status !== wardStatusFilter;
  const isHighlighted = highlightedWardNos.has(normalizeWardNo(p.ward_no));
  const selectionHighlighted = isHighlighted && ["query", "quick"].includes(highlightStyleMode);
  const quickDimmed = highlightedWardNos.size > 0 && !isHighlighted;
  const dimmed = (selectedWardNo != null && !isSelected) || filterDimmed || quickDimmed;
  if (!(p.sensor_with_data > 0)) {
    return {
      fillColor: selectionHighlighted ? "#2563eb" : '#d1d5db',
      color: isSelected ? "#0b3d4c" : selectionHighlighted ? "#1d4ed8" : isHighlighted ? "#0e7490" : "#9ca3af",
      weight: isSelected ? 3 : isHighlighted ? 3.2 : 0.6,
      fillOpacity: dimmed ? 0.12 : selectionHighlighted ? 0.78 : isHighlighted ? 0.72 : 0.55,
      opacity: dimmed ? 0.35 : 1,
      dashArray: isHighlighted ? null : '3 3'
    };
  }
  const colored = wardColor(p);
  return {
    color: isSelected ? "#0b3d4c" : selectionHighlighted ? "#1d4ed8" : colored.stroke,
    weight: isSelected ? 3 : isHighlighted ? 3.2 : (status === "none" ? 0.9 : 1.35),
    opacity: dimmed ? 0.16 : 0.95,
    fillColor: isSelected ? "#028090" : selectionHighlighted ? "#2563eb" : colored.fill,
    fillOpacity: dimmed ? 0.05 : (isSelected ? 0.42 : selectionHighlighted ? 0.78 : isHighlighted ? Math.max(colored.opacity, 0.5) : colored.opacity),
  };
}

function sensorStatusKey(s) {
  if (!s.has_data) return "no_data";
  return "with_data";
}

function wardStatusKey(p) {
  if (analyticsLoaded && isAnalysisLens(currentLens)) return mapAnalysisWardStatusKey(p.ward_no);
  const total = p.sensor_total || 0;
  const withData = p.sensor_with_data || 0;
  if (!total && !withData) return "none";
  const ratio = total ? withData / total : 0;
  if (withData >= 5 || ratio >= 0.75) return "high";
  return "low";
}

function wardReadingLoad(p) {
  return (sensorsByWard[p.ward_no] || []).reduce((sum, s) => sum + (s.reading_count || 0), 0);
}

function wardColor(p) {
  if (analyticsLoaded && isAnalysisLens(currentLens)) {
    const key = mapAnalysisWardStatusKey(p.ward_no);
    if (key === "none") return BASE_WARD_COLOR;
    return {
      fill: CRITICALITY_COLORS[key] || BASE_WARD_COLOR.fill,
      stroke: CRITICALITY_COLORS[key] || BASE_WARD_COLOR.stroke,
      opacity: 1,
    };
  }
  const values = wards ? wards.features.map(f => currentLens === "readings" ? wardReadingLoad(f.properties) : (f.properties.sensor_with_data || 0)) : [];
  const color = choroColor(currentLens === "readings" ? wardReadingLoad(p) : (p.sensor_with_data || 0), computeBreaks(values));
  return { fill: color, stroke: "#5a7a86", opacity: (p.sensor_with_data || 0) ? 0.3 : 0.12 };
}

function normalizeWardNo(value) {
  if (value == null || value === "") return null;
  const n = Number(String(value).trim());
  return Number.isFinite(n) ? n : null;
}

function isYes(value) {
  return String(value || "").trim().toLowerCase() === "yes";
}

const LINEAR_DECLINE_THRESHOLD_FT_PER_WEEK = 0.1;
const TREND_SIGNIFICANCE_ALPHA = 0.05;
const GROUNDWATER_MIN_MK_WEEKS_MODERN = 30;
const GROUNDWATER_MIN_MK_WEEKS_LEGACY = 8;
const PREVIOUS_CONSUMPTION_CRITICAL_WARDS = new Set([
  48, 33, 13, 122, 102, 161, 22, 195, 127, 116,
  15, 183, 74, 37, 68, 31, 19, 187, 43, 28,
  123, 134, 14, 130, 69, 32, 57, 186, 189, 190,
  163, 39, 185, 124, 10, 156, 103, 71, 148, 128,
  81, 21, 6, 75, 49, 171, 30, 70, 126, 36,
  8, 133, 131, 97, 121, 101, 164, 18, 144, 40
]);

