"""Transfer research: local-weather/SPP predictors, distinct load-area targets.

No individual data-center interruptions are observed. Each reference area's
training-period 95th-percentile demand defines an explicit high-demand proxy.
The query point has no invented target, local load measurement or tail quantiles.
"""
from __future__ import annotations

import calendar
import datetime as dt
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from pipeline.common import ROOT, fingerprint, read_hourly, write_json
from pipeline.features import build_features
from pipeline.train import chronological_split, evaluate, fit_ensemble, predict_members

COHORT = {"SPS": "Amarillo, TX", "OKGE": "Oklahoma City, OK", "LES": "Lincoln, NE", "OPPD": "Omaha, NE"}
AREA_GROUPS = (("SPS",), ("OKGE",), ("LES", "OPPD"))
REFERENCE_END = "2022-01-01T00:00:00Z"
POLICY = {"label_method": "observed_event"}
HOME = ROOT / "data/processed/workbench/regional"
MAPPING_REFS = ["https://spp.org/documents/56732/20180330_tariff%20revisions%20to%20implement%20a%20set%20of%20resource%20adequacy%20policies_er18-1268-000.pdf",
                "https://www.spp.org/Documents/37741/RSC%20Materials%2020160425%20PGD.pdf"]


def normalize_area_load(raw, area):
    frame = raw.copy()
    frame.columns = frame.columns.str.strip()
    if area not in frame or "MarketHour" not in frame:
        raise ValueError(f"Source archive lacks load area {area}.")
    frame["timestamp_utc"] = pd.to_datetime(frame.MarketHour, utc=True, format="mixed") - pd.Timedelta(1, unit="h")
    frame["area_load_mw"] = pd.to_numeric(frame[area], errors="raise")
    groups = frame.groupby("timestamp_utc").area_load_mw
    values = groups.first().where(groups.nunique(dropna=False).eq(1))
    values = values.where(np.isfinite(values) & values.ge(0))
    return values.reset_index()


def area_targets(frame):
    result = frame.copy()
    reference = result[result.timestamp_utc.lt(pd.Timestamp(REFERENCE_END))].area_load_mw.dropna()
    if len(reference) < 8760:
        raise ValueError("Each reference area needs at least a year of known training-period load.")
    threshold = float(reference.quantile(.95))
    result["target"] = result.area_load_mw.ge(threshold).astype(float).where(result.area_load_mw.notna())
    return result, threshold


def base_data():
    from pipeline.site import BASE
    path = ROOT / BASE
    if not path.exists():
        from pipeline.history import prepare_history
        from pipeline.generation import add_generation
        original = path.with_name("spp_2019_2024_amarillo.parquet")
        if not original.exists():
            prepare_history(2019, 2024, "Amarillo, TX", original)
        add_generation(original, path)
    return path


def point_weather(point, folder, *, area="QUERY_WEATHER_POINT", area_load=None):
    from pipeline.weather import add_temperature
    destination = folder / "weather.parquet"
    if destination.exists() and destination.with_suffix(".weather.json").exists():
        return read_hourly(destination)
    grid = read_hourly(base_data())
    grid = grid.drop(columns=[name for name in grid if name.startswith("temperature_") or name in {"target", "event_active"}])
    grid.location_id = area
    if area_load is not None:
        grid = grid.merge(area_load, on="timestamp_utc", how="left", validate="one_to_one")
    folder.mkdir(parents=True, exist_ok=True)
    grid.to_parquet(folder / "grid.parquet", index=False)
    write_json(folder / "mapping.json", {"operator": "SPP", "source_type": "assumption", "area_input": point["name"],
        "ref": "A single representative weather point; does not measure weather throughout a utility territory or establish parcel service.",
        "locations": {area: {"description": point["name"], "points": [point]}}})
    add_temperature(folder / "grid.parquet", folder / "mapping.json", destination)
    return read_hourly(destination)


