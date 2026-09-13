"""Reviewed winter price-reader extension; every fixture is synthetic and offline."""
import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from pipeline import wind_signal as wind


HOURS = [f"HE{hour:02d}" for hour in range(1, 25)]
COLUMNS = ["Date", "Settlement Location Name", "PNODE Name", "Price Type", *HOURS]


@pytest.fixture(autouse=True)
def no_fetch(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Price-reader tests must never fetch or compute opportunity flags")
    monkeypatch.setattr(wind.ingest, "fetch_public_evidence", forbidden)
    monkeypatch.setattr(wind.ingest, "fetch_chunk", forbidden)
    monkeypatch.setattr(wind, "wind_oversupply_hours", forbidden)


def bounds(year, month):
    first = pd.Timestamp(year=year, month=month, day=1)
    after = first + pd.offsets.MonthBegin()
    return first, after, first.tz_localize("America/Chicago").tz_convert("UTC"), after.tz_localize("America/Chicago").tz_convert("UTC")


def monthly_rows(month, *, year=2026, location="EXACT_NODE", pnode="EXACT_PNODE"):
    first, after, start, end = bounds(year, month)
    rows = []
    for day in pd.date_range(first, after, freq="D"):
        row = {"Date": day.strftime("%Y/%m/%d"), "Settlement Location Name": location,
               "PNODE Name": pnode, "Price Type": "LMP"}
        for offset, name in enumerate(HOURS):
            stamp = day.tz_localize("UTC") + pd.Timedelta(offset, unit="h")
            row[name] = (stamp - start) / pd.Timedelta(1, unit="h") - 20.25 if start <= stamp < end else None
        rows.append(row)
    return pd.DataFrame(rows, columns=COLUMNS)


def cache(tmp_path, monkeypatch, month=1, *, year=2026, frame=None):
    frame = monthly_rows(month, year=year) if frame is None else frame
    content = frame.rename(columns={"PNODE Name": " PNODE Name"}).to_csv(index=False).encode("utf-8")
    url = ("https://portal.spp.org/file-browser-api/download/da-lmp-by-settlement-location"
           f"?path=/{year}/{month:02d}/DA-LMP-MONTHLY-SL-{year}{month:02d}.csv")
    metadata = {"source_type": "assumption", "ref": url, "requested_url": url,
                "sha256": hashlib.sha256(content).hexdigest()}
    record = {"request_url": url, "source_json": json.dumps(metadata), "content": content}
    path = tmp_path / f"data/raw/spp/evidence/da_lmp_settlement_{year}_{month:02d}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([record]).to_parquet(path, index=False)
    monkeypatch.setattr(wind, "ROOT", tmp_path)
    return path, record, metadata


def read(month, *, year=2026, locations=None):
    return wind.read_cached_day_ahead_price_month(year, month,
        settlement_locations=["EXACT_NODE"] if locations is None else locations)


@pytest.mark.parametrize("month,hours,start,end", [
    (1, 744, "2026-01-01T06:00Z", "2026-02-01T06:00Z"),
    (2, 672, "2026-02-01T06:00Z", "2026-03-01T06:00Z"),
])
def test_reviewed_winter_month_preserves_utc_he_mapping_and_negative_prices(tmp_path, monkeypatch, month, hours, start, end):
    _, _, metadata = cache(tmp_path, monkeypatch, month)
    frame, source = read(month)
    assert frame.columns.tolist() == ["Interval Start", "Interval End", "Market", "Location", "Pnode", "LMP"]
    assert frame["Interval Start"].tolist() == list(pd.date_range(start, end, freq="h", inclusive="left"))
    assert frame["Interval End"].iloc[-1] == pd.Timestamp(end)
    assert (frame["Interval End"] - frame["Interval Start"]).eq(pd.Timedelta(1, unit="h")).all()
    assert frame.LMP.tolist() == [hour - 20.25 for hour in range(hours)]
    assert frame.Location.eq("EXACT_NODE").all() and frame.Pnode.eq("EXACT_PNODE").all()
    assert frame.Market.eq("DAY_AHEAD_HOURLY").all()
    assert source["source_type"] == "assumption"
    reference = json.loads(source["ref"])
    assert reference["inputs"][0] == {"source_type": "assumption", "ref": metadata["ref"] + "; cached_sha256=" + metadata["sha256"]}
    assert "null boundary padding discarded=24" in reference["method"]
    assert "publication vintage unverified" in reference["method"]
    assert "not explicitly defined" in reference["inputs"][1]["ref"]


def test_utc_new_year_prefix_requires_previous_december_and_is_never_filled(tmp_path, monkeypatch):
    cache(tmp_path, monkeypatch, 12, year=2025)
    cache(tmp_path, monkeypatch, 1)
    december, _ = read(12, year=2025)
    january, _ = read(1)
    boundary = pd.Timestamp("2026-01-01T00:00Z")
    prior_tail = december.loc[december["Interval Start"] >= boundary]
    assert prior_tail["Interval Start"].tolist() == list(pd.date_range(boundary, periods=6, freq="h"))
    assert not january["Interval Start"].lt(pd.Timestamp("2026-01-01T06:00Z")).any()
    combined = pd.concat([prior_tail, january.iloc[[0]]], ignore_index=True)
    assert combined["Interval Start"].tolist() == list(pd.date_range(boundary, periods=7, freq="h"))
    assert not combined.duplicated(["Location", "Interval Start"]).any()


@pytest.mark.parametrize("month", [1, 2])
def test_missing_interior_cells_and_absent_days_remain_distinct(tmp_path, monkeypatch, month):
    raw = monthly_rows(month).drop(index=2)
    raw.loc[1, "HE10"] = None
    cache(tmp_path, monkeypatch, month, frame=raw)
    frame, source = read(month)
    assert frame.LMP.isna().sum() == 1
    assert frame.loc[frame.LMP.isna(), "Interval Start"].iloc[0] == pd.Timestamp(year=2026, month=month, day=2, hour=9, tz="UTC")
    assert not frame["Interval Start"].dt.strftime("%Y-%m-%d").eq(f"2026-{month:02d}-03").any()
    assert len(frame) == (744 if month == 1 else 672) - 24
    assert "no gap filling" in source["ref"]


def test_final_padding_date_keeps_six_unknown_hours_for_an_exact_second_point(tmp_path, monkeypatch):
    missing_tail = monthly_rows(2, location="OTHER_EXACT", pnode="OTHER_PNODE").iloc[[-1]].copy()
    missing_tail[HOURS] = np.nan
    raw = pd.concat([monthly_rows(2), missing_tail], ignore_index=True)
    cache(tmp_path, monkeypatch, 2, frame=raw)
    frame, _ = read(2, locations=["EXACT_NODE", "OTHER_EXACT"])
    missing = frame.loc[frame.Location.eq("OTHER_EXACT")]
    assert len(missing) == 6 and missing.LMP.isna().all()
    assert missing["Interval Start"].tolist() == list(pd.date_range("2026-03-01T00:00Z", periods=6, freq="h"))
    assert missing.Pnode.eq("OTHER_PNODE").all()


@pytest.mark.parametrize("boundary,column", [("first", "HE01"), ("last", "HE07")])
def test_nonnull_2026_padding_is_rejected_even_when_zero(tmp_path, monkeypatch, boundary, column):
    raw = monthly_rows(2)
    raw.loc[0 if boundary == "first" else raw.index[-1], column] = 0.
    cache(tmp_path, monkeypatch, 2, frame=raw)
    with pytest.raises(ValueError, match="outside the declared operating month"):
        read(2)


def test_exact_point_and_pnode_names_are_not_aliased_or_merged(tmp_path, monkeypatch):
    north = monthly_rows(1, location="SPPNORTH_HUB", pnode="Exact.North.Pnode")
    south = monthly_rows(1, location="SPPSOUTH_HUB", pnode="Exact.South.Pnode")
    lookalike = north.assign(**{"Settlement Location Name": "SPPNORTH_HUB ", "PNODE Name": "wrong"})
    component = north.assign(**{"Price Type": "MCC", "PNODE Name": "wrong"})
    lookalike[HOURS] = "not selected"
    component[HOURS] = "not selected"
    cache(tmp_path, monkeypatch, frame=pd.concat([lookalike, component, south, north], ignore_index=True))
    frame, _ = read(1, locations=["SPPSOUTH_HUB", "SPPNORTH_HUB"])
    assert frame.groupby("Location").Pnode.unique().map(list).to_dict() == {
        "SPPNORTH_HUB": ["Exact.North.Pnode"], "SPPSOUTH_HUB": ["Exact.South.Pnode"]}
    assert len(frame) == 1488 and frame.LMP.min() == -20.25
    with pytest.raises(ValueError, match="exact settlement locations"):
        read(1, locations=["SPPNORTH_HUB", "sppsouth_hub"])


@pytest.mark.parametrize("month", range(3, 13))
def test_later_2026_months_reject_before_any_cache_read(monkeypatch, month):
    def forbidden(*args, **kwargs):
        pytest.fail("An unreviewed month reached cache I/O")
    monkeypatch.setattr(pd, "read_parquet", forbidden)
    with pytest.raises(ValueError, match="only January/February for 2026"):
        read(month)


@pytest.mark.parametrize("year,month", [(2026., 1), ("2026", 1), (np.int64(2026), 1), (2026, True), (2026, 2.), (2026, "2")])
def test_winter_extension_retains_strict_integer_argument_types(monkeypatch, year, month):
    monkeypatch.setattr(pd, "read_parquet", lambda *a, **kw: pytest.fail("Invalid argument reached cache I/O"))
    with pytest.raises(ValueError):
        read(month, year=year)


@pytest.mark.parametrize("field", ["request_url", "requested_url", "ref", "sha256"])
def test_2026_cache_cannot_identify_2025_or_use_unmatched_bytes(tmp_path, monkeypatch, field):
    path, record, metadata = cache(tmp_path, monkeypatch, 2)
    if field == "request_url":
        record[field] = record[field].replace("2026", "2025")
    elif field == "sha256":
        metadata[field] = "0" * 64
    else:
        metadata[field] = metadata[field].replace("2026", "2025")
    record["source_json"] = json.dumps(metadata)
    pd.DataFrame([record]).to_parquet(path, index=False)
    with pytest.raises(ValueError):
        read(2)


@pytest.mark.parametrize("quantity", ["False", "1e400"])
def test_winter_invalid_quantities_do_not_become_prices(tmp_path, monkeypatch, quantity):
    raw = monthly_rows(1).astype({"HE07": object})
    raw.loc[0, "HE07"] = quantity
    cache(tmp_path, monkeypatch, frame=raw)
    with pytest.raises(ValueError):
        read(1)


def test_2026_reader_does_not_accept_an_unreviewed_baa_schema(tmp_path, monkeypatch):
    cache(tmp_path, monkeypatch, frame=monthly_rows(1).assign(BAA="SPP"))
    with pytest.raises(ValueError, match="requires exactly"):
        read(1)


@pytest.mark.parametrize("month", [1, 2])
def test_missing_2026_cache_has_no_fetch_fallback(tmp_path, monkeypatch, month):
    monkeypatch.setattr(wind, "ROOT", tmp_path)
    with pytest.raises(FileNotFoundError):
        read(month)


def test_all_twelve_legacy_2025_months_remain_supported(tmp_path, monkeypatch):
    for month in range(1, 13):
        cache(tmp_path, monkeypatch, month, year=2025)
        frame, source = read(month, year=2025)
        _, _, start, end = bounds(2025, month)
        assert frame["Interval Start"].tolist() == list(pd.date_range(start, end, inclusive="left", freq="h"))
        assert frame["Interval End"].iloc[-1] == end
        assert source["source_type"] == "assumption"
        assert "monthly_da_lmp_utc_he_v1" in source["ref"]
