"""Synthetic snapshot contradictions; no fixture represents measured emissions."""
from copy import deepcopy
import hashlib
import json
import math

import pytest

from api.grid_impact import EVIDENCE_PREFIX, compile_grid_impact_snapshot, read_grid_impact_snapshot
from pipeline.carbon import BOUNDARY, shift_carbon


PRODUCER_SUM = "signed sum across all explicit shift pairs; not a causal emissions reduction estimate"


def datum(value):
    return {"value": value, "source_type": "assumption", "ref": "synthetic shift-derivation fixture"}


def snapshot(path, makeup_intensity):
    risk, makeup = "2024-01-01T00:00:00Z", "2024-01-01T01:00:00Z"
    raw = shift_carbon(
        [{"timestamp_utc": risk, "boundary": BOUNDARY, "intensity_kg_co2_per_mwh": datum(100.)},
         {"timestamp_utc": makeup, "boundary": BOUNDARY, "intensity_kg_co2_per_mwh": datum(makeup_intensity)}],
        [{"risk_hour": risk, "makeup_hour": makeup, "mwh": datum(10.)}],
        selection_source={"source_type": "assumption", "ref": "synthetic explicit schedule"},
        hourly_limits={risk: {"removable_mwh": datum(10.)}, makeup: {"makeup_capacity_mwh": datum(10.)}},
    )
    coverage = {"period_start_utc": risk, "period_end_exclusive_utc": "2024-01-01T04:00:00Z",
                "observed_hours": datum(4), "evaluable_hours": datum(3 if makeup_intensity is None else 4),
                "unknown_hours": datum(1 if makeup_intensity is None else 0),
                "missing_interval_hours": datum(0), "schedule_scope": "complete_period"}
    before = deepcopy((raw, coverage))
    compile_grid_impact_snapshot("SYNTHETIC_NODE", path, carbon_shift=raw, shift_coverage=coverage)
    assert (raw, coverage) == before
    return path


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def rewrite_named_total(path, kilograms, *, edit_producer_total=False):
    """Rehash changed ancestors while retaining the exact producer pair graph."""
    document = json.loads(path.read_bytes())
    original = document["result"]
    observed_ref = original["carbon_shifted_tonnes_in_observed_hours"]["ref"]
    nodes = original["evidence"]
    conversion = nodes[observed_ref[len(EVIDENCE_PREFIX):]]
    named_total = conversion["inputs"][0]
    assert named_total["quantity"] == "carbon_shifted_kg_co2" and named_total["unit"] == "kg CO2"
    checked_sum = nodes[named_total["ref"][len(EVIDENCE_PREFIX):]]
    totals = [item for item in checked_sum["inputs"] if item.get("ref", "").startswith(EVIDENCE_PREFIX)
              and nodes[item["ref"][len(EVIDENCE_PREFIX):]]["method"] == PRODUCER_SUM]
    assert len(totals) == 1
    producer_ref = totals[0]["ref"]
    producer_node = deepcopy(nodes[producer_ref[len(EVIDENCE_PREFIX):]])
    rewritten, memo = {}, {}

    def visit(value):
        if isinstance(value, str) and value.startswith(EVIDENCE_PREFIX):
            key = value[len(EVIDENCE_PREFIX):]
            if key not in memo:
                changed = visit(nodes[key])
                new_key = digest(changed)
                rewritten[new_key] = changed
                memo[key] = EVIDENCE_PREFIX + new_key
            return memo[key]
        if isinstance(value, dict):
            changed = dict(value)
            if changed.get("quantity") == "carbon_shifted_kg_co2":
                changed["value"] = kilograms
            elif changed.get("ref") == observed_ref:
                changed["value"] = None if kilograms is None else kilograms / 1000
            elif edit_producer_total and changed.get("ref") == producer_ref:
                changed["value"] = kilograms
            return {key: visit(item) for key, item in changed.items()}
        if isinstance(value, list):
            return [visit(item) for item in value]
        return value

    result = visit({key: value for key, value in original.items() if key != "evidence"})
    result["evidence"] = rewritten
    # Even when the aggregate datum is changed, its exact published pair inputs
    # and their unknown-intensity provenance remain unchanged and reachable.
    assert rewritten[producer_ref[len(EVIDENCE_PREFIX):]] == producer_node
    document["result"], document["result_sha256"] = result, digest(result)
    path.write_text(json.dumps(document, sort_keys=True, allow_nan=False), encoding="utf-8")
    return result


@pytest.mark.parametrize("makeup_intensity,kilograms", [
    (None, 0.),       # Unknown pair and total cannot become a known zero.
    (300., 2000.),    # A retained negative pair cannot become a positive total.
    (0., None),      # A known positive sum cannot become unknown.
    (100., math.ulp(0.)),  # Known zero is exact, even at the smallest float.
])
@pytest.mark.parametrize("edit_producer_total", [False, True])
def test_named_total_cannot_contradict_retained_producer_pairs(tmp_path, makeup_intensity, kilograms, edit_producer_total):
    path = snapshot(tmp_path / "result.json", makeup_intensity)
    rewrite_named_total(path, kilograms, edit_producer_total=edit_producer_total)
    with pytest.raises(ValueError, match="evidence derivation"):
        read_grid_impact_snapshot("SYNTHETIC_NODE", path, required=True)


@pytest.mark.parametrize("direction", [-math.inf, math.inf])
def test_named_total_retains_one_step_nonzero_roundoff_without_mutating_evidence(tmp_path, direction):
    path = snapshot(tmp_path / "result.json", 300.)
    kilograms = math.nextafter(-2000., direction)
    expected = rewrite_named_total(path, kilograms)
    before = path.read_bytes()
    result = read_grid_impact_snapshot("SYNTHETIC_NODE", path, required=True)
    assert result == expected
    assert path.read_bytes() == before
    assert result["carbon_shifted_tonnes_in_observed_hours"]["value"] == kilograms / 1000
    assert result["carbon_shifted_tonnes_in_observed_hours"]["source_type"] == "assumption"
    assert result["carbon_shifted_tonnes_per_year"]["value"] is None


def test_named_total_rejects_two_steps_of_nonzero_disagreement(tmp_path):
    path = snapshot(tmp_path / "result.json", 300.)
    kilograms = math.nextafter(math.nextafter(-2000., math.inf), math.inf)
    rewrite_named_total(path, kilograms)
    with pytest.raises(ValueError, match="evidence derivation"):
        read_grid_impact_snapshot("SYNTHETIC_NODE", path, required=True)
