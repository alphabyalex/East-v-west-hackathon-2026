"""Join curated SPP emergency observations without inventing training labels.

Unreported hours stay unknown. Date-only advisories, local transmission events,
and events in a different balancing authority never become system EEA hours.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np
import pandas as pd

from pipeline.common import ROOT, fingerprint, read_hourly, write_json


def validate_catalog(catalog: dict) -> None:
    """Require traceable sources and unambiguous event identifiers before joining."""
    if not isinstance(catalog, dict) or not isinstance(catalog.get("events"), list):
        raise ValueError("Evidence catalog requires an events list.")
    sources = catalog.get("sources")
    if not isinstance(sources, dict) or not sources:
        raise ValueError("Evidence catalog requires source provenance.")
    for source in sources.values():
        if not isinstance(source, dict):
            raise ValueError("Each evidence source must be an object.")
        ref, digest = source.get("ref"), source.get("sha256")
        if (not isinstance(ref, str) or urlsplit(ref).scheme != "https" or not urlsplit(ref).hostname
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise ValueError("Evidence sources require HTTPS references and SHA-256 fingerprints.")
        retrieved = pd.Timestamp(source.get("retrieved_utc"))
        if pd.isna(retrieved) or retrieved.tzinfo is None:
            raise ValueError("Evidence retrieval times require explicit UTC offsets.")
    identifiers = set()
    for event in catalog["events"]:
        if not isinstance(event, dict):
            raise ValueError("Each event must be an object.")
        identifier = event.get("id")
        if (not isinstance(identifier, str) or not re.fullmatch(r"[a-z0-9_-]+", identifier)
                or identifier in identifiers):
            raise ValueError("Event IDs must be unique lowercase identifiers without separators.")
        identifiers.add(identifier)
        refs = event.get("source_ids")
        if (not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or ref not in sources for ref in refs)
                or len(set(refs)) != len(refs)):
            raise ValueError("Every event must reference declared, distinct evidence sources.")
        for field in ("region_id", "kind", "time_precision", "end_status"):
            if not isinstance(event.get(field), str) or not event[field].strip():
                raise ValueError(f"Each event requires {field}.")


def confirmed_eea_intervals(catalog: dict, region_id: str) -> list[tuple]:
    validate_catalog(catalog)
    intervals = []
    for event in catalog["events"]:
        if (event["region_id"] != region_id or event["kind"] not in {"EEA1", "EEA2", "EEA3"}
                or event["time_precision"] != "interval" or event["end_status"] != "confirmed"):
            continue
        start, end = pd.Timestamp(event["start"]), pd.Timestamp(event["end"])
        if start.tzinfo is None or end.tzinfo is None or pd.isna(start) or pd.isna(end) or end <= start:
            raise ValueError("Confirmed event intervals require increasing timestamps with explicit UTC offsets.")
        intervals.append((start.tz_convert("UTC"), end.tz_convert("UTC"), event["id"]))
    return intervals


def annotate_hours(frame: pd.DataFrame, catalog: dict, location_id: str, region_id: str) -> pd.DataFrame:
    """Record union overlap in minutes, using half-open hourly/event intervals."""
    if location_id not in set(frame.location_id):
        raise ValueError("The requested grid location is absent from the hourly input.")
    columns = {"observed_eea_minutes", "observed_eea_event_ids"}
    if columns.intersection(frame.columns):
        raise ValueError("Existing event observations cannot be overwritten.")
    # This explicit mapping is valid for the historical, pre-expansion SPP load.
    if location_id != "SPP_SYSTEM" or region_id != "SPP_BA_PRE_2026":
        raise ValueError("Only the reviewed pre-2026 SPP_SYSTEM mapping is supported.")
    selected = frame.location_id.eq(location_id)
    times = frame.loc[selected, "timestamp_utc"]
    if (not isinstance(times.dtype, pd.DatetimeTZDtype) or times.isna().any()
            or not times.eq(times.dt.floor("h")).all() or times.duplicated().any()):
        raise ValueError("Event observations require unique timezone-aware hourly interval starts.")
    if (frame.loc[selected, "timestamp_utc"] >= pd.Timestamp("2026-01-01", tz="UTC")).any():
        raise ValueError("Post-2025 data requires a separately reviewed East/West region mapping.")
    intervals = confirmed_eea_intervals(catalog, region_id)
    output = frame.copy()
    output["observed_eea_minutes"] = np.nan
    output["observed_eea_event_ids"] = ""
    # Visit only hours touched by a confirmed interval. Positional writes also
    # preserve valid frames with repeated pandas index labels.
    candidates = np.zeros(len(output), dtype=bool)
    for start, end, _ in intervals:
        candidates |= (selected & (frame.timestamp_utc >= start.floor("h")) & (frame.timestamp_utc < end)).to_numpy()
    minutes_col = output.columns.get_loc("observed_eea_minutes")
    ids_col = output.columns.get_loc("observed_eea_event_ids")
    for index in np.flatnonzero(candidates):
        start = pd.Timestamp(output.timestamp_utc.iloc[index])
        end = start + pd.Timedelta(1, unit="h")
        overlaps = sorted((max(start, a), min(end, b), identifier)
                          for a, b, identifier in intervals if a < end and b > start)
        if not overlaps:
            continue
        total = pd.Timedelta(0, unit="s")
        left, right = overlaps[0][:2]
        for a, b, _ in overlaps[1:]:
            if a <= right:
                right = max(right, b)
            else:
                total += right - left
                left, right = a, b
        total += right - left
        output.iat[index, minutes_col] = total.total_seconds() / 60
        output.iat[index, ids_col] = ";".join(sorted({item[2] for item in overlaps}))
    return output


def prepare_events(hourly: Path, catalog_path: Path, out: Path) -> dict:
    if out.resolve() == hourly.resolve():
        raise ValueError("Write event observations to a new file; preserve the input.")
    if out.exists():
        raise ValueError("Output already exists; choose a new path.")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    frame = annotate_hours(read_hourly(hourly), catalog, "SPP_SYSTEM", "SPP_BA_PRE_2026")
    cases = frame[frame.observed_eea_minutes.notna()]
    details = cases[["timestamp_utc", "location_id", "load_mw", "observed_eea_minutes", "observed_eea_event_ids"]].copy()
    details["hour_start_central"] = details.timestamp_utc.dt.tz_convert("America/Chicago").astype(str)
    if "temperature_c" in cases:
        details["temperature_c"] = cases.temperature_c
    summary = {
        "source_type": "data", "ref": str(catalog_path), "catalog_sha256": fingerprint(catalog_path),
        "input_ref": str(hourly), "input_sha256": fingerprint(hourly),
        "coverage": "Selected documented events only; unreported hours remain unknown.",
        "observed_eea_hours": float(cases.observed_eea_minutes.sum() / 60),
        "hourly_buckets_touched": len(cases), "confirmed_negative_hours": 0,
        "training_labels_created": False,
        "interpretation": "Historical emergency exposure; not measured site interruptions or a future prediction.",
        "matching_hours": json.loads(details.to_json(orient="records", date_format="iso")),
        "sources": catalog["sources"],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(out, index=False)
    write_json(out.with_suffix(".evidence.json"), summary)
    details.to_csv(out.with_suffix(".events.csv"), index=False)
    for suffix in (".weather.json", ".area.json", ".quality.json"):
        source = hourly.with_suffix(suffix)
        if source.exists():
            shutil.copyfile(source, out.with_suffix(suffix))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hourly", required=True, type=Path)
    parser.add_argument("--catalog", type=Path, default=ROOT / "docs/spp-event-evidence.json")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    result = prepare_events(args.hourly, args.catalog, args.out)
    print(json.dumps({key: result[key] for key in ("observed_eea_hours", "hourly_buckets_touched", "training_labels_created")}, indent=2))


if __name__ == "__main__":
    main()
