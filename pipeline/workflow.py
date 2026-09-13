"""Beginner-friendly command line for Kristian's offline ML work.

Run: python -m pipeline.workflow --help
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from pipeline.common import ML_DIR, fingerprint, read_hourly, read_policy, write_json


def train_run(hourly_path: Path, policy_path: Path, run_dir: Path, *, seed: int = 2026,
              members: int = 15, trees: int = 100) -> dict:
    import joblib

    from pipeline.confidence import CONFIDENCE_POLICY, estimate_confidence
    from pipeline.features import TEMPERATURE_FEATURE_POLICY, build_features
    from pipeline.label import label_hours
    from pipeline.train import chronological_split, fit_ensemble

    if run_dir.exists() and any(run_dir.iterdir()):
        raise ValueError(f"Run directory is not empty: {run_dir}. Choose a new --run-dir to preserve earlier results.")
    policy = read_policy(policy_path)
    print("1/5 Reading hourly observations and the explicit label policy...", flush=True)
    hourly = read_hourly(hourly_path)
    print("2/5 Labeling observed hours; missing evidence remains unknown...", flush=True)
    labeled = label_hours(hourly, policy)
    frame, feature_names = build_features(labeled, policy)
    if "demand_proxy" in policy:
        import pandas as pd
        reference_end = pd.Timestamp(policy["demand_proxy"]["reference_end_exclusive"])
        training_end = chronological_split(frame)["train"].timestamp_utc.max() + pd.Timedelta(1, unit="h")
        if reference_end.tzinfo is None or reference_end > training_end:
            raise ValueError("Demand-proxy threshold reference must stay within this run's training window.")
    print(f"3/5 Training {members} LightGBM models on {len(frame):,} usable location-hours...", flush=True)
    bundle, report, predictions = fit_ensemble(frame, feature_names, seed=seed, members=members, trees=trees)
    print("4/5 Scoring the untouched test period and estimating ensemble agreement...", flush=True)
    test = chronological_split(frame)["test"]
    confidence = estimate_confidence(bundle, test)
    hashes = {"hourly_sha256": fingerprint(hourly_path), "policy_sha256": fingerprint(policy_path)}
    model_version = f"spp_lgbm_{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%S}_{hashes['hourly_sha256'][:10]}"
    report.update({"model_version": model_version, "policy": policy, "input_hashes": hashes,
                   "input_rows": len(hourly), "usable_rows": len(frame),
                   "unknown_label_rows": int(labeled.target.isna().sum()),
                   "confidence": confidence, "confidence_policy": CONFIDENCE_POLICY})
    from pipeline.common import OBSERVATIONS
    report["input_factors"] = {name: {"observed_hours": int(hourly[name].notna().sum()),
                                     "missing_hours": int(hourly[name].isna().sum())}
                               for name in OBSERVATIONS if name in hourly}
    report["unavailable_factors"] = [name for name in OBSERVATIONS if name not in hourly]
    report["joint_feature_policy"] = "Prior-hour temperature x load, temperature squared, load ramps, and net load when actual wind/solar inputs exist. Relationships are learned; no fixed hot-weather cutoff probability."
    if "generation_source_ref" in hourly:
        report["generation_data"] = {"source_refs": sorted(hourly.generation_source_ref.dropna().unique().tolist())}
    if "temperature_c" in hourly:
        report["temperature_data"] = {
            "observed_hours": int(hourly.temperature_c.notna().sum()),
            "missing_hours": int(hourly.temperature_c.isna().sum()),
            "source_refs": sorted(hourly.temperature_source_ref.dropna().unique().tolist())
                if "temperature_source_ref" in hourly else [policy["data_ref"]],
            "feature_policy": TEMPERATURE_FEATURE_POLICY,
        }
    bundle["report"] = report
    run_dir.mkdir(parents=True, exist_ok=True)
    for filename in ("model.joblib", "model_card.json", "features.parquet", "test_predictions.parquet"):
        if (run_dir / filename).resolve() in {hourly_path.resolve(), policy_path.resolve()}:
            raise ValueError("Use a run directory separate from the input files.")
    labeled.to_parquet(run_dir / "labeled_hours.parquet", index=False)
    frame.to_parquet(run_dir / "features.parquet", index=False)
    predictions.to_parquet(run_dir / "test_predictions.parquet", index=False)
    joblib.dump(bundle, run_dir / "model.joblib")
    write_json(run_dir / "model_card.json", report)
    write_json(run_dir / "confidence.json", confidence)
    write_report(run_dir / "REPORT.md", report)
    from pipeline.hours import prepare_hours
    prepare_hours(run_dir)
    print(f"5/5 Saved model, test predictions, provenance, and report to {run_dir}", flush=True)
    print(f"Held-out Brier score: {report['test']['brier_score']:.4f}; baseline: {report['baseline_test']['brier_score']:.4f}")
    print("Status: research prototype; label/calibration review and annual-tail validation remain required.")
    return report


def write_report(path: Path, report: dict) -> None:
    metrics, baseline = report["test"], report["baseline_test"]
    lines = ["# Offline ML training report", "", f"Model: `{report['model_version']}`", "",
             "Research prototype. Label definitions and calibration need human review.", "",
             f"Target: `{report['policy']['label_method']}`. This is system-level modeled exposure, not actual site curtailment.",
             "", "| Held-out metric | Model | Training-prevalence baseline |", "|---|---:|---:|"]
    for key in ("brier_score", "log_loss", "average_precision", "mean_prediction"):
        lines.append(f"| {key} | {metrics[key]:.5f} | {baseline[key]:.5f} |")
    lines += ["", "Lower Brier score and log loss are better. Average precision should be compared with the event rate.",
              f"Held-out event rate: {metrics['event_rate']:.5f}; positive hours: {metrics['positive_hours']}.", "",
              "| Time window | Start | End | Hours | Positive hours |", "|---|---|---|---:|---:|"]
    for name, part in report["splits"].items():
        lines.append(f"| {name} | {part['start']} | {part['end']} | {part['rows']} | {part['positive_hours']} |")
    lines += ["", "The test window is excluded from fitting and calibration. Adjacent split windows have a 24-hour gap.", ""]
    if report["policy"].get("target_description"):
        lines += ["Target interpretation: " + report["policy"]["target_description"], ""]
    lines += ["Expected hours and coverage are saved in `hours_summary.json`. Annual results, when prepared, are separate experimental seasonal scenarios.", ""]
    lines += [f"- {warning}" for warning in report["warnings"]]
    lines += ["", "All source references, settings, input hashes, reliability bins, confidence limitations, and package versions are in `model_card.json`.", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build, evaluate, and inspect Kristian's SPP ML pipeline offline.")
    commands = parser.add_subparsers(dest="command", required=True)
    site = commands.add_parser("site-job", help="Search a location or build its cached research report.")
    site.add_argument("--request", type=Path, required=True)
    site.add_argument("--out", type=Path, required=True)
    commands.add_parser("fetch-sample", help="Check SPP access; cache real load data, using its annual archive if needed.")
    prepare = commands.add_parser("prepare-load", help="Normalize the cached legacy SPP load archive; invents no labels.")
    prepare.add_argument("--raw", type=Path, required=True)
    prepare.add_argument("--out", type=Path, required=True)
    join = commands.add_parser("join-evidence", help="Join hourly event/proxy evidence by exact location and UTC hour.")
    join.add_argument("--hourly", type=Path, required=True)
    join.add_argument("--evidence", type=Path, required=True)
    join.add_argument("--out", type=Path, required=True)
    weather = commands.add_parser("add-temperature", help="Enter an area, download/cache its historical temperatures, and join them to grid observations.")
    weather.add_argument("--hourly", type=Path, required=True)
    weather.add_argument("--out", type=Path, required=True)
    weather.add_argument("--location-id", help="Grid dataset ID to select when the input contains several locations.")
    mapping = weather.add_mutually_exclusive_group(required=True)
    mapping.add_argument("--area", help='City and state, e.g. "Amarillo, TX"; ambiguous names are rejected.')
    mapping.add_argument("--locations", type=Path, help="Advanced: explicit coordinates/weights JSON, e.g. docs/weather-locations.json.")
    inspect = commands.add_parser("inspect", help="Check a prepared hourly CSV/Parquet file.")
    inspect.add_argument("--hourly", type=Path, required=True)
    train = commands.add_parser("train", help="Prepare data, fit/calibrate the ensemble, and write a held-out report.")
    train.add_argument("--hourly", type=Path, required=True)
    train.add_argument("--policy", type=Path, required=True)
    train.add_argument("--run-dir", type=Path, default=ML_DIR)
    train.add_argument("--seed", type=int, default=2026)
    train.add_argument("--members", type=int, default=15)
    train.add_argument("--trees", type=int, default=100)
    simulate = commands.add_parser("simulate", help="Run the experimental seasonal simulation from saved test predictions.")
    simulate.add_argument("--run-dir", type=Path, default=ML_DIR)
    simulate.add_argument("--simulations", type=int, default=2000)
    simulate.add_argument("--years", type=int, default=7)
    simulate.add_argument("--seed", type=int, default=2026)
    hours = commands.add_parser("hours", help="Summarize saved held-out probabilities as expected exposure hours.")
    hours.add_argument("--run-dir", type=Path, default=ML_DIR)
    stress = commands.add_parser("prepare-demand-proxy", help="Create an explicit high-demand proxy using only a past reference period.")
    stress.add_argument("--hourly", type=Path, required=True)
    stress.add_argument("--out", type=Path, required=True)
    stress.add_argument("--policy", type=Path, required=True)
    stress.add_argument("--reference-end", required=True, help="Exclusive reference end with UTC offset, within the training window.")
    stress.add_argument("--quantile", type=float, default=.95)
    estimate = commands.add_parser("estimate", help="Read a precomputed location; performs no ML or network request.")
    estimate.add_argument("--location", required=True)
    estimate.add_argument("--path", type=Path, default=ML_DIR / "exposure_by_location.parquet")
    args = parser.parse_args()
    from requests import RequestException
    try:
        if args.command == "site-job":
            from pipeline.site import run_site_job
            run_site_job(args.request, args.out)
        elif args.command == "fetch-sample":
            from pipeline.ingest import fetch_load_sample
            frame, path = fetch_load_sample()
            print(f"Cached {len(frame)} source rows at {path}")
            print("Columns: " + ", ".join(frame.columns))
            print("Load confirms access only. Matching observed/proxy event labels are still needed for training.")
        elif args.command == "prepare-load":
            from pipeline.prepare import normalize_legacy_load
            print(json.dumps(normalize_legacy_load(args.raw, args.out), indent=2))
        elif args.command == "join-evidence":
            from pipeline.prepare import join_evidence
            print(json.dumps(join_evidence(args.hourly, args.evidence, args.out), indent=2))
        elif args.command == "add-temperature":
            from pipeline.weather import add_temperature, create_area_mapping
            if args.out.exists() or args.out.with_suffix(".weather.json").exists():
                raise ValueError(f"Output already exists: {args.out}. Use a new output path.")
            mapping_path = args.locations
            location_id = args.location_id
            if args.area:
                mapping_path = args.out.with_suffix(".area.json")
                config, location_id = create_area_mapping(args.area, args.hourly, mapping_path, location_id=location_id)
                print(f"Resolved area: {config['locations'][location_id]['points'][0]['name']}; grid ID remains {location_id}.", flush=True)
            report = add_temperature(args.hourly, mapping_path, args.out, location_id=location_id)
            print(json.dumps({"output_path": report["output_path"], "locations": report["locations"], "timing": report["timing"]}, indent=2))
        elif args.command == "inspect":
            frame = read_hourly(args.hourly)
            print(frame.groupby("location_id").agg(hours=("timestamp_utc", "count"),
                  start=("timestamp_utc", "min"), end=("timestamp_utc", "max")).to_string())
            print("Missing values by column:")
            print(frame.isna().sum().to_string())
        elif args.command == "train":
            train_run(args.hourly, args.policy, args.run_dir, seed=args.seed, members=args.members, trees=args.trees)
        elif args.command == "prepare-demand-proxy":
            from pipeline.stress import prepare_demand_proxy
            print(json.dumps(prepare_demand_proxy(args.hourly, args.out, args.policy,
                reference_end=args.reference_end, quantile=args.quantile), indent=2))
        elif args.command == "hours":
            from pipeline.hours import prepare_hours
            summary = prepare_hours(args.run_dir)
            print(json.dumps({location: {key: info[key] for key in ("expected_exposure_hours", "scored_hours", "unscored_hours", "annual_simulation_ready")}
                              for location, info in summary["locations"].items()}, indent=2))
        elif args.command == "simulate":
            import pandas as pd
            from pipeline.simulate import simulate_exposure
            card = json.loads((args.run_dir / "model_card.json").read_text(encoding="utf-8"))
            prediction = pd.read_parquet(args.run_dir / "test_predictions.parquet")
            frame, trials, metadata = simulate_exposure(prediction, card["confidence"], card["model_version"],
                seed=args.seed, simulations=args.simulations, years=args.years)
            frame.to_parquet(args.run_dir / "exposure_by_location.parquet", index=False)
            trials.to_parquet(args.run_dir / "simulation_trials.parquet", index=False)
            metadata["policy"] = card["policy"]
            if "temperature_data" in card:
                metadata["temperature_data"] = card["temperature_data"]
            write_json(args.run_dir / "simulation_metadata.json", metadata)
            from pipeline.hours import summarize_contract
            write_json(args.run_dir / "contract_hours.json", {
                "source_type": "model", "ref": "Joint saved simulation trials; quantiles of each trial's term total.",
                "model_version": card["model_version"], "site_exposure_applied": False,
                "locations": summarize_contract(trials)})
            print(f"Wrote {len(frame)} experimental annual rows. Read simulation_metadata.json before interpreting them.")
        else:
            from pipeline.simulate import get_location_estimate
            print(json.dumps(get_location_estimate(args.location, path=args.path), indent=2, allow_nan=False))
    except (ValueError, FileNotFoundError, LookupError) as error:
        parser.exit(2, f"Cannot continue: {error}\n")
    except RequestException as error:
        parser.exit(2, f"Data download failed: {error}\nCached successful downloads are preserved.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
