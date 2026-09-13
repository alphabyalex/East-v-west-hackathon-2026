"""Adapt Kristian's precomputed reader; never train, simulate, or fetch here."""

from importlib import import_module, invalidate_caches
import logging
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, ValidationError, model_validator

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
        return self


class PipelineConfidence(ContractModel):
    level: Literal["High", "Medium", "Low"]
    score: Fraction
    n_similar_historical_hours: Annotated[int, Field(ge=0)]


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

    try:
        payload = reader(location_id)
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

    exposure_ref = f"pipeline/simulate.py model_version={data.model_version}; data/processed/exposure_by_location.parquet"
    confidence_ref = f"{exposure_ref}; n_similar_historical_hours={data.confidence.n_similar_historical_hours}"
    return LocationEstimate(
        by_year=tuple(BaselineYear(**row.model_dump()) for row in data.by_year),
        confidence=Confidence(
            level=data.confidence.level,
            score=data.confidence.score,
            basis="ensemble_disagreement",
            source=Source(source_type="model", ref=confidence_ref),
        ),
        source=Source(source_type="model", ref=exposure_ref),
        tariff=placeholder_tariff(),
    )
