"""Prepare dated SPP generation-outage outlooks for retrospective predictors.

These are seven-day outlooks, not measured forced outages. A report is eligible
only from the following Central calendar day. That conservative date-based rule
does not establish the original publication time or rule out later revisions.
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import shutil
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline.common import fingerprint, read_hourly, write_json
from pipeline.ingest import fetch_public_evidence

BASE_URL = "https://portal.spp.org/file-browser-api/download/capacity-of-generation-on-outage"
FUEL_COLUMNS = ("Coal MW", "Diesel Fuel Oil MW", "Hydro MW", "Natural Gas MW", "Nuclear MW",
                "Solar MW", "Waste Disposal MW", "Wind MW", "Waste Heat MW", "Other MW")
OUTLOOK_COLUMNS = {"Outaged MW": "outage_outlook_mw", "Natural Gas MW": "gas_outage_outlook_mw",
                   "Coal MW": "coal_outage_outlook_mw", "Wind MW": "wind_outage_outlook_mw"}
TIMING_POLICY = {
    "source_type": "assumption",
    "eligibility": "Start of the calendar day after the report date, in America/Chicago.",
    "intervals": "Market Hour is the UTC interval end, matching gridstatus's SPP outage reader.",
    "format_ref": "https://github.com/gridstatus/gridstatus/blob/main/gridstatus/spp.py",
    "selection": "Latest eligible report containing that exact forecast hour; no forward fill or interpolation.",
    "limitation": "Retrospective dated snapshots; exact historical publication times and revision availability are unverified.",
}


def parse_report(content: bytes, report_date: dt.date, source_ref: str) -> pd.DataFrame:
    if type(report_date) is not dt.date or not 2019 <= report_date.year <= 2025:
        raise ValueError("Outage outlooks support report dates from 2019 through 2025.")
    raw = pd.read_csv(io.BytesIO(content))
    raw.columns = raw.columns.str.strip()
    expected = {"Market Hour", "Outaged MW", *FUEL_COLUMNS}
    if set(raw.columns) != expected or len(raw.columns) != len(expected) or raw.empty:
        raise ValueError("Unexpected outage outlook schema or empty report.")
    text = raw["Market Hour"].astype(str)
    if not text.str.fullmatch(r"\d{1,2}/\d{1,2}/\d{4} \d{1,2}:\d{2}(:\d{2})?").all():
        raise ValueError("Unexpected Market Hour format; do not infer a changed timezone.")
    ends = pd.to_datetime(text, format="mixed", utc=True, errors="raise")
    starts = ends - pd.Timedelta(1, unit="h")
    report_day = pd.Timestamp(report_date, tz="UTC")
    if (starts.isna().any() or starts.duplicated().any() or not starts.eq(starts.dt.floor("h")).all()
            or len(starts) > 168 or (starts < report_day).any()
            or (starts >= report_day + pd.Timedelta(8, unit="d")).any()):
        raise ValueError("Outlook hours must be unique hourly intervals within the report horizon.")
    numbers = raw[["Outaged MW", *FUEL_COLUMNS]].apply(pd.to_numeric, errors="raise")
    if not np.isfinite(numbers.to_numpy()).all() or (numbers < 0).any().any():
        raise ValueError("Outage capacities must be finite, nonnegative MW values.")
    reconciles = np.isclose(numbers["Outaged MW"], numbers[list(FUEL_COLUMNS)].sum(axis=1), rtol=0, atol=.1)
    result = numbers[list(OUTLOOK_COLUMNS)].rename(columns=OUTLOOK_COLUMNS)
    # Preserve the raw source and row identity, but quarantine inconsistent
    # capacities. Do not silently replace the total or reuse an older report.
    result.loc[~reconciles, list(OUTLOOK_COLUMNS.values())] = np.nan
    result["outage_quality_status"] = np.where(reconciles, "ok", "fuel_total_mismatch")
    result["timestamp_utc"] = starts
    result["outage_report_date"] = str(report_date)
    result["outage_eligible_after_utc"] = pd.Timestamp(report_date + dt.timedelta(days=1), tz="America/Chicago").tz_convert("UTC")
    result["outage_report_ref"] = source_ref
    return result.sort_values("timestamp_utc").reset_index(drop=True)


def load_outlook_year(year: int) -> tuple[pd.DataFrame, dict]:
    if type(year) is not int or not 2019 <= year <= 2025:
        raise ValueError("Use pre-expansion SPP outlook years 2019 through 2025.")
    days = [stamp.date() for stamp in pd.date_range(f"{year}-01-01", f"{year}-12-31", freq="D")]
    frames, sources, incomplete = [], [], []
    if year < 2025:
        content, source = fetch_public_evidence(f"{BASE_URL}?path=/{year}/{year}.zip", f"generation_outage_{year}_annual")
        sources.append(source)
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            files = [name for name in archive.namelist() if not name.endswith("/")]
            expected = {f"{year}/{day:%m}/Capacity-Gen-Outage-{day:%Y%m%d}.csv" for day in days}
            if set(files) != expected or len(files) != len(expected):
                raise ValueError("Annual outage archive must contain one distinct report for each date.")
            for day in days:
                name = f"{year}/{day:%m}/Capacity-Gen-Outage-{day:%Y%m%d}.csv"
                frames.append(parse_report(archive.read(name), day, source["ref"] + "#" + name))
    else:
        # The 2025 annual ZIP is unavailable; use immutable daily CSV caches.
        for day in days:
            content, source = fetch_public_evidence(
                f"{BASE_URL}?path=/{year}/{day:%m}/Capacity-Gen-Outage-{day:%Y%m%d}.csv",
                f"generation_outage_{day:%Y%m%d}")
            sources.append(source)
            frames.append(parse_report(content, day, source["ref"]))
    for day, frame in zip(days, frames):
        if len(frame) != 168:
            incomplete.append({"report_date": str(day), "reported_hours": len(frame), "missing_from_168": 168-len(frame)})
    combined = pd.concat(frames, ignore_index=True)
    return combined, {"year": year, "reports": len(frames), "outlook_rows": len(combined),
                      "quarantined_capacity_rows": int(combined.outage_quality_status.ne("ok").sum()),
                      "incomplete_reports": incomplete, "sources": sources}


def select_outlooks(reports: pd.DataFrame) -> pd.DataFrame:
    """Choose a dated snapshot for each exact target hour before creating lags."""
    if reports.duplicated(["timestamp_utc", "outage_report_date"]).any():
        raise ValueError("Duplicate report/hour revisions must be reconciled explicitly.")
    eligible = reports[reports.outage_eligible_after_utc <= reports.timestamp_utc]
    return (eligible.sort_values(["timestamp_utc", "outage_eligible_after_utc"])
            .drop_duplicates("timestamp_utc", keep="last").reset_index(drop=True))


def add_outlooks(hourly_path: Path, out: Path, *, start_year=2019, end_year=2025) -> dict:
    if type(start_year) is not int or type(end_year) is not int or not 2019 <= start_year <= end_year <= 2025:
        raise ValueError("Use pre-expansion SPP outlook years 2019 through 2025.")
    if out.exists() or out.resolve() == hourly_path.resolve():
        raise ValueError("Choose a new output path and preserve the input.")
    hourly = read_hourly(hourly_path)
    if set(hourly.location_id) != {"SPP_SYSTEM"} or hourly.timestamp_utc.max() >= pd.Timestamp("2026-01-01", tz="UTC"):
        raise ValueError("Outage outlooks require the reviewed pre-2026 SPP_SYSTEM mapping.")
    if set(OUTLOOK_COLUMNS.values()).union({"outage_report_date", "outage_eligible_after_utc", "outage_report_ref", "outage_quality_status", "outage_source_ref"}).intersection(hourly):
        raise ValueError("Existing outage outlooks cannot be overwritten.")
    frames, sources = [], []
    for year in range(start_year, end_year+1):
        frame, source = load_outlook_year(year)
        frames.append(frame)
        sources.append(source)
    selected = select_outlooks(pd.concat(frames, ignore_index=True))
    result = hourly.merge(selected, how="left", on="timestamp_utc", validate="one_to_one")
    result["outage_source_ref"] = str(out.with_suffix(".outages.json"))
    known = result.outage_outlook_mw.notna()
    report = {"source_type": "data", "ref": "https://portal.spp.org/pages/capacity-of-generation-on-outage",
              "description": "Generation-outage outlooks including submitted CROW outages and offered OUTAGE commit status; not measured forced outages.",
              "timing_policy": TIMING_POLICY, "sources": sources,
              "input_sha256": fingerprint(hourly_path), "hourly_rows": len(result),
              "known_hours": int(known.sum()), "unknown_hours": int((~known).sum()),
              "quarantined_selected_hours": int(result.outage_quality_status.eq("fuel_total_mismatch").sum()),
              "no_future_report_dates": bool((result.loc[known, "outage_eligible_after_utc"] <= result.loc[known, "timestamp_utc"]).all())}
    out.parent.mkdir(parents=True, exist_ok=True)
    result.to_parquet(out, index=False)
    report["output_sha256"] = fingerprint(out)
    write_json(out.with_suffix(".outages.json"), report)
    for suffix in (".weather.json", ".area.json", ".quality.json", ".generation.json", ".evidence.json"):
        source = hourly_path.with_suffix(suffix)
        if source.exists():
            shutil.copyfile(source, out.with_suffix(suffix))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hourly", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--start-year", type=int, default=2019)
    parser.add_argument("--end-year", type=int, default=2025)
    args = parser.parse_args()
    report = add_outlooks(args.hourly, args.out, start_year=args.start_year, end_year=args.end_year)
    print(json.dumps({key: report[key] for key in ("hourly_rows", "known_hours", "unknown_hours", "no_future_report_dates")}))


if __name__ == "__main__":
    main()
