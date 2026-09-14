"""Capacity-source normalization regressions; all report fixtures are synthetic."""
import datetime as dt
import hashlib
import io
import json
import zipfile
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from pipeline.capacity import FUEL_COLUMNS, load_capacity_year, normalize_reports, parse_report, prepare_capacity
from pipeline.common import OBSERVATIONS, fingerprint, read_hourly


def raw_report(day="2024-01-01", *, times=None):
    start = pd.Timestamp(day, tz="America/Chicago").tz_convert("UTC")
    end = pd.Timestamp(dt.date.fromisoformat(day) + dt.timedelta(days=1), tz="America/Chicago").tz_convert("UTC")
    if times is None:
        times = pd.date_range(start, end, freq="h", inclusive="left")
    result = pd.DataFrame({"GMT TIME": times.strftime("%Y-%m-%dT%H:%M:%SZ")})
    for index, column in enumerate(FUEL_COLUMNS):
        result[column] = str(index * 100.0)
    return result


def parse(raw=None, day="2024-01-01"):
    if raw is None:
        raw = raw_report(day)
    return parse_report(raw.to_csv(index=False).encode(), dt.date.fromisoformat(day), f"https://spp.org/fixture-{day}.csv")


def test_source_times_fuels_and_exact_report_hash_are_preserved():
    raw = raw_report()
    result = parse(raw)
    assert result.observation_timestamp_utc.iloc[0] == pd.Timestamp("2024-01-01T06:00Z")
    assert result.natural_gas_ecomax_mw.eq(400).all()
    assert set(result.columns).isdisjoint(OBSERVATIONS)
    evidence = json.loads(result.capacity_evidence_refs.iloc[0])
    assert evidence == [{"report_date": "2024-01-01", "ref": "https://spp.org/fixture-2024-01-01.csv",
                         "report_sha256": hashlib.sha256(raw.to_csv(index=False).encode()).hexdigest()}]


@pytest.mark.parametrize("value,expected", [("8,186", 8186), ("1,234,567.125", 1234567.125),
                                           (" 1,234.00 ", 1234), ("0", 0), ("2.3e3", 2300)])
def test_valid_numeric_formats_are_recovered(value, expected):
    raw = raw_report()
    raw.loc[0, "Natural Gas"] = value
    result = parse(raw)
    assert result.natural_gas_ecomax_mw.iloc[0] == expected
    assert result.attrs["formatted_numeric_cells"] == int("," in value)


@pytest.mark.parametrize("value", ["1,23", "12,34,567", "1234,567", "1,,234", "1,234,", "1.2,345",
                                   "", " ", "NA", "NaN", "inf", "-inf", "1e309", "-1", "False", "123 MW"])
def test_bad_numbers_are_rejected_without_coercion(value):
    raw = raw_report()
    raw.loc[0, "Natural Gas"] = value
    with pytest.raises(ValueError):
        parse(raw)


@pytest.mark.parametrize("failure", ["missing_column", "new_baa", "duplicate_header", "empty", "duplicate_time",
                                    "naive_time", "offset_time", "subhour", "other_day", "next_day", "blank_time"])
def test_changed_schemas_and_invalid_time_identity_are_rejected(failure):
    raw = raw_report()
    if failure == "missing_column": raw = raw.drop(columns="Wind")
    elif failure == "new_baa": raw["BAA"] = "SPP"
    elif failure == "duplicate_header": raw = pd.concat([raw, raw[["Wind"]]], axis=1)
    elif failure == "empty": raw = raw.iloc[:0]
    elif failure == "duplicate_time": raw = pd.concat([raw, raw.iloc[[0]]])
    else:
        raw.loc[0, "GMT TIME"] = {"naive_time": "2024-01-01T06:00:00", "offset_time": "2024-01-01T00:00:00-06:00",
                                   "subhour": "2024-01-01T06:05:00Z", "other_day": "2024-01-01T05:00:00Z",
                                   "next_day": "2024-01-02T06:00:00Z", "blank_time": ""}[failure]
    with pytest.raises(ValueError):
        parse(raw)


@pytest.mark.parametrize("day,hours", [("2024-03-10", 23), ("2024-11-03", 25), ("2020-02-29", 24)])
def test_dst_and_leap_days_preserve_each_distinct_utc_hour(day, hours):
    report = parse(day=day)
    assert len(report) == hours
    assert report.observation_timestamp_utc.is_unique


def test_old_spring_duplicate_is_collapsed_with_both_sources_retained():
    first = parse(day="2021-03-13")
    times = pd.date_range("2021-03-14T05:00Z", periods=24, freq="h")
    second = parse(raw_report("2021-03-14", times=times), "2021-03-14")
    result, summary = normalize_reports(pd.concat([second, first]), start_year=2021, end_year=2021)
    row = result.set_index("observation_timestamp_utc").loc["2021-03-14T05:00Z"]
    assert row.capacity_report_count == 2
    assert row.capacity_quality_status == "identical_duplicate"
    assert [ref["report_date"] for ref in json.loads(row.capacity_evidence_refs)] == ["2021-03-13", "2021-03-14"]
    assert summary["raw_rows"] == 48
    assert summary["known_hours"] == 47
    assert summary["identical_duplicate_hours"] == 1
    # Repeated pandas index labels cannot cause accidental overwrites.
    ordered, _ = normalize_reports(pd.concat([first, second], ignore_index=True), start_year=2021, end_year=2021)
    pd.testing.assert_frame_equal(result, ordered)


