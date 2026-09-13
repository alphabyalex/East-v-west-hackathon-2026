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
import hashlib
import io
import json
from numbers import Real
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd

from pipeline import ingest
from pipeline.common import ROOT, fingerprint, read_hourly
from pipeline.generation import normalize_generation


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


def _complete_hourly_power(raw: pd.DataFrame, value_column: str) -> pd.DataFrame:
    """Mean MW or mean price only when interval coverage fills the whole hour.

    gridstatus returns interval-start/end timestamps. Never sum MW as if it were
    energy, forward-fill missing intervals, or combine overlapping revisions.
    Five-minute and hourly intervals are supported; an hour must use one cadence.
    """
    names = ["Interval Start", "Interval End", value_column]
    if not set(names).issubset(raw.columns) or raw.empty:
        raise ValueError(f"Cached observations require nonempty {names}.")
    frame = raw[names].drop_duplicates().copy()
    for name in names[:2]:
        parsed = [pd.Timestamp(value) for value in frame[name]]
        if any(pd.isna(value) or value.tzinfo is None for value in parsed):
            raise ValueError("Cached interval timestamps require explicit timezones.")
        frame[name] = pd.to_datetime(parsed, utc=True)
    if frame.duplicated("Interval Start").any():
        raise ValueError("Conflicting interval revisions must be reconciled before screening.")
    duration = (frame["Interval End"] - frame["Interval Start"]).dt.total_seconds()
    if not duration.isin([300., 3600.]).all():
        raise ValueError("Only complete five-minute or hourly source intervals are supported.")
    if not frame["Interval Start"].eq(frame["Interval Start"].dt.floor("5min")).all():
        raise ValueError("Source intervals must align to five-minute boundaries.")
    hour = frame["Interval Start"].dt.floor("h")
    if (frame["Interval End"] > hour + pd.Timedelta(1, unit="h")).any():
        raise ValueError("Source intervals cannot cross UTC-hour boundaries.")
    if frame[value_column].map(lambda value: isinstance(value, (bool, np.bool_))).any():
        raise ValueError("Cached numeric observations cannot be booleans.")
    frame[value_column] = pd.to_numeric(frame[value_column], errors="raise").astype(float)
    if np.isinf(frame[value_column]).any():
        raise ValueError("Cached numeric observations cannot be infinite.")
    frame["duration"] = duration
    frame["timestamp_utc"] = hour
    rows = []
    for stamp, group in frame.groupby("timestamp_utc", sort=True):
        if group.duration.nunique() != 1:
            raise ValueError("Overlapping mixed-cadence observations must be reconciled first.")
        expected = int(3600 / group.duration.iloc[0])
        known = len(group) == expected and group[value_column].notna().all()
        rows.append({"timestamp_utc": stamp, value_column: float(group[value_column].mean()) if known else np.nan})
    return pd.DataFrame(rows)


def prepare_wind_inputs(
    generation: pd.DataFrame, system_load: pd.DataFrame, prices: pd.DataFrame, *,
    price_locations: Mapping[str, str], market: str, generation_format: str = "gridstatus",
) -> pd.DataFrame:
    """Normalize existing SPP cache formats without geographic or price averaging.

    price_locations explicitly maps each output location_id to ONE exact provider
    Location. The caller must separately provide verified matching wind/load scope
    and provenance to wind_oversupply_hours. There is no guessed node-to-zone map.
    historical generation uses the existing generation.normalize_generation helper;
    gridstatus uses Wind MW with declared interval starts/ends. Fuel-mix totals
    alone do not establish the geographic footprint (notably SPP versus SWPW).
    """
    if not isinstance(price_locations, Mapping) or not price_locations or any(
        not isinstance(key, str) or not key.strip() or not isinstance(value, str) or not value.strip()
        for key, value in price_locations.items()
    ):
        raise ValueError("Provide an explicit nonempty output-location to price-location map.")
    if not isinstance(market, str) or not market.strip():
        raise ValueError("Select one exact price market explicitly.")
    required = {"timestamp_utc", "location_id", "load_mw"}
    if not required.issubset(system_load.columns) or system_load.empty:
        raise ValueError("System load requires nonempty canonical hourly observations.")
    if system_load.location_id.nunique(dropna=False) != 1:
        raise ValueError("Provide one system load footprint, not individual-zone load rows.")
    # A caller may use a different verified system label, but cannot mix footprints.
    load = system_load[["timestamp_utc", "load_mw"]].copy()
    parsed = [pd.Timestamp(value) for value in load.timestamp_utc]
    if any(pd.isna(value) or value.tzinfo is None for value in parsed):
        raise ValueError("System load timestamps need explicit timezones.")
    load["timestamp_utc"] = pd.to_datetime(parsed, utc=True)
    if load.timestamp_utc.duplicated().any() or not load.timestamp_utc.eq(load.timestamp_utc.dt.floor("h")).all():
        raise ValueError("System load must have unique hourly interval-start timestamps.")
    if generation_format == "historical":
        wind = normalize_generation(generation)[["timestamp_utc", "wind_mw"]].rename(columns={"wind_mw": "system_wind_mw"})
    elif generation_format == "gridstatus":
        wind = _complete_hourly_power(generation, "Wind").rename(columns={"Wind": "system_wind_mw"})
    else:
        raise ValueError("generation_format must be gridstatus or historical.")
    price_columns = {"Interval Start", "Interval End", "Market", "Location", "LMP"}
    if not price_columns.issubset(prices.columns):
        raise ValueError("Cached LMP input needs interval timestamps, Market, Location and LMP.")
    selected = prices[prices.Market == market]
    if selected.empty:
        raise ValueError("The selected market has no cached prices.")
    base = load.rename(columns={"load_mw": "system_load_mw"}).merge(wind, how="left", on="timestamp_utc", validate="one_to_one")
    frames = []
    for location, node in sorted(price_locations.items()):
        local = selected[selected.Location == node]
        if local.empty:
            raise ValueError(f"No cached prices for the explicitly selected location {node}.")
        lmp = _complete_hourly_power(local, "LMP").rename(columns={"LMP": "lmp_usd_mwh"})
        frame = base.merge(lmp, how="left", on="timestamp_utc", validate="one_to_one")
        frame["location_id"] = location
        frames.append(frame)
    return pd.concat(frames, ignore_index=True).sort_values(["location_id", "timestamp_utc"]).reset_index(drop=True)


