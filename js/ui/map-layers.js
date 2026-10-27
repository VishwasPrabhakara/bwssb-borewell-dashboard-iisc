// ui/map-layers.js
// Part of the BWSSB/IISc borewell dashboard front-end.
// Split from the former single app.js; loaded as an ordered classic <script>,
// sharing one global scope with its sibling files. Order matters: see index.html.


// ---------- Map ----------
function initMap() {
  // (map click handler wired after map is created)
  map = L.map("map", { zoomControl: false, minZoom: 10, maxZoom: 18 }).setView([12.972, 77.594], 11);
  L.control.zoom({ position: "bottomleft" }).addTo(map);
  // Carto Positron - clean, minimal international-style basemap
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 19,
  }).addTo(map);
  map.on("click", (e) => {
    if (selectedWardNo != null && !e.originalEvent.target.closest(".leaflet-interactive")) {
      clearWardSelection();
    }
  });
}

// ---------- Wards ----------
function renderWards() {
  if (wardLayer) wardLayer.remove();

  wardLayer = L.geoJSON(wards, {
    style: feat => defaultWardStyle(feat),
    onEachFeature: (feat, layer) => {
      const p = feat.properties;
      const cs = currentStressByNo.get(String(p.ward_no));
      let fiveNumRow = "";
      let confidenceBadge = "";
      if (cs && cs.wells && cs.wells.length) {
        const levels = cs.wells.map(w => w.current_ft).filter(v => Number.isFinite(v)).sort((a,b)=>a-b);
        if (levels.length) {
          const q = (arr, p) => arr[Math.min(arr.length-1, Math.max(0, Math.round(p*(arr.length-1))))];
          const fmt = v => v.toFixed(0);
          fiveNumRow = `<div class="kv">Well depths (ft): min <b>${fmt(levels[0])}</b> · P25 <b>${fmt(q(levels,0.25))}</b> · med <b>${fmt(q(levels,0.5))}</b> · P75 <b>${fmt(q(levels,0.75))}</b> · max <b>${fmt(levels[levels.length-1])}</b></div>`;
        }
        const n = cs.wellCount || 0;
        const conf = n >= 5 ? "High" : n >= 3 ? "Medium" : "Low";
        const color = n >= 5 ? "#059669" : n >= 3 ? "#d97706" : "#b91c1c";
        confidenceBadge = `<div class="kv">Confidence: <b style="color:${color}">${conf}</b> (${n} well${n===1?"":"s"} with static data)</div>`;
      }
      const _cg = criticalGroundwaterByNo.get(normalizeWardNo(p.ward_no));
      let methodBadges = "";
      if (_cg) {
        const parts = [];
        if (_cg.insufficientWard) {
          parts.push('<span style="color:#64748b">⚑ sparse ward (&lt;3 good sensors)</span>');
        }
        if (_cg.linearSlopeFtPerWeek == null) {
          parts.push('<span style="color:#64748b">insufficient data for trend</span>');
        } else {
          if (_cg.mkConfirmed === true) {
            parts.push('<span style="color:#047857">✓ MK confirmed (p &lt; 0.05)</span>');
          } else if (_cg.mkConfirmed === false) {
            parts.push('<span style="color:#64748b">watchlist (MK not significant on 14-mo window)</span>');
          }
          if (_cg.slopeReliable === false) {
            parts.push('<span style="color:#b45309">⚠ trend magnitude implausible (short-window extrapolation)</span>');
          }
        }
        if (_cg.stepChangeDetected === "Yes" && _cg.stepChangeDate) {
          parts.push(`<span style="color:#b91c1c">● step change ${_cg.stepChangeDate} (Pettitt p=${(_cg.pettittP ?? 0).toFixed(3)})</span>`);
        }
        if (parts.length) methodBadges = `<div class="kv" style="font-size:11px;line-height:1.4">${parts.join("<br/>")}</div>`;
      }
      // Water level classification info
      let wlcRow = "";
      if (currentLens === "water_level_class") {
        const wlc = waterLevelClassByNo.get(normalizeWardNo(p.ward_no));
        if (wlc) {
          const bands = wlc.bandCounts || {};
          const parts = [];
          for (const [bk, bv] of Object.entries(bands)) {
            if (bv > 0) {
              const short = bk.startsWith("Very") ? "V.Deep" : bk.startsWith("Deep") ? "Deep" : bk.startsWith("Mod") ? "Moderate" : "Shallow";
              parts.push(short + ": " + bv);
            }
          }
          wlcRow = '<div class="kv">Dominant: <b>' + (wlc.dominantBand || '—') + '</b></div>'
                 + '<div class="kv">Sensors by band: ' + (parts.join(' · ') || '—') + '</div>'
                 + '<div class="kv">Median depth: <b>' + (wlc.medianDepthFt != null ? wlc.medianDepthFt.toFixed(0) + ' ft' : '—') + '</b></div>';
        }
      }
      const tip = `
        <div class="name">Ward ${p.ward_no} — ${p.ward_name}</div>
        <div class="kv">Sensors with data: <b>${p.sensor_with_data || 0}</b> / ${p.sensor_total || 0}</div>
        <div class="kv">Map status: <b>${wardStatusLabel(wardStatusKey(p))}</b></div>
        ${fiveNumRow}
        ${confidenceBadge}
        ${methodBadges}
        ${wlcRow}
        <div class="kv">Population (2026 proj): ${p.population_2026 ? Math.round(p.population_2026).toLocaleString("en-IN") : "—"}</div>
        <div class="kv">Area: ${p.area_km2 ? p.area_km2.toFixed(1) + " km²" : "—"}</div>
      `;
      layer.bindTooltip(tip, { className: "ward-tip", sticky: true, direction: "auto" });
      layer.on({
        mouseover: e => e.target.setStyle({ weight: 2.5, color: "#0b3d4c" }),
        mouseout: e => wardLayer.resetStyle(e.target),
        click: () => { setSelectedWard(p.ward_no, feat); openWardDetail(p, feat); },
      });
    },
  }).addTo(map);
  buildLegend();
}


