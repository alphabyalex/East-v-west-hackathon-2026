"""Prepare a reproducible multi-year SPP load/weather research dataset."""
import argparse
from pathlib import Path

import pandas as pd

from pipeline.common import ROOT, fingerprint, read_hourly, write_json
from pipeline.ingest import fetch_load_sample
from pipeline.prepare import normalize_legacy_load
from pipeline.weather import add_temperature, create_area_mapping


def prepare_history(start_year: int, end_year: int, area: str, out: Path) -> None:
    if not 2010 <= start_year <= end_year <= 2024:
        raise ValueError("This archive workflow supports completed legacy archives through 2024.")
    if out.exists() or out.with_suffix(".weather.json").exists():
        raise ValueError("Choose a new final output path.")
    inputs, sources = [], []
    for year in range(start_year, end_year + 1):
        _, raw = fetch_load_sample(f"{year}-01-01")
        prepared = ROOT / f"data/processed/ml_inputs/spp_{year}_load_only.parquet"
        if not prepared.exists():
            normalize_legacy_load(raw, prepared)
        frame = read_hourly(prepared)
        if len(frame) < 8700:
            raise ValueError(f"{year}: source is not a full annual archive.")
        inputs.append(frame)
        sources.append({"path": str(prepared), "sha256": fingerprint(prepared),
                        "raw_path": str(raw), "raw_sha256": fingerprint(raw)})
    combined = pd.concat(inputs, ignore_index=True).drop_duplicates()
    conflicts = combined.duplicated(["location_id", "timestamp_utc"], keep=False)
    combined.loc[conflicts, "load_mw"] = float("nan")
    combined = combined.drop_duplicates(["location_id", "timestamp_utc"]).sort_values("timestamp_utc")
    combined = combined.set_index("timestamp_utc").reindex(pd.date_range(combined.timestamp_utc.min(), combined.timestamp_utc.max(), freq="h"))
    combined.index.name = "timestamp_utc"
    combined["location_id"] = "SPP_SYSTEM"
    combined = combined.reset_index()
    load_path = out.with_suffix(".load.parquet")
    if load_path.exists():
        if not read_hourly(load_path).equals(combined[["timestamp_utc", "location_id", "load_mw"]].reset_index(drop=True)):
            raise ValueError("Cached combined input differs; choose a new output path.")
    else:
        load_path.parent.mkdir(parents=True, exist_ok=True)
        combined.to_parquet(load_path, index=False)
    mapping = out.with_suffix(".area.json")
    if not mapping.exists():
        create_area_mapping(area, load_path, mapping, location_id="SPP_SYSTEM")
    add_temperature(load_path, mapping, out)
    write_json(out.with_suffix(".quality.json"), {"source_type": "data", "ref": "SPP annual hourly-load archives",
        "sources": sources, "hours": len(combined), "unknown_load_hours": int(combined.load_mw.isna().sum()),
        "conflicting_duplicate_rows": int(conflicts.sum()), "output_sha256": fingerprint(out)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-year", type=int, default=2019)
    parser.add_argument("--end-year", type=int, default=2024)
    parser.add_argument("--area", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    prepare_history(args.start_year, args.end_year, args.area, args.out)


if __name__ == "__main__":
    main()
