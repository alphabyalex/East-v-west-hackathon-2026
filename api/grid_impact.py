"""Standalone composition of precomputed grid-impact scenarios; no API wiring.

``compose_grid_impact(location_id, wind_summary=..., carbon_shift=...,
shift_coverage=...)`` accepts one ``summarize_wind`` record and one
``shift_carbon`` result. All six scalar outputs use {value, source_type, ref}:
wind_absorption_mwh_in_observed_hours, wind_absorption_mwh_per_year,
carbon_absorbed_tonnes_in_observed_hours, carbon_absorbed_tonnes_per_year,
carbon_shifted_tonnes_in_observed_hours, carbon_shifted_tonnes_per_year.

The carbon_absorbed compatibility name means associated wind direct operational
CO2, which is zero for known wind energy. It is not atmospheric carbon removal,
lifecycle accounting, or a displaced-generation benefit. Carbon shifted is a
signed attribution difference across explicitly conserved energy pairs.

Additional output fields are location_id, schema_version (a string), boundary,
units (field-name to unit string), basis, coverage (wind and shift records),
evidence_context, and evidence. Every output scalar ref is a concise
grid-impact://evidence/<sha256> pointer into the local evidence dictionary.
Evidence nodes preserve methods and sourced inputs, including their values,
units, accounting boundaries and original opaque refs/URLs. Known nested
{method,inputs} JSON provenance is deduplicated as a content-addressed DAG.
evidence_context.wind points to the screen method, system scope, proxy hours and
coverage. Clients must resolve local pointers; no external request is required.
Available coverage has UTC period_start_utc/period_end_exclusive_utc strings,
sourced observed_hours/evaluable_hours/unknown_hours/missing_interval_hours,
status='complete_calendar_year'|'partial_period', and schedule_scope. Unavailable
coverage contains status='unavailable' and a reason. No null scalar is bare.

shift_coverage must explicitly supply those four sourced counts, both timestamps,
and schedule_scope='complete_period'|'selected_pairs_only'. It certifies the
coverage of risk selection and the submitted makeup schedule, not just the hours
appearing in carbon_shift.pairs. A full UTC calendar year and complete/evaluable
coverage are necessary for annual output. No partial-period extrapolation occurs.
An absent schedule uses carbon_shift=None; an empty pair list is not a measured
zero. A reviewed no-shift declaration for a complete no-risk year needs a future
explicit contract and must not be represented by an invented zero-energy pair.
The adapter checks period coverage, energy conservation and aggregate agreement.
pipeline.carbon owns the intensity formula; opaque source refs are not executed
or reverse-engineered to recalculate it. Aggregate comparisons allow at most one
binary64 step of roundoff between nonzero values; zero is always exact.

Optional offline cache format (inputs already computed, no training or fetching):
{"schema_version":"grid-impact-inputs-v1", "locations":[
  {"location_id":"...", "wind_summary": <record or null>,
   "carbon_shift": <result or null>, "shift_coverage": <coverage or null>}
]}. get_location_grid_impact reads this cache and validates it. Missing file or
location returns sourced unavailable values. The entire envelope and unique
location identifiers are checked, then only the selected location is composed.
Malformed selected data raises; other locations' payloads are validated when
selected. Offline producers should validate all records before publishing them.
This module does not register a route or modify the /api/estimate contract.
"""
from __future__ import annotations

from collections.abc import Mapping
import json
import hashlib
import math
from pathlib import Path

import pandas as pd

from pipeline.carbon import BOUNDARY, wind_carbon
from pipeline.wind_signal import finite_number, source


DEFAULT_PATH = Path(__file__).resolve().parents[1] / "data/processed/grid_impact_by_location.json"
COUNT_KEYS = ("observed_hours", "evaluable_hours", "unknown_hours", "missing_interval_hours")
UNITS = {
    "wind_absorption_mwh_in_observed_hours": "MWh",
    "wind_absorption_mwh_per_year": "MWh/year",
    "carbon_absorbed_tonnes_in_observed_hours": "tonnes CO2",
    "carbon_absorbed_tonnes_per_year": "tonnes CO2/year",
    "carbon_shifted_tonnes_in_observed_hours": "tonnes CO2",
    "carbon_shifted_tonnes_per_year": "tonnes CO2/year",
}
BASIS = {
    "wind": "Flexible-load energy scenario in evaluable high-wind/low-price hours; actual wind curtailment and local deliverability are unobserved.",
    "carbon_absorbed": "Compatibility metric: associated wind direct operational CO2. No atmospheric carbon removal, lifecycle emissions, or displaced-generation benefit is represented.",
    "carbon_shifted": "Signed MWh times risk-hour minus makeup-hour average CO2 intensity. Positive means lower attributed makeup emissions; negative means higher. This is a conserved-energy scenario, not a causal dispatch estimate.",
    "annual": "Annual values cover one complete evaluable UTC calendar year; partial observed periods are never extrapolated.",
}
SCENARIO_SOURCE = {"source_type": "assumption", "ref": "submitted grid-impact energy schedule; counterfactual site dispatch and local deliverability are not established"}
EVIDENCE_PREFIX = "grid-impact://evidence/"


