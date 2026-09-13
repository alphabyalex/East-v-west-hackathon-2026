import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline.features import build_features
from pipeline.generation import normalize_generation
from pipeline.hours import summarize_contract, summarize_hours
from pipeline.stress import prepare_demand_proxy


class HoursTests(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame({"timestamp_utc": pd.to_datetime(["2024-01-01T00:00Z", "2024-01-01T01:00Z", "2024-01-01T03:00Z"]),
            "location_id": "TEST", "probability": [.2, .4, .8], "member_0": [.1, .3, .7],
            "member_1": [.3, .5, .9], "target": [0, 0, 1]})
        self.card = {"model_version": "test", "policy": {"label_method": "observed_event", "label_ref": "test://only"},
                     "confidence": {"TEST": {"level": "Low", "score": .8}}}

    def test_probability_sum_is_hours_without_threshold_or_gap_fill(self):
        summary = summarize_hours(self.frame, self.card)
        info = summary["locations"]["TEST"]
        self.assertAlmostEqual(info["expected_exposure_hours"], 1.4)
        self.assertAlmostEqual(info["member_expected_hours_min"], 1.1)
        self.assertAlmostEqual(info["member_expected_hours_max"], 1.7)
        self.assertEqual(info["unscored_hours"], 1)
        self.assertEqual(info["observed_target_hours"], 1)
        self.assertFalse(info["annual_simulation_ready"])
        self.assertFalse(summary["site_exposure_applied"])
        self.assertFalse(summary["forecast_of_future_dates"])
        self.assertEqual(info["highest_scored_hours"][0]["probability"], .8)

    def test_rejects_duplicates_nonfinite_inconsistent_and_timezone_free(self):
        for frame in [pd.concat([self.frame, self.frame]), self.frame.assign(probability=np.nan),
                      self.frame.assign(probability=.99), self.frame.assign(timestamp_utc=self.frame.timestamp_utc.dt.tz_localize(None)),
                      self.frame.assign(target=np.nan)]:
            with self.assertRaises(ValueError):
                summarize_hours(frame, self.card)

    def test_contract_quantiles_preserve_joint_trials(self):
        trials = pd.DataFrame({"location_id": "TEST", "simulation_id": np.repeat(np.arange(1000), 2),
                               "year_offset": np.tile([1, 2], 1000),
                               "modeled_exposure_hours": np.where(np.arange(2000) % 4 < 2, [0, 100] * 1000, [100, 0] * 1000)})
        info = summarize_contract(trials)["TEST"]
        self.assertEqual(info["p90_total_hours"], 100)
        self.assertEqual(info["expected_total_hours"], 100)
        with self.assertRaises(ValueError):
            summarize_contract(trials.iloc[:-1])

    def test_joint_features_only_use_past_observations(self):
        frame = pd.DataFrame({"timestamp_utc": pd.date_range("2024-01-01", periods=300, freq="h", tz="UTC"),
                              "location_id": "TEST", "load_mw": np.arange(300) + 1000., "temperature_c": 30.,
                              "wind_mw": 200., "solar_mw": 50., "target": np.arange(300) % 2})
        original, _ = build_features(frame, {"label_method": "observed_event"})
        at = frame.timestamp_utc.iloc[200]
        modified = frame.copy()
        modified.loc[modified.timestamp_utc >= at, ["temperature_c", "wind_mw", "solar_mw", "load_mw"]] = 99999.
        changed, _ = build_features(modified, {"label_method": "observed_event"})
        pd.testing.assert_frame_equal(original[original.timestamp_utc <= at], changed[changed.timestamp_utc <= at])
        row = original[original.timestamp_utc == at].iloc[0]
        self.assertEqual(row.temperature_x_load_lag_1h, 30 * 1199)
        self.assertEqual(row.net_load_lag_1h, 949)
        self.assertEqual(row.load_change_1h, 1)


class GenerationTests(unittest.TestCase):
    def test_incomplete_conflicting_samples_and_boundaries_remain_unknown(self):
        raw = pd.DataFrame({"GMT MKT Interval": pd.date_range("2024-01-01", periods=25, freq="5min", tz="UTC"),
                            "Wind Market": 100., "Wind Self": 20., "Solar Market": 5., "Solar Self": 1.})
        result = normalize_generation(raw)
        self.assertEqual(result.wind_mw.iloc[0], 120)
        self.assertTrue(pd.isna(result.wind_mw.iloc[-1]))
        duplicate = raw.iloc[[0]].copy()
        duplicate["Wind Market"] = 99
        result = normalize_generation(pd.concat([raw, duplicate]))
        self.assertTrue(pd.isna(result.wind_mw.iloc[0]))
        self.assertEqual(result.solar_mw.iloc[1], 6)


class DemandProxyTests(unittest.TestCase):
    def test_reference_threshold_ignores_future_and_missing_load_is_not_negative(self):
        times = pd.date_range("2019-01-01", "2025-01-01", freq="h", tz="UTC", inclusive="left")
        load = 1000 + 200 * np.sin(np.arange(len(times)) * 2 * np.pi / 168)
        frame = pd.DataFrame({"timestamp_utc": times, "location_id": "TEST", "load_mw": load})
        frame.loc[200, "load_mw"] = np.nan
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.parquet"
            frame.to_parquet(source, index=False)
            first = prepare_demand_proxy(source, root / "first.parquet", root / "first.json", reference_end="2022-01-01T00:00Z")
            self.assertTrue(pd.isna(pd.read_parquet(root / "first.parquet").event_active.iloc[200]))
            frame.loc[frame.timestamp_utc >= "2023-01-01", "load_mw"] += 10
            frame.to_parquet(source, index=False)
            second = prepare_demand_proxy(source, root / "second.parquet", root / "second.json", reference_end="2022-01-01T00:00Z")
            self.assertEqual(first["demand_proxy"]["thresholds_mw"], second["demand_proxy"]["thresholds_mw"])
            with self.assertRaisesRegex(ValueError, "beyond the training"):
                prepare_demand_proxy(source, root / "bad.parquet", root / "bad.json", reference_end="2024-01-01T00:00Z")


if __name__ == "__main__":
    unittest.main()
