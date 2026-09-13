"""Offline high-wind / low-price opportunity screen for flexible SPP load.

This is an opportunity PROXY, not a measurement of wind curtailment or headroom.
An hour qualifies when same-footprint system wind / system load >= the declared
share threshold AND the exact price location's LMP <= the declared price ceiling.
Generic binding constraints do not establish direction or relief from extra load.
System wind must never be divided by an individual zone's load. Retrospective
observations combined with day-ahead prices are not a verified forecasting signal.

Input is one row per location and UTC hour. system_wind_mw and system_load_mw
refer to the explicitly named common footprint; lmp_usd_mwh is local to location_id.
Reuse pipeline.ingest.load_dataset('fuel_mix'/'lmp') and the historical normalizer
in pipeline.generation to prepare observations; this module never fetches data.
The current gridstatus fuel-mix feed may aggregate SPP and SWPW: an adapter must
establish its footprint before matching it to historical SPP_SYSTEM load.

Energy output is a declared flexible-load scenario, not measured excess supply.
Annual values require one complete observed calendar year with no unknown flags;
partial history is reported for its actual period without extrapolation.
"""
from __future__ import annotations

from dataclasses import dataclass
from numbers import Real
from typing import Mapping

import numpy as np
import pandas as pd


def source(value: object) -> dict:
    """Validate a source without upgrading it or discarding its reference."""
    if not isinstance(value, Mapping) or set(value) != {"source_type", "ref"}:
        raise ValueError("A source requires exactly source_type and ref.")
    if value["source_type"] not in {"data", "model", "assumption"}:
        raise ValueError("Unsupported source_type.")
    ref = value["ref"]
    if not isinstance(ref, str) or not ref.strip():
        raise ValueError("A source needs a nonempty reference.")
    if value["source_type"] != "assumption" and any(
        word in ref.lower() for word in ("placeholder", "mock://", "illustrative")
    ):
        raise ValueError("Placeholder references must remain assumptions.")
    return dict(value)


def sourced(value: float | int | None, origin: Mapping) -> dict:
    return {"value": value, **source(origin)}


def finite_number(value: object, name: str, *, minimum=None, maximum=None) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real) or not np.isfinite(value):
        raise ValueError(f"{name} must be a finite number, not a boolean.")
    number = float(value)
    if minimum is not None and number < minimum or maximum is not None and number > maximum:
        raise ValueError(f"{name} is outside its allowed range.")
    return number


@dataclass(frozen=True)
class WindPolicy:
    minimum_wind_share: float = .5
    maximum_lmp_usd_mwh: float = 0.0

    def __post_init__(self):
        finite_number(self.minimum_wind_share, "minimum_wind_share", minimum=0, maximum=1)
        finite_number(self.maximum_lmp_usd_mwh, "maximum_lmp_usd_mwh")

    @property
    def source(self) -> dict:
        return {
            "source_type": "assumption",
            "ref": "pipeline/wind_signal.py:WindPolicy; declared screening thresholds; "
                   f"wind_share>={self.minimum_wind_share:g}, lmp_usd_mwh<={self.maximum_lmp_usd_mwh:g}; "
                   "high-wind/low-price opportunity proxy, not measured wind curtailment",
        }


INPUT_COLUMNS = ("system_wind_mw", "system_load_mw", "lmp_usd_mwh")


def wind_oversupply_hours(
    hourly: pd.DataFrame, *, sources: Mapping[str, Mapping],
    wind_scope: str, load_scope: str, policy: WindPolicy | None = None,
) -> pd.DataFrame:
    """Classify observed hours; nullable Boolean keeps absent evidence unknown.

    sources maps each INPUT_COLUMNS name to {source_type, ref}. Scope equality is
    necessary, but the caller must also establish actual common coverage. One
    system observation is allowed alongside several explicitly mapped price nodes.
    Contradictory system observations at the same timestamp are rejected.
    """
    policy = WindPolicy() if policy is None else policy
    if not isinstance(policy, WindPolicy):
        raise ValueError("policy must be a WindPolicy.")
    if not isinstance(wind_scope, str) or not wind_scope.strip() or wind_scope != load_scope:
        raise ValueError("Wind and load must have the same explicitly identified footprint.")
    if not isinstance(sources, Mapping) or set(sources) != set(INPUT_COLUMNS):
        raise ValueError(f"sources must name exactly {INPUT_COLUMNS}.")
    origins = {name: source(sources[name]) for name in INPUT_COLUMNS}
    required = {"timestamp_utc", "location_id", *INPUT_COLUMNS}
    if not required.issubset(hourly.columns) or hourly.empty:
        raise ValueError("Nonempty hourly input needs timestamp_utc, location_id, system wind/load and local LMP.")
    frame = hourly[["timestamp_utc", "location_id", *INPUT_COLUMNS]].copy()
    times = [pd.Timestamp(value) for value in frame.timestamp_utc]
    if any(pd.isna(value) or value.tzinfo is None for value in times):
        raise ValueError("Every timestamp must have an explicit timezone.")
    frame["timestamp_utc"] = pd.to_datetime(times, utc=True)
    if not frame.timestamp_utc.eq(frame.timestamp_utc.dt.floor("h")).all():
        raise ValueError("Input must contain hourly interval-start timestamps.")
    if not frame.location_id.map(lambda value: isinstance(value, str) and bool(value.strip())).all():
        raise ValueError("Every location_id must be a nonempty string.")
    if frame.duplicated(["location_id", "timestamp_utc"]).any():
        raise ValueError("Duplicate location/hour observations must be reconciled first.")
    for name in INPUT_COLUMNS:
        if frame[name].map(lambda value: isinstance(value, (bool, np.bool_))).any():
            raise ValueError(f"{name} cannot contain boolean observations.")
        frame[name] = pd.to_numeric(frame[name], errors="raise").astype(float)
        if np.isinf(frame[name]).any():
            raise ValueError(f"{name} contains infinity.")
        if name != "lmp_usd_mwh" and frame[name].lt(0).any():
            raise ValueError(f"{name} cannot be negative.")
    for name in ("system_wind_mw", "system_load_mw"):
        if frame.groupby("timestamp_utc")[name].nunique(dropna=False).gt(1).any():
            raise ValueError(f"Conflicting {name} for the same system and hour.")
    known = frame[list(INPUT_COLUMNS)].notna().all(axis=1) & frame.system_load_mw.gt(0)
    with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
        frame["wind_share"] = frame.system_wind_mw / frame.system_load_mw.where(frame.system_load_mw.gt(0))
    if np.isinf(frame.wind_share).any():
        raise ValueError("Wind/load ratio overflow; inspect input units and scale.")
    flag = frame.wind_share.ge(policy.minimum_wind_share) & frame.lmp_usd_mwh.le(policy.maximum_lmp_usd_mwh)
    frame["wind_oversupply_proxy"] = flag.astype("boolean").where(known, pd.NA)
    frame = frame.sort_values(["location_id", "timestamp_utc"]).reset_index(drop=True)
    frame.attrs = {"method": "high_wind_low_local_price_v1", "scope": wind_scope,
                   "sources": origins, "policy_source": policy.source}
    return frame


