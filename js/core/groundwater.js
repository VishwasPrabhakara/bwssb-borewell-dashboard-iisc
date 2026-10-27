// core/groundwater.js
// Part of the BWSSB/IISc borewell dashboard front-end.
// Split from the former single app.js; loaded as an ordered classic <script>,
// sharing one global scope with its sibling files. Order matters: see index.html.

function numOrNull(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function calculateGroundwaterCriticality(input = {}) {
  // Backend-classification passthrough: honour file's dashboardMapCategory when present.
  const backendCats = new Set(["Critical: Ward-average groundwater decline",
    "Confirmed groundwater rise","Possible groundwater rise","Stable groundwater trend","Insufficient data"]);
  if (criticalityMethod === "modern" && input.dashboardMapCategory && backendCats.has(input.dashboardMapCategory)) {
    const backendCat = input.dashboardMapCategory;
    const isCritical = backendCat === "Critical: Ward-average groundwater decline";
    return {
      ...input,
      groundwaterStatus: isCritical ? "Critical" : (backendCat === "Insufficient data" ? "Insufficient data" : "Normal"),
      groundwaterDirection: isCritical ? "Declining"
        : backendCat === "Confirmed groundwater rise" ? "Improving"
        : backendCat === "Possible groundwater rise" ? "Possible improvement"
        : backendCat === "Stable groundwater trend" ? "Stable" : "Not computed",
      dashboardAction: isCritical ? "Yes" : "No",
      dashboardMapCategory: backendCat,
      linearMethodCritical: isCritical ? "Yes" : "No",
      theilSenMethodCritical: isCritical ? "Yes" : "No",
      mannKendallMethodCritical: input.mannKendallVerdict === "Yes" ? "Yes" : "No",
      linearMannKendallCritical: input.linearMannKendallCritical || (isCritical ? "Yes" : "No"),
      theilSenMannKendallCritical: input.theilSenMannKendallCritical || (isCritical ? "Yes" : "No"),
      pointCount: input.usableWeeklyValues ?? input.pointCount ?? 0,
      hasTrendEvidence: backendCat !== "Insufficient data",
    };
  }

  const linear = numOrNull(input.linearSlopeFtPerWeek ?? input.weeklyChangeFtWeek ?? input.weekly_change_ft_week_from_dashboard_average);
  const theil = numOrNull(input.senSlopeFtPerWeek ?? input.theilSlopeFtPerWeek ?? linear);
  const mk = numOrNull(input.mannKendallS);
  const pValue = numOrNull(input.mannKendallPValue);
  const pointCount = numOrNull(input.pointCount ?? input.usableWeeklyValues ?? input.weeklyPointsUsed);
  const hasSlope = Number.isFinite(linear) || Number.isFinite(theil);
  const selectedSlope = Number.isFinite(linear) ? linear : theil;
  const hasTrendEvidence = hasSlope;
  const hasMannKendall = Number.isFinite(pointCount)
    && pointCount >= (criticalityMethod === 'legacy' ? GROUNDWATER_MIN_MK_WEEKS_LEGACY : GROUNDWATER_MIN_MK_WEEKS_MODERN)
    && Number.isFinite(mk)
    && Number.isFinite(pValue);
  const linearCritical = Number.isFinite(linear) && linear > LINEAR_DECLINE_THRESHOLD_FT_PER_WEEK;
  const theilCritical = Number.isFinite(theil) && theil > LINEAR_DECLINE_THRESHOLD_FT_PER_WEEK;
  const mannCritical = hasMannKendall && mk > 0 && pValue <= TREND_SIGNIFICANCE_ALPHA;
  const linearMannCritical = hasMannKendall ? linearCritical && mannCritical : linearCritical;
  const theilMannCritical = hasMannKendall ? theilCritical && mannCritical : theilCritical;
  const rising = Number.isFinite(selectedSlope) && selectedSlope < -LINEAR_DECLINE_THRESHOLD_FT_PER_WEEK;
  const critical = linearMannCritical || theilMannCritical;
  const stable = hasTrendEvidence && !critical && !rising;
  const category = critical
    ? "Critical: Ward-average groundwater decline"
    : rising
    ? "Possible groundwater rise"
    : stable
    ? "Stable groundwater trend"
    : "Insufficient groundwater data";
  return {
    ...input,
    groundwaterStatus: critical ? "Critical" : hasTrendEvidence ? "Normal" : "Insufficient data",
    groundwaterDirection: critical ? "Declining" : rising ? "Possible improvement" : stable ? "Stable" : "Not computed",
    dashboardAction: critical ? "Yes" : "No",
    dashboardMapCategory: category,
    linearMethodCritical: linearCritical ? "Yes" : "No",
    theilSenMethodCritical: theilCritical ? "Yes" : "No",
    mannKendallMethodCritical: mannCritical ? "Yes" : "No",
    linearMannKendallCritical: linearMannCritical ? "Yes" : "No",
    theilSenMannKendallCritical: theilMannCritical ? "Yes" : "No",
    linearSlopeFtPerWeek: linear,
    senSlopeFtPerWeek: theil,
    declineStrengthFtPerWeek: selectedSlope,
    hasTrendEvidence,
    oldConsumptionNoGroundwaterData: isYes(input.previousCriticalWard) && !hasTrendEvidence ? "Yes" : "No",
  };
}

function methodVotesForCritical(critical = {}) {
  return {
    linear: isYes(critical.linearMethodCritical),
    theil: isYes(critical.theilSenMethodCritical),
    mann: isYes(critical.mannKendallMethodCritical),
  };
}

function selectedGroundwaterMethodIsCritical(critical = {}, mode = groundwaterMethodMode) {
  const votes = methodVotesForCritical(critical);
  // Standard MK vote already in `votes.mann` — uses input.mannKendallMethodCritical.
  // Modified MK vote: recompute using the ACR-corrected p that the backend stamped.
  // In modern mode the backend already ran Modified MK, so mannKendallVerdict IS modified MK.
  // In legacy mode we have no modified MK data, so treat mk_mod == mk.
  const mkMod = (criticalityMethod === "legacy")
    ? votes.mann
    : (critical.mannKendallVerdict === "Yes" && (Number(critical.linearSlopeFtPerWeek) > 0 || Number(critical.senSlopeFtPerWeek) > 0));
  if (mode === "linear") return votes.linear;
  if (mode === "theil") return votes.theil;
  if (mode === "mann") return votes.mann;
  if (mode === "mk_mod") return mkMod;
  if (mode === "linear_theil") return votes.linear && votes.theil;
  if (mode === "linear_mann") return votes.linear && votes.mann;
  if (mode === "theil_mann") return votes.theil && votes.mann;
  if (mode === "linear_mk_mod") return votes.linear && mkMod;
  if (mode === "theil_mk_mod") return votes.theil && mkMod;
  if (mode === "linear_theil_mk_mod") return votes.linear && votes.theil && mkMod;
  if (mode === "all_three") return votes.linear && votes.theil && votes.mann;
  return false;
}

function groundwaterTrendPointCount(critical = {}) {
  const n = Number(critical.pointCount ?? critical.usableWeeklyValues ?? critical.weeklyPointsUsed ?? 0);
  return Number.isFinite(n) ? n : 0;
}

function hasGroundwaterTrendEvidence(critical = {}) {
  if (typeof critical.hasTrendEvidence === "boolean") return critical.hasTrendEvidence;
  const linear = Number(critical.linearSlopeFtPerWeek);
  const theil = Number(critical.senSlopeFtPerWeek);
  return groundwaterTrendPointCount(critical) >= GROUNDWATER_MIN_SLOPE_WEEKS && (Number.isFinite(linear) || Number.isFinite(theil));
}

function isGroundwaterRiseWard(critical = {}) {
  return critical.dashboardMapCategory === "Confirmed groundwater rise"
    || critical.dashboardMapCategory === "Possible groundwater rise"
    || critical.groundwaterDirection === "Improving"
    || critical.groundwaterDirection === "Possible improvement"
    || critical.groundwaterRiseOverride === "Yes";
}

function groundwaterWardStatusKey(wardNo) {
  const critical = criticalGroundwaterByNo.get(normalizeWardNo(wardNo));
  if (!critical) return "none";
  if (isGroundwaterRiseWard(critical)) return "rise";
  if (selectedGroundwaterMethodIsCritical(critical)) return "critical";
  if (hasGroundwaterTrendEvidence(critical)) return "stable";
  return "none";
}

function isAnalysisLens(value) {
  return ["groundwater", "volumetric_deficit", "extraction", "pumping_stress", "consumption", "specific_capacity", "current_stress", "water_level_class"].includes(value);
}

function pumpingWardSummaryForNo(wardNo) {
  const raw = pumpingPerformanceWardSummaryByNo.get(normalizeWardNo(wardNo));
  if (!raw) return null;
  const totalPumpedVolumeM3 = numOrNull(raw.totalPumpedVolumeM3);
  const medianSpecificCapacityScaled = numOrNull(raw.medianSpecificCapacityScaled);
  const medianNormalizedDrawdownFtPerM3 = numOrNull(raw.medianNormalizedDrawdownFtPerM3);
  const extractionP75M3 = numOrNull(pumpingPerformanceWardThresholds.extractionP75M3);
  const specificCapacityP25Scaled = numOrNull(pumpingPerformanceWardThresholds.specificCapacityP25Scaled);
  const normalizedDrawdownP75FtPerM3 = numOrNull(pumpingPerformanceWardThresholds.normalizedDrawdownP75FtPerM3);
  return {
    ...raw,
    totalPumpedVolumeM3,
    medianSpecificCapacityScaled,
    medianNormalizedDrawdownFtPerM3,
    criticalByExtraction: totalPumpedVolumeM3 != null && extractionP75M3 != null && totalPumpedVolumeM3 >= extractionP75M3,
    criticalBySpecificCapacity: medianSpecificCapacityScaled != null && specificCapacityP25Scaled != null && medianSpecificCapacityScaled <= specificCapacityP25Scaled,
    highNormalizedDrawdown: medianNormalizedDrawdownFtPerM3 != null && normalizedDrawdownP75FtPerM3 != null && medianNormalizedDrawdownFtPerM3 >= normalizedDrawdownP75FtPerM3,
  };
}

function isPreviousConsumptionCriticalWard(wardNo) {
  const normalized = normalizeWardNo(wardNo);
  if (PREVIOUS_CONSUMPTION_CRITICAL_WARDS.has(normalized)) return true;
  const c = criticalGroundwaterByNo.get(normalizeWardNo(wardNo));
  return isYes(c?.previousCriticalWard) || isYes(c?.oldConsumptionNoGroundwaterData);
}

function wardVolumetricDeficit(wardNo) {
  const local = volumetricDeficitByNo.get(normalizeWardNo(wardNo));
  const feature = wards?.features?.find(f => normalizeWardNo(f.properties.ward_no) === normalizeWardNo(wardNo));
  if (local) {
    const slope = numOrNull(local.slopeFtPerWeek);
    const durationDays = Math.max(numOrNull(local.durationDays) || 0, 30);
    const areaKm2 = Number(feature?.properties?.area_km2 ?? 8);
    if (!Number.isFinite(slope) || slope <= 0) return { ...local, deficitMl: 0, deficitM3: 0, deficitTankers: 0 };
    const totalDropM = slope * (durationDays / 7) * 0.3048;
    const deficitM3 = areaKm2 * 1000000 * totalDropM * 0.05;
    return {
      ...local,
      deficitMl: deficitM3 / 1000,
      deficitM3,
      deficitTankers: deficitM3 / 12,
      durationDays,
    };
  }
  const ward = criticalGroundwaterByNo.get(normalizeWardNo(wardNo));
  const rawMl = ward?.volumetric_deficit_ml ?? ward?.volumetricDeficitMl;
  if (rawMl != null && Number.isFinite(Number(rawMl)) && Number(rawMl) > 0) return { deficitMl: Number(rawMl) };
  const slope = Number(ward?.senSlopeFtPerWeek ?? ward?.linearSlopeFtPerWeek ?? 0);
  if (!Number.isFinite(slope) || slope <= 0) return { deficitMl: 0 };
  const areaKm2 = Number(feature?.properties?.area_km2 ?? 8);
  const pointCount = Number(ward?.usableWeeklyValues ?? ward?.pointCount ?? 8);
  const durationDays = Math.max(pointCount * 7, 30);
  const totalDropM = slope * (durationDays / 7) * 0.3048;
  return { deficitMl: (areaKm2 * 1000000 * totalDropM * 0.05) / 1000 };
}

function overallCriticalLensFlags(wardNo) {
  const pumping = pumpingWardSummaryForNo(wardNo);
  const vd = wardVolumetricDeficit(wardNo);
  return {
    groundwater: groundwaterWardStatusKey(wardNo) === "critical",
    volumetric_deficit: vd.deficitMl >= 10,
    extraction: Boolean(pumping?.criticalByExtraction),
    pumping_stress: Boolean(pumping?.highNormalizedDrawdown),
    specific_capacity: Boolean(pumping?.criticalBySpecificCapacity),
  };
}

function commonLensHitCount(wardNo) {
  return Object.values(overallCriticalLensFlags(wardNo)).filter(Boolean).length;
}

function commonLensThreshold() {
  const input = document.getElementById("common-threshold");
  const raw = Number(input?.value ?? 2);
  const value = Number.isFinite(raw) ? Math.round(raw) : 2;
  return Math.max(1, Math.min(5, value));
}

function mapAnalysisWardStatusKey(wardNo) {
  if (currentLens === "consumption") return isPreviousConsumptionCriticalWard(wardNo) ? "critical" : "none";
  if (currentLens === "volumetric_deficit") {
    const vd = wardVolumetricDeficit(wardNo);
    return vd.deficitMl >= 10 ? "critical" : vd.deficitMl > 0 ? "stable" : "none";
  }
  if (currentLens === "extraction") {
    const pumping = pumpingWardSummaryForNo(wardNo);
    if (!pumping) return "none";
    return pumping.criticalByExtraction ? "critical" : "stable";
  }
  if (currentLens === "current_stress") {
    const cs = currentStressByNo.get(normalizeWardNo(wardNo));
    if (!cs) return "none";
    const cat = cs.stressCategory || "";
    if (cat.startsWith("Critical")) return "critical";
    if (cat.startsWith("Elevated")) return "rise";
    if (cat.startsWith("Normal") || cat.startsWith("Below")) return "stable";
    return "none";
  }
  if (currentLens === "specific_capacity") {
    const pumping = pumpingWardSummaryForNo(wardNo);
    if (!pumping) return "none";
    return pumping.criticalBySpecificCapacity ? "critical" : "stable";
  }
  if (currentLens === "pumping_stress") {
    const pumping = pumpingWardSummaryForNo(wardNo);
    if (!pumping) return "none";
    return pumping.highNormalizedDrawdown ? "critical" : "stable";
  }
  if (currentLens === "water_level_class") {
    const wlc = waterLevelClassByNo.get(normalizeWardNo(wardNo));
    if (!wlc) return "none";
    const band = wlc.dominantBand || "";
    if (band.startsWith("Very Deep")) return "critical";
    if (band.startsWith("Deep")) return "rise";
    if (band.startsWith("Moderate")) return "stable";
    if (band.startsWith("Shallow")) return "stable";
    return "none";
  }
  return groundwaterWardStatusKey(wardNo);
}

function groundwaterMethodLabel() {
  return "Sen slope + Mann-Kendall (Hamed-Rao)";
}

function analysisLensLabel(value = currentLens) {
  return ({
    groundwater: "Groundwater Decline",
    volumetric_deficit: "High Volumetric Deficit (ML)",
    extraction: "High Extraction",
    pumping_stress: "Volume-normalized Drawdown",
    consumption: "Previous Consumption Criticality",
    specific_capacity: "Low Specific Capacity",
    current_stress: "Current Stress Percentile",
    water_level_class: "Water Level Classification",
    coverage: "Sensor Coverage",
    readings: "Reading Load",
  })[value] || "Groundwater Decline";
}

function analysisCriticalLabel(value = currentLens) {
  return ({
    groundwater: "Critical: GW Decline",
    volumetric_deficit: "Critical: High Volumetric Loss",
    extraction: "Critical: High Extraction",
    pumping_stress: "Critical: High Volume-normalized Drawdown",
    consumption: "Previous Consumption Critical",
    specific_capacity: "Critical: Low Specific Capacity",
    current_stress: "Critical: Currently deep vs own history",
    water_level_class: "Very Deep: >500 ft",
  })[value] || "Critical ward";
}

function stableStatusLabel(value = currentLens) {
  if (value === "groundwater") return "No strong trend";
  if (value === "current_stress") return "Normal / recovered";
  if (value === "water_level_class") return "Shallow / Moderate (<250 ft)";
  if (["extraction", "pumping_stress", "specific_capacity", "volumetric_deficit"].includes(value)) return "Below threshold";
  return "No strong trend";
}

function groundwaterWardSummary(wardNo) {
  const c = criticalGroundwaterByNo.get(normalizeWardNo(wardNo));
  if (!c) return null;
  const slope = Number.isFinite(Number(c.senSlopeFtPerWeek)) ? Number(c.senSlopeFtPerWeek) : Number(c.linearSlopeFtPerWeek);
  return {
    status: groundwaterWardStatusKey(wardNo),
    category: c.dashboardMapCategory || c.groundwaterStatus || "Not classified",
    direction: c.groundwaterDirection || "Not computed",
    reason: c.updateReason || c.skippedReasonDetails || "",
    points: groundwaterTrendPointCount(c),
    slope: Number.isFinite(slope) ? slope : null,
    previousCritical: isPreviousConsumptionCriticalWard(wardNo),
  };
}

function panelWidthPx() {
  // Match style.css: .detail { min-width: 480px; width: 55vw }.
  // We compute from window width so this works even when the panel is still
  // hidden at fit-time (openWardDetail runs AFTER setSelectedWard).
  const vw = window.innerWidth || document.documentElement.clientWidth || 1200;
  return Math.max(Math.round(vw * 0.55), 480);
}
