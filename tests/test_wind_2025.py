"""Explicit 2025 readers; generated observation fixtures remain assumptions."""
import hashlib
import io
import json
from pathlib import Path
import zipfile

import numpy as np
import pandas as pd
import pytest

from pipeline import wind_signal as wind
from pipeline.prepare import LOAD_AREAS, normalize_legacy_load


VER_URL = "https://portal.spp.org/file-browser-api/download/ver-curtailments?path=/2025/2025-VER-Curtailments-ANNUAL-ROLLUP.zip"
VER_MEMBER = "2025-VER-Curtailments-ANNUAL-ROLLUP.csv"
VER_COLUMNS = ["LocalIntervalEnding", "GMTIntervalEnding", *wind.VER_WIND_COLUMNS,
               "SolarRedispatchCurtailments", "SolarManualCurtailments", "SolarCurtailedForEnergy"]


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Cached archive readers must neither fetch nor extract ZIP files")
    monkeypatch.setattr(wind.ingest, "fetch_public_evidence", forbidden)
    monkeypatch.setattr(wind.ingest, "fetch_load_sample", forbidden)
    monkeypatch.setattr(zipfile.ZipFile, "extract", forbidden)
    monkeypatch.setattr(zipfile.ZipFile, "extractall", forbidden)


def evidence(tmp_path, monkeypatch, key, url, content, *, changes=None, manifest_changes=None):
    manifest = {"source_type": "assumption", "ref": url, "requested_url": url,
                "sha256": hashlib.sha256(content).hexdigest(), **(manifest_changes or {})}
    row = {"request_url": url, "content": content, "source_json": json.dumps(manifest), **(changes or {})}
    path = tmp_path / f"data/raw/spp/evidence/{key}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([row]).to_parquet(path, index=False)
    monkeypatch.setattr(wind, "ROOT", tmp_path)
    return path


def ver_rows(start="2025-01-01T06:00:00Z"):
    ends = pd.date_range(start, periods=13, freq="5min")[1:]
    return pd.DataFrame({"LocalIntervalEnding": ends.tz_convert("America/Chicago").strftime("%m/%d/%Y %H:%M:%S"),
                         "GMTIntervalEnding": ends.strftime("%m/%d/%Y %H:%M:%S"),
                         **{name: np.zeros(12) for name in VER_COLUMNS[2:]}})


def ver_cache(tmp_path, monkeypatch, *, rows=None, members=None, **kwargs):
    rows = ver_rows() if rows is None else rows
    members = [(VER_MEMBER, rows.to_csv(index=False))] if members is None else members
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in members:
            archive.writestr(name, content)
    return evidence(tmp_path, monkeypatch, "ver_curtailments_2025_rollup", VER_URL, stream.getvalue(), **kwargs)


def load_rows(month):
    start = pd.Timestamp(f"2025-{month:02d}-01", tz="America/Chicago")
    end = (start + pd.Timedelta(1, unit="h")).tz_convert("UTC")
    return pd.DataFrame({"MarketHour": [end.strftime("%m/%d/%Y %H:%M:%S")], **{name: [10.] for name in LOAD_AREAS}})


def load_cache(tmp_path, monkeypatch, *, replace_month=None, replacement=None, missing_month=None,
               changes=None, manifest_changes=None):
    paths = []
    for month in range(1, 13):
        if month == missing_month:
            continue
        raw = load_rows(month)
        raw.columns = ["MarketHour", *[" " + name for name in LOAD_AREAS]]
        content = raw.to_csv(index=False).encode()
        if month == replace_month and replacement is not None:
            content = replacement if isinstance(replacement, bytes) else replacement.to_csv(index=False).encode()
        url = f"https://portal.spp.org/file-browser-api/download/hourly-load?path=/2025/HOURLY_LOAD-2025{month:02d}.csv"
        paths.append(evidence(tmp_path, monkeypatch, f"hourly_load_2025_{month:02d}", url, content,
                              changes=changes if month == replace_month else None,
                              manifest_changes=manifest_changes if month == replace_month else None))
    return paths


