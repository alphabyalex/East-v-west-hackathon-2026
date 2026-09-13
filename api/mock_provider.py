"""Cached, authored fixture data; nothing here is a measurement or model result."""

from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
from typing import Callable

from .schemas import Confidence, EstimateResponse, Source, Tariff


FIXTURE_PATH = Path(__file__).resolve().parents[1] / "web/src/model/mock-response.json"
# These are the same illustrative SPP-region examples as web/src/model/fixture.ts.
# They are not verified pricing nodes or actual points of interconnection.
LOCATION_SCALES = {
    "spp-wichita-demo": 1.0,
    "spp-oklahoma-city-demo": 1.12,
    "spp-lincoln-demo": 0.9,
    # Canonical request example; support is placeholder-only, not node coverage.
    "SPP_SPS_HUB": 1.0,
    # Real input data is system-aggregate; these exposure values are still authored
    # placeholders until the matching precomputed system estimate is published.
    "SPP_SYSTEM": 1.0,
}


class LocationNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class BaselineYear:
    """Unscaled output, matching the pipeline's year-offset and hours fields."""

    year_offset: int
    p50_hours: float
    p90_hours: float
    p99_hours: float
    worst_contiguous_hours: float


@dataclass(frozen=True)
class LocationEstimate:
    by_year: tuple[BaselineYear, ...]
    confidence: Confidence
    source: Source
    tariff: Tariff


LocationProvider = Callable[[str], LocationEstimate]


@lru_cache(maxsize=1)
def load_fixture() -> EstimateResponse:
    """Read and validate the checked-in response once, never per HTTP request."""
    return EstimateResponse.model_validate(json.loads(FIXTURE_PATH.read_text(encoding="utf-8")))


def placeholder_tariff() -> Tariff:
    tariff = deepcopy(load_fixture().tariff)
    for trigger in tariff.curtailment_triggers:
        trigger.source = Source(
            source_type="assumption",
            ref="mock://placeholder/tariff; placeholder, tariff extraction not wired yet; no verified citation",
        )
    return tariff


def get_mock_location(location_id: str, *, reason: str = "precomputed pipeline reader unavailable") -> LocationEstimate:
    try:
        scale = LOCATION_SCALES[location_id]
    except KeyError as error:
        raise LocationNotFoundError(location_id) from error

    fixture = load_fixture()
    exposure = fixture.modeled_exposure
    fixture_factor = fixture.inputs_echo.site_exposure
    # The checked-in canonical example is already site-scaled. Recover its authored
    # baseline; decimal cleanup removes division noise, not modeled uncertainty.
    def baseline(value: float) -> float:
        return round(value / fixture_factor, 10)

    return LocationEstimate(
        by_year=tuple(
            BaselineYear(
                year_offset=row.year,
                p50_hours=baseline(row.p50) * scale,
                p90_hours=baseline(row.p90) * scale,
                p99_hours=baseline(row.p99) * scale,
                # Match the UI's round placeholder, independent of location scale.
                worst_contiguous_hours=baseline(exposure.worst_contiguous_outage_hours),
            )
            for row in exposure.by_year
        ),
        confidence=fixture.confidence.model_copy(update={"source": Source(
            source_type="assumption",
            ref=f"mock://placeholder/confidence; placeholder, pipeline not wired yet; {reason}; not computed by an ensemble",
        )}, deep=True),
        source=Source(
            source_type="assumption",
            ref=f"mock://placeholder/exposure; placeholder, pipeline not wired yet; {reason}",
        ),
        tariff=placeholder_tariff(),
    )
