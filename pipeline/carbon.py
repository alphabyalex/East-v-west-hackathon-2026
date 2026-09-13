"""Offline operational-CO2 accounting for explicit energy-shift scenarios.

The accounting functions perform no I/O, dispatch optimization or annualization.
The separate EPA loader only reads an already cached workbook during offline prep.
Fuel-mix input is long-form hourly generation: timestamp_utc, fuel,
generation_mwh, source_type, ref. Each fuel must appear once per UTC hour;
the caller must supply the complete generation mix, not just wind and solar.
Factors are keyed by the exact fuel label and contain value, source_type, ref,
unit='kgCO2/MWh', boundary='direct_operational_co2'. Annual/regional factor
selection is an explicit sourced assumption supplied by the caller.

Hourly results are JSON-compatible records with sourced numeric values. Shift
inputs pair risk_hour and makeup_hour with one sourced mwh value, representing
the same energy removed and subsequently made up. Carbon shifted is signed:
MWh * (risk-hour intensity - makeup-hour intensity), not a causal dispatch claim.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
from io import BytesIO
import json
import math
from numbers import Real
from pathlib import Path

import numpy as np
import pandas as pd


BOUNDARY = "direct_operational_co2"
FACTOR_UNIT = "kgCO2/MWh"
EGRID_METRIC_URL = "https://www.epa.gov/system/files/documents/2025-06/egrid2023_data_metric_rev2.xlsx"
EGRID_METRIC_SHA256 = "3dfbbcf2f949d58d5b2dbee3aab8150bd04a0c8ebb730ba1cd37a013bd4450ab"
DEFAULT_EGRID_CACHE = Path(__file__).resolve().parents[1] / "data/raw/epa/egrid2023_metric_rev2.parquet"
SPP_ARCHIVE_FUELS = ("Coal", "Diesel Fuel Oil", "Hydro", "Natural Gas", "Nuclear", "Solar",
                     "Waste Disposal Services", "Wind", "Waste Heat", "Other")
UNKNOWN_GENERATION_STATUSES = {"missing_component", "incomplete_observations", "negative_net_generation"}
WIND_OPERATIONAL_CO2_FACTOR = {
    "value": 0.0,
    "source_type": "data",
    "ref": (
        "https://www.epa.gov/system/files/documents/2025-01/egrid2023_technical_guide.pdf#page=21; "
        "eGRID2023 Technical Guide, printed page 10; wind generation direct operational CO2 is zero; "
        "excludes lifecycle emissions and any displaced generation"
    ),
    "unit": FACTOR_UNIT,
    "boundary": BOUNDARY,
}


def _source(source: Mapping) -> dict:
    if not isinstance(source, Mapping) or source.get("source_type") not in {"data", "model", "assumption"}:
        raise ValueError("Every input requires data, model, or assumption provenance")
    ref = source.get("ref")
    if not isinstance(ref, str) or not ref.strip():
        raise ValueError("Every input requires a nonempty provenance ref")
    if source["source_type"] != "assumption" and any(
        word in ref.lower() for word in ("placeholder", "mock://", "illustrative")
    ):
        raise ValueError("Placeholder references must remain assumptions")
    return {"source_type": source["source_type"], "ref": ref}


def _number(value, name: str, *, nullable=False, signed=False) -> float | None:
    if nullable and value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if not signed and value < 0:
        raise ValueError(f"{name} must be nonnegative")
    return float(value)


def _datum(datum: Mapping, name: str, *, nullable=False) -> dict:
    source = _source(datum)
    if "value" not in datum:
        raise ValueError(f"{name} requires a value")
    return {"value": _number(datum["value"], name, nullable=nullable), **source}


def _factor(factor: Mapping, name: str) -> dict:
    result = _datum(factor, name)
    if factor.get("unit") != FACTOR_UNIT or factor.get("boundary") != BOUNDARY:
        raise ValueError(f"{name} requires {FACTOR_UNIT} and {BOUNDARY}; no lifecycle or unit conversion is implicit")
    return {**result, "unit": FACTOR_UNIT, "boundary": BOUNDARY}


def load_egrid_swpp_factors(path=DEFAULT_EGRID_CACHE) -> dict[str, dict]:
    """Read the verified eGRID2023 metric revision-2 cache, only during offline prep.

    Never call this workbook parser from an API request. The one-row parquet has
    content: bytes and source_json with data/ref/retrieved_utc/sha256 metadata.
    Both the declared digest and the independently verified release digest must
    match. A new release requires an explicit review, never a silent fallback.

    Coal/Natural Gas/Oil factors are SWPP annual generation-weighted operational
    CO2 rates, not hourly plant or marginal dispatch factors. Applying them to
    2024 generation or individual plants still requires the caller's explicit
    assumption in fuel_mix_intensity(application_source=...). Oil is not silently
    mapped to Diesel Fuel Oil; waste and Other receive no invented factors.
    Wind/Solar/Hydro/Nuclear operational zero comes from the EPA technical guide,
    not absent workbook cells. It excludes lifecycle emissions.
    """
    # Resolve as a local path before giving pandas any input: URLs never trigger I/O.
    cache_path = Path(path)
    if not cache_path.is_file():
        raise FileNotFoundError(f"Missing offline EPA cache: {cache_path}")
    cache = pd.read_parquet(cache_path)
    if len(cache) != 1 or not cache.columns.is_unique or set(cache.columns) != {"content", "source_json"}:
        raise ValueError("EPA cache must contain one content/source_json record")
    content, raw_source = cache.iloc[0]["content"], cache.iloc[0]["source_json"]
    if not isinstance(content, bytes) or not content or not isinstance(raw_source, str):
        raise ValueError("EPA cache requires workbook bytes and JSON source metadata")

    def unique_object(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate EPA metadata key: {key}")
            result[key] = value
        return result

    origin = json.loads(raw_source, object_pairs_hook=unique_object)
    if not isinstance(origin, Mapping) or set(origin) != {"source_type", "ref", "retrieved_utc", "sha256"}:
        raise ValueError("EPA metadata requires source_type/ref/retrieved_utc/sha256")
    _source(origin)
    if origin["source_type"] != "data" or origin["ref"] != EGRID_METRIC_URL:
        raise ValueError("EPA cache must identify the official eGRID2023 metric revision-2 source")
    digest = hashlib.sha256(content).hexdigest()
    if digest != origin["sha256"] or digest != EGRID_METRIC_SHA256:
        raise ValueError("EPA workbook SHA256 does not match its metadata and verified release")
    retrieved = origin["retrieved_utc"]
    if not isinstance(retrieved, str) or not retrieved.strip():
        raise ValueError("EPA metadata requires a UTC retrieval timestamp")
    try:
        timestamp = pd.Timestamp(retrieved)
    except (TypeError, ValueError) as error:
        raise ValueError("EPA metadata requires a UTC retrieval timestamp") from error
    if pd.isna(timestamp) or timestamp.tzinfo is None or timestamp.utcoffset().total_seconds() != 0:
        raise ValueError("EPA metadata requires a UTC retrieval timestamp")

    # Keep the display-label row and raw header row so duplicate field codes and
    # wrong units cannot be hidden by pandas' automatic duplicate-name mangling.
    sheet = pd.read_excel(BytesIO(content), sheet_name="BA23", header=None, dtype=object, engine="openpyxl")
    codes = {"Coal": ("BACCO2RT", "coal"), "Natural Gas": ("BAGCO2RT", "gas"), "Oil": ("BAOCO2RT", "oil")}
    if len(sheet) < 3:
        raise ValueError("EPA BA23 sheet needs display labels, field headers, and data")
    fields = {}
    for code in ("YEAR", "BACODE", *(code for code, _fuel in codes.values())):
        columns = [index for index, name in enumerate(sheet.iloc[1]) if name == code]
        if len(columns) != 1:
            raise ValueError(f"EPA BA23 requires one unambiguous {code} field")
        fields[code] = columns[0]
    for code, fuel in codes.values():
        label = sheet.iloc[0, fields[code]]
        expected = f"BA annual CO2 {fuel} output emission rate (kg/MWh)"
        if not isinstance(label, str) or " ".join(label.split()) != expected:
            raise ValueError(f"EPA {code} must be a CO2 output rate in kg/MWh, not kg/GJ or CO2e")
    rows = sheet.iloc[2:]
    selected = rows[(rows.iloc[:, fields["BACODE"]] == "SWPP") & (rows.iloc[:, fields["YEAR"]] == 2023)]
    if len(selected) != 1:
        raise ValueError("EPA BA23 requires exactly one SWPP/YEAR=2023 row")
    row_index = selected.index[0]
    from openpyxl.utils import get_column_letter
    result = {}
    for fuel, (code, _label) in codes.items():
        column = fields[code]
        value = _number(selected.iloc[0, column], f"EPA SWPP {code}")
        ref = json.dumps({**origin, "sheet": "BA23", "YEAR": 2023, "BACODE": "SWPP",
                          "field": code, "cell": f"{get_column_letter(column + 1)}{row_index + 1}",
                          "unit": FACTOR_UNIT, "boundary": BOUNDARY}, sort_keys=True)
        result[fuel] = {"value": value, "source_type": "data", "ref": ref,
                        "unit": FACTOR_UNIT, "boundary": BOUNDARY}
    for fuel in ("Wind", "Solar", "Hydro", "Nuclear"):
        result[fuel] = {**WIND_OPERATIONAL_CO2_FACTOR,
                        "ref": WIND_OPERATIONAL_CO2_FACTOR["ref"].replace("wind generation", f"{fuel.lower()} generation")}
    return result


def _hour(value) -> str:
    try:
        time = pd.Timestamp(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Timestamps must identify timezone-aware UTC hours") from error
    if pd.isna(time) or time.tzinfo is None:
        raise ValueError("Timestamps must identify timezone-aware UTC hours")
    time = time.tz_convert("UTC")
    if time != time.floor("h"):
        raise ValueError("Timestamps must identify complete UTC hours")
    return time.isoformat()


def _derived(value, sources: Sequence[Mapping], method: str, *, modeled=False) -> dict:
    value = _number(value, "Calculated value", nullable=True, signed=True)
    types = {_source(source)["source_type"] for source in sources}
    kind = "assumption" if "assumption" in types else "model" if modeled or "model" in types else "data"
    # Include factor values and both time-pair sources, not just a selected ref.
    evidence = sorted({json.dumps(dict(source), sort_keys=True, allow_nan=False) for source in sources})
    return {"value": value, "source_type": kind,
            "ref": json.dumps({"method": method, "inputs": [json.loads(item) for item in evidence]}, sort_keys=True)}


def normalize_spp_generation_archive(raw: pd.DataFrame, *, generation_source: Mapping, timing_source: Mapping) -> pd.DataFrame:
    """Normalize the full historical SPP Market/Self archive without I/O.

    Input comes from wind_signal.read_cached_generation_archive, not the existing
    wind/solar-only normalizer. GMT MKT Interval is treated as observation time,
    assigned to left-closed UTC hours without shifting timestamps. This convention
    is NOT independently verified as interval start/end; timing_source must be an
    explicit assumption. Each hourly fuel requires all 12 distinct five-minute
    observations and both Market/Self components. Mean combined MW times one hour
    gives MWh under that convention; no partial-hour extrapolation occurs.

    The ten published fuels are always retained, as are any additional named
    Market/Self fuels. Only Gas Self aliases Natural Gas Self. Load is demand and
    is excluded, never used as the generation denominator. Missing components or
    samples produce unknown generation. Signed components may offset within a
    fuel/sample, but any negative combined sample makes that fuel-hour unknown:
    negative net generation needs a reviewed accounting rule, never a zero clamp.

    Output columns: timestamp_utc (ISO UTC hour), fuel, generation_mwh (float or
    None), unit='MWh', generation_status, source_type, ref. Refs retain source and
    timing evidence, component names, sample counts, and negative-value diagnostics.
    All output sources remain assumptions because timing is assumed. Feed this
    table to fuel_mix_intensity using attrs['expected_fuels']; no geographic or
    annual coverage is inferred. Missing hours between first/last observations
    remain explicit unknown rows; time outside the supplied range is not invented.
    """
    origin, timing = _source(generation_source), _source(timing_source)
    if timing["source_type"] != "assumption":
        raise ValueError("Historical observation-time binning must remain an explicit timing assumption")
    if not isinstance(raw, pd.DataFrame) or raw.empty or not all(isinstance(name, str) for name in raw.columns):
        raise ValueError("A nonempty historical generation archive with named columns is required")
    frame = raw.copy(deep=True)
    frame.columns = frame.columns.str.strip()
    if not frame.columns.is_unique or "GMT MKT Interval" not in frame:
        raise ValueError("Historical generation needs unique columns and GMT MKT Interval")
    if "Gas Self" in frame:
        if "Natural Gas Self" in frame:
            raise ValueError("Ambiguous Gas Self and Natural Gas Self components")
        frame = frame.rename(columns={"Gas Self": "Natural Gas Self"})
    components = [name for name in frame.columns if name not in {"GMT MKT Interval", "Load"}]
    if not components or any(not name.endswith((" Market", " Self")) or not name.rsplit(" ", 1)[0] for name in components):
        raise ValueError("Unknown archive column; every generation field must identify a Market/Self fuel")
    fuels = sorted(set(SPP_ARCHIVE_FUELS) | {name.rsplit(" ", 1)[0] for name in components})
    try:
        times = [pd.Timestamp(value) for value in frame["GMT MKT Interval"]]
    except (TypeError, ValueError) as error:
        raise ValueError("Historical observations require explicit timezone-aware timestamps") from error
    if any(pd.isna(value) or value.tzinfo is None for value in times):
        raise ValueError("Historical observations require explicit timezone-aware timestamps")
    frame["GMT MKT Interval"] = pd.to_datetime(times, utc=True)
    if not frame["GMT MKT Interval"].eq(frame["GMT MKT Interval"].dt.floor("5min")).all():
        raise ValueError("Historical observations must align to the five-minute cadence")
    for name in components:
        if frame[name].map(lambda value: isinstance(value, (bool, np.bool_))).any():
            raise ValueError("Generation component MW cannot be boolean")
        frame[name] = pd.to_numeric(frame[name], errors="raise").astype(float)
        if np.isinf(frame[name]).any():
            raise ValueError("Generation component MW cannot be infinite")
    frame = frame.drop_duplicates()
    if frame["GMT MKT Interval"].duplicated().any():
        raise ValueError("Conflicting duplicate generation observations must be reconciled first")
    frame = frame.set_index("GMT MKT Interval").sort_index()
    hours = pd.date_range(frame.index.min().floor("h"), frame.index.max().floor("h"), freq="h")
    hour_strings = [hour.isoformat() for hour in hours]
    rows = []
    for fuel in fuels:
        names = [fuel + " Market", fuel + " Self"]
        missing = [name for name in names if name not in frame]
        power = frame.reindex(columns=names)
        combined = power[names[0]] + power[names[1]]
        if np.isinf(combined).any():
            raise ValueError("Combined fuel generation overflow; inspect MW units and scale")
        counts = power.resample("h").count().reindex(hours, fill_value=0)
        combined_count = combined.resample("h").count().reindex(hours, fill_value=0)
        negatives = power.lt(0).resample("h").sum().reindex(hours, fill_value=0)
        negative_net = combined.lt(0).resample("h").sum().reindex(hours, fill_value=0)
        complete = combined_count.eq(12) & negative_net.eq(0)
        energy = combined.resample("h").mean().reindex(hours).where(complete)
        if np.isinf(energy).any():
            raise ValueError("Hourly generation overflow; inspect MW units and scale")
        base = json.loads(_derived(None, [origin, timing],
            "mean(Market MW + Self MW) * 1 hour; 12 distinct samples; observation-time left-closed UTC bins; no interval-start/end claim")["ref"])
        base.update({"fuel": fuel, "input_unit": "MW", "output_unit": "MWh",
                     "components": ["Gas Self" if name == "Natural Gas Self" and "Gas Self" in raw.columns.str.strip() else name for name in names],
                     "missing_components": missing, "signed_components": "preserved within each fuel/sample; negative combined generation is unknown"})
        values = zip(hour_strings, energy.to_numpy(), complete.to_numpy(), combined_count.to_numpy(),
                     negative_net.to_numpy(), counts.to_numpy(), negatives.to_numpy())
        for stamp, mwh, is_complete, samples, net_negative, component_counts, component_negative in values:
            status = "missing_component" if missing else "negative_net_generation" if net_negative else "complete" if is_complete else "incomplete_observations"
            ref = {**base, "hour_utc": stamp, "generation_status": status,
                   "complete_samples": int(samples),
                   "component_samples": {name: int(count) for name, count in zip(names, component_counts)},
                   "negative_component_samples": {name: int(count) for name, count in zip(names, component_negative)},
                   "negative_net_samples": int(net_negative)}
            rows.append({"timestamp_utc": stamp, "fuel": fuel,
                         "generation_mwh": float(mwh) if is_complete else None,
                         "unit": "MWh", "generation_status": status, "source_type": "assumption",
                         "ref": json.dumps(ref, sort_keys=True, allow_nan=False)})
    output = pd.DataFrame(rows, dtype=object).sort_values(["timestamp_utc", "fuel"]).reset_index(drop=True)
    output.attrs = {"expected_fuels": fuels, "generation_unit": "MWh",
                    "timing_convention": "observation_time_left_closed_utc_hour",
                    "scope": "historical SPP archive; no site-specific or complete-year coverage inferred"}
    return output


def fuel_mix_intensity(frame: pd.DataFrame, factors: Mapping[str, Mapping], *, expected_fuels: Sequence[str], application_source: Mapping) -> list[dict]:
    """Generation-weighted hourly intensity; any unknown positive fuel yields null.

    application_source records why these factors apply to this geography/year.
    For example, using national annual factors for hourly SPP must be an assumption.
    expected_fuels declares the complete fuel universe for this one grid area.
    Missing fuel rows are unknown, even if the supplied rows contain only wind.
    Missing factors are never silently zeroed or dropped from the denominator.
    generation_mwh is energy for one complete hour, not an instantaneous MW sample.
    An empty frame returns no hourly observations, never a fabricated zero hour.
    Explicit unknown statuses from normalize_spp_generation_archive retain missing
    generation (None or a parquet-null NaN); ordinary unmarked NaN is still invalid.
    """
    required = {"timestamp_utc", "fuel", "generation_mwh", "source_type", "ref"}
    if not isinstance(frame, pd.DataFrame) or not frame.columns.is_unique or not required.issubset(frame.columns):
        raise ValueError(f"Fuel mix requires columns {sorted(required)}")
    policy = _source(application_source)
    if not isinstance(expected_fuels, Sequence) or isinstance(expected_fuels, (str, bytes)) or not expected_fuels or any(
        not isinstance(fuel, str) or not fuel.strip() for fuel in expected_fuels
    ) or len(set(expected_fuels)) != len(expected_fuels):
        raise ValueError("Declare a nonempty, unique expected_fuels list for the complete grid mix")
    expected = set(expected_fuels)
    if not isinstance(factors, Mapping):
        raise ValueError("Factors must be keyed by exact fuel label")
    checked = {}
    for fuel, factor in factors.items():
        if not isinstance(fuel, str) or not fuel.strip():
            raise ValueError("Factor fuel labels must be nonempty strings")
        checked[fuel] = _factor(factor, f"Factor for {fuel}")
    groups: dict[str, dict[str, dict]] = {}
    for row in frame.to_dict("records"):
        time = _hour(row["timestamp_utc"])
        fuel = row["fuel"]
        if not isinstance(fuel, str) or not fuel.strip():
            raise ValueError("Generation fuel labels must be nonempty strings")
        if fuel not in expected:
            raise ValueError(f"Fuel {fuel} is outside the declared complete fuel universe")
        fuels = groups.setdefault(time, {})
        if fuel in fuels:
            raise ValueError(f"Duplicate fuel {fuel} at {time}")
        if "unit" in row and row["unit"] != "MWh":
            raise ValueError("Fuel generation must be energy in MWh, not raw MW")
        value = row["generation_mwh"]
        status = row.get("generation_status")
        if status in UNKNOWN_GENERATION_STATUSES:
            if value is not None and value is not pd.NA and not (isinstance(value, Real) and math.isnan(value)):
                raise ValueError("An explicit unknown generation status requires a missing value")
            value = None
        elif status not in {None, "complete"}:
            raise ValueError("Unknown generation_status")
        else:
            value = _number(value, "Generation MWh")
        fuels[fuel] = {"value": value, **_source(row)}
    result = []
    for time, fuels in sorted(groups.items()):
        fuels = dict(sorted(fuels.items()))
        observed = {fuel: datum for fuel, datum in fuels.items() if datum["value"] is not None}
        total = math.fsum(datum["value"] for datum in observed.values())
        missing_generation = sorted(expected - set(observed))
        missing = sorted(fuel for fuel, datum in observed.items() if datum["value"] > 0 and fuel not in checked)
        known = math.fsum(datum["value"] for fuel, datum in observed.items() if fuel in checked)
        coverage_source = {**policy, "ref": f"{policy['ref']}; expected_fuels={sorted(expected)}; missing_generation_fuels={missing_generation}; missing_factor_fuels={missing}"}
        sources = list(fuels.values()) + [checked[fuel] for fuel in sorted(fuels) if fuel in checked] + [coverage_source]
        intensity = None if missing_generation or missing or total == 0 else math.fsum(
            datum["value"] / total * checked[fuel]["value"]
            for fuel, datum in observed.items() if datum["value"] > 0
        )
        result.append({
            "timestamp_utc": time,
            "boundary": BOUNDARY,
            "status": "missing_generation" if missing_generation else "missing_factors" if missing else "no_generation" if total == 0 else "available",
            "intensity_kg_co2_per_mwh": _derived(intensity, sources, "generation-weighted average; not marginal dispatch intensity", modeled=True),
            "generation_mwh": _derived(None if missing_generation else total, list(fuels.values()), "complete fuel universe generation MWh; null if a required fuel row is absent"),
            "reported_generation_mwh": _derived(total if observed else None, list(fuels.values()), "sum of reported complete fuel generation MWh; may be incomplete; null if none are known"),
            "known_generation_mwh": _derived(known if observed else None, sources, "generation MWh with supplied fuel factors; null if no generation is known"),
            "factor_coverage_fraction": _derived(known / total if total and not missing_generation else None, sources, "known generation MWh / all generation MWh"),
            "missing_fuels": missing,
            "missing_generation_fuels": missing_generation,
        })
    return result


def shift_carbon(intensities: Sequence[Mapping], moves: Sequence[Mapping], *, selection_source: Mapping, hourly_limits: Mapping) -> dict:
    """Account for explicit conserved-MWh pairs, with no inferred makeup schedule.

    moves: [{risk_hour, makeup_hour, mwh: {value, source_type, ref}}]. Makeup
    must occur after the risk hour. Missing intensities propagate null to the
    total instead of quietly summing only the computable pairs.
    hourly_limits maps hours to sourced removable_mwh and/or makeup_capacity_mwh.
    All moves sharing an hour must fit its submitted limit; limits themselves
    need provenance and do not establish site interruption or dispatch response.
    Empty observations or schedules are rejected, not interpreted as measured
    zero. An explicit zero-MWh pair with observed intensities is a zero scenario.
    Do not invent zero pairs for a no-risk period; establishing a complete-period
    no-shift result requires a separate reviewed coverage/selection contract.
    Shared limits permit at most one binary64 ULP of summation roundoff above a
    positive limit (for example 0.1 + 0.2 at a 0.3 MWh limit), never above zero.
    Pair ordering and numeric aggregation are canonical and permutation-stable.
    """
    policy = _source(selection_source)
    if not isinstance(hourly_limits, Mapping):
        raise ValueError("hourly_limits must map timestamps to sourced energy limits")
    limits = {}
    for time, values in hourly_limits.items():
        time = _hour(time)
        if time in limits:
            raise ValueError("Duplicate hourly energy limits")
        if not isinstance(values, Mapping):
            raise ValueError("Each hourly limit must map energy names to sourced values")
        limits[time] = {name: _datum(values[name], name) for name in ("removable_mwh", "makeup_capacity_mwh") if name in values}
    hours = {}
    for row in intensities:
        if not isinstance(row, Mapping) or not {"timestamp_utc", "intensity_kg_co2_per_mwh"}.issubset(row):
            raise ValueError("Each hourly intensity requires timestamp_utc and intensity_kg_co2_per_mwh")
        time = _hour(row["timestamp_utc"])
        if time in hours:
            raise ValueError("Duplicate hourly intensity")
        if row.get("boundary") != BOUNDARY:
            raise ValueError("Shift intensities must share the direct operational CO2 boundary")
        hours[time] = _datum(row["intensity_kg_co2_per_mwh"], "Hourly intensity", nullable=True)
    if not hours:
        raise ValueError("No intensity observations supplied; empty input cannot establish zero carbon shifted")
    pairs, seen, energy_sources, carbon_sources = [], set(), [policy], [policy]
    removed, made_up = {}, {}
    for move in moves:
        if not isinstance(move, Mapping) or not {"risk_hour", "makeup_hour", "mwh"}.issubset(move):
            raise ValueError("Each move requires risk_hour, makeup_hour, and sourced mwh")
        risk, makeup = _hour(move["risk_hour"]), _hour(move["makeup_hour"])
        if makeup <= risk:
            raise ValueError("Makeup hour must be later than risk hour")
        if (risk, makeup) in seen:
            raise ValueError("Duplicate shift pair; do not count the same scheduled move twice")
        seen.add((risk, makeup))
        if risk not in hours or makeup not in hours:
            raise ValueError("Both explicitly paired hours require an intensity record")
        energy = _datum(move["mwh"], "Moved MWh")
        if "removable_mwh" not in limits.get(risk, {}) or "makeup_capacity_mwh" not in limits.get(makeup, {}):
            raise ValueError("Each paired hour requires its explicitly sourced energy limit")
        removed.setdefault(risk, []).append(energy["value"])
        made_up.setdefault(makeup, []).append(energy["value"])
        before, after = hours[risk], hours[makeup]
        provenance = [energy, before, after, policy, limits[risk]["removable_mwh"], limits[makeup]["makeup_capacity_mwh"],
                      {"source_type": "assumption", "ref": f"submitted conserved-energy pair: risk_hour={risk}; makeup_hour={makeup}; no loss or extra makeup energy modeled"}]
        shift = None if before["value"] is None or after["value"] is None else energy["value"] * (before["value"] - after["value"])
        selected_energy = _derived(energy["value"], provenance, "same submitted MWh removed and made up within the sourced hourly limits")
        pair = {"risk_hour": risk, "makeup_hour": makeup,
                "mwh_removed": dict(selected_energy), "mwh_made_up": dict(selected_energy),
                "carbon_shifted_kg_co2": _derived(shift, provenance, "MWh * (risk-hour intensity - makeup-hour intensity); positive means lower makeup intensity")}
        pairs.append(pair)
        energy_sources.append(selected_energy)
        carbon_sources.append(pair["carbon_shifted_kg_co2"])
    if not pairs:
        raise ValueError("No explicit energy moves supplied; an empty schedule cannot establish a zero scenario")
    for schedule, limit_name in ((removed, "removable_mwh"), (made_up, "makeup_capacity_mwh")):
        for time, quantities in sorted(schedule.items()):
            limit = limits[time][limit_name]["value"]
            rounded_limit = math.nextafter(limit, math.inf) if limit > 0 else limit
            if math.fsum(sorted(quantities)) > rounded_limit:
                raise ValueError("Combined scheduled MWh exceed a risk-hour or makeup-hour energy limit")
    pairs.sort(key=lambda pair: (pair["risk_hour"], pair["makeup_hour"]))
    quantities = [pair["carbon_shifted_kg_co2"]["value"] for pair in pairs]
    total = None if None in quantities else math.fsum(quantities)
    energy_total = math.fsum(pair["mwh_removed"]["value"] for pair in pairs)
    return {"boundary": BOUNDARY, "pairs": pairs,
            "mwh_removed": _derived(energy_total, energy_sources, "sum of explicitly scheduled removed MWh"),
            "mwh_made_up": _derived(energy_total, energy_sources, "same conserved MWh made up at explicitly paired hours"),
            "carbon_shifted_kg_co2": _derived(total, carbon_sources, "signed sum across all explicit shift pairs; not a causal emissions reduction estimate"),
            "interpretation": "Positive means lower attributed CO2 after shifting; negative means higher attributed CO2. Equal energy is moved, not eliminated."}


def wind_carbon(absorbed_mwh: Mapping, *, selection_source: Mapping, factor: Mapping | None = None) -> dict:
    """Direct operational CO2 associated with wind energy, never physical removal.

    selection_source must expose how the absorption quantity was established.
    A negative-price/high-wind proxy cannot establish otherwise-curtailed MWh.
    The EPA zero factor excludes lifecycle emissions and displaced fossil output.
    """
    energy = _datum(absorbed_mwh, "Absorbed wind MWh")
    policy = _source(selection_source)
    wind_factor = _factor(WIND_OPERATIONAL_CO2_FACTOR if factor is None else factor, "Wind factor")
    if wind_factor["value"] != 0:
        raise ValueError("Wind direct operational CO2 is zero; a nonzero lifecycle factor needs a separate accounting boundary")
    return {"boundary": BOUNDARY,
            "wind_energy_absorbed_mwh": _derived(energy["value"], [energy, policy], "submitted wind absorption quantity; counterfactual curtailment must be established separately"),
            "wind_operational_co2_kg": _derived(energy["value"] * wind_factor["value"], [energy, wind_factor, policy], "wind MWh * direct operational wind CO2 factor"),
            "interpretation": "Wind energy is absorbed as electricity. This CO2 value represents associated direct operational emissions, not atmospheric carbon removal or displaced-generation benefit."}
