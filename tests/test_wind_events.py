"""Independent VER event tests; every generated observation remains an assumption."""
from copy import deepcopy
import hashlib
import io
import json
import zipfile

import numpy as np
import pandas as pd
import pytest

import pipeline.wind_signal as wind


ORIGIN = {"source_type": "assumption", "ref": "synthetic VER observation fixture"}
SCOPE = {"source_type": "assumption", "ref": "synthetic historical SPP footprint declaration"}
WIND = ("WindRedispatchCurtailments", "WindManualCurtailments", "WindCurtailedForEnergy")
URL = "https://portal.spp.org/file-browser-api/download/ver-curtailments?path=/2024/2024.zip"
DAILY = "2024/01/VER-Curtailments-20240101.csv"


def observations(hours=1, start="2024-01-01T06:00:00Z"):
    ends = pd.date_range(start, periods=12 * hours + 1, freq="5min")[1:]
    return pd.DataFrame({"GMTIntervalEnding": ends.strftime("%m/%d/%Y %H:%M:%S"),
                         **{name: np.zeros(len(ends)) for name in WIND}})


def labels(frame, **changes):
    return wind.prepare_wind_curtailment_labels(frame, **{
        "origin": ORIGIN, "system_scope": "SPP_SYSTEM", "scope_source": SCOPE, **changes,
    })


@pytest.fixture(autouse=True)
def prohibit_network_and_extraction(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("An event reader must neither fetch data nor extract ZIP members")
    monkeypatch.setattr(wind.ingest, "fetch_public_evidence", forbidden)
    monkeypatch.setattr(zipfile.ZipFile, "extract", forbidden)
    monkeypatch.setattr(zipfile.ZipFile, "extractall", forbidden)


def cached_archive(tmp_path, monkeypatch, members=None, *, manifest_changes=None, row_changes=None):
    if members is None:
        members = [(DAILY, observations().to_csv(index=False))]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in members:
            archive.writestr(name, content)
    content = buffer.getvalue()
    manifest = {"source_type": "assumption", "ref": URL, "requested_url": URL,
                "sha256": hashlib.sha256(content).hexdigest(), **(manifest_changes or {})}
    row = {"request_url": URL, "content": content, "source_json": json.dumps(manifest), **(row_changes or {})}
    path = tmp_path / "data/raw/spp/evidence/ver_curtailments_2024.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([row]).to_parquet(path, index=False)
    monkeypatch.setattr(wind, "ROOT", tmp_path)
    return path, row


@pytest.mark.parametrize("category", WIND)
def test_each_independent_reported_category_can_establish_an_event(category):
    frame = observations()
    frame.loc[5, category] = 1.
    result = labels(frame)
    assert result.wind_curtailment_event.tolist() == [True]
    assert result.evaluable_five_minute_samples.tolist() == [12]
    assert result.attrs["source"]["source_type"] == "assumption"


def test_energy_subset_is_not_counted_twice_or_converted_into_absorbable_mwh():
    frame = observations()
    frame.loc[0, WIND[0]] = 10.
    first = labels(frame)
    frame.loc[0, WIND[2]] = 8.
    second = labels(frame)
    pd.testing.assert_frame_equal(first, second)
    assert second.wind_curtailment_event.tolist() == [True]
    assert not any("mwh" in name or "absorb" in name for name in second.columns)
    assert "not a site absorption opportunity" in second.attrs["limitation"]
    assert "recoverable-energy" in second.attrs["limitation"]


def test_observed_positive_with_another_missing_category_still_evaluates_interval():
    frame = observations()
    frame.loc[0, WIND[0]] = 1.
    frame.loc[0, WIND[1]] = np.nan
    result = labels(frame)
    assert result.wind_curtailment_event.tolist() == [True]
    assert result.evaluable_five_minute_samples.tolist() == [12]


def test_all_observed_zeros_are_negative_without_inventing_an_event():
    result = labels(observations())
    assert result.wind_curtailment_event.tolist() == [False]
    assert result.observed_five_minute_samples.tolist() == [12]


@pytest.mark.parametrize("bad", [1j, np.complex64(1j), pd.Timestamp(1, unit="ns"),
                                  np.datetime64(1, "ns"), pd.Timedelta(1, unit="ns"), np.timedelta64(1, "ns")])
@pytest.mark.parametrize("mixed", [False, True])
def test_nonreal_or_temporal_ver_quantities_cannot_become_evaluable_labels(bad, mixed):
    # Pure imaginary positives formerly lost their imaginary part and became a
    # fully evaluable false label; timestamps/durations became positive counts.
    values = pd.Series([bad] * 12) if not mixed else pd.Series(["0"] * 11 + [bad], dtype=object)
    for column in WIND:
        frame = observations()
        frame[column] = values
        original = frame.copy(deep=True)
        with pytest.raises(ValueError, match="complex, datetime or timedelta"):
            labels(frame)
        pd.testing.assert_frame_equal(frame, original)


@pytest.mark.parametrize("dtype", [object, "Float64", "Int64"])
def test_real_nullable_ver_values_remain_unknown_instead_of_complete_negatives(dtype):
    frame = observations()
    values = ["0"] * 11 + [pd.NA] if dtype is object else [0] * 11 + [pd.NA]
    frame[WIND[0]] = pd.Series(values, dtype=dtype)
    result = labels(frame)
    assert pd.isna(result.wind_curtailment_event.iloc[0])
    assert result.evaluable_five_minute_samples.tolist() == [11]


@pytest.mark.parametrize("missing", [np.nan, None, pd.NA])
def test_zero_with_any_missing_category_makes_complete_hour_unknown(missing):
    frame = observations()
    frame[WIND[2]] = frame[WIND[2]].astype(object)
    frame.loc[5, WIND[2]] = missing
    result = labels(frame)
    assert pd.isna(result.wind_curtailment_event.iloc[0])
    assert result.observed_five_minute_samples.tolist() == [12]
    assert result.evaluable_five_minute_samples.tolist() == [11]


def test_all_missing_categories_remain_unknown_not_zero():
    frame = observations()
    frame.loc[:, list(WIND)] = np.nan
    result = labels(frame)
    assert pd.isna(result.wind_curtailment_event.iloc[0])
    assert result.evaluable_five_minute_samples.tolist() == [0]


@pytest.mark.parametrize("has_positive", [False, True])
def test_partial_hour_is_unknown_even_when_a_reported_event_is_positive(has_positive):
    frame = observations().iloc[:-1].copy()
    if has_positive:
        frame.loc[0, WIND[0]] = 1.
    result = labels(frame)
    assert pd.isna(result.wind_curtailment_event.iloc[0])
    assert result.observed_five_minute_samples.tolist() == [11]


def test_wholly_missing_middle_hour_remains_on_the_hourly_grid():
    frame = observations(hours=3).drop(index=range(12, 24))
    result = labels(frame)
    assert result.observed_five_minute_samples.tolist() == [12, 0, 12]
    assert result.evaluable_five_minute_samples.tolist() == [12, 0, 12]
    assert pd.isna(result.wind_curtailment_event.iloc[1])
    assert result.wind_curtailment_event.iloc[0] == False
    assert result.wind_curtailment_event.iloc[2] == False


def test_identical_repeated_observations_are_deduplicated_without_filling_a_gap():
    frame = observations()
    duplicate = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)
    pd.testing.assert_frame_equal(labels(duplicate), labels(frame))
    gap_with_duplicate = pd.concat([frame.iloc[:-1], frame.iloc[[0]]], ignore_index=True)
    result = labels(gap_with_duplicate)
    assert result.observed_five_minute_samples.tolist() == [11]
    assert pd.isna(result.wind_curtailment_event.iloc[0])


