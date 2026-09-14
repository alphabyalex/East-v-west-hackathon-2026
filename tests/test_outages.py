"""Outage report timing and data-quality regressions; synthetic fixtures only."""
import datetime as dt
import io
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from pipeline.features import build_features
from pipeline.outages import FUEL_COLUMNS, OUTLOOK_COLUMNS, add_outlooks, load_outlook_year, parse_report, select_outlooks


def raw_report(day="2024-01-01", periods=48, mw=100.0):
    frame = pd.DataFrame({"Market Hour": pd.date_range(day+"T10:00Z", periods=periods, freq="h").strftime("%m/%d/%Y %H:%M:%S")})
    for name in FUEL_COLUMNS:
        frame[name] = 0.0
    frame["Natural Gas MW"] = mw
    frame["Outaged MW"] = mw
    return frame


def parse(frame=None, day="2024-01-01"):
    if frame is None:
        frame = raw_report(day)
    return parse_report(frame.to_csv(index=False).encode(), dt.date.fromisoformat(day), "https://spp.org/test-only.csv")


def test_market_hour_is_utc_end_and_reports_are_eligible_next_central_day():
    report = parse()
    assert report.timestamp_utc.iloc[0] == pd.Timestamp("2024-01-01T09:00Z")
    assert report.outage_eligible_after_utc.iloc[0] == pd.Timestamp("2024-01-02T06:00Z")
    selected = select_outlooks(report)
    assert selected.timestamp_utc.min() == pd.Timestamp("2024-01-02T06:00Z")
    assert (selected.outage_eligible_after_utc <= selected.timestamp_utc).all()


@pytest.mark.parametrize("day,eligible", [("2024-03-09", "2024-03-10T06:00Z"),
                                          ("2024-03-10", "2024-03-11T05:00Z"),
                                          ("2024-11-02", "2024-11-03T05:00Z"),
                                          ("2024-11-03", "2024-11-04T06:00Z")])
def test_eligibility_uses_calendar_days_across_dst(day, eligible):
    assert parse(day=day).outage_eligible_after_utc.iloc[0] == pd.Timestamp(eligible)


def test_latest_eligible_vintage_wins_without_using_future_reports():
    first, second = parse(), parse(raw_report("2024-01-02", mw=200), "2024-01-02")
    selected = select_outlooks(pd.concat([second, first])).set_index("timestamp_utc")
    assert selected.loc["2024-01-03T05:00Z", "outage_outlook_mw"] == 100
    assert selected.loc["2024-01-03T06:00Z", "outage_outlook_mw"] == 200


def test_missing_report_hours_are_not_forward_filled():
    report = parse().drop(index=24)
    selected = select_outlooks(report)
    assert pd.Timestamp("2024-01-02T09:00Z") not in set(selected.timestamp_utc)


def test_inconsistent_fuel_totals_are_quarantined_without_using_older_vintage():
    raw = raw_report("2024-01-02", mw=200)
    raw["Outaged MW"] += 130
    second = parse(raw, "2024-01-02")
    assert second[list(OUTLOOK_COLUMNS.values())].isna().all().all()
    selected = select_outlooks(pd.concat([parse(), second])).set_index("timestamp_utc")
    row = selected.loc["2024-01-03T06:00Z"]
    assert pd.isna(row.outage_outlook_mw)
    assert row.outage_quality_status == "fuel_total_mismatch"
    assert row.outage_report_date == "2024-01-02"


@pytest.mark.parametrize("failure", ["duplicate", "missing_column", "unknown_column", "negative", "infinite", "missing", "nonhourly", "out_of_horizon", "timezone", "empty"])
def test_malformed_reports_are_rejected(failure):
    raw = raw_report()
    if failure == "duplicate": raw = pd.concat([raw, raw.iloc[[0]]])
    elif failure == "missing_column": raw = raw.drop(columns="Wind MW")
    elif failure == "unknown_column": raw["Balancing Authority"] = "SPPW"
    elif failure in {"negative", "infinite", "missing"}: raw.loc[0, "Natural Gas MW"] = {"negative": -1, "infinite": np.inf, "missing": np.nan}[failure]
    elif failure == "nonhourly": raw.loc[0, "Market Hour"] = "01/01/2024 10:15:00"
    elif failure == "out_of_horizon": raw.loc[0, "Market Hour"] = "02/01/2024 10:00:00"
    elif failure == "timezone": raw.loc[0, "Market Hour"] += "-06:00"
    elif failure == "empty": raw = raw.iloc[:0]
    with pytest.raises(ValueError): parse(raw)


def test_duplicate_vintages_are_not_silently_deduplicated():
    with pytest.raises(ValueError): select_outlooks(pd.concat([parse(), parse()]))


def test_annual_zip_requires_all_report_dates():
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr("2024/01/Capacity-Gen-Outage-20240101.csv", raw_report().to_csv(index=False))
    with patch("pipeline.outages.fetch_public_evidence", return_value=(content.getvalue(), {"ref": "https://spp.org/fixture.zip"})):
        with pytest.raises(ValueError, match="each date"): load_outlook_year(2024)


def test_unknown_mapping_existing_columns_and_output_are_protected():
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        hourly, out = folder/"input.parquet", folder/"output.parquet"
        frame = pd.DataFrame({"timestamp_utc": pd.date_range("2024-01-02T00:00Z", periods=24, freq="h"), "location_id": "SPP_SYSTEM", "load_mw": 1000.0})
        frame.to_parquet(hourly, index=False)
        with patch("pipeline.outages.load_outlook_year", return_value=(parse(), {"year": 2024})):
            report = add_outlooks(hourly, out, start_year=2024, end_year=2024)
        result = pd.read_parquet(out)
        assert report["known_hours"] == 18
        assert report["unknown_hours"] == 6
        assert report["no_future_report_dates"]
        pd.testing.assert_frame_equal(result[frame.columns], frame)
        with pytest.raises(ValueError): add_outlooks(hourly, out)
        with pytest.raises(ValueError): add_outlooks(out, folder/"duplicate.parquet")
        frame["location_id"] = "SPP_WEST"
        frame.to_parquet(hourly, index=False)
        with pytest.raises(ValueError): add_outlooks(hourly, folder/"west.parquet")


def test_outlook_features_use_only_prior_hours():
    frame = pd.DataFrame({"timestamp_utc": pd.date_range("2024-01-01", periods=200, freq="h", tz="UTC"), "location_id": "SPP_SYSTEM", "load_mw": 1000.0, "target": 0})
    for column in OUTLOOK_COLUMNS.values(): frame[column] = np.arange(len(frame), dtype=float)
    before, names = build_features(frame, {"label_method": "observed_event"})
    cutoff = frame.timestamp_utc.iloc[100]
    frame.loc[frame.timestamp_utc >= cutoff, list(OUTLOOK_COLUMNS.values())] += 10000
    after, _ = build_features(frame, {"label_method": "observed_event"})
    pd.testing.assert_frame_equal(before[before.timestamp_utc <= cutoff], after[after.timestamp_utc <= cutoff])
    assert "outage_outlook_mw_lag_1h" in names
    assert not any(name.startswith("outage_report") for name in names)
