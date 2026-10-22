// core/data.js
// Part of the BWSSB/IISc borewell dashboard front-end.
// Split from the former single app.js; loaded as an ordered classic <script>,
// sharing one global scope with its sibling files. Order matters: see index.html.


// ---------- Boot ----------
async function boot() {
  initMap();
  buildLegend();
  wireToolbar();
  wireSearch();
  wireFilters();
  wireKpiCards();
  wireDetailClose();
  try {
    await loadData();
    updateMetrics();
    renderWards();
    renderSensors();
    document.getElementById("about-manifest").textContent =
      `Snapshot: ${manifest.kh_zip}. ${manifest.sensor_with_data} sensors reporting between ${fmtDate(manifest.period_start)} and ${fmtDate(manifest.period_end)}, across ${manifest.wards_with_data} wards.`;
  } catch (err) {
    console.error("Dashboard data load failed:", err);
    const msg = "Could not load the dashboard data (" + (err && err.message ? err.message : "unknown error") + "). The map may be empty — check your connection or data files.";
    const about = document.getElementById("about-manifest");
    if (about) about.textContent = msg;
    notifyError(msg);
    try { openOverlay("about-overlay"); } catch (_) { /* overlays not wired */ }
  }
}
async function loadData() {
  console.info("[dashboard] fetching", { wards: urlOf("wards.geojson"), sensors: urlOf("sensors.json") });
  // Core layers (wards, sensors, manifest) are required: if any is missing the
  // dashboard cannot render, so we surface it and let boot()'s catch handle it.
  // Everything else is optional and degrades gracefully to null/empty.
  const [ws, ss, mf, qs, analytics, qcPayload] = await Promise.all([
    getJson(urlOf("wards.geojson"), { label: "wards.geojson" }),
    getJson(urlOf("sensors.json"), { label: "sensors.json" }),
    getJson(urlOf("manifest.json"), { label: "manifest.json" }),
    getJson("./data/session_quality_summary.json", { fallback: null, label: "session quality" }),
    loadAnalyticsData(),
    getJson("./data/sensor_qc.json", { fallback: null, label: "sensor QC" }),
  ]);
  if (!ws || !ss || !mf) {
    throw new Error("Core data missing: " + [["wards", ws], ["sensors", ss], ["manifest", mf]].filter(([, v]) => !v).map(([n]) => n).join(", "));
  }
  if (qcPayload && Array.isArray(qcPayload.sensors)) {
    for (const row of qcPayload.sensors) sensorQcByUid.set(String(row.uid), row);
  }
  wards = ws;
  sensors = mergeSensorInventory(ss, analytics.fullSensors || []);
  manifest = mf;
  sessionSummary = qs;
  currentStressByNo = new Map((analytics.currentStress?.wards || [])
    .map(w => [normalizeWardNo(w.wardNo), w])
    .filter(([k]) => k != null));
  criticalGroundwaterByNo = new Map((analytics.criticalGroundwater?.wards || [])
    .map(calculateGroundwaterCriticality)
    .map(item => [normalizeWardNo(item.wardNo), item])
    .filter(([k]) => k != null));
  pumpingPerformanceWardSummaryByNo = new Map((analytics.pumpingPerformance?.wards || []).map(item => [normalizeWardNo(item.wardNo), item]).filter(([k]) => k != null));
  pumpingPerformanceWardThresholds = analytics.pumpingPerformance?.thresholds || {};
  volumetricDeficitByNo = new Map((analytics.volumetricDeficit?.wards || []).map(item => [normalizeWardNo(item.wardNo), item]).filter(([k]) => k != null));
  analyticsLoaded = criticalGroundwaterByNo.size > 0;
  if (analyticsLoaded) currentLens = "groundwater";
  console.info("[dashboard] loaded", { wards: wards.features.length, sensors: sensors.length, sensorsWithData: sensors.filter(s => s.has_data).length, manifest });
  for (const s of sensors) {
    sensorsByUid[s.uid] = s;
    if (s.ward_no != null) (sensorsByWard[s.ward_no] = sensorsByWard[s.ward_no] || []).push(s);
  }
  recomputeWardSensorCounts();
  syncAnalyticsControls();
}

