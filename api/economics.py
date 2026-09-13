"""Cheap economics from explicit, file-backed assumptions; no numeric fallbacks."""

import json
import math
import re
from datetime import date
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urlparse

from pydantic import Field, ValidationError, model_validator

from .schemas import ContractModel, Economics, EstimateRequest, NonEmpty, Source


ASSUMPTIONS_PATH = Path(__file__).resolve().parents[1] / "docs" / "ASSUMPTIONS.md"
QUANTILES = ("p50", "p90", "p99")
_BLOCK = re.compile(
    r"<!-- headroom:economics-assumptions:v1 -->\s*```json\s*\n(.*?)\n```",
    re.DOTALL,
)
_UNITS = {
    "gpu_rental_price_usd_per_hour": "USD/GPU-hour",
    "industrial_electricity_price_usd_per_mwh": "USD/MWh",
    "gpus_per_mw": "GPU/MW",
    "early_connection_years": "year",
    "early_margin_usd_per_mw_year": "USD/MW-year",
    "close_call_fraction": "fraction",
}


class ArithmeticRangeError(ValueError):
    """Finite scenario inputs exceed the finite arithmetic range."""


class AssumptionsError(ValueError):
    """The required assumptions file is absent or invalid; never use defaults."""


def _is_placeholder(ref: str) -> bool:
    return ref.lower().startswith("mock:") or "placeholder" in ref.lower()


class AssumptionValue(ContractModel):
    value: Annotated[float, Field(ge=0)]
    source_type: Literal["assumption", "data"]
    ref: NonEmpty
    unit: NonEmpty
    source_url: str | None
    retrieved_on: str | None
    low: Annotated[float, Field(ge=0)]
    high: Annotated[float, Field(ge=0)]

    @model_validator(mode="after")
    def valid_range_and_provenance(self) -> Self:
        if not self.low <= self.value <= self.high:
            raise ValueError("Assumption value must fall within its recorded range")
        if _is_placeholder(self.ref):
            if self.source_type != "assumption" or not self.ref.startswith("mock://") or "placeholder" not in self.ref.lower():
                raise ValueError("Placeholder inputs require explicit mock:// placeholder assumption provenance")
            if self.source_url is not None or self.retrieved_on is not None:
                raise ValueError("Placeholder inputs must not imply source retrieval")
        else:
            if self.source_url is None or self.retrieved_on is None:
                raise ValueError("Referenced inputs require source URL and retrieval date")
            parsed = urlparse(self.source_url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("Referenced inputs require an HTTPS source URL without credentials")
            date.fromisoformat(self.retrieved_on)
        return self


class EconomicsAssumptions(ContractModel):
    schema_version: Literal[1]
    status: Literal["placeholder", "mixed", "sourced"]
    gpu_rental_price_usd_per_hour: AssumptionValue
    industrial_electricity_price_usd_per_mwh: AssumptionValue
    gpus_per_mw: AssumptionValue
    early_connection_years: AssumptionValue
    early_margin_usd_per_mw_year: AssumptionValue
    close_call_fraction: AssumptionValue

    @model_validator(mode="after")
    def validate_policy(self) -> Self:
        for key, unit in _UNITS.items():
            if getattr(self, key).unit != unit:
                raise ValueError(f"{key} must use unit {unit}")
        if self.gpus_per_mw.value <= 0:
            raise ValueError("GPU density must be positive")
        if self.close_call_fraction.value >= 1:
            raise ValueError("Close-call tolerance must be less than one")
        placeholders = [_is_placeholder(getattr(self, key).ref) for key in _UNITS]
        expected_status = "placeholder" if all(placeholders) else "mixed" if any(placeholders) else "sourced"
        if self.status != expected_status:
            raise ValueError("Assumptions status must match its per-value provenance")
        return self


def _unique_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"Non-finite JSON number: {value}")


def load_assumptions(path: Path | None = None) -> EconomicsAssumptions:
    """Read the marked JSON block afresh so edits never leave cached silent defaults."""
    try:
        blocks = _BLOCK.findall((path or ASSUMPTIONS_PATH).read_text(encoding="utf-8-sig"))
        if len(blocks) != 1:
            raise ValueError("Expected exactly one marked economics assumptions JSON block")
        payload = json.loads(blocks[0], object_pairs_hook=_unique_keys, parse_constant=_reject_nonfinite)
        return EconomicsAssumptions.model_validate(payload)
    except (OSError, UnicodeError, ValueError, ValidationError) as error:
        raise AssumptionsError("Economics assumptions file is missing or invalid") from error


def build_economics(
    request: EstimateRequest,
    summary: dict[str, float],
    exposure_source: Source,
    *,
    assumptions_path: Path | None = None,
) -> Economics:
    local = load_assumptions(assumptions_path)
    interruptible_mw = request.load_mw * request.flexibility_split
    gpu_hours = {key: summary[key] * interruptible_mw * local.gpus_per_mw.value for key in QUANTILES}
    annual_cost = {key: gpu_hours[key] * local.gpu_rental_price_usd_per_hour.value for key in QUANTILES}
    benefit = min(local.early_connection_years.value, request.term_years) * request.load_mw * local.early_margin_usd_per_mw_year.value
    cost_per_hour = interruptible_mw * local.gpus_per_mw.value * local.gpu_rental_price_usd_per_hour.value
    denominator = request.term_years * cost_per_hour
    breakeven = None if cost_per_hour == 0 else benefit / denominator
    p50_term_cost = annual_cost["p50"] * request.term_years
    p90_term_cost = annual_cost["p90"] * request.term_years
    tolerance = local.close_call_fraction.value
    upper_threshold = benefit * (1 + tolerance)
    lower_threshold = benefit * (1 - tolerance)
    values = [*gpu_hours.values(), *annual_cost.values(), benefit, denominator,
              p50_term_cost, p90_term_cost, upper_threshold, lower_threshold]
    if breakeven is not None:
        values.append(breakeven)
    if not all(math.isfinite(value) for value in values):
        raise ArithmeticRangeError("Scenario inputs exceed the finite arithmetic range")

    decision = (
        "not_worth_it" if p50_term_cost > upper_threshold
        else "worth_it" if p90_term_cost < lower_threshold
        else "close_call"
    )
    placeholder_exposure = _is_placeholder(exposure_source.ref)
    is_mock = local.status != "sourced" or placeholder_exposure
    prefix = "mock://economics-placeholder/docs/ASSUMPTIONS.md" if is_mock else "docs/ASSUMPTIONS.md#machine-readable-assumptions"
    input_refs = "; ".join(
        f"{key}={getattr(local, key).value!r} [{getattr(local, key).ref}]"
        for key in _UNITS
    )
    ref = (
        f"{prefix}; status={local.status}; {input_refs}; exposure=[{exposure_source.ref}]; "
        "annual_cost_basis=lost GPU-hours valued at gross rental price; "
        "early_connection_basis=assumed operating margin, not gross revenue; "
        "electricity=informational, not applied"
    )
    if placeholder_exposure:
        ref += "; placeholder, pipeline not wired yet"

    return Economics.model_validate({
        "gpus_per_mw": local.gpus_per_mw.value,
        "lost_gpu_hours_per_year": gpu_hours,
        "annual_cost_usd": annual_cost,
        "value_of_early_connection_usd": benefit,
        "breakeven_exposure_hours_per_year": breakeven,
        "decision": decision,
        "source": {"source_type": "assumption", "ref": ref},
    })
