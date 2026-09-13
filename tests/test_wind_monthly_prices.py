"""Hermetic monthly DA price fixtures, with explicitly inferred time mapping."""
import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from pipeline import wind_signal as wind


HOURS = [f"HE{hour:02d}" for hour in range(1, 25)]
COLUMNS = ["Date", "Settlement Location Name", "PNODE Name", "Price Type", *HOURS]


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Monthly price preparation must not fetch data")
    monkeypatch.setattr(wind.ingest, "fetch_public_evidence", forbidden)
    monkeypatch.setattr(wind.ingest, "fetch_chunk", forbidden)


def month_bounds(month):
    first = pd.Timestamp(year=2025, month=month, day=1)
    next_date = first + pd.offsets.MonthBegin()
    return (first, next_date, first.tz_localize("America/Chicago").tz_convert("UTC"),
            next_date.tz_localize("America/Chicago").tz_convert("UTC"))


def rows(month=1, *, location="EXACT_NODE", pnode="EXACT_PNODE"):
    first, next_date, start, end = month_bounds(month)
    result = []
    for day in pd.date_range(first, next_date, freq="D"):
        record = {"Date": day.strftime("%Y/%m/%d"), "Settlement Location Name": location,
                  "PNODE Name": pnode, "Price Type": "LMP"}
        for index, hour in enumerate(HOURS):
            instant = day.tz_localize("UTC") + pd.Timedelta(index, unit="h")
            record[hour] = (instant - start) / pd.Timedelta(1, unit="h") - 20.5 if start <= instant < end else None
        result.append(record)
    return pd.DataFrame(result, columns=COLUMNS)


def write_cache(tmp_path, monkeypatch, *, month=1, frame=None, content=None,
                metadata_changes=None, row_changes=None):
    if content is None:
        frame = rows(month) if frame is None else frame
        # SPP's actual headers contain inconsistent spaces; only headers trim.
        frame = frame.rename(columns={name: " " + name for name in COLUMNS[1:5]})
        content = frame.to_csv(index=False).encode()
    url = ("https://portal.spp.org/file-browser-api/download/da-lmp-by-settlement-location"
           f"?path=/2025/{month:02d}/DA-LMP-MONTHLY-SL-2025{month:02d}.csv")
    manifest = {"source_type": "assumption", "ref": url, "requested_url": url,
                "sha256": hashlib.sha256(content).hexdigest(), **(metadata_changes or {})}
    record = {"request_url": url, "content": content, "source_json": json.dumps(manifest), **(row_changes or {})}
    path = tmp_path / f"data/raw/spp/evidence/da_lmp_settlement_2025_{month:02d}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([record]).to_parquet(path, index=False)
    monkeypatch.setattr(wind, "ROOT", tmp_path)
    return path, record, manifest


def read(month=1, locations=None):
    return wind.read_cached_day_ahead_price_month(month=month,
        settlement_locations=["EXACT_NODE"] if locations is None else locations)


@pytest.mark.parametrize("month,expected_hours,padding", [(1, 744, 24), (3, 743, 25), (11, 721, 23), (12, 744, 24)])
def test_complete_operating_month_utc_grid_dst_and_year_spill(tmp_path, monkeypatch, month, expected_hours, padding):
    write_cache(tmp_path, monkeypatch, month=month)
    frame, origin = read(month)
    _, _, start, end = month_bounds(month)
    assert frame.columns.tolist() == ["Interval Start", "Interval End", "Market", "Location", "Pnode", "LMP"]
    assert frame["Interval Start"].tolist() == list(pd.date_range(start, end, inclusive="left", freq="h"))
    assert len(frame) == expected_hours
    assert frame["Interval End"].iloc[-1] == end
    assert (frame["Interval End"] - frame["Interval Start"]).eq(pd.Timedelta(1, unit="h")).all()
    assert frame.LMP.tolist() == [hour - 20.5 for hour in range(expected_hours)]
    assert frame.Market.eq("DAY_AHEAD_HOURLY").all()
    assert frame.Pnode.eq("EXACT_PNODE").all()
    assert f"null boundary padding discarded={padding}" in origin["ref"]
    if month in (3, 11):
        transition = "2025-03-09" if month == 3 else "2025-11-02"
        local = frame["Interval Start"].dt.tz_convert("America/Chicago")
        assert local.dt.strftime("%Y-%m-%d").eq(transition).sum() == (23 if month == 3 else 25)
    if month == 12:
        assert frame["Interval Start"].iloc[-1] == pd.Timestamp("2026-01-01T05:00Z")


