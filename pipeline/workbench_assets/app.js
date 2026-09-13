"use strict";
const $ = id => document.getElementById(id);
const format = value => typeof value === "number" && Number.isFinite(value)
  ? value.toLocaleString("en-US", {maximumFractionDigits: 1}) : "Unavailable";
const normalizeQuery = query => query.trim().toLowerCase();
const sameInputs = (a, b) => JSON.stringify(a) === JSON.stringify(b);
let state = {site_reports: [], job: null}, activeScan = null, activeReport = null;
let displayedInputs = null, pendingSearch = null, ownReport = null;
let posting = false, loadingReport = false, reportVersion = 0, pollTimer = null, refreshPromise = null;
const handledJobs = new Set();

function summarizeReport(report) {
  const settings = report.assumptions;
  if (report.status === "research_transfer_exposure") {
    const e = report.estimates;
    return {annual: e.assumed_site_annual_hours, regional: e.annual_expected_hours,
      term: e.assumed_site_term_hours, energy: e.conditional_annual_mwh,
      annualLabel: "Estimated site exposure per year",
      regionalLabel: "Regional high-demand hours per year",
      termLabel: "Estimated site exposure over " + settings.years + " years",
      energyLabel: "Estimated energy exposure per year",
      note: "Expected hours in a comparison year, with your " + format(settings.site_exposure * 100) + "% site-exposure assumption applied.",
      calculation: "Site hours = regional high-demand hours × site-exposure assumption. Energy exposure = site hours × facility MW × flexible share. Full-term hours = annual site hours × years. Annual expectations use historical monthly conditions and a 365-day comparison year."};
  }
  if (report.status === "research_modeled_exposure") {
    const first = report.scenario.annual[0];
    return {annual: first.site_p50_hours, regional: first.system_p50_hours,
      term: report.scenario.site_term.p50_total_hours, energy: first.conditional_p50_mwh,
      annualLabel: "Typical site exposure in year 1 (P50)",
      regionalLabel: "Typical SPP high-demand hours in year 1 (P50)",
      termLabel: "Typical site exposure over " + settings.years + " years (P50)",
      energyLabel: "Typical energy exposure in year 1 (P50)",
      note: "Saved annual-scenario result. P50 is the middle simulated outcome, with your site-exposure assumption applied. New estimates use the regional comparison model.",
      calculation: "This saved report uses the earlier SPP-wide annual simulation. P50 means half of simulated outcomes are at or below this value; it is not a guarantee. Full-term P50 comes from joint multi-year simulations, not a sum of yearly P50 values. Energy exposure applies facility MW and flexible share."};
  }
  return null;
}
function inputFacts(report) {
  const s = report.assumptions;
  return [["Facility power demand", format(s.load_mw) + " MW"],
    ["Flexible share of power", format(s.conditional_share * 100) + "%"],
    ["Site-exposure assumption", format(s.site_exposure * 100) + "%"],
    ["Years estimated", String(s.years)],
    ["Location coordinates", report.location.latitude + ", " + report.location.longitude]];
}
function sourceReferences(report) {
  const refs = new Map();
  function visit(value) {
    if (!value || typeof value !== "object") return;
    if (typeof value.ref === "string") {
      try {
        const url = new URL(value.ref);
        if (["https:", "http:"].includes(url.protocol)) {
          const period = value.request_parameters?.start_date;
          const label = (value.dataset || url.hostname) + (period ? " · from " + period : "");
          refs.set(url.href, label);
        }
      } catch (_) { /* Descriptive references remain in the source download. */ }
    }
    Object.values(value).forEach(visit);
  }
  [report.training_sources, report.query_weather, report.data?.weather,
    report.data?.grid_sources, report.coverage?.source, report.geocoding].forEach(visit);
  return [...refs].map(([url, label]) => ({url, label}));
}
function estimateText(report) {
  const s = summarizeReport(report);
  if (!s) throw new Error("This location has no completed hours estimate.");
  return ["Fluxline — " + report.location.name, "Saved: " + report.created_utc,
    "Confidence: Low. Research estimate of modeled exposure.",
    "", s.annualLabel + ": " + format(s.annual) + " hours/year",
    s.termLabel + ": " + format(s.term) + " hours",
    s.energyLabel + ": " + format(s.energy) + " MWh/year",
    s.regionalLabel + ": " + format(s.regional) + " hours/year", "", s.note,
    "", "Inputs used:", ...inputFacts(report).map(row => row.join(": ")),
    "", s.calculation, "Model: " + report.model_version,
    "", "Actual power cutoffs and their dates are not predicted. Local transmission constraints and site interruption records are unavailable. Uses historical SPP data from 2019–2024; this is not a live forecast.",
    "Values above are rounded to one decimal. The source JSON retains full precision.",
    "", "Data sources:", ...sourceReferences(report).map(row => row.label + ": " + row.url),
    "", "Limitations:", ...(report.limitations || [])].join("\n");
}
function readInputs() {
  return {query: $("location-query").value.trim(), load_mw: Number($("facility-mw").value),
    conditional_share: Number($("flexible-percent").value) / 100,
    site_exposure: Number($("site-exposure").value), years: Number($("term-years").value),
    confirm_spp: $("confirm-spp").checked,
    candidate: activeScan && $("location-candidate").value !== "" ? Number($("location-candidate").value) : null};
}
function setError(message = "") {
  $("error").textContent = message; $("error").hidden = !message;
}
function controls() {
  const busy = posting || loadingReport || state.job?.status === "running";
  $("input-fields").disabled = busy;
  $("saved-report").disabled = busy || !state.site_reports.length;
  $("estimate-submit").textContent = loadingReport ? "Loading saved estimate…" : busy ? "Preparing your estimate…" : "Estimate hours";
  $("result-panel").setAttribute("aria-busy", String(busy));
  $("exposure-value").textContent = format(Number($("site-exposure").value) * 100) + "%";
  $("stale-result").hidden = !activeReport || sameInputs(displayedInputs, readInputs());
}
async function request(path, options = {}) {
  const response = await fetch(path, {...options, signal: AbortSignal.timeout(20000)});
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "Request failed (" + response.status + ").");
  return body;
}
function replaceOptions(id, rows, preferred = "") {
  const select = $(id);
  select.replaceChildren(...rows.map(row => {
    const option = document.createElement("option");
    option.value = row.id; option.textContent = row.label; return option;
  }));
  if (rows.some(row => row.id === preferred)) select.value = preferred;
}
function savedLabel(row) {
  const match = /(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})/.exec(row.id);
  const date = match ? new Date(match[1] + "-" + match[2] + "-" + match[3] + "T" + match[4] + ":" + match[5] + ":" + match[6] + "Z").toLocaleString() : "";
  return row.name + (date ? " · " + date : "");
}
function facts(id, rows) {
  $(id).replaceChildren(...rows.map(([label, value]) => {
    const row = document.createElement("div"), dt = document.createElement("dt"), dd = document.createElement("dd");
    dt.textContent = label; dd.textContent = value; row.append(dt, dd); return row;
  }));
}
function resetLocation(keepConfirmation = false) {
  activeScan = null; $("candidate-block").hidden = true;
  $("location-candidate").required = false; $("location-candidate").replaceChildren();
  if (!keepConfirmation) { $("confirm-spp").checked = false; $("coverage-confirmation").hidden = true; }
}
function restoreInputs(report) {
  resetLocation(); $("location-query").value = report.location.name;
  if (report.assumptions) {
    const s = report.assumptions;
    $("facility-mw").value = s.load_mw; $("flexible-percent").value = s.conditional_share * 100;
    $("site-exposure").value = s.site_exposure; $("term-years").value = s.years;
    $("confirm-spp").checked = s.confirm_spp;
    $("coverage-confirmation").hidden = !s.confirm_spp;
  }
}
async function loadReport(id, restore = true, submittedInputs = null) {
  const version = ++reportVersion;
  loadingReport = true; controls();
  try {
  activeReport = null; $("estimate-result").hidden = true; $("confidence").hidden = true;
  $("coverage-result").hidden = true; $("stale-result").hidden = true;
  if (!id) { $("empty-result").hidden = false; return; }
  const report = await request("/api/site-report?id=" + encodeURIComponent(id));
  if (version !== reportVersion) return;
  if (restore) restoreInputs(report);
  activeReport = report; displayedInputs = submittedInputs || readInputs();
  $("empty-result").hidden = true; $("saved-report").value = id;
  const values = summarizeReport(report);
  if (!values) {
    $("coverage-result").hidden = false;
    $("coverage-result").textContent = report.location.name + ": " + (report.message || "No hours estimate is available.");
    $("coverage-confirmation").hidden = report.coverage?.status !== "unverified";
    controls(); return;
  }
  $("estimate-result").hidden = false; $("confidence").hidden = false;
  $("result-location").textContent = report.location.name;
  $("result-date").textContent = "Saved " + new Date(report.created_utc).toLocaleString();
  for (const [id, key] of [["annual-hours", "annual"], ["term-hours", "term"], ["annual-energy", "energy"], ["regional-hours", "regional"]]) $(id).textContent = format(values[key]);
  for (const [id, key] of [["annual-label", "annualLabel"], ["term-label", "termLabel"], ["energy-label", "energyLabel"], ["regional-label", "regionalLabel"]]) $(id).textContent = values[key];
  $("annual-note").textContent = values.note; $("calculation").textContent = values.calculation;
  facts("result-inputs", inputFacts(report));
  facts("source-facts", [["Model", report.model_version], ["Grid history", "2019–2024"],
    ["Weather", "Historical local ERA5 temperature; not live weather"],
    ["Grid measurements", "SPP demand, wind and solar; local grid constraints unavailable"],
    ["Model artifact hash", report.input_hashes?.model || "Recorded in the source JSON"]]);
  $("source-links").replaceChildren(...sourceReferences(report).map(ref => {
    const item = document.createElement("li"), a = document.createElement("a");
    a.href = ref.url; a.textContent = ref.label; a.target = "_blank"; a.rel = "noopener noreferrer"; item.append(a); return item;
  }));
  $("source-limits").textContent = (report.limitations || []).join(" ");
  $("download-data").href = "/api/site-download?id=" + encodeURIComponent(id) + "&file=site_report.json";
  controls();
  } finally {
    if (version === reportVersion) { loadingReport = false; controls(); }
  }
}
function jobView() {
  const job = state.job;
  $("job-panel").hidden = !job;
  if (!job) return;
  $("job-log").textContent = job.log || "Starting…";
  if (job.status === "running") $("job-message").textContent = job.kind === "site-scan"
    ? "Finding your location…" : "Collecting data and calculating your estimate. Keep this app running.";
  else if (job.status === "failed") {
    $("job-message").textContent = "The estimate could not be completed. Check the processing details, then try again.";
    $("job-details").open = true;
  } else $("job-message").textContent = job.kind === "site-scan"
    ? "Location search finished. Confirm the matching location, then select Estimate hours."
    : "Finished. Your saved estimate is available on the right, or below on a small screen.";
}
async function postJob(payload) {
  posting = true; controls(); setError();
  try {
    const token = document.querySelector('meta[name="workspace-token"]').content;
    state.job = await request("/api/jobs", {method: "POST",
      headers: {"Content-Type": "application/json", "X-Workspace-Token": token}, body: JSON.stringify(payload)});
    jobView(); return state.job;
  } finally { posting = false; controls(); }
}
async function startReport(inputs) {
  const job = await postJob({kind: "site-transfer", scan: activeScan.id, candidate: inputs.candidate,
    load_mw: inputs.load_mw, conditional_share: inputs.conditional_share,
    site_exposure: inputs.site_exposure, years: inputs.years, confirm_spp: inputs.confirm_spp});
  ownReport = {id: job.id, inputs};
}
async function receiveSearch(job) {
  const pending = pendingSearch?.id === job.id ? pendingSearch : null;
  pendingSearch = null;
  const scan = await request("/api/site-scan?id=" + encodeURIComponent(job.result_id));
  if (normalizeQuery(scan.query) !== normalizeQuery($("location-query").value)) return;
  activeScan = {id: job.result_id, query: scan.query, candidates: scan.candidates};
  if (!scan.candidates.length) {
    resetLocation(); setError("No matching location was found. Try a city and state, or latitude and longitude."); return;
  }
  const multiple = scan.candidates.length > 1;
  replaceOptions("location-candidate", [...(multiple ? [{id: "", label: "Choose the correct location"}] : []),
    ...scan.candidates.map((p, i) => ({id: String(i), label: p.name + " (" + p.latitude + ", " + p.longitude + ")"}))]);
  $("candidate-block").hidden = !multiple; $("location-candidate").required = multiple;
  if (!multiple && pending) {
    const inputs = readInputs();
    // Continue only the specific search requested by this tab, with its captured assumptions.
    if (sameInputs({...pending.inputs, candidate: 0}, inputs)) await startReport(inputs);
  } else if (multiple) $("job-message").textContent = "Several locations matched. Choose the correct one, then select Estimate hours.";
}
async function refresh(initial = false) {
  if (refreshPromise) return refreshPromise;
  refreshPromise = (async () => {
    clearTimeout(pollTimer);
    state = await request("/api/state");
    const previous = $("saved-report").value;
    replaceOptions("saved-report", state.site_reports.map(row => ({id: row.id, label: savedLabel(row)})), previous);
    $("saved-control").hidden = !state.site_reports.length;
    jobView(); controls();
    const job = state.job;
    if (initial) {
      if (job && job.status !== "running") handledJobs.add(job.id);
      if (state.site_reports.length) await loadReport($("saved-report").value);
    } else if (job && job.status !== "running" && !handledJobs.has(job.id)) {
      handledJobs.add(job.id);
      if (job.status === "succeeded") {
        if (job.kind === "site-scan") await receiveSearch(job);
        else if (["site-transfer", "site-report", "site-signals"].includes(job.kind)) {
          const own = ownReport?.id === job.id ? ownReport : null;
          await loadReport(job.result_id, !own, own?.inputs);
          if (own && activeReport && summarizeReport(activeReport)) $("result-panel").scrollIntoView({block: "start"});
          ownReport = null;
        }
      } else { pendingSearch = null; ownReport = null; }
    }
    controls();
    if (state.job?.status === "running") pollTimer = setTimeout(() => refresh().catch(connectionError), 1500);
  })();
  try { return await refreshPromise; } finally { refreshPromise = null; }
}
function connectionError(e) {
  setError("Could not reach the app. Select Refresh results to reconnect. " + e.message);
}
async function estimate(event) {
  event.preventDefault();
  if (posting || loadingReport || state.job?.status === "running" || !$("estimate-form").reportValidity()) return;
  setError();
  try {
    const inputs = readInputs();
    if (activeScan && normalizeQuery(activeScan.query) === normalizeQuery(inputs.query) && inputs.candidate !== null) {
      await startReport(inputs);
    } else {
      resetLocation(true);
      const captured = readInputs(), job = await postJob({kind: "site-scan", query: captured.query});
      pendingSearch = {id: job.id, inputs: captured};
    }
    await refresh();
  } catch (e) { setError(e.message); controls(); }
}
function saveEstimate() {
  if (!activeReport || !summarizeReport(activeReport)) return;
  const blob = new Blob([estimateText(activeReport)], {type: "text/plain;charset=utf-8"});
  const href = URL.createObjectURL(blob), link = document.createElement("a");
  link.href = href; link.download = "fluxline-" + activeReport.location.name.toLowerCase().replace(/[^a-z0-9]+/g, "-") + "-estimate.txt";
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(href), 1000);
}
async function initialize() {
  $("estimate-form").addEventListener("submit", estimate);
  $("estimate-form").addEventListener("input", event => {
    if (event.target.id === "location-query" || event.target.id === "location-candidate") {
      if (event.target.id === "location-query") resetLocation();
      else { $("confirm-spp").checked = false; $("coverage-confirmation").hidden = true; }
    }
    controls();
  });
  $("saved-report").addEventListener("change", () => loadReport($("saved-report").value).catch(e => setError(e.message)));
  $("refresh").addEventListener("click", () => { setError(); refresh().catch(connectionError); });
  $("save-estimate").addEventListener("click", saveEstimate);
  document.querySelectorAll("[data-sources]").forEach(button => button.addEventListener("click", () => {
    $("sources").open = true; $("sources").scrollIntoView({block: "nearest"});
  }));
  await refresh(true);
}
if (typeof document !== "undefined") document.addEventListener("DOMContentLoaded", () => initialize().catch(connectionError));