function wardStatusLabel(key) {
  if (currentLens === "current_stress") {
    return ({ critical: "Critical current stress", rise: "Elevated current stress", stable: "Normal / recovered", none: "No current-stress data" })[key] || "All wards";
  }
  if (currentLens === "water_level_class") {
    return ({ critical: "Very Deep (>500 ft)", rise: "Deep (250–500 ft)", stable: "Shallow / Moderate (<250 ft)", none: "No classification data" })[key] || "All wards";
  }
  return ({ critical: "Critical", rise: "Rising / improving", stable: stableStatusLabel(), high: "High coverage", low: "Needs coverage", none: "No sensors" })[key] || "All wards";
}

// ---------- Sensors ----------
function renderSensors() {
  if (currentSensorMarkers) currentSensorMarkers.remove();
  currentSensorMarkers = L.layerGroup();
  const visible = filteredSensors();
  for (const s of visible) {
    const isSel = selectedSensorUid === s.uid;
    const status = sensorStatusKey(s);
    const depth = sensorDepthBandByUid.get(String(s.uid));
    const tr = sensorTrend(s);
    // Borewells are coloured by their Sen / Mann-Kendall (Hamed-Rao) trend class.
    const fill = TREND_COLORS[tr.c];
    const m = L.circleMarker([s.lat, s.lng], {
      radius: isSel ? 11 : 6,
      color: isSel ? "#f59e0b" : "#ffffff",
      weight: isSel ? 3 : 1.6,
      fillColor: fill,
      fillOpacity: 0.95,
    });
    const wardLabel = s.ward_no != null ? `Ward ${s.ward_no} — ${s.ward_name || ""}` : "Unassigned";
    const depthLine = depth ? `<br/>Water level: <b>${depth.latest_ft} ft</b> · ${DEPTH_BAND_LABELS[depth.band]}` : "";
    const trendLine = `<br/>Trend (${TREND_MODE_LABELS[trendMode]}): <b>${tr.c}</b>` + (tr.s != null ? ` · ${tr.s > 0 ? "+" : ""}${tr.s} ft/wk · p=${tr.p} · n=${tr.n}` : "");
    m.bindTooltip(`<b>${s.uid}</b><br/>${wardLabel}<br/>${sensorStatusLabel(status)}${s.last_data_at ? " · " + fmtDate(s.last_data_at) : ""}${depthLine}${trendLine}`, { className: "sensor-tip", direction: "top" });
    m.on("click", () => openSensorDetail(s.uid));
    currentSensorMarkers.addLayer(m);
    if (isSel) m.bringToFront();
  }
  currentSensorMarkers.addTo(map);
}

function sensorStatusLabel(key) {
  return ({ with_data: "Reporting", no_data: "No data" })[key] || "Sensor";
}

function sensorFilterMatch(s) {
  if (sensorStatusFilter === "all") return true;
  if (sensorStatusFilter === "with_data") return !!s.has_data;
  return sensorStatusKey(s) === sensorStatusFilter;
}

// Trend record {c, s, p, n} of a borewell for the chosen chart filter (dow uses the chart's weekday).
function sensorTrend(s) {
  const r = sensorTrendByUid[String(s.uid)];
  return (r && (trendMode === "dow" ? r.dow[String(dowWeekday)] : r[trendMode])) || { c: "none" };
}

function legendFilterMatch(s) {
  const d = sensorDepthBandByUid.get(String(s.uid))?.band;
  return (!legendFilter.depth.size || legendFilter.depth.has(d)) &&
         (!legendFilter.trend.size || legendFilter.trend.has(sensorTrend(s).c));
}

