"""Explicit local jobs: search any location, collect its weather, fit and report.

The target stays the documented SPP high-demand proxy. Area weather variants do
not establish local transmission capacity, actual interruptions, or future dates.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import math
import uuid
from pathlib import Path

import pandas as pd

from pipeline.common import ROOT, fingerprint, read_hourly, write_json

TERRITORY_URL = "https://eedgis.pnnl.gov/arcgis/rest/services/Hosted/Electric_Service_Territories/FeatureServer/0"
BASE = "data/processed/ml_inputs/spp_2019_2024_multifactor.parquet"
MODEL_FILES = ("site.py", "workflow.py", "features.py", "train.py", "confidence.py", "label.py", "stress.py", "simulate.py", "hours.py", "weather.py", "generation.py", "prepare.py", "common.py", "ingest.py")


def validate_query(query):
    if not isinstance(query, str) or not 2 <= len(query.strip()) <= 120 or any(ord(c) < 32 for c in query):
        raise ValueError('Enter a city and state or "latitude, longitude" (up to 120 characters).')
    return query.strip()


def validate_assumptions(data):
    result = {}
    for name, low, high in (("load_mw", 0.001, 10000), ("conditional_share", 0, 1), ("site_exposure", 0, 1)):
        value = data.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{name} must be a finite number between {low} and {high}.")
        result[name] = float(value)
    years = data.get("years")
    if isinstance(years, bool) or not isinstance(years, int) or not 1 <= years <= 7:
        raise ValueError("Choose a contract term of 1 to 7 years.")
    result["years"] = years
    if not isinstance(data.get("confirm_spp", False), bool):
        raise ValueError("SPP confirmation must be true or false.")
    result["confirm_spp"] = data.get("confirm_spp", False)
    return result


def search_location(query):
    from pipeline.ingest import search_weather_areas
    from pipeline.weather import STATE_NAMES
    query = validate_query(query)
    parts = [item.strip() for item in query.split(",")]
    coordinates = None
    if len(parts) == 2:
        try:
            coordinates = tuple(float(item) for item in parts)
        except ValueError:
            pass
    if coordinates is not None:
        lat, lon = coordinates
        if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("Latitude must be -90 to 90; longitude must be -180 to 180.")
        points = [{"name": f"{lat:.5f}, {lon:.5f}", "latitude": lat, "longitude": lon, "weight": 1.0}]
        source = {"source_type": "assumption", "ref": "Coordinates entered by the user; latitude first."}
    else:
        if len(parts) > 2 or not all(parts):
            raise ValueError('Use "City, State" or "latitude, longitude". For a parcel, use coordinates.')
        frame, source = search_weather_areas(parts[0], cache_dir=ROOT / "data/raw/weather/geocoding")
        frame = frame[frame.country_code.eq("US")]
        if len(parts) == 2:
            state = STATE_NAMES.get(parts[1].upper(), parts[1])
            frame = frame[frame.admin1.astype("string").str.casefold().eq(state.casefold())]
        frame = frame.drop_duplicates(["latitude", "longitude"])
        points = [{"name": f"{row['name']}, {row.admin1}", "latitude": float(row.latitude),
                   "longitude": float(row.longitude), "weight": 1.0, "geonames_id": int(row.id)}
                  for _, row in frame.head(20).iterrows()]
        if not points:
            raise ValueError("No matching US location found. Try a nearby city and state, or enter exact coordinates.")
    return {"query": query, "candidates": points, "source": source,
            "note": "Choose the intended result. City coordinates represent its center; use parcel coordinates for a more precise weather/territory lookup."}


def classify_coverage(payload):
    territories = [feature["attributes"] for feature in payload.get("features", [])]
    controls = {str(row.get("cntrl_area") or "").strip().upper() for row in territories}
    if controls & {"SOUTHWEST POWER POOL", "SWPP", "SPP"}:
        status = "historical_spp_match"
    elif any(any(name in value for name in ("ERCOT", "ELECTRIC RELIABILITY COUNCIL OF TEXAS", "MIDCONTINENT", "MIDWEST INDEPENDENT", "PJM", "CALIFORNIA INDEPENDENT", "NEW YORK INDEPENDENT", "ISO NEW ENGLAND")) for value in controls):
        status = "other_grid_match"
    else:
        status = "unverified"
    return {"status": status, "territories": territories,
            "interpretation": "Historical retail-territory screening only. Confirm the serving utility and point of interconnection; mixed polygons and post-2024 SPP expansion are not resolved by this map."}


def area_model_key(point, base):
    definition = {"coordinates": [point["latitude"], point["longitude"]], "base_path": str(base), "base_sha256": fingerprint(base),
                  "pipeline_sha256": {name: fingerprint(Path(__file__).with_name(name)) for name in MODEL_FILES},
                  "reference_end": "2022-01-01T00:00:00Z", "quantile": .95,
                  "members": 15, "trees": 100, "seed": 2026, "simulations": 2000, "years": 7}
    return hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()[:24], definition


def prepare_area_model(point):
    from pipeline.weather import add_temperature
    from pipeline.stress import prepare_demand_proxy
    from pipeline.workflow import train_run
    from pipeline.simulate import simulate_exposure
    base = ROOT / BASE
    if not base.exists():
        from pipeline.history import prepare_history
        from pipeline.generation import add_generation
        history = base.with_name("spp_2019_2024_amarillo.parquet")
        if not history.exists():
            prepare_history(2019, 2024, "Amarillo, TX", history)
        add_generation(history, base)
    key, definition = area_model_key(point, base)
    cache = ROOT / "data/processed/workbench/area_models" / key
    marker = cache / "complete.json"
    if marker.exists():
        complete = json.loads(marker.read_text(encoding="utf-8"))
        run = cache / complete["build"] / "run"
        if all((run / name).is_file() and fingerprint(run / name) == digest for name, digest in complete["artifacts"].items()):
            print("Reusing the fitted model and seasonal simulation for these coordinates.", flush=True)
            return run, True, definition
    build = cache / uuid.uuid4().hex[:12]
    build.mkdir(parents=True)
    hourly = read_hourly(base)
    if set(hourly.location_id) != {"SPP_SYSTEM"}:
        raise ValueError("Area comparison requires the documented SPP system historical dataset.")
    # Remove the old area's weather entirely before joining the chosen point.
    hourly = hourly.drop(columns=[name for name in hourly if name.startswith("temperature_") or name == "event_active"])
    grid_path = build / "grid.parquet"
    hourly.to_parquet(grid_path, index=False)
    mapping = {"operator": "SPP", "source_type": "assumption", "area_input": point["name"],
               "ref": "User-selected local weather point paired with SPP-wide grid history; no local grid capacity inferred.",
               "locations": {"SPP_SYSTEM": {"description": point["name"], "points": [point]}}}
    write_json(build / "area.json", mapping)
    weather_path = build / "weather.parquet"
    add_temperature(grid_path, build / "area.json", weather_path)
    labeled = build / "labeled.parquet"
    policy = build / "policy.json"
    prepare_demand_proxy(weather_path, labeled, policy, reference_end="2022-01-01T00:00:00Z", quantile=.95)
    run = build / "run"
    print("Fitting and evaluating a model using this location's historical temperature.", flush=True)
    card = train_run(labeled, policy, run)
    predictions = pd.read_parquet(run / "test_predictions.parquet")
    print("Simulating 2,000 seven-year seasonal scenarios; this stage may take several minutes.", flush=True)
    annual, trials, metadata = simulate_exposure(predictions, card["confidence"], card["model_version"], years=7)
    annual.to_parquet(run / "exposure_by_location.parquet", index=False)
    trials.to_parquet(run / "simulation_trials.parquet", index=False)
    metadata["policy"] = card["policy"]
    write_json(run / "simulation_metadata.json", metadata)
    from pipeline.hours import summarize_contract
    write_json(run / "contract_hours.json", {"locations": summarize_contract(trials), "source_type": "model", "model_version": card["model_version"]})
    names = ("model.joblib", "model_card.json", "test_predictions.parquet", "simulation_trials.parquet", "exposure_by_location.parquet", "hours_summary.json", "simulation_metadata.json")
    write_json(marker, {"build": build.name, "definition": definition, "artifacts": {name: fingerprint(run / name) for name in names}})
    return run, False, definition


def scenario_summary(trials, assumptions):
    from pipeline.hours import summarize_contract
    settings = validate_assumptions(assumptions)
    selected = trials[trials.year_offset.le(settings["years"])]
    system_term = summarize_contract(selected)["SPP_SYSTEM"]
    exposure = settings["site_exposure"]
    flexible_mw = settings["load_mw"] * settings["conditional_share"]
    annual = []
    for year, group in selected.groupby("year_offset"):
        quantiles = group.modeled_exposure_hours.quantile([.5, .9, .99]).to_numpy()
        row = {"year": int(year), "confidence": "Low"}
        for label, value in zip(("p50", "p90", "p99"), quantiles):
            row[f"system_{label}_hours"] = float(value)
            row[f"site_{label}_hours"] = float(value) * exposure
            row[f"conditional_{label}_mwh"] = float(value) * exposure * flexible_mw
        annual.append(row)
    return {"annual": annual, "system_term": system_term, "flexible_mw": flexible_mw,
            "site_term": {name: value * exposure for name, value in system_term.items() if name.endswith("hours")},
            "units": "Hours are modeled clock-hour exposure; MWh assumes the entire conditional MW is interrupted during those assumed site hours. Facility MW changes MWh only; it does not model added grid stress."}


def analyzed_report(point, coverage, scan, assumptions, run, reused, definition):
    card = json.loads((run / "model_card.json").read_text(encoding="utf-8"))
    hours = json.loads((run / "hours_summary.json").read_text(encoding="utf-8"))["locations"]["SPP_SYSTEM"]
    trials = pd.read_parquet(run / "simulation_trials.parquet")
    observations = read_hourly(run.parent / "weather.parquet")
    predictions = pd.read_parquet(run / "test_predictions.parquet")
    # Explicit timestamp join to prior-hour observations (never row-shift over gaps).
    previous = observations[["timestamp_utc", "location_id", "temperature_c", "load_mw", "wind_mw", "solar_mw"]].copy()
    previous.timestamp_utc += pd.Timedelta(hours=1)
    previous = previous.rename(columns={name: "prior_" + name for name in previous if name not in {"timestamp_utc", "location_id"}})
    scored = predictions.merge(previous, on=["timestamp_utc", "location_id"], how="left", validate="one_to_one")
    fields = ["timestamp_utc", "probability", "prior_temperature_c", "prior_load_mw", "prior_wind_mw", "prior_solar_mw"]
    ranked = scored.sort_values(["probability", "timestamp_utc"], ascending=[False, True]).head(24)[fields]
    months = []
    for month, group in scored.groupby(scored.timestamp_utc.dt.month):
        months.append({"month_utc": int(month), "scored_hours": len(group), "mean_probability": float(group.probability.mean()),
                       "mean_prior_temperature_c": float(group.prior_temperature_c.mean()),
                       "expected_hours_in_scored_period": float(group.probability.sum())})
    hot = scored[scored.prior_temperature_c.ge(30)]
    cool = scored[scored.prior_temperature_c.lt(30)]
    thermal = {"threshold_c": 30, "source_type": "assumption", "ref": "Descriptive comparison only; 30 C does not define the training target or prove a causal temperature effect.",
               "groups": [{"condition": label, "scored_hours": len(group), "mean_probability": float(group.probability.mean()) if len(group) else None}
                          for label, group in (("Prior-hour temperature >= 30 C", hot), ("Prior-hour temperature < 30 C", cool))]}
    weather = json.loads((run.parent / "weather.weather.json").read_text(encoding="utf-8"))
    grid_sources = {}
    # Keep the grid-source chain with each downloadable report, even when the
    # report is moved away from the workspace. Never reuse the base city's weather.
    generation_manifest = (ROOT / BASE).with_suffix(".generation.json")
    if generation_manifest.exists():
        grid_sources["generation"] = json.loads(generation_manifest.read_text(encoding="utf-8"))
    grid_sources["load"] = []
    for year in range(2019, 2025):
        source_path = ROOT / f"data/raw/spp/access_check/{year}_hourly_load.source.json"
        if source_path.exists():
            grid_sources["load"].append(json.loads(source_path.read_text(encoding="utf-8")))
    return {"status": "research_modeled_exposure", "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "location": point, "coverage": coverage, "geocoding": scan["source"], "assumptions": assumptions,
            "model_version": card["model_version"], "model_run": run.relative_to(ROOT).as_posix(), "model_reused": reused,
            "confidence": {**card["confidence"]["SPP_SYSTEM"], "level": "Low"},
            "target": card["policy"], "scenario": scenario_summary(trials, assumptions),
            "held_out": hours, "validation": {"test": card["test"], "baseline": card["baseline_test"], "splits": card["splits"]},
            "historical_highest_hours": json.loads(ranked.to_json(orient="records", date_format="iso")),
            "month_profile": months, "temperature_comparison": thermal,
            "data": {"start_utc": str(observations.timestamp_utc.min()), "end_utc": str(observations.timestamp_utc.max()),
                     "factors": card["input_factors"], "missing_factors": card["unavailable_factors"],
                     "weather": weather, "grid_sources": grid_sources, "model_definition": definition,
                     "scope": "Temperature is local ERA5 reanalysis. Load, wind and solar generation are SPP-wide, not city or feeder measurements."},
            "limitations": ["The target is high SPP demand (training-reference 95th percentile), not recorded data-center cutoff or every form of network stress.",
                "Local transmission headroom, outages, available reserves, operating instructions and site contract triggers are not established by these inputs.",
                "Site exposure is a user assumption, applied after system modeling. A historical utility-map match does not verify a parcel's interconnection.",
                "Annual scenarios reuse historical seasons. No forecast of future cutoff dates, climate change, load growth or the incremental effect of building this facility.",
                "Annual tails have not been validated; confidence is Low. Temperature comparisons show association and can also reflect load, season and other inputs.",
                "SPP grid history is 2019-2024. Newly joined territories may be outside its applicability; this is not a live 2026 grid-condition forecast."]}


def report_markdown(report):
    if report["status"] == "research_transfer_exposure":
        from pipeline.regional import transfer_markdown
        return transfer_markdown(report)
    point = report["location"]
    lines = [f"# Location report: {point['name']}", "", f"Coordinates: {point['latitude']}, {point['longitude']}", "",
             f"Utility-area check: {report['coverage']['status']}", "", report["coverage"]["interpretation"], ""]
    if report["status"] != "research_modeled_exposure":
        return "\n".join(lines + [report["message"], ""])
    settings, scenario = report["assumptions"], report["scenario"]
    lines += ["**Modeled exposure. Confidence: Low.** These are research scenarios, not a schedule or guarantee of actual site cutoffs.", "",
              f"Model: {report['model_version']}", "", f"Facility: {settings['load_mw']:g} MW. Conditional share: {settings['conditional_share']:.0%}. Assumed site exposure: {settings['site_exposure']:.0%}.", "",
              "| Year | System P50 h | System P90 h | System P99 h | Assumed site P50 h | Site P90 h | Site P99 h | Conditional P50 MWh |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for row in scenario["annual"]:
        lines.append("| " + " | ".join(str(row[key]) if key == "year" else f"{row[key]:,.1f}" for key in ("year", "system_p50_hours", "system_p90_hours", "system_p99_hours", "site_p50_hours", "site_p90_hours", "site_p99_hours", "conditional_p50_mwh")) + " |")
    term = scenario["site_term"]
    lines += ["", f"Assumed site {settings['years']}-year total: P50 {term['p50_total_hours']:,.1f} h; P90 {term['p90_total_hours']:,.1f} h; P99 {term['p99_total_hours']:,.1f} h. Joint-trial quantiles; confidence Low.", "", scenario["units"], "",
              f"Historical inputs: {report['data']['start_utc']} through {report['data']['end_utc']}. {report['data']['scope']}", "",
              f"Held-out expected system exposure: {report['held_out']['expected_exposure_hours']:,.1f} h across {report['held_out']['scored_hours']:,} scored hours; {report['held_out']['unscored_hours']} unscored hours. Confidence Low.", "",
              f"Held-out Brier score {report['validation']['test']['brier_score']:.5f}; training-prevalence baseline {report['validation']['baseline']['brier_score']:.5f}.", "",
              "Highest-risk months in the scored history (ranked by mean hourly probability): " + ", ".join(str(row["month_utc"]) for row in sorted(report["month_profile"], key=lambda row: row["mean_probability"], reverse=True)[:3]) + ". These are historical associations, not future dates.", "",
              "Missing factors: " + ", ".join(report["data"]["missing_factors"]) + ".", ""]
    lines += ["- " + item for item in report["limitations"]]
    lines += ["", "Historical temperature comparison (association only; confidence Low):", ""]
    for group in report["temperature_comparison"]["groups"]:
        value = "unavailable" if group["mean_probability"] is None else f"{group['mean_probability']:.2%}"
        lines += [f"- {group['condition']}: {group['scored_hours']:,} scored hours; mean proxy probability {value}."]
    lines += ["", "Highest scored historical examples (UTC; prior-hour inputs):", "",
              "| Hour UTC | Proxy probability | Local temperature C | SPP load MW | SPP wind MW | SPP solar MW |",
              "|---|---:|---:|---:|---:|---:|"]
    for row in report["historical_highest_hours"][:12]:
        conditions = ["unknown" if row[name] is None else f"{row[name]:,.1f}" for name in ("prior_temperature_c", "prior_load_mw", "prior_wind_mw", "prior_solar_mw")]
        lines += [f"| {row['timestamp_utc']} | {row['probability']:.2%} | " + " | ".join(conditions) + " |"]
    lines += ["", f"Model and artifact provenance: {report['model_run']}", "", f"Utility-map source: {TERRITORY_URL}", "",
              "Weather: https://open-meteo.com/en/docs/historical-weather-api", "",
              "The companion site_report.json contains hourly examples, seasonal and temperature comparisons, source URLs, retrieval dates, hashes, input coverage and all assumptions.", ""]
    if report.get("warning_signs"):
        from pipeline.signals import signals_markdown
        lines.append(signals_markdown(report["warning_signs"]))
    return "\n".join(lines)


def write_report(out, report):
    out.mkdir(parents=True, exist_ok=True)
    markdown = report_markdown(report)
    (out / "SITE_REPORT.md").write_text(markdown, encoding="utf-8")
    # Render our own bounded Markdown subset; all text is escaped before HTML.
    rendered, table_open, list_open = [], False, False
    for line in markdown.splitlines():
        if table_open and not line.startswith("|"):
            rendered.append("</tbody></table></div>")
            table_open = False
        if list_open and not line.startswith("- "):
            rendered.append("</ul>")
            list_open = False
        if line.startswith("|"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if all(set(cell) <= set("-: ") for cell in cells):
                continue
            tag = "td" if table_open else "th"
            if not table_open:
                rendered.append('<div class="table"><table><tbody>')
                table_open = True
            rendered.append("<tr>" + "".join(f"<{tag}>{html.escape(cell)}</{tag}>" for cell in cells) + "</tr>")
        elif line.startswith("## "):
            rendered.append("<h2>" + html.escape(line[3:]) + "</h2>")
        elif line.startswith("# "):
            rendered.append("<h1>" + html.escape(line[2:]) + "</h1>")
        elif line.startswith("- "):
            if not list_open:
                rendered.append("<ul>")
                list_open = True
            rendered.append("<li>" + html.escape(line[2:]) + "</li>")
        elif line:
            rendered.append("<p>" + html.escape(line.replace("**", "")) + "</p>")
    if table_open:
        rendered.append("</tbody></table></div>")
    if list_open:
        rendered.append("</ul>")
    style = "body{font:15px/1.6 system-ui;color:#172922;max-width:1180px;margin:40px auto;padding:0 24px}h1{font-size:30px}p,li{overflow-wrap:anywhere}.table{overflow:auto}table{border-collapse:collapse;width:100%;font-size:13px}td,th{border:1px solid #bfcfc6;padding:9px;text-align:left}th{background:#e8f0eb}li{margin:8px 0}@media print{body{margin:0}.table{overflow:visible}}"
    document = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Location exposure report</title><style>' + style + '</style></head><body>' + "\n".join(rendered) + '</body></html>'
    (out / "SITE_REPORT.html").write_text(document, encoding="utf-8")
    # Write the discovery marker last; readers only see completed reports.
    write_json(out / "site_report.json", report)


def run_site_job(request_path, out):
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if request["kind"] == "site-scan":
        print("Searching for matching locations (or accepting your coordinates).", flush=True)
        write_json(out / "scan.json", search_location(request["query"]))
        return
    if request["kind"] == "site-signals":
        from pipeline.signals import explain_saved_run
        original = Path(request["source_dir"])
        report = json.loads((original / "site_report.json").read_text(encoding="utf-8"))
        report["warning_signs"] = explain_saved_run(ROOT / report["model_run"])
        report["explanation_source_report"] = str(original)
        write_report(out, report)
        return
    from pipeline.ingest import fetch_utility_territories
    from requests import RequestException
    settings = validate_assumptions(request)
    scan, point = request["scan"], request["point"]
    print(f"Checking utility-area data for {point['name']}.", flush=True)
    try:
        payload, source = fetch_utility_territories(point["latitude"], point["longitude"])
        coverage = {**classify_coverage(payload), "source": source}
    except (RequestException, ValueError) as error:
        coverage = {"status": "unverified", "territories": [], "error": str(error),
                    "source": {"ref": TERRITORY_URL}, "interpretation": "Utility lookup unavailable; no grid membership established."}
    allowed = coverage["status"] == "historical_spp_match" or (coverage["status"] == "unverified" and settings["confirm_spp"])
    if not allowed:
        write_report(out, {"status": "coverage_review_required", "location": point, "coverage": coverage,
            "message": "No hours calculated. " + ("The historical map associates this point with another grid; the current SPP model is not applicable." if coverage["status"] == "other_grid_match" else "SPP membership was not verified. Confirm the utility/point of interconnection; if it belongs to the historical SPP footprint, select the explicit SPP assumption and generate again.")})
        return
    if request["kind"] == "site-transfer":
        from pipeline.regional import transfer_report
        report = transfer_report(point, coverage, scan, settings, out)
    else:
        from pipeline.signals import explain_saved_run
        run, reused, definition = prepare_area_model(point)
        report = analyzed_report(point, coverage, scan, settings, run, reused, definition)
        report["warning_signs"] = explain_saved_run(run)
    write_report(out, report)
    print(f"Report saved: {out / 'SITE_REPORT.html'}", flush=True)
