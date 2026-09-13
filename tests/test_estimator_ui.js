/* Behavioral checks with an in-memory DOM and server; no browser or training required.
   Run: node tests/test_estimator_ui.js
   Also callable as runEstimatorChecks(appSource, htmlSource) in a JavaScript runtime. */
async function runEstimatorChecks(source, html) {
  const assert = (value, message) => { if (!value) throw new Error(message); };
  const clone = value => JSON.parse(JSON.stringify(value));
  const point = {name: "Wichita, Kansas", latitude: 37.69224, longitude: -97.33754};
  const settings = {load_mw: 200, conditional_share: .5, site_exposure: .25, years: 3, confirm_spp: false};
  // Disposable numeric fixtures exercise units and saved-input integrity, not model accuracy.
  const transfer = {status: "research_transfer_exposure", location: point, assumptions: settings,
    created_utc: "2024-01-01T00:00:00Z", model_version: "test-only", confidence: {level: "Low"},
    estimates: {annual_expected_hours: 600, assumed_site_annual_hours: 150, assumed_site_term_hours: 450, conditional_annual_mwh: 15000},
    query_weather: {ref: "https://example.org/weather"}, coverage: {status: "historical_spp_match"},
    warning_signs: {secret_explanation_marker: "NOT_FOR_MAIN_SCREEN"}, limitations: ["Test fixture only."]};
  const legacy = {...clone(transfer), status: "research_modeled_exposure", scenario: {
    annual: [{site_p50_hours: 125, system_p50_hours: 500, conditional_p50_mwh: 12500}],
    site_term: {p50_total_hours: 420, expected_total_hours: 430}}};
  function environment(reports = {}) {
    class Element {
      constructor(tag = "div") { this.tag = tag; this.children = []; this.listeners = {}; this._value = ""; this.hidden = false; this.checked = false; }
      get value() { return this._value; } set value(value) { this._value = String(value); }
      replaceChildren(...children) { this.children = children; if (this.tag === "select") this.value = children[0]?.value || ""; }
      append(...children) { this.children.push(...children); }
      addEventListener(name, fn) { this.listeners[name] = fn; }
      setAttribute(name, value) { this[name] = value; }
      reportValidity() { return true; }
      scrollIntoView() {} click() {} remove() {}
    }
    const elements = new Map();
    for (const match of html.matchAll(/<([a-z0-9]+)\b[^>]*\bid="([^"]+)"/g)) elements.set(match[2], new Element(match[1]));
    const node = id => { assert(elements.has(id), "Missing DOM element: " + id); return elements.get(id); };
    for (const [id, value] of [["facility-mw", 100], ["flexible-percent", 100], ["site-exposure", 1], ["term-years", 7]]) node(id).value = value;
    const document = {getElementById: node, createElement: tag => new Element(tag), addEventListener() {},
      querySelector: () => ({content: "test-token"}), querySelectorAll: () => [], body: new Element()};
    const data = {reports, scans: {}, posts: [], gets: [], job: null, delay: null};
    const reportRows = () => Object.entries(data.reports).map(([id, r]) => ({id, name: r.location.name}));
    const fetch = async (path, opts = {}) => {
      let body;
      if (opts.method === "POST") {
        assert(opts.headers["X-Workspace-Token"] === "test-token", "POST must include workspace token");
        const payload = JSON.parse(opts.body); data.posts.push(payload);
        data.job = {id: "job" + data.posts.length, kind: payload.kind, status: "running", result_id: "result" + data.posts.length};
        body = data.job;
      } else {
        data.gets.push(path);
        const u = new URL(path, "http://localhost");
        if (u.pathname === "/api/state") body = {site_reports: reportRows(), job: data.job};
        else if (u.pathname === "/api/site-report") {
          if (data.delay) await data.delay(u.searchParams.get("id"));
          body = data.reports[u.searchParams.get("id")];
        } else if (u.pathname === "/api/site-scan") body = data.scans[u.searchParams.get("id")];
        else throw new Error("Unexpected read: " + path);
      }
      assert(body, "No fixture response for " + path);
      return {ok: true, json: async () => clone(body)};
    };
    const api = new Function("document", "fetch", "AbortSignal", "setTimeout", "clearTimeout", source +
      "\nreturn {initialize, refresh, estimate, loadReport, summarizeReport, format, estimateText, sourceReferences, readInputs, getReport: () => activeReport};")(
      document, fetch, {timeout: () => undefined}, () => 1, () => {});
    const input = (id, value) => {
      node(id).value = value;
      node("estimate-form").listeners.input?.({target: {id}});
    };
    const submit = () => api.estimate({preventDefault() {}});
    const finishSearch = candidates => {
      data.scans[data.job.result_id] = {query: data.posts.at(-1).query, candidates}; data.job.status = "succeeded";
    };
    return {api, data, node, input, submit, finishSearch};
  }
  let count = 0;
  let env = environment({saved: transfer}); await env.api.initialize();
  assert(env.node("annual-hours").textContent === "150", "Show saved site hours, not regional hours");
  assert(env.node("term-hours").textContent === "450" && env.node("annual-energy").textContent === "15,000", "Keep term hours and MWh distinct");
  assert(env.node("site-exposure").value === "0.25", "Saved result restores its assumptions");
  assert(env.data.posts.length === 0, "Opening the page must not start jobs"); count++;

  env.input("facility-mw", 500);
  assert(!env.node("stale-result").hidden, "Edited inputs must flag the old result");
  assert(env.node("annual-energy").textContent === "15,000", "Never silently recalculate a saved result with unsaved inputs");
  const text = env.api.estimateText(env.api.getReport());
  assert(text.includes("Facility power demand: 200 MW") && text.includes("15,000 MWh/year"), "Export must retain saved values and units");
  assert(!text.includes("NOT_FOR_MAIN_SCREEN"), "Plain export excludes warning-sign explanations"); count++;

  env = environment(); await env.api.initialize();
  for (const [id, value] of [["location-query", "Wichita, KS"], ["facility-mw", 200], ["flexible-percent", 50], ["site-exposure", .25], ["term-years", 3]]) env.input(id, value);
  await env.submit(); assert(env.data.posts.length === 1 && env.data.posts[0].kind === "site-scan", "One click starts location lookup");
  env.finishSearch([point]); await env.api.refresh();
  const payload = env.data.posts[1];
  assert(payload.kind === "site-transfer" && payload.candidate === 0, "Unique match must continue to the regional model");
  assert(payload.load_mw === 200 && payload.conditional_share === .5 && payload.site_exposure === .25 && payload.years === 3 && payload.confirm_spp === false, "Preserve captured inputs and percentage units");
  assert(env.node("input-fields").disabled, "Prevent edits while the report runs");
  env.data.reports[env.data.job.result_id] = clone(transfer); env.data.job.status = "succeeded";
  await env.api.refresh(); await env.api.refresh();
  assert(env.node("annual-hours").textContent === "150" && env.node("stale-result").hidden, "Show the matching completed output automatically");
  assert(env.data.posts.length === 2, "Refreshing a completed result must never resubmit it"); count++;

  env = environment(); await env.api.initialize(); env.input("location-query", "Springfield"); await env.submit();
  env.finishSearch([point, {...point, name: "Another match"}]); await env.api.refresh();
  assert(env.data.posts.length === 1 && !env.node("candidate-block").hidden, "Ambiguous locations must wait for selection");
  env.input("location-candidate", "1"); await env.submit();
  assert(env.data.posts[1].candidate === 1, "Use the explicitly selected match"); count++;

  env = environment(); await env.api.initialize(); env.input("location-query", "Wichita, KS"); await env.submit();
  env.input("location-query", "Lincoln, NE"); env.finishSearch([point]); await env.api.refresh();
  assert(env.data.posts.length === 1, "A stale lookup must not start a report for the old location"); count++;

  const blocked = {status: "coverage_review_required", location: point, coverage: {status: "unverified"}, message: "No hours calculated."};
  env = environment({blocked}); await env.api.initialize();
  assert(env.node("estimate-result").hidden && !env.node("coverage-result").hidden, "Unknown coverage must never show hours");
  assert(!env.node("confirm-spp").checked && !env.node("coverage-confirmation").hidden, "Grid confirmation must be explicit");
  env.node("confirm-spp").checked = true; await env.submit(); env.finishSearch([point]); await env.api.refresh();
  assert(env.data.posts[1].confirm_spp === true, "Preserve an explicit assertion through a fresh location lookup");
  env.input("location-query", "Different place");
  assert(!env.node("confirm-spp").checked, "Do not carry grid confirmation to another place"); count++;

  env = environment({legacy}); await env.api.initialize();
  assert(env.node("annual-label").textContent.includes("P50") && env.node("annual-hours").textContent === "125", "Legacy medians must not become expected-value labels");
  assert(env.node("term-hours").textContent === "420", "Legacy term median must use joint trials, not 3 times the annual median"); count++;

  assert(env.api.format(null) === "Unavailable" && env.api.format(NaN) === "Unavailable" && env.api.format(0) === "0", "Missing numbers must remain distinct from zero");
  assert(env.api.summarizeReport(blocked) === null, "Unsupported reports have no summary");
  assert(env.api.sourceReferences({...transfer, query_weather: {ref: "javascript:alert(1)"}}).length === 0, "Reject unsafe source URLs"); count++;

  env = environment({first: transfer, second: {...clone(transfer), location: {...point, name: "Second location"}}});
  await env.api.initialize(); let release;
  env.data.delay = id => id === "first" ? new Promise(resolve => { release = resolve; }) : Promise.resolve();
  const old = env.api.loadReport("first"); await env.api.loadReport("second"); release(); await old;
  assert(env.node("result-location").textContent === "Second location", "Late responses must not overwrite the chosen report");
  assert(!env.node("input-fields").disabled, "Unlock controls after the latest report loads"); count++;
  return count + " estimator behavior checks passed";
}
if (typeof require === "function" && require.main === module) {
  const fs = require("node:fs"), path = require("node:path");
  const assets = path.join(__dirname, "../pipeline/workbench_assets");
  runEstimatorChecks(fs.readFileSync(path.join(assets, "app.js"), "utf8"), fs.readFileSync(path.join(assets, "index.html"), "utf8"))
    .then(console.log).catch(error => { console.error(error); process.exitCode = 1; });
}
