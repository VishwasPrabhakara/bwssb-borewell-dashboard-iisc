// ui/charts.js
// Part of the BWSSB/IISc borewell dashboard front-end.
// Split from the former single app.js; loaded as an ordered classic <script>,
// sharing one global scope with its sibling files. Order matters: see index.html.

// ============================================================
// Backend annotations are authoritative for the current policy version.
const sessionCache = new WeakMap();
let qualityFilter = "all";
let waterMode = "cleaned", dischargeMode = "all";
let showTrend = { water: true, discharge: false };
let dowWeekday = 4; // JS getDay(): 0=Sun..6=Sat — 4=Thursday (richest weekday in the PPT analysis)
const DOW_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
const DOW_SHORT = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
let sessionPage = 0;
const REASON_LABEL = {
  too_few_samples: "Fewer than 3 readings",
  no_volume: "Yield counter did not advance",
  sensor_relock_jump: "Water-level jump above 20 ft (possible sensor re-lock)",
  net_level_rise: "Ended shallower than it started",
  missing_water_level: "Missing water-level readings",
  missing_yield: "Missing yield readings",
};
function computeSessions(series) {
  if (!series || !series.times) return [];
  if (sessionCache.has(series)) return sessionCache.get(series);
  const raw = series.quality_policy_version === SessionQuality.version && Array.isArray(series.sessions)
    ? series.sessions : SessionQuality.build(series);
  const result = raw.map((ss, i) => ({ ...ss, number: i + 1,
    startIdx: ss.start, stopIdx: ss.stop,
    startTime: new Date(series.times[ss.start]), stopTime: new Date(series.times[ss.stop]),
    nSamples: ss.n, drawdownFt: ss.drawdown_ft, pumpedKl: ss.pumped_kl,
    maxStepFt: ss.max_step_ft,
  }));
  sessionCache.set(series, result);
  return result;
}
function sessionCoverage(sessions, seriesLen) {
  const cover = new Uint8Array(seriesLen);
  for (const sess of sessions) cover.fill({ok: 1, flagged: 2, excluded: 3}[sess.status], sess.startIdx, sess.stopIdx + 1);
  return cover;
}
function summarizeSessions(sessions) {
  const counts = { total: sessions.length, ok: 0, flagged: 0, excluded: 0, byReason: {} };
  for (const s of sessions) {
    counts[s.status]++;
    for (const r of s.reasons) counts.byReason[r] = (counts.byReason[r] || 0) + 1;
  }
  return counts;
}

async function loadAndRenderSeries(uid) {
  const res = await fetch(seriesUrlOf(uid));
  if (!res.ok) return;
  const series = await res.json();
  if (selectedSensorUid !== uid) return;
  currentSensorSeries = series;
  qualityFilter = "all";
  sessionPage = 0;
  currentRange = "1M";
  waterMode = "all"; dischargeMode = "all";
  drawCharts();
}

function filteredSeries(range) {
  const s = currentSensorSeries;
  if (!s) return { times: [], water: [], flow: [], cover: [] };
  const times = s.times.map(t => new Date(t));
  const last = times.length ? times[times.length - 1] : new Date();
  let from = null;
  if (range === "1W") from = new Date(last.getTime() - 7 * 86400000);
  else if (range === "1M") from = new Date(last.getTime() - 30 * 86400000);
  else if (range === "3M") from = new Date(last.getTime() - 90 * 86400000);
  const sessions = computeSessions(s);
  const cover = sessionCoverage(sessions, s.times.length);
  const out = { times: [], water: [], flow: [], cover: [] };
  for (let i = 0; i < times.length; i++) {
    if (from && times[i] < from) continue;
    out.times.push(times[i]);
    const included = qualityFilter === "all" || cover[i] === {ok: 1, flagged: 2, excluded: 3}[qualityFilter];
    out.water.push(included ? s.water_ft[i] : null);
    out.flow.push(included ? s.flow_lpm[i] : null);
    out.cover.push(cover[i]);
  }
  return out;
}


