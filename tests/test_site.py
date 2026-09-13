"""Location/report boundaries. Synthetic fixtures are temporary and never shipped as data."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd

from pipeline.common import write_json
from pipeline.ingest import fetch_utility_territories
from pipeline.site import (area_model_key, classify_coverage, run_site_job, scenario_summary,
                           search_location, validate_assumptions, write_report)
from pipeline.workbench import Workspace


class SiteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = {"load_mw": 100, "conditional_share": .5, "site_exposure": .25, "years": 2}

    def test_arbitrary_coordinates_and_out_of_range(self):
        with patch("requests.get", side_effect=AssertionError("Coordinates need no geocoder")):
            point = search_location("37.123456, -97.654321")["candidates"][0]
            self.assertEqual(point["latitude"], 37.123456)
            self.assertEqual(point["longitude"], -97.654321)
        for query in ("91, 20", "37, -181", "nan, 10", "inf, 2", "a\nb", ""):
            with self.subTest(query=query), self.assertRaises(ValueError):
                search_location(query)

    def test_city_search_preserves_choices_and_state_filter(self):
        frame = pd.DataFrame([{"id": i, "name": "Example City", "admin1": state, "latitude": lat,
                               "longitude": -98.0, "country_code": "US"}
                              for i, state, lat in ((1, "Kansas", 37), (2, "Nebraska", 41))])
        with patch("pipeline.ingest.search_weather_areas", return_value=(frame, {"ref": "test://geocoding"})):
            self.assertEqual(len(search_location("Example City")["candidates"]), 2)
            chosen = search_location("Example City, NE")
        self.assertEqual(chosen["candidates"][0]["latitude"], 41)
        self.assertEqual(chosen["source"]["ref"], "test://geocoding")

    def test_utility_download_cached_with_provenance(self):
        response = Mock(content=b"source bytes", url="https://eedgis.pnnl.gov/test")
        response.json.return_value = {"features": [{"attributes": {"cntrl_area": "SOUTHWEST POWER POOL", "year": "2020"}}]}
        with patch("requests.get", return_value=response) as get:
            first = fetch_utility_territories(37, -97, cache_dir=self.root)
            second = fetch_utility_territories(37, -97, cache_dir=self.root)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(first, second)
        self.assertIn("sha256", first[1])
        self.assertEqual(classify_coverage(first[0])["status"], "historical_spp_match")
        self.assertEqual(classify_coverage({"features": []})["status"], "unverified")
        self.assertEqual(classify_coverage({"features": [{"attributes": {"cntrl_area": "PJM INTERCONNECTION"}}]})["status"], "other_grid_match")

    def test_hours_and_energy_are_distinct_and_joint_totals_correct(self):
        trials = pd.DataFrame([{"location_id": "SPP_SYSTEM", "simulation_id": i, "year_offset": year,
                                "modeled_exposure_hours": hours}
                               for i in range(1000) for year, hours in ((1, 100), (2, 200), (3, 300))])
        result = scenario_summary(trials, self.settings)
        self.assertEqual(result["annual"][0]["site_p50_hours"], 25)
        self.assertEqual(result["annual"][0]["conditional_p50_mwh"], 1250)
        self.assertEqual(result["site_term"]["p50_total_hours"], 75)
        doubled = scenario_summary(trials, {**self.settings, "load_mw": 200})
        self.assertEqual(doubled["annual"][0]["site_p50_hours"], 25)
        self.assertEqual(doubled["annual"][0]["conditional_p50_mwh"], 2500)
        zero = scenario_summary(trials, {**self.settings, "site_exposure": 0})
        self.assertEqual(zero["site_term"]["p99_total_hours"], 0)
        for name, value in (("load_mw", float("nan")), ("site_exposure", 1.1), ("years", True), ("conditional_share", -1), ("confirm_spp", "yes")):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_assumptions({**self.settings, name: value})

    def test_wrong_grid_never_trains_even_with_assumption(self):
        scan = search_location("40, -75")
        request = {"kind": "site-report", "scan": scan, "point": scan["candidates"][0], **self.settings, "confirm_spp": True}
        request_path = self.root / "request.json"
        write_json(request_path, request)
        response = ({"features": [{"attributes": {"cntrl_area": "PJM"}}]}, {"ref": "test://map"})
        with patch("pipeline.ingest.fetch_utility_territories", return_value=response), patch("pipeline.site.prepare_area_model", side_effect=AssertionError("Wrong grid cannot train")):
            run_site_job(request_path, self.root / "out")
        result = json.loads((self.root / "out/site_report.json").read_text())
        self.assertEqual(result["status"], "coverage_review_required")
        self.assertNotIn("scenario", result)

    def test_unknown_coverage_needs_explicit_assumption(self):
        scan = search_location("37, -97")
        request = {"kind": "site-report", "scan": scan, "point": scan["candidates"][0], **self.settings}
        path = self.root / "request.json"
        write_json(path, request)
        with patch("pipeline.ingest.fetch_utility_territories", return_value=({"features": []}, {})), patch("pipeline.site.prepare_area_model") as fit:
            run_site_job(path, self.root / "out")
            fit.assert_not_called()

    def test_cache_changes_for_coordinates_or_base_data(self):
        base = self.root / "base.parquet"
        base.write_bytes(b"original input")
        point = {"latitude": 37, "longitude": -97}
        first, _ = area_model_key(point, base)
        same, _ = area_model_key({**point, "name": "Different display name"}, base)
        other, _ = area_model_key({**point, "latitude": 38}, base)
        base.write_bytes(b"updated input")
        changed, _ = area_model_key(point, base)
        self.assertEqual(first, same)
        self.assertNotEqual(first, other)
        self.assertNotEqual(first, changed)

    def test_explicit_assumption_is_preserved_for_unverified_coverage(self):
        scan = search_location("37, -97")
        request = {"kind": "site-report", "scan": scan, "point": scan["candidates"][0], **self.settings, "confirm_spp": True}
        path = self.root / "request.json"
        write_json(path, request)
        with patch("pipeline.ingest.fetch_utility_territories", return_value=({"features": []}, {})), \
             patch("pipeline.site.prepare_area_model", return_value=(self.root, True, {})) as fit, \
             patch("pipeline.site.analyzed_report", return_value={}) as report, patch("pipeline.site.write_report"), \
             patch("pipeline.signals.explain_saved_run", return_value={}):
            run_site_job(path, self.root / "out")
        fit.assert_called_once()
        self.assertTrue(report.call_args.args[3]["confirm_spp"])
        self.assertEqual(report.call_args.args[1]["status"], "unverified")

    def test_reports_escape_location_and_workspace_only_uses_saved_candidates(self):
        workspace = Workspace(self.root)
        folder = workspace.home / "sites/fixture"
        write_json(folder / "scan.json", search_location("37, -97"))
        scan_id = workspace.relative(folder)
        with patch("threading.Thread.start"):
            job = workspace.submit({"kind": "site-report", "scan": scan_id, "candidate": 0,
                                    "point": {"latitude": 99}, **self.settings})
        saved = json.loads((workspace.resolve(job["log_path"]).parent / "site_request.json").read_text())
        self.assertEqual(saved["point"]["latitude"], 37)
        workspace.job = None
        with self.assertRaises(ValueError):
            workspace.submit({"kind": "site-report", "scan": "../../secret", "candidate": 0, **self.settings})
        with self.assertRaises(ValueError):
            workspace.submit({"kind": "site-report", "scan": scan_id, "candidate": -1, **self.settings})
        report = {"status": "coverage_review_required", "location": {"name": "<script>alert(1)</script>", "latitude": 37, "longitude": -97},
                  "coverage": {"status": "unverified", "interpretation": "test"}, "message": "No hours calculated."}
        write_report(folder, report)
        self.assertNotIn("<script>", (folder / "SITE_REPORT.html").read_text())
        with patch("requests.get", side_effect=AssertionError("No fetch on reads")), patch("pipeline.workbench.run_command", side_effect=AssertionError("No jobs on reads")):
            self.assertEqual(len(workspace.snapshot()["site_reports"]), 1)
            self.assertEqual(workspace.site_record(scan_id, "site_report.json")["status"], "coverage_review_required")


if __name__ == "__main__":
    unittest.main()
