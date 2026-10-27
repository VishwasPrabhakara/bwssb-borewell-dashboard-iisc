// ui/controls.js
// Part of the BWSSB/IISc borewell dashboard front-end.
// Split from the former single app.js; loaded as an ordered classic <script>,
// sharing one global scope with its sibling files. Order matters: see index.html.

// ---------- Toolbar / overlays ----------
function wireToolbar() {
  const clearChip = document.getElementById("clear-filter-chip");
  if (clearChip) clearChip.addEventListener("click", () => clearWardSelection());
  document.getElementById("btn-search").addEventListener("click", () => openOverlay("search-overlay", () => document.getElementById("search-input").focus()));
  // Left control rail: collapse / re-open (replaces the old filter overlay button)
  const railApp = document.getElementById("app");
  const railOpen = document.getElementById("rail-open");
  const railToggle = document.getElementById("rail-toggle");
  const setRail = (collapsed) => {
    if (railApp) railApp.classList.toggle("rail-collapsed", collapsed);
    if (railOpen) railOpen.hidden = !collapsed;
    if (railToggle) railToggle.setAttribute("aria-expanded", String(!collapsed));
    setTimeout(() => { if (typeof map !== "undefined" && map) map.invalidateSize(); }, 220);
  };
  if (railToggle) railToggle.addEventListener("click", () => setRail(true));
  if (railOpen) railOpen.addEventListener("click", () => setRail(false));
  // Map-first on phones: start with the control rail collapsed.
  if (window.matchMedia && window.matchMedia("(max-width: 720px)").matches) setRail(true);
  document.getElementById("btn-info").addEventListener("click", () => openOverlay("about-overlay"));
  document.addEventListener("keydown", e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      openOverlay("search-overlay", () => document.getElementById("search-input").focus());
    }
    if (e.key === "Escape") {
      closeAllOverlays();
      if (selectedWardNo != null || selectedSensorUid != null) clearWardSelection();
    }
  });
  // Close on background click
  document.querySelectorAll(".overlay").forEach(ov => {
    ov.addEventListener("click", e => { if (e.target === ov) ov.hidden = true; });
  });
  document.querySelectorAll("[data-close]").forEach(btn => {
    btn.addEventListener("click", () => { document.getElementById(btn.dataset.close).hidden = true; });
  });
}

function openOverlay(id, cb) {
  closeAllOverlays();
  const el = document.getElementById(id);
  el.hidden = false;
  if (cb) setTimeout(cb, 0);
}
function closeAllOverlays() {
  document.querySelectorAll(".overlay").forEach(o => o.hidden = true);
}

function wireDetailClose() {
  document.querySelector("#detail .close-btn").addEventListener("click", () => clearWardSelection());
}

// ---------- Search ----------
function wireSearch() {
  const input = document.getElementById("search-input");
  const results = document.getElementById("search-results");
  const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
  input.addEventListener("input", debounce(() => runSearch(input.value.trim(), results), 120));
  runSearch("", results); // initial: show top wards
}

