"""Join cached SPP wind/solar observations to historical system load and weather."""
import argparse
import io
import json
from pathlib import Path

import pandas as pd

from pipeline.common import ROOT, fingerprint, read_hourly, write_json
from pipeline.ingest import fetch_public_evidence


def normalize_generation(raw: pd.DataFrame) -> pd.DataFrame:
    frame = raw.copy()
    frame.columns = frame.columns.str.strip()
    required = {"GMT MKT Interval", "Wind Market", "Wind Self", "Solar Market", "Solar Self"}
    if not required.issubset(frame):
        raise ValueError("Generation archive lacks timestamp and wind/solar components.")
    frame = frame[list(sorted(required))].drop_duplicates()
    times = [pd.Timestamp(value) for value in frame["GMT MKT Interval"]]
    if any(pd.isna(value) or value.tzinfo is None for value in times):
        raise ValueError("Generation timestamps require explicit timezone information.")
    frame["timestamp_utc"] = pd.to_datetime(frame["GMT MKT Interval"], utc=True)
    if not frame.timestamp_utc.eq(frame.timestamp_utc.dt.floor("5min")).all():
        raise ValueError("Expected five-minute observations.")
    for fuel in ("Wind", "Solar"):
        frame[fuel.lower() + "_mw"] = frame[[fuel + " Market", fuel + " Self"]].apply(pd.to_numeric, errors="raise").sum(axis=1, min_count=2)
    conflict = frame.duplicated("timestamp_utc", keep=False)
    frame.loc[conflict, ["wind_mw", "solar_mw"]] = float("nan")
    frame = frame.drop_duplicates("timestamp_utc").set_index("timestamp_utc").sort_index()
    # Treat source timestamps as observation times. Left-closed bins and lagged
    # features ensure a target hour never uses its own or later observations.
    hourly = frame[["wind_mw", "solar_mw"]].resample("h").mean()
    counts = frame[["wind_mw", "solar_mw"]].resample("h").count()
    hourly = hourly.where(counts.eq(12))
    return hourly.reset_index()


def add_generation(hourly_path: Path, out: Path, *, start_year=2019, end_year=2024):
    if not 2019 <= start_year <= end_year <= 2024:
        raise ValueError("Use historical generation years from 2019 through 2024.")
    if out.exists():
        raise ValueError("Choose a new output path.")
    hourly = read_hourly(hourly_path)
    if set(hourly.location_id) != {"SPP_SYSTEM"} or hourly.timestamp_utc.max() >= pd.Timestamp("2026-01-01", tz="UTC"):
        raise ValueError("This join is reviewed only for the historical pre-2026 SPP system.")
    if {"wind_mw", "solar_mw"}.intersection(hourly.columns):
        raise ValueError("Existing generation observations cannot be overwritten.")
    frames, sources = [], []
    for year in range(start_year, end_year + 1):
        path = ROOT / f"data/raw/spp/generation/{year}.parquet"
        if path.exists():
            frame = pd.read_parquet(path)
            source = json.loads(path.with_suffix(".source.json").read_text(encoding="utf-8"))
        else:
            print(f"Generation archive: {year}", flush=True)
            content, source = fetch_public_evidence(
                f"https://portal.spp.org/file-browser-api/download/generation-mix-historical?path=/GenMix_{year}.csv", f"genmix_{year}")
            frame = normalize_generation(pd.read_csv(io.BytesIO(content)))
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(path, index=False)
            write_json(path.with_suffix(".source.json"), source)
        frames.append(frame)
        sources.append({**source, "normalized_sha256": fingerprint(path)})
    # Boundary hours require all 12 raw samples. Never combine partial means from
    # adjacent annual files; leave any incomplete boundary hour unknown.
    combined = pd.concat(frames).sort_values("timestamp_utc")
    combined = combined.groupby("timestamp_utc")[["wind_mw", "solar_mw"]].agg(
        lambda values: values.dropna().iloc[0] if len(values.dropna().unique()) == 1 else float("nan")).reset_index()
    result = hourly.merge(combined, on="timestamp_utc", how="left", validate="one_to_one")
    result["generation_source_ref"] = str(out.with_suffix(".generation.json"))
    out.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(out, index=False)
    report = {"source_type": "data", "ref": "https://portal.spp.org/pages/generation-mix-historical",
              "sources": sources, "input_sha256": fingerprint(hourly_path), "output_sha256": fingerprint(out),
              "timing": "UTC observation timestamps in left-closed hourly bins; 12 valid distinct samples per fuel required. Predictors lag by at least one hour.",
              "known_hours": {name: int(result[name].notna().sum()) for name in ("wind_mw", "solar_mw")}}
    write_json(out.with_suffix(".generation.json"), report)
    for suffix in (".weather.json", ".area.json", ".quality.json"):
        source = hourly_path.with_suffix(suffix)
        if source.exists():
            out.with_suffix(suffix).write_bytes(source.read_bytes())
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hourly", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--start-year", type=int, default=2019)
    parser.add_argument("--end-year", type=int, default=2024)
    args = parser.parse_args()
    print(json.dumps(add_generation(args.hourly, args.out, start_year=args.start_year, end_year=args.end_year)["known_hours"]))


if __name__ == "__main__":
    main()