def test_conflicting_revision_is_not_resolved_by_row_order():
    frame = observations()
    conflict = frame.iloc[[0]].copy()
    conflict[WIND[0]] = 3.
    for combined in (pd.concat([frame, conflict]), pd.concat([conflict, frame])):
        with pytest.raises(ValueError, match="Conflicting"):
            labels(combined)


@pytest.mark.parametrize("invalid", [-1., np.inf, -np.inf, True, np.bool_(False), "not numeric"])
@pytest.mark.parametrize("category", WIND)
def test_invalid_category_quantities_are_rejected_before_labeling(category, invalid):
    frame = observations()
    frame[category] = frame[category].astype(object)
    frame.loc[2, category] = invalid
    with pytest.raises(ValueError):
        labels(frame)


def test_gmt_end_at_hour_boundary_belongs_to_preceding_hour():
    frame = observations(hours=2)
    frame.loc[11, WIND[0]] = 1.  # GMT end 07:00 corresponds to start 06:55.
    result = labels(frame)
    assert result.timestamp_utc.tolist() == [pd.Timestamp("2024-01-01T06:00:00Z"),
                                           pd.Timestamp("2024-01-01T07:00:00Z")]
    assert result.wind_curtailment_event.tolist() == [True, False]


def test_utc_end_times_disambiguate_repeated_fall_dst_local_hour():
    frame = observations(hours=2, start="2024-11-03T06:00:00Z")
    frame["LocalIntervalEnding"] = "11/03/2024 01:05:00"
    frame.loc[12, WIND[1]] = 1.
    result = labels(frame)
    assert result.timestamp_utc.tolist() == [pd.Timestamp("2024-11-03T06:00:00Z"),
                                           pd.Timestamp("2024-11-03T07:00:00Z")]
    assert result.wind_curtailment_event.tolist() == [False, True]