// loadAnalyticsData() and loadCriticalityForMethod() now live in js/core/api.js
// (the data-access layer), alongside urlOf/seriesUrlOf/getJson.

function normalizeApiSensor(s) {
  return {
    uid: String(s.uid || ""),
    lat: s.lat,
    lng: s.lng,
    ward_no: normalizeWardNo(s.ward_no ?? s.wardNo),
    ward_name: s.ward_name ?? s.wardName ?? null,
    motor_hp: s.motor_hp ?? s.motorHp ?? null,
    borewell_depth: s.borewell_depth ?? s.borewellDepth ?? null,
    pump_name: s.pump_name ?? s.pumpName ?? "",
    has_data: Boolean(s.has_data ?? s.hasData),
    data_category: s.data_category ?? s.dataCategory ?? (s.has_data || s.hasData ? "both" : "none"),
    reading_count: Number(s.reading_count ?? s.totalReadings ?? s.waterReadings ?? 0),
    first_data_at: s.first_data_at ?? s.firstDataAt ?? null,
    last_data_at: s.last_data_at ?? s.lastDataAt ?? null,
  };
}

function mergeSensorInventory(localSensors, apiSensors) {
  const merged = new Map();
  for (const s of apiSensors.map(normalizeApiSensor)) {
    if (!s.uid) continue;
    merged.set(s.uid, s);
  }
  for (const local of localSensors.map(normalizeApiSensor)) {
    const existing = merged.get(local.uid) || {};
    merged.set(local.uid, {
      ...existing,
      ...local,
      has_data: local.has_data || existing.has_data || false,
      data_category: local.data_category || existing.data_category || "none",
      reading_count: local.reading_count || existing.reading_count || 0,
      first_data_at: local.first_data_at || existing.first_data_at || null,
      last_data_at: local.last_data_at || existing.last_data_at || null,
    });
  }
  const out = [...merged.values()].filter(s => s.uid);
  assignMissingSensorWards(out);
  return out;
}

function assignMissingSensorWards(items) {
  if (!wards?.features?.length) return;
  const features = wards.features.map(f => ({ feature: f, bounds: featureBounds(f) }));
  for (const s of items) {
    if (s.ward_no != null || s.lat == null || s.lng == null) continue;
    const hit = features.find(({ feature, bounds }) => pointInBounds(s.lng, s.lat, bounds) && pointInFeature(s.lng, s.lat, feature));
    if (hit) {
      s.ward_no = normalizeWardNo(hit.feature.properties.ward_no);
      s.ward_name = hit.feature.properties.ward_name;
    }
  }
}

function featureBounds(feature) {
  const coords = [];
  const walk = arr => Array.isArray(arr?.[0]) && typeof arr[0][0] === "number" ? coords.push(...arr) : arr.forEach(walk);
  walk(feature.geometry.coordinates);
  return coords.reduce((b, [lng, lat]) => ({
    minLng: Math.min(b.minLng, lng), maxLng: Math.max(b.maxLng, lng),
    minLat: Math.min(b.minLat, lat), maxLat: Math.max(b.maxLat, lat),
  }), { minLng: Infinity, maxLng: -Infinity, minLat: Infinity, maxLat: -Infinity });
}

function pointInBounds(lng, lat, b) {
  return lng >= b.minLng && lng <= b.maxLng && lat >= b.minLat && lat <= b.maxLat;
}

function pointInRing(lng, lat, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i][0], yi = ring[i][1], xj = ring[j][0], yj = ring[j][1];
    const intersect = ((yi > lat) !== (yj > lat)) && (lng < (xj - xi) * (lat - yi) / (yj - yi) + xi);
    if (intersect) inside = !inside;
  }
  return inside;
}

function pointInFeature(lng, lat, feature) {
  const polys = feature.geometry.type === "MultiPolygon" ? feature.geometry.coordinates : [feature.geometry.coordinates];
  return polys.some(poly => pointInRing(lng, lat, poly[0]) && !poly.slice(1).some(hole => pointInRing(lng, lat, hole)));
}