def reference_data():
    from pipeline.site import search_location
    from pipeline.ingest import fetch_load_sample
    base = base_data()
    raw = []
    sources = []
    for year in range(2019, 2025):
        path = ROOT / f"data/raw/spp/access_check/{year}_hourly_load.parquet"
        if not path.exists():
            fetch_load_sample(f"{year}-01-01")
        raw.append(pd.read_parquet(path))
        sources.append({"path": str(path), "sha256": fingerprint(path),
                        "source": json.loads(path.with_suffix(".source.json").read_text(encoding="utf-8"))})
    all_load = pd.concat(raw, ignore_index=True)
    reference_key = hashlib.sha256(json.dumps({"base": fingerprint(base), "sources": sources,
        "weather_code": fingerprint(Path(__file__).with_name("weather.py")), "ingest_code": fingerprint(Path(__file__).with_name("ingest.py")),
        "normalizer": 1}, sort_keys=True).encode()).hexdigest()[:20]
    tables, definitions = [], []
    for area, city in COHORT.items():
        print(f"Reference area {area}: {city}; using its own load history for stress labels.", flush=True)
        scan = search_location(city)
        # Resolve the exact city/state, never the first fuzzy geocoder match.
        candidates = [point for point in scan["candidates"] if point["name"].split(",")[0].casefold() == city.split(",")[0].casefold()]
        if len(candidates) != 1:
            raise ValueError(f"Reference weather point is ambiguous: {city}.")
        point = candidates[0]
        point_key = hashlib.sha256(json.dumps(point, sort_keys=True).encode()).hexdigest()[:12]
        folder = HOME / "reference" / reference_key / f"{area}_{point_key}"
        frame = point_weather(point, folder, area=area, area_load=normalize_area_load(all_load, area))
        labeled, threshold = area_targets(frame)
        features, names = build_features(labeled, POLICY)
        if pd.Timestamp(REFERENCE_END) > chronological_split(features)["train"].timestamp_utc.max():
            raise ValueError("Area target reference overlaps calibration or testing.")
        tables.append(features)
        definitions.append({"area": area, "weather_point": point, "threshold_mw": threshold,
            "reference_end_exclusive": REFERENCE_END, "quantile": .95, "target_source_type": "assumption",
            "target": "This area's own load >= its training-reference 95th percentile; not physical overload or cutoff.",
            "known_area_load_hours": int(frame.area_load_mw.notna().sum()), "weather_mapping_source_type": "assumption",
            "weather_manifest": json.loads((folder / "weather.weather.json").read_text(encoding="utf-8")),
            "weather_sha256": fingerprint(folder / "weather.parquet"), "mapping_refs": MAPPING_REFS})
    frame = pd.concat(tables, ignore_index=True).sort_values(["timestamp_utc", "location_id"]).reset_index(drop=True)
    return frame, names, {"areas": definitions, "load_sources": sources, "base_path": str(base), "base_sha256": fingerprint(base)}


def validation_folds(frame, groups=AREA_GROUPS):
    common_test_start = chronological_split(frame)["test"].timestamp_utc.min()
    for withheld in groups:
        train = frame[~frame.location_id.isin(withheld)].copy()
        # Removing an area's rows can change the available calendar. Honor both
        # the common holdout and the actual fold model's later embargoed cutoff.
        test_start = max(common_test_start, chronological_split(train)["test"].timestamp_utc.min())
        test = frame[frame.location_id.isin(withheld) & frame.timestamp_utc.ge(test_start)].copy()
        if train.empty or test.empty:
            raise ValueError("Every area-validation fold needs separate training and query areas.")
        yield withheld, train, test


