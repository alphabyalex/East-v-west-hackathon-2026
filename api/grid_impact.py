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

Demo read path: compile_grid_impact_snapshot(location_id, path, ...) runs offline
and publishes an immutable single-location snapshot. read_grid_impact_snapshot
returns that already-computed result without calling the raw composer. Its
optional GridImpactSnapshotCache is caller-owned; every read hashes fresh bytes,
then reuses validation for the same path/content digest and returns a deep copy.
Snapshot envelope: {snapshot_version:'grid-impact-snapshot-v1', location_id,
result_sha256, result:<the unchanged grid-impact-v1 result including all evidence>}.
Checksums establish integrity, not the authenticity of the underlying observations.

For request-specific wind capacity, use read_wind_scenario(location_id, path,
flexible_load_mw=<sourced datum>, available_fraction=<sourced datum>, cache=...).
Both controls are required. It uses only the stored screening count and coverage,
not hourly data, and accepts only wind-only snapshots with structured energy_model
and scenario_inputs. It never scales or carries forward a stored carbon-shift
schedule. Legacy snapshots remain readable as fixed scenarios, but cannot be used
by this recomputation helper. Neither site_exposure nor interruptibility itself
establishes available upward capacity; the caller must state that assumption.
"""
from __future__ import annotations

from collections.abc import Mapping
from collections import OrderedDict
from copy import deepcopy
import json
import hashlib
import math
import os
from pathlib import Path
import tempfile
import threading

import pandas as pd

from pipeline.carbon import BOUNDARY, wind_carbon
from pipeline.wind_signal import finite_number, source, wind_scenario_mwh


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
    "carbon_shifted": "Signed MWh times risk-hour minus makeup-hour average CO2 intensity under the supplied factors' accounting convention. eGRID-sourced rates exclude biogenic CO2 and allocate CHP emissions to electricity; they do not represent total physical stack CO2. Positive means lower attributed makeup emissions; negative means higher. This is a conserved-energy scenario, not a causal dispatch estimate.",
    "annual": "Annual values cover one complete evaluable UTC calendar year; partial observed periods are never extrapolated.",
}
SCENARIO_SOURCE = {"source_type": "assumption", "ref": "submitted grid-impact energy schedule; counterfactual site dispatch and local deliverability are not established"}
EVIDENCE_PREFIX = "grid-impact://evidence/"
WIND_ENERGY_MODEL = "declared_available_capacity_times_proxy_hours_v1"


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


def _empty_result(location_id, evidence, reason=None):
    return {"schema_version": "grid-impact-v1", "location_id": location_id, "boundary": BOUNDARY,
            "units": dict(UNITS), "basis": dict(BASIS), "evidence_context": {},
            "coverage": {key: {"status": "unavailable", "reason": f"No {key} observations or schedule supplied"} for key in ("wind", "shift")},
            **{key: _unavailable(evidence, reason or "no precomputed observations or explicit energy schedule supplied") for key in UNITS}}


def _wind_controls(flexible_load_mw, available_fraction):
    controls = {"flexible_load_mw": _datum(flexible_load_mw, "flexible_load_mw"),
                "available_fraction": _datum(available_fraction, "available_fraction")}
    if controls["available_fraction"]["value"] > 1:
        raise ValueError("available_fraction must be between zero and one")
    return controls


def _wind_energy(proxy, controls):
    if proxy["value"] is None:
        return None
    return wind_scenario_mwh(proxy["value"], controls["flexible_load_mw"]["value"],
                             controls["available_fraction"]["value"])


def _wind_scenario_metadata(record, proxy, energy):
    """Legacy fixed records remain readable; present controls must be complete."""
    if not {"energy_model", "scenario_inputs"}.intersection(record):
        return {}
    controls = record.get("scenario_inputs")
    if record.get("energy_model") != WIND_ENERGY_MODEL or not isinstance(controls, Mapping) or set(controls) != {"flexible_load_mw", "available_fraction"}:
        raise ValueError("Wind scenario requires its supported energy_model and both structured controls")
    controls = _wind_controls(**controls)
    expected, supplied = _wind_energy(proxy, controls), energy["value"]
    if (expected is None) != (supplied is None) or expected is not None and not _same_quantity(expected, supplied):
        raise ValueError("Wind MWh disagrees with proxy_hours times flexible_load_mw times available_fraction")
    return {"energy_model": WIND_ENERGY_MODEL, "scenario_inputs": controls}


def _wind_operational_carbon(evidence, energy):
    if energy["value"] is None:
        return _unavailable(evidence, "wind energy is unknown; the operational zero factor does not establish an energy quantity", energy)
    carbon = wind_carbon(energy, selection_source=SCENARIO_SOURCE)["wind_operational_co2_kg"]
    return evidence.derive(carbon["value"] / 1000, [carbon], "associated wind direct operational kilograms CO2 / 1000 = tonnes CO2")


def compose_grid_impact(location_id, *, wind_summary=None, carbon_shift=None, shift_coverage=None, unavailable_reason=None):
    """Validate already computed inputs and return the standalone sourced contract."""
    location_id = _location(location_id)
    evidence = _Evidence()
    if unavailable_reason is not None and (not isinstance(unavailable_reason, str) or not unavailable_reason.strip()):
        raise ValueError("An unavailable reason must be nonempty text")
    result = _empty_result(location_id, evidence, unavailable_reason)
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
        metadata = _wind_scenario_metadata(wind_summary, proxy, energy)
        control_sources = list(metadata.get("scenario_inputs", {}).values())
        if metadata:
            metadata["scenario_inputs"] = {key: evidence.compact_source(value) for key, value in metadata["scenario_inputs"].items()}
        context = evidence.register({"method": "wind screening context", "screen_method": wind_summary["method"],
                                     "system_scope": wind_summary["system_scope"],
                                     "proxy_hours": evidence.compact_source(proxy),
                                     "coverage": {key: evidence.compact_source(value) if key in COUNT_KEYS else value for key, value in coverage.items()}, **metadata})
        result["evidence_context"]["wind"] = context
        energy = evidence.derive(energy["value"], [energy, proxy, *control_sources, SCENARIO_SOURCE,
                                  {"source_type": "assumption", "ref": context}], "flexible-load scenario in evaluable observed high-wind/low-price hours")
        annual = _annual(evidence, energy, coverage)
        supplied_annual = _datum(wind_summary["wind_absorption_mwh_per_year"], "annual wind MWh", nullable=True)
        if supplied_annual["value"] != annual["value"]:
            raise ValueError("Annual wind MWh disagrees with verified complete-year coverage and observed total")
        annual = evidence.derive(annual["value"], [annual, supplied_annual], "validated annual wind scenario")
        result["wind_absorption_mwh_in_observed_hours"] = energy
        result["wind_absorption_mwh_per_year"] = annual
        operational = _wind_operational_carbon(evidence, energy)
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


SNAPSHOT_VERSION = "grid-impact-snapshot-v1"


def _canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _validate_snapshot_result(result, location_id):
    """Validate the stored result and complete DAG without rerunning its models."""
    required = {"schema_version", "location_id", "boundary", "units", "basis", "coverage", "evidence_context", "evidence", *UNITS}
    if not isinstance(result, dict) or set(result) != required:
        raise ValueError("Snapshot result does not match the pinned grid-impact schema")
    if result["schema_version"] != "grid-impact-v1" or result["boundary"] != BOUNDARY or result["location_id"] != location_id:
        raise ValueError("Snapshot result version, location or accounting boundary is inconsistent")
    if result["units"] != UNITS or result["basis"] != BASIS:
        raise ValueError("Snapshot units and honesty framing must match the pinned schema")
    evidence = result["evidence"]
    if not isinstance(evidence, dict) or not evidence:
        raise ValueError("Snapshot requires its complete provenance evidence dictionary")
    rank = {"data": 0, "model": 1, "assumption": 2}
    visited, active = {}, set()

    def pointer(ref):
        if not isinstance(ref, str) or not ref.startswith(EVIDENCE_PREFIX):
            raise ValueError("Snapshot scalar refs must point into their local evidence graph")
        key = ref[len(EVIDENCE_PREFIX):]
        if len(key) != 64 or any(char not in "0123456789abcdef" for char in key) or key not in evidence:
            raise ValueError("Snapshot contains an invalid or dangling evidence pointer")
        if key in active:
            raise ValueError("Snapshot evidence must be an acyclic graph")
        if key not in visited:
            node = evidence[key]
            if not isinstance(node, dict) or not isinstance(node.get("method"), str) or not node["method"].strip() or _digest(node) != key:
                raise ValueError("Snapshot evidence node has invalid metadata or hash")
            active.add(key)
            visited[key] = walk(node)
            active.remove(key)
        return visited[key]

    def walk(value):
        if isinstance(value, dict):
            if {"source_type", "ref"}.intersection(value):
                origin = _source_info(value)
                own_rank = rank[origin["source_type"]]
                if "value" in value:
                    _datum({key: value[key] for key in ("value", "source_type", "ref")}, "snapshot evidence value", nullable=True, signed=True)
                dependencies = [walk(child) for key, child in value.items() if key not in {"source_type", "value"}]
                if max(dependencies, default=-1) > own_rank:
                    raise ValueError("Snapshot provenance upgrades a nested source")
                return own_rank
            return max((walk(child) for child in value.values()), default=-1)
        if isinstance(value, list):
            return max((walk(child) for child in value), default=-1)
        if isinstance(value, str):
            return pointer(value) if value.startswith(EVIDENCE_PREFIX) else -1
        raise ValueError("Every snapshot number or null must be inside a sourced value")

    scalars = {}
    for key in UNITS:
        scalars[key] = _datum(result[key], key, nullable=True, signed=key.startswith("carbon_shifted"))
        pointer(scalars[key]["ref"])
    if not isinstance(result["coverage"], dict) or set(result["coverage"]) != {"wind", "shift"}:
        raise ValueError("Snapshot requires separate wind and shift coverage")
    for name in ("wind", "shift"):
        coverage = result["coverage"][name]
        if not isinstance(coverage, dict):
            raise ValueError("Snapshot coverage must be an object")
        if coverage.get("status") == "unavailable":
            if set(coverage) != {"status", "reason"} or not isinstance(coverage["reason"], str) or not coverage["reason"].strip():
                raise ValueError("Unavailable coverage requires an explicit reason")
        else:
            expected_keys = {"status", "period_start_utc", "period_end_exclusive_utc", "schedule_scope", *COUNT_KEYS}
            if set(coverage) != expected_keys:
                raise ValueError("Snapshot coverage fields do not match the pinned schema")
            checked = _coverage(coverage, schedule_scope=coverage["schedule_scope"])
            if coverage != checked:
                raise ValueError("Snapshot coverage status or counts are inconsistent")
            if name == "wind" and coverage["schedule_scope"] != "complete_period":
                raise ValueError("Wind coverage must describe its complete observed period")
            for key in COUNT_KEYS:
                pointer(coverage[key]["ref"])
        fields = ("wind_absorption_mwh", "carbon_absorbed_tonnes") if name == "wind" else ("carbon_shifted_tonnes",)
        for field in fields:
            observed, annual = (scalars[field + suffix]["value"] for suffix in ("_in_observed_hours", "_per_year"))
            if coverage["status"] == "unavailable" and observed is not None:
                raise ValueError("Unavailable coverage cannot supply an observed numeric total")
            if coverage["status"] != "unavailable" and coverage["evaluable_hours"]["value"] == 0 and observed is not None:
                raise ValueError("Unevaluable coverage cannot supply an observed numeric total")
            expected_annual = observed if coverage["status"] == "complete_calendar_year" else None
            if annual != expected_annual:
                raise ValueError("Snapshot annual values disagree with observed totals or coverage")
    wind = scalars["wind_absorption_mwh_in_observed_hours"]["value"]
    carbon = scalars["carbon_absorbed_tonnes_in_observed_hours"]["value"]
    if carbon != (None if wind is None else 0):
        raise ValueError("Wind operational carbon must be zero for known energy and null for unknown energy")
    contexts = result["evidence_context"]
    wind_available = result["coverage"]["wind"]["status"] != "unavailable"
    if not isinstance(contexts, dict) or set(contexts) != ({"wind"} if wind_available else set()):
        raise ValueError("Snapshot must preserve the available wind screening context")
    if wind_available:
        pointer(contexts["wind"])
        context = evidence[contexts["wind"][len(EVIDENCE_PREFIX):]]
        if not {"screen_method", "system_scope", "proxy_hours", "coverage"}.issubset(context) or any(
            not isinstance(context[key], str) or not context[key].strip() for key in ("screen_method", "system_scope")
        ):
            raise ValueError("Snapshot wind context lacks its method, footprint or count")
        proxy = _datum(context["proxy_hours"], "snapshot proxy_hours", nullable=True, integer=True)["value"]
        counts = result["coverage"]["wind"]
        if not isinstance(context["coverage"], dict) or set(context["coverage"]) != set(counts) or any(
            not isinstance(context["coverage"].get(key), dict)
            or context["coverage"][key].get("value") != counts[key]["value"] for key in COUNT_KEYS
        ) or any(context["coverage"].get(key) != value for key, value in counts.items() if key not in COUNT_KEYS):
            raise ValueError("Snapshot wind context disagrees with its published coverage")
        evaluable = counts["evaluable_hours"]["value"]
        if ((proxy is None) != (evaluable == 0) or (wind is None) != (evaluable == 0)
                or proxy is not None and proxy > evaluable or proxy == 0 and wind != 0):
            raise ValueError("Snapshot wind proxy count disagrees with its energy or coverage")
        _wind_scenario_metadata(context, context["proxy_hours"], scalars["wind_absorption_mwh_in_observed_hours"])
    walk({key: value for key, value in result.items() if key != "evidence"})
    if set(visited) != set(evidence):
        raise ValueError("Snapshot contains unreachable evidence nodes")


def compile_grid_impact_snapshot(location_id, path, *, wind_summary=None, carbon_shift=None, shift_coverage=None):
    """Offline immutable publication; a racing/existing destination is preserved.

    A complete flushed sibling temp file is atomically hard-linked into place.
    No replace/overwrite operation occurs. Filesystems must support hard links;
    unsupported publication fails explicitly and leaves no partial destination.
    Return the output Path. No timestamps/randomness are embedded in its content.
    """
    location_id = _location(location_id)
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"Snapshot already exists: {path}")
    result = compose_grid_impact(location_id, wind_summary=wind_summary, carbon_shift=carbon_shift, shift_coverage=shift_coverage)
    _validate_snapshot_result(result, location_id)
    encoded = _canonical_bytes({"snapshot_version": SNAPSHOT_VERSION, "location_id": location_id,
                                "result_sha256": _digest(result), "result": result})
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return path


class GridImpactSnapshotCache:
    """Caller-owned LRU, bounded by count and serialized input bytes, not heap size.

    No file-stat trust or global registry: read_grid_impact_snapshot hashes fresh
    bytes on every access. Returned copies cannot mutate privately cached results.
    Initialize once during application setup and optionally prewarm demo locations.
    """

    def __init__(self, *, max_entries=8, max_bytes=64 * 1024 * 1024):
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in (max_entries, max_bytes)):
            raise ValueError("Snapshot cache limits must be positive integers")
        self.max_entries, self.max_bytes = max_entries, max_bytes
        self._entries, self._bytes = OrderedDict(), 0
        self._lock = threading.RLock()

    def _get(self, key):
        with self._lock:
            item = self._entries.get(key)
            if item is None:
                return None
            self._entries.move_to_end(key)
            result = item[0]
        return deepcopy(result)

    def _put(self, key, result, size):
        with self._lock:
            for old in [old for old in self._entries if old[0] == key[0]]:
                self._bytes -= self._entries.pop(old)[1]
            if size > self.max_bytes:
                return
            while self._entries and (len(self._entries) >= self.max_entries or self._bytes + size > self.max_bytes):
                _, removed = self._entries.popitem(last=False)
                self._bytes -= removed[1]
            self._entries[key] = (result, size)
            self._bytes += size


def read_grid_impact_snapshot(location_id, path, *, cache=None):
    """Read only one precompiled location; never invoke composition or a model.

    Missing files return sourced unavailable values. A present file identifying a
    different location is an error. Any changed content, even at unchanged size
    and timestamps, is revalidated. The checksum is integrity, not a signature.
    """
    location_id = _location(location_id)
    if cache is not None and not isinstance(cache, GridImpactSnapshotCache):
        raise ValueError("cache must be a GridImpactSnapshotCache")
    path = Path(path)
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        evidence = _Evidence()
        return evidence.finish(_empty_result(location_id, evidence, f"missing precompiled grid-impact snapshot: {path}"))
    key = (str(path.resolve()), hashlib.sha256(content).hexdigest())
    if cache is not None:
        hit = cache._get(key)
        if hit is not None:
            if hit["location_id"] != location_id:
                raise ValueError("Snapshot location does not match the requested location")
            return hit
    try:
        document = json.loads(content, object_pairs_hook=_unique_object, parse_constant=_nonfinite_constant)
        if not isinstance(document, dict) or set(document) != {"snapshot_version", "location_id", "result_sha256", "result"} or document["snapshot_version"] != SNAPSHOT_VERSION:
            raise ValueError("Unsupported grid-impact snapshot envelope")
        if document["location_id"] != location_id:
            raise ValueError("Snapshot location does not match the requested location")
        result = document["result"]
        if document["result_sha256"] != _digest(result):
            raise ValueError("Grid-impact snapshot result checksum mismatch")
        _validate_snapshot_result(result, location_id)
    except (KeyError, TypeError, OverflowError, RecursionError, UnicodeDecodeError) as error:
        raise ValueError("Malformed grid-impact snapshot") from error
    if cache is not None:
        cache._put(key, result, len(content))
    return deepcopy(result)


def read_wind_scenario(location_id, path, *, flexible_load_mw, available_fraction, cache=None):
    """Recompute only declared wind capacity from a validated wind-only snapshot.

    Both sourced caller controls are mandatory; neither uses a hidden default or
    a site-exposure mapping. Observed MWh = proxy hours * flexible MW * available
    fraction, not recoverable surplus or verified local headroom. Annual results
    still require complete evaluable calendar-year coverage. Existing shift
    schedules are rejected, including schedules with unknown/zero carbon totals;
    their conserved pairs must be prepared independently for any new scenario.

    Output remains grid-impact-v1. evidence_context.wind exposes energy_model and
    scenario_inputs for the NEW controls; its baseline node preserves the original
    snapshot's count, controls, source refs and MWh as audit lineage only. No
    existing energy value is multiplied by a capacity ratio. The optional cache
    belongs to the caller and avoids repeating original graph validation.
    """
    controls = _wind_controls(flexible_load_mw, available_fraction)
    original = read_grid_impact_snapshot(location_id, path, cache=cache)
    if original["coverage"]["shift"]["status"] != "unavailable" or any(
        original[field]["value"] is not None for field in ("carbon_shifted_tonnes_in_observed_hours", "carbon_shifted_tonnes_per_year")
    ):
        raise ValueError("read_wind_scenario requires a wind-only snapshot; prepare explicit carbon-shift schedules separately")
    if original["coverage"]["wind"]["status"] == "unavailable":
        return original
    old_pointer = original["evidence_context"]["wind"]
    old_context = original["evidence"][old_pointer[len(EVIDENCE_PREFIX):]]
    if not {"energy_model", "scenario_inputs"}.issubset(old_context):
        raise ValueError("Legacy fixed wind snapshot is unbound; rebuild it with structured scenario_inputs before recomputation")
    # read_grid_impact_snapshot has validated this graph and returned a private
    # copy. Reuse its nodes, hashing only new lineage/derivation nodes below.
    evidence = _Evidence()
    evidence.nodes = original["evidence"]
    proxy = old_context["proxy_hours"]
    baseline = evidence.register({
        "method": "original fixed snapshot scenario; audit lineage only, not the new energy capacity",
        "snapshot_path": str(Path(path)), "wind_context": old_pointer,
        "wind_absorption_mwh_in_observed_hours": original["wind_absorption_mwh_in_observed_hours"],
        "wind_absorption_mwh_per_year": original["wind_absorption_mwh_per_year"],
        "units": {key: UNITS[key] for key in ("wind_absorption_mwh_in_observed_hours", "wind_absorption_mwh_per_year")},
    })
    context = evidence.register({
        "method": "wind screening context", "screen_method": old_context["screen_method"],
        "system_scope": old_context["system_scope"], "proxy_hours": proxy,
        "coverage": old_context["coverage"], "energy_model": WIND_ENERGY_MODEL,
        "scenario_inputs": {key: evidence.compact_source(value) for key, value in controls.items()},
        "baseline": baseline,
    })
    result = _empty_result(original["location_id"], evidence, "wind-only scenario; no independently prepared explicit carbon-shift schedule supplied")
    result["coverage"]["wind"] = original["coverage"]["wind"]
    result["evidence_context"]["wind"] = context
    energy = evidence.derive(_wind_energy(proxy, controls), [proxy, *controls.values(), SCENARIO_SOURCE,
        {"source_type": "assumption", "ref": context}],
        "proxy hours times explicitly supplied constant flexible MW times available fraction; not measured recoverable wind")
    result["wind_absorption_mwh_in_observed_hours"] = energy
    result["wind_absorption_mwh_per_year"] = _annual(evidence, energy, result["coverage"]["wind"])
    operational = _wind_operational_carbon(evidence, energy)
    result["carbon_absorbed_tonnes_in_observed_hours"] = operational
    result["carbon_absorbed_tonnes_per_year"] = _annual(evidence, operational, result["coverage"]["wind"])
    return evidence.finish(result)