function rangeFrom() {
  const S = currentSensorSeries; if (!S || !S.times.length) return null;
  const last = new Date(S.times[S.times.length - 1]);
  if (currentRange === "1W") return new Date(last.getTime() - 7 * 86400000);
  if (currentRange === "1M") return new Date(last.getTime() - 30 * 86400000);
  if (currentRange === "3M") return new Date(last.getTime() - 90 * 86400000);
  return null;
}
function restedLevelPoints(minGapHours) {
  const S = currentSensorSeries; if (!S || !S.times || !S.times.length) return [];
  const from = (typeof rangeFrom === "function") ? rangeFrom() : null;
  const gapMs = (minGapHours || 8) * 3600 * 1000;
  const out = [];
  let prev = null;
  for (let i = 0; i < S.times.length; i++) {
    const t = new Date(S.times[i]);
    const y = S.water_ft ? S.water_ft[i] : null;
    if (prev !== null && (t - prev) >= gapMs && y != null) {
      if (!from || t >= from) out.push({ x: t, y });
    }
    prev = t;
  }
  return out;
}

function sessionLevelPoints(which) {
  const S = currentSensorSeries, wl = S.water_ft, from = rangeFrom();
  const pts = [];
  for (const se of computeSessions(S)) {
    if (qualityFilter !== "all" && se.status !== qualityFilter) continue;
    const idx = which === "off" ? se.startIdx : se.stopIdx;
    const y = wl[idx]; if (y == null) continue;
    const x = new Date(S.times[idx]); if (from && x < from) continue;
    pts.push({ x, y });
  }
  return pts;
}
function dailyExtreme(times, values, mode) {
  const byDay = new Map();
  for (let i = 0; i < times.length; i++) {
    if (values[i] == null) continue;
    const t = times[i], key = t.getFullYear() + "-" + t.getMonth() + "-" + t.getDate();
    const cur = byDay.get(key);
    if (!cur || (mode === "dmax" ? values[i] > cur.y : values[i] < cur.y)) byDay.set(key, { x: t, y: values[i] });
  }
  return [...byDay.values()].sort((a, b) => a.x - b.x);
}
function chartBaseOpts(xUnit) {
  return {
    responsive: true, maintainAspectRatio: false, animation: { duration: 250 },
    interaction: { mode: "nearest", intersect: false },
    plugins: { legend: { display: false }, tooltip: { backgroundColor: "rgba(11,61,76,0.95)" } },
    scales: {
      x: { type: "time", time: { unit: xUnit, tooltipFormat: "dd MMM yyyy, hh:mm a", displayFormats: { hour: "dd MMM", day: "dd MMM", week: "dd MMM", month: "MMM yyyy" } }, ticks: { color: "#5a6472", maxRotation: 0, autoSkip: true }, grid: { display: false } },
      y: { ticks: { color: "#5a6472" }, grid: { color: "#eef2f5" } },
    },
    elements: { point: { radius: 0 }, line: { borderWidth: 1.6 } },
    spanGaps: true,
  };
}

// --- Dashboard methodology helpers (match ppt + QC rules) ---

// Return the cleaned series (dedup + range clamp + Hampel + flatline) that the backend
// baked into sensor_series/*.json. Falls back to raw if the backend step was not run.
function cleanedArrays() {
  const s = currentSensorSeries;
  if (!s) return { times: [], water_ft: [], flow_lpm: [] };
  if (Array.isArray(s.cleaned_times) && s.cleaned_times.length) {
    return { times: s.cleaned_times, water_ft: s.cleaned_water_ft || [], flow_lpm: s.cleaned_flow_lpm || [] };
  }
  return { times: s.times || [], water_ft: s.water_ft || [], flow_lpm: s.flow_lpm || [] };
}

// Static-filtered points (preceding gap >= 8h) computed from cleaned arrays.
function cleanedStaticPoints(minGapHours) {
  const C = cleanedArrays();
  if (!C.times.length) return [];
  const from = rangeFrom();
  const out = [];
  let prevT = null;
  for (let i = 0; i < C.times.length; i++) {
    const t = new Date(C.times[i]);
    if (from && t < from) { prevT = t; continue; }
    if (prevT === null) { prevT = t; continue; }
    const gapH = (t - prevT) / 3_600_000;
    if (gapH >= minGapHours && C.water_ft[i] != null) {
      out.push({ x: t, y: C.water_ft[i] });
    }
    prevT = t;
  }
  return out;
}

