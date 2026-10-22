// ui/metrics-query.js
// Part of the BWSSB/IISc borewell dashboard front-end.
// Split from the former single app.js; loaded as an ordered classic <script>,
// sharing one global scope with its sibling files. Order matters: see index.html.

function recomputeWardSensorCounts() {
  for (const f of wards.features) {
    const list = sensorsByWard[normalizeWardNo(f.properties.ward_no)] || [];
    f.properties.sensor_total = list.length;
    f.properties.sensor_with_data = list.filter(s => s.has_data).length;
  }
}

function syncAnalyticsControls() {
  const lensSelect = document.getElementById("analysis-lens");
  if (lensSelect) lensSelect.value = currentLens;
  const methodSelect = document.getElementById("groundwater-method");
  if (methodSelect) methodSelect.value = groundwaterMethodMode;
  const methodRow = document.getElementById("groundwater-method-row");
  if (methodRow) methodRow.hidden = !(analyticsLoaded && currentLens === "groundwater");
  const note = document.getElementById("analytics-note");
  if (note) note.textContent = analyticsLoaded
    ? "Earlier-dashboard ward criticality and the full 1,594-device inventory are loaded. Raw water-level/discharge series are not imported here."
    : "Earlier-dashboard analytics are unavailable right now, so the dashboard is using local coverage and reading-load lenses.";
}

function activeWardPropsForDetail() {
  if (selectedWardNo == null || !wards) return null;
  return wards.features.find(f => normalizeWardNo(f.properties.ward_no) === normalizeWardNo(selectedWardNo))?.properties || null;
}

function updateMetrics() {
  _guardLens();
  clearKpiHighlightState();
  const number = v => v == null ? "—" : Number(v).toLocaleString("en-IN");
  const usableSet = new Set(["GOOD","USABLE_WITH_CAUTION"]);
  const reporting = sensors.filter(s => {
    if (!s) return false;
    const qc = sensorQcByUid.get(String(s.uid));
    return qc ? usableSet.has(qc.qc_status) : !!s.has_data;
  }).length;
  document.getElementById("metric-total-sensors").textContent = number(reporting);
  const wardsWithData = wards?.features?.filter(f => (f.properties.sensor_with_data || 0) > 0).length || 0;
  document.getElementById("metric-wards").textContent = `${number(wardsWithData)}/${number(wards?.features?.length || manifest?.wards || 0)}`;
  updateLensCounts();
}

function updateLensCounts() {
  let critical = 0, rise = 0, stable = 0;
  if (wards?.features) {
    for (const f of wards.features) {
      const key = wardStatusKey(f.properties);
      if (key === "critical") critical++;
      else if (key === "rise") rise++;
      else if (key === "stable") stable++;
    }
  }
  const num = v => Number(v).toLocaleString("en-IN");
  const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = num(val); };
  set("metric-critical", critical);
  set("metric-rise", rise);
  set("metric-stable", stable);
  const lbl = document.getElementById("metric-critical-label");
  if (lbl && typeof analysisLensLabel === "function") {
    lbl.textContent = `Deepening · ${analysisLensLabel()}`;
  }
  const stableLbl = document.getElementById("metric-stable-label");
  if (stableLbl) stableLbl.textContent = stableStatusLabel();
  const stableChip = document.getElementById("ward-stable-chip");
  if (stableChip) stableChip.textContent = stableStatusLabel();
}

function wardFeaturesForKpi(kind) {
  if (!wards?.features) return [];
  if (kind === "total") {
    return wards.features.filter(f => (f.properties.sensor_total || 0) > 0 || (sensorsByWard[f.properties.ward_no] || []).length > 0);
  }
  if (kind === "covered") {
    return wards.features.filter(f => (f.properties.sensor_with_data || 0) > 0);
  }
  return wards.features.filter(f => wardStatusKey(f.properties) === kind);
}

function fitHighlightedWards(features) {
  if (!features.length || !map || !wardLayer) return;
  const wanted = new Set(features.map(f => normalizeWardNo(f.properties.ward_no)));
  const bounds = L.latLngBounds([]);
  wardLayer.eachLayer(layer => {
    const wardNo = normalizeWardNo(layer.feature?.properties?.ward_no);
    if (wanted.has(wardNo) && typeof layer.getBounds === "function") {
      bounds.extend(layer.getBounds());
    }
  });
  if (bounds.isValid()) map.fitBounds(bounds, { padding: [44, 44], maxZoom: 13 });
}

