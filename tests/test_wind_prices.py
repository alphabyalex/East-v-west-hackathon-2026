"""Hermetic archived-price fixtures; no synthetic value is declared SPP data."""
import hashlib
import io
import json
import zipfile

import pandas as pd
import pytest

from pipeline.wind_signal import prepare_wind_inputs, read_cached_day_ahead_prices


URL = "https://portal.spp.org/file-browser-api/download/da-lmp-by-settlement-location?path=/2024/2024.zip"
DAILY = "2024/01/By_Day/DA-LMP-SL-202401010100.csv"
MONTHLY = "2024/01/DA-LMP-MONTHLY-SL-202401.csv"


def prices():
    return pd.DataFrame({"GMTIntervalEnd": ["01/01/2024 07:00:00", "01/01/2024 08:00:00"] * 2,
                         "Settlement Location": ["EXACT_NODE"] * 2 + ["OTHER"] * 2,
                         "Pnode": ["EXACT_PNODE"] * 2 + ["OTHER_PNODE"] * 2,
                         "LMP": [-5., 10., -100., 100.]})


def write_cache(tmp_path, monkeypatch, *, members=None, source_type="assumption", metadata_changes=None):
    if members is None:
        members = [(DAILY, prices()), (MONTHLY, prices().assign(LMP=999.))]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, frame in members:
            archive.writestr(name, frame.to_csv(index=False))
    content = buffer.getvalue()
    manifest = {"source_type": source_type, "ref": URL, "requested_url": URL,
                "sha256": hashlib.sha256(content).hexdigest(), **(metadata_changes or {})}
    row = {"request_url": URL, "content": content, "source_json": json.dumps(manifest)}
    path = tmp_path / "data/raw/spp/evidence/da_lmp_settlement_2024.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([row]).to_parquet(path, index=False)
    monkeypatch.setattr("pipeline.wind_signal.ROOT", tmp_path)
    return path, row


def read():
    return read_cached_day_ahead_prices(2024, settlement_locations=["EXACT_NODE"])


def test_exact_node_selection_interval_end_and_monthly_duplicate_exclusion(tmp_path, monkeypatch):
    write_cache(tmp_path, monkeypatch)
    frame, source = read()
    assert frame.LMP.tolist() == [-5., 10.]
    assert frame.Location.tolist() == ["EXACT_NODE"] * 2
    assert frame.Pnode.tolist() == ["EXACT_PNODE"] * 2
    assert frame.Market.tolist() == ["DAY_AHEAD_HOURLY"] * 2
    assert frame["Interval Start"].tolist() == list(pd.date_range("2024-01-01T06:00Z", periods=2, freq="h"))
    assert (frame["Interval End"] - frame["Interval Start"]).dt.total_seconds().eq(3600).all()
    assert source["source_type"] == "assumption"
    assert "publication vintage unverified" in source["ref"]
    assert "cached_sha256=" in source["ref"]


def test_reader_connects_to_existing_wind_input_adapter(tmp_path, monkeypatch):
    write_cache(tmp_path, monkeypatch)
    price, _source = read()
    starts = pd.date_range("2024-01-01T06:00Z", periods=24, freq="5min")
    generation = pd.DataFrame({"Interval Start": starts, "Interval End": starts + pd.Timedelta(5, unit="min"), "Wind": 60.})
    load = pd.DataFrame({"timestamp_utc": price["Interval Start"], "location_id": "SPP_SYSTEM", "load_mw": 100.})
    frame = prepare_wind_inputs(generation, load, price, price_locations={"USER_SELECTED_PRICE_POINT": "EXACT_NODE"}, market="DAY_AHEAD_HOURLY")
    assert frame.lmp_usd_mwh.tolist() == [-5., 10.]
    assert frame.location_id.tolist() == ["USER_SELECTED_PRICE_POINT"] * 2


@pytest.mark.parametrize("locations", [[], ["EXACT_NODE", "EXACT_NODE"], [" EXACT_NODE"], ["EXACT_NODE", "UNKNOWN"], [True]])
def test_unknown_or_ambiguous_location_selection_is_not_guessed(tmp_path, monkeypatch, locations):
    write_cache(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        read_cached_day_ahead_prices(2024, settlement_locations=locations)


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), True])
def test_invalid_selected_prices_are_rejected(tmp_path, monkeypatch, bad):
    frame = prices().astype({"LMP": object})
    frame.loc[0, "LMP"] = bad
    write_cache(tmp_path, monkeypatch, members=[(DAILY, frame)])
    with pytest.raises(ValueError):
        read()


def test_missing_price_remains_missing_and_unrelated_node_is_not_averaged(tmp_path, monkeypatch):
    frame = prices()
    frame.loc[0, "LMP"] = float("nan")
    write_cache(tmp_path, monkeypatch, members=[(DAILY, frame)])
    actual, _source = read()
    assert pd.isna(actual.LMP.iloc[0])
    assert actual.LMP.iloc[1] == 10.


@pytest.mark.parametrize("changes", [{"LMP": 2.}, {"Pnode": "CHANGED_PNODE"}])
def test_conflicting_same_node_hour_is_rejected(tmp_path, monkeypatch, changes):
    frame = prices()
    conflict = frame.iloc[[0]].assign(**changes)
    write_cache(tmp_path, monkeypatch, members=[(DAILY, pd.concat([frame, conflict]))])
    with pytest.raises(ValueError, match="Conflicting"):
        read()


def test_identical_duplicate_rows_do_not_inflate_hour_count(tmp_path, monkeypatch):
    frame = prices()
    write_cache(tmp_path, monkeypatch, members=[(DAILY, pd.concat([frame, frame]))])
    assert len(read()[0]) == 2


@pytest.mark.parametrize("stamp", ["01/01/2024 07:30:00", "not a timestamp", None, 0])
def test_invalid_delivery_hour_is_rejected(tmp_path, monkeypatch, stamp):
    frame = prices()
    frame.loc[0, "GMTIntervalEnd"] = stamp
    write_cache(tmp_path, monkeypatch, members=[(DAILY, frame)])
    with pytest.raises(ValueError):
        read()


def test_cached_payload_tampering_is_rejected_before_zip_parsing(tmp_path, monkeypatch):
    path, row = write_cache(tmp_path, monkeypatch)
    row["content"] += b"changed"
    pd.DataFrame([row]).to_parquet(path, index=False)
    with pytest.raises(ValueError, match="fingerprint"):
        read()


def test_missing_cache_never_attempts_download(tmp_path, monkeypatch):
    monkeypatch.setattr("pipeline.wind_signal.ROOT", tmp_path)
    monkeypatch.setattr("pipeline.wind_signal.ingest.fetch_public_evidence", lambda *args, **kwargs: pytest.fail("reader attempted a download"))
    with pytest.raises(FileNotFoundError):
        read()


def test_archive_order_and_input_row_order_do_not_change_prices(tmp_path, monkeypatch):
    write_cache(tmp_path, monkeypatch)
    first, _ = read()
    write_cache(tmp_path, monkeypatch, members=[(MONTHLY, prices().assign(LMP=999.)), (DAILY, prices().iloc[::-1])])
    second, _ = read()
    pd.testing.assert_frame_equal(first, second)