function runSearch(q, container) {
  container.innerHTML = "";
  if (!wards || !sensors) { container.innerHTML = '<div class="loading">Data still loading, please wait...</div>'; return; }
  const query = q.toLowerCase();
  const items = [];

  // Statistical shortcuts
  if (!query || "max sensors top most".includes(query.split(" ")[0])) {
    if (query.startsWith("max") || query.startsWith("top") || !query) {
      const sorted = [...wards.features].sort((a, b) => (b.properties.sensor_with_data || 0) - (a.properties.sensor_with_data || 0)).slice(0, 8);
      sorted.forEach(f => items.push({ kind: "ward-quick", label: `Ward ${f.properties.ward_no} · ${f.properties.ward_name}`, sub: `${f.properties.sensor_with_data} sensors`, feat: f, badge: "top" }));
    }
  }
  if (query.startsWith("min") || query.startsWith("few") || query === "no sensors") {
    const wSorted = wards.features
      .filter(f => (f.properties.sensor_with_data || 0) > 0)
      .sort((a, b) => (a.properties.sensor_with_data || 0) - (b.properties.sensor_with_data || 0))
      .slice(0, 8);
    wSorted.forEach(f => items.push({ kind: "ward-quick", label: `Ward ${f.properties.ward_no} · ${f.properties.ward_name}`, sub: `${f.properties.sensor_with_data} sensors`, feat: f, badge: "min" }));
  }

  // Regular ward + UID matching
  if (query.length >= 1) {
    for (const f of wards.features) {
      const p = f.properties;
      const name = String(p.ward_name || "").toLowerCase();
      const no = String(p.ward_no || "");
      if (name.includes(query) || no === query || no.startsWith(query)) {
        items.push({ kind: "ward", label: `Ward ${p.ward_no} · ${p.ward_name}`, sub: `${p.sensor_with_data}/${p.sensor_total} sensors`, feat: f });
      }
      if (items.length > 40) break;
    }
    for (const s of sensors) {
      if (s.uid.toLowerCase().includes(query)) {
        items.push({ kind: "sensor", label: s.uid, sub: s.ward_name ? `Ward ${s.ward_no} · ${s.ward_name}` : "Unassigned", sensor: s });
      }
      if (items.length > 60) break;
    }
  }

  if (items.length === 0) {
    container.innerHTML = `<div class="loading">No matches. Try a ward name, ward number, or a UID.</div>`;
    return;
  }
  const frag = document.createDocumentFragment();
  items.slice(0, 40).forEach(it => {
    const row = document.createElement("div");
    row.className = "search-item";
    row.innerHTML = `<div><div class="primary">${it.label}</div><div class="secondary">${it.sub}</div></div>${it.badge ? `<span class="badge">${it.badge}</span>` : ""}`;
    row.onclick = () => {
      closeAllOverlays();
      if (it.kind === "sensor") openSensorDetail(it.sensor.uid);
      else openWardDetail(it.feat.properties, it.feat);
    };
    frag.appendChild(row);
  });
  container.appendChild(frag);
}

