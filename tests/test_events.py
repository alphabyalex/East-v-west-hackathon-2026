"""Evidence timing/coverage regressions; these fixtures are not training data."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd

from pipeline.events import annotate_hours, confirmed_eea_intervals, prepare_events, validate_catalog
from pipeline.ingest import fetch_public_evidence


def event(**overrides):
    row = dict(id="test", kind="EEA1", region_id="SPP_BA_PRE_2026", time_precision="interval",
               end_status="confirmed", start="2024-08-26T12:30:00-05:00", end="2024-08-26T15:00:00-05:00",
               source_ids=["fixture"])
    return row | overrides


def catalog(events):
    return {"events": events, "sources": {"fixture": {"ref": "https://spp.org/test-only.pdf",
            "sha256": "a" * 64, "retrieved_utc": "2026-09-13T00:00:00Z"}}}


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame({"timestamp_utc": pd.date_range("2024-08-26T16:00Z", periods=5, freq="h"),
                                   "location_id": "SPP_SYSTEM", "load_mw": 1000.0})

    def join(self, events):
        return annotate_hours(self.frame, catalog(events), "SPP_SYSTEM", "SPP_BA_PRE_2026")

    def test_partial_hours_and_unknowns(self):
        result = self.join([event()])
        self.assertEqual(result.observed_eea_minutes.dropna().tolist(), [30, 60, 60])
        self.assertEqual(result.observed_eea_minutes.sum() / 60, 2.5)
        self.assertTrue(pd.isna(result.observed_eea_minutes.iloc[-1]))
        self.assertNotIn("event_active", result)
        pd.testing.assert_frame_equal(result[self.frame.columns], self.frame)

    def test_duplicate_and_overlapping_intervals_are_unioned(self):
        result = self.join([event(), event(id="second", start="2024-08-26T13:00:00-05:00")])
        self.assertEqual(result.observed_eea_minutes.sum(), 150)

    def test_other_regions_date_only_and_anticipated_ends_are_not_hours(self):
        rows = [event(region_id="SPP_WEST_BAA"), event(kind="LOCAL_LOAD_SHED"),
                event(time_precision="date"), event(end_status="anticipated")]
        for i, row in enumerate(rows):
            row["id"] = f"excluded_{i}"
        self.assertTrue(self.join(rows).observed_eea_minutes.isna().all())

    def test_repeated_dataframe_index_does_not_mix_distinct_hours(self):
        self.frame.index = [0] * len(self.frame)
        result = self.join([event()])
        self.assertEqual(result.observed_eea_minutes.dropna().tolist(), [30, 60, 60])
        self.assertTrue(result.observed_eea_minutes.iloc[[0, 4]].isna().all())
        pd.testing.assert_frame_equal(result[self.frame.columns], self.frame)

    def test_duplicate_naive_missing_and_nonhourly_timestamps_are_rejected(self):
        original = self.frame.copy()
        for values in [original.timestamp_utc.dt.tz_localize(None),
                       original.timestamp_utc + pd.Timedelta(1, unit="m"),
                       pd.Series([pd.NaT] * 5, dtype="datetime64[ns, UTC]"),
                       pd.Series([original.timestamp_utc.iloc[0]] * 5)]:
            self.frame["timestamp_utc"] = values
            with self.assertRaises(ValueError):
                self.join([event()])

    def test_catalog_provenance_and_event_identity_are_required(self):
        valid = catalog([event()])
        broken = [None, {}, valid | {"sources": {}}, catalog([event(), event()]),
                  catalog([event(source_ids=["missing"])]), catalog([event(source_ids=[])]),
                  catalog([event(source_ids=["fixture", "fixture"])]), catalog([event(id="a;b")])]
        for field, value in [("sha256", "bad"), ("ref", "http://spp.org/test.pdf"),
                             ("retrieved_utc", "2026-01-01"), ("retrieved_utc", None)]:
            changed = copy.deepcopy(valid)
            changed["sources"]["fixture"][field] = value
            broken.append(changed)
        for value in broken:
            with self.subTest(catalog=value), self.assertRaises(ValueError):
                validate_catalog(value)

    def test_reviewed_winter_intervals_preserve_gaps_and_levels(self):
        path = Path(__file__).resolve().parents[1] / "docs/spp-event-evidence.json"
        evidence = json.loads(path.read_text(encoding="utf-8"))
        intervals = confirmed_eea_intervals(evidence, "SPP_BA_PRE_2026")
        totals = {}
        for start, end, _ in intervals:
            totals[start.year] = totals.get(start.year, 0) + (end-start).total_seconds() / 60
        self.assertAlmostEqual(totals[2021], 5485)
        self.assertAlmostEqual(totals[2022], 273)
        self.assertAlmostEqual(totals[2024], 150)
        self.frame = pd.DataFrame({"timestamp_utc": pd.date_range("2021-02-18T15:00Z", periods=11, freq="h"),
                                   "location_id": "SPP_SYSTEM", "load_mw": 1000.0})
        result = annotate_hours(self.frame, evidence, "SPP_SYSTEM", "SPP_BA_PRE_2026")
        self.assertEqual(result.observed_eea_minutes.iloc[0], 30)
        self.assertTrue(result.observed_eea_minutes.iloc[1:9].isna().all())
        self.assertEqual(result.observed_eea_minutes.iloc[9], 35)

    def test_2019_local_clock_record_is_not_mapped_to_utc_without_a_verified_timezone(self):
        path = Path(__file__).resolve().parents[1] / "docs/spp-event-evidence.json"
        evidence = json.loads(path.read_text(encoding="utf-8"))
        candidate = next(row for row in evidence["events"] if row["id"] == "spp_20190806_eea1_local_clock")
        self.assertEqual(candidate["reported_start_clock"], "14:45")
        self.assertEqual(candidate["reported_end_clock"], "19:00")
        self.assertEqual(candidate["timezone"], "unverified")
        intervals = confirmed_eea_intervals(evidence, "SPP_BA_PRE_2026")
        self.assertNotIn(candidate["id"], [identifier for _, _, identifier in intervals])
        self.frame = pd.DataFrame({"timestamp_utc": pd.date_range("2019-08-06T00:00Z", periods=48, freq="h"),
                                   "location_id": "SPP_SYSTEM", "load_mw": 1000.0})
        joined = annotate_hours(self.frame, evidence, "SPP_SYSTEM", "SPP_BA_PRE_2026")
        self.assertTrue(joined.observed_eea_minutes.isna().all())

    def test_cli_preparation_preserves_unknowns_and_does_not_create_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            hourly, evidence, out = folder / "input.parquet", folder / "catalog.json", folder / "output.parquet"
            self.frame.to_parquet(hourly, index=False)
            evidence.write_text(json.dumps(catalog([event()])), encoding="utf-8")
            result = prepare_events(hourly, evidence, out)
            self.assertEqual(result["observed_eea_hours"], 2.5)
            self.assertEqual(result["confirmed_negative_hours"], 0)
            self.assertFalse(result["training_labels_created"])
            self.assertNotIn("event_active", pd.read_parquet(out))
            with self.assertRaises(ValueError):
                prepare_events(hourly, evidence, out)

    def test_invalid_timezone_or_duration_is_rejected(self):
        for changes in [dict(start="2024-08-26T12:30:00"), dict(end="2024-08-26T12:00:00-05:00")]:
            with self.assertRaises(ValueError):
                self.join([event(**changes)])

    def test_explicit_mapping_and_existing_data_are_protected(self):
        with self.assertRaises(ValueError):
            annotate_hours(self.frame, {"events": []}, "SPP_SYSTEM", "SPP_WEST_BAA")
        self.frame["observed_eea_minutes"] = 1
        with self.assertRaises(ValueError):
            self.join([event()])

    def test_cache_reuse_and_key_collision(self):
        url = "https://spp.org/example.pdf"
        response = Mock(content=b"public evidence", url=url, headers={"Content-Type": "application/pdf"})
        with tempfile.TemporaryDirectory() as tmp, patch("requests.get", return_value=response) as get:
            first = fetch_public_evidence(url, "example", cache_dir=Path(tmp))
            second = fetch_public_evidence(url, "example", cache_dir=Path(tmp))
            self.assertEqual(first, second)
            get.assert_called_once()
            with self.assertRaises(ValueError):
                fetch_public_evidence("https://spp.org/other.pdf", "example", cache_dir=Path(tmp))

    def test_invalid_source_or_cache_key_is_rejected_without_fetch(self):
        with patch("requests.get") as get:
            for url, key in [("http://spp.org/x", "x"), ("https://example.org/x", "x"), ("https://spp.org/x", "../x")]:
                with self.assertRaises(ValueError):
                    fetch_public_evidence(url, key)
            get.assert_not_called()

    def test_corrupt_cached_evidence_is_rejected_without_network_or_overwrite(self):
        url = "https://spp.org/example.pdf"
        response = Mock(content=b"public evidence", url=url, headers={"Content-Type": "application/pdf"})
        with tempfile.TemporaryDirectory() as tmp, patch("requests.get", return_value=response) as get:
            path = Path(tmp) / "example.parquet"
            fetch_public_evidence(url, "example", cache_dir=Path(tmp))
            original = pd.read_parquet(path)
            versions = [original.iloc[:0], pd.concat([original, original]), original.drop(columns="source_json")]
            altered = original.copy()
            altered.loc[0, "content"] = b"altered evidence"
            versions.append(altered)
            for field, value in [("sha256", "b"*64), ("requested_url", url+"wrong"),
                                 ("retrieved_utc", None), ("ref", "http://spp.org/test.pdf")]:
                changed = original.copy()
                source = json.loads(changed.source_json.iloc[0])
                source[field] = value
                changed.loc[0, "source_json"] = json.dumps(source)
                versions.append(changed)
            for version in versions:
                version.to_parquet(path, index=False)
                before = path.read_bytes()
                with self.assertRaises(ValueError):
                    fetch_public_evidence(url, "example", cache_dir=Path(tmp))
                self.assertEqual(before, path.read_bytes())
            get.assert_called_once()


if __name__ == "__main__":
    unittest.main()
