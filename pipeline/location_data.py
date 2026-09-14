"""Location lookup and approximate SPP regional eligibility for explicit local jobs.

These checks admit regional comparisons, not verified electrical interconnections.
All external pulls are cached; reading a saved estimate never calls this module.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import math
import re
import unicodedata
import zipfile

import numpy as np
import pandas as pd
import requests

from pipeline.common import ROOT, write_json
from pipeline.weather import STATE_NAMES

CACHE = ROOT / "data/raw/site/location_data"
HISTORICAL_MAP = "https://services.arcgis.com/3xOwF6p0r7IHIjfn/ArcGIS/rest/services/SPP_Boundary/FeatureServer/0/query"
UTILITY_MAP = "https://eedgis.pnnl.gov/arcgis/rest/services/Hosted/Electric_Service_Territories/FeatureServer/0/query"
EXPANSION = "https://spp.org/news-list/spp-and-member-utilities-successfully-complete-historic-western-expansion/"
TRISTATE = "https://tristate.coop/member-list"
DESERET = "https://deseretpower.com/member-cooperatives/"
PLATTE = "https://prpa.org/about-prpa/who-we-serve/"
CENTRAL_MONTANA = "https://cmepc.org/member-cooperatives"
CENSUS = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gazetteer/2025_Gaz_place_national.zip"
REGION_RADIUS_KM = 100.0

# Distinctive utility names, not city allowlists. These match older retail polygons
# to utilities listed by SPP and its new wholesale members in September 2026.
# Their full retail footprints are only analogues for regional comparison.
WESTERN_NAMES = (
    "COLORADO SPRINGS", "CITY OF AZTEC", "CITY OF FARMINGTON", "CITY OF FOUNTAIN",
    "CITY OF PAGE", "CITY OF SIDNEY", "DELTA MONTROSE", "LA PLATA", "LOS ALAMOS",
    "UNITED POWER", "CENTRAL MONTANA", "TRI STATE", "DESERET", "PLATTE RIVER", "PAGE UTILITY",
    "CITY OF ESTES PARK", "TOWN OF ESTES PARK", "CITY OF FORT COLLINS", "CITY OF LONGMONT", "CITY OF LOVELAND",
    "BIG HORN RURAL", "CARBON POWER", "CENTRAL NEW MEXICO", "CHIMNEY ROCK",
    "COLUMBUS ELECTRIC", "CONTINENTAL DIVIDE", "EMPIRE ELECTRIC", "GARLAND LIGHT",
    "GUNNISON COUNTY", "HIGH PLAINS POWER", "HIGH WEST ENERGY", "HIGHLINE ELECTRIC",
    "JEMEZ MOUNTAINS", "K C ELECTRIC", "MIDWEST ELECTRIC COOPERATIVE", "MORA SAN MIGUEL",
    "MORGAN COUNTY RURAL", "MOUNTAIN VIEW ELECTRIC", "NIOBRARA ELECTRIC", "NORTHERN RIO ARRIBA",
    "NORTHWEST RURAL PUBLIC", "OTERO COUNTY ELECTRIC", "PANHANDLE RURAL ELECTRIC",
    "POUDRE VALLEY", "ROOSEVELT PUBLIC", "SAN ISABEL", "SAN LUIS VALLEY", "SAN MIGUEL POWER",
    "SANGRE DE CRISTO", "SIERRA ELECTRIC", "SOCORRO ELECTRIC", "SOUTHEAST COLORADO",
    "SOUTHWESTERN ELECTRIC COOPERATIVE", "SPRINGER ELECTRIC", "WHEAT BELT", "WHEATLAND RURAL",
    "WHITE RIVER ELECTRIC", "WYRULEC", "Y W ELECTRIC", "BRIDGER VALLEY", "DIXIE",
    "GARKANE", "MOON LAKE", "BIG FLAT ELECTRIC", "HILL COUNTY ELECTRIC", "MARIAS RIVER",
    "PARK ELECTRIC", "SUN RIVER ELECTRIC", "NORVAL ELECTRIC", "YELLOWSTONE VALLEY", "MCCONE ELECTRIC",
)
WESTERN_STATES = {"AZ", "CO", "MT", "NE", "NM", "UT", "WY"}


def normalized(value):
    value = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode().upper()
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", value).split())


def place_name(value):
    value = normalized(value)
    value = re.sub(r" (CITY|TOWN|VILLAGE|CDP|BOROUGH|MUNICIPALITY)$", "", value)
    return re.sub(r"\bFT\b", "FORT", re.sub(r"\bST\b", "SAINT", value))


def split_city_state(query):
    parts = [part.strip() for part in query.split(",")]
    if len(parts) == 3 and normalized(parts[-1]) in {"US", "USA", "UNITED STATES"}:
        parts.pop()
    states = {normalized(name): name for name in STATE_NAMES.values()}
    states.update({code: name for code, name in STATE_NAMES.items()})
    if len(parts) == 2 and all(parts):
        state = states.get(normalized(parts[1]))
        if not state:
            raise ValueError("Use a US state name or its two-letter abbreviation.")
        return parts[0], state
    if len(parts) != 1 or not parts[0]:
        raise ValueError('Use "City, State" or "latitude, longitude".')
    # Accept Wichita KS and Colorado Springs Colorado as well as commas.
    for suffix in sorted(states, key=len, reverse=True):
        match = re.search(r"\s+" + re.escape(suffix) + r"$", parts[0], re.I)
        if match:
            return parts[0][:match.start()].strip(), states[suffix]
    return parts[0], None


def cached_json(url, params, *, cache_dir=None):
    folder = cache_dir or CACHE
    key = hashlib.sha256(json.dumps([url, params], sort_keys=True).encode()).hexdigest()[:24]
    path = folder / f"{key}.parquet"
    if path.exists():
        row = pd.read_parquet(path).iloc[0]
        return json.loads(row.payload_json), json.loads(row.source_json)
    response = requests.get(url, params=params, timeout=45)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("error") or not isinstance(payload.get("features"), list):
        raise ValueError("Regional map returned an invalid response.")
    if payload.get("exceededTransferLimit"):
        raise ValueError("Regional map response was truncated; no partial coverage map used.")
    source = {"source_type": "data", "ref": response.url, "request_parameters": params,
              "retrieved_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "sha256": hashlib.sha256(response.content).hexdigest(), "cache_path": str(path)}
    folder.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"payload_json": json.dumps(payload), "source_json": json.dumps(source)}]).to_parquet(path, index=False)
    return payload, source


def census_places(*, cache_dir=None):
    folder = cache_dir or CACHE
    path = folder / "census_places_2025.parquet"
    if path.exists() and path.with_suffix(".source.json").exists():
        return pd.read_parquet(path), json.loads(path.with_suffix(".source.json").read_text(encoding="utf-8"))
    response = requests.get(CENSUS, timeout=45)
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = [name for name in archive.namelist() if name.endswith(".txt")]
        if len(names) != 1:
            raise ValueError("Unexpected Census place archive.")
        raw = archive.read(names[0]).decode("utf-8-sig")
    frame = pd.read_csv(io.StringIO(raw), sep="|" if "|" in raw.splitlines()[0] else "\t", dtype={"GEOID": str})
    frame.columns = frame.columns.str.strip()
    if not {"USPS", "NAME", "INTPTLAT", "INTPTLONG", "GEOID"}.issubset(frame):
        raise ValueError("Census place coordinates are unavailable.")
    frame = frame[["USPS", "NAME", "INTPTLAT", "INTPTLONG", "GEOID"]].copy()
    frame["search_name"] = frame.NAME.map(place_name)
    source = {"source_type": "data", "ref": CENSUS, "dataset": "2025 US Census national places gazetteer",
              "retrieved_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "sha256": hashlib.sha256(response.content).hexdigest(), "cache_path": str(path)}
    folder.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    write_json(path.with_suffix(".source.json"), source)
    return frame, source


def find_census_city(city, state):
    frame, source = census_places()
    if state:
        codes = {code for code, name in STATE_NAMES.items() if name.casefold() == state.casefold()}
        frame = frame[frame.USPS.isin(codes)]
    frame = frame[frame.search_name.eq(place_name(city))]
    points = [{"name": re.sub(r" (city|town|village|CDP|borough|municipality)$", "", row.NAME, flags=re.I) + ", " + STATE_NAMES[row.USPS],
               "latitude": float(row.INTPTLAT), "longitude": float(row.INTPTLONG), "weight": 1.0,
               "census_geoid": str(row.GEOID)} for row in frame.itertuples() if row.USPS in STATE_NAMES]
    return points[:20], source


def city_candidates(city, state):
    from pipeline.ingest import search_weather_areas
    failure = None
    try:
        frame, source = search_weather_areas(city, cache_dir=ROOT / "data/raw/weather/geocoding")
        frame = frame[frame.country_code.eq("US")]
        if state:
            frame = frame[frame.admin1.astype("string").str.casefold().eq(state.casefold())]
        # Provider searches can return only similarly named cities. Never silently
        # turn e.g. a missing Springfield into Springfield Township in another area.
        exact = frame[frame["name"].map(place_name).eq(place_name(city))]
        if len(exact):
            frame = exact.drop_duplicates(["latitude", "longitude"])
            points = [{"name": f"{row['name']}, {row.admin1}", "latitude": float(row.latitude),
                       "longitude": float(row.longitude), "weight": 1.0, "geonames_id": int(row.id)}
                      for _, row in frame.head(20).iterrows()]
            return points, source
    except (requests.RequestException, ValueError) as error:
        failure = str(error)
    points, source = find_census_city(city, state)
    if failure:
        source = {**source, "primary_lookup_error": failure}
    if not points:
        raise ValueError("No matching US city found. Include the state, spell out the city name, or enter latitude, longitude for any site in the region.")
    return points, source


def distance_km(a, b):
    lat1, lat2 = math.radians(a["latitude"]), math.radians(b["latitude"])
    dlat, dlon = lat2 - lat1, math.radians(b["longitude"] - a["longitude"])
    value = math.sin(dlat / 2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2)**2
    return 6371.0088 * 2 * math.asin(min(1, math.sqrt(value)))


def geometry_distance_km(point, geometry):
    """Distance to WGS84 Polygon/MultiPolygon, respecting holes. US latitudes only.

    Segment distances use a local equirectangular projection. This is a coarse
    100-km comparison screen, not a survey or an electrical-distance measure.
    """
    kind = geometry.get("type")
    if kind not in {"Polygon", "MultiPolygon"}:
        raise ValueError("Regional boundary must contain polygon geometry.")
    polygons = [geometry["coordinates"]] if kind == "Polygon" else geometry["coordinates"]
    x, y = point["longitude"], point["latitude"]
    nearest = float("inf")
    for polygon in polygons:
        inside = False
        for ring in polygon:
            vertices = np.asarray(ring, dtype=float)[:, :2]
            if len(vertices) < 4 or not np.isfinite(vertices).all():
                raise ValueError("Invalid regional boundary ring.")
            start, end = vertices, np.roll(vertices, -1, axis=0)
            crossing = (start[:, 1] > y) != (end[:, 1] > y)
            if crossing.any():
                aa, bb = start[crossing], end[crossing]
                intersections = aa[:, 0] + (y - aa[:, 1]) * (bb[:, 0] - aa[:, 0]) / (bb[:, 1] - aa[:, 1])
                inside ^= bool(np.count_nonzero(x < intersections) % 2)
            scale = np.array([111.195 * math.cos(math.radians(y)), 111.195])
            aa, bb = (start - [x, y]) * scale, (end - [x, y]) * scale
            segment = bb - aa
            denom = np.sum(segment * segment, axis=1)
            t = np.clip(np.divide(-np.sum(aa * segment, axis=1), denom, out=np.zeros_like(denom), where=denom > 0), 0, 1)
            nearest = min(nearest, float(np.linalg.norm(aa + t[:, None] * segment, axis=1).min()))
        if inside or nearest < 1e-6:
            return 0.0
    return nearest


def western_match(row):
    state = str(row.get("state") or "").strip().upper()
    if state not in WESTERN_STATES:
        return False
    name = normalized(row.get("name") or "")
    return any(alias in name for alias in WESTERN_NAMES)


def regional_coverage(point, exact):
    if exact["status"] == "historical_spp_match":
        return {**exact, "eligible": True, "match_method": "historical_utility", "approximate": False}
    evidence, errors = [], []
    try:
        payload, source = cached_json(HISTORICAL_MAP, {"f": "geojson", "where": "1=1", "outFields": "*", "outSR": 4326})
        distances = [geometry_distance_km(point, row["geometry"]) for row in payload["features"]]
        distance = min(distances, default=float("inf"))
        if distance <= REGION_RADIUS_KM:
            evidence.append({"method": "historical_spp_region", "distance_km": round(distance, 1),
                             "source": {**source, "dataset": "Historical HIFLD SPP outline (2019), University of Oklahoma ArcGIS mirror"}})
    except (requests.RequestException, ValueError, KeyError) as error:
        errors.append(str(error))
    # West expansion is not in the 2019 outline. Use full nearby retail polygons
    # of documented participants/member utilities; no finite city allowlist.
    if not evidence and 30 <= point["latitude"] <= 49.1 and -115 <= point["longitude"] <= -101:
        matches = [row for row in exact.get("territories", []) if western_match(row)]
        source = exact.get("source", {})
        if not matches:
            try:
                payload, source = cached_json(UTILITY_MAP, {"f": "json", "where": "1=1",
                    "geometry": f"{point['longitude']},{point['latitude']}", "geometryType": "esriGeometryPoint",
                    "inSR": 4326, "spatialRel": "esriSpatialRelIntersects", "distance": REGION_RADIUS_KM,
                    "units": "esriSRUnit_Kilometer", "outFields": "name,state,cntrl_area,holding_co,year",
                    "returnGeometry": "false"})
                matches = [row["attributes"] for row in payload["features"] if western_match(row["attributes"])]
            except (requests.RequestException, ValueError, KeyError) as error:
                errors.append(str(error))
        if matches:
            evidence.append({"method": "western_participant_region", "maximum_distance_km": REGION_RADIUS_KM,
                "utilities": matches, "source": source, "membership_sources": [{"ref": ref, "source_type": "data"} for ref in (EXPANSION, TRISTATE, DESERET, PLATTE, CENTRAL_MONTANA)]})
    if evidence:
        western = evidence[0]["method"] == "western_participant_region"
        note = "Regional comparison using local or nearby weather and historical SPP grid patterns; exact utility membership is not established."
        if western:
            note += " This western-area estimate uses earlier SPP East patterns as an analogy; the model has not been trained on the 2026 western expansion."
        return {**exact, "status": "regional_spp_comparison", "eligible": True, "approximate": True,
                "match_method": evidence[0]["method"], "interpretation": note,
                "source": {"exact_lookup": exact.get("source", {}), "regional_evidence": evidence},
                "policy": {"source_type": "assumption", "maximum_region_distance_km": REGION_RADIUS_KM,
                           "ref": "User-authorized nearby regional comparison; full retail polygons are proxies, not an electrical RTO boundary."},
                "lookup_errors": errors}
    return {**exact, "eligible": False, "lookup_errors": errors}