def test_rollup_removes_only_exact_embedded_headers_and_records_it(tmp_path, monkeypatch):
    lines = ver_rows().to_csv(index=False).splitlines()
    content = "\n".join(lines[:7] + [lines[0]] + lines[7:] + [lines[0]]) + "\n"
    ver_cache(tmp_path, monkeypatch, members=[(VER_MEMBER, content)])
    raw, origin = wind.read_cached_wind_curtailment_archive(2025)
    assert len(raw) == 12
    assert set(raw.archive_member) == {VER_MEMBER}
    assert "headers removed=2" in origin["ref"]
    assert "does not guarantee complete annual coverage" in origin["ref"]
    assert origin["source_type"] == "assumption"
    result = wind.prepare_wind_curtailment_labels(raw, origin=origin, system_scope="SPP_SYSTEM",
        scope_source={"source_type": "assumption", "ref": "explicit test SPP footprint"})
    assert result.wind_curtailment_event.tolist() == [False]


def test_partial_header_repetition_is_not_dropped_as_an_observation(tmp_path, monkeypatch):
    lines = ver_rows().to_csv(index=False).splitlines()
    malformed = lines[0].replace("WindManualCurtailments", "0")
    ver_cache(tmp_path, monkeypatch, members=[(VER_MEMBER, "\n".join(lines + [malformed]) + "\n")])
    with pytest.raises(ValueError):
        wind.read_cached_wind_curtailment_archive(2025)


@pytest.mark.parametrize("names", [["2025/01/VER-Curtailments-20250101.csv"],
    ["../2025-VER-Curtailments-ANNUAL-ROLLUP.csv"], [VER_MEMBER, "extra.csv"], [VER_MEMBER, VER_MEMBER]])
def test_rollup_requires_one_exact_unique_member(tmp_path, monkeypatch, names):
    with pytest.warns(UserWarning) if len(set(names)) != len(names) else __import__('contextlib').nullcontext():
        ver_cache(tmp_path, monkeypatch, members=[(name, ver_rows().to_csv(index=False)) for name in names])
    with pytest.raises(ValueError, match="one exact CSV"):
        wind.read_cached_wind_curtailment_archive(2025)
    assert not (tmp_path / VER_MEMBER).exists()


@pytest.mark.parametrize("change", ["schema", "duplicate_header", "empty", "partial_interval", "numeric_timestamp", "prior_operating_year"])
def test_invalid_rollup_structure_or_calendar_is_rejected(tmp_path, monkeypatch, change):
    rows = ver_rows()
    if change == "schema":
        rows = rows.drop(columns="LocalIntervalEnding")
    elif change == "duplicate_header":
        rows = rows.rename(columns={"LocalIntervalEnding": "GMTIntervalEnding"})
    elif change == "empty":
        rows = rows.iloc[:0]
    elif change == "partial_interval":
        rows.loc[0, "GMTIntervalEnding"] = "2025-01-01T06:04:00Z"
    elif change == "numeric_timestamp":
        rows["GMTIntervalEnding"] = 1
    else:
        rows = ver_rows("2025-01-01T05:00:00Z")
    ver_cache(tmp_path, monkeypatch, rows=rows)
    with pytest.raises(ValueError):
        wind.read_cached_wind_curtailment_archive(2025)


def test_rollup_allows_utc_next_year_for_last_central_operating_hour(tmp_path, monkeypatch):
    ver_cache(tmp_path, monkeypatch, rows=ver_rows("2026-01-01T05:00:00Z"))
    frame, _ = wind.read_cached_wind_curtailment_archive(2025)
    assert len(frame) == 12


def test_rollup_rejects_timezone_dropping_before_accepting_operating_year(tmp_path, monkeypatch):
    rows = ver_rows()
    rows["GMTIntervalEnding"] += " CST"
    ver_cache(tmp_path, monkeypatch, rows=rows)
    with pytest.raises(ValueError, match="unrecognized timezone"):
        wind.read_cached_wind_curtailment_archive(2025)