def read_cached_wind_inputs(
    system_load_path: Path, *, price_locations: Mapping[str, str], market: str,
) -> pd.DataFrame:
    """Read-only adapter around the existing ingest.load_dataset cache readers.

    Run from the repository root because the existing reader's RAW_DIR is relative.
    Missing caches raise FileNotFoundError; there is no fetch or fake-data fallback.
    Returned attrs identify cached bytes and exact market/node selections. Before
    scoring, the integration caller must supply each input's source and matching
    footprint explicitly; cache existence is not proof of source or spatial scope.
    """
    if Path.cwd().resolve() != ROOT.resolve():
        raise ValueError("Run the existing relative-path cache reader from the repository root.")
    load_path = Path(system_load_path)
    load = read_hourly(load_path)
    generation, prices = ingest.load_dataset("fuel_mix"), ingest.load_dataset("lmp")
    frame = prepare_wind_inputs(generation, load, prices, price_locations=price_locations, market=market)
    frame.attrs = {
        "load_sha256": fingerprint(load_path), "load_location_id": str(load.location_id.iloc[0]),
        "price_market": market, "price_locations": dict(sorted(price_locations.items())),
        "cache_hashes": {str(path): fingerprint(path) for dataset in ("fuel_mix", "lmp")
                         for path in sorted((ingest.RAW_DIR / dataset).glob("*.parquet"))},
        "scope_status": "caller must establish common system wind/load footprint before screening",
    }
    return frame


def read_cached_generation_archive(year: int) -> tuple[pd.DataFrame, dict]:
    """Read the existing historical downloader's cached CSV, with its manifest.

    Offline preparation only. An absent archive stays missing; call the existing
    ingest.fetch_public_evidence downloader separately to populate this cache.
    The returned raw table retains ALL fuel columns for separate carbon accounting.
    Do not use a normalized wind/solar-only table as a complete grid fuel mix.
    """
    if isinstance(year, bool) or not isinstance(year, int) or not 2019 <= year <= 2024:
        raise ValueError("Historical generation reader supports declared 2019..2024 archive years.")
    path = ROOT / f"data/raw/spp/evidence/genmix_{year}.parquet"
    cached = pd.read_parquet(path)
    if len(cached) != 1 or not {"request_url", "content", "source_json"}.issubset(cached.columns):
        raise ValueError("Invalid cached historical-generation document.")
    row = cached.iloc[0]
    url = f"https://portal.spp.org/file-browser-api/download/generation-mix-historical?path=/GenMix_{year}.csv"
    manifest = json.loads(row.source_json)
    if not isinstance(manifest, dict) or row.request_url != url or manifest.get("requested_url") != url or manifest.get("ref") != url:
        raise ValueError("Historical generation cache must match its exact SPP archive URL.")
    origin = source({key: manifest.get(key) for key in ("source_type", "ref")})
    if not isinstance(row.content, (bytes, bytearray)):
        raise ValueError("Cached generation content must contain CSV bytes.")
    digest = hashlib.sha256(row.content).hexdigest()
    if manifest.get("sha256") != digest:
        raise ValueError("Cached generation bytes do not match their source fingerprint.")
    frame = pd.read_csv(io.BytesIO(row.content))
    if frame.empty:
        raise ValueError("Cached generation CSV is empty.")
    return frame, {**origin, "ref": f"{origin['ref']}; cached_sha256={digest}; "
                                  "GMT MKT Interval treated as observation time by existing generation normalizer; "
                                  "left-closed hourly bins require 12 distinct five-minute observations"}


def read_cached_historical_wind_inputs(
    system_load_path: Path, *, year: int, price_locations: Mapping[str, str], market: str,
) -> pd.DataFrame:
    """Use historical full-mix archive plus existing load/LMP caches, without fetch.

    Reuses generation.normalize_generation through prepare_wind_inputs. Its
    observation-time binning convention is explicit in generation_source; it is
    not an assertion about historical dispatch information availability.
    """
    if Path.cwd().resolve() != ROOT.resolve():
        raise ValueError("Run the existing relative-path cache reader from the repository root.")
    raw, generation_source = read_cached_generation_archive(year)
    load = read_hourly(Path(system_load_path))
    if set(load.location_id) != {"SPP_SYSTEM"} or not load.timestamp_utc.dt.year.eq(year).all():
        raise ValueError("Historical join needs matching-year SPP_SYSTEM load, not a zone load or another vintage.")
    frame = prepare_wind_inputs(raw, load, ingest.load_dataset("lmp"), price_locations=price_locations,
                                market=market, generation_format="historical")
    frame.attrs = {"generation_source": generation_source, "load_sha256": fingerprint(Path(system_load_path)),
                   "price_market": market, "price_locations": dict(sorted(price_locations.items())),
                   "price_cache_hashes": {str(path): fingerprint(path) for path in sorted((ingest.RAW_DIR / "lmp").glob("*.parquet"))},
                   "scope_status": "historical SPP_SYSTEM wind/load; local deliverability still unobserved"}
    return frame


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
