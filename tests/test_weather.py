"""Weather integration tests. Synthetic stress labels are software fixtures only."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

from pipeline.common import read_hourly, write_json
from pipeline.features import build_features
from pipeline.ingest import fetch_temperature_year, search_weather_areas
from pipeline.label import label_hours
from pipeline.prepare import join_evidence
from pipeline.weather import add_temperature, aggregate_temperature, create_area_mapping, read_weather_locations, select_area
from pipeline.workflow import train_run

POLICY = {"operator": "SPP", "label_method": "observed_event",
          "label_ref": "test://synthetic-event-fixture-only", "data_ref": "test://synthetic-weather-fixture-only"}
POINT = {"name": "Test point", "latitude": 35.22, "longitude": -101.83, "weight": 1.0}
MAPPING = {"operator": "SPP", "source_type": "assumption", "ref": "test://explicit-weather-mapping",
           "locations": {"TEST_ONLY": {"points": [POINT]}}}
CANDIDATES = pd.DataFrame([
    {"id": 1, "name": "Springfield", "admin1": "Illinois", "country_code": "US", "latitude": 39.8, "longitude": -89.6},
    {"id": 2, "name": "Springfield", "admin1": "Missouri", "country_code": "US", "latitude": 37.2, "longitude": -93.3},
])


def hourly_fixture(hours=300):
    return pd.DataFrame({"timestamp_utc": pd.date_range("2024-01-01", periods=hours, freq="h", tz="UTC"),
                         "location_id": "TEST_ONLY", "load_mw": 1000.0})


def weather_response():
    timeline = pd.date_range("2024-01-01", "2025-01-01", freq="h", inclusive="left", tz="UTC")
    return {"latitude": POINT["latitude"], "longitude": POINT["longitude"], "elevation": 100,
            "utc_offset_seconds": 0, "hourly_units": {"temperature_2m": "\u00b0C"},
            "hourly": {"time": [int(t.timestamp()) for t in timeline], "temperature_2m": [20.0] * len(timeline)}}


class AreaTests(unittest.TestCase):
    def test_state_is_respected_and_ambiguous_or_unknown_areas_are_rejected(self):
        self.assertEqual(select_area(CANDIDATES, "springfield, MO")["geonames_id"], 2)
        self.assertEqual(select_area(CANDIDATES, "Springfield, Illinois")["geonames_id"], 1)
        for area in ("Springfield", "Springfield, TX", "Nowhere, TX", "Springfield, MO, USA"):
            with self.subTest(area=area), self.assertRaises(ValueError):
                select_area(CANDIDATES, area)

    def test_empty_geocoding_result_is_cached_and_gives_actionable_error(self):
        response = Mock(url="https://example.invalid/geocoding", json=Mock(return_value={}))
        with tempfile.TemporaryDirectory() as directory, patch("requests.get", return_value=response) as get:
            candidates, _ = search_weather_areas("Unknown town", cache_dir=Path(directory))
            cached, _ = search_weather_areas("Unknown town", cache_dir=Path(directory))
            self.assertEqual(get.call_count, 1)
            self.assertTrue(cached.empty)
            with self.assertRaisesRegex(ValueError, "Candidates: none"):
                select_area(candidates, "Unknown town, TX")

    def test_area_mapping_preserves_grid_id_and_requires_selection_for_multiple_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hourly = hourly_fixture()
            hourly.to_parquet(root / "hourly.parquet", index=False)
            with patch("pipeline.ingest.search_weather_areas", return_value=(CANDIDATES, {"ref": "test://geocoding"})):
                mapping, location = create_area_mapping("Springfield, MO", root / "hourly.parquet", root / "area.json")
                self.assertEqual(location, "TEST_ONLY")
                self.assertEqual(mapping["locations"][location]["points"][0]["latitude"], 37.2)
                self.assertEqual(read_weather_locations(root / "area.json"), mapping)
            pd.concat([hourly, hourly.assign(location_id="SECOND_TEST_ONLY")]).to_parquet(root / "hourly.parquet", index=False)
            with patch("pipeline.ingest.search_weather_areas") as lookup, self.assertRaisesRegex(ValueError, "--location-id"):
                create_area_mapping("Springfield, MO", root / "hourly.parquet", root / "other.json")
            lookup.assert_not_called()

    def test_invalid_coordinate_weight_or_duplicate_point_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.json"
            for key, value in (("latitude", 91), ("longitude", -181), ("weight", 0), ("weight", True)):
                config = copy.deepcopy(MAPPING)
                config["locations"]["TEST_ONLY"]["points"][0][key] = value
                write_json(path, config)
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    read_weather_locations(path)
            config = copy.deepcopy(MAPPING)
            config["locations"]["TEST_ONLY"]["points"].append(POINT)
            write_json(path, config)
            with self.assertRaisesRegex(ValueError, "double-count"):
                read_weather_locations(path)


class DownloadAndJoinTests(unittest.TestCase):
    def test_calendar_year_cache_keeps_leap_day_and_never_refetches(self):
        payload = weather_response()
        payload["hourly"]["temperature_2m"][5] = None
        response = Mock(url="https://example.invalid/temperature", json=Mock(return_value=payload))
        with tempfile.TemporaryDirectory() as directory, patch("requests.get", return_value=response) as get:
            frame, path, source = fetch_temperature_year(35.22, -101.83, 2024, cache_dir=Path(directory))
            self.assertEqual(len(frame), 8784)
            self.assertEqual(source["missing_temperature_hours"], 1)
            self.assertEqual(len(frame[frame.timestamp_utc.dt.strftime("%m-%d").eq("02-29")]), 24)
            get.side_effect = AssertionError("Cached data must not be fetched again")
            cached, cached_path, _ = fetch_temperature_year(35.22, -101.83, 2024, cache_dir=Path(directory))
            pd.testing.assert_frame_equal(frame, cached)
            self.assertEqual(path, cached_path)
            self.assertEqual(get.call_count, 1)

    def test_wrong_units_timezone_and_duplicate_weather_hours_never_enter_cache(self):
        for case in ("units", "timezone", "duplicate"):
            payload = weather_response()
            if case == "units":
                payload["hourly_units"]["temperature_2m"] = "\u00b0F"
            elif case == "timezone":
                payload["utc_offset_seconds"] = -21600
            else:
                payload["hourly"]["time"][1] = payload["hourly"]["time"][0]
            response = Mock(url="https://example.invalid/temperature", json=Mock(return_value=payload))
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory, patch("requests.get", return_value=response):
                with self.assertRaises(ValueError):
                    fetch_temperature_year(35.22, -101.83, 2024, cache_dir=Path(directory))
                self.assertEqual(list(Path(directory).glob("*.parquet")), [])

    def test_weighted_area_requires_every_point_and_aligns_by_utc_timestamp(self):
        timeline = pd.date_range("2024-01-01 23:00", periods=3, freq="h", tz="UTC")
        one = pd.DataFrame({"timestamp_utc": timeline, "temperature_c": [10, 20, 30]})
        two = pd.DataFrame({"timestamp_utc": timeline, "temperature_c": [30, np.nan, 10]}).iloc[::-1]
        result = aggregate_temperature(timeline, [{"weight": 3}, {"weight": 1}], [one, two])
        np.testing.assert_allclose(result.temperature_c, [15, np.nan, 25], equal_nan=True)
        np.testing.assert_allclose(result.temperature_area_min_c, [10, np.nan, 10], equal_nan=True)
        np.testing.assert_allclose(result.temperature_area_max_c, [30, np.nan, 30], equal_nan=True)

    def test_weather_and_evidence_join_preserves_grid_data_gaps_and_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hourly = hourly_fixture(48)
            hourly.loc[4, "load_mw"] = np.nan
            hourly.to_parquet(root / "hourly.parquet", index=False)
            write_json(root / "area.json", MAPPING)
            weather = hourly[["timestamp_utc"]].assign(temperature_c=np.arange(48, dtype=float)).drop(index=12)
            weather.to_parquet(root / "raw.parquet", index=False)
            with patch("pipeline.ingest.fetch_temperature_year", return_value=(weather, root / "raw.parquet", {"ref": "test://weather"})), patch("builtins.print"):
                report = add_temperature(root / "hourly.parquet", root / "area.json", root / "joined.parquet")
                with self.assertRaisesRegex(ValueError, "already exists"):
                    add_temperature(root / "hourly.parquet", root / "area.json", root / "joined.parquet")
            joined = read_hourly(root / "joined.parquet")
            pd.testing.assert_frame_equal(hourly, joined[hourly.columns])
            self.assertNotIn("event_active", joined)
            self.assertEqual(report["locations"]["TEST_ONLY"]["missing_temperature_hours"], 1)
            self.assertTrue(pd.isna(joined.temperature_c.iloc[12]))
            evidence = hourly[["timestamp_utc", "location_id"]].iloc[:30].assign(event_active=0)
            evidence.to_csv(root / "evidence.csv", index=False)
            join_evidence(root / "joined.parquet", root / "evidence.csv", root / "labeled.parquet")
            labeled = read_hourly(root / "labeled.parquet")
            pd.testing.assert_series_equal(labeled.temperature_source_ref, joined.temperature_source_ref)
            self.assertEqual(labeled.event_active.isna().sum(), 18)

    def test_unmapped_grid_location_fails_before_any_weather_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hourly_fixture().assign(location_id="UNMAPPED").to_parquet(root / "hourly.parquet", index=False)
            write_json(root / "area.json", MAPPING)
            with patch("pipeline.ingest.fetch_temperature_year") as fetch, self.assertRaisesRegex(ValueError, "No weather mapping"):
                add_temperature(root / "hourly.parquet", root / "area.json", root / "joined.parquet")
            fetch.assert_not_called()


class TemperatureModelTests(unittest.TestCase):
    def test_future_temperature_cannot_change_prior_features_or_current_prediction_inputs(self):
        hourly = hourly_fixture(400).assign(event_active=0, temperature_c=np.arange(400, dtype=float))
        hourly["temperature_area_min_c"] = hourly.temperature_c - 5
        hourly["temperature_area_max_c"] = hourly.temperature_c + 5
        changed = hourly.copy()
        columns = [column for column in hourly if column.startswith("temperature")]
        changed.loc[200:, columns] = -1000
        before, names = build_features(label_hours(hourly, POLICY), POLICY)
        after, _ = build_features(label_hours(changed, POLICY), POLICY)
        cutoff = hourly.timestamp_utc.iloc[200]
        pd.testing.assert_frame_equal(before.loc[before.timestamp_utc <= cutoff, names], after.loc[after.timestamp_utc <= cutoff, names])
        row = before[before.timestamp_utc.eq(cutoff)].iloc[0]
        self.assertEqual(row.temperature_c_lag_1h, 199)
        self.assertEqual(row.temperature_c_min_24h, 176)
        self.assertEqual(row.temperature_c_max_24h, 199)
        self.assertEqual(row.temperature_c_change_24h, 24)

    def test_temperature_features_do_not_bridge_missing_hours_or_mix_locations(self):
        hourly = hourly_fixture(300).assign(event_active=0, temperature_c=10.0)
        other = hourly.assign(location_id="SECOND_TEST_ONLY", temperature_c=-10.0)
        hourly.loc[100, "temperature_c"] = np.nan
        features, _ = build_features(label_hours(pd.concat([hourly, other]), POLICY), POLICY)
        rows = features[features.timestamp_utc.eq(hourly.timestamp_utc.iloc[101])].set_index("location_id")
        self.assertTrue(pd.isna(rows.loc["TEST_ONLY", "temperature_c_lag_1h"]))
        self.assertTrue(pd.isna(rows.loc["TEST_ONLY", "temperature_c_max_24h"]))
        self.assertEqual(rows.loc["SECOND_TEST_ONLY", "temperature_c_lag_1h"], -10)

    def test_saved_model_can_learn_both_heat_and_cold_effects_on_synthetic_labels(self):
        # Deliberately artificial relation, used only to verify the input reaches ML.
        rng = np.random.default_rng(161)
        hourly = hourly_fixture(2400)
        hourly["temperature_c"] = rng.choice([-15.0, 15.0, 40.0], len(hourly))
        previous = hourly.temperature_c.shift(1)
        hourly["event_active"] = ((previous < 0) | (previous > 30)).astype(int)
        hourly["temperature_source_ref"] = "test://synthetic-temperatures-only"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hourly.to_parquet(root / "hourly.parquet", index=False)
            write_json(root / "policy.json", POLICY)
            with patch("builtins.print"):
                report = train_run(root / "hourly.parquet", root / "policy.json", root / "run", members=2, trees=35)
            saved = json.loads((root / "run/model_card.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["temperature_data"]["source_refs"], ["test://synthetic-temperatures-only"])
            self.assertEqual(saved["temperature_data"]["observed_hours"], 2400)
            self.assertGreater(report["mean_feature_importance"]["temperature_c_lag_1h"], 0)
            predictions = pd.read_parquet(root / "run/test_predictions.parquet")
            temps = hourly[["timestamp_utc"]].assign(previous=previous)
            predictions = predictions.merge(temps, on="timestamp_utc")
            means = predictions.groupby("previous").probability.mean()
            self.assertGreater(means[-15.0], means[15.0] + .3)
            self.assertGreater(means[40.0], means[15.0] + .3)


if __name__ == "__main__":
    unittest.main()
