"""Offline price bins and cheap read-only attachment to grid-impact responses.

Value per available MW = sum(max(0, -day-ahead LMP) * one hour) over
evaluable system wind/load >= .5 AND exact-point LMP <= 0 hours. The $0
comparison deliberately excludes an invented normal retail tariff. Multiplying
by declared available MW is a counterfactual wholesale energy value, not a bill
saving, guaranteed negative-price payment, or measured recoverable wind.
No raw reads, classification, or external fetching happen in attach_power.
"""
import hashlib
import json
import math
import os
from pathlib import Path

from . import grid_impact as grid

INPUT = Path("data/processed/wind_audits/proxy_ver_2025_v1/hourly.parquet")
INPUT_SHA = "54f0692fdbf0fca2fd724e0fed699bf350dad873cb009afea387e3faafe4b2f2"
FILENAME = "cheap_power_v1.json"
BASIS = ("Observed qualifying hours only: system wind/load >= 0.5 and point day-ahead LMP <= 0 USD/MWh. "
         "USD = selected load MW * explicitly assumed upward available fraction * sum(-LMP * 1 hour). "
         "Reference price is 0 USD/MWh; zero-priced hours contribute energy but no dollar premium. "
         "Wholesale energy component only: excludes tariffs, network charges, hedges and retail pass-through. "
         "Not guaranteed savings, actual curtailed wind recovery, or a forecast; no annual extrapolation. "
         "Month and hour coordinates are UTC; missing observations are excluded, never filled.")


def datum(value, ref):
    # Timing conversion and the screen policy are assumptions even before the
    # frontend adds counterfactual capacity. Do not relabel these as measured savings.
    return {"value": value, "source_type": "assumption", "ref": ref}


