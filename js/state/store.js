// state/store.js
// Single source of truth for the dashboard's mutable state.
// Loaded FIRST (before every other dashboard module) as an ordered classic
// <script> sharing one global scope, so these globals exist before any module
// that reads them. Reads stay as the existing bare globals (e.g. `currentLens`);
// WRITES to interaction state should go through Store.set(key, value), which
// performs the assignment in one place and notifies subscribers. This gives a
// single observable choke-point for user-driven state changes without a
// codebase-wide rename. See docs/ARCHITECTURE.md.

/* ---- Loaded data (set once by core/data.js after fetch; not user-mutated) ---- */
let sensors = [];              // full sensor list
let wards = null;              // ward polygons (GeoJSON)
let manifest = null;           // snapshot metadata
let sessionSummary = null;     // session-quality summary
let analyticsLoaded = false;   // true once groundwater analytics are present
let sensorQcByUid = new Map();
let criticalGroundwaterByNo = new Map();
let currentStressByNo = new Map();
let pumpingPerformanceWardSummaryByNo = new Map();
let pumpingPerformanceWardThresholds = {};
let volumetricDeficitByNo = new Map();
let waterLevelClassByNo = new Map();
let waterLevelClassSensors = [];
let sensorDepthBandByUid = new Map();
let sensorTrendByUid = {};      // uid -> {raw, cleaned, static, dow:{0..6}} from sensor_trends.json
let sensorsByUid = {};
let sensorsByWard = {};         // ward_no -> [sensor]

/* ---- Map / layer handles (set by the UI layer) ---- */
let currentSensorMarkers = null;
let wardLayer = null;
let map;

/* ---- Interaction state (user-driven; prefer Store.set to mutate) ---- */
let currentShading = "with_data";
let currentLens = "coverage";         // active analysis lens
let criticalityMethod = "modern";     // "modern" | "legacy"
let sensorStatusFilter = "with_data"; // "with_data" | "no_data" | "all"
let groundwaterMethodMode = "linear_mk_mod";
let selectedWardNo = null;
let selectedSensorUid = null;
let highlightedWardNos = new Set();
let quickViewLabel = "";
let highlightStyleMode = "";
let currentRange = "1M";              // chart range
let trendMode = "static";             // which chart filter's trend colours the borewells
const legendFilter = { ward: new Set(), depth: new Set(), trend: new Set() }; // clicked legend classes; groups AND, classes OR

/* ---- Chart handles / caches ---- */
let charts = { water: null, discharge: null, modal: null };
let currentSensorSeries = null;

/* ---- Store: observable writes for interaction state ---- */
const Store = (() => {
  const subscribers = [];
  function emit(key, value) {
    for (const fn of subscribers) { try { fn(key, value); } catch (e) { console.warn("[store] subscriber error", e); } }
  }
  // Map a state key to its assignment. Reads elsewhere still use the bare global.
  function set(key, value) {
    switch (key) {
      case "currentShading": currentShading = value; break;
      case "currentLens": currentLens = value; break;
      case "criticalityMethod": criticalityMethod = value; break;
      case "sensorStatusFilter": sensorStatusFilter = value; break;
      case "groundwaterMethodMode": groundwaterMethodMode = value; break;
      case "selectedWardNo": selectedWardNo = value; break;
      case "selectedSensorUid": selectedSensorUid = value; break;
      case "quickViewLabel": quickViewLabel = value; break;
      case "highlightStyleMode": highlightStyleMode = value; break;
      case "currentRange": currentRange = value; break;
      case "trendMode": trendMode = value; break;
      default: console.warn("[store] unknown key:", key); return value;
    }
    emit(key, value);
    return value;
  }
  function get(key) {
    const bag = { currentShading, currentLens, criticalityMethod,
      sensorStatusFilter, groundwaterMethodMode, selectedWardNo, selectedSensorUid,
      quickViewLabel, highlightStyleMode, currentRange };
    return bag[key];
  }
  function subscribe(fn) { subscribers.push(fn); return () => { const i = subscribers.indexOf(fn); if (i >= 0) subscribers.splice(i, 1); }; }
  return { set, get, subscribe };
})();
