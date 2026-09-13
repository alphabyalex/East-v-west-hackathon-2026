"""Public adapter regressions for canonical interval identity and raw validity."""
from copy import deepcopy
import socket

import numpy as np
import pandas as pd
import pytest

from pipeline import wind_signal


ORIGIN = {"source_type": "assumption", "ref": "synthetic interval identity fixture; no measured data"}


@pytest.fixture
def wind(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("Interval identity tests cannot fetch, fit or load cached datasets")

    monkeypatch.setattr(socket.socket, "connect", reject)
    monkeypatch.setattr(socket, "create_connection", reject)
    monkeypatch.setattr(socket, "getaddrinfo", reject)
    monkeypatch.setattr(wind_signal.ingest, "fetch_public_evidence", reject)
    monkeypatch.setattr(wind_signal.ingest, "load_dataset", reject)
    monkeypatch.setattr(wind_signal, "fit_wind_event_classifier", reject)
    monkeypatch.setattr(wind_signal, "prepare_wind_classifier_features", reject)
    return wind_signal


def inputs():
    times = pd.date_range("2024-01-01", periods=2, freq="h", tz="UTC")
    generation = pd.DataFrame({"Interval Start": times,
                               "Interval End": times + pd.Timedelta(1, unit="h"),
                               "Wind": [1., 2.]})
    load = pd.DataFrame({"timestamp_utc": times, "location_id": "SPP_SYSTEM", "load_mw": 100.})
    prices = pd.DataFrame({"Interval Start": times,
                           "Interval End": times + pd.Timedelta(1, unit="h"),
                           "Market": "DA", "Location": "EXACT_NODE", "LMP": [-1., 2.]})
    for frame in (generation, load, prices):
        frame.attrs["source"] = deepcopy(ORIGIN)
    return [generation, load, prices]


def prepared(wind, frames):
    originals = [frame.copy(deep=True) for frame in frames]
    attrs = [deepcopy(frame.attrs) for frame in frames]
    try:
        return wind.prepare_wind_inputs(*frames, price_locations={"CSWS": "EXACT_NODE"}, market="DA")
    finally:
        for actual, original, origin in zip(frames, originals, attrs):
            pd.testing.assert_frame_equal(actual, original)
            assert actual.attrs == origin


def target(field):
    return (0, "Wind", "system_wind_mw") if field == "wind" else (2, "LMP", "lmp_usd_mwh")


@pytest.mark.parametrize("field", ["wind", "lmp"])
@pytest.mark.parametrize("invalid_first", [False, True], ids=["valid-first", "invalid-first"])
@pytest.mark.parametrize("good,bad,message", [
    pytest.param(1., True, "booleans", id="true-equals-one"),
    pytest.param(0., False, "booleans", id="false-equals-zero"),
    pytest.param(1., np.bool_(True), "booleans", id="numpy-boolean"),
    pytest.param(1., complex(1, 0), "complex, datetime or timedelta", id="zero-imaginary-complex"),
])
def test_invalid_equal_quantity_cannot_disappear_in_duplicate_reconciliation(
    wind, field, invalid_first, good, bad, message,
):
    frames = inputs()
    index, column, _ = target(field)
    frame = frames[index]
    frame[column] = frame[column].astype(object)
    frame.loc[0, column] = good
    duplicate = frame.iloc[[0]].copy()
    duplicate.loc[0, column] = bad
    frames[index] = pd.concat([frame, duplicate])
    if invalid_first:
        frames[index] = frames[index].iloc[::-1]
    with pytest.raises(ValueError, match=message):
        prepared(wind, frames)


@pytest.mark.parametrize("field", ["wind", "lmp"])
@pytest.mark.parametrize("representation", ["numeric-string", "explicit-offset"])
def test_equivalent_canonical_intervals_deduplicate_without_changing_other_hours(
    wind, field, representation,
):
    frames = inputs()
    expected = prepared(wind, frames)
    index, column, _ = target(field)
    duplicate = frames[index].iloc[[0]].copy()
    if representation == "numeric-string":
        duplicate[column] = duplicate[column].astype(object)
        duplicate.loc[0, column] = str(duplicate.loc[0, column])
    else:
        duplicate["Interval Start"] = pd.Series(["2023-12-31T18:00:00-06:00"], dtype=object)
        duplicate["Interval End"] = pd.Series(["2023-12-31T19:00:00-06:00"], dtype=object)
    # Repeated indices and reverse order catch alignment mistakes after deduplication.
    frames[index] = pd.concat([frames[index], duplicate]).iloc[::-1]
    pd.testing.assert_frame_equal(prepared(wind, frames), expected)


@pytest.mark.parametrize("field", ["wind", "lmp"])
@pytest.mark.parametrize("conflict", ["unknown-versus-zero", "unequal-value", "different-end"])
def test_canonical_reconciliation_still_rejects_actual_revisions(wind, field, conflict):
    frames = inputs()
    index, column, _ = target(field)
    frame = frames[index]
    frame[column] = frame[column].astype(object)
    frame.loc[0, column] = 0.
    duplicate = frame.iloc[[0]].copy()
    if conflict == "unknown-versus-zero":
        duplicate.loc[0, column] = pd.NA
    elif conflict == "unequal-value":
        duplicate.loc[0, column] = 1.
    else:
        duplicate.loc[0, "Interval End"] = pd.Timestamp("2024-01-01T00:05:00Z")
    frames[index] = pd.concat([duplicate, frame])
    with pytest.raises(ValueError, match="Conflicting"):
        prepared(wind, frames)


@pytest.mark.parametrize("field", ["wind", "lmp"])
def test_five_minute_coverage_and_unknown_values_are_not_filled_by_duplicates(wind, field):
    frames = inputs()
    index, column, output = target(field)
    times = pd.date_range("2024-01-01", periods=24, freq="5min", tz="UTC")
    observed = pd.DataFrame({"Interval Start": times,
                             "Interval End": times + pd.Timedelta(5, unit="min"), column: 0.})
    if field == "lmp":
        observed = observed.assign(Market="DA", Location="EXACT_NODE")
    observed.attrs["source"] = deepcopy(ORIGIN)
    frames[index] = pd.concat([observed, observed.iloc[[0, 12]]])
    assert prepared(wind, frames)[output].tolist() == [0., 0.]
    # Eleven distinct samples stay unknown even if a repeated interval fills row count.
    partial = observed.drop(index=0)
    frames[index] = pd.concat([partial, partial.iloc[[0]]])
    assert pd.isna(prepared(wind, frames)[output].iloc[0])
    # A complete hour with an unavailable quantity remains unknown as well.
    observed.loc[0, column] = np.nan
    frames[index] = pd.concat([observed, observed.iloc[[0]]])
    result = prepared(wind, frames)
    assert pd.isna(result[output].iloc[0])
    assert result[output].iloc[1] == 0.


def test_negative_wind_is_rejected_before_duplicate_hourly_averaging(wind):
    frames = inputs()
    frames[0].loc[0, "Wind"] = -1.
    frames[0] = pd.concat([frames[0], frames[0].iloc[[0]]])
    with pytest.raises(ValueError, match="Negative net wind"):
        prepared(wind, frames)


def test_overlapping_mixed_cadence_is_not_mistaken_for_identical_intervals(wind):
    frames = inputs()
    extra = frames[0].iloc[[0]].copy()
    extra.loc[0, "Interval Start"] = pd.Timestamp("2024-01-01T00:05:00Z")
    extra.loc[0, "Interval End"] = pd.Timestamp("2024-01-01T00:10:00Z")
    frames[0] = pd.concat([frames[0], extra])
    with pytest.raises(ValueError, match="mixed-cadence"):
        prepared(wind, frames)
