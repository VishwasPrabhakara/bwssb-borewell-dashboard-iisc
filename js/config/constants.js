// config/constants.js
// Part of the BWSSB/IISc borewell dashboard front-end.
// Split from the former single app.js; loaded as an ordered classic <script>,
// sharing one global scope with its sibling files. Order matters: see index.html.

// SUSPEND_LENS_GUARD
const _SUSPENDED_LENSES = new Set(["overall","common","common_composite","composite","previous_consumption","consumption_criticality"]);
function _guardLens() {
  if (typeof currentLens === "string" && _SUSPENDED_LENSES.has(currentLens)) {
    currentLens = "groundwater";
    const sel = document.getElementById("analysis-lens");
    if (sel) sel.value = "groundwater";
  }
}

// BWSSB Borewell Dashboard — IISc
// Full-screen map first. Everything else opens on click.

// NOTE: data-source config and URL routing (CONFIG, ANALYTICS_API_BASE, urlOf,
// seriesUrlOf) now live in js/core/api.js, the single data-access layer.

// ---------- User-facing error banner ----------
// Non-blocking toast for load/runtime failures, so problems are visible to a
// government user instead of hiding only in the console. Safe to call anytime.
function notifyError(message) {
  try {
    let el = document.getElementById("app-error-banner");
    if (!el) {
      el = document.createElement("div");
      el.id = "app-error-banner";
      el.setAttribute("role", "alert");
      el.style.cssText =
        "position:fixed;left:50%;bottom:18px;transform:translateX(-50%);z-index:9999;" +
        "max-width:min(92vw,620px);background:#7f1d1d;color:#fff;padding:10px 14px;" +
        "border-radius:8px;font:13px system-ui,sans-serif;box-shadow:0 6px 24px rgba(0,0,0,.3);" +
        "display:flex;gap:12px;align-items:flex-start";
      const span = document.createElement("span");
      span.id = "app-error-banner-text";
      const btn = document.createElement("button");
      btn.textContent = "✕";
      btn.setAttribute("aria-label", "Dismiss");
      btn.style.cssText = "background:none;border:none;color:#fff;cursor:pointer;font-size:14px;line-height:1";
      btn.onclick = () => el.remove();
      el.append(span, btn);
      document.body.appendChild(el);
    }
    el.querySelector("#app-error-banner-text").textContent = message;
  } catch (_) { /* DOM not ready: the console.warn at the call site still fired */ }
}

// NOTE: all mutable application state now lives in js/state/store.js (loaded
// first), so these declarations are no longer here. Reads still use the bare
// globals; interaction-state writes should go through Store.set(key, value).
