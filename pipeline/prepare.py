"""Normalize the legacy SPP load archive and join separately sourced evidence."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from pipeline.common import fingerprint, read_hourly, write_json

LOAD_AREAS = ("CSWS", "EDE", "GRDA", "INDN", "KACY", "KCPL", "LES", "MPS", "NPPD",
              "OKGE", "OPPD", "SECI", "SPRM", "SPS", "WAUE", "WFEC", "WR")


def normalize_legacy_load(raw_path: Path, output_path: Path) -> dict:
    if output_path.exists():
        raise ValueError(f"Output already exists: {output_path}. Choose a new path.")
    raw = pd.read_parquet(raw_path)
    raw.columns = raw.columns.str.strip()
    if not {"MarketHour", *LOAD_AREAS}.issubset(raw.columns):
        raise ValueError("Expected the legacy SPP hourly-load archive with all 17 component load areas.")
    original_rows = len(raw)
    raw = raw.drop_duplicates().copy()
    exact_duplicates = original_rows - len(raw)
    # This legacy SPP field is hour-ending UTC, despite the 'MarketHour' name.
    # Matches gridstatus.SPP._handle_market_end_to_interval used by the public loader.
    raw["timestamp_utc"] = pd.to_datetime(raw.MarketHour, utc=True, format="mixed") - pd.Timedelta(1, unit="h")
    raw["load_mw"] = raw[list(LOAD_AREAS)].apply(pd.to_numeric, errors="raise").sum(axis=1, min_count=len(LOAD_AREAS))
    conflict_mask = raw.duplicated("timestamp_utc", keep=False)
    conflict_times = raw.loc[conflict_mask, "timestamp_utc"].unique()
    # Conflicting revisions are unknown, not averaged or arbitrarily chosen.
    raw.loc[conflict_mask, "load_mw"] = float("nan")
    hourly = raw[["timestamp_utc", "load_mw"]].drop_duplicates("timestamp_utc").set_index("timestamp_utc").sort_index()
    expected = pd.date_range(hourly.index.min(), hourly.index.max(), freq="h")
    missing_hours = len(expected.difference(hourly.index))
    hourly = hourly.reindex(expected).rename_axis("timestamp_utc").reset_index()
    hourly["location_id"] = "SPP_SYSTEM"
    hourly = hourly[["timestamp_utc", "location_id", "load_mw"]]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    hourly.to_parquet(output_path, index=False)
    read_hourly(output_path)
    source_path = raw_path.with_suffix(".source.json")
    report = {
        "status": "load_only_labels_required", "operator": "SPP",
        "source_type": "data", "raw_path": str(raw_path), "raw_sha256": fingerprint(raw_path),
        "source_manifest": str(source_path) if source_path.exists() else None,
        "input_rows": original_rows, "exact_duplicate_rows_removed": exact_duplicates,
        "conflicting_hours_marked_unknown": len(conflict_times),
        "conflicting_hour_timestamps": [str(value) for value in conflict_times],
        "missing_hours_inserted_as_unknown": missing_hours,
        "output_hours": len(hourly), "known_load_hours": int(hourly.load_mw.notna().sum()),
        "location_meaning": "System sum of the 17 load areas in the source; not a pricing node or site estimate.",
        "aggregation": "Sum with all 17 components required; MarketHour UTC hour-ending converted to interval start.",
        "label_status": "No labels invented. Join separately reviewed hourly event or proxy evidence.",
    }
    write_json(output_path.with_suffix(".quality.json"), report)
    return report


def join_evidence(hourly_path: Path, evidence_path: Path, output_path: Path) -> dict:
    if output_path.exists():
        raise ValueError(f"Output already exists: {output_path}. Choose a new path.")
    hourly = read_hourly(hourly_path)
    evidence = pd.read_parquet(evidence_path) if evidence_path.suffix == ".parquet" else pd.read_csv(evidence_path)
    keys = {"timestamp_utc", "location_id"}
    if not keys.issubset(evidence.columns) or len(evidence.columns) <= 2:
        raise ValueError("Evidence requires timestamp_utc, location_id, and at least one event/proxy/sensor column.")
    times = [pd.Timestamp(value) for value in evidence.timestamp_utc]
    if any(pd.isna(value) or value.tzinfo is None for value in times):
        raise ValueError("Evidence timestamps must include an explicit UTC offset.")
    evidence["timestamp_utc"] = pd.to_datetime(evidence.timestamp_utc, utc=True)
    if not evidence.timestamp_utc.eq(evidence.timestamp_utc.dt.floor("h")).all():
        raise ValueError("Evidence must use hourly interval-start timestamps.")
    if evidence.location_id.isna().any():
        raise ValueError("Evidence location_id cannot be missing.")
    evidence["location_id"] = evidence.location_id.astype(str)
    if evidence.duplicated(["timestamp_utc", "location_id"]).any():
        raise ValueError("Duplicate evidence hours; reconcile revisions first.")
    overlap = (set(hourly.columns) & set(evidence.columns)) - keys
    if overlap:
        raise ValueError(f"Evidence would overwrite existing columns: {sorted(overlap)}")
    merged = hourly.merge(evidence, on=["timestamp_utc", "location_id"], how="left", validate="one_to_one", indicator=True)
    matched = int(merged._merge.eq("both").sum())
    if not matched:
        raise ValueError("No matching location/hour keys. Check region IDs and interval-start UTC conventions.")
    merged = merged.drop(columns="_merge")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(output_path, index=False)
    read_hourly(output_path)
    report = {"hourly_sha256": fingerprint(hourly_path), "evidence_sha256": fingerprint(evidence_path),
              "matched_hours": matched, "unmatched_hours_left_unknown": len(merged) - matched,
              "unused_evidence_rows": len(evidence) - matched,
              "input_paths": [str(hourly_path), str(evidence_path)]}
    write_json(output_path.with_suffix(".quality.json"), report)
    return report
