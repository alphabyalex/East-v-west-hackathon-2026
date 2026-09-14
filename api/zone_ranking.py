from pathlib import Path
import logging
from typing import Annotated, Literal
from pydantic import Field, model_validator
from typing_extensions import Self

from .artifact_json import loads_artifact
from .schemas import ContractModel, Fraction, NonEmpty, NonNegative, Source

log = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent

AnnualHours = Annotated[float, Field(ge=0, le=8760)]


class ZoneRankingItem(ContractModel):
    location_id: NonEmpty
    avg_p50_risk_hours: AnnualHours
    avg_p90_risk_hours: AnnualHours
    avg_p99_risk_hours: AnnualHours
    avg_worst_contiguous_hours: AnnualHours
    wind_absorption_mwh_per_year: NonNegative
    carbon_absorbed_tonnes_per_year: NonNegative
    wind_source_ref: NonEmpty
    score_risk: Fraction
    score_wind: Fraction
    score_carbon: Fraction
    composite_score: Annotated[float, Field(ge=0, le=100)]
    rank: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def ordered_quantiles(self) -> Self:
        if not self.avg_p50_risk_hours <= self.avg_p90_risk_hours <= self.avg_p99_risk_hours:
            raise ValueError("Ranking risk hours must satisfy p50 <= p90 <= p99")
        return self


class ExcludedLocation(ContractModel):
    location_id: NonEmpty
    reasons: list[NonEmpty] = Field(min_length=1)
    source: Source


class RankingDatum(Source):
    value: NonNegative


class WindRankingEvidence(ContractModel):
    location_id: NonEmpty
    reference_location_id: NonEmpty
    period_start_utc: NonEmpty
    period_end_exclusive_utc: NonEmpty
    proxy_hours: RankingDatum
    evaluable_hours: RankingDatum
    unknown_hours: RankingDatum


class ZoneRankingsResponse(ContractModel):
    operator: Literal["SPP"]
    composite_weight_formula: NonEmpty
    description: NonEmpty
    rankings: list[ZoneRankingItem]
    status: Literal["available", "unavailable"] = "available"
    excluded_locations: list[ExcludedLocation] = Field(default_factory=list)
    available_wind_evidence: list[WindRankingEvidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def honest_availability(self) -> Self:
        if self.status == "available" and not self.rankings:
            raise ValueError("Available rankings require ranked locations")
        if self.status == "unavailable" and (self.rankings or not self.excluded_locations):
            raise ValueError("Unavailable rankings require explicit exclusions and no invented ranks")
        return self

    @model_validator(mode="after")
    def consistent_rankings(self) -> Self:
        if len({item.location_id for item in self.rankings}) != len(self.rankings):
            raise ValueError("Ranking locations must be unique")
        if [item.rank for item in self.rankings] != list(range(1, len(self.rankings) + 1)):
            raise ValueError("Ranks must be sequential starting at one")
        if any(left.composite_score < right.composite_score
               for left, right in zip(self.rankings, self.rankings[1:])):
            raise ValueError("Rankings must be sorted by descending composite score")
        return self


class ZoneRankingsError(Exception):
    """Signifies missing or malformed rankings data."""

def load_zone_rankings() -> ZoneRankingsResponse:
    """
    Loads and parses the precomputed SPP zone rankings from the JSON manifest.
    """
    path = ROOT_DIR / "data/processed/national_stack/zone_rankings.json"
    try:
        data = loads_artifact(path.read_text(encoding="utf-8"))
        return ZoneRankingsResponse.model_validate(data)
    except FileNotFoundError as error:
        raise ZoneRankingsError("Zone rankings JSON manifest is missing; provide a reviewed rankings artifact.") from error
    except OSError as error:
        log.exception("Cannot read zone rankings manifest")
        raise ZoneRankingsError("Zone rankings manifest could not be read") from error
    except ValueError as error:
        log.exception("Invalid zone rankings manifest")
        raise ZoneRankingsError("Malformed zone rankings schema; check the saved manifest") from error