@pytest.mark.parametrize("change", ["hash", "url", "manifest_url", "oversize"])
def test_rollup_cache_identity_and_size_are_checked(tmp_path, monkeypatch, change):
    kwargs = {"manifest_changes": {"sha256": "bad"}} if change == "hash" else {"changes": {"request_url": VER_URL.replace("2025", "2024")}} if change == "url" else {"manifest_changes": {"requested_url": "wrong"}} if change == "manifest_url" else {}
    ver_cache(tmp_path, monkeypatch, **kwargs)
    if change == "oversize":
        original = zipfile.ZipFile.getinfo
        def oversized(archive, name):
            info = original(archive, name)
            info.file_size = 10_000_001
            return info
        monkeypatch.setattr(zipfile.ZipFile, "getinfo", oversized)
    with pytest.raises(ValueError):
        wind.read_cached_wind_curtailment_archive(2025)


def test_monthly_reader_preserves_components_missingness_and_shared_normalizer(tmp_path, monkeypatch):
    first = load_rows(1)
    first.loc[0, "CSWS"] = np.nan
    load_cache(tmp_path, monkeypatch, replace_month=1, replacement=first)
    frame, origin = wind.read_cached_monthly_load()
    assert list(frame.columns) == ["MarketHour", *LOAD_AREAS]
    assert len(frame) == 12 and pd.isna(frame.CSWS.iloc[0])
    assert origin["source_type"] == "assumption"
    assert len(frame.attrs["monthly_sources"]) == 12
    assert all("cached_sha256=" in value["ref"] for value in frame.attrs["monthly_sources"].values())
    raw_path, output_path = tmp_path / "raw.parquet", tmp_path / "normalized.parquet"
    frame.to_parquet(raw_path, index=False)
    normalize_legacy_load(raw_path, output_path)
    normalized = pd.read_parquet(output_path)
    assert pd.isna(normalized.load_mw.iloc[0])
    assert normalized.load_mw.dropna().eq(170.).all()


def test_monthly_reader_retains_duplicates_and_conflicts_for_shared_normalizer(tmp_path, monkeypatch):
    row = load_rows(1)
    replacement = pd.concat([row, row, row.assign(CSWS=11.)], ignore_index=True)
    load_cache(tmp_path, monkeypatch, replace_month=1, replacement=replacement)
    frame, _ = wind.read_cached_monthly_load()
    assert len(frame) == 14
    raw_path, output_path = tmp_path / "raw.parquet", tmp_path / "normalized.parquet"
    frame.to_parquet(raw_path, index=False)
    report = normalize_legacy_load(raw_path, output_path)
    assert report["exact_duplicate_rows_removed"] == 1
    assert report["conflicting_hours_marked_unknown"] == 1
    assert pd.isna(pd.read_parquet(output_path).load_mw.iloc[0])


@pytest.mark.parametrize("component, expected_total", [(-5., 155.), (-160., 0.), (-200., None)])
def test_monthly_reader_preserves_signed_components_and_shared_system_total_validation(
    tmp_path, monkeypatch, component, expected_total,
):
    rows = load_rows(1)
    rows["CSWS"] = component
    load_cache(tmp_path, monkeypatch, replace_month=1, replacement=rows)
    frame, _ = wind.read_cached_monthly_load()
    assert frame.CSWS.iloc[0] == component
    assert frame.loc[0, [name for name in LOAD_AREAS if name != "CSWS"]].eq(10.).all()
    raw_path, output_path = tmp_path / "raw.parquet", tmp_path / "normalized.parquet"
    frame.to_parquet(raw_path, index=False)
    if expected_total is None:
        with pytest.raises(ValueError, match="load_mw cannot be negative"):
            normalize_legacy_load(raw_path, output_path)
    else:
        normalize_legacy_load(raw_path, output_path)
        normalized = pd.read_parquet(output_path)
        assert normalized.load_mw.iloc[0] == expected_total
        assert normalized.load_mw.dropna().iloc[1:].eq(170.).all()


@pytest.mark.parametrize("value", [True, False])
def test_monthly_reader_rejects_boolean_components_even_alongside_missing_values(tmp_path, monkeypatch, value):
    rows = pd.concat([load_rows(1), load_rows(1)], ignore_index=True)
    rows.loc[1, "MarketHour"] = "01/01/2025 08:00:00"
    rows["CSWS"] = pd.Series([value, None], dtype=object)
    load_cache(tmp_path, monkeypatch, replace_month=1, replacement=rows)
    with pytest.raises(ValueError, match="Monthly load components cannot be boolean"):
        wind.read_cached_monthly_load()


