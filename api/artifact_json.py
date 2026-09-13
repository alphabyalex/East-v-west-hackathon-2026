"""Decode artifact JSON without silently discarding ambiguous or invalid values."""

import json
import math


def _unique_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"Non-finite JSON number: {value}")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        _reject_nonfinite(value)
    return number


def loads_artifact(contents: str) -> object:
    """Reject duplicate keys, NaN/Infinity, and finite literals overflowing float."""
    return json.loads(contents, object_pairs_hook=_unique_keys,
                      parse_constant=_reject_nonfinite, parse_float=_finite_float)
