// ui/detail.js
// Part of the BWSSB/IISc borewell dashboard front-end.
// Split from the former single app.js; loaded as an ordered classic <script>,
// sharing one global scope with its sibling files. Order matters: see index.html.

// ---------- Detail panel: ward ----------
function openWardDetail(p, feat) {
  const panel = document.getElementById("detail");
  const title = document.getElementById("detail-title");
  const body = document.getElementById("detail-body");
  title.innerHTML = `<div class="kicker">Ward ${p.ward_no}</div><h2>${p.ward_name || "—"}</h2>`;
  const list = (sensorsByWard[p.ward_no] || []).sort((a, b) => (b.has_data - a.has_data) || (a.uid > b.uid ? 1 : -1));
  const withData = list.filter(s => s.has_data).length;
  const noData = list.length - withData;
  const gw = groundwaterWardSummary(p.ward_no);
  const fmtInt = v => v == null ? "—" : Math.round(v).toLocaleString("en-IN");
  const fmtSlope = v => v == null ? "—" : `${v.toFixed(3)} ft/week`;
  const fmtMm = v => v == null ? "—" : v.toLocaleString("en-IN", { maximumFractionDigits: 0 }) + " mm";
  body.innerHTML = `
    <div class="stat-grid">
      <div class="stat-card"><div class="stat-label">Sensors with data</div><div class="stat-value">${withData}</div><div class="stat-sub">out of ${list.length} total</div></div>
      <div class="stat-card"><div class="stat-label">Ward status</div><div class="stat-value small">${wardStatusLabel(wardStatusKey(p))}</div><div class="stat-sub">${currentLens === "groundwater" ? groundwaterMethodLabel() : "Current map lens"}</div></div>
      <div class="stat-card"><div class="stat-label">No-data devices</div><div class="stat-value small">${noData}</div><div class="stat-sub">from full inventory</div></div>
      ${gw ? `<div class="stat-card"><div class="stat-label">Groundwater</div><div class="stat-value small">${wardStatusLabel(gw.status)}</div><div class="stat-sub">${groundwaterMethodLabel()}</div></div>` : ""}
      ${gw ? `<div class="stat-card"><div class="stat-label">Slope</div><div class="stat-value small">${fmtSlope(gw.slope)}</div><div class="stat-sub">${gw.points} weekly values</div></div>` : ""}
      <div class="stat-card"><div class="stat-label">Area</div><div class="stat-value small">${p.area_km2 ? p.area_km2.toFixed(2) + " km²" : "—"}</div></div>
<!-- Rainfall stat card hidden until KWRIS+KSNDMC pipeline is finalised. Re-enable by restoring this line. -->
      <div class="stat-card"><div class="stat-label">Population 2001</div><div class="stat-value small">${fmtInt(p.population_2001)}</div><div class="stat-sub">Census</div></div>
      <div class="stat-card"><div class="stat-label">Population 2011</div><div class="stat-value small">${fmtInt(p.population_2011)}</div><div class="stat-sub">Census</div></div>
      <div class="stat-card"><div class="stat-label">Projected 2026</div><div class="stat-value small">${fmtInt(p.population_2026)}</div><div class="stat-sub">CAGR from 2001–10</div></div>
    </div>
    ${gw ? `<div class="criticality-summary">
      <div class="section-title">Groundwater criticality</div>
      <div class="criticality-line"><b>${gw.category}</b> · ${gw.direction}${gw.previousCritical ? " · Previous consumption-critical ward" : ""}</div>
      ${gw.reason ? `<div class="criticality-note">${gw.reason}</div>` : ""}
    </div>` : ""}
    <div class="section-title">Sensors in this ward (${list.length})</div>
    <div class="uid-list" id="ward-uid-list"></div>
  `;
  const ul = body.querySelector("#ward-uid-list");
  if (list.length === 0) {
    ul.innerHTML = `<div class="loading">No sensors registered in this ward.</div>`;
  } else {
    list.forEach(s => {
      const row = document.createElement("div");
      row.className = "uid-item" + (selectedSensorUid === s.uid ? " selected" : "");
      row.dataset.uid = s.uid;
      const status = sensorStatusKey(s);
      row.innerHTML = `<span class="uid-mono">${s.uid}</span><span class="uid-tag ${status}">${status === "with_data" ? "reporting" : "no data"}</span>`;
      row.onclick = () => openSensorDetail(s.uid);
      ul.appendChild(row);
    });
  }
  panel.hidden = false;
}