def prepare_model():
    frame, names, sources = reference_data()
    definition = {"sources": sources, "features": names, "members": 15, "trees": 100, "seed": 2026,
                  "code": {name: fingerprint(Path(__file__).with_name(name)) for name in ("regional.py", "features.py", "train.py", "common.py")}}
    key = hashlib.sha256(json.dumps(definition, sort_keys=True).encode()).hexdigest()[:20]
    run = HOME / "models" / key
    marker = run / "complete.json"
    if marker.exists():
        complete = json.loads(marker.read_text(encoding="utf-8"))
        if all((run / name).exists() and fingerprint(run / name) == digest for name, digest in complete["artifacts"].items()):
            print("Reusing the model trained across reference areas and its separate-area validation.", flush=True)
            return run, frame, True
    run.mkdir(parents=True, exist_ok=True)
    validations = []
    for withheld, training, test in validation_folds(frame):
        print(f"Validating on entirely withheld areas: {', '.join(withheld)}.", flush=True)
        bundle, card, _ = fit_ensemble(training, names)
        probabilities = predict_members(bundle, test).mean(axis=1)
        test = test.assign(probability=probabilities)
        prevalence = float(chronological_split(training)["train"].target.mean())
        for area, group in test.groupby("location_id"):
            metrics = evaluate(group.target.to_numpy(), group.probability.to_numpy())
            baseline = evaluate(group.target.to_numpy(), np.full(len(group), prevalence))
            validations.append({"area": area, "excluded_areas": list(withheld), "training_areas": sorted(training.location_id.unique()),
                "training_end": card["splits"]["train"]["end"], "calibration_end": card["splits"]["calibration"]["end"],
                "test_start": str(group.timestamp_utc.min()), "test_end": str(group.timestamp_utc.max()),
                "metrics": metrics, "baseline": baseline, "brier_skill": 1 - metrics["brier_score"] / baseline["brier_score"],
                "expected_proxy_hours": float(group.probability.sum()), "observed_proxy_hours": int(group.target.sum())})
    print("Fitting the pooled model using four areas and separate chronological calibration/test windows.", flush=True)
    bundle, card, predictions = fit_ensemble(frame, names)
    card.update({"model_version": "spp_regional_" + key, "scope": "regional_high_demand_transfer",
                 "target": "Distinct load-area high-demand proxies; each area's threshold uses only its pre-2022 history.",
                 "sources": sources, "definition": definition, "cross_area_validation": validations,
                 "transfer_limitations": "No local load, reserves or constraints for the query point. Transfer uses its weather and shared SPP inputs; representative city weather is an assumption."})
    bundle["report"] = card
    joblib.dump(bundle, run / "regional_model.joblib")
    write_json(run / "training_card.json", card)
    predictions.to_parquet(run / "test_predictions.parquet", index=False)
    frame.to_parquet(run / "features.parquet", index=False)
    write_json(marker, {"artifacts": {name: fingerprint(run / name) for name in ("regional_model.joblib", "training_card.json", "features.parquet", "test_predictions.parquet")}})
    return run, frame, False


def expected_hours(query, probabilities):
    times = pd.DatetimeIndex(query.timestamp_utc)
    if probabilities.ndim != 2 or probabilities.shape[1] < 2 or len(times) == 0 or times.has_duplicates or times.hasnans or times.tz is None or not times.equals(times.floor("h")) or probabilities.shape[0] != len(times) or not np.isfinite(probabilities).all() or ((probabilities < 0) | (probabilities > 1)).any():
        raise ValueError("Expected hours require unique hourly timestamps and finite probabilities.")
    if times.max() - times.min() < pd.Timedelta(365 * 24, unit="h"):
        raise ValueError("Annual transfer comparison needs at least a full year of query history.")
    timeline = pd.date_range(times.min(), times.max(), freq="h")
    annual = np.zeros(probabilities.shape[1])
    months = []
    for month in range(1, 13):
        positions = times.month == month
        available = int(positions.sum())
        expected = int((timeline.month == month).sum())
        if not expected or available < .9 * expected:
            raise ValueError(f"Insufficient query coverage in month {month}; do not invent missing seasons.")
        weighted = probabilities[positions].mean(axis=0) * calendar.monthrange(2023, month)[1] * 24
        annual += weighted
        months.append({"month": month, "scored_hours": available, "calendar_hours": expected,
                       "mean_probability": float(probabilities[positions].mean()), "stationary_expected_hours": float(weighted.mean())})
    return {"scored_period_expected_hours": float(probabilities.mean(axis=1).sum()), "scored_hours": len(times),
            "unscored_hours": len(timeline) - len(times), "annual_expected_hours": float(annual.mean()),
            "member_annual_min": float(annual.min()), "member_annual_max": float(annual.max()), "months": months,
            "annual_method": "Stationary 365-day comparison: sum(monthly mean probability x calendar-month hours); not an outcome percentile or future-date forecast.",
            "uncertainty": "Ensemble min/max is model disagreement, not a P90/P99 interval. No annual tail quantiles are inferred for an unobserved query-area target."}


