"""Reviewed Jan/Feb VER caches; synthetic observations stay assumptions."""

import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from pipeline import wind_signal as wind


VER_COLUMNS = [
    "LocalIntervalEnding", "GMTIntervalEnding", "WindRedispatchCurtailments",
    "WindManualCurtailments", "WindCurtailedForEnergy", "SolarRedispatchCurtailments",
    "SolarManualCurtailments", "SolarCurtailedForEnergy",
]
SCOPE = {"source_type": "assumption", "ref": "synthetic test SPP_SYSTEM footprint"}


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("The reviewed monthly VER reader must use its existing cache, never fetch")

    monkeypatch.setattr(wind.ingest, "fetch_public_evidence", forbidden)
    monkeypatch.setattr(wind.ingest, "fetch_load_sample", forbidden)


def ver_rows(month, *, start=None, hours=1):
    first = pd.Timestamp(start or f"2026-{month:02d}-01T06:00:00Z")
    ends = pd.date_range(first + pd.Timedelta(5, unit="min"), periods=12 * hours, freq="5min")
    return pd.DataFrame({
        "LocalIntervalEnding": ends.tz_convert("America/Chicago").strftime("%m/%d/%Y %H:%M:%S"),
        "GMTIntervalEnding": ends.strftime("%m/%d/%Y %H:%M:%S"),
        **{name: np.zeros(len(ends)) for name in VER_COLUMNS[2:]},
    })


def month_cache(tmp_path, monkeypatch, month, *, rows=None, content=None,
                row_changes=None, source_changes=None):
    if content is None:
        content = (ver_rows(month) if rows is None else rows).to_csv(index=False).encode()
    url = ("https://portal.spp.org/file-browser-api/download/ver-curtailments?path="
           f"/2026/{month:02d}/VER-Curtailments-MONTHLY-2026{month:02d}.csv")
    origin = {"source_type": "assumption", "ref": url, "requested_url": url,
              "sha256": hashlib.sha256(content).hexdigest(), **(source_changes or {})}
    record = {"request_url": url, "content": content, "source_json": json.dumps(origin),
              **(row_changes or {})}
    path = tmp_path / f"data/raw/spp/evidence/ver_curtailments_2026_{month:02d}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([record]).to_parquet(path, index=False)
    monkeypatch.setattr(wind, "ROOT", tmp_path)
    return path, content, url