def test_conflicting_duplicates_stop_import_instead_of_silently_selecting_a_revision():
    first = parse(day="2021-03-13")
    raw = raw_report("2021-03-14", times=pd.date_range("2021-03-14T05:00Z", periods=24, freq="h"))
    raw.loc[0, "Natural Gas"] = "401"
    second = parse(raw, "2021-03-14")
    with pytest.raises(ValueError, match="Conflicting"):
        normalize_reports(pd.concat([first, second]), start_year=2021, end_year=2021)


def test_accidentally_loading_a_report_twice_is_rejected():
    frame = parse()
    with pytest.raises(ValueError, match="more than once"):
        normalize_reports(pd.concat([frame, frame]), start_year=2024, end_year=2024)


def test_clock_gaps_are_explicit_unknown_rows_without_interpolation():
    frame = parse().drop(index=3)
    normalized, summary = normalize_reports(frame, start_year=2024, end_year=2024)
    row = normalized.set_index("observation_timestamp_utc").loc["2024-01-01T09:00Z"]
    assert row[list(FUEL_COLUMNS.values())].isna().all()
    assert row.capacity_quality_status == "missing"
    assert row.capacity_evidence_refs == "[]"
    assert row.capacity_report_count == 0
    assert summary["known_hours"] == 23
    assert summary["clock_hours"] == 8784
    assert summary["missing_hours"] == 8761
    assert summary["training_features_created"] is False


def test_2025_central_year_does_not_leak_six_post_scope_utc_hours():
    result, report = normalize_reports(parse(day="2025-12-31"), start_year=2025, end_year=2025)
    assert report["source_unique_hours"] == 24
    assert report["excluded_post_2025_unique_hours"] == 6
    assert report["known_hours"] == 18
    assert report["clock_hours"] == 8754
    assert result.observation_timestamp_utc.max() == pd.Timestamp("2025-12-31T23:00Z")


@pytest.mark.parametrize("failure", ["naive_time", "missing_time", "subhour", "nan", "infinite", "negative", "outside_year"])
def test_invalid_parsed_data_cannot_bypass_observation_validation(failure):
    frame = parse()
    if failure == "naive_time": frame["observation_timestamp_utc"] = frame.observation_timestamp_utc.dt.tz_localize(None)
    elif failure == "missing_time": frame.loc[0, "observation_timestamp_utc"] = pd.NaT
    elif failure == "subhour": frame.loc[0, "observation_timestamp_utc"] += pd.Timedelta(30, unit="min")
    elif failure == "outside_year": frame.loc[0, "observation_timestamp_utc"] = pd.Timestamp("2025-02-01T00:00Z")
    else: frame.loc[0, "natural_gas_ecomax_mw"] = {"nan": np.nan, "infinite": np.inf, "negative": -1}[failure]
    with pytest.raises(ValueError):
        normalize_reports(frame, start_year=2024, end_year=2024)


@pytest.mark.parametrize("year", [2018, 2026, True, 2024.0, "2024"])
def test_unsupported_years_do_not_trigger_downloads(year):
    with patch("pipeline.capacity.fetch_public_evidence") as fetch:
        with pytest.raises(ValueError): load_capacity_year(year)
        fetch.assert_not_called()


def test_incomplete_annual_zip_is_rejected():
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr("2024/01/HRLY-GEN-CAP-BY-FUEL-TYPE-20240101.csv", raw_report().to_csv(index=False))
    with patch("pipeline.capacity.fetch_public_evidence", return_value=(content.getvalue(), {"ref": "https://spp.org/fixture.zip"})):
        with pytest.raises(ValueError, match="each date"): load_capacity_year(2024)


def test_2025_daily_loader_tracks_all_sources_and_does_not_request_missing_zip():
    def fetch(url, key):
        day = dt.datetime.strptime(key.removeprefix("generation_capacity_"), "%Y%m%d").date()
        data = raw_report(str(day)).to_csv(index=False).encode()
        return data, {"ref": url, "sha256": hashlib.sha256(data).hexdigest()}
    with patch("pipeline.capacity.fetch_public_evidence", side_effect=fetch) as fetcher:
        frame, summary = load_capacity_year(2025)
    assert fetcher.call_count == 365
    assert len(frame) == 8760
    assert summary["reports"] == len(summary["sources"]) == 365
    assert summary["incomplete_reports"] == []
    assert summary["formatted_numeric_cells"] == 0
    assert all(".csv" in call.args[0] for call in fetcher.call_args_list)


def test_preparation_writes_matching_manifest_and_cannot_be_used_as_training_input(tmp_path):
    out = tmp_path / "capacity.parquet"
    with patch("pipeline.capacity.load_capacity_year", return_value=(parse(), {"year": 2024})):
        summary = prepare_capacity(out, start_year=2024, end_year=2024)
    saved = json.loads(out.with_suffix(".capacity.json").read_text())
    assert saved == summary
    assert saved["output_sha256"] == fingerprint(out)
    assert len(pd.read_parquet(out)) == summary["clock_hours"]
    with pytest.raises(ValueError, match="missing columns"): read_hourly(out)
    with patch("pipeline.capacity.load_capacity_year") as loader:
        with pytest.raises(ValueError, match="preserved"): prepare_capacity(out)
        loader.assert_not_called()


def test_existing_sidecar_is_preserved_even_if_parquet_is_absent(tmp_path):
    out = tmp_path / "capacity.parquet"
    out.with_suffix(".capacity.json").write_text("{}")
    with patch("pipeline.capacity.load_capacity_year") as loader:
        with pytest.raises(ValueError, match="preserved"): prepare_capacity(out)
        loader.assert_not_called()
    assert not out.exists()
