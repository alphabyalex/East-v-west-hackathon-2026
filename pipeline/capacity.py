"""Normalize cached historical SPP EcoMax reports for retrospective research.

This standalone table deliberately has no training-input timestamp or load
column. Exact historical publication times and interval semantics have not
been established; reported capacity is not deliverable reserves.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np
import pandas as pd

from pipeline.common import fingerprint, write_json
from pipeline.ingest import fetch_public_evidence

BASE_URL = "https://portal.spp.org/file-browser-api/download/hourly-generation-capacity-by-fuel-type"
FUEL_COLUMNS = {
    "Coal Market": "coal_market_ecomax_mw", "Coal Self": "coal_self_ecomax_mw",
    "Diesel Fuel Oil": "diesel_fuel_oil_ecomax_mw", "Hydro": "hydro_ecomax_mw",
    "Natural Gas": "natural_gas_ecomax_mw", "Nuclear": "nuclear_ecomax_mw",
    "Solar": "solar_ecomax_mw", "Waste Disposal Services": "waste_disposal_ecomax_mw",
    "Wind": "wind_ecomax_mw", "Waste Heat": "waste_heat_ecomax_mw", "Other": "other_ecomax_mw",
}
CAPACITY_POLICY = {
    "ref": "https://portal.spp.org/pages/hourly-generation-capacity-by-fuel-type",
    "guide_ref": "https://www.spp.org/Documents/75871/SPP%20Markets%20Public%20Data%20Guide%20and%20Samples%20v35.zip",
    "description": "Reported generation capacity by fuel, based on EcoMax used in real-time operations.",
    "timestamp": "Preserve source GMT TIME exactly; do not infer interval start/end or publication time.",
    "scope": "Pre-2026 SPP balancing area; exclude observations at or after 2026-01-01T00:00:00Z.",
    "publication": "Prior-day reports; exact historical publication and revision availability are unverified.",
    "usage": "Retrospective research only; not registered as model predictors or emergency labels.",
    "duplicates": "Collapse only identical fuel values, retain every report reference; reject conflicts.",
    "missing": "Explicit missing clock rows; never interpolate or forward fill.",
    "limitation": "EcoMax is not a guarantee of available or deliverable reserves or actual site interruptions.",
}


def _validate_years(start_year: int, end_year: int) -> None:
    if (type(start_year) is not int or type(end_year) is not int
            or not 2019 <= start_year <= end_year <= 2025):
        raise ValueError("Capacity reports support pre-expansion years 2019 through 2025.")


def parse_report(content: bytes, report_date: dt.date, source_ref: str) -> pd.DataFrame:
    if type(report_date) is not dt.date:
        raise ValueError("Capacity report dates must be calendar dates.")
    _validate_years(report_date.year, report_date.year)
    if not isinstance(source_ref, str) or urlsplit(source_ref).scheme != "https" or not urlsplit(source_ref).hostname:
        raise ValueError("Capacity reports require an HTTPS source reference.")
    raw = pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False)
    raw.columns = raw.columns.str.strip()
    expected = {"GMT TIME", *FUEL_COLUMNS}
    if raw.empty or set(raw.columns) != expected or len(raw.columns) != len(expected):
        raise ValueError("Unexpected capacity schema or empty report; do not infer a new balancing area.")
    text = raw["GMT TIME"]
    if not text.str.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:00:00Z").all():
        raise ValueError("GMT TIME must explicitly identify UTC hourly observations.")
    times = pd.to_datetime(text, utc=True, errors="raise")
    start = pd.Timestamp(report_date, tz="America/Chicago").tz_convert("UTC")
    end = pd.Timestamp(report_date + dt.timedelta(days=1), tz="America/Chicago").tz_convert("UTC")
    # Some old spring-transition reports include the preceding 23:00 observation
    # and overlap the previous report. Preserve that identity for reconciliation.
    earliest = start - pd.Timedelta(1, unit="h") if end - start == pd.Timedelta(23, unit="h") else start
    if times.duplicated().any() or len(times) > 25 or (times < earliest).any() or (times >= end).any():
        raise ValueError("Capacity timestamps must be unique hours within the dated operating report.")
    result = pd.DataFrame({"observation_timestamp_utc": times})
    formatted = 0
    for column, name in FUEL_COLUMNS.items():
        values = raw[column].str.strip()
        grouped = values.str.contains(",", regex=False)
        if not values[grouped].str.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?").all():
            raise ValueError("Malformed thousands separators in capacity data.")
        numbers = pd.to_numeric(values.str.replace(",", "", regex=False), errors="raise")
        if not np.isfinite(numbers).all() or (numbers < 0).any():
            raise ValueError("Capacity values must be finite, nonnegative MW numbers.")
        result[name] = numbers.astype(float)
        formatted += int(grouped.sum())
    result["capacity_evidence_refs"] = json.dumps([{
        "report_date": str(report_date), "ref": source_ref,
        "report_sha256": hashlib.sha256(content).hexdigest(),
    }], sort_keys=True)
    result.attrs["formatted_numeric_cells"] = formatted
    return result.sort_values("observation_timestamp_utc").reset_index(drop=True)


def load_capacity_year(year: int) -> tuple[pd.DataFrame, dict]:
    _validate_years(year, year)
    days = [stamp.date() for stamp in pd.date_range(f"{year}-01-01", f"{year}-12-31", freq="D")]
    frames, sources, short_reports = [], [], []
    if year < 2025:
        content, source = fetch_public_evidence(f"{BASE_URL}?path=/{year}/{year}.zip", f"generation_capacity_{year}_annual")
        sources.append(source)
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            files = [name for name in archive.namelist() if not name.endswith("/")]
            expected = {f"{year}/{day:%m}/HRLY-GEN-CAP-BY-FUEL-TYPE-{day:%Y%m%d}.csv" for day in days}
            if set(files) != expected or len(files) != len(expected):
                raise ValueError("Annual capacity archive must contain one distinct report for each date.")
            for day in days:
                name = f"{year}/{day:%m}/HRLY-GEN-CAP-BY-FUEL-TYPE-{day:%Y%m%d}.csv"
                frames.append(parse_report(archive.read(name), day, source["ref"] + "#" + name))
    else:
        # This annual ZIP is unavailable. Reuse the daily CSV byte caches.
        for day in days:
            content, source = fetch_public_evidence(
                f"{BASE_URL}?path=/{year}/{day:%m}/HRLY-GEN-CAP-BY-FUEL-TYPE-{day:%Y%m%d}.csv",
                f"generation_capacity_{day:%Y%m%d}")
            sources.append(source)
            frames.append(parse_report(content, day, source["ref"]))
    for day, frame in zip(days, frames):
        start = pd.Timestamp(day, tz="America/Chicago").tz_convert("UTC")
        end = pd.Timestamp(day + dt.timedelta(days=1), tz="America/Chicago").tz_convert("UTC")
        expected = pd.date_range(start, end, freq="h", inclusive="left")
        missing = expected.difference(frame.observation_timestamp_utc)
        if len(missing):
            short_reports.append({"report_date": str(day), "missing_local_day_hours": len(missing)})
    combined = pd.concat(frames, ignore_index=True)
    return combined, {
        "year": year, "reports": len(frames), "raw_rows": len(combined),
        "formatted_numeric_cells": sum(frame.attrs["formatted_numeric_cells"] for frame in frames),
        "incomplete_reports": short_reports,
        "sources": [{key: value for key, value in source.items() if key != "cache_path"} for source in sources],
    }


def normalize_reports(reports: pd.DataFrame, *, start_year: int, end_year: int) -> tuple[pd.DataFrame, dict]:
    """Reconcile parsed reports and explicitly retain absent clock observations."""
    _validate_years(start_year, end_year)
    required = {"observation_timestamp_utc", "capacity_evidence_refs", *FUEL_COLUMNS.values()}
    if reports.empty or set(reports.columns) != required or not reports.columns.is_unique:
        raise ValueError("Expected parsed capacity reports with source evidence.")
    times = reports.observation_timestamp_utc
    if not isinstance(times.dtype, pd.DatetimeTZDtype) or times.isna().any():
        raise ValueError("Capacity observations require timezone-aware timestamps.")
    reports = reports.copy()
    reports["observation_timestamp_utc"] = times.dt.tz_convert("UTC")
    times = reports.observation_timestamp_utc
    if not times.eq(times.dt.floor("h")).all():
        raise ValueError("Capacity observations must be on hourly boundaries.")
    fuels = list(FUEL_COLUMNS.values())
    if not np.isfinite(reports[fuels].to_numpy()).all() or (reports[fuels] < 0).any().any():
        raise ValueError("Parsed capacity reports must contain finite, nonnegative MW values.")
    start = pd.Timestamp(f"{start_year}-01-01", tz="America/Chicago").tz_convert("UTC")
    source_end = pd.Timestamp(f"{end_year+1}-01-01", tz="America/Chicago").tz_convert("UTC")
    if (times < start).any() or (times >= source_end).any():
        raise ValueError("Capacity reports extend outside the requested source years.")
    counts = reports.groupby("observation_timestamp_utc").size()
    duplicate = reports[reports.observation_timestamp_utc.duplicated(keep=False)]
    unique = reports.drop_duplicates("observation_timestamp_utc").set_index("observation_timestamp_utc").sort_index()
    for stamp, group in duplicate.groupby("observation_timestamp_utc"):
        if group[fuels].nunique(dropna=False).gt(1).any():
            raise ValueError(f"Conflicting capacity reports at {stamp}; source revisions require explicit reconciliation.")
        refs = [ref for value in group.capacity_evidence_refs for ref in json.loads(value)]
        if len({(ref["report_date"], ref["ref"]) for ref in refs}) != len(refs):
            raise ValueError("A capacity report was supplied more than once.")
        unique.loc[stamp, "capacity_evidence_refs"] = json.dumps(sorted(refs, key=lambda ref: (ref["report_date"], ref["ref"])), sort_keys=True)
    end = min(source_end, pd.Timestamp("2026-01-01", tz="UTC"))
    clock = pd.date_range(start, end, freq="h", inclusive="left", name="observation_timestamp_utc")
    result = unique.reindex(clock)
    result["capacity_report_count"] = counts.reindex(clock, fill_value=0).astype(int)
    result["capacity_evidence_refs"] = result.capacity_evidence_refs.fillna("[]")
    result["capacity_quality_status"] = np.select(
        [result.capacity_report_count.eq(0), result.capacity_report_count.gt(1)],
        ["missing", "identical_duplicate"], default="reported")
    result["region_id"] = "SPP_BA_PRE_2026"
    report = {
        "source_type": "data", "capacity_policy": CAPACITY_POLICY,
        "start_year": start_year, "end_year": end_year, "raw_rows": len(reports),
        "source_unique_hours": len(unique), "clock_hours": len(result),
        "known_hours": int(result.capacity_report_count.gt(0).sum()),
        "missing_hours": int(result.capacity_report_count.eq(0).sum()),
        "identical_duplicate_hours": int(result.capacity_report_count.gt(1).sum()),
        "excluded_post_2025_unique_hours": int((unique.index >= end).sum()),
        "training_features_created": False,
    }
    return result.reset_index(), report


def prepare_capacity(out: Path, *, start_year: int = 2019, end_year: int = 2025) -> dict:
    _validate_years(start_year, end_year)
    if out.suffix != ".parquet" or out.exists() or out.with_suffix(".capacity.json").exists():
        raise ValueError("Choose a new parquet output and sidecar path; existing artifacts must be preserved.")
    frames, years = [], []
    for year in range(start_year, end_year + 1):
        frame, source = load_capacity_year(year)
        frames.append(frame)
        years.append(source)
    normalized, report = normalize_reports(pd.concat(frames, ignore_index=True), start_year=start_year, end_year=end_year)
    report["years"] = years
    out.parent.mkdir(parents=True, exist_ok=True)
    normalized.to_parquet(out, index=False)
    report["output_sha256"] = fingerprint(out)
    write_json(out.with_suffix(".capacity.json"), report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--start-year", type=int, default=2019)
    parser.add_argument("--end-year", type=int, default=2025)
    args = parser.parse_args()
    report = prepare_capacity(args.out, start_year=args.start_year, end_year=args.end_year)
    print(json.dumps({key: value for key, value in report.items() if key not in {"capacity_policy", "years"}}, indent=2))


if __name__ == "__main__":
    main()
