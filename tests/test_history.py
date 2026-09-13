"""Authored fixtures for annual coverage and cache lineage; never model evidence."""
import json

import pandas as pd
import pytest

from pipeline import history, wind_signal
from pipeline.common import fingerprint, write_json
from pipeline.prepare import LOAD_AREAS


def year_frame(year):
    times = pd.date_range(f"{year}-01-01", f"{year+1}-01-01", freq="h", inclusive="left", tz="America/Chicago").tz_convert("UTC")
    return pd.DataFrame({"timestamp_utc": times, "location_id": "SPP_SYSTEM", "load_mw": 1000.})


@pytest.mark.parametrize("year,hours", [(2024, 8784), (2025, 8760)])
def test_annual_coverage_handles_leap_year_and_unknown_observations(year, hours):
    frame = year_frame(year)
    frame.loc[42, "load_mw"] = float("nan")
    assert history.validate_annual_load(frame, year) == {
        "expected_hours": hours, "known_load_hours": hours - 1, "unknown_load_hours": 1,
    }


@pytest.mark.parametrize("change", ["daily", "missing_boundary", "shifted_year", "duplicate", "other_location"])
def test_daily_or_misaligned_inputs_cannot_pass_as_annual(change):
    frame = year_frame(2025)
    if change == "daily":
        frame = frame.iloc[:24]
    elif change == "missing_boundary":
        frame = frame.iloc[:-1]
    elif change == "shifted_year":
        frame.timestamp_utc += pd.Timedelta(1, unit="h")
    elif change == "duplicate":
        frame = pd.concat([frame, frame.iloc[[-1]]])
    else:
        frame.loc[0, "location_id"] = "some-other-grid"
    with pytest.raises(ValueError, match="complete Central operating year"):
        history.validate_annual_load(frame, 2025)


def test_2025_uses_twelve_reviewed_months_and_detects_corrupted_combined_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "ROOT", tmp_path)
    raw = pd.DataFrame({"MarketHour": ["2025-01-01T07:00:00Z"], **{key: [1.] for key in LOAD_AREAS}})
    calls = []
    def fetch(url, key):
        calls.append(key)
        return b"authored test source", {"requested_url": url, "sha256": key, "source_type": "assumption"}
    monkeypatch.setattr(history, "fetch_public_evidence", fetch)
    monkeypatch.setattr(history, "fetch_load_sample", lambda *_: pytest.fail("A daily sample is not a year"))
    monkeypatch.setattr(wind_signal, "read_cached_monthly_load", lambda year: (raw.copy(), {"source_type": "assumption", "ref": "test"}))
    result, path = history.load_archive_year(2025)
    assert calls == [f"hourly_load_2025_{month:02d}" for month in range(1, 13)]
    pd.testing.assert_frame_equal(result, raw)
    digest = fingerprint(path)
    history.load_archive_year(2025)
    assert fingerprint(path) == digest
    raw.assign(CSWS=2.).to_parquet(path, index=False)
    with pytest.raises(ValueError, match="differs from its source evidence"):
        history.load_archive_year(2025)


def test_history_rejects_stale_prepared_cache_before_weather_work(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "ROOT", tmp_path)
    raw = tmp_path / "raw.parquet"
    raw.write_bytes(b"new source snapshot")
    prepared = tmp_path / "data/processed/ml_inputs/spp_2025_load_only.parquet"
    prepared.parent.mkdir(parents=True)
    year_frame(2025).to_parquet(prepared, index=False)
    write_json(prepared.with_suffix(".quality.json"), {"raw_sha256": "old snapshot"})
    monkeypatch.setattr(history, "load_archive_year", lambda _: (None, raw))
    monkeypatch.setattr(history, "add_temperature", lambda *args: pytest.fail("Stale data must not be joined"))
    with pytest.raises(ValueError, match="different raw observations"):
        history.prepare_history(2025, 2025, "test", tmp_path / "out.parquet")


def test_history_excludes_and_reports_only_post_2025_utc_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "ROOT", tmp_path)
    raw = tmp_path / "raw.parquet"
    raw.write_bytes(b"authored input")
    prepared = tmp_path / "data/processed/ml_inputs/spp_2025_load_only.parquet"
    prepared.parent.mkdir(parents=True)
    year_frame(2025).to_parquet(prepared, index=False)
    write_json(prepared.with_suffix(".quality.json"), {"raw_sha256": fingerprint(raw)})
    monkeypatch.setattr(history, "load_archive_year", lambda _: (None, raw))
    monkeypatch.setattr(history, "create_area_mapping", lambda *args, **kwargs: None)
    def add_weather(source, mapping, out):
        frame = pd.read_parquet(source)
        assert frame.timestamp_utc.max() == pd.Timestamp("2025-12-31T23:00Z")
        frame.to_parquet(out, index=False)
    monkeypatch.setattr(history, "add_temperature", add_weather)
    out = tmp_path / "history.parquet"
    history.prepare_history(2025, 2025, "test", out)
    report = json.loads(out.with_suffix(".quality.json").read_text())
    assert report["excluded_post_2025_utc_hours"] == 6
    assert report["hours"] == 8754
    assert report["sources"][0]["known_load_hours"] == 8760
