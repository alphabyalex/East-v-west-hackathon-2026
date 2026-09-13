"""Prepare a reproducible multi-year SPP load/weather research dataset."""
import argparse
import json
from pathlib import Path

import pandas as pd

from pipeline.common import ROOT, fingerprint, read_hourly, write_json
from pipeline.ingest import fetch_load_sample, fetch_public_evidence
from pipeline.prepare import normalize_legacy_load
from pipeline.weather import add_temperature, create_area_mapping

def load_archive_year(year: int) -> tuple[pd.DataFrame, Path]:
    """Use the reviewed monthly format for 2025, not a one-day access sample."""
    if year != 2025:
        return fetch_load_sample(f"{year}-01-01")
    from pipeline.wind_signal import read_cached_monthly_load

    sources = []
    for month in range(1, 13):
        _, source = fetch_public_evidence(
            f"https://portal.spp.org/file-browser-api/download/hourly-load?path=/2025/HOURLY_LOAD-2025{month:02d}.csv",
            f"hourly_load_2025_{month:02d}")
        sources.append(source)
    # The existing reader checks month/UTC conventions, component schema and
    # each document hash. It retains conflicting revisions for the normalizer.
    raw, origin = read_cached_monthly_load(2025)
    path = ROOT / "data/raw/spp/access_check/2025_monthly_load.parquet"
    manifest_path = path.with_suffix(".source.json")
    source_hashes = {source["requested_url"]: source["sha256"] for source in sources}
    if path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (manifest.get("source_sha256") != source_hashes
                or manifest.get("parquet_sha256") != fingerprint(path)
                or not pd.read_parquet(path).equals(raw)):
            raise ValueError("Combined 2025 load cache differs from its source evidence; use a new reviewed snapshot.")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        raw.to_parquet(path, index=False)
        write_json(manifest_path, {**origin, "sources": sources, "source_sha256": source_hashes,
                                  "parquet_sha256": fingerprint(path)})
    return raw, path


def validate_annual_load(frame: pd.DataFrame, year: int) -> dict:
    """A long daily file or a shifted year must not masquerade as annual history."""
    expected = pd.date_range(f"{year}-01-01", f"{year+1}-01-01", freq="h",
                             inclusive="left", tz="America/Chicago").tz_convert("UTC")
    if (set(frame.location_id) != {"SPP_SYSTEM"}
            or not pd.DatetimeIndex(frame.timestamp_utc).equals(expected)):
        raise ValueError(f"{year}: normalized load does not cover the complete Central operating year.")
    return {"expected_hours": len(expected), "known_load_hours": int(frame.load_mw.notna().sum()),
            "unknown_load_hours": int(frame.load_mw.isna().sum())}


def prepare_history(start_year: int, end_year: int, area: str, out: Path) -> None:
    if (type(start_year) is not int or type(end_year) is not int
            or not 2010 <= start_year <= end_year <= 2025):
        raise ValueError("This archive workflow supports completed legacy archives through 2025.")
    if out.exists() or out.with_suffix(".weather.json").exists():
        raise ValueError("Choose a new final output path.")
    inputs, sources = [], []
    for year in range(start_year, end_year + 1):
        _, raw = load_archive_year(year)
        prepared = ROOT / f"data/processed/ml_inputs/spp_{year}_load_only.parquet"
        if not prepared.exists():
            normalize_legacy_load(raw, prepared)
        quality = json.loads(prepared.with_suffix(".quality.json").read_text(encoding="utf-8"))
        if quality.get("raw_sha256") != fingerprint(raw):
            raise ValueError(f"{year}: normalized load cache belongs to different raw observations.")
        frame = read_hourly(prepared)
        if "location_id" in frame.columns:
            frame = frame[frame.location_id == "SPP_SYSTEM"]
        coverage = validate_annual_load(frame, year)
        inputs.append(frame)
        sources.append({"path": str(prepared), "sha256": fingerprint(prepared),
                        "raw_path": str(raw), "raw_sha256": fingerprint(raw), **coverage})
    combined = pd.concat(inputs, ignore_index=True).drop_duplicates()
    conflicts = combined.duplicated(["location_id", "timestamp_utc"], keep=False)
    combined.loc[conflicts, "load_mw"] = float("nan")
    combined = combined.drop_duplicates(["location_id", "timestamp_utc"]).sort_values("timestamp_utc")
    combined = combined.set_index("timestamp_utc").reindex(pd.date_range(combined.timestamp_utc.min(), combined.timestamp_utc.max(), freq="h"))
    combined.index.name = "timestamp_utc"
    combined["location_id"] = "SPP_SYSTEM"
    combined = combined.reset_index()
    # The current SPP_SYSTEM mapping and generation join are explicitly scoped
    # before 2026 UTC. Drop the six Central-year boundary hours, with an audit.
    outside_scope = combined.timestamp_utc >= pd.Timestamp("2026-01-01", tz="UTC")
    excluded_hours = int(outside_scope.sum())
    combined = combined.loc[~outside_scope].reset_index(drop=True)
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
        "excluded_post_2025_utc_hours": excluded_hours,
        "scope": "Historical SPP_SYSTEM before 2026 UTC; not site-specific load or interruption evidence.",
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
