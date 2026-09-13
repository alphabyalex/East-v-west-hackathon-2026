"""Cheap scenario arithmetic over precomputed location output, never simulation."""

from urllib.parse import urlencode

from .economics import build_economics
from .mock_provider import LocationProvider
from .pipeline_provider import PipelineDataError
from .schemas import EstimateRequest, EstimateResponse


QUANTILES = ("p50", "p90", "p99")


def _url_number(value: str | int | float) -> str:
    # Match URLSearchParams' readable integer values for normal scenario inputs.
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _query(values: dict) -> str:
    return urlencode({key: _url_number(value) for key, value in values.items()})


def build_estimate(request: EstimateRequest, provider: LocationProvider) -> EstimateResponse:
    location = provider(request.location_id)
    rows = location.by_year[: request.term_years]
    if [row.year_offset for row in rows] != list(range(1, request.term_years + 1)):
        raise PipelineDataError("Precomputed output does not cover the requested contract term")
    by_year = [
        {
            "year": row.year_offset,
            **{key: getattr(row, f"{key}_hours") * request.site_exposure for key in QUANTILES},
        }
        for row in rows
    ]
    # Means of annual marginal quantiles, matching the existing frontend mock.
    # A summed marginal-quantile path is not a quantile of total contract loss.
    # Divide before summing so finite nonnegative annual values cannot overflow
    # merely while taking their mean. Economic overflow is checked separately.
    summary = {key: sum(row[key] / len(by_year) for row in by_year) for key in QUANTILES}
    echo = request.model_dump()
    scenario_query = _query(echo)
    source = location.source.model_dump()
    source["ref"] += ("&" if "?" in source["ref"] else "?") + scenario_query

    return EstimateResponse.model_validate({
        "inputs_echo": echo,
        "modeled_exposure": {
            "unit": "hours/year",
            **summary,
            "worst_contiguous_outage_hours": max(row.worst_contiguous_hours for row in rows) * request.site_exposure,
            "by_year": by_year,
            "source": source,
        },
        "confidence": location.confidence,
        "economics": build_economics(request, summary, location.source),
        "tariff": location.tariff,
    })