def _location(value):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError("location_id must be a nonempty exact identifier without surrounding whitespace")
    return value


def _source_info(value):
    if not isinstance(value, Mapping) or not isinstance(value.get("source_type"), str):
        raise ValueError("Every source requires a supported string source_type")
    return source({key: value.get(key) for key in ("source_type", "ref")})


def _datum(value, name, *, nullable=False, signed=False, integer=False):
    if not isinstance(value, Mapping) or set(value) != {"value", "source_type", "ref"}:
        raise ValueError(f"{name} requires exactly value, source_type and ref")
    origin = _source_info(value)
    number = value["value"]
    if number is None and nullable:
        return {"value": None, **origin}
    number = finite_number(number, name, minimum=None if signed else 0)
    if integer and not number.is_integer():
        raise ValueError(f"{name} must be a whole hour count")
    return {"value": int(number) if integer else number, **origin}


class _Evidence:
    """One composition's complete provenance graph, without recursive escaping."""

    def __init__(self):
        self.nodes = {}
        self.refs = {}

    def register(self, node):
        encoded = json.dumps(node, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        if digest not in self.nodes:
            self.nodes[digest] = json.loads(encoded)
        return EVIDENCE_PREFIX + digest

    def compact_source(self, item):
        if not isinstance(item, Mapping) or not {"source_type", "ref"}.issubset(item):
            raise ValueError("Evidence inputs require source_type and ref")
        _source_info(item)
        # Retain all original input metadata, especially value/unit/boundary.
        result = dict(item)
        result["ref"] = self.compact_ref(item["ref"])
        if result["ref"].startswith(EVIDENCE_PREFIX):
            node = self.nodes[result["ref"][len(EVIDENCE_PREFIX):]]
            rank = {"data": 0, "model": 1, "assumption": 2}
            inputs = node.get("inputs", [])
            if any(rank[child["source_type"]] > rank[result["source_type"]] for child in inputs):
                raise ValueError("Derived provenance cannot upgrade its nested input source types")
        return result

    def compact_ref(self, ref):
        if ref.startswith(EVIDENCE_PREFIX):
            if ref[len(EVIDENCE_PREFIX):] not in self.nodes:
                raise ValueError("Unresolved grid-impact evidence pointer in supplied input")
            return ref
        if ref in self.refs:
            return self.refs[ref]
        if not ref.lstrip().startswith("{"):
            self.refs[ref] = ref
            return ref
        try:
            decoded = json.loads(ref)
        except (ValueError, TypeError):
            self.refs[ref] = ref
            return ref
        known = (isinstance(decoded, dict) and set(decoded) == {"method", "inputs"}
                 and isinstance(decoded["method"], str) and isinstance(decoded["inputs"], list)
                 and all(isinstance(item, dict) and {"source_type", "ref"}.issubset(item)
                         for item in decoded["inputs"]))
        if not known:
            self.refs[ref] = ref
            return ref
        pointer = self.register({"method": decoded["method"],
                                 "inputs": self.inputs(decoded["inputs"])})
        self.refs[ref] = pointer
        return pointer

    def inputs(self, origins):
        unique = {}
        for item in origins:
            compact = self.compact_source(item)
            encoded = json.dumps(compact, sort_keys=True, separators=(",", ":"), allow_nan=False)
            unique.setdefault(encoded, compact)
        return [unique[key] for key in sorted(unique)]

    def derive(self, value, origins, method):
        # Validate the uncompressed sources before a pointer can obscure them.
        checked = self.inputs(origins)
        kind = "assumption" if any(s["source_type"] == "assumption" for s in checked) else "model" if any(s["source_type"] == "model" for s in checked) else "data"
        pointer = self.register({"method": method, "inputs": checked})
        return _datum({"value": value, "source_type": kind, "ref": pointer},
                      "derived grid impact", nullable=True, signed=True)

    def finish(self, result):
        def attach(value):
            if isinstance(value, dict):
                if {"value", "source_type", "ref"}.issubset(value):
                    if value["ref"].startswith(EVIDENCE_PREFIX):
                        self.compact_ref(value["ref"])
                        return value
                    attached = self.derive(value["value"], [value], "submitted sourced value")
                    attached["value"] = value["value"]
                    return attached
                return {key: attach(child) for key, child in value.items()}
            if isinstance(value, list):
                return [attach(child) for child in value]
            return value

        result = attach(result)
        reachable = set()
        def visit(value):
            if isinstance(value, str) and value.startswith(EVIDENCE_PREFIX):
                digest = value[len(EVIDENCE_PREFIX):]
                if digest not in self.nodes:
                    raise ValueError("Unresolved grid-impact evidence pointer")
                if digest not in reachable:
                    reachable.add(digest)
                    visit(self.nodes[digest])
            elif isinstance(value, dict):
                for child in value.values():
                    visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
        visit(result)
        result["evidence"] = {key: self.nodes[key] for key in sorted(reachable)}
        return result


def _unavailable(evidence, reason, *origins):
    return evidence.derive(None, [*origins, {"source_type": "assumption", "ref": f"unavailable: {reason}"}], "unavailable; no numeric substitute")


def _hour(value):
    if not isinstance(value, str):
        raise ValueError("Coverage and pair timestamps must be ISO strings")
    try:
        time = pd.Timestamp(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Invalid grid-impact timestamp") from error
    if pd.isna(time) or time.tzinfo is None:
        raise ValueError("Grid-impact timestamps need an explicit timezone")
    time = time.tz_convert("UTC")
    if time != time.floor("h"):
        raise ValueError("Grid-impact timestamps must be hourly interval boundaries")
    return time


def _coverage(raw, *, schedule_scope):
    required = {"period_start_utc", "period_end_exclusive_utc", *COUNT_KEYS}
    if not isinstance(raw, Mapping) or not required.issubset(raw):
        raise ValueError("Explicit period coverage is required")
    start, end = _hour(raw["period_start_utc"]), _hour(raw["period_end_exclusive_utc"])
    if end <= start:
        raise ValueError("Coverage must have a positive interval")
    counts = {key: _datum(raw[key], key, integer=True) for key in COUNT_KEYS}
    count = {key: item["value"] for key, item in counts.items()}
    hours = int((end - start) / pd.Timedelta(1, unit="h"))
    if count["observed_hours"] + count["missing_interval_hours"] != hours:
        raise ValueError("Observed plus missing hours must equal the coverage interval")
    if count["evaluable_hours"] + count["unknown_hours"] != count["observed_hours"]:
        raise ValueError("Evaluable plus unknown hours must equal observed hours")
    if schedule_scope not in {"complete_period", "selected_pairs_only"}:
        raise ValueError("Declare schedule_scope as complete_period or selected_pairs_only")
    complete = start == pd.Timestamp(year=start.year, month=1, day=1, tz="UTC") and end == pd.Timestamp(year=start.year + 1, month=1, day=1, tz="UTC")
    complete = bool(complete and count["evaluable_hours"] == hours and schedule_scope == "complete_period")
    return {"status": "complete_calendar_year" if complete else "partial_period",
            "period_start_utc": start.isoformat(), "period_end_exclusive_utc": end.isoformat(),
            **counts, "schedule_scope": schedule_scope}


def _annual(evidence, datum, coverage):
    origins = [datum, *(coverage[key] for key in COUNT_KEYS)]
    if coverage["status"] != "complete_calendar_year" or datum["value"] is None:
        return _unavailable(evidence, "requires one complete calendar year of evaluable observations and a complete-period schedule", *origins)
    return evidence.derive(datum["value"], origins, "same complete observed UTC calendar-year total; no extrapolation")


def _same_quantity(first, second):
    if first == second:
        return True
    return first != 0 and second != 0 and math.nextafter(first, second) == second


def _sum_quantities(values):
    try:
        return math.fsum(sorted(values))
    except OverflowError as error:
        raise ValueError("Grid-impact aggregate overflows; inspect input units and scale") from error


def _shift_total(evidence, raw, coverage):
    required = {"boundary", "pairs", "carbon_shifted_kg_co2", "mwh_removed", "mwh_made_up"}
    if not isinstance(raw, Mapping) or not required.issubset(raw) or raw.get("boundary") != BOUNDARY or not isinstance(raw.get("pairs"), list):
        raise ValueError("carbon_shift must be a direct operational shift_carbon result with explicit pairs")
    if not raw["pairs"]:
        raise ValueError("An empty shift schedule is unavailable; use carbon_shift=None")
    total = _datum(raw["carbon_shifted_kg_co2"], "carbon_shifted_kg_co2", nullable=True, signed=True)
    removed = _datum(raw["mwh_removed"], "mwh_removed")
    made_up = _datum(raw["mwh_made_up"], "mwh_made_up")
    values, energies, origins, seen = [], [], [total, removed, made_up, SCENARIO_SOURCE], set()
    start, end = _hour(coverage["period_start_utc"]), _hour(coverage["period_end_exclusive_utc"])
    for pair in raw["pairs"]:
        if not isinstance(pair, Mapping) or not {"risk_hour", "makeup_hour", "mwh_removed", "mwh_made_up", "carbon_shifted_kg_co2"}.issubset(pair):
            raise ValueError("Every carbon shift pair requires ordered hours, conserved MWh and carbon")
        risk, makeup = _hour(pair["risk_hour"]), _hour(pair["makeup_hour"])
        if not start <= risk < makeup < end:
            raise ValueError("Both ordered shift hours must lie within the declared coverage period")
        if (risk, makeup) in seen:
            raise ValueError("Duplicate carbon shift pair")
        seen.add((risk, makeup))
        before = _datum(pair["mwh_removed"], "pair mwh_removed")
        after = _datum(pair["mwh_made_up"], "pair mwh_made_up")
        carbon = _datum(pair["carbon_shifted_kg_co2"], "pair carbon shifted", nullable=True, signed=True)
        if not _same_quantity(before["value"], after["value"]):
            raise ValueError("Every shift pair must conserve submitted MWh")
        if before["value"] == 0 and carbon["value"] is not None and carbon["value"] != 0:
            raise ValueError("A zero-MWh pair cannot have nonzero carbon shifted")
        values.append(carbon["value"])
        energies.append(before["value"])
        origins.extend([before, after, carbon])
    energy = _sum_quantities(energies)
    if not all(_same_quantity(item["value"], energy) for item in (removed, made_up)):
        raise ValueError("Shift energy totals disagree with their explicit pairs")
    expected = None if None in values else _sum_quantities(values)
    if (expected is None) != (total["value"] is None) or expected is not None and not _same_quantity(expected, total["value"]):
        raise ValueError("Shift carbon total disagrees with its pairs or hides unknown intensities")
    return evidence.derive(None if expected is None else expected / 1000, origins, "signed kilograms CO2 / 1000 = tonnes CO2; explicit conserved-energy pairs")


def compose_grid_impact(location_id, *, wind_summary=None, carbon_shift=None, shift_coverage=None, unavailable_reason=None):
    """Validate already computed inputs and return the standalone sourced contract."""
    location_id = _location(location_id)
    evidence = _Evidence()
    if unavailable_reason is not None and (not isinstance(unavailable_reason, str) or not unavailable_reason.strip()):
        raise ValueError("An unavailable reason must be nonempty text")
    result = {"schema_version": "grid-impact-v1", "location_id": location_id, "boundary": BOUNDARY,
              "units": dict(UNITS), "basis": dict(BASIS), "evidence_context": {},
              "coverage": {key: {"status": "unavailable", "reason": f"No {key} observations or schedule supplied"} for key in ("wind", "shift")},
              **{key: _unavailable(evidence, unavailable_reason or "no precomputed observations or explicit energy schedule supplied") for key in UNITS}}
    if wind_summary is not None:
        if not isinstance(wind_summary, Mapping) or wind_summary.get("location_id") != location_id:
            raise ValueError("Wind summary location must exactly match the requested location")
        if not {"wind_absorption_mwh_in_observed_hours", "wind_absorption_mwh_per_year", "proxy_hours"}.issubset(wind_summary):
            raise ValueError("Wind summary requires observed and annual MWh plus proxy-hour count")
        coverage = _coverage(wind_summary, schedule_scope="complete_period")
        if any(not isinstance(wind_summary.get(key), str) or not wind_summary[key].strip() for key in ("method", "system_scope")):
            raise ValueError("Wind summary requires its screen method and system_scope")
        result["coverage"]["wind"] = coverage
        energy = _datum(wind_summary["wind_absorption_mwh_in_observed_hours"], "observed wind MWh", nullable=True)
        proxy = _datum(wind_summary["proxy_hours"], "proxy_hours", nullable=True, integer=True)
        evaluable = coverage["evaluable_hours"]["value"]
        if (evaluable == 0) != (proxy["value"] is None) or proxy["value"] is not None and proxy["value"] > evaluable:
            raise ValueError("Proxy hours must represent evaluable hours; unknown history cannot claim zero opportunity")
        if (evaluable == 0) != (energy["value"] is None) or proxy["value"] == 0 and energy["value"] != 0:
            raise ValueError("Wind energy is inconsistent with observed proxy-hour coverage")
        context = evidence.register({"method": "wind screening context", "screen_method": wind_summary["method"],
                                     "system_scope": wind_summary["system_scope"],
                                     "proxy_hours": evidence.compact_source(proxy),
                                     "coverage": {key: evidence.compact_source(value) if key in COUNT_KEYS else value for key, value in coverage.items()}})
        result["evidence_context"]["wind"] = context
        energy = evidence.derive(energy["value"], [energy, proxy, SCENARIO_SOURCE,
                                  {"source_type": "assumption", "ref": context}], "flexible-load scenario in evaluable observed high-wind/low-price hours")
        annual = _annual(evidence, energy, coverage)
        supplied_annual = _datum(wind_summary["wind_absorption_mwh_per_year"], "annual wind MWh", nullable=True)
        if supplied_annual["value"] != annual["value"]:
            raise ValueError("Annual wind MWh disagrees with verified complete-year coverage and observed total")
        annual = evidence.derive(annual["value"], [annual, supplied_annual], "validated annual wind scenario")
        result["wind_absorption_mwh_in_observed_hours"] = energy
        result["wind_absorption_mwh_per_year"] = annual
        if energy["value"] is None:
            operational = _unavailable(evidence, "wind energy is unknown; the operational zero factor does not establish an energy quantity", energy)
        else:
            carbon = wind_carbon(energy, selection_source=SCENARIO_SOURCE)["wind_operational_co2_kg"]
            operational = evidence.derive(carbon["value"] / 1000, [carbon], "associated wind direct operational kilograms CO2 / 1000 = tonnes CO2")
        result["carbon_absorbed_tonnes_in_observed_hours"] = operational
        result["carbon_absorbed_tonnes_per_year"] = _annual(evidence, operational, coverage)
    if carbon_shift is not None:
        if not isinstance(shift_coverage, Mapping):
            raise ValueError("carbon_shift requires explicit shift_coverage")
        coverage = _coverage(shift_coverage, schedule_scope=shift_coverage.get("schedule_scope"))
        result["coverage"]["shift"] = coverage
        shifted = _shift_total(evidence, carbon_shift, coverage)
        if coverage["evaluable_hours"]["value"] == 0:
            shifted = _unavailable(evidence, "shift selection has no evaluable observed hours", shifted)
        result["carbon_shifted_tonnes_in_observed_hours"] = shifted
        result["carbon_shifted_tonnes_per_year"] = _annual(evidence, shifted, coverage)
    elif shift_coverage is not None:
        raise ValueError("shift_coverage without a carbon_shift result is inconsistent")
    return evidence.finish(result)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _nonfinite_constant(value):
    raise ValueError(f"Nonfinite JSON constant: {value}")


def get_location_grid_impact(location_id, path=DEFAULT_PATH):
    """Read one location from the documented offline cache; missing is unavailable."""
    location_id = _location(location_id)
    path = Path(path)
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return compose_grid_impact(location_id, unavailable_reason=f"missing precomputed grid-impact file: {path}")
    cache = json.loads(content, object_pairs_hook=_unique_object, parse_constant=_nonfinite_constant)
    if not isinstance(cache, dict) or set(cache) != {"schema_version", "locations"} or cache["schema_version"] != "grid-impact-inputs-v1" or not isinstance(cache["locations"], list):
        raise ValueError("Expected grid-impact-inputs-v1 cache with a locations list")
    found = None
    seen = set()
    for row in cache["locations"]:
        if not isinstance(row, dict) or set(row) != {"location_id", "wind_summary", "carbon_shift", "shift_coverage"}:
            raise ValueError("Malformed precomputed grid-impact location record")
        key = _location(row["location_id"])
        if key in seen:
            raise ValueError("Duplicate grid-impact location")
        seen.add(key)
        if key == location_id:
            found = row
    if found is not None:
        try:
            return compose_grid_impact(**found)
        except (KeyError, TypeError, OverflowError) as error:
            raise ValueError(f"Malformed grid-impact inputs for {location_id}") from error
    return compose_grid_impact(location_id, unavailable_reason=f"location {location_id} is absent from precomputed grid-impact file: {path}")
