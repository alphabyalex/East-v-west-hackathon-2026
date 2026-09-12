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
import logging
import sys
import time
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
