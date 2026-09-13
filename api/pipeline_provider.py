"""Adapt Kristian's precomputed reader; never train, simulate, or fetch here."""

from importlib import import_module, invalidate_caches
from datetime import datetime
import logging
from pathlib import Path
import re
from typing import Annotated, Literal
from typing_extensions import Self

from pydantic import Field, ValidationError, model_validator

from .artifact_json import loads_artifact
from .mock_provider import BaselineYear, LocationEstimate, LocationNotFoundError, get_mock_location, placeholder_tariff
from .schemas import Confidence, ContractModel, Fraction, NonEmpty, NonNegative, Source


logger = logging.getLogger(__name__)
PARQUET_PATH = Path(__file__).resolve().parents[1] / "data/processed/exposure_by_location.parquet"


class PipelineDataError(RuntimeError):
    """A present pipeline is broken or violates the precomputed reader contract."""


class PipelineYear(ContractModel):
    year_offset: Annotated[int, Field(ge=1, le=7)]
    p50_hours: NonNegative
    p90_hours: NonNegative
    p99_hours: NonNegative
    worst_contiguous_hours: NonNegative

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if not self.p50_hours <= self.p90_hours <= self.p99_hours:
            raise ValueError("Pipeline quantiles must satisfy p50 <= p90 <= p99")
        if max(self.p99_hours, self.worst_contiguous_hours) > 8784:
            raise ValueError("Pipeline exposure cannot exceed the hours in a year")
        return self


class PipelineConfidence(ContractModel):
    level: Literal["High", "Medium", "Low"]
    score: Fraction
    n_similar_historical_hours: Annotated[int, Field(ge=0)]


class ConfidenceEvidence(PipelineConfidence):
    """Recorded classifier evidence, before the separate annual-tail Low cap."""

    agreement_score: Fraction
    historical_support_score: Fraction
    mean_ensemble_probability_std: Annotated[float, Field(ge=0, le=0.5)]
    limitations: list[NonEmpty]
    policy_version: Annotated[int, Field(ge=2, le=2)]
    source_ref: Literal["pipeline/confidence.py:CONFIDENCE_POLICY"]


class PipelineEstimate(ContractModel):
    """Exact get_location_estimate output from BUILD_PLAN.md section 1."""

    location_id: NonEmpty
    by_year: Annotated[list[PipelineYear], Field(min_length=1, max_length=7)]
    confidence: PipelineConfidence
    model_version: NonEmpty

    @model_validator(mode="after")
    def complete_horizon(self) -> Self:
        if [row.year_offset for row in self.by_year] != list(range(1, len(self.by_year) + 1)):
            raise ValueError("Pipeline years must start at one and be complete and ordered")
        return self


def _placeholder(location_id: str, reason: str) -> LocationEstimate:
    # The response itself retains the reason; an absent pipeline never looks live.
    logger.info("Using placeholder exposure: %s", reason)
    return get_mock_location(location_id, reason=reason)