@pytest.mark.parametrize("month", [1, 2])
def test_reviewed_month_reads_exact_cached_bytes_without_upgrading_source(tmp_path, monkeypatch, month):
    rows = ver_rows(month)
    path, content, url = month_cache(tmp_path, monkeypatch, month, rows=rows)
    before = path.read_bytes()

    actual, origin = wind.read_cached_wind_curtailment_month(2026, month)

    pd.testing.assert_frame_equal(actual.drop(columns="archive_member"), rows)
    assert actual.archive_member.eq(f"VER-Curtailments-MONTHLY-2026{month:02d}.csv").all()
    assert origin["source_type"] == "assumption"
    assert origin["ref"].startswith(f"{url}; cached_sha256={hashlib.sha256(content).hexdigest()};")
    assert "does not guarantee complete monthly coverage" in origin["ref"]
    assert "no category summation or gap filling" in origin["ref"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("month", [1, 2])
def test_first_and_last_central_intervals_belong_to_declared_month(tmp_path, monkeypatch, month):
    # The last UTC timestamp lies in the next month, but its five-minute
    # interval start is still in the declared Central operating month.
    following = pd.Timestamp(f"2026-{month + 1:02d}-01T06:00:00Z")
    rows = pd.concat([ver_rows(month), ver_rows(month, start=following - pd.Timedelta(1, unit="h"))],
                     ignore_index=True)
    month_cache(tmp_path, monkeypatch, month, rows=rows)
    actual, _ = wind.read_cached_wind_curtailment_month(2026, month)
    pd.testing.assert_frame_equal(actual.drop(columns="archive_member"), rows)
    assert pd.Timestamp(actual.GMTIntervalEnding.iloc[-1], tz="UTC") == following


@pytest.mark.parametrize("month,end", [
    (1, "2026-01-01T06:00:00Z"),  # Its start is still December 2025.
    (2, "2026-03-01T06:05:00Z"),  # Its start is already March 2026.
])
def test_adjacent_month_observations_are_rejected_by_interval_start(tmp_path, monkeypatch, month, end):
    rows = ver_rows(month)
    rows.loc[0, "GMTIntervalEnding"] = end
    month_cache(tmp_path, monkeypatch, month, rows=rows)
    with pytest.raises(ValueError, match="Central operating month"):
        wind.read_cached_wind_curtailment_month(2026, month)


@pytest.mark.parametrize("year,month", [
    (2026, 0), (2026, 3), (2026, 12), (2027, 1), (2025, 1),
    (True, 1), (2026.0, 1), ("2026", 1), (2026, True), (2026, 1.0), (2026, "1"),
])
def test_unreviewed_periods_and_coerced_types_fail_before_cache_access(monkeypatch, year, month):
    def no_read(*args, **kwargs):
        pytest.fail("Unreviewed periods must be rejected before cache access")

    monkeypatch.setattr(wind.pd, "read_parquet", no_read)
    with pytest.raises(ValueError, match="reviewed"):
        wind.read_cached_wind_curtailment_month(year, month)


@pytest.mark.parametrize("month", [1, 2])
def test_missing_reviewed_cache_is_not_fetched_or_reported_as_zero(tmp_path, monkeypatch, month):
    monkeypatch.setattr(wind, "ROOT", tmp_path)
    with pytest.raises(FileNotFoundError):
        wind.read_cached_wind_curtailment_month(2026, month)


@pytest.mark.parametrize("change", ["row_url", "requested_url", "ref", "sha256"])
def test_month_cache_cannot_borrow_another_month_identity_or_bad_hash(tmp_path, monkeypatch, change):
    wrong_url = ("https://portal.spp.org/file-browser-api/download/ver-curtailments?path="
                 "/2026/01/VER-Curtailments-MONTHLY-202601.csv")
    kwargs = ({"row_changes": {"request_url": wrong_url}} if change == "row_url" else
              {"source_changes": {change: "0" * 64 if change == "sha256" else wrong_url}})
    path, _, _ = month_cache(tmp_path, monkeypatch, 2, **kwargs)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="exact month|fingerprint"):
        wind.read_cached_wind_curtailment_month(2026, 2)
    assert path.read_bytes() == before


def test_missing_categories_and_solar_only_events_preserve_shared_label_knownness(tmp_path, monkeypatch):
    rows = ver_rows(1, hours=4)
    rows.loc[12, "WindManualCurtailments"] = np.nan
    # A known positive wind category establishes an interval event even when
    # another wind category is missing; no quantities need to be added.
    rows.loc[24, ["WindRedispatchCurtailments", "WindCurtailedForEnergy"]] = 2.5
    rows.loc[24, "WindManualCurtailments"] = np.nan
    rows.loc[36:47, VER_COLUMNS[5:]] = 9.0
    rows = pd.concat([rows, rows.iloc[[1]]], ignore_index=True)
    month_cache(tmp_path, monkeypatch, 1, rows=rows)
    raw, origin = wind.read_cached_wind_curtailment_month(2026, 1)
    pd.testing.assert_frame_equal(raw.drop(columns="archive_member"), rows)

    labels = wind.prepare_wind_curtailment_labels(raw, origin=origin, system_scope="SPP_SYSTEM",
                                                 scope_source=SCOPE)

    assert labels.observed_five_minute_samples.tolist() == [12, 12, 12, 12]
    assert labels.evaluable_five_minute_samples.tolist() == [12, 11, 12, 12]
    assert labels.wind_curtailment_event.iloc[0] == False
    assert pd.isna(labels.wind_curtailment_event.iloc[1])
    assert labels.wind_curtailment_event.iloc[2] == True
    assert labels.wind_curtailment_event.iloc[3] == False
    assert labels.attrs["source"]["source_type"] == "assumption"
    assert origin["ref"] in labels.attrs["source"]["ref"]