def test_exact_lmp_type_and_location_selection_never_substitutes_components(tmp_path, monkeypatch):
    selected = rows()
    irrelevant = pd.concat([rows(location="OTHER"), selected.assign(**{"Price Type": "MCC"}),
                            selected.assign(**{"Price Type": "MLC"})], ignore_index=True)
    irrelevant[HOURS] = "not a selected LMP"
    write_cache(tmp_path, monkeypatch, frame=pd.concat([irrelevant, selected], ignore_index=True))
    frame, _ = read()
    assert frame.Location.unique().tolist() == ["EXACT_NODE"]
    assert frame.LMP.tolist() == [hour - 20.5 for hour in range(744)]


@pytest.mark.parametrize("price_type", ["MCC", "MLC", "lmp", " LMP"])
def test_missing_exact_lmp_rows_are_not_replaced_by_other_price_types(tmp_path, monkeypatch, price_type):
    write_cache(tmp_path, monkeypatch, frame=rows().assign(**{"Price Type": price_type}))
    with pytest.raises(ValueError, match="No cached LMP observations"):
        read()


def test_missing_interior_prices_and_absent_dates_are_not_filled(tmp_path, monkeypatch):
    raw = rows().drop(index=2)
    raw.loc[1, "HE10"] = None
    write_cache(tmp_path, monkeypatch, frame=raw)
    frame, origin = read()
    assert len(frame) == 720
    missing = frame["Interval Start"].eq(pd.Timestamp("2025-01-02T09:00Z"))
    assert missing.sum() == 1 and frame.loc[missing, "LMP"].isna().all()
    assert frame.LMP.isna().sum() == 1
    assert not frame["Interval Start"].dt.strftime("%Y-%m-%d").eq("2025-01-03").any()
    assert "no gap filling" in origin["ref"]


def test_explicit_all_missing_month_stays_unknown_not_zero(tmp_path, monkeypatch):
    raw = rows()
    raw[HOURS] = None
    write_cache(tmp_path, monkeypatch, frame=raw)
    frame, _ = read()
    assert len(frame) == 744 and frame.LMP.isna().all()


@pytest.mark.parametrize("month,retained_unknown_hours", [(1, 6), (3, 5), (11, 6), (12, 6)])
def test_mixed_location_final_date_keeps_genuine_in_period_unknowns(tmp_path, monkeypatch, month, retained_unknown_hours):
    complete = rows(month)
    final = rows(month, location="UNKNOWN_FINAL", pnode="UNKNOWN_PNODE").iloc[[-1]].copy()
    final[HOURS] = np.nan
    write_cache(tmp_path, monkeypatch, month=month, frame=pd.concat([complete, final], ignore_index=True))
    frame, _ = read(month, locations=["EXACT_NODE", "UNKNOWN_FINAL"])
    unknown = frame.loc[frame.Location.eq("UNKNOWN_FINAL")]
    assert len(unknown) == retained_unknown_hours and unknown.LMP.isna().all()
    assert unknown.Pnode.eq("UNKNOWN_PNODE").all()
    assert unknown["Interval End"].iloc[-1] == month_bounds(month)[3]
    assert frame.loc[frame.Location.eq("EXACT_NODE"), "LMP"].notna().all()


@pytest.mark.parametrize("boundary", ["first", "last"])
@pytest.mark.parametrize("value", [0., -5., 20.])
def test_nonnull_boundary_padding_is_rejected_including_zero_and_negative(tmp_path, monkeypatch, boundary, value):
    raw = rows()
    raw.loc[0 if boundary == "first" else raw.index[-1], "HE01" if boundary == "first" else "HE07"] = value
    write_cache(tmp_path, monkeypatch, frame=raw)
    with pytest.raises(ValueError, match="outside the declared operating month"):
        read()