@pytest.mark.parametrize("invalid", [None, "NaT", True, 1704092400, "2024-01-01T06:04:00Z", "not a time"])
def test_bad_or_unaligned_gmt_timestamps_are_rejected(invalid):
    frame = observations()
    frame.loc[0, "GMTIntervalEnding"] = invalid
    with pytest.raises(ValueError):
        labels(frame)


@pytest.mark.parametrize("zone", ["CST", "CDT", "PDT"])
def test_timezone_abbreviations_cannot_silently_move_a_complete_event_hour(zone):
    frame = observations(start="2024-01-01T00:00:00Z")
    frame["GMTIntervalEnding"] += " " + zone
    with pytest.raises(ValueError, match="unrecognized timezone.*explicit UTC offset"):
        labels(frame)


@pytest.mark.parametrize("representation", ["plain", "Z", "GMT", "offset", "mixed"])
def test_documented_gmt_and_explicit_offsets_keep_the_same_interval_membership(representation):
    frame = observations(hours=2)
    frame.loc[11, WIND[0]] = 1.
    expected = labels(frame)
    ends = pd.to_datetime(frame.GMTIntervalEnding, utc=True)
    if representation == "Z":
        frame["GMTIntervalEnding"] = ends.dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    elif representation == "GMT":
        frame["GMTIntervalEnding"] += " GMT"
    elif representation == "offset":
        frame["GMTIntervalEnding"] = ends.dt.tz_convert("America/Chicago").map(lambda value: value.isoformat())
    elif representation == "mixed":
        frame["GMTIntervalEnding"] = [value.tz_convert("America/Chicago").isoformat() if index % 2
                                       else value.isoformat() for index, value in enumerate(ends)]
    pd.testing.assert_frame_equal(labels(frame), expected)


@pytest.mark.parametrize("scope", [None, {}, {"source_type": "assumption", "ref": " "}])
def test_historical_rows_without_baa_require_a_sourced_footprint_declaration(scope):
    with pytest.raises(ValueError):
        labels(observations(), scope_source=scope)


@pytest.mark.parametrize("scope", ["SWPW", "SPP", "OKGE", None])
def test_system_target_cannot_be_relabeled_as_a_zone_or_other_footprint(scope):
    with pytest.raises(ValueError, match="SPP_SYSTEM"):
        labels(observations(), system_scope=scope)


def test_explicit_spp_selection_excludes_same_time_swpw_events():
    spp = observations().assign(BAA="SPP")
    west = observations().assign(BAA="SWPW")
    west.loc[:, list(WIND)] = 100.
    result = labels(pd.concat([west, spp], ignore_index=True))
    assert result.location_id.tolist() == ["SPP_SYSTEM"]
    assert result.observed_five_minute_samples.tolist() == [12]
    assert result.wind_curtailment_event.tolist() == [False]


@pytest.mark.parametrize("baa", ["SWPW", "spp", " SPP ", None])
def test_baa_matching_is_exact_and_does_not_guess_a_missing_scope(baa):
    with pytest.raises(ValueError):
        labels(observations().assign(BAA=baa))


def test_reordering_rows_columns_and_source_keys_is_canonical_and_does_not_mutate_input():
    frame = observations(hours=2)
    frame.loc[0, WIND[0]] = 1.
    saved = frame.copy(deep=True)
    sources = deepcopy((ORIGIN, SCOPE))
    expected = labels(frame)
    reordered = labels(frame.iloc[::-1, ::-1], origin=dict(reversed(list(ORIGIN.items()))),
                       scope_source=dict(reversed(list(SCOPE.items()))))
    pd.testing.assert_frame_equal(expected, reordered)
    assert json.dumps(expected.attrs, allow_nan=False) == json.dumps(reordered.attrs, allow_nan=False)
    pd.testing.assert_frame_equal(frame, saved)
    assert (ORIGIN, SCOPE) == sources
    assert ORIGIN["ref"] in expected.attrs["source"]["ref"]
    assert SCOPE["ref"] in expected.attrs["source"]["ref"]
    assert wind.VER_DESCRIPTION_REF in expected.attrs["source"]["ref"]