@pytest.mark.parametrize("month", [1, 7, 12])
def test_missing_month_is_not_fetched_or_silently_ignored(tmp_path, monkeypatch, month):
    load_cache(tmp_path, monkeypatch, missing_month=month)
    with pytest.raises(FileNotFoundError):
        wind.read_cached_monthly_load()


@pytest.mark.parametrize("change", ["missing_area", "extra_area", "duplicate_header", "blank_header", "empty", "wrong_month", "numeric_time", "fractional_hour", "infinite", "negative_infinite", "boolean", "boolean_false", "corrupt_string"])
def test_invalid_monthly_schema_and_observations_are_rejected(tmp_path, monkeypatch, change):
    rows = load_rows(1)
    if change == "missing_area":
        rows = rows.drop(columns="WR")
    elif change == "extra_area":
        rows["NEW_AREA"] = 10.
    elif change == "duplicate_header":
        rows = rows.rename(columns={"WR": " CSWS "})
    elif change == "blank_header":
        rows = rows.rename(columns={"WR": " "})
    elif change == "empty":
        rows = rows.iloc[:0]
    elif change == "wrong_month":
        rows = load_rows(2)
    elif change == "numeric_time":
        rows["MarketHour"] = 1
    elif change == "fractional_hour":
        rows.loc[0, "MarketHour"] = "2025-01-01T07:30:00Z"
    else:
        rows["CSWS"] = {"infinite": np.inf, "negative_infinite": -np.inf, "boolean": True,
                        "boolean_false": False, "corrupt_string": "not-a-load"}[change]
    load_cache(tmp_path, monkeypatch, replace_month=1, replacement=rows)
    with pytest.raises(ValueError):
        wind.read_cached_monthly_load()


@pytest.mark.parametrize("change", ["hash", "row_url", "manifest_url", "not_bytes"])
def test_monthly_cache_identity_is_exact(tmp_path, monkeypatch, change):
    kwargs = {"manifest_changes": {"sha256": "bad"}} if change == "hash" else {"changes": {"request_url": "wrong"}} if change == "row_url" else {"manifest_changes": {"requested_url": "wrong"}} if change == "manifest_url" else {"changes": {"content": "not CSV bytes"}}
    load_cache(tmp_path, monkeypatch, replace_month=1, **kwargs)
    with pytest.raises(ValueError):
        wind.read_cached_monthly_load()


def test_monthly_bounds_use_central_interval_starts_including_dst(tmp_path, monkeypatch):
    load_cache(tmp_path, monkeypatch)
    for month, ends in [(3, ["2025-03-09T08:00:00Z", "2025-03-09T09:00:00Z", "2025-04-01T05:00:00Z"]),
                        (11, ["2025-11-02T07:00:00Z", "2025-11-02T08:00:00Z", "2025-12-01T06:00:00Z"]),
                        (12, ["2026-01-01T06:00:00Z"])]:
        rows = pd.DataFrame({"MarketHour": ends, **{name: [10.] * len(ends) for name in LOAD_AREAS}})
        url = f"https://portal.spp.org/file-browser-api/download/hourly-load?path=/2025/HOURLY_LOAD-2025{month:02d}.csv"
        evidence(tmp_path, monkeypatch, f"hourly_load_2025_{month:02d}", url, rows.to_csv(index=False).encode())
    frame, _ = wind.read_cached_monthly_load()
    assert len(frame) == 16
    assert frame.MarketHour.iloc[-1] == "2026-01-01T06:00:00Z"


def test_monthly_load_rejects_timezone_dropping_before_accepting_operating_month(tmp_path, monkeypatch):
    rows = load_rows(1)
    rows["MarketHour"] += " CST"
    load_cache(tmp_path, monkeypatch, replace_month=1, replacement=rows)
    with pytest.raises(ValueError, match="unrecognized timezone.*explicit UTC offset"):
        wind.read_cached_monthly_load()


