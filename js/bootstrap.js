// bootstrap.js
// Part of the BWSSB/IISc borewell dashboard front-end.
// Split from the former single app.js; loaded as an ordered classic <script>,
// sharing one global scope with its sibling files. Order matters: see index.html.

// ---------- Utils ----------
function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

// -------- Rainfall CSV download --------
async function downloadRainfallCsv(wardNo, grain) {
  const res = await fetch(`./data/rainfall/${wardNo}.json`);
  if (!res.ok) { alert("No rainfall data for this ward yet. Run backend/fetch_rainfall.py to populate."); return; }
  const j = await res.json();
  const rows = grain === "monthly"
    ? [["month","rainfall_mm"], ...(j.monthly || []).map(r => [r.month, r.rainfall_mm])]
    : [["date","rainfall_mm"], ...(j.daily || []).map(r => [r.date, r.rainfall_mm ?? ""])];
  const csv = rows.map(r => r.join(",")).join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `rainfall_ward${wardNo}_${grain}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-download-rain]");
  if (!el) return;
  e.preventDefault();
  const wardNo = el.dataset.downloadRain;
  const grain = window.confirm("Daily rainfall CSV?\nOK = daily · Cancel = monthly") ? "daily" : "monthly";
  downloadRainfallCsv(wardNo, grain);
});


async function switchCriticalityMethod(method) {
  Store.set("criticalityMethod", method);
  const payload = await loadCriticalityForMethod(method);
  const rows = (payload.wards || []).map(calculateGroundwaterCriticality);
  criticalGroundwaterByNo = new Map(rows.map(w => [normalizeWardNo(w.wardNo), w]));
  analyticsLoaded = criticalGroundwaterByNo.size > 0;
  console.log(`[method] ${method} loaded, wards=${criticalGroundwaterByNo.size}`);
  if (wardLayer) wardLayer.setStyle(defaultWardStyle);
  if (typeof updateMetrics === "function") updateMetrics();
  if (typeof buildLegend === "function") buildLegend();
}

document.addEventListener("DOMContentLoaded", () => {
  const el = document.getElementById("criticality-method");
  if (el) el.addEventListener("change", () => switchCriticalityMethod(el.value));
});



boot();
