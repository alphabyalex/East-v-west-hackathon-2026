"""Synthetic fixtures for geographic and nearby-data behavior; no external calls."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
import requests

from pipeline.common import write_json
from pipeline.location_data import (city_candidates, find_census_city, geometry_distance_km,
    regional_coverage, split_city_state, western_match, cached_json)
from pipeline.location_weather import prepare_query_weather, validate_weather, cached_weather_points
from pipeline.site import run_site_job, search_location


class LocationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.point = {"name": "Requested city", "latitude": 38.8, "longitude": -104.8, "weight": 1.0}
        self.exact = {"status": "unverified", "territories": [], "source": {"ref": "test://exact"}}
        self.square = {"type": "Polygon", "coordinates": [
            [[-101, 36], [-99, 36], [-99, 38], [-101, 38], [-101, 36]],
            [[-100.2, 36.8], [-99.8, 36.8], [-99.8, 37.2], [-100.2, 37.2], [-100.2, 36.8]] ]}

    def test_city_state_formats_and_aliases(self):
        for query in ("Colorado Springs CO", "Colorado Springs, CO", "Colorado Springs Colorado", "Colorado Springs, Colorado, USA"):
            self.assertEqual(split_city_state(query), ("Colorado Springs", "Colorado"))
        self.assertEqual(split_city_state("Kansas City"), ("Kansas City", None))
        with self.assertRaises(ValueError):
            split_city_state("Paris, France")
        frame = pd.DataFrame([{"USPS": code, "NAME": "St. Francis city", "search_name": "SAINT FRANCIS",
                               "INTPTLAT": lat, "INTPTLONG": -101.8, "GEOID": "001"}
                              for code, lat in (("KS", 39.7), ("MN", 45.4))])
        with patch("pipeline.location_data.census_places", return_value=(frame, {"ref": "test://census"})):
            points, _ = find_census_city("Saint Francis", "Kansas")
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]["latitude"], 39.7)

    def test_census_fallback_on_unavailable_or_nonexact_primary_city(self):
        nearby = pd.DataFrame([{"name": "Wrong Nearby City", "admin1": "Colorado", "country_code": "US"}])
        for response in (requests.ConnectionError("offline"), (nearby, {})):
            kwargs = {"side_effect": response} if isinstance(response, Exception) else {"return_value": response}
            with patch("pipeline.ingest.search_weather_areas", **kwargs), patch("pipeline.location_data.find_census_city", return_value=([self.point], {"ref": "test://census"})):
                scan = search_location("Requested city CO")
                self.assertEqual(scan["query"], "Requested city CO")
                self.assertEqual(scan["candidates"], [self.point])

    def test_polygon_holes_edges_and_multipolygon(self):
        distance = lambda lat, lon, geometry=self.square: geometry_distance_km({"latitude": lat, "longitude": lon}, geometry)
        self.assertEqual(distance(37, -100.5), 0)
        self.assertEqual(distance(36, -100), 0)
        self.assertGreater(distance(37, -100), 10)  # hole is not in the region
        self.assertLess(distance(38.1, -100), 12)
        self.assertGreater(distance(40, -100), 200)
        multi = {"type": "MultiPolygon", "coordinates": [self.square["coordinates"]]}
        self.assertEqual(distance(37, -100.5, multi), 0)

    def test_historical_region_admits_nearby_point_but_not_distant_grid(self):
        payload = {"features": [{"geometry": self.square}]}
        with patch("pipeline.location_data.cached_json", return_value=(payload, {"ref": "test://region"})):
            near = regional_coverage({"latitude": 38.1, "longitude": -100}, self.exact)
            far = regional_coverage({"latitude": 29.7, "longitude": -95.4}, {**self.exact, "status": "other_grid_match"})
        self.assertTrue(near["eligible"])
        self.assertTrue(near["approximate"])
        self.assertFalse(far["eligible"])

    def test_western_members_and_nearby_utility_region(self):
        self.assertTrue(western_match({"name": "CITY OF COLORADO SPRINGS - (CO)", "state": "CO"}))
        self.assertTrue(western_match({"name": "MORA-SAN MIGUEL ELECTRIC COOP", "state": "NM"}))
        self.assertFalse(western_match({"name": "DIXIE ELECTRIC", "state": "MS"}))
        self.assertFalse(western_match({"name": "NORTHWESTERN ENERGY", "state": "MT"}))
        nearby = {"features": [{"attributes": {"name": "POUDRE VALLEY RURAL ELECTRIC", "state": "CO"}}]}
        with patch("pipeline.location_data.cached_json", side_effect=[({"features": []}, {}), (nearby, {"ref": "test://nearby"})]):
            coverage = regional_coverage(self.point, self.exact)
        self.assertTrue(coverage["eligible"])
        self.assertIn("SPP East", coverage["interpretation"])
        self.assertEqual(coverage["match_method"], "western_participant_region")

    def test_cached_map_and_truncation(self):
        response = Mock(content=b"real response bytes", url="https://example.test/map")
        response.json.return_value = {"features": []}
        with patch("requests.get", return_value=response) as get:
            first = cached_json(response.url, {"f": "json"}, cache_dir=self.root)
            second = cached_json(response.url, {"f": "json"}, cache_dir=self.root)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(first, second)
        self.assertIn("sha256", first[1])
        response.json.return_value = {"features": [], "exceededTransferLimit": True}
        with patch("requests.get", return_value=response), self.assertRaises(ValueError):
            cached_json(response.url, {"f": "geojson"}, cache_dir=self.root)

    def test_regional_job_generates_without_manual_confirmation(self):
        request = {"kind": "site-transfer", "scan": {"source": {}}, "point": self.point,
                   "load_mw": 100, "conditional_share": 1, "site_exposure": .25, "years": 3}
        write_json(self.root / "request.json", request)
        coverage = {**self.exact, "eligible": True, "approximate": True, "status": "regional_spp_comparison", "interpretation": "Regional comparison"}
        with patch("pipeline.ingest.fetch_utility_territories", return_value=({"features": []}, {})), \
             patch("pipeline.location_data.regional_coverage", return_value=coverage), \
             patch("pipeline.location_weather.prepare_query_weather", return_value={"note": "Nearby temperature"}), \
             patch("pipeline.regional.transfer_report", return_value={"limitations": []}) as transfer, \
             patch("pipeline.site.write_report") as save:
            run_site_job(self.root / "request.json", self.root / "out")
        self.assertFalse(transfer.call_args.args[3]["confirm_spp"])
        self.assertEqual(transfer.call_args.args[0], self.point)
        self.assertIn("Nearby temperature", save.call_args.args[1]["location_data_note"])

    def test_weather_fallback_keeps_query_and_provenance(self):
        frame = pd.DataFrame({"timestamp_utc": pd.date_range("2024-01-01", periods=48, freq="h", tz="UTC"),
                              "temperature_c": 25., "target": float("nan"), "location_id": "QUERY_WEATHER_POINT"})
        nearby = {"latitude": 38.75, "longitude": -104.75}
        def weather(point, folder):
            if point["latitude"] == self.point["latitude"]:
                raise requests.ConnectionError("exact unavailable")
            folder.mkdir(parents=True)
            frame.to_parquet(folder / "weather.parquet", index=False)
            frame.to_parquet(folder / "grid.parquet", index=False)
            write_json(folder / "mapping.json", {"point": point})
            write_json(folder / "weather.weather.json", {"raw_sources": [{"ref": "test://weather", "returned_grid_latitude": 38.75, "returned_grid_longitude": -104.75}]})
            return frame
        with patch("pipeline.regional.base_data", return_value=self.root / "base"), \
             patch("pipeline.common.read_hourly", return_value=frame), \
             patch("pipeline.regional.point_weather", side_effect=weather) as fetch, \
             patch("pipeline.location_weather.cached_weather_points", return_value=[nearby]):
            match = prepare_query_weather(self.point, self.root / "out")
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(match["requested_location"], self.point)
        self.assertEqual(match["method"], "nearby_cached_weather")
        self.assertLess(match["request_distance_km"], 10)
        result = pd.read_parquet(self.root / "out/query/weather.parquet")
        self.assertTrue(result.target.isna().all())
        self.assertEqual(set(result.location_id), {"QUERY_WEATHER_POINT"})
        manifest = json.loads((self.root / "out/query/weather.weather.json").read_text())
        self.assertEqual(manifest["location_match"], match)
        self.assertEqual(manifest["raw_sources"][0]["ref"], "test://weather")
        missing = frame.copy()
        missing.loc[:1, "temperature_c"] = float("nan")
        with self.assertRaises(ValueError):
            validate_weather(missing)

    def test_cached_weather_requires_all_years_and_nearby_coordinates(self):
        folder = self.root / "data/raw/weather/open_meteo"
        folder.mkdir(parents=True)
        for i, (lat, year) in enumerate(((38.75, 2023), (38.75, 2024), (38.8, 2024), (45, 2023), (45, 2024))):
            write_json(folder / f"{i}.source.json", {"request_parameters": {"models": "era5", "latitude": lat, "longitude": -104.75, "start_date": f"{year}-01-01"}, "missing_temperature_hours": 0})
            (folder / f"{i}.parquet").write_bytes(b"fixture")
        with patch("pipeline.location_weather.ROOT", self.root):
            candidates = cached_weather_points(self.point, {2023, 2024})
        self.assertEqual(candidates, [{"latitude": 38.75, "longitude": -104.75}])


if __name__ == "__main__":
    unittest.main()