def transfer_report(point, coverage, scan, settings, out):
    from pipeline.signals import explain_signals
    run, references, reused = prepare_model()
    card = json.loads((run / "training_card.json").read_text(encoding="utf-8"))
    validation = card["cross_area_validation"]
    model_error = sum(row["metrics"]["brier_score"] * row["metrics"]["n_hours"] for row in validation)
    baseline_error = sum(row["baseline"]["brier_score"] * row["baseline"]["n_hours"] for row in validation)
    if not model_error < baseline_error:
        raise ValueError("Cross-area validation does not beat the training-prevalence baseline. Transferred hours are withheld; inspect the saved training_card.json.")
    bundle = joblib.load(run / "regional_model.joblib")
    print(f"Collecting the query area's parameters: {point['name']}.", flush=True)
    observations = point_weather(point, out / "query")
    query, names = build_features(observations, POLICY, require_target=False)
    if names != bundle["feature_names"]:
        raise ValueError("Query predictors do not match the pooled model.")
    split = chronological_split(references)
    query = query[query.timestamp_utc.ge(split["test"].timestamp_utc.min())].copy()
    probability = predict_members(bundle, query)
    estimates = expected_hours(query, probability)
    estimates["assumed_site_annual_hours"] = estimates["annual_expected_hours"] * settings["site_exposure"]
    estimates["conditional_annual_mwh"] = estimates["assumed_site_annual_hours"] * settings["load_mw"] * settings["conditional_share"]
    estimates["assumed_site_term_hours"] = estimates["assumed_site_annual_hours"] * settings["years"]
    query.assign(probability=probability.mean(axis=1)).to_parquet(out / "query_predictions.parquet", index=False)
    signals = explain_signals(bundle, split["train"], split["test"], query, card["model_version"])
    report = {"status": "research_transfer_exposure", "location": point, "coverage": coverage, "geocoding": scan["source"],
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "assumptions": settings, "model_reused": reused,
        "model_version": card["model_version"], "confidence": {"level": "Low"}, "estimates": estimates, "warning_signs": signals,
        "cross_area_validation": validation, "cross_area_brier_skill": 1 - model_error / baseline_error, "training_sources": card["sources"],
        "model_path": str(run), "query_weather": json.loads((out / "query/weather.weather.json").read_text(encoding="utf-8")),
        "input_hashes": {"model": fingerprint(run / "regional_model.joblib"), "card": fingerprint(run / "training_card.json"),
                         "query_weather": fingerprint(out / "query/weather.parquet"), "predictions": fingerprint(out / "query_predictions.parquet")},
        "limitations": ["These are transferred regional high-demand-proxy estimates, not observed or predicted actual data-center cutoffs.",
            "The four reference areas have separate measured load histories and proxy labels; individual facilities do not supply interruption labels.",
            "Predictors are local representative weather plus shared SPP load/wind/solar and their histories. Local load at the query point, reserves, outages and local constraints are unavailable.",
            "The model is tested with entire reference areas excluded from fitting and calibration; these tests do not validate every possible new point or site contract.",
            "Stationary estimates use 2019-2024 inputs and the late-2023/2024 scoring window; no load-growth, climate or real-time forecast is made.",
            "Confidence remains Low. Site-exposure and conditional-load settings are user assumptions; facility MW only scales energy."]}
    return report


def transfer_markdown(report):
    from pipeline.signals import signals_markdown
    value, settings = report["estimates"], report["assumptions"]
    lines = ["# Regional comparison: " + report["location"]["name"], "", "Confidence: Low. Transferred high-demand-proxy exposure.", "",
        f"Expected regional proxy exposure: {value['annual_expected_hours']:,.1f} h per stationary comparison year.",
        f"Assumed site exposure: {value['assumed_site_annual_hours']:,.1f} h/year at {settings['site_exposure']:.0%} site exposure.",
        f"Conditional energy: {value['conditional_annual_mwh']:,.1f} MWh/year for {settings['load_mw']:g} MW and {settings['conditional_share']:.0%} conditional load.",
        f"Expected assumed-site total over {settings['years']} years: {value['assumed_site_term_hours']:,.1f} h.", "",
        value["annual_method"], value["uncertainty"], "", "## Testing on areas excluded from model training", "",
        "| Excluded area | Model Brier | Baseline Brier | Expected proxy h | Observed proxy h |", "|---|---:|---:|---:|---:|"]
    for row in report["cross_area_validation"]:
        lines.append(f"| {row['area']} | {row['metrics']['brier_score']:.4f} | {row['baseline']['brier_score']:.4f} | {row['expected_proxy_hours']:.1f} | {row['observed_proxy_hours']} |")
    lines += ["", "Lower Brier is better. The held-out time window is shared across areas; same-hour outcomes are never predictors.", ""]
    lines += ["- " + text for text in report["limitations"]]
    lines += [signals_markdown(report["warning_signs"]), "", "All source URLs, hashes, parameter cutoffs, model contributions and comparison examples are in site_report.json."]
    return "\n".join(lines)
