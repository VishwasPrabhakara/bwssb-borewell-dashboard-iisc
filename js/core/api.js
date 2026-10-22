// core/api.js
// Data-access layer for the BWSSB/IISc dashboard.
// The ONE place that knows where data comes from (bundled ./data/*.json in
// "static" mode, or the Cloudflare Worker + D1 API in "api" mode) and how to
// fetch it resiliently. UI/logic modules call these helpers and never fetch directly.
// Loaded as an ordered classic <script>; shares global scope. See index.html.

const CONFIG = window.DASHBOARD_CONFIG || { source: "static", apiBase: "" };

// Base URL for the earlier "analytics" Worker API. Overridable via config so the
// endpoint is not hard-wired in code (env/config separation).
const ANALYTICS_API_BASE =
  (window.DASHBOARD_CONFIG && window.DASHBOARD_CONFIG.analyticsApiBase) ||
  "https://bbmp-borewell-api.vishwas-borewellworkersdev.workers.dev";

function urlOf(name) {
  if (CONFIG.source === "api" && CONFIG.apiBase) {
    const route = { "wards.geojson": "/api/wards.geojson", "sensors.json": "/api/sensors", "manifest.json": "/api/manifest" };
    return CONFIG.apiBase.replace(/\/$/, "") + route[name];
  }
  return `./data/${name}`;
}

function seriesUrlOf(uid) {
  return CONFIG.source === "api" && CONFIG.apiBase
    ? CONFIG.apiBase.replace(/\/$/, "") + "/api/sensor/" + uid + "/series"
    : `./data/sensor_series/${uid}.json`;
}

// Resilient JSON GET. Returns `fallback` on any failure (network, non-2xx, parse)
// and reports through onError(label) so callers can decide whether it's fatal.
async function getJson(url, { fallback = null, label = url, onError = null } = {}) {
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (err) {
    console.warn("[dashboard] fetch failed:", label, err && err.message);
    if (typeof onError === "function") onError(label, err);
    return fallback;
  }
}

// Load the analytics bundle, preferring local static snapshots and falling back
// to the Worker API. Never throws; missing pieces come back as empty.
async function loadAnalyticsData() {
  const apiGet = (path, fallback) => getJson(`${ANALYTICS_API_BASE}${path}`, { fallback, label: `analytics API ${path}` });
  const localGet = (path, fallback) => getJson(path, { fallback, label: `local ${path}` });

  const [sensorPayload, criticalGroundwater, pumpingPerformance] = await Promise.all([
    apiGet("/api/sensors?source=kh", { sensors: [] }),
    apiGet("/api/critical-wards-groundwater", { wards: [] }),
    apiGet("/api/pumping-performance/wards?cache_v=pump-kh-cycles-20260831-1", { wards: [], thresholds: {} }),
  ]);
  const localCriticalGroundwater = await localGet(`./data/critical_groundwater_ward_summary.json?ts=${Date.now()}`, { wards: [] });
  const hasActiveGroundwaterClasses = (criticalGroundwater?.wards || []).some(item => (
    isYes(item.dashboardAction)
    || item.groundwaterStatus === "Critical"
    || item.dashboardMapCategory === "Critical: Ward-average groundwater decline"
    || item.dashboardMapCategory === "Confirmed groundwater rise"
    || item.dashboardMapCategory === "Possible groundwater rise"
    || item.dashboardMapCategory === "Stable groundwater trend"
  ));
  const hasLocalGroundwater = (localCriticalGroundwater?.wards || []).length > 0;
  const activeCriticalGroundwater = hasLocalGroundwater ? localCriticalGroundwater : (hasActiveGroundwaterClasses ? criticalGroundwater : localCriticalGroundwater);
  const localPumpingPerformance = await localGet(`./data/pumping_performance_ward_summary.json?ts=${Date.now()}`, { wards: [], thresholds: {} });
  const volumetricDeficit = await localGet("./data/ward_volumetric_deficit_summary.json", { wards: [] });
  const currentStress = await localGet(`./data/current_stress_ward_summary.json?ts=${Date.now()}`, { wards: [] });
  return { fullSensors: sensorPayload.sensors || [], criticalGroundwater: activeCriticalGroundwater, pumpingPerformance: localPumpingPerformance, volumetricDeficit, currentStress };
}

async function loadCriticalityForMethod(method) {
  const path = method === "legacy" ? "./data/critical_groundwater_ward_summary_legacy.json" : "./data/critical_groundwater_ward_summary.json";
  return getJson(`${path}?ts=${Date.now()}`, { fallback: { wards: [] }, label: `criticality (${method})` });
}
