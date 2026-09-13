"""Explicit location mapping and hourly joins for historical temperature data.

Network access lives in ingest.py. This module never infers site curtailment from
temperature; the classifier learns any association from independently supplied labels.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline.common import ROOT, fingerprint, read_hourly, write_json

TEMPERATURE_COLUMNS = ("temperature_c", "temperature_area_min_c", "temperature_area_max_c")
STATE_NAMES = dict(item.split(":") for item in (
    "AL:Alabama|AK:Alaska|AZ:Arizona|AR:Arkansas|CA:California|CO:Colorado|CT:Connecticut|DE:Delaware|"
    "DC:District of Columbia|FL:Florida|GA:Georgia|HI:Hawaii|ID:Idaho|IL:Illinois|IN:Indiana|IA:Iowa|"
    "KS:Kansas|KY:Kentucky|LA:Louisiana|ME:Maine|MD:Maryland|MA:Massachusetts|MI:Michigan|MN:Minnesota|"
    "MS:Mississippi|MO:Missouri|MT:Montana|NE:Nebraska|NV:Nevada|NH:New Hampshire|NJ:New Jersey|"
    "NM:New Mexico|NY:New York|NC:North Carolina|ND:North Dakota|OH:Ohio|OK:Oklahoma|OR:Oregon|"
    "PA:Pennsylvania|RI:Rhode Island|SC:South Carolina|SD:South Dakota|TN:Tennessee|TX:Texas|UT:Utah|"
    "VT:Vermont|VA:Virginia|WA:Washington|WV:West Virginia|WI:Wisconsin|WY:Wyoming"
).split("|"))


def select_area(candidates: pd.DataFrame, area: str) -> dict:
    """Never choose an ambiguous city by population or silently ignore its state."""
    parts = [part.strip() for part in area.split(",")]
    if len(parts) > 2 or not parts[0] or (len(parts) == 2 and not parts[1]):
        raise ValueError('Use a city or "City, State", for example "Amarillo, TX".')
    city = parts[0]
    matched = candidates[candidates.country_code.eq("US") & candidates.name.astype("string").str.casefold().eq(city.casefold())]
    if len(parts) == 2:
        state = STATE_NAMES.get(parts[1].upper(), parts[1])
        matched = matched[matched.admin1.astype("string").str.casefold().eq(state.casefold())]
    matched = matched.drop_duplicates(["latitude", "longitude"])
    if len(matched) != 1:
        examples = "; ".join(f"{row.name}, {row.admin1}" for row in candidates.head(6).itertuples())
        raise ValueError(f'Area "{area}" did not identify exactly one US city. Enter a more specific city/state. Candidates: {examples or "none"}.')
    chosen = matched.iloc[0]
    return {"name": f"{chosen['name']}, {chosen.admin1}", "latitude": float(chosen.latitude),
            "longitude": float(chosen.longitude), "weight": 1.0, "geonames_id": int(chosen.id)}


def create_area_mapping(area: str, hourly_path: Path, mapping_path: Path, *, location_id: str | None = None,
                        cache_dir: Path | None = None) -> tuple[dict, str]:
    from pipeline.ingest import search_weather_areas

    hourly = read_hourly(hourly_path)
    identifiers = sorted(hourly.location_id.unique())
    if location_id is None:
        if len(identifiers) != 1:
            raise ValueError(f"Input contains several grid IDs; choose --location-id from {identifiers}.")
        location_id = identifiers[0]
    if location_id not in identifiers:
        raise ValueError(f"Unknown grid location_id {location_id}.")
    city = area.split(",")[0].strip()
    candidates, source = search_weather_areas(city, cache_dir=cache_dir or ROOT / "data/raw/weather/geocoding")
    point = select_area(candidates, area)
    config = {
        "operator": "SPP", "source_type": "assumption",
        "ref": f'User-specified weather area "{area}" associated with grid dataset {location_id}; this association does not verify grid-node or site applicability.',
        "area_input": area, "geocoding_source": source,
        "locations": {location_id: {"description": f"Temperature at {point['name']} used as an input to {location_id} stress modeling.",
                                     "points": [point]}},
    }
    if mapping_path.exists():
        if json.loads(mapping_path.read_text(encoding="utf-8")) != config:
            raise ValueError(f"A different area mapping already exists at {mapping_path}. Use a new output path.")
    else:
        write_json(mapping_path, config)
    return config, location_id


def read_weather_locations(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(config, dict) or config.get("operator") != "SPP":
        raise ValueError("Weather mapping must specify operator SPP.")
    if config.get("source_type") != "assumption" or not config.get("ref"):
        raise ValueError("Weather point selection/weights require source_type assumption and an explanatory ref.")
    locations = config.get("locations")
    if not isinstance(locations, dict) or not locations:
        raise ValueError("Weather mapping requires at least one location.")
    for location, definition in locations.items():
        if not isinstance(location, str) or not location.strip() or not isinstance(definition, dict):
            raise ValueError("Invalid location mapping.")
        points = definition.get("points")
        if not isinstance(points, list) or not points:
            raise ValueError(f"{location}: provide at least one weather point.")
        coordinates = set()
        for point in points:
            if not isinstance(point, dict) or not isinstance(point.get("name"), str) or not point["name"].strip():
                raise ValueError(f"{location}: every weather point needs a name.")
            for key, minimum, maximum in (("latitude", -90, 90), ("longitude", -180, 180), ("weight", 0, np.inf)):
                value = point.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or not minimum <= value <= maximum:
                    raise ValueError(f"{location}: invalid {key}.")
            if point["weight"] <= 0:
                raise ValueError(f"{location}: weather weights must be positive.")
            coordinate = (point["latitude"], point["longitude"])
            if coordinate in coordinates:
                raise ValueError(f"{location}: repeated weather coordinates would double-count a point.")
            coordinates.add(coordinate)
        total = sum(point["weight"] for point in points)
        if not np.isfinite(total):
            raise ValueError(f"{location}: total weather weight must be finite.")
    return config


def aggregate_temperature(timeline: pd.DatetimeIndex, points: list[dict], frames: list[pd.DataFrame]) -> pd.DataFrame:
    if not len(points) == len(frames) or not frames:
        raise ValueError("Each weather point requires one hourly temperature table.")
    series = []
    for frame in frames:
        if frame.timestamp_utc.duplicated().any():
            raise ValueError("Duplicate weather timestamps.")
        series.append(frame.set_index("timestamp_utc").temperature_c.reindex(timeline))
    values = np.column_stack([value.to_numpy(dtype=float) for value in series])
    weights = np.array([point["weight"] for point in points], dtype=float)
    weights /= weights.sum()
    complete = np.isfinite(values).all(axis=1)
    result = pd.DataFrame({"timestamp_utc": timeline})
    # Never renormalize around missing stations: that would silently change the area.
    result["temperature_c"] = np.where(complete, values @ weights, np.nan)
    result["temperature_area_min_c"] = np.where(complete, values.min(axis=1), np.nan)
    result["temperature_area_max_c"] = np.where(complete, values.max(axis=1), np.nan)
    return result


def add_temperature(hourly_path: Path, locations_path: Path, output_path: Path,
                    *, cache_dir: Path | None = None, location_id: str | None = None) -> dict:
    from pipeline.ingest import fetch_temperature_year

    if output_path.exists() or output_path.with_suffix(".weather.json").exists():
        raise ValueError(f"Output already exists: {output_path}. Choose a new path to preserve previous data.")
    hourly = read_hourly(hourly_path)
    if location_id is not None:
        hourly = hourly[hourly.location_id.eq(location_id)]
        if hourly.empty:
            raise ValueError(f"Unknown grid location_id {location_id}.")
    if set(TEMPERATURE_COLUMNS + ("temperature_source_ref",)) & set(hourly.columns):
        raise ValueError("Input already contains temperature fields; start with the original input or a new dataset.")
    config = read_weather_locations(locations_path)
    missing = set(hourly.location_id) - set(config["locations"])
    if missing:
        raise ValueError(f"No weather mapping for {sorted(missing)}. Do not substitute another area.")
    cache_dir = cache_dir or ROOT / "data/raw/weather/open_meteo"
    source_records = {}
    joined = []
    locations_report = {}
    for location, group in hourly.groupby("location_id", sort=True):
        timeline = pd.DatetimeIndex(group.timestamp_utc)
        points = config["locations"][location]["points"]
        point_frames = []
        for point in points:
            annual_frames = []
            for year in sorted(set(timeline.year)):
                print(f"Temperature: {location} / {point['name']} / {year} (cached when available)", flush=True)
                frame, path, source = fetch_temperature_year(point["latitude"], point["longitude"], int(year), cache_dir=cache_dir)
                annual_frames.append(frame)
                source_records[str(path)] = {**source, "parquet_path": str(path), "parquet_sha256": fingerprint(path)}
            point_frames.append(pd.concat(annual_frames, ignore_index=True))
        weather = aggregate_temperature(timeline, points, point_frames)
        weather["location_id"] = location
        joined.append(group.merge(weather, on=["timestamp_utc", "location_id"], validate="one_to_one"))
        locations_report[location] = {
            "hours": len(weather), "temperature_hours": int(weather.temperature_c.notna().sum()),
            "missing_temperature_hours": int(weather.temperature_c.isna().sum()),
            "normalized_weights": [point["weight"] / sum(p["weight"] for p in points) for point in points],
        }
    output = pd.concat(joined, ignore_index=True).sort_values(["location_id", "timestamp_utc"])
    if output.temperature_c.notna().sum() == 0:
        raise ValueError("No complete weather observations matched the input hours.")
    manifest_path = output_path.with_suffix(".weather.json")
    try:
        relative_ref = manifest_path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        relative_ref = str(manifest_path.resolve())
    output["temperature_source_ref"] = f"{relative_ref}#hourly-temperature; map_sha256={fingerprint(locations_path)}"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output.to_parquet(output_path, index=False)
    report = {
        "source_type": "data", "ref": "https://open-meteo.com/en/docs/historical-weather-api",
        "dataset": "ERA5 hourly 2-m air temperature via Open-Meteo", "unit": "degrees Celsius",
        "input_path": str(hourly_path), "input_sha256": fingerprint(hourly_path),
        "output_path": str(output_path), "output_sha256": fingerprint(output_path),
        "location_mapping": config, "location_mapping_sha256": fingerprint(locations_path),
        "locations": locations_report, "raw_sources": list(source_records.values()),
        "aggregation": "Weighted mean and unweighted min/max across configured points; all points required per hour.",
        "timing": "Historical reanalysis, not an as-issued forecast archive. Prior-hour features do not establish real-time publication availability.",
        "interpretation": "Input to the event/proxy classifier. Temperature alone neither creates event labels nor establishes site curtailment.",
    }
    write_json(manifest_path, report)
    return report