@pytest.mark.parametrize("value", [True, False, "True", "False", "60+5j", "2025-01-01", "1 day", "corrupt", "NaN", np.inf, -np.inf, "1e400"])
def test_invalid_csv_price_quantities_are_not_coerced_to_plausible_real_prices(tmp_path, monkeypatch, value):
    raw = rows().astype({"HE07": object})
    raw.loc[0, "HE07"] = value
    write_cache(tmp_path, monkeypatch, frame=raw)
    with pytest.raises((ValueError, TypeError)):
        read()


def test_numeric_strings_nullable_prices_and_na_named_identities_keep_meaning(tmp_path, monkeypatch):
    raw = rows(location="NA", pnode="null").astype({"HE07": object})
    raw.loc[0, "HE07"] = " -1.25 "
    raw.loc[1, "HE07"] = pd.NA
    write_cache(tmp_path, monkeypatch, frame=raw)
    frame, _ = read(locations=["NA"])
    assert frame.Location.eq("NA").all() and frame.Pnode.eq("null").all()
    assert frame.LMP.iloc[0] == -1.25 and frame.LMP.isna().sum() == 1


@pytest.mark.parametrize("source_type", ["assumption", "data"])
def test_inferred_time_mapping_never_inherits_raw_data_status(tmp_path, monkeypatch, source_type):
    # "data" exercises a raw provenance boundary only, not a claim about this fixture.
    _, _, manifest = write_cache(tmp_path, monkeypatch, metadata_changes={"source_type": source_type})
    _, origin = read()
    assert wind.source(origin) == origin and origin["source_type"] == "assumption"
    derived = json.loads(origin["ref"])
    assert derived["inputs"][0] == {"source_type": source_type,
        "ref": f"{manifest['ref']}; cached_sha256={manifest['sha256']}"}
    assert derived["inputs"][1]["source_type"] == "assumption"
    assert "not explicitly defined" in derived["inputs"][1]["ref"]
    assert "publication vintage unverified" in derived["method"]


@pytest.mark.parametrize("year", [2024, 2026, "2025", 2025., True])
def test_unreviewed_year_is_rejected_before_read(tmp_path, monkeypatch, year):
    monkeypatch.setattr(wind, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="only 2025"):
        wind.read_cached_day_ahead_price_month(year, settlement_locations=["EXACT_NODE"])


@pytest.mark.parametrize("month", [0, 13, "1", 1., True])
def test_invalid_month_is_rejected_before_read(tmp_path, monkeypatch, month):
    monkeypatch.setattr(wind, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="integer"):
        read(month)