// Cleaned (not static) points inside the current range.
function cleanedRangePoints() {
  const C = cleanedArrays();
  const from = rangeFrom();
  const outW = [], outF = [], outT = [];
  for (let i = 0; i < C.times.length; i++) {
    const t = new Date(C.times[i]);
    if (from && t < from) continue;
    outT.push(t); outW.push(C.water_ft[i]); outF.push(C.flow_lpm[i]);
  }
  return { times: outT, water: outW, flow: outF };
}

// OLS slope on time-series points. Accepts [{x: Date, y: num}] OR separate times[] + values[].
// Returns {slope_per_ms, slope_per_week, slope_m_per_year, intercept, r2, firstX, lastX, label}.
function olsSlope(pts, unitLabel) {
  const xs = [], ys = [];
  for (const p of pts) {
    if (!p || p.y == null || isNaN(p.y)) continue;
    const x = p.x instanceof Date ? p.x.getTime() : +p.x;
    if (!isFinite(x)) continue;
    xs.push(x); ys.push(p.y);
  }
  const n = xs.length;
  if (n < 2) return null;
  const mx = xs.reduce((a, b) => a + b, 0) / n;
  const my = ys.reduce((a, b) => a + b, 0) / n;
  let num = 0, den = 0, sstot = 0;
  for (let i = 0; i < n; i++) {
    num += (xs[i] - mx) * (ys[i] - my);
    den += (xs[i] - mx) ** 2;
    sstot += (ys[i] - my) ** 2;
  }
  if (den === 0) return null;
  const slope = num / den;         // y per ms
  const intercept = my - slope * mx;
  let ssres = 0;
  for (let i = 0; i < n; i++) {
    const yhat = intercept + slope * xs[i];
    ssres += (ys[i] - yhat) ** 2;
  }
  const r2 = sstot > 0 ? 1 - ssres / sstot : 0;
  const perWeek = slope * 1000 * 60 * 60 * 24 * 7;
  const mPerYear = perWeek * 52 * 0.3048;  // ft/wk -> m/yr (only meaningful for water_ft)
  const label = unitLabel === "ft"
    ? "OLS trend  " + (perWeek >= 0 ? "+" : "") + perWeek.toFixed(4) + " ft/wk  (" + (mPerYear >= 0 ? "+" : "") + mPerYear.toFixed(2) + " m/yr)  R² " + r2.toFixed(2)
    : "OLS trend  " + (perWeek >= 0 ? "+" : "") + perWeek.toFixed(1) + "/wk  R² " + r2.toFixed(2);
  return { slope, intercept, r2, firstX: xs[0], lastX: xs[xs.length-1], label };
}

function trendDataset(pts, color, unitLabel) {
  const o = olsSlope(pts, unitLabel);
  if (!o) return null;
  const yFirst = o.intercept + o.slope * o.firstX;
  const yLast  = o.intercept + o.slope * o.lastX;
  return {
    label: o.label,
    data: [{ x: new Date(o.firstX), y: yFirst }, { x: new Date(o.lastX), y: yLast }],
    borderColor: color, borderWidth: 2, borderDash: [6, 4],
    fill: false, pointRadius: 0, tension: 0, parsing: false,
  };
}

// Precomputed Sen / Mann-Kendall (Hamed-Rao) record for the current water filter.
function waterTrendRecord() {
  const r = sensorTrendByUid[String(currentSensorSeries?.uid)];
  return r && (waterMode === "dow" ? r.dow[String(dowWeekday)] : r[waterMode]);
}

