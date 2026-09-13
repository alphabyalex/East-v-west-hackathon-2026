"use strict";
const $ = id => document.getElementById(id);
const number = value => Number(value).toLocaleString("en-US");
const token = document.querySelector('meta[name="workspace-token"]').content;
let state = {datasets: [], runs: [], job: null};
let pollTimer, dataVersion = 0, runVersion = 0, submitting = false;
let activeRun = null;
let scanVersion = 0, siteReportVersion = 0;
let activeSiteReport = null;

async function request(path, options = {}) {
  const response = await fetch(path, {...options, signal: AbortSignal.timeout(20000)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
  return data;
}
function error(message = "") {
  $("error").textContent = message;
  $("error").hidden = !message;
}
function options(id, items, preferred) {
  const select = $(id), previous = preferred || select.value;
  select.replaceChildren(...items.map(item => {
    const option = document.createElement("option");
    option.value = item.id; option.textContent = item.label;
    return option;
  }));
  if (items.some(item => item.id === previous)) select.value = previous;
  select.disabled = !items.length;
}
function datasetLabel(dataset) {
  return `${dataset.area || dataset.name} · ${number(dataset.hours)} h`;
}
function locations(datasetId, selectId) {
  const dataset = state.datasets.find(item => item.id === $(datasetId).value);
  options(selectId, (dataset?.locations || []).map(id => ({id, label: id})));
}
function buttons() {
  const busy = submitting || state.job?.status === "running";
  $("site-search-submit").disabled = busy;
  $("site-report-submit").disabled = busy || !$("site-scan-select").value || !$("site-candidate").options.length;
  $("site-signals-submit").disabled = busy || activeSiteReport?.status !== "research_modeled_exposure" || !!activeSiteReport?.warning_signs;
  $("weather-submit").disabled = busy || !$("weather-dataset").value;
  $("train-submit").disabled = busy || !$("train-dataset").value || !$("evidence").files.length || $("label-ref").value.trim().length < 5;
  $("hours-submit").disabled = busy || !activeRun;
  $("annual-submit").disabled = busy || !activeRun?.hours || !Object.values(activeRun.hours.locations).every(info => info.annual_simulation_ready);
}
function jobView() {
  const job = state.job;
  $("job-panel").hidden = !job;
  if (!job) { buttons(); return; }
  $("job-title").textContent = job.kind === "weather" ? "Temperature preparation" : job.kind === "hours" ? "Exposure hours calculation" : "Training & evaluation";
  if (job.kind.startsWith("site-")) $("job-title").textContent = job.kind === "site-scan" ? "Location search" : "Location data scan & report";
  $("job-badge").textContent = job.status.toUpperCase();
  $("job-status").textContent = job.status === "running" ? "Offline job running" : `Last job ${job.status}`;
  $("job-message").textContent = job.status === "running" ? "Working in the background. You can inspect cached data while this runs." : job.status === "succeeded" ? "Finished. The saved output is available above." : "The job stopped. See the log below, correct the input, and try again. Successful downloads remain cached.";
  const log = $("job-log"), nearEnd = log.scrollHeight - log.scrollTop - log.clientHeight < 80;
  log.textContent = job.log || "Starting offline pipeline…";
  if (nearEnd) log.scrollTop = log.scrollHeight;
  buttons();
}
async function refresh(preferredDataset = "", preferredRun = "") {
  clearTimeout(pollTimer);
  state = await request("/api/state");
  $("data-status").textContent = state.datasets.length ? `${number(state.datasets.length)} prepared datasets` : "No prepared data found";
  $("model-status").textContent = state.runs.length ? `${state.runs.length} saved research run${state.runs.length === 1 ? "" : "s"}` : "Awaiting reviewed event labels";
  const all = [...state.datasets].sort((a, b) => b.temperature_hours - a.temperature_hours);
  options("view-dataset", all.map(item => ({id: item.id, label: datasetLabel(item)})), preferredDataset);
  options("train-dataset", all.filter(item => !item.event_hours).map(item => ({id: item.id, label: datasetLabel(item)})), preferredDataset);
  options("weather-dataset", all.filter(item => !item.temperature_hours).map(item => ({id: item.id, label: datasetLabel(item)})));
  locations("view-dataset", "view-location"); locations("weather-dataset", "weather-location");
  $("no-model").hidden = !!state.runs.length;
  $("model-results").hidden = !state.runs.length;
  options("run-select", state.runs.map(item => ({id: item.id, label: item.name})), preferredRun);
  jobView();
  const completed = state.job?.status === "succeeded" ? state.job : null;
  options("site-scan-select", (state.site_scans || []).map(row => ({id: row.id, label: row.name})), completed?.kind === "site-scan" ? completed.result_id : "");
  options("site-report-select", (state.site_reports || []).map(row => ({id: row.id, label: row.name + " · " + row.id.split("/").pop()})), ["site-report", "site-transfer", "site-signals"].includes(completed?.kind) ? completed.result_id : "");
  await Promise.all([showDataset(), showRun(), showSiteScan(), showSiteReport()]);
  if (state.job?.status === "running") pollTimer = setTimeout(pollJob, 1500);
}
async function pollJob() {
  try {
    const next = await request("/api/state");
    state.job = next.job;
    jobView();
    if (next.job?.status === "running") pollTimer = setTimeout(pollJob, 1500);
    else await refresh(next.job?.kind === "weather" && next.job.status === "succeeded" ? next.job.result_id : "", ["train", "hours"].includes(next.job?.kind) && next.job.status === "succeeded" ? next.job.result_id : "");
  } catch (e) {
    error(`Cannot reach the workspace: ${e.message} Use Refresh files to reconnect.`);
  }
}

function chart(id, rows, field, label, isLoad = false) {
  const container = $(id);
  container.replaceChildren();
  const available = rows.filter(row => Number.isFinite(row[field]));
  if (!available.length) {
    const empty = document.createElement("div"); empty.className = "chart-empty";
    empty.textContent = field === "temperature_c" ? "Prepare an area's temperature data to view this chart." : "No observations available.";
    container.append(empty); return;
  }
  const svgNS = "http://www.w3.org/2000/svg";
  const node = (name, attributes, text) => {
    const element = document.createElementNS(svgNS, name);
    Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, value));
    if (text !== undefined) element.textContent = text;
    return element;
  };
  const width = 760, height = isLoad ? 150 : 210, left = 61, right = width - 15, top = 20, bottom = height - 28;
  const times = rows.map(row => Date.parse(row.timestamp_utc));
  const first = Math.min(...times), last = Math.max(...times);
  let min = Math.min(...available.map(row => row[field])), max = Math.max(...available.map(row => row[field]));
  const pad = (max - min) * .1 || 1; min -= pad; max += pad;
  const x = t => left + (t - first) / (last - first || 1) * (right - left);
  const y = value => bottom - (value - min) / (max - min) * (bottom - top);
  const svg = node("svg", {viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": label, preserveAspectRatio: "none"});
  svg.append(node("title", {}, label));
  for (let i = 0; i < 4; i++) {
    const value = min + i / 3 * (max - min), position = y(value);
    svg.append(node("line", {x1: left, x2: right, y1: position, y2: position}));
    svg.append(node("text", {x: left - 10, y: position + 4, "text-anchor": "end"}, isLoad ? Math.round(value).toLocaleString("en-US") : value.toFixed(1)));
  }
  for (let i = 0; i < 5; i++) {
    const timestamp = first + i / 4 * (last - first);
    const date = new Date(timestamp).toLocaleDateString("en-US", {month: "short", day: "numeric", timeZone: "UTC"});
    svg.append(node("text", {x: x(timestamp), y: height - 7, "text-anchor": i === 0 ? "start" : i === 4 ? "end" : "middle"}, date));
  }
  let path = "", gap = true;
  rows.forEach(row => {
    if (!Number.isFinite(row[field])) { gap = true; return; }
    path += `${gap ? "M" : "L"}${x(Date.parse(row.timestamp_utc)).toFixed(2)},${y(row[field]).toFixed(2)} `;
    gap = false;
  });
  svg.append(node("path", {d: path, class: isLoad ? "load" : "temperature"}));
  container.append(svg);
}
async function showDataset() {
  const version = ++dataVersion, id = $("view-dataset").value;
  if (!id) return;
  const data = await request(`/api/dataset?id=${encodeURIComponent(id)}&location=${encodeURIComponent($("view-location").value)}`);
  if (version !== dataVersion) return;
  $("hours").textContent = number(data.summary.hours);
  $("temperature-hours").textContent = number(data.summary.temperature_hours);
  $("event-hours").textContent = number(data.summary.event_hours);
  $("data-range").textContent = `${data.location} · ${data.summary.start} → ${data.summary.end}`;
  $("data-source").textContent = `Source: ${data.source_ref} · Observed load hours: ${number(data.summary.load_hours)}`;
  $("data-provenance").textContent = JSON.stringify(data.sources, null, 2);
  chart("temperature-chart", data.daily, "temperature_c", "Daily mean historical temperature, Celsius");
  chart("load-chart", data.daily, "load_mw", "Daily mean SPP grid load, megawatts", true);
}
async function showRun() {
  const version = ++runVersion, id = $("run-select").value;
  activeRun = null; buttons();
  if (!id) return;
  const result = await request(`/api/run?id=${encodeURIComponent(id)}`);
  if (version !== runVersion) return;
  const card = result.card;
  activeRun = result;
  options("hours-location", Object.keys(result.hours?.locations || card.confidence || {}).map(id => ({id, label: id})));
  showHours(); buttons();
  $("model-version").textContent = `${card.model_version} · ${result.id}`;
  $("brier").textContent = card.test.brier_score.toFixed(4);
  $("baseline").textContent = card.baseline_test.brier_score.toFixed(4);
  $("test-hours").textContent = number(card.test.n_hours);
  $("confidence").textContent = Object.entries(card.confidence || {}).map(([location, value]) => `${location}: ${value.level} confidence · Ensemble agreement ${value.score.toFixed(3)} · ${number(value.n_similar_historical_hours)} similar historical hours`).join(". ") + ". Confidence reflects ensemble agreement and historical support, not a probability of correctness.";
  $("model-warnings").replaceChildren(...(card.warnings || []).map(warning => { const p = document.createElement("p"); p.textContent = warning; return p; }));
  $("downloads").replaceChildren(...result.downloads.map(file => {
    const link = document.createElement("a"); link.href = `/api/download?id=${encodeURIComponent(id)}&file=${encodeURIComponent(file)}`;
    link.textContent = file === "model.joblib" ? "Download model" : file; link.download = file; return link;
  }));
  $("model-report").textContent = result.report;
  $("model-card").textContent = JSON.stringify(card, null, 2);
}
function showHours() {
  const location = $("hours-location").value, info = activeRun?.hours?.locations[location];
  const share = Number($("site-exposure").value), fmt = value => Number(value).toLocaleString("en-US", {maximumFractionDigits: 1});
  $("site-exposure-value").textContent = `${Math.round(share * 100)}%`;
  $("hours-target").textContent = activeRun?.card.policy.target_description || activeRun?.card.policy.label_ref || "";
  $("expected-hours").textContent = info ? fmt(info.expected_exposure_hours) : "—";
  $("site-hours").textContent = info ? fmt(info.expected_exposure_hours * share) : "—";
  $("missing-hours").textContent = info ? number(info.unscored_hours) : "—";
  $("hours-period").textContent = info ? `${info.start_utc} to ${info.end_exclusive_utc} (end exclusive). ${number(info.scored_hours)} scored hours. Expected hours sum hourly probabilities across this held-out period; this is not an annualized result or a future-date forecast.` : "Calculate hours from this run's saved probabilities.";
  $("hours-spread").textContent = info ? `Ensemble expected-hours range: ${fmt(info.member_expected_hours_min)}–${fmt(info.member_expected_hours_max)}. This measures model variation, not an outcome interval. Confidence: ${info.confidence.level}.` : "";
  $("ranked-hours").textContent = info ? JSON.stringify({source: activeRun.hours.ref, model: activeRun.hours.model_version, highest_scored_hours: info.highest_scored_hours, assumptions: activeRun.hours.limitations}, null, 2) : "";
  $("annual-status").textContent = info ? (info.annual_simulation_ready ? "Annual simulation uses historical seasonal conditions. Confidence is Low; future load growth and climate changes are not modeled." : info.annual_blockers.join(" ")) : "A scored-period summary is required before annual simulation.";
  $("annual-hours").replaceChildren(); $("contract-hours").textContent = "";
  const annual = activeRun?.annual?.locations[location];
  if (!annual) return;
  const table = document.createElement("table");
  const header = document.createElement("tr");
  ["Year", "System P50 h", "System P90 h", "System P99 h", "Assumed site P50 h"].forEach(text => {const cell = document.createElement("th"); cell.textContent = text; header.append(cell);});
  const head = document.createElement("thead"); head.append(header); table.append(head);
  const body = document.createElement("tbody");
  annual.by_year.forEach(row => {
    const tr = document.createElement("tr");
    [row.year_offset, fmt(row.p50_hours), fmt(row.p90_hours), fmt(row.p99_hours), fmt(row.p50_hours * share)].forEach(text => {const cell = document.createElement("td"); cell.textContent = text; tr.append(cell);});
    body.append(tr);
  }); table.append(body); $("annual-hours").append(table);
  const contract = activeRun.annual.contract?.locations[location];
  if (contract) $("contract-hours").textContent = `${contract.term_years}-year system total: P50 ${fmt(contract.p50_total_hours)} h, P90 ${fmt(contract.p90_total_hours)} h, P99 ${fmt(contract.p99_total_hours)} h. Assumed site P50: ${fmt(contract.p50_total_hours * share)} h. Term quantiles come from joint trials, not sums of yearly quantiles. Source: ${annual.model_version}, saved simulation trials.`;
}
async function submit(data) {
  submitting = true; buttons(); error();
  try {
    await request("/api/jobs", {method: "POST", headers: {"Content-Type": "application/json", "X-Workspace-Token": token}, body: JSON.stringify(data)});
    await refresh();
    $("job-panel").scrollIntoView({behavior: "smooth", block: "center"});
  } catch (e) { error(e.message); }
  finally { submitting = false; buttons(); }
}
async function showSiteScan() {
  const version = ++scanVersion, id = $("site-scan-select").value;
  if (!id) { options("site-candidate", []); buttons(); return; }
  const scan = await request(`/api/site-scan?id=${encodeURIComponent(id)}`);
  if (version !== scanVersion) return;
  options("site-candidate", scan.candidates.map((point, index) => ({id: String(index), label: `${point.name} (${point.latitude.toFixed(5)}, ${point.longitude.toFixed(5)})`})));
  buttons();
}
function siteTable(id, headers, rows) {
  const table = document.createElement("table"), head = document.createElement("thead"), body = document.createElement("tbody");
  const tr = document.createElement("tr");
  headers.forEach(value => { const th = document.createElement("th"); th.textContent = value; tr.append(th); });
  head.append(tr);
  rows.forEach(row => { const tr = document.createElement("tr"); row.forEach(value => { const td = document.createElement("td"); td.textContent = value; tr.append(td); }); body.append(tr); });
  table.append(head, body); $(id).replaceChildren(table);
}
async function showSiteReport() {
  const version = ++siteReportVersion, id = $("site-report-select").value;
  $("site-annual-table").replaceChildren(); $("site-hour-table").replaceChildren(); $("site-downloads").replaceChildren();
  activeSiteReport = null; $("signal-panel").hidden = true; buttons();
  if (!id) return;
  const report = await request(`/api/site-report?id=${encodeURIComponent(id)}`);
  if (version !== siteReportVersion) return;
  activeSiteReport = report; buttons();
  if (report.warning_signs) showSignalPatterns();
  $("site-report-text").textContent = report.markdown;
  const {markdown, ...details} = report;
  $("site-report-data").textContent = JSON.stringify(details, null, 2);
  for (const name of ["SITE_REPORT.html", "SITE_REPORT.md", "site_report.json"]) {
    const link = document.createElement("a"); link.className = "text-link"; link.textContent = `Download ${name}`;
    link.href = `/api/site-download?id=${encodeURIComponent(id)}&file=${encodeURIComponent(name)}`;
    $("site-downloads").append(link);
  }
  if (report.status === "research_transfer_exposure") {
    $("site-extra-title").textContent = "How well the model performed on areas excluded from training";
    $("site-extra-note").textContent = "Each listed area was excluded from fitting and calibration. Lower Brier scores are better; compare the model with its baseline. Hours refer to the held-out historical period, not one annual forecast.";
    const value = report.estimates, fmt = n => Number(n).toLocaleString("en-US", {maximumFractionDigits: 1});
    $("site-report-summary").textContent = `${report.location.name}: ${fmt(value.annual_expected_hours)} expected regional proxy hours per stationary comparison year; ${fmt(value.assumed_site_annual_hours)} assumed site hours at ${Math.round(report.assumptions.site_exposure * 100)}% exposure. Confidence: Low. Learned from four areas with separate load histories. Local cutoff records and query-area load are unavailable. No P50/P90/P99 outcome quantiles are inferred for this transferred estimate.`;
    siteTable("site-annual-table", ["Expected proxy h/year", "Assumed site h/year", "Conditional MWh/year", `${report.assumptions.years}-year assumed site h`], [[value.annual_expected_hours, value.assumed_site_annual_hours, value.conditional_annual_mwh, value.assumed_site_term_hours].map(fmt)]);
    siteTable("site-hour-table", ["Area excluded from training", "Model Brier", "Baseline Brier", "Expected proxy h", "Observed proxy h"], report.cross_area_validation.map(row => [row.area, row.metrics.brier_score.toFixed(4), row.baseline.brier_score.toFixed(4), fmt(row.expected_proxy_hours), row.observed_proxy_hours]));
    return;
  }
  if (report.status !== "research_modeled_exposure") { $("site-report-summary").textContent = report.message; return; }
  $("site-extra-title").textContent = "Highest historical hours with local conditions (UTC)";
  $("site-extra-note").textContent = "Probabilities refer to the SPP high-demand proxy. Weather and grid conditions shown are from the preceding hour, as used by the model.";
  const fmt = value => value === null ? "Unknown" : Number(value).toLocaleString("en-US", {maximumFractionDigits: 1});
  const first = report.scenario.annual[0];
  $("site-report-summary").textContent = `${report.location.name}: year-one assumed site exposure P50 ${fmt(first.site_p50_hours)} h, P90 ${fmt(first.site_p90_hours)} h, P99 ${fmt(first.site_p99_hours)} h. Confidence: Low. Site-exposure assumption: ${Math.round(report.assumptions.site_exposure * 100)}%. Grid target: high SPP demand. ${report.model_reused ? "Reused this location's fitted model." : "Fitted using this location's weather."} Historical inputs: 2019–2024; no future-date forecast.`;
  siteTable("site-annual-table", ["Year", "System P50 h", "System P90 h", "System P99 h", "Assumed site P50 h", "Site P90 h", "Site P99 h", "Conditional P50 MWh"], report.scenario.annual.map(row => [row.year, ...["system_p50_hours", "system_p90_hours", "system_p99_hours", "site_p50_hours", "site_p90_hours", "site_p99_hours", "conditional_p50_mwh"].map(key => fmt(row[key]))]));
  siteTable("site-hour-table", ["Historical hour UTC", "Proxy probability", "Prior °C", "Prior SPP load MW", "Prior wind MW", "Prior solar MW"], report.historical_highest_hours.map(row => [row.timestamp_utc, `${fmt(row.probability * 100)}%`, ...["prior_temperature_c", "prior_load_mw", "prior_wind_mw", "prior_solar_mw"].map(key => fmt(row[key]))]));
}
$("site-search-form").addEventListener("submit", event => { event.preventDefault(); submit({kind: "site-scan", query: $("site-query").value}); });
$("site-report-form").addEventListener("submit", event => {
  event.preventDefault();
  submit({kind: $("site-method").value, scan: $("site-scan-select").value, candidate: Number($("site-candidate").value), load_mw: Number($("site-load").value), conditional_share: Number($("site-conditional").value) / 100, site_exposure: Number($("report-exposure").value), years: Number($("site-years").value), confirm_spp: $("site-confirm-spp").checked});
});
function showSignalPatterns() {
  const signs = activeSiteReport.warning_signs;
  $("signal-panel").hidden = false;
  const pct = n => n === null ? "Unknown" : `${(n * 100).toFixed(1)}%`;
  const fmt = n => Number(n).toLocaleString("en-US", {maximumFractionDigits: 1});
  const labels = {temperature_c_lag_1h: ["Temperature", "°C"], load_mw_lag_1h: ["SPP load", "MW"], load_change_1h: ["Hourly load increase", "MW"], net_load_lag_1h: ["SPP load minus wind/solar", "MW"], wind_mw_lag_1h: ["SPP wind generation", "MW"]};
  siteTable("signal-patterns", ["Combination", "Prior-hour comparison rule", "Held-out stress frequency", "Baseline comparison", "Area-hours / days"], signs.patterns.map(row => [row.pattern, row.criteria.map(c => `${labels[c.feature]?.[0] || c.feature} ${c.operator} ${fmt(c.value)} ${labels[c.feature]?.[1] || ""}`).join(" and "), pct(row.stress_fraction), row.difference_percentage_points === null ? "Unknown" : `${row.difference_percentage_points >= 0 ? "+" : ""}${fmt(row.difference_percentage_points)} percentage points; baseline ${pct(row.baseline_fraction)}`, `${row.matched_area_hours} / ${row.distinct_days} (${row.support})`]));
  options("signal-hour", signs.hours.map((row, i) => ({id: String(i), label: `${row.timestamp_utc} · proxy probability ${pct(row.probability)}`})));
  showSignalHour();
}
function showSignalHour() {
  const row = activeSiteReport?.warning_signs?.hours[Number($("signal-hour").value)];
  if (!row) return;
  const fmt = n => n === null ? "Unknown" : Number(n).toLocaleString("en-US", {maximumFractionDigits: 1});
  $("signal-summary").textContent = `Previous hour: ${fmt(row.prior_temperature_c)} °C local temperature and ${fmt(row.prior_spp_load_mw)} MW SPP load. Proxy probability ${(row.probability * 100).toFixed(1)}%. Confidence: Low. Factors are ordered by contribution magnitude.`;
  siteTable("signal-drivers", ["Factor", "Effect on this model prediction", "Models agreeing on direction", "Input coverage"], row.drivers.map(item => [item.factor, item.direction, `${Math.round(item.member_direction_agreement * 100)}%`, item.missing_input_count ? `${item.missing_input_count} missing history values` : "Complete history"]));
  siteTable("signal-analogues", ["Reference area", "Hour UTC", "Prior °C", "Prior SPP MW", "Recorded high-demand proxy"], row.similar_training_hours.map(item => [item.area, item.timestamp_utc, fmt(item.prior_temperature_c), fmt(item.prior_spp_load_mw), item.observed_stress_proxy ? "Yes" : "No"]));
  if (!row.similar_training_hours.length) $("signal-analogues").textContent = "No close complete training examples within the comparison distance. Treat this hour as weakly supported.";
}
$("signal-hour").addEventListener("change", showSignalHour);
$("site-signals-submit").addEventListener("click", () => submit({kind: "site-signals", report: $("site-report-select").value}));
$("report-exposure").addEventListener("input", () => { $("report-exposure-value").textContent = `${Math.round(Number($("report-exposure").value) * 100)}%`; });
$("site-scan-select").addEventListener("change", () => showSiteScan().catch(e => error(e.message)));
$("site-report-select").addEventListener("change", () => showSiteReport().catch(e => error(e.message)));
$("weather-form").addEventListener("submit", event => {
  event.preventDefault();
  submit({kind: "weather", dataset: $("weather-dataset").value, location: $("weather-location").value, area: $("area").value});
});
$("train-form").addEventListener("submit", async event => {
  event.preventDefault();
  try {
    const file = $("evidence").files[0];
    if (!file || file.size > 5000000) throw new Error("Choose an event CSV under 5 MB.");
    await submit({kind: "train", dataset: $("train-dataset").value, evidence_csv: await file.text(), label_ref: $("label-ref").value});
  } catch (e) { error(e.message); }
});
$("weather-dataset").addEventListener("change", () => { locations("weather-dataset", "weather-location"); buttons(); });
$("view-dataset").addEventListener("change", () => { locations("view-dataset", "view-location"); showDataset().catch(e => error(e.message)); });
$("view-location").addEventListener("change", () => showDataset().catch(e => error(e.message)));
$("run-select").addEventListener("change", () => showRun().catch(e => error(e.message)));
$("hours-location").addEventListener("change", showHours);
$("site-exposure").addEventListener("input", showHours);
$("hours-submit").addEventListener("click", () => submit({kind: "hours", run: activeRun.id, years: 0}));
$("annual-submit").addEventListener("click", () => submit({kind: "hours", run: activeRun.id, years: Number($("contract-years").value)}));
$("evidence").addEventListener("change", buttons); $("label-ref").addEventListener("input", buttons);
$("train-dataset").addEventListener("change", buttons);
$("refresh").addEventListener("click", () => { error(); refresh().catch(e => error(e.message)); });
refresh().catch(e => error(`Could not load the workspace: ${e.message}`));