def _read_provenance(path: Path) -> dict:
    value = loads_artifact(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return value


def _validate_confidence(data: PipelineEstimate, card: dict, simulation: dict) -> ConfidenceEvidence:
    # Share the producer's arithmetic without importing training or fitting models.
    from pipeline.confidence_policy import CONFIDENCE_POLICY, confidence_from_evidence

    for manifest in (card, simulation):
        policy = manifest.get("confidence_policy")
        if (not isinstance(policy, dict) or policy != CONFIDENCE_POLICY
                or any(type(policy[key]) is not type(value) for key, value in CONFIDENCE_POLICY.items())):
            raise ValueError("Stale or unsupported confidence policy; regenerate the matching artifact bundle")
    by_location = card.get("confidence")
    if not isinstance(by_location, dict):
        raise ValueError("Model card requires per-location confidence evidence")
    evidence = ConfidenceEvidence.model_validate(by_location.get(data.location_id))
    expected = confidence_from_evidence(evidence.mean_ensemble_probability_std,
                                        evidence.n_similar_historical_hours, evidence.limitations)
    if evidence.model_dump() != expected:
        raise ValueError("Confidence score/components do not match the recorded agreement and historical support")
    if any(expected[key] != getattr(data.confidence, key) for key in ("score", "n_similar_historical_hours")):
        raise ValueError("Confidence evidence does not match the exposure table")
    return evidence


def _validate_provenance(data: PipelineEstimate, card: dict, simulation: dict) -> tuple[str, str]:
    """Check the small producer handoff, not model fitness or human approval.

    These manifests document research output. Their presence does not turn the
    provisional label/calibration rules or annual tails into validated forecasts.
    """
    if card.get("model_version") != data.model_version:
        raise ValueError("Model card version does not match the exposure table")
    if card.get("status") != "research_only_pending_label_and_calibration_review":
        raise ValueError("Unsupported model-card research status")
    policy = card.get("policy")
    if not isinstance(policy, dict) or policy.get("operator") != "SPP":
        raise ValueError("Model card requires an SPP target policy")
    if policy.get("label_method") not in {"observed_event", "reserve_shortfall", "binding_constraint", "scarcity_price"}:
        raise ValueError("Model card target is unsupported")
    for key in ("data_ref", "label_ref"):
        value = policy.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Model card requires a nonempty {key}")
        if value.lower().startswith(("mock:", "placeholder", "synthetic:", "test:")):
            raise ValueError(f"Model card {key} identifies placeholder evidence")
    if policy["label_method"] == "scarcity_price":
        threshold = policy.get("price_threshold_usd_mwh")
        if type(threshold) not in (int, float) or not float("-inf") < threshold < float("inf"):
            raise ValueError("Scarcity target requires an explicit finite price threshold")
    hashes = card.get("input_hashes")
    if not isinstance(hashes, dict) or any(
        not isinstance(hashes.get(key), str) or not re.fullmatch(r"[0-9a-fA-F]{64}", hashes[key])
        for key in ("hourly_sha256", "policy_sha256")
    ):
        raise ValueError("Model card requires hourly and policy SHA-256 input hashes")
    if simulation.get("policy") != policy:
        raise ValueError("Simulation target policy does not match the model card")
    if simulation.get("status") != "experimental_unvalidated_annual_tails":
        raise ValueError("Unsupported simulation validation status")
    if simulation.get("method") != "seasonal_joint_probability_and_randomized_residual_block_bootstrap":
        raise ValueError("Unsupported simulation method")
    if simulation.get("source_type") != "model" or simulation.get("ref") != f"pipeline/simulate.py model_version={data.model_version}":
        raise ValueError("Simulation provenance does not match the exposure model version")
    if simulation.get("site_exposure_applied") is not False:
        raise ValueError("Pipeline must not apply the user-set site exposure factor")
    for key, minimum in (("simulations", 1000), ("years", 1), ("seed", 0), ("block_hours", 24)):
        value = simulation.get(key)
        if type(value) is not int or value < minimum:
            raise ValueError(f"Simulation requires a valid {key}")
    if simulation["years"] != len(data.by_year):
        raise ValueError("Simulation year count does not match the exposure table")
    if simulation["block_hours"] > 168 or simulation["block_hours"] % 24:
        raise ValueError("Simulation blocks must cover complete days up to seven days")
    if type(simulation.get("hours_per_year")) is not int or simulation["hours_per_year"] != 8760:
        raise ValueError("Simulation must document the producer's 365-day comparison years")
    if any(max(row.p99_hours, row.worst_contiguous_hours) > 8760 for row in data.by_year):
        raise ValueError("Exposure exceeds the simulation's hours per year")
    if data.confidence.level != "Low":
        raise ValueError("Unvalidated annual exposure confidence must remain capped Low")
    confidence = _validate_confidence(data, card, simulation)
    members = card.get("settings", {}).get("ensemble_members")
    if type(members) is not int or members < 2:
        raise ValueError("Model card requires at least two ensemble members")

    exposure_ref = (
        f"pipeline/simulate.py model_version={data.model_version}; data/processed/exposure_by_location.parquet; "
        f"model_card.json; simulation_metadata.json; target={policy['label_method']}; "
        f"data_ref={policy['data_ref']}; label_ref={policy['label_ref']}; "
        f"hourly_sha256={hashes['hourly_sha256']}; policy_sha256={hashes['policy_sha256']}; "
        f"experimental_unvalidated_annual_tails; simulations={simulation['simulations']}; seed={simulation['seed']}; "
        f"block_hours={simulation['block_hours']}; 365-day stationary seasonal comparison; "
        "worst_contiguous_hours=p99 of annual longest modeled episodes, not a guaranteed maximum; "
        "system-event/proxy research output, not actual site curtailment; label and calibration review pending"
    )
    confidence_ref = (
        f"{exposure_ref}; ensemble_members={members}; "
        f"confidence_policy_version={confidence.policy_version}; "
        f"n_similar_historical_hours={data.confidence.n_similar_historical_hours}; "
        f"agreement_score={confidence.agreement_score:.12g}; "
        f"historical_support_score={confidence.historical_support_score:.12g}; "
        f"limitations={','.join(confidence.limitations) or 'none recorded'}; "
        "annual exposure confidence capped Low; numeric score combines classifier ensemble agreement "
        "with same-location historical support and validation limits, "
        "not annual-tail coverage or a probability of correctness"
    )
    return exposure_ref, confidence_ref


def _annual_reference_issue(data: PipelineEstimate, card: dict) -> str | None:
    """Require annual held-out evidence, without pooling hours across locations.

    Split endpoints are inclusive hourly interval starts. This is a minimum
    readiness check, not validation of annual tails or of the underlying labels.
    """
    splits = card.get("splits", {})
    by_location = card.get("test_by_location", {})
    if not isinstance(splits, dict) or not isinstance(by_location, dict):
        raise ValueError("Annual reference coverage must contain objects")
    test = splits.get("test", {})
    local = by_location.get(data.location_id, {})
    if not isinstance(test, dict) or not isinstance(local, dict):
        raise ValueError("Held-out split and location coverage must contain objects")

    missing = []
    times = {}
    for key in ("start", "end"):
        if key not in test:
            missing.append(f"splits.test.{key}")
            continue
        value = test[key]
        if not isinstance(value, str):
            raise ValueError(f"Held-out {key} must be an hourly timestamp with a timezone")
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if timestamp.utcoffset() is None or any((timestamp.minute, timestamp.second, timestamp.microsecond)):
            raise ValueError(f"Held-out {key} must be an hourly timestamp with a timezone")
        times[key] = timestamp
    count = local.get("n_hours")
    if "n_hours" not in local:
        missing.append(f"test_by_location[{data.location_id}].n_hours")
    elif type(count) is not int or count < 0:
        raise ValueError("Held-out local scored hours must be a nonnegative integer")

    span_hours = None
    if len(times) == 2:
        if times["end"] < times["start"]:
            raise ValueError("Held-out reference endpoints are reversed")
        span_hours = (times["end"] - times["start"]).total_seconds() / 3600 + 1
        if count is not None and count > span_hours:
            raise ValueError("Held-out local scored hours exceed the reference time span")
    if missing:
        return "annual reference coverage missing: " + ", ".join(missing)
    if span_hours < 8760 or count < 8760:
        return (
            f"annual reference not ready: held-out span={span_hours:g} hours, "
            f"local scored hours={count} for {data.location_id}; "
            "requires at least 8760 hours (365 days) for both; missing seasons must not be substituted"
        )
    return None


def get_pipeline_location(location_id: str) -> LocationEstimate:
    try:
        module = import_module("pipeline.simulate")
    except ModuleNotFoundError as error:
        if error.name in ("pipeline", "pipeline.simulate"):
            invalidate_caches()  # Discover a new reader on the next request.
            return _placeholder(location_id, "pipeline.simulate is absent")
        logger.exception("Pipeline reader dependency cannot be imported")
        raise PipelineDataError("Pipeline reader dependency is unavailable") from error
    except FileNotFoundError:
        return _placeholder(location_id, "the pipeline module's precomputed file is unavailable")
    except Exception as error:
        logger.exception("Pipeline reader import failed")
        raise PipelineDataError("Pipeline reader could not be imported") from error

    reader = getattr(module, "get_location_estimate", None)
    if not callable(reader):
        return _placeholder(location_id, "get_location_estimate is absent")
    if not PARQUET_PATH.is_file():
        return _placeholder(location_id, "data/processed/exposure_by_location.parquet is absent")

    sidecars = {name: PARQUET_PATH.with_name(name) for name in ("model_card.json", "simulation_metadata.json")}
    missing = [name for name, path in sidecars.items() if not path.is_file()]
    if missing:
        return _placeholder(location_id, f"pipeline artifact provenance is incomplete: {', '.join(missing)} absent")

    try:
        payload = reader(location_id, path=PARQUET_PATH)
    except FileNotFoundError:
        # Covers the file being moved/replaced after the existence check.
        return _placeholder(location_id, "the pipeline reader's precomputed file is unavailable")
    except Exception as error:
        location_error = getattr(module, "LocationNotFoundError", None)
        if isinstance(location_error, type) and issubclass(location_error, Exception) and isinstance(error, location_error):
            raise LocationNotFoundError(location_id) from error
        logger.exception("Precomputed pipeline reader failed")
        raise PipelineDataError("Precomputed pipeline reader failed") from error

    try:
        data = PipelineEstimate.model_validate(payload)
        if data.location_id != location_id:
            raise ValueError("Pipeline returned a different location")
    except (ValidationError, ValueError) as error:
        logger.exception("Invalid precomputed pipeline output")
        raise PipelineDataError("Precomputed output does not match BUILD_PLAN.md section 1") from error

    try:
        card = _read_provenance(sidecars["model_card.json"])
        simulation = _read_provenance(sidecars["simulation_metadata.json"])
        exposure_ref, confidence_ref = _validate_provenance(data, card, simulation)
        readiness_issue = _annual_reference_issue(data, card)
    except FileNotFoundError:
        return _placeholder(location_id, "pipeline artifact provenance disappeared during read")
    except (OSError, ValueError, TypeError, AttributeError) as error:
        logger.exception("Invalid pipeline artifact provenance")
        raise PipelineDataError(f"Precomputed artifact provenance is invalid: {error}") from error
    if readiness_issue:
        return _placeholder(location_id, f"model_version={data.model_version}; {readiness_issue}")
    return LocationEstimate(
        by_year=tuple(BaselineYear(**row.model_dump()) for row in data.by_year),
        confidence=Confidence(
            level=data.confidence.level,
            score=data.confidence.score,
            basis="ensemble_agreement_and_historical_support",
            source=Source(source_type="model", ref=confidence_ref),
        ),
        source=Source(source_type="model", ref=exposure_ref),
        tariff=placeholder_tariff(),
    )