// Trend line from the full-record Sen slope, anchored at the median of the plotted points.
// Falls back to the OLS line when sensor_trends.json has no entry (e.g. API mode).
function senTrendDataset(pts, color) {
  const t = waterTrendRecord(), v = pts.filter(p => p && p.y != null && !isNaN(p.y));
  if (!t || t.s == null || v.length < 2) return trendDataset(pts, color, "ft");
  const med = a => { const b = [...a].sort((x, y) => x - y); return b[Math.floor(b.length / 2)]; };
  const xs = v.map(p => +p.x), mx = med(xs), my = med(v.map(p => p.y)), k = t.s / (7 * 86400000);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  return {
    label: `Sen ${t.s > 0 ? "+" : ""}${t.s} ft/wk · MK(Hamed-Rao) p=${t.p} · ${t.c}`,
    data: [{ x: new Date(x0), y: my + k * (x0 - mx) }, { x: new Date(x1), y: my + k * (x1 - mx) }],
    borderColor: color, borderWidth: 2, borderDash: [6, 4], fill: false, pointRadius: 0, tension: 0, parsing: false,
  };
}

// Day-of-week static levels: one median static level per pumping session that
// falls on the chosen weekday, across the whole period. Mirrors the PPT method
// (sessions split by >=8h gaps -> one representative level each -> group by weekday).
function dowLevelPoints(weekday) {
  const S = currentSensorSeries;
  if (!S || !S.water_ft) return [];
  const from = rangeFrom();
  const out = [];
  for (const se of computeSessions(S)) {
    if (qualityFilter !== "all" && se.status !== qualityFilter) continue;
    const t = se.startTime;
    if (!t || t.getDay() !== weekday) continue;
    if (from && t < from) continue;
    const vals = [];
    for (let i = se.startIdx; i <= se.stopIdx; i++) {
      const y = S.water_ft[i];
      if (y != null && !isNaN(y)) vals.push(y);
    }
    if (!vals.length) continue;
    vals.sort((a, b) => a - b);
    out.push({ x: t, y: vals[Math.floor(vals.length / 2)] });
  }
  out.sort((a, b) => a.x - b.x);
  return out;
}

// Update the recalculated day-of-week slope note under the water chart.
function syncDowControls() {
  const dowSel = document.getElementById("dow-weekday");
  if (dowSel) { dowSel.style.display = (waterMode === "dow") ? "" : "none"; dowSel.value = String(dowWeekday); }
}

function updateDowNote() {
  const el = document.getElementById("dow-slope-note");
  if (!el) return;
  if (waterMode !== "dow") { el.style.display = "none"; return; }
  el.style.display = "block";
  const pts = dowLevelPoints(dowWeekday);
  const o = (pts.length >= 2) ? olsSlope(pts, "ft") : null;
  if (!o) {
    el.innerHTML = `<b>${DOW_NAMES[dowWeekday]} trend:</b> only ${pts.length} ${DOW_NAMES[dowWeekday]} session${pts.length === 1 ? "" : "s"} in this range — need at least 2 to fit a slope. Try another weekday or the ALL range.`;
    return;
  }
  const t = waterTrendRecord();
  const perWeek = t && t.s != null ? t.s : o.slope * 1000 * 60 * 60 * 24 * 7;
  const perYearFt = perWeek * 52;
  const dir = perWeek > 0.1 ? "deepening — water table falling"
            : perWeek < -0.1 ? "recovering — water table rising"
            : "roughly stable";
  el.innerHTML = `<b>${DOW_NAMES[dowWeekday]} trend</b> · Sen slope on <b>${pts.length}</b> session medians${t && t.p != null ? ` (Mann-Kendall/Hamed-Rao p=${t.p}, ${t.c})` : ""}: `
    + `<b>${perWeek >= 0 ? "+" : ""}${perWeek.toFixed(3)} ft/week</b> `
    + `(${perYearFt >= 0 ? "+" : ""}${perYearFt.toFixed(1)} ft/year) — ${dir}.`;
}