def test_monthly_load_keeps_explicit_offset_source_text_and_interval_membership(tmp_path, monkeypatch):
    rows = load_rows(1)
    utc_ends = pd.to_datetime(rows.MarketHour, utc=True)
    rows["MarketHour"] = utc_ends.dt.tz_convert("America/Chicago").map(lambda value: value.isoformat())
    load_cache(tmp_path, monkeypatch, replace_month=1, replacement=rows)
    result, _origin = wind.read_cached_monthly_load()
    assert result.MarketHour.iloc[0] == "2025-01-01T01:00:00-06:00"
    assert pd.to_datetime(result.MarketHour.iloc[0], utc=True) == utc_ends.iloc[0]


@pytest.mark.parametrize("year", [True, 2024, 2026, "2025", 2025.0])
def test_monthly_year_is_explicit_and_not_coerced(year):
    with pytest.raises(ValueError):
        wind.read_cached_monthly_load(year)


@pytest.mark.parametrize("duplicate", [False, True])
def test_generation_2025_exact_vintage_and_duplicate_header_guard(tmp_path, monkeypatch, duplicate):
    header = "GMT MKT Interval,Coal Market,Wind Market" if not duplicate else "GMT MKT Interval,Wind Market, Wind Market "
    content = (header + "\n2025-01-01T00:00:00Z,10,20\n").encode()
    url = "https://portal.spp.org/file-browser-api/download/generation-mix-historical?path=/GenMix_2025.csv"
    evidence(tmp_path, monkeypatch, "genmix_2025", url, content)
    if duplicate:
        with pytest.raises(ValueError, match="headers"):
            wind.read_cached_generation_archive(2025)
    else:
        frame, origin = wind.read_cached_generation_archive(2025)
        assert "Coal Market" in frame
        assert origin["source_type"] == "assumption"
        assert "GenMix_2025.csv" in origin["ref"]


MONTH_URL = "https://portal.spp.org/file-browser-api/download/ver-curtailments?path=/2025/12/VER-Curtailments-MONTHLY-202512.csv"


def ver_month_cache(tmp_path, monkeypatch, rows=None, **kwargs):
    rows = ver_rows("2025-12-01T06:00:00Z") if rows is None else rows
    return evidence(tmp_path, monkeypatch, "ver_curtailments_2025_12", MONTH_URL,
                    rows.to_csv(index=False).encode(), **kwargs)


def test_monthly_ver_missing_values_and_duplicate_rows_reach_shared_label_preparer(tmp_path, monkeypatch):
    rows = ver_rows("2025-12-01T06:00:00Z")
    rows.loc[0, "WindRedispatchCurtailments"] = np.nan
    rows = pd.concat([rows, rows.iloc[[1]]], ignore_index=True)
    ver_month_cache(tmp_path, monkeypatch, rows)
    raw, origin = wind.read_cached_wind_curtailment_month()
    assert len(raw) == 13 and pd.isna(raw.WindRedispatchCurtailments.iloc[0])
    assert origin["source_type"] == "assumption"
    assert "cached_sha256=" in origin["ref"]
    assert "separate December monthly supplement" in origin["ref"]
    assert "does not guarantee complete monthly coverage" in origin["ref"]
    result = wind.prepare_wind_curtailment_labels(raw, origin=origin, system_scope="SPP_SYSTEM",
        scope_source={"source_type": "assumption", "ref": "test SPP footprint"})
    assert result.observed_five_minute_samples.tolist() == [12]
    assert result.evaluable_five_minute_samples.tolist() == [11]
    assert result.wind_curtailment_event.isna().all()


@pytest.mark.parametrize("bad", ["conflicting_revision", "corrupt_category", "negative_category"])
def test_monthly_ver_does_not_silently_repair_category_values_or_conflicts(tmp_path, monkeypatch, bad):
    rows = ver_rows("2025-12-01T06:00:00Z")
    if bad == "conflicting_revision":
        rows = pd.concat([rows, rows.iloc[[0]].assign(WindRedispatchCurtailments=2.)], ignore_index=True)
    else:
        rows["WindRedispatchCurtailments"] = rows.WindRedispatchCurtailments.astype(object)
        rows.loc[0, "WindRedispatchCurtailments"] = "unknown-corrupt" if bad == "corrupt_category" else -1.
    ver_month_cache(tmp_path, monkeypatch, rows)
    raw, origin = wind.read_cached_wind_curtailment_month()
    assert len(raw) == len(rows)
    with pytest.raises(ValueError):
        wind.prepare_wind_curtailment_labels(raw, origin=origin, system_scope="SPP_SYSTEM",
            scope_source={"source_type": "assumption", "ref": "test SPP footprint"})