@pytest.mark.parametrize("bad", ["conflict", "negative", "infinite", "corrupt"])
def test_bad_categories_reach_shared_label_validation_without_silent_repair(tmp_path, monkeypatch, bad):
    rows = ver_rows(2)
    if bad == "conflict":
        rows = pd.concat([rows, rows.iloc[[0]].assign(WindRedispatchCurtailments=2.0)], ignore_index=True)
    elif bad == "corrupt":
        rows["WindRedispatchCurtailments"] = rows.WindRedispatchCurtailments.astype(object)
        rows.loc[0, "WindRedispatchCurtailments"] = "unreviewed-category-value"
    else:
        rows.loc[0, "WindRedispatchCurtailments"] = -1.0 if bad == "negative" else np.inf
    month_cache(tmp_path, monkeypatch, 2, rows=rows)
    raw, origin = wind.read_cached_wind_curtailment_month(2026, 2)
    assert len(raw) == len(rows)
    if bad == "corrupt":
        assert raw.WindRedispatchCurtailments.iloc[0] == "unreviewed-category-value"
    else:
        pd.testing.assert_frame_equal(raw.drop(columns="archive_member"), rows)
    with pytest.raises(ValueError, match="Conflicting|nonnegative finite|Unable to parse string"):
        wind.prepare_wind_curtailment_labels(raw, origin=origin, system_scope="SPP_SYSTEM",
                                             scope_source=SCOPE)


@pytest.mark.parametrize("change", ["baa", "extra_category", "missing_column", "duplicate_header",
                                   "extra_field", "short_record", "unterminated_quote"])
def test_unreviewed_columns_and_malformed_csv_are_rejected_before_projection(tmp_path, monkeypatch, change):
    rows = ver_rows(1)
    if change == "baa":
        rows["BAA"] = "SPP"
    elif change == "extra_category":
        rows["NewWindCategory"] = 0.0
    elif change == "missing_column":
        rows = rows.drop(columns="SolarManualCurtailments")
    elif change == "duplicate_header":
        rows = rows.rename(columns={"LocalIntervalEnding": "GMTIntervalEnding"})
    content = rows.to_csv(index=False)
    if change in {"extra_field", "short_record", "unterminated_quote"}:
        lines = content.splitlines()
        lines[1] = (lines[1] + ",discarded" if change == "extra_field" else
                    lines[1].rsplit(",", 1)[0] if change == "short_record" else '"unterminated')
        content = "\n".join(lines) + "\n"
    month_cache(tmp_path, monkeypatch, 1, content=content.encode())
    with pytest.raises(ValueError, match="eight-column|headers|fields|malformed"):
        wind.read_cached_wind_curtailment_month(2026, 1)


@pytest.mark.parametrize("change", ["timezone_abbreviation", "off_interval", "missing_time", "numeric_time"])
def test_reviewed_month_keeps_existing_strict_timestamp_guards(tmp_path, monkeypatch, change):
    rows = ver_rows(2)
    if change == "timezone_abbreviation":
        rows["GMTIntervalEnding"] += " CST"
    elif change == "off_interval":
        rows.loc[0, "GMTIntervalEnding"] = "2026-02-01T06:06:00Z"
    elif change == "missing_time":
        rows.loc[0, "GMTIntervalEnding"] = None
    else:
        rows["GMTIntervalEnding"] = 1
    month_cache(tmp_path, monkeypatch, 2, rows=rows)
    with pytest.raises(ValueError, match="unrecognized timezone|Central operating month|numeric"):
        wind.read_cached_wind_curtailment_month(2026, 2)