function drawCharts() {
  if (charts.water) charts.water.destroy();
  if (charts.discharge) charts.discharge.destroy();
  const d = filteredSeries(currentRange);
  const xUnit = currentRange === "3M" ? "week" : currentRange === "ALL" ? "month" : "day";
  const yOpts = t => {
    const o = chartBaseOpts(xUnit);
    o.scales.y.title = { display: true, text: t, color: "#5a6472", font: { size: 12, weight: "500" } };
    o.plugins.legend = { display: true, position: "top", align: "end", labels: { boxWidth: 14, boxHeight: 2, color: "#334155", font: { size: 11 }, filter: it => it.text && it.text.length > 0 } };
    return o;
  };

  // Water level ---------------------------------------------------------
  let wDatasets = [], wLabels, trendPts = null;
  const C = cleanedRangePoints();
  if (waterMode === "raw") {
    wDatasets.push({ label: "Water level (raw)", data: d.water, borderColor: "#0e7490", backgroundColor: "rgba(14,116,144,0.08)", fill: true, tension: 0.25, pointRadius: 0, borderWidth: 1.4 });
    wLabels = d.times;
    trendPts = d.times.map((t, i) => ({ x: t, y: d.water[i] }));
  } else if (waterMode === "cleaned") {
    wDatasets.push({ label: "Water level (cleaned)", data: C.water, borderColor: "#0e7490", backgroundColor: "rgba(14,116,144,0.08)", fill: true, tension: 0.25, pointRadius: 0, borderWidth: 1.4 });
    wLabels = C.times;
    trendPts = C.times.map((t, i) => ({ x: t, y: C.water[i] }));
  } else if (waterMode === "dow") {
    const pts = dowLevelPoints(dowWeekday);
    wDatasets.push({ label: `${DOW_SHORT[dowWeekday]} static median (per session)`, data: pts, borderColor: "#7c3aed", backgroundColor: "rgba(124,58,237,0.12)", fill: false, tension: 0, pointRadius: 3.4, borderWidth: 1.6, showLine: true });
    trendPts = pts;
  } else { // static
    const pts = cleanedStaticPoints(8);
    wDatasets.push({ label: "Static level (cleaned + rested ≥ 8h)", data: pts, borderColor: "#0e7490", backgroundColor: "rgba(14,116,144,0.10)", fill: false, tension: 0.2, pointRadius: 2.6, borderWidth: 1.8 });
    trendPts = pts;
  }
  if ((showTrend.water || waterMode === "dow") && trendPts) {
    const td = senTrendDataset(trendPts, "#b45309");
    if (td) wDatasets.push(td);
  }
  charts.water = new Chart(document.getElementById("chart-water"), {
    type: "line",
    data: wLabels ? { labels: wLabels, datasets: wDatasets } : { datasets: wDatasets },
    options: yOpts("ft below surface  (lower on chart = deeper water)"),
  });
  updateDowNote();
  syncDowControls();

  // Discharge -----------------------------------------------------------
  let fDatasets = [], fLabels, fTrendPts = null;
  if (dischargeMode === "all") {
    fDatasets.push({ label: "Discharge", data: d.flow, borderColor: "#0891b2", backgroundColor: "rgba(8,145,178,0.10)", fill: true, tension: 0.25, pointRadius: 0, borderWidth: 1.4 });
    fLabels = d.times;
    fTrendPts = d.times.map((t, i) => ({ x: t, y: d.flow[i] }));
  } else {
    const pts = dailyExtreme(d.times, d.flow, dischargeMode);
    fDatasets.push({ label: dischargeMode === "dmax" ? "Daily max discharge" : "Daily min discharge", data: pts, borderColor: "#0891b2", backgroundColor: "rgba(8,145,178,0.10)", fill: false, tension: 0.2, pointRadius: 2.5, borderWidth: 1.6 });
    fTrendPts = pts;
  }
  if (showTrend.discharge && fTrendPts) {
    const td = trendDataset(fTrendPts, "#b45309", "L/min");
    if (td) fDatasets.push(td);
  }
  charts.discharge = new Chart(document.getElementById("chart-discharge"), {
    type: "line",
    data: fLabels ? { labels: fLabels, datasets: fDatasets } : { datasets: fDatasets },
    options: yOpts("L/min"),
  });

  // Reflect trend toggle state visually
  document.querySelectorAll(".trend-btn[data-trend]").forEach(b => {
    const which = b.dataset.trend;
    b.classList.toggle("active", !!showTrend[which]);
  });
}
function renderSessionStatsCard() {
  if (!currentSensorSeries) return;
  const sessions = computeSessions(currentSensorSeries), stats = summarizeSessions(sessions);
  const number = v => v == null ? "—" : v.toLocaleString("en-IN", {maximumFractionDigits: 2});
  const date = d => d.toLocaleString("en-IN", {day:"2-digit", month:"short", year:"numeric", hour:"2-digit", minute:"2-digit", second:"2-digit", hour12:true});
  const LBL = {total:"Total", ok:"OK", flagged:"Flagged", excluded:"Excluded"};
  const STAT = {ok:"OK", flagged:"Flagged", excluded:"Excluded"};
  const tiles = document.getElementById("session-tiles");
  if (tiles) {
    const activeKey = qualityFilter === "all" ? "total" : qualityFilter;
    tiles.innerHTML =
      `<div class="session-stats-grid">${["total","ok","flagged","excluded"].map(k =>
        `<div class="ss-tile ${k}${k===activeKey?" active":""}" data-filter="${k}"><div class="ss-num">${number(stats[k])}</div><div class="ss-lbl">${LBL[k]}</div></div>`).join("")}</div>`;
    tiles.querySelectorAll(".ss-tile").forEach(t => t.onclick = () => {
      qualityFilter = t.dataset.filter === "total" ? "all" : t.dataset.filter;
      sessionPage = 0; renderSessionStatsCard(); drawCharts();
    });
  }
  const el = document.getElementById("session-stats-card");
  if (!el) return;
  const visible = sessions.filter(s => qualityFilter === "all" || s.status === qualityFilter);
  const pageSize = 25, pages = Math.max(1, Math.ceil(visible.length / pageSize));
  sessionPage = Math.min(Math.max(0, sessionPage), pages - 1);
  el.hidden = false;
  el.innerHTML = `
    <div class="quality-toolbar">
      <label>Show <select id="quality-filter">${[["all","All sessions"],["ok","OK only"],["flagged","Flagged"],["excluded","Excluded"]].map(([v,l])=>`<option value="${v}" ${v===qualityFilter?"selected":""}>${l}</option>`).join("")}</select></label>
      <button id="download-sessions">Download CSV</button>
    </div>
    <div class="quality-table-wrap"><table class="quality-table"><thead><tr><th>Session</th><th>Read.</th><th>Status</th><th>Vol&nbsp;kL</th><th>Draw&nbsp;ft</th><th>Jumps</th><th>Eligible</th></tr></thead><tbody>
    ${visible.slice(sessionPage*pageSize,(sessionPage+1)*pageSize).map(s=>`<tr class="row-${s.status}"><td><b>#${s.number}</b><br><span class="cell-sub">${date(s.startTime)}<br>\u2192 ${date(s.stopTime)}</span></td><td>${s.n}</td><td><span class="status-pill ${s.status}">${STAT[s.status]}</span>${s.reasons.length?`<br><span class="cell-sub">${s.reasons.map(r=>REASON_LABEL[r]||"Other").join(", ")}</span>`:""}</td><td>${number(s.pumpedKl)}</td><td>${number(s.drawdownFt)}</td><td>${s.jump_count}</td><td><span class="cell-sub">${[s.eligible_volume?"Vol":"",s.eligible_drawdown?"Draw":"",s.eligible_specific_capacity?"Sp.cap":""].filter(Boolean).join(", ")||"—"}</span></td></tr>`).join("") || '<tr><td colspan="7" class="cell-sub">No sessions in this category.</td></tr>'}
    </tbody></table></div>
    <div class="quality-toolbar"><button id="sessions-prev" ${sessionPage===0?"disabled":""}>‹ Prev</button><span class="page-info">Page ${sessionPage+1}/${pages} · ${number(visible.length)} shown</span><button id="sessions-next" ${sessionPage+1>=pages?"disabled":""}>Next ›</button></div>
    <details class="quality-about"><summary>About these numbers &amp; rules</summary>
      <p class="quality-note">All readings are retained. Flagged sessions need review but are not discarded; excluded sessions (fewer than 3 readings, no positive volume, or missing endpoint water levels) stay available here. Counts cover the full sensor history.</p>
      <p class="quality-note">New session: a gap of more than 30 minutes. Volume is reconstructed by integrating flow (L/min × minutes) because the device's cumulative-yield counter is unreliable; the raw counter is retained for audit. A level jump over 20 ft flags a session and blocks drawdown, while its volume can stay usable.</p>
      ${Object.entries(stats.byReason).map(([r,n]) => `<div class="reason-row"><span>${REASON_LABEL[r] || "Other issue"}</span><b>${number(n)}</b></div>`).join("")}
    </details>`;
  el.querySelector("#quality-filter").onchange = e => { qualityFilter = e.target.value; sessionPage = 0; renderSessionStatsCard(); drawCharts(); };
  el.querySelector("#sessions-prev").onclick = () => { sessionPage--; renderSessionStatsCard(); };
  el.querySelector("#sessions-next").onclick = () => { sessionPage++; renderSessionStatsCard(); };
  el.querySelector("#download-sessions").onclick = () => {
    const rows = [["uid","session","start","stop","readings","status","reasons","observed_volume_kl","observed_drawdown_ft","jump_count"],
      ...visible.map(s=>[currentSensorSeries.uid,s.number,date(s.startTime),date(s.stopTime),s.n,s.status,s.reasons.join("; "),s.pumpedKl,s.drawdownFt,s.jump_count])];
    const csv = rows.map(row=>row.map(v=>'"'+String(v ?? "").replaceAll('"','""')+'"').join(",")).join("\r\n");
    const url = URL.createObjectURL(new Blob([csv],{type:"text/csv;charset=utf-8"}));
    const a = document.createElement("a"); a.href=url; a.download=`${currentSensorSeries.uid}_sessions_${qualityFilter}.csv`; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
  };
}

function wireSensorTabs() {
  document.querySelectorAll(".sensor-tab").forEach(tab => {
    tab.onclick = () => {
      const name = tab.dataset.tab;
      document.querySelectorAll(".sensor-tab").forEach(t => t.classList.toggle("active", t === tab));
      document.querySelectorAll(".tab-pane").forEach(pane => pane.classList.toggle("active", pane.dataset.pane === name));
      if (name === "charts") drawCharts();
    };
  });
}
function wireRangeChips() {
  document.querySelectorAll(".range-chip").forEach(chip => {
    chip.addEventListener("click", () => {
      const parent = chip.parentElement;
      parent.querySelectorAll(".range-chip").forEach(c => c.classList.remove("active"));
      // sync both blocks to the same range
      Store.set("currentRange", chip.dataset.range);
      document.querySelectorAll(".range-chip").forEach(c => {
        c.classList.toggle("active", c.dataset.range === currentRange);
      });
      drawCharts();
    });
  });
  document.querySelectorAll(".expand-btn").forEach(btn => {
    btn.addEventListener("click", () => openChartModal(btn.dataset.expand));
  });
  document.querySelectorAll(".dl-btn").forEach(btn => {
    btn.addEventListener("click", () => downloadChartPng(btn.dataset.download));
  });
  document.querySelectorAll(".chart-mode").forEach(sel => {
    sel.addEventListener("change", () => {
      if (sel.dataset.chart === "water") {
        waterMode = sel.value;
        // Day-of-week needs the full history to fit a meaningful slope.
        if (waterMode === "dow" && currentRange !== "ALL") {
          Store.set("currentRange", "ALL");
          document.querySelectorAll(".range-chip[data-range]").forEach(c => c.classList.toggle("active", c.dataset.range === "ALL"));
        }
      } else {
        dischargeMode = sel.value;
      }
      drawCharts();
    });
  });
  const dowSel = document.getElementById("dow-weekday");
  if (dowSel) dowSel.addEventListener("change", () => { dowWeekday = Number(dowSel.value); drawCharts(); if (trendMode === "dow") { renderSensors(); buildLegend(); } });
  document.querySelectorAll(".trend-btn[data-trend]").forEach(btn => {
    btn.addEventListener("click", () => {
      const which = btn.dataset.trend;
      showTrend[which] = !showTrend[which];
      drawCharts();
    });
  });
}

function downloadChartPng(which) {
  const chart = which === "modal" ? charts.modal : charts[which];
  if (!chart || !currentSensorSeries) return;
  const src = chart.canvas;
  const tmp = document.createElement("canvas");
  tmp.width = src.width; tmp.height = src.height;
  const ctx = tmp.getContext("2d");
  ctx.fillStyle = "#ffffff"; ctx.fillRect(0, 0, tmp.width, tmp.height);
  ctx.drawImage(src, 0, 0);
  const label = which === "discharge" ? "discharge" : which === "modal" ? "chart" : "water_level";
  const a = document.createElement("a");
  a.href = tmp.toDataURL("image/png");
  a.download = `${currentSensorSeries.uid}_${label}.png`;
  a.click();
}

function openChartModal(which) {
  const isWater = which === "water";
  const mode = isWater ? waterMode : dischargeMode;
  const title = isWater ? "Water level (ft below surface)" : "Discharge (L/min)";
  document.getElementById("chart-modal-title").textContent = `${title} — ${currentSensorSeries.uid}`;
  const body = document.getElementById("chart-modal-body");
  body.innerHTML = `<canvas id="chart-modal-canvas"></canvas>`;
  openOverlay("chart-modal");
  const _mc = document.querySelector("#chart-modal .close-btn");
  if (_mc && !document.getElementById("modal-dl")) {
    const dl = document.createElement("button");
    dl.id = "modal-dl"; dl.className = "close-btn"; dl.title = "Download PNG"; dl.textContent = "⬇";
    dl.onclick = () => downloadChartPng("modal");
    _mc.parentNode.insertBefore(dl, _mc);
  }
  const d = filteredSeries(currentRange);
  const color = isWater ? "#1e3a8a" : "#0891b2";
  const yTitle = isWater ? "ft below surface" : "L/min";
  const xUnit = currentRange === "3M" ? "week" : currentRange === "ALL" ? "month" : "day";
  let datasets, labels;
  if (mode === "all") {
    labels = d.times;
    datasets = [{ data: isWater ? d.water : d.flow, borderColor: color, backgroundColor: color + "1A", fill: true, tension: 0.25, pointRadius: 0 }];
  } else if (isWater && mode === "rested8h") {
    datasets = [{ data: restedLevelPoints(8), borderColor: color, backgroundColor: color + "1A", fill: false, tension: 0.2, pointRadius: 3 }];
  } else if (isWater) {
    datasets = [{ data: sessionLevelPoints(waterMode), borderColor: color, backgroundColor: color + "1A", fill: false, tension: 0.2, pointRadius: 3 }];
  } else {
    datasets = [{ data: dailyExtreme(d.times, d.flow, dischargeMode), borderColor: color, backgroundColor: color + "1A", fill: false, tension: 0.2, pointRadius: 3 }];
  }
  if (charts.modal) charts.modal.destroy();
  charts.modal = new Chart(document.getElementById("chart-modal-canvas"), {
    type: "line",
    data: labels ? { labels, datasets } : { datasets },
    options: {
      responsive: true, maintainAspectRatio: false, spanGaps: true,
      plugins: { legend: { display: false }, tooltip: { backgroundColor: "rgba(11,61,76,0.95)" } },
      scales: {
        x: { type: "time", time: { unit: xUnit, tooltipFormat: "dd MMM yyyy, hh:mm a", displayFormats: { hour: "dd MMM", day: "dd MMM", week: "dd MMM", month: "MMM yyyy" } }, grid: { color: "#eef2f5" } },
        y: { title: { display: true, text: yTitle } },
      },
      elements: { point: { radius: mode === "all" ? 0 : 3 }, line: { borderWidth: 1.8 } },
    },
  });
}