@pytest.mark.parametrize("change", ["wrong_month", "wrong_year", "empty", "missing_column", "extra_baa", "duplicate_header", "numeric_time", "missing_time", "off_interval"])
def test_monthly_ver_rejects_unreviewed_schema_or_invalid_timestamp_bounds(tmp_path, monkeypatch, change):
    rows = ver_rows("2025-12-01T06:00:00Z")
    if change == "wrong_month":
        rows = ver_rows("2025-12-01T05:00:00Z")
    elif change == "wrong_year":
        rows = ver_rows("2024-12-01T06:00:00Z")
    elif change == "empty":
        rows = rows.iloc[:0]
    elif change == "missing_column":
        rows = rows.drop(columns="LocalIntervalEnding")
    elif change == "extra_baa":
        rows["BAA"] = "SPP"
    elif change == "duplicate_header":
        rows = rows.rename(columns={"LocalIntervalEnding": "GMTIntervalEnding"})
    elif change == "numeric_time":
        rows["GMTIntervalEnding"] = 1
    elif change == "missing_time":
        rows.loc[0, "GMTIntervalEnding"] = None
    else:
        rows.loc[0, "GMTIntervalEnding"] = "2025-12-01T06:06:00Z"
    ver_month_cache(tmp_path, monkeypatch, rows)
    with pytest.raises(ValueError):
        wind.read_cached_wind_curtailment_month()


def test_monthly_ver_allows_january_utc_end_with_december_central_start(tmp_path, monkeypatch):
    ver_month_cache(tmp_path, monkeypatch, ver_rows("2026-01-01T05:00:00Z"))
    frame, _ = wind.read_cached_wind_curtailment_month()
    assert len(frame) == 12


def test_monthly_ver_rejects_timezone_dropping_before_accepting_operating_month(tmp_path, monkeypatch):
    rows = ver_rows("2025-12-01T06:00:00Z")
    rows["GMTIntervalEnding"] += " CST"
    ver_month_cache(tmp_path, monkeypatch, rows)
    with pytest.raises(ValueError, match="unrecognized timezone"):
        wind.read_cached_wind_curtailment_month()


@pytest.mark.parametrize("change", ["row_url", "manifest_url", "ref", "hash", "not_bytes", "oversize"])
def test_monthly_ver_cache_source_and_content_are_verified(tmp_path, monkeypatch, change):
    kwargs = {"changes": {"request_url": "wrong"}} if change == "row_url" else {"manifest_changes": {"requested_url": "wrong"}} if change == "manifest_url" else {"manifest_changes": {"ref": "wrong"}} if change == "ref" else {"manifest_changes": {"sha256": "wrong"}} if change == "hash" else {"changes": {"content": "not bytes" if change == "not_bytes" else b"x" * 2_000_001}}
    ver_month_cache(tmp_path, monkeypatch, **kwargs)
    with pytest.raises(ValueError):
        wind.read_cached_wind_curtailment_month()


@pytest.mark.parametrize("year,month", [(True, 12), (2025.0, 12), ("2025", 12), (2024, 12),
                                        (2025, True), (2025, 12.0), (2025, "12"), (2025, 11)])
def test_monthly_ver_reviewed_period_is_narrow_and_never_coerced(year, month):
    with pytest.raises(ValueError, match="only reviewed December 2025 and January/February 2026"):
        wind.read_cached_wind_curtailment_month(year, month)


def test_missing_monthly_ver_cache_remains_missing_without_fetch(tmp_path, monkeypatch):
    monkeypatch.setattr(wind, "ROOT", tmp_path)
    with pytest.raises(FileNotFoundError):
        wind.read_cached_wind_curtailment_month()


