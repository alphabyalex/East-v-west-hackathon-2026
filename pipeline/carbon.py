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

import pandas as pd


BOUNDARY = "direct_operational_co2"
FACTOR_UNIT = "kgCO2/MWh"
EGRID_METRIC_URL = "https://www.epa.gov/system/files/documents/2025-06/egrid2023_data_metric_rev2.xlsx"
EGRID_METRIC_SHA256 = "3dfbbcf2f949d58d5b2dbee3aab8150bd04a0c8ebb730ba1cd37a013bd4450ab"
DEFAULT_EGRID_CACHE = Path(__file__).resolve().parents[1] / "data/raw/epa/egrid2023_metric_rev2.parquet"
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


def fuel_mix_intensity(frame: pd.DataFrame, factors: Mapping[str, Mapping], *, expected_fuels: Sequence[str], application_source: Mapping) -> list[dict]:
    """Generation-weighted hourly intensity; any unknown positive fuel yields null.

    application_source records why these factors apply to this geography/year.
    For example, using national annual factors for hourly SPP must be an assumption.
    expected_fuels declares the complete fuel universe for this one grid area.
    Missing fuel rows are unknown, even if the supplied rows contain only wind.
    Missing factors are never silently zeroed or dropped from the denominator.
    generation_mwh is energy for one complete hour, not an instantaneous MW sample.
    An empty frame returns no hourly observations, never a fabricated zero hour.
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
        fuels[fuel] = {"value": _number(row["generation_mwh"], "Generation MWh"), **_source(row)}
    result = []
    for time, fuels in sorted(groups.items()):
        fuels = dict(sorted(fuels.items()))
        total = math.fsum(datum["value"] for datum in fuels.values())
        missing_generation = sorted(expected - set(fuels))
        missing = sorted(fuel for fuel, datum in fuels.items() if datum["value"] > 0 and fuel not in checked)
        known = math.fsum(datum["value"] for fuel, datum in fuels.items() if fuel in checked)
        coverage_source = {**policy, "ref": f"{policy['ref']}; expected_fuels={sorted(expected)}; missing_generation_fuels={missing_generation}; missing_factor_fuels={missing}"}
        sources = list(fuels.values()) + [checked[fuel] for fuel in sorted(fuels) if fuel in checked] + [coverage_source]
        intensity = None if missing_generation or missing or total == 0 else math.fsum(
            datum["value"] / total * checked[fuel]["value"]
            for fuel, datum in fuels.items() if datum["value"] > 0
        )
        result.append({
            "timestamp_utc": time,
            "boundary": BOUNDARY,
            "status": "missing_generation" if missing_generation else "missing_factors" if missing else "no_generation" if total == 0 else "available",
            "intensity_kg_co2_per_mwh": _derived(intensity, sources, "generation-weighted average; not marginal dispatch intensity", modeled=True),
            "generation_mwh": _derived(None if missing_generation else total, list(fuels.values()), "complete fuel universe generation MWh; null if a required fuel row is absent"),
            "reported_generation_mwh": _derived(total, list(fuels.values()), "sum of reported fuel generation MWh; may be incomplete"),
            "known_generation_mwh": _derived(known, sources, "generation MWh with supplied fuel factors"),
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