function applyKpiHighlight(kind) {
  if (!wards?.features) return;
  const features = wardFeaturesForKpi(kind);
  highlightedWardNos = new Set(features.map(f => normalizeWardNo(f.properties.ward_no)));
  highlightStyleMode = "";
  selectedWardNo = null;
  selectedSensorUid = null;
  const labels = {
    total: "Highlighted: wards with sensors",
    covered: "Highlighted: wards covered by reporting sensors",
    critical: `Highlighted: ${analysisCriticalLabel()}`,
    rise: "Highlighted: rising wards",
    stable: `Highlighted: ${stableStatusLabel()} wards`
  };
  quickViewLabel = labels[kind] || "Highlighted wards";
  document.querySelectorAll("[data-kpi-highlight]").forEach(card => {
    card.classList.toggle("active", card.dataset.kpiHighlight === kind);
  });
  closeAllOverlays();
  if (wardLayer) wardLayer.setStyle(defaultWardStyle);
  renderSensors();
  buildLegend();
  fitHighlightedWards(features);
}

function updateCommonQueryNote(count = null, threshold = commonLensThreshold()) {
  const note = document.getElementById("common-query-note");
  if (!note) return;
  const suffix = count == null ? "" : ` ${count.toLocaleString("en-IN")} wards match.`;
  note.textContent = `Highlights wards active in at least ${threshold} of 5 component lenses.${suffix}`;
}

function applyQueryResult(features, label) {
  highlightedWardNos = new Set(features.map(f => normalizeWardNo(f.properties.ward_no)));
  highlightStyleMode = "query";
  selectedWardNo = null;
  selectedSensorUid = null;
  wardStatusFilter = "";
  quickViewLabel = label;
  clearKpiHighlightState();
  document.querySelectorAll("[data-ward-status]").forEach(c => c.classList.toggle("active", c.dataset.wardStatus === ""));
  closeAllOverlays();
  if (wardLayer) wardLayer.setStyle(defaultWardStyle);
  renderSensors();
  buildLegend();
  updateCommonQueryNote(features.length, commonLensThreshold());
  fitHighlightedWards(features);
}

function applyCommonQuery() {
  if (!wards?.features) return;
  const threshold = commonLensThreshold();
  const input = document.getElementById("common-threshold");
  if (input) input.value = String(threshold);
  const features = wards.features.filter(f => commonLensHitCount(f.properties.ward_no) >= threshold);
  applyQueryResult(features, `Highlighted: Common query >= ${threshold}/5 lenses`);
  updateCommonQueryNote(features.length, threshold);
}

function queryPresetFeatures(preset) {
  if (!wards?.features) return [];
  return wards.features.filter(f => {
    const wardNo = f.properties.ward_no;
    if (preset === "groundwater_critical") return groundwaterWardStatusKey(wardNo) === "critical";
    if (preset === "previous_consumption") return isPreviousConsumptionCriticalWard(wardNo);
    if (preset === "volumetric_deficit") return wardVolumetricDeficit(wardNo).deficitMl >= 10;
    const pumping = pumpingWardSummaryForNo(wardNo);
    if (preset === "extraction") return Boolean(pumping?.criticalByExtraction);
    if (preset === "pumping_stress") return Boolean(pumping?.highNormalizedDrawdown);
    if (preset === "specific_capacity") return Boolean(pumping?.criticalBySpecificCapacity);
    if (preset === "current_stress_critical") return currentStressByNo.get(normalizeWardNo(wardNo))?.stressCategory?.startsWith("Critical");
    if (preset === "current_stress_elevated") return currentStressByNo.get(normalizeWardNo(wardNo))?.stressCategory?.startsWith("Elevated");
    return false;
  });
}

function queryPresetLabel(preset) {
  return ({
    groundwater_critical: "Query result: Groundwater critical",
    previous_consumption: "Query result: Previous consumption critical",
    volumetric_deficit: "Query result: High volumetric deficit",
    extraction: "Query result: High extraction",
    pumping_stress: "Query result: Volume-normalized drawdown",
    specific_capacity: "Query result: Low specific capacity",
    current_stress_critical: "Query result: Current stress critical",
    current_stress_elevated: "Query result: Current stress elevated",
  })[preset] || "Query result";
}

function applyPresetQuery(preset) {
  const features = queryPresetFeatures(preset);
  applyQueryResult(features, queryPresetLabel(preset));
}

function clearKpiHighlightState() {
  document.querySelectorAll("[data-kpi-highlight]").forEach(card => card.classList.remove("active"));
}

function wireKpiCards() {
  document.querySelectorAll("[data-kpi-highlight]").forEach(card => {
    const run = () => applyKpiHighlight(card.dataset.kpiHighlight);
    card.addEventListener("click", run);
    card.addEventListener("keydown", e => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        run();
      }
    });
  });
}
