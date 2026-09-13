"""Canonical HTTP contract from docs/BUILD_PLAN.md, section 2."""

from typing import Annotated, Literal
from typing_extensions import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


NonNegative = Annotated[float, Field(ge=0)]
Fraction = Annotated[float, Field(ge=0, le=1)]
NonEmpty = Annotated[str, Field(min_length=1, pattern=r"\S")]


class EstimateRequest(ContractModel):
    location_id: NonEmpty
    load_mw: Annotated[float, Field(gt=0)]
    term_years: Annotated[int, Field(ge=1, le=7)]
    flexibility_split: Fraction
    site_exposure: Fraction


class Source(ContractModel):
    source_type: Literal["data", "clause", "assumption", "model"]
    ref: NonEmpty


class Quantiles(ContractModel):
    p50: NonNegative
    p90: NonNegative
    p99: NonNegative

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if not self.p50 <= self.p90 <= self.p99:
            raise ValueError("Quantiles must satisfy p50 <= p90 <= p99")
        return self


class AnnualExposure(Quantiles):
    year: Annotated[int, Field(ge=1, le=7)]


class ModeledExposure(Quantiles):
    unit: Literal["hours/year"]
    worst_contiguous_outage_hours: NonNegative
    by_year: list[AnnualExposure]
    source: Source


class Confidence(ContractModel):
    level: Literal["High", "Medium", "Low"]
    score: Fraction
    basis: NonEmpty
    source: Source


class Economics(ContractModel):
    gpus_per_mw: NonNegative
    lost_gpu_hours_per_year: Quantiles
    annual_cost_usd: Quantiles
    value_of_early_connection_usd: NonNegative
    # A zero interruptible load has no finite cost crossover.
    breakeven_exposure_hours_per_year: NonNegative | None
    decision: Literal["worth_it", "not_worth_it", "close_call"]
    source: Source


class TariffTrigger(ContractModel):
    text: NonEmpty
    observable: bool
    source: Source


class Tariff(ContractModel):
    operator: Literal["SPP"]
    service: NonEmpty
    curtailment_triggers: list[TariffTrigger]


class EstimateResponse(ContractModel):
    inputs_echo: EstimateRequest
    modeled_exposure: ModeledExposure
    confidence: Confidence
    economics: Economics
    tariff: Tariff

    @model_validator(mode="after")
    def complete_horizon(self) -> Self:
        years = [row.year for row in self.modeled_exposure.by_year]
        if years != list(range(1, self.inputs_echo.term_years + 1)):
            raise ValueError("Exposure rows must cover the complete ordered contract term")
        return self
