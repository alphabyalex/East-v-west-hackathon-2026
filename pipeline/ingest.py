"""
pipeline/ingest.py

Pull SPP grid data and cache it to parquet. This is the only file in the
pipeline that talks to the network. Everything downstream reads parquet.

Design constraints (see AGENTS.md):
  - Cache every external pull. Never re-fetch a (dataset, date) we already have.
  - Deterministic: same date range in, same file out.
  - No secrets needed. SPP's public feeds via `gridstatus` require no auth.

Usage:
    python -m pipeline.ingest --start 2024-01-01 --end 2026-09-01
    python -m pipeline.ingest --start 2024-01-01 --end 2026-09-01 --datasets load,reserves

Datasets pulled (all SPP, via gridstatus):
    load               get_load_by_baa            hourly load by balancing authority
    reserves           get_operating_reserves      operating reserve margins
    binding_constraints get_binding_constraints_day_ahead_hourly   transmission constraint bindings
    lmp                get_lmp_day_ahead_hourly    day-ahead locational marginal prices
    fuel_mix           get_fuel_mix                generation fuel mix

`load`, `reserves`, and `binding_constraints` are the label.py priority-order
inputs (see docs/build-roadmap.md section 3): reserve shortfalls and binding
transmission constraints are our proxy for emergency/curtailment-triggering
conditions, since SPP's real-time EEA/emergency declarations are not exposed
through gridstatus for SPP (spp.get_status raises NotImplementedError as of
gridstatus 0.36 -- confirmed by direct test, not assumed).
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import hashlib
import logging
import sys
import time
import zipfile
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ingest")

RAW_DIR = Path("data/raw/spp")

DATASETS = {
    "load": "get_load_by_baa",
    "reserves": "get_operating_reserves",
    "binding_constraints": "get_binding_constraints_day_ahead_hourly",
    "lmp": "get_lmp_day_ahead_hourly",
    "fuel_mix": "get_fuel_mix",
}

# How many days to request per call. SPP's real-time feeds (load, fuel_mix)
# are heavy per-day; day-ahead feeds are lighter. Chunking keeps any single
# failure from losing a huge date range, and lets a re-run skip finished chunks.
CHUNK_DAYS = {
    "load": 1,
    "reserves": 7,
    "binding_constraints": 7,
    "lmp": 7,
    "fuel_mix": 1,
}


def daterange_chunks(start: dt.date, end: dt.date, step_days: int):
    cur = start
    while cur <= end:
        chunk_end = min(cur + dt.timedelta(days=step_days - 1), end)
        yield cur, chunk_end
        cur = chunk_end + dt.timedelta(days=1)


def chunk_path(dataset: str, chunk_start: dt.date, chunk_end: dt.date) -> Path:
    d = RAW_DIR / dataset
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{chunk_start.isoformat()}_{chunk_end.isoformat()}.parquet"


def fetch_chunk(spp, dataset: str, chunk_start: dt.date, chunk_end: dt.date) -> pd.DataFrame | None:
    method_name = DATASETS[dataset]
    method = getattr(spp, method_name)
    try:
        df = method(date=chunk_start, end=chunk_end + dt.timedelta(days=1))
    except NotImplementedError:
        log.error("%s is not implemented for SPP in this gridstatus version. Skipping dataset.", method_name)
        raise
    except Exception as e:
        log.warning("Fetch failed for %s [%s, %s]: %s", dataset, chunk_start, chunk_end, e)
        return None
    if df is None or len(df) == 0:
        log.warning("Empty result for %s [%s, %s]", dataset, chunk_start, chunk_end)
        return None
    return df


def ingest_dataset(spp, dataset: str, start: dt.date, end: dt.date, retries: int = 2, sleep_s: float = 2.0) -> int:
    step = CHUNK_DAYS.get(dataset, 7)
    n_written = 0
    n_skipped = 0
    n_failed = 0

    for chunk_start, chunk_end in daterange_chunks(start, end, step):
        out_path = chunk_path(dataset, chunk_start, chunk_end)
        if out_path.exists():
            n_skipped += 1
            continue

        df = None
        for attempt in range(1, retries + 2):
            df = fetch_chunk(spp, dataset, chunk_start, chunk_end)
            if df is not None:
                break
            if attempt <= retries:
                log.info("Retrying %s [%s, %s] (attempt %d)", dataset, chunk_start, chunk_end, attempt + 1)
                time.sleep(sleep_s)

        if df is None:
            n_failed += 1
            continue

        df.to_parquet(out_path, index=False)
        n_written += 1
        log.info("Wrote %s rows=%d -> %s", dataset, len(df), out_path)

    log.info(
        "Dataset '%s' done: %d written, %d skipped (cached), %d failed",
        dataset, n_written, n_skipped, n_failed,
    )
    return n_failed


def load_dataset(dataset: str) -> pd.DataFrame:
    """Read every cached chunk for a dataset back into one DataFrame. Used by
    downstream pipeline stages (label.py, features.py) instead of re-fetching."""
    d = RAW_DIR / dataset
    files = sorted(d.glob("*.parquet")) if d.exists() else []
    if not files:
        raise FileNotFoundError(
            f"No cached data for '{dataset}' in {d}. Run: python -m pipeline.ingest --datasets {dataset}"
        )
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


def fetch_load_sample(date: str = "2024-01-01") -> tuple[pd.DataFrame, Path]:
    """Fetch legacy SPP hourly load to verify access, caching it once.

    Archived daily files can be replaced by the annual ZIP. Cache that entire
    archive as parquet if necessary. Load alone is not a labeled training set.
    The URL format is documented in gridstatus.SPP.get_hourly_load_historical.
    """
    import requests

    day = dt.date.fromisoformat(date)
    if day >= dt.date(2026, 3, 24):
        raise ValueError("This legacy-format sample check requires a date before 2026-03-24.")
    cache = Path(__file__).resolve().parents[1] / "data/raw/spp/access_check"
    path = cache / f"{day.isoformat()}_hourly_load.parquet"
    annual_path = cache / f"{day.year}_hourly_load.parquet"
    if annual_path.exists():
        return pd.read_parquet(annual_path), annual_path
    if path.exists():
        return pd.read_parquet(path), path
    url = (
        "https://portal.spp.org/file-browser-api/download/hourly-load"
        f"?path=/{day.year}/DAILY_HOURLY_LOAD-{day:%Y%m%d}.csv"
    )
    response = requests.get(url, timeout=25)
    if response.status_code == 404:
        url = f"https://portal.spp.org/file-browser-api/download/hourly-load?path=/{day.year}/{day.year}.zip"
        response = requests.get(url, timeout=45)
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            csv_files = sorted(name for name in archive.namelist() if name.lower().endswith(".csv"))
            if not csv_files:
                raise ValueError("SPP's annual archive contains no CSV files.")
            frame = pd.concat([pd.read_csv(archive.open(name)) for name in csv_files], ignore_index=True)
        path = annual_path
    else:
        response.raise_for_status()
        frame = pd.read_csv(io.StringIO(response.text))
    if frame.empty or len(frame.columns) < 2:
        raise ValueError("SPP returned an empty or unexpected hourly-load file.")
    cache.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    path.with_suffix(".source.json").write_text(json.dumps({
        "source_type": "data", "ref": url,
        "retrieved_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }, indent=2) + "\n", encoding="utf-8")
    return frame, path


def fetch_temperature_year(latitude: float, longitude: float, year: int, *, cache_dir: Path) -> tuple[pd.DataFrame, Path, dict]:
    """Fetch/cache a complete ERA5 calendar year for a geographic point.

    Fixed calendar-year caches are reused across overlapping date requests and
    location-weight changes. The first implementation accepts completed past years
    only, avoiding a partial current-year cache that silently becomes stale.
    """
    import numpy as np
    import requests

    if isinstance(year, bool) or not isinstance(year, int) or not 1940 <= year < dt.datetime.now(dt.timezone.utc).year:
        raise ValueError("Temperature ingestion requires a completed calendar year from 1940 onward.")
    for value, bound in ((latitude, 90), (longitude, 180)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or abs(value) > bound:
            raise ValueError("Invalid weather coordinates.")
    latitude, longitude = float(latitude), float(longitude)
    parameters = {"latitude": latitude, "longitude": longitude, "start_date": f"{year}-01-01",
                  "end_date": f"{year}-12-31", "hourly": "temperature_2m", "models": "era5",
                  "timezone": "UTC", "temperature_unit": "celsius", "timeformat": "unixtime"}
    key = hashlib.sha256(json.dumps(parameters, sort_keys=True).encode()).hexdigest()[:20]
    path = Path(cache_dir) / f"era5_{year}_{key}.parquet"
    source_path = path.with_suffix(".source.json")
    if path.exists():
        if not source_path.exists():
            raise ValueError(f"Cached temperature is missing its source manifest: {source_path}")
        source = json.loads(source_path.read_text(encoding="utf-8"))
        if source.get("request_parameters") != parameters:
            raise ValueError(f"Temperature cache parameters do not match: {path}")
        cached = pd.read_parquet(path)
        return cached, path, source
    response = requests.get("https://archive-api.open-meteo.com/v1/archive", params=parameters, timeout=45)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("error"):
        raise ValueError("Weather provider returned an error or an unexpected response.")
    hourly = payload.get("hourly", {})
    units = payload.get("hourly_units", {})
    if units.get("temperature_2m") != "°C" or payload.get("utc_offset_seconds") != 0:
        raise ValueError("Weather response must contain Celsius temperatures at UTC timestamps.")
    times, temperatures = hourly.get("time"), hourly.get("temperature_2m")
    if not isinstance(times, list) or not isinstance(temperatures, list) or len(times) != len(temperatures):
        raise ValueError("Weather time and temperature arrays are missing or misaligned.")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) for value in times):
        raise ValueError("Weather timestamps must be finite Unix seconds.")
    frame = pd.DataFrame({"timestamp_utc": pd.to_datetime(times, unit="s", utc=True),
                          "temperature_c": pd.to_numeric(pd.Series(temperatures), errors="raise")})
    expected = pd.date_range(f"{year}-01-01", f"{year+1}-01-01", freq="h", inclusive="left", tz="UTC")
    if not pd.DatetimeIndex(frame.timestamp_utc).equals(expected):
        raise ValueError("Weather response must contain every requested hourly timestamp exactly once in order.")
    if np.isinf(frame.temperature_c).any() or frame.temperature_c.notna().sum() == 0:
        raise ValueError("Weather response has no valid temperatures or contains infinity.")
    source = {"source_type": "data", "ref": response.url, "request_parameters": parameters,
              "retrieved_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "returned_grid_latitude": payload.get("latitude"), "returned_grid_longitude": payload.get("longitude"),
              "returned_elevation_m": payload.get("elevation"), "hours": len(frame),
              "missing_temperature_hours": int(frame.temperature_c.isna().sum()),
              "availability": "Retrospective ERA5 reanalysis; not verified as available at historical prediction time."}
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    source_path.write_text(json.dumps(source, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return frame, path, source


def search_weather_areas(city: str, *, cache_dir: Path) -> tuple[pd.DataFrame, dict]:
    """Cache public US city candidates; selecting the area happens in weather.py."""
    import requests

    parameters = {"name": city.strip(), "count": 100, "language": "en", "format": "json", "countryCode": "US"}
    if len(parameters["name"]) < 2:
        raise ValueError("Enter a city name with at least two characters.")
    key = hashlib.sha256(json.dumps(parameters, sort_keys=True).encode()).hexdigest()[:20]
    path = Path(cache_dir) / f"area_{key}.parquet"
    source_path = path.with_suffix(".source.json")
    if path.exists():
        if not source_path.exists():
            raise ValueError(f"Area cache is missing its source manifest: {source_path}")
        source = json.loads(source_path.read_text(encoding="utf-8"))
        if source.get("request_parameters") != parameters:
            raise ValueError("Area cache parameters do not match.")
        return pd.read_parquet(path), source
    response = requests.get("https://geocoding-api.open-meteo.com/v1/search", params=parameters, timeout=30)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("error"):
        raise ValueError("Area lookup returned an unexpected response.")
    fields = ["id", "name", "latitude", "longitude", "admin1", "admin2", "country_code", "timezone", "population"]
    results = payload.get("results", [])
    if not isinstance(results, list):
        raise ValueError("Area lookup returned invalid candidates.")
    frame = pd.DataFrame(results).reindex(columns=fields)
    source = {"source_type": "data", "ref": response.url, "request_parameters": parameters,
              "retrieved_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "provider": "Open-Meteo Geocoding / GeoNames"}
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    source_path.write_text(json.dumps(source, indent=2) + "\n", encoding="utf-8")
    return frame, source


def fetch_public_evidence(url: str, key: str, *, cache_dir: Path | None = None) -> tuple[bytes, dict]:
    """Cache a public SPP source document as bytes in parquet, with provenance.

    Keys identify immutable research snapshots; a changing status page needs a
    dated key. Parsing and event interpretation happen outside this downloader.
    """
    import re
    from urllib.parse import urlsplit
    import requests

    host = urlsplit(url).hostname or ""
    if urlsplit(url).scheme != "https" or not (host == "spp.org" or host.endswith(".spp.org") or host == "www.oasis.oati.com"):
        raise ValueError("Evidence downloader accepts public HTTPS SPP/OASIS sources only.")
    if not re.fullmatch(r"[a-z0-9_-]+", key):
        raise ValueError("Evidence cache key must use lowercase letters, digits, underscores or hyphens.")
    folder = cache_dir or Path(__file__).resolve().parents[1] / "data/raw/spp/evidence"
    path = folder / f"{key}.parquet"
    if path.exists():
        cached = pd.read_parquet(path).iloc[0]
        if cached.request_url != url:
            raise ValueError("Evidence snapshot key already belongs to another URL.")
        return bytes(cached.content), json.loads(cached.source_json)
    try:
        response = requests.get(url, timeout=40)
        response.raise_for_status()
        content, final_url = response.content, response.url
        content_type = response.headers.get("Content-Type", "")
        tls_validation = "requests_default_ca_bundle"
    except requests.exceptions.SSLError:
        # Python's standard context on Windows also loads trusted Windows roots.
        # Certificate and hostname verification remain required; never verify=False.
        import ssl
        from urllib.request import urlopen
        with urlopen(url, timeout=40, context=ssl.create_default_context()) as response:
            content, final_url = response.read(), response.url
            content_type = response.headers.get("Content-Type", "")
        tls_validation = "system_default_trust_store_certificate_and_hostname_verified"
    if not content:
        raise ValueError("Source returned an empty document.")
    source = {"source_type": "data", "ref": final_url, "requested_url": url,
              "retrieved_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "content_type": content_type, "tls_validation": tls_validation,
              "sha256": hashlib.sha256(content).hexdigest(), "cache_path": str(path)}
    folder.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"request_url": url, "content": content, "source_json": json.dumps(source)}]).to_parquet(path, index=False)
    return content, source


def main():
    parser = argparse.ArgumentParser(description="Ingest SPP grid data to parquet cache.")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument(
        "--datasets",
        default=",".join(DATASETS.keys()),
        help=f"Comma-separated subset of: {','.join(DATASETS.keys())}",
    )
    args = parser.parse_args()

    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)
    if end < start:
        parser.error("--end must be on or after --start")

    datasets = [d.strip() for d in args.datasets.split(",") if d.strip()]
    unknown = set(datasets) - set(DATASETS)
    if unknown:
        parser.error(f"Unknown dataset(s): {unknown}. Valid: {list(DATASETS)}")

    try:
        import gridstatus
    except ImportError:
        log.error("gridstatus not installed. Run: pip install -r requirements.txt")
        sys.exit(1)

    spp = gridstatus.SPP()

    log.info("Ingesting %s from %s to %s", datasets, start, end)
    total_failed = 0
    for dataset in datasets:
        try:
            total_failed += ingest_dataset(spp, dataset, start, end)
        except NotImplementedError:
            total_failed += 1
            continue

    if total_failed:
        log.warning("%d chunk(s) failed across all datasets. Re-run the same command to retry only the gaps.", total_failed)
        sys.exit(1)
    log.info("Ingest complete.")


if __name__ == "__main__":
    main()
