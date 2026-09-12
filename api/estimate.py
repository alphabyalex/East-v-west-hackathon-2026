"""Cheap scenario arithmetic over precomputed location output, never simulation."""

import math
from urllib.parse import urlencode

from .mock_provider import LocationProvider
from .schemas import EstimateRequest, EstimateResponse


# Round, explicitly mocked defaults shared with web/src/model/fixture.ts.
# Replace these from docs/ASSUMPTIONS.md when the team's sourcing lands on main.
MOCK_ECONOMICS = {
    "firm_wait_years": 3,
    "gpu_per_mw": 1000,
    "gpu_hour_value_usd": 2,
    "early_margin_usd_per_mw_year": 500000,
}
MOCK_CLOSE_CALL_FRACTION = 0.05
QUANTILES = ("p50", "p90", "p99")


class ArithmeticRangeError(ValueError):
    """Finite input is too large/small to produce a finite JSON response."""


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
    by_year = [
        {
            "year": row.year_offset,
            **{key: getattr(row, f"{key}_hours") * request.site_exposure for key in QUANTILES},
        }
        for row in rows
    ]
    # Means of annual marginal quantiles, matching the existing frontend mock.
    # A summed marginal-quantile path is not a quantile of total contract loss.
    summary = {key: sum(row[key] for row in by_year) / len(by_year) for key in QUANTILES}
    local = MOCK_ECONOMICS
    interruptible_mw = request.load_mw * request.flexibility_split
    gpu_hours = {key: summary[key] * interruptible_mw * local["gpu_per_mw"] for key in QUANTILES}
    annual_cost = {key: gpu_hours[key] * local["gpu_hour_value_usd"] for key in QUANTILES}
    benefit = min(local["firm_wait_years"], request.term_years) * request.load_mw * local["early_margin_usd_per_mw_year"]
    cost_per_hour = interruptible_mw * local["gpu_per_mw"] * local["gpu_hour_value_usd"]
    denominator = request.term_years * cost_per_hour
    breakeven = None if cost_per_hour == 0 else benefit / denominator
    p50_term_cost = annual_cost["p50"] * request.term_years
    p90_term_cost = annual_cost["p90"] * request.term_years
    values = [*gpu_hours.values(), *annual_cost.values(), benefit, denominator, p50_term_cost, p90_term_cost]
    if breakeven is not None:
        values.append(breakeven)
    if not all(math.isfinite(value) for value in values):
        raise ArithmeticRangeError("Scenario inputs exceed the finite arithmetic range")

    tolerance = MOCK_CLOSE_CALL_FRACTION
    decision = (
        "not_worth_it" if p50_term_cost > benefit * (1 + tolerance)
        else "worth_it" if p90_term_cost < benefit * (1 - tolerance)
        else "close_call"
    )
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
        "economics": {
            "gpus_per_mw": local["gpu_per_mw"],
            "lost_gpu_hours_per_year": gpu_hours,
            "annual_cost_usd": annual_cost,
            "value_of_early_connection_usd": benefit,
            "breakeven_exposure_hours_per_year": breakeven,
            "decision": decision,
            "source": {
                "source_type": "assumption",
                "ref": "mock://illustrative/economics-placeholder/api/estimate?" + _query({
                    **echo, **local, "pending": "docs/ASSUMPTIONS.md",
                }),
            },
        },
        "tariff": location.tariff,
    })