def compile_power(directory, input_path=INPUT):
    """Explicit offline command, pinned to the reviewed hourly source and snapshots."""
    import pandas as pd
    input_path, directory = Path(input_path), Path(directory)
    if hashlib.sha256(input_path.read_bytes()).hexdigest() != INPUT_SHA:
        raise ValueError("Hourly evidence changed: review before compiling price bins")
    hourly = pd.read_parquet(input_path)
    locations = {}
    for location, rows in hourly.groupby("location_id", sort=True):
        snapshot = grid.read_grid_impact_snapshot(location, directory / f"{location}.snapshot.json", required=True)
        stamps = pd.DatetimeIndex(rows.timestamp_utc)
        expected = pd.date_range("2025-01-01", "2026-01-01", freq="h", inclusive="left", tz="UTC")
        if list(stamps) != list(expected):
            raise ValueError("Expected exact complete UTC hour grid without duplicates")
        known = rows[["system_load_mw", "system_wind_mw", "lmp_usd_mwh"]].map(math.isfinite).all(axis=1) & (rows.system_load_mw > 0)
        proxy = known & (rows.system_wind_mw / rows.system_load_mw >= .5) & (rows.lmp_usd_mwh <= 0)
        context = snapshot["evidence"][snapshot["evidence_context"]["wind"].removeprefix(grid.EVIDENCE_PREFIX)]
        if int(proxy.sum()) != context["proxy_hours"]["value"] or int((~known).sum()) != snapshot["coverage"]["wind"]["unknown_hours"]["value"]:
            raise ValueError("Price evidence does not reproduce the published wind screen")
        ref = (f"{INPUT.as_posix()}; sha256={INPUT_SHA}; exact settlement point {location}; "
               "original source lineage in grid-impact evidence_context.wind; " + BASIS)
        bins = []
        for month in range(1, 13):
            for hour in range(24):
                mask = (stamps.month == month) & (stamps.hour == hour)
                selected = proxy & mask
                bin_ref = f"grid-power://{location}/month/{month}/hour-utc/{hour}; lineage: cheap_power.input_source; formula: cheap_power.basis"
                bins.append({"month": datum(month, bin_ref), "hour_utc": datum(hour, bin_ref),
                    "proxy_hours": datum(int(selected.sum()), bin_ref),
                    "unknown_hours": datum(int((~known & mask).sum()), bin_ref),
                    "usd_per_available_mw": datum(math.fsum(-float(v) for v in rows.loc[selected, "lmp_usd_mwh"]), bin_ref)})
        locations[location] = {"status": "observed_hours_only", "source_location_id": location,
            "snapshot_sha256": grid._digest(snapshot), "basis": BASIS, "input_source": {"source_type": "assumption", "ref": ref},
            "reference_price_usd_mwh": datum(0., "Declared zero wholesale comparison price; not a normal retail tariff"),
            "proxy_hours": datum(int(proxy.sum()), ref),
            "unknown_hours": datum(int((~known).sum()), ref),
            "usd_per_available_mw": datum(math.fsum(b["usd_per_available_mw"]["value"] for b in bins), ref), "bins": bins}
    result = {"schema_version": "grid-power-v1", "locations": locations}
    document = {"result": result, "sha256": grid._digest(result)}
    # Canonical single-line encoding survives Windows checkout without hash drift.
    with (directory / FILENAME).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(document, stream, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return {key: value["usd_per_available_mw"]["value"] for key, value in locations.items()}


def attach_power(result):
    """Add optional precompiled price evidence, bound to the exact serving snapshot.

    Missing price evidence stays unavailable while wind/carbon remain usable.
    Invalid evidence fails through the route's existing 503 mapping.
    """
    unavailable = {"status": "unavailable", "reason": "No matched precompiled hourly price evidence for this location", "bins": []}
    result["cheap_power"] = unavailable
    if result["coverage"]["wind"]["status"] == "unavailable":
        return result
    directory = Path(os.environ.get("FLUXLINE_GRID_IMPACT_DIR", grid.LIVE_SNAPSHOT_DIRECTORY)).resolve()
    try:
        document = json.loads((directory / FILENAME).read_text(encoding="utf-8"), object_pairs_hook=grid._unique_object, parse_constant=grid._nonfinite_constant)
    except FileNotFoundError:
        return result
    try:
        body = document["result"]
        if document["sha256"] != grid._digest(body) or body["schema_version"] != "grid-power-v1":
            raise ValueError("Invalid price evidence checksum/schema")
        point = result.get("location_mapping", {}).get("source_location_id", result["location_id"])
        power = body["locations"].get(point)
        if power is None:
            return result
        snapshot = json.loads((directory / f"{point}.snapshot.json").read_text(encoding="utf-8"))
        original = snapshot["result"]
        if snapshot["result_sha256"] != grid._digest(original):
            raise ValueError("Snapshot changed or became invalid during price read")
        if power["snapshot_sha256"] != snapshot["result_sha256"]:
            return result  # a new wind snapshot requires matching offline price compilation
        if (original["coverage"] != result["coverage"] or original["evidence_context"] != result["evidence_context"]
                or any(original[key]["value"] != result[key]["value"] for key in grid.UNITS)):
            return result  # prevent mixing versions across concurrent artifact publication
        validate_power(power, point)
        result["cheap_power"] = power
    except (KeyError, TypeError, OverflowError) as error:
        raise ValueError("Malformed precompiled price evidence") from error
    return result


def validate_power(power, point):
    if power["status"] != "observed_hours_only" or power["source_location_id"] != point or power["basis"] != BASIS:
        raise ValueError("Invalid price basis or identity")
    def number(row, key, integer=False):
        item = row[key]
        value = item["value"]
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0
                or (integer and int(value) != value) or item["source_type"] != "assumption"
                or not isinstance(item["ref"], str) or not item["ref"].strip()):
            raise ValueError("Invalid sourced price aggregate")
        return value
    if number(power, "reference_price_usd_mwh") != 0 or len(power["bins"]) != 288:
        raise ValueError("Invalid price grid")
    for index, row in enumerate(power["bins"]):
        if number(row, "month", True) != index // 24 + 1 or number(row, "hour_utc", True) != index % 24:
            raise ValueError("Invalid or duplicate month/hour coordinate")
        count, unknown = number(row, "proxy_hours", True), number(row, "unknown_hours", True)
        dollars = number(row, "usd_per_available_mw")
        if count + unknown > 31 or (count == 0 and dollars != 0):
            raise ValueError("Price value without qualifying hours")
    for key in ("proxy_hours", "unknown_hours", "usd_per_available_mw"):
        total = number(power, key, key != "usd_per_available_mw")
        if not math.isclose(total, math.fsum(b[key]["value"] for b in power["bins"]), rel_tol=1e-12, abs_tol=1e-9):
            raise ValueError("Price bins do not reconcile")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=grid.LIVE_SNAPSHOT_DIRECTORY)
    args = parser.parse_args()
    print(json.dumps(compile_power(args.directory), sort_keys=True))
