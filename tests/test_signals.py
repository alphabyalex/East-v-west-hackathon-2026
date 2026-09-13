"""Meaningful explanation/transfer checks using disposable synthetic histories."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
from scipy.special import expit

from pipeline.features import build_features
from pipeline.regional import area_targets, expected_hours, normalize_area_load, validation_folds
from pipeline.signals import calibrated_contributions, explain_signals, pattern_associations
from pipeline.train import chronological_split, fit_ensemble, predict_members


def fixture(hours=2400):
    rng = np.random.default_rng(910)
    temp = rng.uniform(-10, 40, hours)
    load = rng.uniform(100, 300, hours)
    return pd.DataFrame({"timestamp_utc": pd.date_range("2020-01-01", periods=hours, freq="h", tz="UTC"),
        "location_id": "TEST_ONLY", "load_mw": load, "temperature_c": temp,
        "wind_mw": rng.uniform(10, 50, hours), "solar_mw": rng.uniform(0, 10, hours),
        "target": ((pd.Series(temp).shift(1) > 25) & (pd.Series(load).shift(1) > 220)).astype(int)})


class SignalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = fixture()
        cls.features, cls.names = build_features(cls.raw, {"label_method": "observed_event"})
        cls.bundle, cls.card, _ = fit_ensemble(cls.features, cls.names, members=2, trees=12)
        cls.split = chronological_split(cls.features)

    def test_calibrated_contributions_reconstruct_each_member(self):
        query = self.split["test"].head(10)
        contrib, probability = calibrated_contributions(self.bundle, query)
        np.testing.assert_allclose(expit(contrib.sum(axis=2)).T, probability)
        np.testing.assert_allclose(probability, predict_members(self.bundle, query))
        # Direction must follow the fitted calibrator even if its slope reverses.
        calibrator = self.bundle["members"][0][1]
        previous = calibrator.coef_.copy()
        try:
            calibrator.coef_ *= -1
            flipped, _ = calibrated_contributions(self.bundle, query)
            np.testing.assert_allclose(flipped[0, :, :-1], -contrib[0, :, :-1])
        finally:
            calibrator.coef_ = previous

    def test_unlabeled_inference_never_invents_known_negative_targets(self):
        observed = self.raw.drop(columns="target")
        frame, names = build_features(observed, {"label_method": "observed_event"}, require_target=False)
        self.assertTrue(frame.target.isna().all())
        self.assertEqual(names, self.names)
        self.assertNotIn("target", names)
        self.assertNotIn("location_id", names)
        self.assertTrue(np.isfinite(predict_members(self.bundle, frame)).all())

    def test_pattern_cutoffs_do_not_use_future_observations(self):
        training, test = self.split["train"], self.split["test"]
        before = pattern_associations(training, test)
        changed = test.assign(temperature_c_lag_1h=9999, load_mw_lag_1h=1e8)
        after = pattern_associations(training, changed)
        self.assertEqual([x["criteria"] for x in before], [x["criteria"] for x in after])

    def test_explanations_use_training_analogues_and_preserve_probability(self):
        test = self.split["test"].copy()
        result = explain_signals(self.bundle, self.split["train"], test, test, "test://fixture", limit=3)
        json.dumps(result, allow_nan=False)
        self.assertEqual(len(result["hours"]), 3)
        scores = predict_members(self.bundle, test).mean(axis=1)
        self.assertAlmostEqual(result["hours"][0]["probability"], scores.max())
        for hour in result["hours"]:
            keys = []
            for example in hour["similar_training_hours"]:
                time = pd.Timestamp(example["timestamp_utc"])
                self.assertLess(time, test.timestamp_utc.min())
                keys.append((example["area"], time.floor("D")))
            self.assertEqual(len(keys), len(set(keys)))
            self.assertTrue(hour["drivers"])
            self.assertEqual(hour["confidence"], "Low")

    def test_missing_temperature_is_not_a_zero_temperature(self):
        query = self.split["test"].head(2).copy()
        query["temperature_c_lag_1h"] = np.nan
        result = explain_signals(self.bundle, self.split["train"], self.split["test"], query, "test://missing", limit=2)
        self.assertIsNone(result["hours"][0]["prior_temperature_c"])
        self.assertEqual(result["hours"][0]["similar_training_hours"], [])

    def test_area_validation_excludes_locations_and_later_times(self):
        frame = pd.concat([self.features.assign(location_id=area) for area in ("SPS", "OKGE", "LES", "OPPD")], ignore_index=True)
        observed = set()
        for excluded, training, test in validation_folds(frame):
            self.assertFalse(set(training.location_id) & set(test.location_id))
            split = chronological_split(training)
            self.assertLess(split["calibration"].timestamp_utc.max(), test.timestamp_utc.min())
            observed.update(excluded)
        self.assertEqual(observed, {"SPS", "OKGE", "LES", "OPPD"})

    def test_area_targets_use_separate_raw_histories_and_past_thresholds(self):
        times = pd.date_range("2020-01-01", periods=10000, freq="h", tz="UTC")
        frame = pd.DataFrame({"timestamp_utc": times, "area_load_mw": np.arange(len(times))})
        labeled, cutoff = area_targets(frame)
        future = pd.DataFrame({"timestamp_utc": pd.date_range("2024-01-01", periods=100, freq="h", tz="UTC"), "area_load_mw": 1e9})
        _, later = area_targets(pd.concat([frame, future], ignore_index=True))
        self.assertEqual(cutoff, later)
        unknown = frame.copy(); unknown.loc[10, "area_load_mw"] = np.nan
        self.assertTrue(pd.isna(area_targets(unknown)[0].target.iloc[10]))
        archive = pd.DataFrame({"MarketHour": ["2024-01-01 01:00"] * 2 + ["2024-01-01 02:00"],
                                " A": [10, 10, 30], " B": [10, 20, 5]})
        a, b = normalize_area_load(archive, "A"), normalize_area_load(archive, "B")
        self.assertEqual(a.area_load_mw.iloc[0], 10)
        self.assertTrue(pd.isna(b.area_load_mw.iloc[0]))
        self.assertEqual(a.timestamp_utc.iloc[0], pd.Timestamp("2024-01-01T00:00Z"))

    def test_area_validation_remains_chronological_with_uneven_source_coverage(self):
        parts = [self.features.assign(location_id="SPS")]
        for area in ("OKGE", "LES", "OPPD"):
            parts.append(self.features.iloc[600:].assign(location_id=area))
        frame = pd.concat(parts, ignore_index=True)
        for _, training, test in validation_folds(frame):
            self.assertLess(chronological_split(training)["calibration"].timestamp_utc.max(), test.timestamp_utc.min())

    def test_annual_expected_hours_preserve_units_and_reject_missing_seasons(self):
        query = pd.DataFrame({"timestamp_utc": pd.date_range("2023-01-01", "2024-01-01", freq="h", tz="UTC")})
        probability = np.full((len(query), 2), .1)
        result = expected_hours(query, probability)
        self.assertAlmostEqual(result["annual_expected_hours"], 876)
        self.assertEqual(result["unscored_hours"], 0)
        keep = query.timestamp_utc.dt.month.ne(7)
        with self.assertRaisesRegex(ValueError, "month 7"):
            expected_hours(query[keep], probability[keep])
        with self.assertRaises(ValueError):
            expected_hours(pd.concat([query, query.tail(1)]), np.full((len(query) + 1, 2), .1))
        with self.assertRaises(ValueError):
            expected_hours(query, np.full((len(query), 2), float("nan")))


if __name__ == "__main__":
    unittest.main()