@pytest.fixture(params=["ver_archive", "ver_month", "generation", "day_ahead", "monthly_load"])
def metadata_reader(request, tmp_path, monkeypatch):
    """Valid cached observations for each independent source_json read boundary."""
    if request.param == "ver_archive":
        return [ver_cache(tmp_path, monkeypatch)], lambda: wind.read_cached_wind_curtailment_archive(2025)
    if request.param == "ver_month":
        return [ver_month_cache(tmp_path, monkeypatch)], wind.read_cached_wind_curtailment_month
    if request.param == "monthly_load":
        return load_cache(tmp_path, monkeypatch), wind.read_cached_monthly_load
    if request.param == "generation":
        url = "https://portal.spp.org/file-browser-api/download/generation-mix-historical?path=/GenMix_2025.csv"
        content = b"GMT MKT Interval,Coal Market,Wind Market\n2025-01-01T00:00:00Z,10,20\n"
        path = evidence(tmp_path, monkeypatch, "genmix_2025", url, content)
        return [path], lambda: wind.read_cached_generation_archive(2025)
    url = "https://portal.spp.org/file-browser-api/download/da-lmp-by-settlement-location?path=/2024/2024.zip"
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("2024/01/By_Day/DA-LMP-SL-202401010100.csv",
                         "GMTIntervalEnd,Settlement Location,Pnode,LMP\n01/01/2024 07:00:00,EXACT_NODE,EXACT_PNODE,-1\n")
    path = evidence(tmp_path, monkeypatch, "da_lmp_settlement_2024", url, stream.getvalue())
    return [path], lambda: wind.read_cached_day_ahead_prices(2024, settlement_locations=["EXACT_NODE"])


@pytest.mark.parametrize("key,earlier", [
    ("source_type", "assumption"),
    ("ref", "mock://earlier synthetic source"),
    ("requested_url", "https://example.invalid/wrong-archive"),
    ("sha256", "0" * 64),
])
def test_cache_metadata_duplicate_identity_keys_cannot_discard_earlier_evidence(metadata_reader, key, earlier):
    paths, read = metadata_reader
    assert read()[1]["source_type"] == "assumption"
    cached = pd.read_parquet(paths[0])
    manifest = json.loads(cached.loc[0, "source_json"])
    # A last-key-wins parser would accept this complete data source, hiding the
    # earlier assumption, conflicting URL or fingerprint before validation.
    manifest["source_type"] = "data"
    encoded = json.dumps(manifest)
    member = json.dumps(key) + ": " + json.dumps(manifest[key])
    cached.loc[0, "source_json"] = encoded.replace(member, json.dumps(key) + ": " + json.dumps(earlier) + ", " + member)
    cached.to_parquet(paths[0], index=False)
    before = {path: path.read_bytes() for path in paths}
    with pytest.raises(ValueError, match="Duplicate wind artifact JSON key"):
        read()
    assert all(path.read_bytes() == content for path, content in before.items())


@pytest.mark.parametrize("extra", [
    '"audit":{"value":1,"value":2}',
    *['"audit":{"value":' + literal + '}' for literal in ("NaN", "Infinity", "-Infinity", "1e400")],
])
def test_cache_metadata_rejects_nested_duplicates_and_nonfinite_literals(metadata_reader, extra):
    paths, read = metadata_reader
    assert read()[1]["source_type"] == "assumption"
    cached = pd.read_parquet(paths[0])
    cached.loc[0, "source_json"] = cached.loc[0, "source_json"][:-1] + "," + extra + "}"
    cached.to_parquet(paths[0], index=False)
    before = paths[0].read_bytes()
    with pytest.raises(ValueError, match="Duplicate|Nonfinite"):
        read()
    assert paths[0].read_bytes() == before


def test_valid_cache_metadata_whitespace_and_extra_finite_fields_preserve_results(metadata_reader):
    paths, read = metadata_reader
    expected_frame, expected_origin = read()
    for path in paths:
        cached = pd.read_parquet(path)
        manifest = json.loads(cached.loc[0, "source_json"])
        manifest["audit"] = {"finite_value": 1.25, "unrelated_ref": '{"external_format":"opaque citation"}'}
        cached.loc[0, "source_json"] = json.dumps(manifest, indent=4) + "\n"
        cached.to_parquet(path, index=False)
    before = {path: path.read_bytes() for path in paths}
    actual_frame, actual_origin = read()
    pd.testing.assert_frame_equal(actual_frame, expected_frame)
    assert actual_origin == expected_origin
    assert actual_origin["source_type"] == "assumption"
    assert all(path.read_bytes() == content for path, content in before.items())