function filteredSensors() {
  return sensors.filter(s => {
    if (s.lat == null || s.lng == null) return false;
    if (!legendFilterMatch(s)) return false;
    if (selectedWardNo != null && s.ward_no !== selectedWardNo) return false;
    if (legendFilter.ward.size) {
      const wardProps = wards?.features?.find(f => f.properties.ward_no === s.ward_no)?.properties;
      if (!wardProps || !legendFilter.ward.has(wardStatusKey(wardProps))) return false;
    }
    return sensorFilterMatch(s);
  });
}

function visibleWardFeaturesForLegend() {
  if (!wards?.features) return [];
  return wards.features.filter(f => {
    const wardNo = normalizeWardNo(f.properties.ward_no);
    if (selectedWardNo != null && wardNo !== normalizeWardNo(selectedWardNo)) return false;
    if (highlightedWardNos.size > 0 && !highlightedWardNos.has(wardNo)) return false;
    return true;
  });
}

// ---------- Legend ----------
function buildLegend() {
  const el = document.getElementById("legend-scale");
  if (el) {
    el.style.display = isAnalysisLens(currentLens) ? "none" : "flex";
    el.innerHTML = CHORO.map(c => `<span style="background:${c}"></span>`).join("");
  }
  const items = document.getElementById("legend-items");
  if (items) {
    const visibleWards = visibleWardFeaturesForLegend();
    const wardItems = isAnalysisLens(currentLens) && analyticsLoaded
      ? [
          ["critical", CRITICALITY_COLORS.critical, analysisCriticalLabel()],
          ["rise", CRITICALITY_COLORS.rise, currentLens === "current_stress" ? "Elevated current stress" : currentLens === "water_level_class" ? "Deep (250–500 ft)" : "Groundwater Rise"],
          ["stable", CRITICALITY_COLORS.stable, stableStatusLabel()],
          ["none", BASE_WARD_COLOR.fill, "Other wards"],
        ]
          .filter(([key]) => key !== "rise" || ["groundwater", "current_stress", "water_level_class"].includes(currentLens))
          .filter(([key]) => key !== "stable" || !["overall", "consumption"].includes(currentLens))
          .filter(([key]) => visibleWards.some(f => wardStatusKey(f.properties) === key))
      : visibleWards.some(f => (currentLens === "readings" ? wardReadingLoad(f.properties) : (f.properties.sensor_with_data || 0)) > 0)
      ? [
          [null, CHORO[1], currentLens === "readings" ? "Lower reading load" : "Lower coverage"],
          [null, CHORO[6], currentLens === "readings" ? "Higher reading load" : "Higher coverage"],
        ]
      : [];
    const selectionItems = ["query", "quick"].includes(highlightStyleMode) && highlightedWardNos.size
      ? [["#2563eb", quickViewLabel || "Highlighted wards"]]
      : [];
    // Clickable classes: click toggles; picking several in one group = OR, across groups = AND.
    const lfItem = (group, key, color, label) => {
      const on = legendFilter[group].has(key), dim = legendFilter[group].size && !on;
      return `<div class="legend-item legend-filter${on ? " active" : ""}${dim ? " dim" : ""}" role="button" tabindex="0" aria-pressed="${on}" data-lf-group="${group}" data-lf-key="${key}"><span style="background:${color}"></span>${label}</div>`;
    };
    const plain = ([color, label]) => `<div class="legend-item"><span style="background:${color}"></span>${label}</div>`;
    items.innerHTML = selectionItems.map(plain).join("")
      + (wardItems.length ? `<div class="legend-group">Ward class</div>` : "")
      + wardItems.map(([key, color, label]) => key ? lfItem("ward", key, color, label) : plain([color, label])).join("")
      + `<div class="legend-group">Borewell trend</div>`
      + TREND_ORDER.map(k => lfItem("trend", k, TREND_COLORS[k], TREND_LABELS[k])).join("")
      + `<div class="legend-group">Water-level depth</div>`
      + DEPTH_BAND_ORDER.map(k => lfItem("depth", k, DEPTH_BAND_COLORS[k], DEPTH_BAND_LABELS[k])).join("");
  }
  const cap = document.querySelector(".legend-caption");
  if (cap) {
    const lensLabel = isAnalysisLens(currentLens)
      ? `Wards classified by ${analysisLensLabel()}${currentLens === "groundwater" ? ` (${groundwaterMethodLabel()})` : ""}`
      : ({ coverage: "Wards shaded by reporting sensor coverage", readings: "Wards shaded by reading volume" })[currentLens];
    cap.textContent = selectedWardNo != null
      ? "Ward isolated - click map background to clear"
      : (["query", "quick"].includes(highlightStyleMode) ? lensLabel : (quickViewLabel || lensLabel));
  }
}