// ---------- Filters ----------
function wireFilters() {
  const lensSelect = document.getElementById("analysis-lens");
  if (lensSelect) {
    lensSelect.addEventListener("change", () => {
      Store.set("currentLens", lensSelect.value || "groundwater");
      syncAnalyticsControls();
      if (wardLayer) wardLayer.setStyle(defaultWardStyle);
      updateMetrics();
      buildLegend();
    });
  }
  const methodSelect = document.getElementById("groundwater-method");
  if (methodSelect) {
    methodSelect.addEventListener("change", () => {
      Store.set("groundwaterMethodMode", methodSelect.value || "dashboard");
      if (wardLayer) wardLayer.setStyle(defaultWardStyle);
      updateMetrics();
      buildLegend();
      if (!document.getElementById("detail").hidden && activeWardPropsForDetail()) {
        openWardDetail(activeWardPropsForDetail());
      }
    });
  }
  const commonApply = document.getElementById("common-query-apply");
  const commonInput = document.getElementById("common-threshold");
  if (commonApply) commonApply.addEventListener("click", applyCommonQuery);
  if (commonInput) {
    commonInput.addEventListener("change", () => updateCommonQueryNote(null, commonLensThreshold()));
    commonInput.addEventListener("keydown", e => {
      if (e.key === "Enter") {
        e.preventDefault();
        applyCommonQuery();
      }
    });
  }
  document.querySelectorAll("[data-query-preset]").forEach(chip => {
    chip.addEventListener("click", () => applyPresetQuery(chip.dataset.queryPreset));
  });
  document.querySelectorAll("[data-ward-status]").forEach(chip => {
    chip.addEventListener("click", () => {
      Store.set("wardStatusFilter", chip.dataset.wardStatus);
      document.querySelectorAll("[data-ward-status]").forEach(c => c.classList.toggle("active", c === chip));
      if (wardLayer) wardLayer.setStyle(defaultWardStyle);
      renderSensors();
      buildLegend();
    });
  });
  document.querySelectorAll("[data-sensor-status]").forEach(chip => {
    chip.addEventListener("click", () => {
      Store.set("sensorStatusFilter", chip.dataset.sensorStatus);
      document.querySelectorAll("[data-sensor-status]").forEach(c => c.classList.toggle("active", c === chip));
      renderSensors();
      buildLegend();
    });
  });
  const redrawSensors = () => { renderSensors(); buildLegend(); };
  document.getElementById("trend-mode")?.addEventListener("change", e => { Store.set("trendMode", e.target.value); redrawSensors(); });
  const legendEl = document.getElementById("legend-items");
  const legendToggle = el => {
    const f = el.closest("[data-lf-group]"); if (!f) return;
    const set = legendFilter[f.dataset.lfGroup], k = f.dataset.lfKey;
    set.has(k) ? set.delete(k) : set.add(k);
    redrawSensors();
  };
  legendEl?.addEventListener("click", e => legendToggle(e.target));
  legendEl?.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); legendToggle(e.target); } });
  const tvToggle = document.getElementById("tv-mode-toggle");
  if (tvToggle) {
    tvToggle.addEventListener("change", () => {
      document.body.classList.toggle("tv-mode", tvToggle.checked);
      setTimeout(() => map.invalidateSize(), 120);
    });
  }
  document.querySelectorAll("[data-quick]").forEach(chip => {
    chip.addEventListener("click", () => {
      const which = chip.dataset.quick;
      clearKpiHighlightState();
      highlightStyleMode = "";
      closeAllOverlays();
      if (which === "reset") {
        Store.set("selectedWardNo", null);
        Store.set("selectedSensorUid", null);
        highlightedWardNos = new Set();
        Store.set("highlightStyleMode", "");
        Store.set("quickViewLabel", "");
        Store.set("wardStatusFilter", "");
        Store.set("sensorStatusFilter", "with_data");
        legendFilter.depth.clear(); legendFilter.trend.clear();
        Store.set("currentLens", analyticsLoaded ? "groundwater" : "coverage");
        document.querySelectorAll("[data-ward-status]").forEach(c => c.classList.toggle("active", c.dataset.wardStatus === ""));
        document.querySelectorAll("[data-sensor-status]").forEach(c => c.classList.toggle("active", c.dataset.sensorStatus === "with_data"));
        syncAnalyticsControls();
        updateCommonQueryNote();
        if (wardLayer) wardLayer.setStyle(defaultWardStyle);
        renderSensors();
        buildLegend();
        map.setView([12.972, 77.594], 11);
        return;
      }
      if (which === "all_wards") {
        highlightedWardNos = new Set(wards.features.map(f => normalizeWardNo(f.properties.ward_no)));
        highlightStyleMode = "quick";
        quickViewLabel = "All wards highlighted";
        if (wardLayer) wardLayer.setStyle(defaultWardStyle);
        buildLegend();
        map.fitBounds(wardLayer.getBounds(), { padding: [40, 40] });
        return;
      }
      const filtered = wards.features
        .filter(f => which === "no_sensors" ? (f.properties.sensor_with_data || 0) === 0 : (f.properties.sensor_with_data || 0) > 0)
        .sort((a, b) => which === "max_sensors" ? (b.properties.sensor_with_data || 0) - (a.properties.sensor_with_data || 0) : (a.properties.sensor_with_data || 0) - (b.properties.sensor_with_data || 0));
      const top = filtered.slice(0, which === "no_sensors" ? filtered.length : 10);
      highlightedWardNos = new Set(top.map(f => normalizeWardNo(f.properties.ward_no)));
      highlightStyleMode = "quick";
      quickViewLabel = which === "max_sensors"
        ? "Highlighted: top 10 wards by sensor count"
        : which === "min_sensors"
        ? "Highlighted: 10 wards with fewest sensors"
        : "Highlighted: wards with no sensors";
      if (wardLayer) wardLayer.setStyle(defaultWardStyle);
      buildLegend();
      if (top.length && top[0].properties.centroid) {
        const bounds = L.latLngBounds(top.map(f => [f.properties.centroid[1], f.properties.centroid[0]]));
        map.fitBounds(bounds, { padding: [40, 40] });
      }
    });
  });
}

