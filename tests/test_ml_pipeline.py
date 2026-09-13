"""Synthetic fixtures are software tests only; they never populate production data."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from pipeline.common import read_hourly, read_policy, write_json
from pipeline.features import build_features
from pipeline.label import label_hours
from pipeline.prepare import LOAD_AREAS, join_evidence, normalize_legacy_load
from pipeline.simulate import LocationNotFoundError, get_location_estimate, longest_run, simulate_exposure
from pipeline.train import chronological_split, fit_ensemble, predict_members
from pipeline.workflow import train_run

POLICY = {"operator": "SPP", "label_method": "observed_event",
          "label_ref": "test://synthetic-events-software-verification-only",
          "data_ref": "test://synthetic-observations-software-verification-only"}


def fixture(hours: int = 2400) -> pd.DataFrame:
    rng = np.random.default_rng(812)
    index = np.arange(hours)
    load = 20000 + 2000 * np.sin(index / 40) + 400 * rng.normal(size=hours)
    target = (np.roll(load, 1) > 21000).astype(float)
    return pd.DataFrame({"timestamp_utc": pd.date_range("2023-01-01", periods=hours, freq="h", tz="UTC"),
                         "location_id": "TEST_ONLY_SPP_SYSTEM", "load_mw": load, "event_active": target})


class InputAndLabelTests(unittest.TestCase):
    def test_missing_labels_are_not_normal_hours(self):
        frame = fixture(50)
        frame.loc[25, "event_active"] = np.nan
        result = label_hours(frame, POLICY)
        self.assertTrue(pd.isna(result.loc[25, "target"]))
        features, _ = build_features(result, POLICY)
        self.assertNotIn(frame.loc[25, "timestamp_utc"], features.timestamp_utc.to_list())

    def test_labels_require_declared_evidence(self):
        with self.assertRaisesRegex(ValueError, "do not substitute"):
            label_hours(fixture(), {**POLICY, "label_method": "reserve_shortfall"})

    def test_reserve_equality_is_not_shortfall_and_missing_stays_unknown(self):
        frame = fixture(3)
        frame["available_reserves_mw"] = [99, 100, np.nan]
        frame["required_reserves_mw"] = [100, 100, 100]
        result = label_hours(frame, {**POLICY, "label_method": "reserve_shortfall"})
        self.assertEqual(result.target.iloc[:2].tolist(), [1, 0])
        self.assertTrue(pd.isna(result.target.iloc[2]))

    def test_observed_event_binary_validation(self):
        frame = fixture(3)
        frame.loc[0, "event_active"] = 2
        with self.assertRaisesRegex(ValueError, "0, 1"):
            label_hours(frame, POLICY)

    def test_input_rejects_timezone_ambiguity_duplicates_and_infinity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.csv"
            for case in ("naive", "duplicate", "infinite"):
                with self.subTest(case=case):
                    frame = fixture(50)
                    if case == "naive":
                        frame.timestamp_utc = frame.timestamp_utc.dt.tz_localize(None)
                    elif case == "duplicate":
                        frame = pd.concat([frame, frame.iloc[:1]])
                    else:
                        frame.loc[0, "load_mw"] = np.inf
                    frame.to_csv(path, index=False)
                    with self.assertRaises(ValueError):
                        read_hourly(path)

    def test_price_threshold_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            write_json(path, {**POLICY, "label_method": "scarcity_price"})
            with self.assertRaisesRegex(ValueError, "price_threshold"):
                read_policy(path)

    def test_hourly_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.parquet"
            frame = fixture(50)
            frame.to_parquet(path, index=False)
            result = read_hourly(path)
            pd.testing.assert_frame_equal(frame, result)


class FeatureTests(unittest.TestCase):
    def test_future_changes_cannot_change_earlier_features(self):
        original = fixture(400)
        changed = original.copy()
        changed.loc[200:, "load_mw"] *= 10
        before, names = build_features(label_hours(original, POLICY), POLICY)
        after, _ = build_features(label_hours(changed, POLICY), POLICY)
        cutoff = original.timestamp_utc.iloc[200]
        pd.testing.assert_frame_equal(before.loc[before.timestamp_utc <= cutoff, names],
                                      after.loc[after.timestamp_utc <= cutoff, names])

    def test_same_hour_value_is_excluded(self):
        frame = fixture(100)
        features, names = build_features(label_hours(frame, POLICY), POLICY)
        hour = features.iloc[0]
        self.assertEqual(hour.load_mw_lag_1h, frame.load_mw.iloc[23])
        self.assertNotIn("event_active", " ".join(names))
        self.assertNotIn("target", names)

    def test_proxy_source_and_all_its_lags_are_excluded(self):
        frame = fixture(300)
        frame["lmp_usd_mwh"] = frame.load_mw / 100
        policy = {**POLICY, "label_method": "scarcity_price", "price_threshold_usd_mwh": 210}
        _, names = build_features(label_hours(frame, policy), policy)
        self.assertFalse(any("lmp" in name for name in names))

    def test_missing_hour_is_not_treated_as_adjacent_observation(self):
        frame = fixture(300).drop(index=100)
        features, _ = build_features(label_hours(frame, POLICY), POLICY)
        following_hour = pd.Timestamp("2023-01-05 05:00", tz="UTC")  # index 101
        self.assertNotIn(following_hour, features.timestamp_utc.to_list())

    def test_time_split_has_no_overlap_and_has_embargo(self):
        features, _ = build_features(label_hours(fixture(), POLICY), POLICY)
        split = chronological_split(features)
        self.assertGreater(split["calibration"].timestamp_utc.min() - split["train"].timestamp_utc.max(), pd.Timedelta(24, unit="h"))
        self.assertGreater(split["test"].timestamp_utc.min() - split["calibration"].timestamp_utc.max(), pd.Timedelta(24, unit="h"))

    def test_single_class_rejected(self):
        frame = fixture()
        frame.event_active = 0
        features, _ = build_features(label_hours(frame, POLICY), POLICY)
        with self.assertRaisesRegex(ValueError, "Collect more history"):
            chronological_split(features)


class TrainingTests(unittest.TestCase):
    def test_end_to_end_creates_real_model_with_test_fixture_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, policy_path, run = root / "hourly.csv", root / "policy.json", root / "run"
            fixture().to_csv(source, index=False)
            write_json(policy_path, POLICY)
            with patch("builtins.print"):
                report = train_run(source, policy_path, run, members=3, trees=30)
            self.assertTrue((run / "model.joblib").exists())
            self.assertTrue((run / "REPORT.md").exists())
            self.assertEqual(len(report["input_hashes"]["hourly_sha256"]), 64)
            self.assertGreater(report["brier_skill_vs_train_prevalence"], 0)
            self.assertEqual(report["confidence"]["TEST_ONLY_SPP_SYSTEM"]["level"], "Low")
            predictions = pd.read_parquet(run / "test_predictions.parquet")
            self.assertTrue(predictions.probability.between(0, 1).all())
            self.assertNotIn("site_exposure", predictions)
            json.loads((run / "model_card.json").read_text(encoding="utf-8"))
            with self.assertRaisesRegex(ValueError, "not empty"):
                train_run(source, policy_path, run, members=3, trees=30)

    def test_deterministic_and_test_labels_cannot_affect_fit(self):
        features, names = build_features(label_hours(fixture(), POLICY), POLICY)
        first, _, _ = fit_ensemble(features, names, members=2, trees=12)
        changed = features.copy()
        test_start = chronological_split(features)["test"].timestamp_utc.min()
        changed.loc[changed.timestamp_utc >= test_start, "target"] = 1 - changed.loc[changed.timestamp_utc >= test_start, "target"]
        second, _, _ = fit_ensemble(changed, names, members=2, trees=12)
        np.testing.assert_array_equal(predict_members(first, features), predict_members(second, features))


class PreparationTests(unittest.TestCase):
    def test_archive_duplicates_conflicts_and_missing_hours(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = pd.DataFrame({"MarketHour": ["01/01/2024 01:00", "01/01/2024 02:00", "01/01/2024 04:00"],
                                **{name: [100, 100, 100] for name in LOAD_AREAS}})
            conflict = raw.iloc[[1]].copy()
            conflict["SPS"] = 200
            raw = pd.concat([raw, raw.iloc[[0]], conflict], ignore_index=True)
            raw.to_parquet(root / "raw.parquet", index=False)
            report = normalize_legacy_load(root / "raw.parquet", root / "ready.parquet")
            result = read_hourly(root / "ready.parquet")
            self.assertEqual(report["exact_duplicate_rows_removed"], 1)
            self.assertEqual(report["conflicting_hours_marked_unknown"], 1)
            self.assertEqual(report["missing_hours_inserted_as_unknown"], 1)
            self.assertEqual(result.load_mw.iloc[0], 1700)
            self.assertTrue(result.load_mw.iloc[1:3].isna().all())
            self.assertEqual(result.timestamp_utc.iloc[0], pd.Timestamp("2024-01-01", tz="UTC"))

    def test_join_does_not_invent_negative_event_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            observations = fixture(100).drop(columns="event_active")
            evidence = fixture(100).iloc[:50].drop(columns="load_mw")
            observations.to_parquet(root / "observations.parquet", index=False)
            evidence.to_csv(root / "events.csv", index=False)
            report = join_evidence(root / "observations.parquet", root / "events.csv", root / "joined.parquet")
            result = read_hourly(root / "joined.parquet")
            self.assertEqual(report["unmatched_hours_left_unknown"], 50)
            self.assertTrue(result.event_active.iloc[50:].isna().all())
            with self.assertRaisesRegex(ValueError, "overwrite existing"):
                join_evidence(root / "joined.parquet", root / "events.csv", root / "bad.parquet")

    def test_join_rejects_mismatched_region_and_duplicate_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frame = fixture(100)
            frame.drop(columns="event_active").to_parquet(root / "hourly.parquet", index=False)
            evidence = frame.drop(columns="load_mw")
            evidence.location_id = "OTHER_OPERATOR"
            evidence.to_csv(root / "evidence.csv", index=False)
            with self.assertRaisesRegex(ValueError, "No matching"):
                join_evidence(root / "hourly.parquet", root / "evidence.csv", root / "out.parquet")
            pd.concat([evidence, evidence]).to_csv(root / "evidence.csv", index=False)
            with self.assertRaisesRegex(ValueError, "Duplicate evidence"):
                join_evidence(root / "hourly.parquet", root / "evidence.csv", root / "out.parquet")


class SimulationTests(unittest.TestCase):
    @staticmethod
    def predictions():
        times = pd.date_range("2023-01-01", "2024-01-03", freq="h", tz="UTC", inclusive="left")
        signal = (np.arange(len(times)) % 168 < 8).astype(int)
        probability = np.where(signal, .8, .02)
        return pd.DataFrame({"timestamp_utc": times, "location_id": "TEST_ONLY_SPP_SYSTEM",
                             "target": signal, "probability": probability,
                             "member_0": probability, "member_1": probability})

    def test_longest_episode(self):
        self.assertEqual(longest_run(np.array([1, 1, 0, 1, 1, 1])), 3)
        self.assertEqual(longest_run(np.zeros(10)), 0)
        self.assertEqual(longest_run(np.ones(10)), 10)

    def test_missing_seasons_and_too_few_trials_rejected(self):
        confidence = {"TEST_ONLY_SPP_SYSTEM": {"score": .9, "n_similar_historical_hours": 10}}
        with self.assertRaisesRegex(ValueError, "one year"):
            simulate_exposure(self.predictions().iloc[:1000], confidence, "test", simulations=1000, years=1)
        with self.assertRaisesRegex(ValueError, "1,000"):
            simulate_exposure(self.predictions(), confidence, "test", simulations=10, years=1)

    def test_simulation_contract_quantiles_determinism_and_zero_exposure(self):
        reference = self.predictions()
        confidence = {"TEST_ONLY_SPP_SYSTEM": {"score": .9, "n_similar_historical_hours": 10}}
        first, trials, metadata = simulate_exposure(reference, confidence, "test", simulations=1000, years=1)
        second, _, _ = simulate_exposure(reference, confidence, "test", simulations=1000, years=1)
        pd.testing.assert_frame_equal(first, second)
        row = first.iloc[0]
        self.assertLessEqual(row.p50_hours, row.p90_hours)
        self.assertLessEqual(row.p90_hours, row.p99_hours)
        self.assertLessEqual(row.p99_hours, 8760)
        self.assertEqual(len(trials), 1000)
        self.assertFalse(metadata["site_exposure_applied"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "exposure.parquet"
            first.to_parquet(path, index=False)
            estimate = get_location_estimate("TEST_ONLY_SPP_SYSTEM", path=path)
            self.assertEqual(set(estimate), {"location_id", "by_year", "confidence", "model_version"})
            with self.assertRaises(LocationNotFoundError):
                get_location_estimate("NOT_REAL", path=path)
            invalid = first.copy()
            invalid["p99_hours"] = -1
            invalid.to_parquet(path, index=False)
            with self.assertRaisesRegex(ValueError, "Invalid exposure"):
                get_location_estimate("TEST_ONLY_SPP_SYSTEM", path=path)
        reference[["target", "probability", "member_0", "member_1"]] = 0
        zero, _, _ = simulate_exposure(reference, confidence, "test", simulations=1000, years=1)
        self.assertEqual(zero.p99_hours.iloc[0], 0)


if __name__ == "__main__":
    unittest.main()
