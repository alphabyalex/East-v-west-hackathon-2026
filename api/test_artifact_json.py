"""Ambiguous metadata must not become model provenance."""

import pytest

from api.artifact_json import loads_artifact


@pytest.mark.parametrize("contents", [
    '{"score": 0, "score": 0.99}',
    '{"nested": {"score": 0, "score": 0.99}}',
    '{"rows": [{"score": 0, "score": 0.99}]}',
    '{"score": NaN}', '{"score": Infinity}', '{"score": -Infinity}',
    '{"unused": [1e9999]}', '{"unused": -1e9999}',
])
def test_rejects_duplicate_or_nonfinite_values_anywhere(contents):
    with pytest.raises(ValueError):
        loads_artifact(contents)


def test_accepts_finite_numbers_and_repeated_keys_in_separate_objects():
    assert loads_artifact('{"rows": [{"score": 0}, {"score": 1e-10}]}') == {
        "rows": [{"score": 0}, {"score": 1e-10}],
    }
