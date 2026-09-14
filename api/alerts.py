"""Static threshold evaluation, never a scheduler or a future-risk guarantee."""

from typing import Literal

from .schemas import ContractModel, EstimateResponse, Fraction, NonNegative, Source


class Thresholds(ContractModel):
    exposure_p90_hours_above: NonNegative | None = None
    confidence_score_below: Fraction | None = None


class ThresholdCheck(ContractModel):
    metric: Literal["exposure_p90_hours_above", "confidence_score_below"]
    threshold: float
    threshold_source: Source
    observed: float | None
    observed_source: Source | None
    status: Literal["breached", "within_threshold", "unavailable"]
    reason: str


class ThresholdStatus(ContractModel):
    mode: Literal["static_precomputed_check"] = "static_precomputed_check"
    status: Literal["not_configured", "breached", "within_thresholds", "unavailable"]
    checks: list[ThresholdCheck]


def supported_source(source: Source) -> bool:
    """Conservative: assumption-only or placeholder output cannot clear a check."""
    ref = source.ref.lower()
    return source.source_type in {"data", "model"} and not any(
        marker in ref for marker in ("mock:", "placeholder", "not wired")
    )


def evaluate_thresholds(thresholds: Thresholds, estimate: EstimateResponse | None,
                        unavailable_reason: str) -> ThresholdStatus:
    checks = []
    for metric, threshold in thresholds.model_dump().items():
        if threshold is None:
            continue
        block = None if estimate is None else (
            estimate.modeled_exposure if metric == "exposure_p90_hours_above" else estimate.confidence
        )
        available = block is not None and supported_source(block.source)
        observed = (block.p90 if metric == "exposure_p90_hours_above" else block.score) if available else None
        breached = available and (observed > threshold if metric == "exposure_p90_hours_above" else observed < threshold)
        checks.append(ThresholdCheck(
            metric=metric, threshold=threshold,
            threshold_source=Source(source_type="assumption", ref=f"user://portfolio/thresholds/{metric}"),
            observed=observed, observed_source=block.source if available else None,
            status="unavailable" if not available else "breached" if breached else "within_threshold",
            reason=("Static comparison with current precomputed data; equality does not breach."
                    if available else unavailable_reason or "Supported model/data evidence is unavailable; no check was cleared."),
        ))
    states = {check.status for check in checks}
    status = ("not_configured" if not checks else "breached" if "breached" in states else
              "unavailable" if "unavailable" in states else "within_thresholds")
    return ThresholdStatus(status=status, checks=checks)
