"use strict";
const $ = id => document.getElementById(id);
const number = value => Number(value).toLocaleString("en-US");
const token = document.querySelector('meta[name="workspace-token"]').content;
let state = {datasets: [], runs: [], job: null};
let pollTimer, dataVersion = 0, runVersion = 0, submitting = false;

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
  $("weather-submit").disabled = busy || !$("weather-dataset").value;
  $("train-submit").disabled = busy || !$("train-dataset").value || !$("evidence").files.length || $("label-ref").value.trim().length < 5;
}
function jobView() {
  const job = state.job;
  $("job-panel").hidden = !job;
  if (!job) { buttons(); return; }
  $("job-title").textContent = job.kind === "weather" ? "Temperature preparation" : "Training & evaluation";
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
  await Promise.all([showDataset(), showRun()]);
  if (state.job?.status === "running") pollTimer = setTimeout(pollJob, 1500);
}
async function pollJob() {
  try {
    const next = await request("/api/state");
    state.job = next.job;
    jobView();
    if (next.job?.status === "running") pollTimer = setTimeout(pollJob, 1500);
    else await refresh(next.job?.kind === "weather" && next.job.status === "succeeded" ? next.job.result_id : "", next.job?.kind === "train" && next.job.status === "succeeded" ? next.job.result_id : "");
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
  if (!id) return;
  const result = await request(`/api/run?id=${encodeURIComponent(id)}`);
  if (version !== runVersion) return;
  const card = result.card;
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
async function submit(data) {
  submitting = true; buttons(); error();
  try {
    await request("/api/jobs", {method: "POST", headers: {"Content-Type": "application/json", "X-Workspace-Token": token}, body: JSON.stringify(data)});
    await refresh();
    $("job-panel").scrollIntoView({behavior: "smooth", block: "center"});
  } catch (e) { error(e.message); }
  finally { submitting = false; buttons(); }
}
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
$("evidence").addEventListener("change", buttons); $("label-ref").addEventListener("input", buttons);
$("train-dataset").addEventListener("change", buttons);
$("refresh").addEventListener("click", () => { error(); refresh().catch(e => error(e.message)); });
refresh().catch(e => error(`Could not load the workspace: ${e.message}`));
