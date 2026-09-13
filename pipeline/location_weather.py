"""Bounded nearby-weather fallback without substituting another area's load/labels."""
from __future__ import annotations

import json
import math
import shutil

import numpy as np
import requests

from pipeline.common import ROOT, write_json
from pipeline.location_data import distance_km

MAX_WEATHER_DISTANCE_KM = 100.0
MIN_MONTHLY_COVERAGE = .99


def cached_weather_points(point, years):
    groups = {}
    for path in (ROOT / "data/raw/weather/open_meteo").glob("*.source.json"):
        try:
            source = json.loads(path.read_text(encoding="utf-8"))
            params = source["request_parameters"]
            if params.get("models") != "era5" or not path.with_name(path.name.replace(".source.json", ".parquet")).exists():
                continue
            coords = float(params["latitude"]), float(params["longitude"])
            candidate = {"latitude": coords[0], "longitude": coords[1]}
            distance = distance_km(point, candidate)
            if not 0 < distance <= MAX_WEATHER_DISTANCE_KM:
                continue
            group = groups.setdefault(coords, {"point": candidate, "years": set(), "distance": distance})
            if source.get("missing_temperature_hours", 0) == 0:
                group["years"].add(int(params["start_date"][:4]))
        except (ValueError, KeyError, TypeError):
            continue
    return [row["point"] for row in sorted(groups.values(), key=lambda row: row["distance"]) if years <= row["years"]][:3]


def nearby_points(point):
    lat, lon = point["latitude"], point["longitude"]
    grid_lat, grid_lon = round(lat * 4) / 4, round(lon * 4) / 4
    candidates = [{"latitude": grid_lat + dy, "longitude": grid_lon + dx}
                  for dy in (-.25, 0, .25) for dx in (-.25, 0, .25)]
    return sorted([row for row in candidates if 0 < distance_km(point, row) <= MAX_WEATHER_DISTANCE_KM],
                  key=lambda row: distance_km(point, row))[:3]


def validate_weather(frame):
    if frame.empty or "temperature_c" not in frame or not np.isfinite(frame.temperature_c.dropna()).all():
        raise ValueError("Weather has no usable hourly temperatures.")
    coverage = frame.temperature_c.notna().groupby(frame.timestamp_utc.dt.strftime("%Y-%m")).mean()
    if coverage.empty or coverage.min() < MIN_MONTHLY_COVERAGE:
        raise ValueError("Weather coverage is below 99% in at least one historical month.")
    return float(coverage.min())


def prepare_query_weather(point, out):
    from pipeline.regional import base_data, point_weather
    from pipeline.common import read_hourly
    grid = read_hourly(base_data())
    years = set(grid.timestamp_utc.dt.year)
    attempts, seen = [], set()
    # Exact coordinates first. Nearby cached points are considered only on failure.
    candidates = [(point, "requested_location")]
    while candidates:
        candidate, method = candidates.pop(0)
        key = candidate["latitude"], candidate["longitude"]
        if key in seen:
            continue
        seen.add(key)
        candidate = {"name": point["name"] if method == "requested_location" else f"Nearby weather ({key[0]:.5f}, {key[1]:.5f})",
                     "latitude": key[0], "longitude": key[1], "weight": 1.0}
        folder = out / "weather_attempts" / str(len(seen))
        try:
            print(f"Checking historical temperature at {candidate['name']}.", flush=True)
            frame = point_weather(candidate, folder)
            completeness = validate_weather(frame)
            manifest = json.loads((folder / "weather.weather.json").read_text(encoding="utf-8"))
            cells = []
            for raw in manifest["raw_sources"]:
                source = raw.get("source", raw)
                lat, lon = source.get("returned_grid_latitude"), source.get("returned_grid_longitude")
                if lat is not None and lon is not None and math.isfinite(lat) and math.isfinite(lon):
                    cell = {"latitude": lat, "longitude": lon}
                    cell["distance_km"] = round(distance_km(point, cell), 1)
                    if cell["distance_km"] > MAX_WEATHER_DISTANCE_KM:
                        raise ValueError("Provider weather grid is more than 100 km from the requested site.")
                    if cell not in cells:
                        cells.append(cell)
            offset = round(distance_km(point, candidate), 1)
            note = (f"Weather: ERA5 grid near your selected location."
                    if method == "requested_location" else f"Weather: nearby data {offset:g} km from your selected location.")
            match = {"requested_location": point, "weather_request_location": candidate, "method": method,
                     "request_distance_km": offset, "returned_grid_cells": cells, "note": note,
                     "minimum_monthly_coverage": completeness, "attempts": attempts,
                     "policy": {"source_type": "assumption", "maximum_distance_km": MAX_WEATHER_DISTANCE_KM,
                                "minimum_monthly_coverage": MIN_MONTHLY_COVERAGE,
                                "ref": "Use one complete local/nearby ERA5 history, closest cached point first on failure; never replace query-area load or labels."}}
            destination = out / "query"
            destination.mkdir(parents=True, exist_ok=True)
            for name in ("weather.parquet", "grid.parquet", "mapping.json"):
                shutil.copyfile(folder / name, destination / name)
            manifest["location_match"] = match
            manifest["output_path"] = str(destination / "weather.parquet")
            write_json(destination / "weather.weather.json", manifest)
            return match
        except (requests.RequestException, ValueError) as error:
            attempts.append({"point": candidate, "method": method, "error": str(error)})
            print("That weather point was unavailable; checking nearby data.", flush=True)
            if method == "requested_location":
                candidates.extend((row, "nearby_cached_weather") for row in cached_weather_points(point, years))
                candidates.extend((row, "nearby_weather_grid") for row in nearby_points(point))
    raise ValueError("Historical weather was unavailable at the site and nearby points within 100 km. No missing weather was replaced with invented values. Try again when the weather service is available.")