def test_assumed_scope_cannot_be_upgraded_by_an_observation_source():
    result = labels(observations(), origin={"source_type": "model", "ref": "synthetic model fixture"})
    assert result.attrs["source"]["source_type"] == "assumption"


def test_monthly_rollup_is_excluded_and_original_category_values_survive(tmp_path, monkeypatch):
    frame = observations()
    frame.loc[0, list(WIND)] = [10., 2., 8.]
    contradictory_month = observations()
    contradictory_month.loc[:, list(WIND)] = 900.
    cached_archive(tmp_path, monkeypatch, [(DAILY, frame.to_csv(index=False)),
        ("2024/01/VER-Curtailments-202401.csv", contradictory_month.to_csv(index=False))])
    raw, origin = wind.read_cached_wind_curtailment_archive(2024)
    assert len(raw) == 12
    assert raw.loc[0, list(WIND)].tolist() == [10., 2., 8.]
    assert raw.archive_member.unique().tolist() == [DAILY]
    assert origin["source_type"] == "assumption"
    assert URL in origin["ref"] and "cached_sha256=" in origin["ref"]
    assert labels(raw, origin=origin).wind_curtailment_event.tolist() == [True]


def test_reader_sorts_members_without_extracting_path_traversal_entries(tmp_path, monkeypatch):
    next_day = "2024/01/VER-Curtailments-20240102.csv"
    cached_archive(tmp_path, monkeypatch, [
        (next_day, observations(start="2024-01-02T06:00:00Z").to_csv(index=False)),
        ("../../must-not-exist.txt", "untrusted archive entry"),
        (DAILY, observations().to_csv(index=False)),
    ])
    raw, _ = wind.read_cached_wind_curtailment_archive(2024)
    assert raw.archive_member.unique().tolist() == [DAILY, next_day]
    assert not (tmp_path / "must-not-exist.txt").exists()


@pytest.mark.parametrize("member", [
    "2024/02/VER-Curtailments-20240101.csv",  # Month folder conflicts with filename.
    "2024/02/VER-Curtailments-20240230.csv",  # Impossible calendar date.
    "2023/01/VER-Curtailments-20230101.csv",  # Wrong annual archive.
    "2024/01/VER-Curtailments-202401.csv",   # Monthly data alone is not a daily source.
    "../2024/01/VER-Curtailments-20240101.csv",
])
def test_invalid_daily_members_never_become_sourced_observations(tmp_path, monkeypatch, member):
    cached_archive(tmp_path, monkeypatch, [(member, observations().to_csv(index=False))])
    with pytest.raises(ValueError):
        wind.read_cached_wind_curtailment_archive(2024)


def test_duplicate_archive_member_names_are_rejected(tmp_path, monkeypatch):
    csv = observations().to_csv(index=False)
    with pytest.warns(UserWarning, match="Duplicate name"):
        cached_archive(tmp_path, monkeypatch, [(DAILY, csv), (DAILY, csv)])
    with pytest.raises(ValueError, match="unambiguous"):
        wind.read_cached_wind_curtailment_archive(2024)


def test_empty_daily_csv_cannot_supply_evidence(tmp_path, monkeypatch):
    cached_archive(tmp_path, monkeypatch, [(DAILY, observations().iloc[:0].to_csv(index=False))])
    with pytest.raises(ValueError, match="no observations"):
        wind.read_cached_wind_curtailment_archive(2024)


@pytest.mark.parametrize("field", ["requested_url", "ref"])
def test_manifest_wrong_archive_url_is_rejected(tmp_path, monkeypatch, field):
    cached_archive(tmp_path, monkeypatch, manifest_changes={field: URL.replace("2024", "2023")})
    with pytest.raises(ValueError, match="archive URL"):
        wind.read_cached_wind_curtailment_archive(2024)


def test_cached_request_url_cannot_disagree_with_manifest(tmp_path, monkeypatch):
    cached_archive(tmp_path, monkeypatch, row_changes={"request_url": URL.replace("2024", "2023")})
    with pytest.raises(ValueError, match="archive URL"):
        wind.read_cached_wind_curtailment_archive(2024)


def test_changed_bytes_are_rejected_before_parsing_zip(tmp_path, monkeypatch):
    path, row = cached_archive(tmp_path, monkeypatch)
    row["content"] = b"not the fingerprinted archive"
    pd.DataFrame([row]).to_parquet(path, index=False)
    with pytest.raises(ValueError, match="fingerprint"):
        wind.read_cached_wind_curtailment_archive(2024)