// ---------- Detail panel: sensor ----------
async function openSensorDetail(uid) {
  const s = sensorsByUid[uid];
  if (!s) return;
  selectedSensorUid = uid;
  renderSensors();
  updateClearFilterChip();
  // If the ward-list is visible, refresh row highlighting without re-rendering the whole panel.
  document.querySelectorAll(".uid-item").forEach(el => {
    el.classList.toggle("selected", el.dataset.uid === uid);
  });
  const panel = document.getElementById("detail");
  const title = document.getElementById("detail-title");
  const body = document.getElementById("detail-body");
  title.innerHTML = `<div class="kicker">Sensor</div><h2>${s.uid}</h2>`;
  body.innerHTML = `
    <div class="stat-grid">
      <div class="stat-card"><div class="stat-label">Ward</div><div class="stat-value small">${s.ward_no != null ? `${s.ward_no} · ${s.ward_name || ""}` : "Unassigned"}</div></div>
      <div class="stat-card"><div class="stat-label">Motor HP</div><div class="stat-value small">${s.motor_hp != null ? s.motor_hp : "—"}</div></div>
      <div class="stat-card"><div class="stat-label">Borewell depth</div><div class="stat-value small">${s.borewell_depth != null ? s.borewell_depth + " ft" : "—"}</div></div>
      <div class="stat-card"><div class="stat-label">Readings</div><div class="stat-value small">${(s.reading_count || 0).toLocaleString("en-IN")}</div></div>
      <div class="stat-card"><div class="stat-label">Status</div><div class="stat-value small">${sensorStatusLabel(sensorStatusKey(s))}</div></div>
      <div class="stat-card"><div class="stat-label">First reading</div><div class="stat-value small">${fmtDate(s.first_data_at)}</div></div>
      <div class="stat-card"><div class="stat-label">Last reading</div><div class="stat-value small">${fmtDate(s.last_data_at)}</div></div>
    </div>
    ${s.has_data ? `
      <div class="sensor-tabs">
        <button class="sensor-tab active" data-tab="charts">Charts</button>
        <button class="sensor-tab" data-tab="sessions">Sessions</button>
      </div>
      <div class="tab-pane active" data-pane="charts">
        <div id="session-tiles" class="session-tiles"></div>
        ${sensorChartsHTML()}
      </div>
      <div class="tab-pane" data-pane="sessions">
        <div id="session-stats-card" class="session-stats-card">Computing session quality…</div>
      </div>` : `<div class="loading">No time-series data for this sensor in the current snapshot.</div>`}
  `;
  panel.hidden = false;
  // If the sensor was clicked without a ward context, zoom in to it (like a ward selection).
  const targetZoom = selectedWardNo == null ? 16 : null;
  if (s.lat != null && s.lng != null) panToLatLngLeftHalf(s.lat, s.lng, targetZoom);
  if (s.has_data) {
    await loadAndRenderSeries(uid);
    if (selectedSensorUid !== uid) return;
    renderSessionStatsCard();
    wireRangeChips();
    wireSensorTabs();
  }
}

function sensorChartsHTML() {
  const chips = ["1W", "1M", "3M", "ALL"].map(r => `<button class="range-chip${r === "1M" ? " active" : ""}" data-range="${r}">${r}</button>`).join("");
  return `
    <div class="chart-block">
      <div class="chart-header">
        <div class="chart-title">Water level (ft below surface)</div>
        <div class="chart-actions"><select class="chart-mode" data-chart="water" title="Water-level view"><option value="raw">Raw (as recorded)</option><option value="cleaned" selected>Cleaned (QC applied)</option><option value="static">Static (cleaned + rested ≥ 8h)</option></select>${chips}<button class="trend-btn" data-trend="water" title="Toggle OLS trend line">Trend</button><button class="dl-btn" data-download="water" title="Download PNG">⬇</button><button class="expand-btn" data-expand="water" title="Expand">⤢</button></div>
      </div>
      <div class="chart-canvas-wrap"><canvas id="chart-water"></canvas></div>
    </div>
    <div class="chart-block">
      <div class="chart-header">
        <div class="chart-title">Recorded discharge (L/min)</div>
        <div class="chart-actions"><select class="chart-mode" data-chart="discharge" title="Discharge view"><option value="all">All</option><option value="dmax">Daily max</option><option value="dmin">Daily min</option></select>${chips}<button class="trend-btn" data-trend="discharge" title="Toggle OLS trend line">Trend</button><button class="dl-btn" data-download="discharge" title="Download PNG">⬇</button><button class="expand-btn" data-expand="discharge" title="Expand">⤢</button></div>
      </div>
      <div class="chart-canvas-wrap"><canvas id="chart-discharge"></canvas></div>
    </div>
  `;
}

