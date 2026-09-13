"""Evidence timing/coverage regressions; these fixtures are not training data."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd

from pipeline.events import annotate_hours
from pipeline.ingest import fetch_public_evidence


def event(**overrides):
    row = dict(id="test", kind="EEA1", region_id="SPP_BA_PRE_2026", time_precision="interval",
               end_status="confirmed", start="2024-08-26T12:30:00-05:00", end="2024-08-26T15:00:00-05:00")
    return row | overrides


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame({"timestamp_utc": pd.date_range("2024-08-26T16:00Z", periods=5, freq="h"),
                                   "location_id": "SPP_SYSTEM", "load_mw": 1000.0})

    def join(self, events):
        return annotate_hours(self.frame, {"events": events}, "SPP_SYSTEM", "SPP_BA_PRE_2026")

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
        self.assertTrue(self.join(rows).observed_eea_minutes.isna().all())

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


if __name__ == "__main__":
    unittest.main()