def test_manifest_order_does_not_change_output_or_provenance(tmp_path, monkeypatch):
    path, row = cached_archive(tmp_path, monkeypatch)
    first, origin = wind.read_cached_wind_curtailment_archive(2024)
    row["source_json"] = json.dumps(dict(reversed(list(json.loads(row["source_json"]).items()))))
    pd.DataFrame([row]).to_parquet(path, index=False)
    second, second_origin = wind.read_cached_wind_curtailment_archive(2024)
    pd.testing.assert_frame_equal(first, second)
    assert json.dumps(origin) == json.dumps(second_origin)


def test_missing_archive_stays_missing_without_refetch(tmp_path, monkeypatch):
    monkeypatch.setattr(wind, "ROOT", tmp_path)
    with pytest.raises(FileNotFoundError):
        wind.read_cached_wind_curtailment_archive(2024)


@pytest.mark.parametrize("year", [True, 2013, 2026, "2024", 2024.0])
def test_unsupported_year_is_rejected_before_any_cache_read(monkeypatch, year):
    monkeypatch.setattr(pd, "read_parquet", lambda *a, **k: pytest.fail("Must validate year before reading"))
    with pytest.raises(ValueError):
        wind.read_cached_wind_curtailment_archive(year)


def test_archive_manifest_cannot_accept_multiple_records(tmp_path, monkeypatch):
    path, row = cached_archive(tmp_path, monkeypatch)
    pd.DataFrame([row, row]).to_parquet(path, index=False)
    with pytest.raises(ValueError, match="record"):
        wind.read_cached_wind_curtailment_archive(2024)


@pytest.mark.parametrize("content", ["not JSON", "null", "[]", '{"ref":"different source"}'])
def test_malformed_or_incomplete_manifest_cannot_be_used_as_evidence(tmp_path, monkeypatch, content):
    cached_archive(tmp_path, monkeypatch, row_changes={"source_json": content})
    with pytest.raises(ValueError):
        wind.read_cached_wind_curtailment_archive(2024)


def test_non_zip_bytes_with_valid_hash_are_not_treated_as_observations(tmp_path, monkeypatch):
    content = b"not a ZIP file"
    cached_archive(tmp_path, monkeypatch, row_changes={"content": content},
                   manifest_changes={"sha256": hashlib.sha256(content).hexdigest()})
    with pytest.raises(zipfile.BadZipFile):
        wind.read_cached_wind_curtailment_archive(2024)


def test_large_compressed_member_is_rejected_before_csv_allocation(tmp_path, monkeypatch):
    cached_archive(tmp_path, monkeypatch, [(DAILY, "x" * 10_000_001)])
    monkeypatch.setattr(pd, "read_csv", lambda *a, **k: pytest.fail("Must bound decompressed member before parsing"))
    with pytest.raises(ValueError, match="size"):
        wind.read_cached_wind_curtailment_archive(2024)


def test_daily_content_cannot_claim_a_different_operating_day_than_its_member(tmp_path, monkeypatch):
    wrong_day = observations(start="2024-01-02T06:00:00Z")
    cached_archive(tmp_path, monkeypatch, [(DAILY, wrong_day.to_csv(index=False))])
    with pytest.raises(ValueError):
        wind.read_cached_wind_curtailment_archive(2024)


def test_daily_reader_rejects_timezone_dropping_before_accepting_member_day(tmp_path, monkeypatch):
    frame = observations()
    frame["GMTIntervalEnding"] += " CST"
    cached_archive(tmp_path, monkeypatch, [(DAILY, frame.to_csv(index=False))])
    with pytest.raises(ValueError, match="unrecognized timezone"):
        wind.read_cached_wind_curtailment_archive(2024)


def test_duplicate_csv_headers_are_rejected_before_pandas_hides_second_category(tmp_path, monkeypatch):
    csv = ("GMTIntervalEnding,WindRedispatchCurtailments,WindRedispatchCurtailments,"
           "WindManualCurtailments,WindCurtailedForEnergy\n"
           "01/01/2024 06:05:00,0,100,0,0\n")
    cached_archive(tmp_path, monkeypatch, [(DAILY, csv)])
    with pytest.raises(ValueError):
        wind.read_cached_wind_curtailment_archive(2024)


def test_last_interval_ending_at_next_local_midnight_belongs_to_member_day(tmp_path, monkeypatch):
    final_hour = observations(start="2024-01-02T05:00:00Z")
    cached_archive(tmp_path, monkeypatch, [(DAILY, final_hour.to_csv(index=False))])
    raw, origin = wind.read_cached_wind_curtailment_archive(2024)
    result = labels(raw, origin=origin)
    assert result.timestamp_utc.tolist() == [pd.Timestamp("2024-01-02T05:00:00Z")]
    assert result.wind_curtailment_event.tolist() == [False]