def summarize_wind(
    hourly: pd.DataFrame, *, sources: Mapping[str, Mapping], wind_scope: str, load_scope: str,
    flexible_load_mw: Mapping, available_fraction: Mapping, policy: WindPolicy | None = None,
) -> list[dict]:
    """Per-location screening counts and explicitly assumed energy opportunity.

    flexible_load_mw and available_fraction each use {value,source_type,ref}.
    available_fraction is the assumed share of flexible capacity that can ramp up
    in every selected hour. It is NOT local transmission headroom or measured
    curtailed wind. No zone shares, site exposure factor or growth is inferred.
    """
    def parameter(value, name, maximum=None):
        if not isinstance(value, Mapping) or set(value) != {"value", "source_type", "ref"}:
            raise ValueError(f"{name} requires value, source_type and ref.")
        origin = source({key: value[key] for key in ("source_type", "ref")})
        return finite_number(value["value"], name, minimum=0, maximum=maximum), origin

    load, load_origin = parameter(flexible_load_mw, "flexible_load_mw")
    fraction, fraction_origin = parameter(available_fraction, "available_fraction", 1)
    frame = wind_oversupply_hours(hourly, sources=sources, wind_scope=wind_scope, load_scope=load_scope, policy=policy)
    observation_source = {
        "source_type": "assumption" if any(v["source_type"] == "assumption" for v in frame.attrs["sources"].values())
                       else "model" if any(v["source_type"] == "model" for v in frame.attrs["sources"].values()) else "data",
        "ref": "; ".join(f"{name}: {origin['ref']}" for name, origin in frame.attrs["sources"].items()),
    }
    proxy_source = {"source_type": "assumption", "ref": frame.attrs["policy_source"]["ref"] + "; " + observation_source["ref"]}
    energy_source = {
        "source_type": "assumption",
        "ref": f"{proxy_source['ref']}; flexible_load_mw={load:g} ({load_origin['ref']}); "
               f"available_fraction={fraction:g} ({fraction_origin['ref']}); "
               "one-hour intervals; scenario capacity times proxy hours, not measured recoverable wind",
    }
    output = []
    for location, group in frame.groupby("location_id", sort=True):
        flags = group.wind_oversupply_proxy
        start, end = group.timestamp_utc.min(), group.timestamp_utc.max() + pd.Timedelta(1, unit="h")
        expected_start = pd.Timestamp(year=start.year, month=1, day=1, tz="UTC")
        expected_end = pd.Timestamp(year=start.year + 1, month=1, day=1, tz="UTC")
        complete = start == expected_start and end == expected_end and flags.notna().all()
        complete = bool(complete and len(group) == (expected_end - expected_start) / pd.Timedelta(1, unit="h"))
        count = int(flags.sum())
        mwh = finite_number(count * load * fraction, "scenario MWh", minimum=0) if flags.notna().any() else None
        missing_hours = int((end - start) / pd.Timedelta(1, unit="h")) - len(group)
        annual_source = energy_source if complete else {
            "source_type": "assumption",
            "ref": "unavailable: requires one complete calendar year of evaluable hourly observations; " + energy_source["ref"],
        }
        output.append({
            "location_id": location, "method": frame.attrs["method"], "system_scope": wind_scope,
            "period_start_utc": start.isoformat(), "period_end_exclusive_utc": end.isoformat(),
            "observed_hours": sourced(len(group), observation_source),
            "evaluable_hours": sourced(int(flags.notna().sum()), observation_source),
            "unknown_hours": sourced(int(flags.isna().sum()), observation_source),
            "missing_interval_hours": sourced(missing_hours, observation_source),
            "proxy_hours": sourced(count if flags.notna().any() else None, proxy_source),
            "wind_absorption_mwh_in_observed_hours": sourced(mwh, energy_source),
            "wind_absorption_mwh_per_year": sourced(mwh if complete else None, annual_source),
            "basis": "Flexible-load energy scenario over evaluable observed high-wind/low-price hours only; "
                     "wind curtailment, local deliverability and recoverable MW are unobserved.",
        })
    return output