@pytest.mark.parametrize("locations", [[], ["EXACT_NODE", "EXACT_NODE"], [" EXACT_NODE"], [True], "EXACT_NODE", ["EXACT_NODE", "UNKNOWN"]])
def test_location_selection_is_explicit_unique_and_complete(tmp_path, monkeypatch, locations):
    write_cache(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        read(locations=locations)


@pytest.mark.parametrize("date", ["2025-01-01", "2025/1/01", "2025/01/1", "2025/01/01Z", "2025/02/30", "2024/12/31", "2025/02/02", "", "1"])
def test_dates_are_strict_and_only_declared_boundary_dates_are_allowed(tmp_path, monkeypatch, date):
    raw = rows()
    raw.loc[0, "Date"] = date
    raw.loc[0, HOURS] = None  # Invalid dates are not forgiven as all-null padding.
    write_cache(tmp_path, monkeypatch, frame=raw)
    with pytest.raises(ValueError):
        read()


@pytest.mark.parametrize("pnode", [None, "", " "])
def test_selected_pnode_cannot_be_missing(tmp_path, monkeypatch, pnode):
    raw = rows()
    raw.loc[0, "PNODE Name"] = pnode
    write_cache(tmp_path, monkeypatch, frame=raw)
    with pytest.raises(ValueError, match="Pnode"):
        read()


def test_duplicate_identical_observations_deduplicate_and_sort_deterministically(tmp_path, monkeypatch):
    raw = pd.concat([rows(location="Z"), rows(location="A")], ignore_index=True)
    raw = pd.concat([raw, raw.iloc[[0, 31]]], ignore_index=True).sample(frac=1, random_state=7)
    write_cache(tmp_path, monkeypatch, frame=raw)
    first, origin_first = read(locations=["Z", "A"])
    second, origin_second = read(locations=["A", "Z"])
    pd.testing.assert_frame_equal(first, second, check_exact=True)
    assert origin_first == origin_second
    assert len(first) == 1488 and not first.duplicated(["Location", "Interval Start"]).any()
    assert first.Location.iloc[0] == "A"


@pytest.mark.parametrize("column,value", [("HE07", 30.), ("HE07", None), ("PNODE Name", "CHANGED_PNODE")])
def test_conflicting_price_missingness_or_pnode_is_rejected(tmp_path, monkeypatch, column, value):
    raw = rows().astype(object)
    conflict = raw.iloc[[0]].copy()
    conflict[column] = value
    write_cache(tmp_path, monkeypatch, frame=pd.concat([raw, conflict], ignore_index=True))
    with pytest.raises(ValueError, match="Conflicting"):
        read()


@pytest.mark.parametrize("change", ["missing_header", "duplicate_header", "extra_hour_header", "extra_field", "short_field", "unrelated_bad_width", "broken_quote", "invalid_utf8", "empty", "only_header"])
def test_full_csv_validation_rejects_ambiguous_or_invalid_records(tmp_path, monkeypatch, change):
    raw = rows()
    content = raw.to_csv(index=False).encode()
    if change == "missing_header":
        content = raw.drop(columns="HE24").to_csv(index=False).encode()
    elif change == "duplicate_header":
        content = content.replace(b"HE24", b" HE23")
    elif change == "extra_hour_header":
        content = raw.assign(HE25=1.).to_csv(index=False).encode()
    elif change in {"extra_field", "short_field", "unrelated_bad_width"}:
        record = content.decode().splitlines()[1].split(",")
        if change == "unrelated_bad_width":
            record[1] = "OTHER"
        record = record[:-1] if change == "short_field" else record + ["999"]
        content += (",".join(record) + "\n").encode()
    elif change == "broken_quote":
        content += b'2025/01/01,EXACT_NODE,"unterminated Pnode\n'
    elif change == "invalid_utf8":
        content += b"\xff"
    elif change == "empty":
        content = b""
    else:
        content = (",".join(COLUMNS) + "\n").encode()
    write_cache(tmp_path, monkeypatch, content=content)
    with pytest.raises(ValueError):
        read()


@pytest.mark.parametrize("change", ["request_url", "requested_url", "ref", "hash", "content_type", "source_type", "extra_record", "missing_column"])
def test_cache_identity_content_and_source_are_validated(tmp_path, monkeypatch, change):
    path, record, manifest = write_cache(tmp_path, monkeypatch)
    if change == "request_url":
        record[change] = record[change].replace("/01/", "/02/")
    elif change in {"requested_url", "ref"}:
        manifest[change] += "&unreviewed=1"
    elif change == "hash":
        manifest["sha256"] = "0" * 64
    elif change == "content_type":
        record["content"] = "not bytes"
    elif change == "source_type":
        manifest["source_type"] = "clause"
    record["source_json"] = json.dumps(manifest)
    cached = pd.DataFrame([record, record] if change == "extra_record" else [record])
    if change == "missing_column":
        cached = cached.drop(columns="request_url")
    cached.to_parquet(path, index=False)
    with pytest.raises(ValueError):
        read()


@pytest.mark.parametrize("extra", ['"source_type":"data"', '"ref":"duplicate"', '"sha256":"duplicate"',
                                   '"extra":{"value":1,"value":2}', '"extra":NaN', '"extra":Infinity', '"extra":1e400'])
def test_source_manifest_duplicate_or_nonfinite_keys_cannot_hide_assumptions(tmp_path, monkeypatch, extra):
    path, record, _ = write_cache(tmp_path, monkeypatch)
    record["source_json"] = record["source_json"][:-1] + "," + extra + "}"
    pd.DataFrame([record]).to_parquet(path, index=False)
    with pytest.raises(ValueError):
        read()


def test_missing_cache_is_not_fetched(tmp_path, monkeypatch):
    monkeypatch.setattr(wind, "ROOT", tmp_path)
    with pytest.raises(FileNotFoundError):
        read()


def test_existing_annual_reader_still_rejects_2025(tmp_path, monkeypatch):
    monkeypatch.setattr(wind, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="through 2024"):
        wind.read_cached_day_ahead_prices(2025, settlement_locations=["EXACT_NODE"])
