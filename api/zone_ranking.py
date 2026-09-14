from pathlib import Path
import json
import logging
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing_extensions import Self

from .schemas import ContractModel, NonEmpty, NonNegative, Source

log = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent

class ZoneRankingItem(ContractModel):
    location_id: NonEmpty
    avg_p50_risk_hours: NonNegative
    avg_p90_risk_hours: NonNegative
    avg_p99_risk_hours: NonNegative
    avg_worst_contiguous_hours: NonNegative
    wind_absorption_mwh_per_year: NonNegative
    carbon_absorbed_tonnes_per_year: NonNegative
    wind_source_ref: NonEmpty
    score_risk: NonNegative
    score_wind: NonNegative
    score_carbon: NonNegative
    composite_score: NonNegative
    rank: int

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

class ZoneRankingsError(Exception):
    """Signifies missing or malformed rankings data."""
    pass

def load_zone_rankings() -> ZoneRankingsResponse:
    """
    Loads and parses the precomputed SPP zone rankings from the JSON manifest.
    """
    path = ROOT_DIR / "data/processed/national_stack/zone_rankings.json"
    if not path.exists():
        raise ZoneRankingsError("Zone rankings JSON manifest is missing; run 'python -m pipeline.site_rank' first.")
    
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return ZoneRankingsResponse(**data)
    except Exception as error:
        raise ZoneRankingsError(f"Malformed zone rankings schema: {error}") from error
