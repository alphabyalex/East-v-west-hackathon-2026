"""Offline high-wind / low-price opportunity screen for flexible SPP load.

This is an opportunity PROXY, not a measurement of wind curtailment or headroom.
An hour qualifies when same-footprint system wind / system load >= the declared
share threshold AND the exact price location's LMP <= the declared price ceiling.
Generic binding constraints do not establish direction or relief from extra load.
System wind must never be divided by an individual zone's load. Retrospective
observations combined with day-ahead prices are not a verified forecasting signal.

Input is one row per location and UTC hour. system_wind_mw and system_load_mw
refer to the explicitly named common footprint; lmp_usd_mwh is local to location_id.
Reuse pipeline.ingest.load_dataset('fuel_mix'/'lmp') and the historical normalizer
in pipeline.generation to prepare observations; this module never fetches data.
The current gridstatus fuel-mix feed may aggregate SPP and SWPW: an adapter must
establish its footprint before matching it to historical SPP_SYSTEM load.

Energy output is a declared flexible-load scenario, not measured excess supply.
Annual values require one complete observed calendar year with no unknown flags;
partial history is reported for its actual period without extrapolation.

Frozen independent replay can be published offline with:
  python -m pipeline.wind_signal replay --bundle bundle.json --hourly hourly.parquet --labels labels.parquet --baseline baseline.json --start-utc 2025-01-01T00:00:00Z --end-exclusive-utc 2026-01-01T00:00:00Z --output-dir new_replay
Hourly parquet must retain attrs.sources; labels must retain their existing
method/system_scope/source attributes. Baseline JSON is one explicit sourced
probability, never a default or an automatically inferred training prevalence.
The new directory copies the exact four consumed inputs and publishes report
JSON, ZSTD predictions parquet and a completion manifest with file hashes and
runtime versions. No fitting, fetching, model selection or API wiring occurs.
Existing output directories are never reused. A failed write may leave an
incomplete new directory; only the final valid manifest marks publication.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import csv
from fractions import Fraction
import hashlib
import io
import json
from numbers import Real
import os
from pathlib import Path
import re
import tempfile
from typing import Mapping
import warnings
import zipfile

import numpy as np
import pandas as pd

from pipeline import ingest
from pipeline.common import ROOT, fingerprint, read_hourly
from pipeline.generation import normalize_generation
from pipeline.prepare import LOAD_AREAS


GENMIX_SOURCE_QUALIFICATION = {
    "revision": "spp-generation-source-review-20260913",
    "source": {"source_type": "data", "ref": "https://portal.spp.org/api/pageConfig/by-slug/generation-mix-historical; "
               "reviewed_metadata_sha256=0160d0a41a70029190b79c3856cd427eeb59381a0d1af1f083f8e40011d435db"},
    "text": "SPP describes GenMix Self columns as end-of-dispatch MW targets. Market plus Self is not established "
            "as metered actual generation. SPP describes GMT MKT Interval as hour-ending, while the reviewed "
            "gridstatus adapter treats its five-minute timestamps as interval-start. That discrepancy is unresolved; "
            "the frozen experiment retains its declared observation-time binning assumption. This qualification "
            "does not change fitted parameters, historical processing or verified publication availability.",
}


def source(value: object) -> dict:
    """Validate a source without upgrading it or discarding its reference."""
    if not isinstance(value, Mapping) or set(value) != {"source_type", "ref"}:
        raise ValueError("A source requires exactly source_type and ref.")
    if not isinstance(value["source_type"], str) or value["source_type"] not in {"data", "model", "assumption"}:
        raise ValueError("Unsupported source_type.")
    ref = value["ref"]
    if not isinstance(ref, str) or not ref.strip():
        raise ValueError("A source needs a nonempty reference.")
    if value["source_type"] != "assumption" and any(
        word in ref.lower() for word in ("placeholder", "mock://", "illustrative")
    ):
        raise ValueError("Placeholder references must remain assumptions.")
    # Traverse only the producer's exact derived-source format. Other JSON refs
    # remain opaque citations, and no ref is ever followed or fetched.
    if ref.lstrip().startswith("{"):
        try:
            nested = json.loads(ref)
        except ValueError:
            nested = None
        recognized = (isinstance(nested, dict) and set(nested) == {"method", "inputs"}
                      and isinstance(nested["method"], str) and isinstance(nested["inputs"], list)
                      and all(isinstance(item, dict) and {"source_type", "ref"}.issubset(item)
                              for item in nested["inputs"]))
        if recognized:
            # Recognition preserves unrelated opaque citations. Once interpreted,
            # a ref must have one unambiguous value per key and finite numbers;
            # default JSON last-key-wins parsing can hide an assumption source.
            def unique(pairs):
                result = {}
                for key, item in pairs:
                    if key in result:
                        raise ValueError(f"Duplicate derived provenance JSON key: {key}")
                    result[key] = item
                return result

            def finite_literal(literal):
                number = float(literal)
                if not np.isfinite(number):
                    raise ValueError("Nonfinite derived provenance JSON number")
                return number

            nested = json.loads(ref, object_pairs_hook=unique,
                                parse_constant=finite_literal, parse_float=finite_literal)
            rank = {"data": 0, "model": 1, "assumption": 2}
            for item in nested["inputs"]:
                # Inputs may be complete sourced datums with value/unit fields;
                # the source validator itself still requires exactly two keys.
                origin = source({key: item[key] for key in ("source_type", "ref")})
                if rank[origin["source_type"]] > rank[value["source_type"]]:
                    raise ValueError("Derived provenance cannot upgrade a nested input source type.")
    return dict(value)


def sourced(value: float | int | None, origin: Mapping) -> dict:
    return {"value": value, **source(origin)}


def finite_number(value: object, name: str, *, minimum=None, maximum=None) -> float:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite number, not a boolean.")
    # JSON integers and Real fractions need not fit a NumPy scalar dtype.
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a finite number, not a boolean.") from error
    if not np.isfinite(number):
        raise ValueError(f"{name} must be a finite number, not a boolean.")
    if minimum is not None and number < minimum or maximum is not None and number > maximum:
        raise ValueError(f"{name} is outside its allowed range.")
    return number


def _numeric_observations(values: pd.Series, name: str) -> pd.Series:
    """Keep real numeric strings/nulls, never complex parts or temporal units."""
    kind = getattr(values.dtype, "kind", None)
    forbidden = (complex, np.complexfloating, date, timedelta, np.datetime64, np.timedelta64)
    if kind in {"c", "M", "m"} or kind not in {"b", "i", "u", "f"} and any(
        isinstance(value, forbidden) for value in values
    ):
        raise ValueError(f"{name} cannot contain complex, datetime or timedelta quantities.")
    return pd.to_numeric(values, errors="raise")


def wind_scenario_mwh(proxy_hours, flexible_load_mw, available_fraction):
    """Round the three-factor energy product once, without intermediate loss.

    Exact binary-float ratios preserve subnormal final results and avoid an
    overflowing intermediate when the final energy is finite. This is the same
    count times capacity times availability scenario, not a new physical model.
    """
    count = finite_number(proxy_hours, "proxy hours", minimum=0)
    if not count.is_integer():
        raise ValueError("Proxy hours must be an integer count of complete hourly flags.")
    capacity = finite_number(flexible_load_mw, "flexible_load_mw", minimum=0)
    fraction = finite_number(available_fraction, "available_fraction", minimum=0, maximum=1)
    product = Fraction(count) * Fraction(capacity) * Fraction(fraction)
    try:
        result = float(product)
    except OverflowError as exc:
        raise ValueError("Scenario MWh must be finite; final energy is unrepresentable.") from exc
    return finite_number(result, "scenario MWh", minimum=0)


@dataclass(frozen=True)
class WindPolicy:
    minimum_wind_share: float = .5
    maximum_lmp_usd_mwh: float = 0.0

    def __post_init__(self):
        # Screening must compare the same binary64 thresholds recorded in source.
        object.__setattr__(self, "minimum_wind_share",
                           finite_number(self.minimum_wind_share, "minimum_wind_share", minimum=0, maximum=1))
        object.__setattr__(self, "maximum_lmp_usd_mwh",
                           finite_number(self.maximum_lmp_usd_mwh, "maximum_lmp_usd_mwh"))

    @property
    def source(self) -> dict:
        return {
            "source_type": "assumption",
            "ref": "pipeline/wind_signal.py:WindPolicy; declared screening thresholds; "
                   f"wind_share>={float(self.minimum_wind_share)!r}, lmp_usd_mwh<={float(self.maximum_lmp_usd_mwh)!r}; "
                   "high-wind/low-price opportunity proxy, not measured wind curtailment",
        }


INPUT_COLUMNS = ("system_wind_mw", "system_load_mw", "lmp_usd_mwh")
VER_WIND_COLUMNS = ("WindRedispatchCurtailments", "WindManualCurtailments", "WindCurtailedForEnergy")
VER_DESCRIPTION_REF = "https://portal.spp.org/api/pageConfig/by-slug/ver-curtailments"


def _archive_csv_header(stream):
    """Validate complete CSV records before pandas renames/projects source fields.

    This offline streaming pass uses bounded memory, then rewinds the same input
    for the existing pandas parser. In particular, usecols can otherwise silently
    discard unheaded extra fields. Empty physical lines are not observations;
    delimiter-only rows still require exactly the declared number of fields.
    """
    text = io.TextIOWrapper(stream, encoding="utf-8-sig", newline="")
    try:
        records = csv.reader(text, strict=True)
        header = next(records, None)
        if not header or any(not name.strip() for name in header) or len(header) != len(set(name.strip() for name in header)):
            raise ValueError("Archive CSV has duplicate or missing column headers.")
        for record in records:
            if record and len(record) != len(header):
                raise ValueError(f"Archive CSV record ending on line {records.line_num} has {len(record)} fields; "
                                 f"expected exactly {len(header)} declared columns.")
    except (csv.Error, UnicodeDecodeError) as exc:
        raise ValueError("Archive CSV contains malformed quoted records or invalid UTF-8.") from exc
    finally:
        text.detach()
        stream.seek(0)


def _archive_gmt_times(values):
    """Keep documented bare GMT/explicit offsets; never discard a timezone label."""
    with warnings.catch_warnings():
        warnings.filterwarnings("error", message=r".*(?:un-recognized|unrecognized) timezone.*", category=FutureWarning)
        try:
            return pd.to_datetime(values, utc=True, format="mixed")
        except FutureWarning as exc:
            raise ValueError("Archive GMT timestamps contain an unrecognized timezone; provide actual documented GMT values "
                             "or an explicit UTC offset without discarding the timezone label.") from exc


def _archive_operating_day(raw_ends, day, *, minutes):
    """Check the member's Central operating day using interval starts, not ends."""
    ends = _archive_gmt_times(raw_ends)
    starts = ends - pd.Timedelta(minutes, unit="min")
    local_days = starts.dt.tz_convert("America/Chicago").dt.strftime("%Y-%m-%d")
    if ends.isna().any() or not local_days.eq(day.strftime("%Y-%m-%d")).all():
        raise ValueError("Archive observation operating day disagrees with its daily member date.")


def read_cached_wind_curtailment_archive(year: int) -> tuple[pd.DataFrame, dict]:
    """Read independent SPP VER observations from the existing evidence cache.

    Through 2024 uses daily CSV members only, excluding monthly duplicates.
    The explicit 2025 annual-rollup URL has one CSV with repeated full headers;
    only exact complete header repetitions are removed, with count in provenance.
    No archive member is extracted to disk and this reader never downloads.
    The raw category quantities retain their source units; they are not summed
    or converted to energy here. WindCurtailedForEnergy is a redispatch subset.
    Missing days/observations remain missing, not negative training examples.
    """
    if isinstance(year, bool) or not isinstance(year, int) or not 2014 <= year <= 2025:
        raise ValueError("Use a declared historical VER archive year from 2014 through 2025.")
    rollup = year == 2025
    path = ROOT / f"data/raw/spp/evidence/ver_curtailments_{year}{'_rollup' if rollup else ''}.parquet"
    cached = pd.read_parquet(path)
    if len(cached) != 1 or not cached.columns.is_unique or not {"request_url", "content", "source_json"}.issubset(cached.columns):
        raise ValueError("Invalid cached VER archive record.")
    row = cached.iloc[0]
    filename = f"{year}-VER-Curtailments-ANNUAL-ROLLUP.zip" if rollup else f"{year}.zip"
    url = f"https://portal.spp.org/file-browser-api/download/ver-curtailments?path=/{year}/{filename}"
    manifest = _strict_wind_json(row.source_json)
    if not isinstance(manifest, dict) or row.request_url != url or manifest.get("requested_url") != url or manifest.get("ref") != url:
        raise ValueError("VER cache must identify its exact SPP archive URL.")
    origin = source({key: manifest.get(key) for key in ("source_type", "ref")})
    if not isinstance(row.content, (bytes, bytearray)):
        raise ValueError("Cached VER archive must contain ZIP bytes.")
    digest = hashlib.sha256(row.content).hexdigest()
    if manifest.get("sha256") != digest:
        raise ValueError("Cached VER bytes do not match their source fingerprint.")
    with zipfile.ZipFile(io.BytesIO(row.content)) as archive:
        if rollup:
            member = f"{year}-VER-Curtailments-ANNUAL-ROLLUP.csv"
            if archive.namelist() != [member]:
                raise ValueError("VER annual rollup requires its one exact CSV member, without duplicates or extra paths.")
            if archive.getinfo(member).file_size > 10_000_000:
                raise ValueError("VER annual rollup exceeds the supported observation size.")
            with archive.open(member) as stream:
                _archive_csv_header(stream)
                frame = pd.read_csv(stream)
            expected = {"LocalIntervalEnding", "GMTIntervalEnding", *VER_WIND_COLUMNS,
                        "SolarRedispatchCurtailments", "SolarManualCurtailments", "SolarCurtailedForEnergy"}
            if set(frame.columns) != expected:
                raise ValueError("VER annual rollup requires the reviewed eight-column schema.")
            repeated = frame.eq(pd.Series(frame.columns, index=frame.columns)).all(axis=1)
            removed_headers = int(repeated.sum())
            frame = frame.loc[~repeated].copy()
            if frame.empty or frame.GMTIntervalEnding.map(lambda value: isinstance(value, (Real, bool, np.bool_))).any():
                raise ValueError("VER annual rollup requires nonempty, nonnumeric GMT interval ends.")
            ends = _archive_gmt_times(frame.GMTIntervalEnding)
            operating_starts = (ends - pd.Timedelta(5, unit="min")).dt.tz_convert("America/Chicago")
            if ends.isna().any() or not ends.eq(ends.dt.floor("5min")).all() or not operating_starts.dt.year.eq(year).all():
                raise ValueError("VER annual rollup interval starts must lie within its Central operating year.")
            frame["archive_member"] = member
            return frame.reset_index(drop=True), {**origin, "ref": f"{url}; cached_sha256={digest}; "
                f"one exact annual-rollup member; exact repeated full CSV headers removed={removed_headers}; "
                "GMT interval end minus five minutes checked against Central operating year; "
                "archive filename does not guarantee complete annual coverage; category quantities not added"}
        names = sorted(name for name in archive.namelist()
                       if re.fullmatch(rf"{year}/\d{{2}}/VER-Curtailments-{year}\d{{4}}\.csv", name))
        if not names or len(names) != len(set(names)) or len(names) > 366:
            raise ValueError("VER archive requires unambiguous daily CSV members.")
        frames = []
        for name in names:
            stamp = pd.Timestamp(name[-12:-4])
            if stamp.strftime("%m") != name.split("/")[1]:
                raise ValueError("VER daily member path disagrees with its filename date.")
            # Limit malformed archives before allocating their decompressed data.
            if archive.getinfo(name).file_size > 10_000_000:
                raise ValueError("VER daily member exceeds the supported observation size.")
            with archive.open(name) as stream:
                _archive_csv_header(stream)
                frame = pd.read_csv(stream)
            if frame.empty:
                raise ValueError("VER daily member contains no observations.")
            if "GMTIntervalEnding" not in frame:
                raise ValueError("VER daily member lacks its GMT interval-end field.")
            _archive_operating_day(frame.GMTIntervalEnding, stamp, minutes=5)
            frame["archive_member"] = name
            frames.append(frame)
    result = pd.concat(frames, ignore_index=True)
    return result, {**origin, "ref": f"{url}; cached_sha256={digest}; daily CSV members only; "
                                    "monthly archive duplicates excluded; category quantities not added"}


def read_cached_wind_curtailment_month(year: int = 2025, month: int = 12) -> tuple[pd.DataFrame, dict]:
    """Read reviewed December 2025 or January/February 2026 VER without fetching.

    This separate cached source does not replace the partial annual rollup or
    any prior dataset/evaluation. Returns original category values and rows;
    prepare_wind_curtailment_labels owns completeness, duplicate/conflicting
    interval handling and numeric validation. No missing category becomes zero,
    no category quantities are added, and the filename does not prove coverage.
    Only those three reviewed months and their exact eight-column format are
    accepted. None has a BAA field: the downstream label preparer still requires
    an explicit sourced footprint declaration. No scope is inferred here from
    the filename. Later 2026 months require a separate schema/footprint review.
    """
    if type(year) is not int or type(month) is not int or (year, month) not in {(2025, 12), (2026, 1), (2026, 2)}:
        raise ValueError("Monthly VER reader supports only reviewed December 2025 and January/February 2026.")
    path = ROOT / f"data/raw/spp/evidence/ver_curtailments_{year}_{month:02d}.parquet"
    cached = pd.read_parquet(path)
    if len(cached) != 1 or not cached.columns.is_unique or not {"request_url", "content", "source_json"}.issubset(cached.columns):
        raise ValueError("Invalid cached monthly VER evidence record.")
    row = cached.iloc[0]
    member = f"VER-Curtailments-MONTHLY-{year}{month:02d}.csv"
    url = f"https://portal.spp.org/file-browser-api/download/ver-curtailments?path=/{year}/{month:02d}/{member}"
    manifest = _strict_wind_json(row.source_json)
    if not isinstance(manifest, dict) or row.request_url != url or manifest.get("requested_url") != url or manifest.get("ref") != url:
        raise ValueError("Monthly VER cache must identify its exact month and SPP source URL.")
    origin = source({key: manifest.get(key) for key in ("source_type", "ref")})
    if not isinstance(row.content, (bytes, bytearray)) or len(row.content) > 2_000_000:
        raise ValueError("Cached monthly VER must contain bounded CSV bytes.")
    digest = hashlib.sha256(row.content).hexdigest()
    if manifest.get("sha256") != digest:
        raise ValueError("Cached monthly VER bytes do not match their source fingerprint.")
    stream = io.BytesIO(row.content)
    _archive_csv_header(stream)
    frame = pd.read_csv(stream)
    expected = {"LocalIntervalEnding", "GMTIntervalEnding", *VER_WIND_COLUMNS,
                "SolarRedispatchCurtailments", "SolarManualCurtailments", "SolarCurtailedForEnergy"}
    if frame.empty or set(frame.columns) != expected:
        raise ValueError("Monthly VER requires the reviewed eight-column schema and observations.")
    if frame.GMTIntervalEnding.map(lambda value: isinstance(value, (Real, bool, np.bool_))).any():
        raise ValueError("Monthly VER GMT interval-end timestamps cannot be numeric or boolean.")
    ends = _archive_gmt_times(frame.GMTIntervalEnding)
    starts = (ends - pd.Timedelta(5, unit="min")).dt.tz_convert("America/Chicago")
    if (ends.isna().any() or not ends.eq(ends.dt.floor("5min")).all()
            or not starts.dt.strftime("%Y-%m").eq(f"{year}-{month:02d}").all()):
        raise ValueError("Monthly VER interval starts disagree with their Central operating month.")
    frame["archive_member"] = member
    # Preserve the previously published December source text byte for byte.
    supplement = "separate December monthly supplement" if (year, month) == (2025, 12) else f"separate reviewed {year}-{month:02d} monthly archive; BAA absent, sourced footprint declaration required"
    return frame, {**origin, "ref": f"{url}; cached_sha256={digest}; "
        f"{supplement}; GMT interval end minus five minutes checked against Central operating month; "
        "original category values and duplicate/conflicting rows retained for shared label preparation; "
        "no category summation or gap filling; filename does not guarantee complete monthly coverage"}


def prepare_wind_curtailment_labels(
    raw: pd.DataFrame, *, origin: Mapping, system_scope: str, scope_source: Mapping,
) -> pd.DataFrame:
    """Independent reported system-wind-curtailment event labels, not site labels.

    SPP documents GMTIntervalEnding as the five-minute interval END in GMT.
    Each interval is positive if ANY observed wind category is positive. Energy
    curtailment is a subset of redispatch and is never added a second time.
    With no positive category, missing categories mean unknown, not zero.
    A training hour requires twelve distinct, evaluable five-minute intervals;
    even a partial positive hour stays unknown for this complete-hour target.

    An explicit sourced historical SPP footprint declaration is mandatory because
    older files omit BAA. If BAA exists, select SPP exactly and exclude SWPW. The
    result cannot establish local deliverability, recoverable MWh, or whether an
    individual flexible load would have absorbed reported curtailed wind.
    """
    origin, scope = source(origin), source(scope_source)
    if system_scope != "SPP_SYSTEM":
        raise ValueError("Independent VER labels currently support only the declared SPP_SYSTEM footprint.")
    needed = {"GMTIntervalEnding", *VER_WIND_COLUMNS}
    if not isinstance(raw, pd.DataFrame) or not raw.columns.is_unique or raw.empty or not needed.issubset(raw.columns):
        raise ValueError("VER labels require nonempty GMT interval-end and all three wind-category columns.")
    selected = raw
    if "BAA" in raw:
        if raw.BAA.isna().any():
            raise ValueError("Explicit BAA observations cannot have an unknown footprint.")
        selected = raw[raw.BAA == "SPP"]
        if selected.empty:
            raise ValueError("VER observations contain no explicitly selected SPP BAA rows.")
    frame = selected[["GMTIntervalEnding", *VER_WIND_COLUMNS]].copy()
    if frame.GMTIntervalEnding.map(lambda value: isinstance(value, (Real, bool, np.bool_))).any():
        raise ValueError("GMT interval-end timestamps cannot be numeric or boolean.")
    ends = _archive_gmt_times(frame.GMTIntervalEnding)
    if ends.isna().any() or not ends.eq(ends.dt.floor("5min")).all():
        raise ValueError("VER interval ends must identify valid five-minute GMT boundaries.")
    frame["timestamp_utc"] = ends - pd.Timedelta(5, unit="min")
    frame = frame.drop(columns="GMTIntervalEnding")
    for name in VER_WIND_COLUMNS:
        if frame[name].map(lambda value: isinstance(value, (bool, np.bool_))).any():
            raise ValueError("VER quantities cannot be boolean.")
        frame[name] = _numeric_observations(frame[name], "VER quantities").astype(float)
        if np.isinf(frame[name]).any() or frame[name].lt(0).any():
            raise ValueError("VER categories require nonnegative finite observations or missing values.")
    frame = frame.drop_duplicates()
    if frame.timestamp_utc.duplicated().any():
        raise ValueError("Conflicting VER interval revisions must be reconciled before labeling.")
    frame = frame.set_index("timestamp_utc").sort_index()
    positive = frame[list(VER_WIND_COLUMNS)].gt(0).any(axis=1)
    evaluable = positive | frame[list(VER_WIND_COLUMNS)].notna().all(axis=1)
    event = positive.astype("boolean").where(evaluable, pd.NA)
    grouped = pd.DataFrame({"event": event}).resample("h")
    observed = grouped.size()
    known = grouped.event.count()
    count = grouped.event.sum(min_count=1)
    complete = observed.eq(12) & known.eq(12)
    output = pd.DataFrame({"observed_five_minute_samples": observed,
                           "evaluable_five_minute_samples": known,
                           "wind_curtailment_event": count.gt(0).astype("boolean").where(complete, pd.NA)})
    output["location_id"] = system_scope
    kind = "assumption" if "assumption" in (origin["source_type"], scope["source_type"]) else "model" if "model" in (origin["source_type"], scope["source_type"]) else "data"
    output.attrs = {"method": "reported_system_wind_curtailment_any_category_v1", "system_scope": system_scope,
                    "source": {"source_type": kind, "ref": f"{origin['ref']}; footprint: {scope['ref']}; "
                               f"schema: {VER_DESCRIPTION_REF}; GMT interval end minus five minutes; "
                               "any observed positive wind category; twelve evaluable samples per hour; no category summation"},
                    "limitation": "Reported system wind-curtailment occurrence; not a site absorption opportunity or recoverable-energy estimate."}
    return output.reset_index()


def read_cached_day_ahead_prices(year: int, *, settlement_locations: list[str]) -> tuple[pd.DataFrame, dict]:
    """Offline exact-location selection from SPP's archived annual DA price ZIP.

    Reuses an archive cached by ingest.fetch_public_evidence under the key
    da_lmp_settlement_<year>. Daily URLs may disappear after annual rollup; this
    reader never retries them or fetches anything. It streams daily CSV members,
    filters before concatenation, and excludes monthly duplicate files.

    Returned columns match the existing wind adapter: Interval Start/End, Market
    ('DAY_AHEAD_HOURLY'), Location (exact Settlement Location), Pnode and LMP.
    GMTIntervalEnd is documented delivery interval END in GMT, so subtract one
    hour. This does not establish the publication/revision vintage. Prices are
    USD/MWh at the supplied settlement point; no city, BAA or site mapping is made.
    This full-archive parsing is preparation work, never an API request operation.
    """
    if isinstance(year, bool) or not isinstance(year, int) or not 2014 <= year <= 2024:
        raise ValueError("Use a declared historical day-ahead price year from 2014 through 2024.")
    if not isinstance(settlement_locations, list) or not settlement_locations or any(
        not isinstance(value, str) or not value.strip() or value != value.strip() for value in settlement_locations
    ) or len(set(settlement_locations)) != len(settlement_locations):
        raise ValueError("Select a nonempty unique list of exact settlement-location identifiers.")
    path = ROOT / f"data/raw/spp/evidence/da_lmp_settlement_{year}.parquet"
    cached = pd.read_parquet(path)
    if len(cached) != 1 or not cached.columns.is_unique or not {"request_url", "content", "source_json"}.issubset(cached.columns):
        raise ValueError("Invalid cached day-ahead price archive record.")
    row = cached.iloc[0]
    url = f"https://portal.spp.org/file-browser-api/download/da-lmp-by-settlement-location?path=/{year}/{year}.zip"
    manifest = _strict_wind_json(row.source_json)
    if not isinstance(manifest, dict) or row.request_url != url or manifest.get("requested_url") != url or manifest.get("ref") != url:
        raise ValueError("Day-ahead price cache must identify its exact SPP archive URL.")
    origin = source({key: manifest.get(key) for key in ("source_type", "ref")})
    if not isinstance(row.content, (bytes, bytearray)):
        raise ValueError("Cached day-ahead archive must contain ZIP bytes.")
    digest = hashlib.sha256(row.content).hexdigest()
    if manifest.get("sha256") != digest:
        raise ValueError("Cached day-ahead bytes do not match their source fingerprint.")
    columns = ["GMTIntervalEnd", "Settlement Location", "Pnode", "LMP"]
    selected = []
    with zipfile.ZipFile(io.BytesIO(row.content)) as archive:
        names = sorted(name for name in archive.namelist()
                       if re.fullmatch(rf"{year}/\d{{2}}/By_Day/DA-LMP-SL-{year}\d{{4}}0100\.csv", name))
        if not names or len(names) != len(set(names)) or len(names) > 366:
            raise ValueError("Day-ahead archive requires unambiguous daily CSV members.")
        for name in names:
            date = pd.Timestamp(name.rsplit("-", 1)[1][:8])
            if date.strftime("%m") != name.split("/")[1]:
                raise ValueError("Day-ahead member path disagrees with its filename date.")
            if archive.getinfo(name).file_size > 50_000_000:
                raise ValueError("Day-ahead daily member exceeds the supported observation size.")
            with archive.open(name) as stream:
                _archive_csv_header(stream)
                for chunk in pd.read_csv(stream, usecols=columns, dtype={"Settlement Location": str, "Pnode": str}, chunksize=50_000):
                    local = chunk[chunk["Settlement Location"].isin(settlement_locations)]
                    if not local.empty:
                        _archive_operating_day(local.GMTIntervalEnd, date, minutes=60)
                        selected.append(local.copy())
    if not selected:
        raise ValueError("No cached observations for the explicitly selected settlement locations.")
    frame = pd.concat(selected, ignore_index=True)
    absent = set(settlement_locations) - set(frame["Settlement Location"])
    if absent:
        raise ValueError(f"No cached observations for exact settlement locations: {sorted(absent)}.")
    if frame.GMTIntervalEnd.map(lambda value: isinstance(value, (Real, bool, np.bool_))).any():
        raise ValueError("GMT delivery interval-end timestamps cannot be numeric or boolean.")
    ends = _archive_gmt_times(frame.GMTIntervalEnd)
    if ends.isna().any() or not ends.eq(ends.dt.floor("h")).all():
        raise ValueError("Day-ahead delivery interval ends require whole GMT hours.")
    if frame.Pnode.isna().any() or not frame.Pnode.map(lambda value: isinstance(value, str) and bool(value.strip())).all():
        raise ValueError("Selected prices require their explicit published Pnode identifiers.")
    if frame.LMP.map(lambda value: isinstance(value, (bool, np.bool_))).any():
        raise ValueError("Day-ahead LMP cannot be boolean.")
    frame["LMP"] = _numeric_observations(frame.LMP, "LMP").astype(float)
    if np.isinf(frame.LMP).any():
        raise ValueError("Day-ahead LMP cannot be infinite.")
    frame["Interval End"] = ends
    frame["Interval Start"] = ends - pd.Timedelta(1, unit="h")
    frame["Market"] = "DAY_AHEAD_HOURLY"
    frame = frame.rename(columns={"Settlement Location": "Location"})
    frame = frame[["Interval Start", "Interval End", "Market", "Location", "Pnode", "LMP"]].drop_duplicates()
    if frame.duplicated(["Location", "Interval Start"]).any():
        raise ValueError("Conflicting settlement price or Pnode revisions must be reconciled first.")
    frame = frame.sort_values(["Location", "Interval Start"]).reset_index(drop=True)
    return frame, {**origin, "ref": f"{url}; cached_sha256={digest}; daily members only; "
                                   f"exact Settlement Location selection={sorted(settlement_locations)!r}; "
                                   "GMTIntervalEnd minus one hour; LMP USD/MWh; publication vintage unverified; "
                                   "https://portal.spp.org/api/pageConfig/by-slug/da-lmp-by-settlement-location"}


def read_cached_day_ahead_price_month(
    year: int = 2025, month: int = 1, *, settlement_locations: list[str],
) -> tuple[pd.DataFrame, dict]:
    """Prepare exact-location DA LMP from one reviewed cached monthly CSV.

    Supports all twelve 2025 months and January/February 2026 only. Later 2026
    archives require a separate raw-schema review before this guard is extended.

    Date at UTC midnight plus HE minus one hour is an INFERRED interval-start
    mapping. It matched all selected 2024 monthly/daily prices, including DST,
    but is not explicitly defined in the reviewed SPP guide. Therefore the
    returned source remains an assumption and retains the raw source/hash.

    The file spans a Central operating month, with null UTC-date padding on
    its first/final rows. Drop only that padding; interior missing prices stay
    missing, absent rows are not filled, and negative prices are valid. Output
    uses the annual reader's six columns. No fetching, annualization, time-zone
    shift of the HE grid, publication-vintage claim or API-time parsing occurs.
    """
    if isinstance(year, bool) or not isinstance(year, int) or year not in (2025, 2026):
        raise ValueError("The reviewed monthly day-ahead price reader supports only 2025 and January/February 2026.")
    if isinstance(month, bool) or not isinstance(month, int) or not 1 <= month <= 12:
        raise ValueError("Select an integer day-ahead operating month from 1 through 12.")
    if year == 2026 and month not in (1, 2):
        raise ValueError("The reviewed monthly day-ahead price reader supports only January/February for 2026.")
    if not isinstance(settlement_locations, list) or not settlement_locations or any(
        not isinstance(value, str) or not value.strip() or value != value.strip() for value in settlement_locations
    ) or len(set(settlement_locations)) != len(settlement_locations):
        raise ValueError("Select a nonempty unique list of exact settlement-location identifiers.")
    path = ROOT / f"data/raw/spp/evidence/da_lmp_settlement_{year}_{month:02d}.parquet"
    cached = pd.read_parquet(path)
    if len(cached) != 1 or not cached.columns.is_unique or not {"request_url", "content", "source_json"}.issubset(cached.columns):
        raise ValueError("Invalid cached monthly day-ahead price record.")
    row = cached.iloc[0]
    url = ("https://portal.spp.org/file-browser-api/download/da-lmp-by-settlement-location"
           f"?path=/{year}/{month:02d}/DA-LMP-MONTHLY-SL-{year}{month:02d}.csv")
    manifest = _strict_wind_json(row.source_json)
    if not isinstance(manifest, dict) or row.request_url != url or manifest.get("requested_url") != url or manifest.get("ref") != url:
        raise ValueError("Monthly day-ahead cache must identify its exact SPP CSV URL.")
    origin = source({key: manifest.get(key) for key in ("source_type", "ref")})
    if not isinstance(row.content, (bytes, bytearray)) or not row.content:
        raise ValueError("Cached monthly day-ahead observations must contain CSV bytes.")
    digest = hashlib.sha256(row.content).hexdigest()
    if manifest.get("sha256") != digest:
        raise ValueError("Cached monthly day-ahead bytes do not match their source fingerprint.")
    stream = io.BytesIO(row.content)
    _archive_csv_header(stream)
    identifiers = ["Date", "Settlement Location Name", "PNODE Name", "Price Type"]
    hours = [f"HE{hour:02d}" for hour in range(1, 25)]
    selected = []
    # Read text first: CSV cannot encode genuine complex/temporal quantities,
    # and pandas' default NA tokens must not change exact published identities.
    for chunk in pd.read_csv(stream, dtype=str, keep_default_na=False, chunksize=20_000):
        chunk.columns = chunk.columns.str.strip()
        if set(chunk.columns) != set(identifiers + hours):
            raise ValueError("Monthly day-ahead CSV requires exactly Date, exact identifiers, Price Type and HE01 through HE24.")
        local = chunk.loc[chunk["Settlement Location Name"].isin(settlement_locations) & chunk["Price Type"].eq("LMP"),
                          identifiers + hours]
        if not local.empty:
            selected.append(local.copy())
    if not selected:
        raise ValueError("No cached LMP observations for the explicitly selected settlement locations.")
    wide = pd.concat(selected, ignore_index=True)
    absent = set(settlement_locations) - set(wide["Settlement Location Name"])
    if absent:
        raise ValueError(f"No cached LMP observations for exact settlement locations: {sorted(absent)}.")
    if not wide.Date.str.fullmatch(r"\d{4}/\d{2}/\d{2}").all():
        raise ValueError("Monthly day-ahead Date requires strict YYYY/MM/DD text.")
    dates = pd.to_datetime(wide.Date, format="%Y/%m/%d", utc=True)
    first_date = pd.Timestamp(year=year, month=month, day=1, tz="UTC")
    next_date = first_date + pd.offsets.MonthBegin()
    if not dates.between(first_date, next_date).all():
        raise ValueError("Monthly day-ahead dates exceed the declared month and its final padding date.")
    if not wide["PNODE Name"].map(lambda value: isinstance(value, str) and bool(value.strip())).all():
        raise ValueError("Selected prices require their explicit published Pnode identifiers.")
    frame = wide.melt(id_vars=identifiers, value_vars=hours, var_name="HE", value_name="LMP")
    frame["LMP"] = frame.LMP.mask(frame.LMP.str.strip().eq(""), np.nan)
    if frame.LMP.map(lambda value: isinstance(value, (bool, np.bool_))).any():
        raise ValueError("Day-ahead LMP cannot be boolean.")
    frame["LMP"] = _numeric_observations(frame.LMP, "LMP").astype(float)
    if np.isinf(frame.LMP).any():
        raise ValueError("Day-ahead LMP cannot be infinite.")
    frame["Interval Start"] = (pd.to_datetime(frame.Date, format="%Y/%m/%d", utc=True)
                               + pd.to_timedelta(frame.HE.str[2:].astype(int) - 1, unit="h"))
    start = first_date.tz_localize(None).tz_localize("America/Chicago").tz_convert("UTC")
    end = next_date.tz_localize(None).tz_localize("America/Chicago").tz_convert("UTC")
    inside = frame["Interval Start"].ge(start) & frame["Interval Start"].lt(end)
    if frame.loc[~inside, "LMP"].notna().any():
        raise ValueError("Monthly day-ahead prices outside the declared operating month must be null padding.")
    padding = int((~inside).sum())
    frame = frame.loc[inside].copy()
    if frame.empty:
        raise ValueError("No cached observations within the declared day-ahead operating month.")
    frame["Interval End"] = frame["Interval Start"] + pd.Timedelta(1, unit="h")
    frame["Market"] = "DAY_AHEAD_HOURLY"
    frame = frame.rename(columns={"Settlement Location Name": "Location", "PNODE Name": "Pnode"})
    frame = frame[["Interval Start", "Interval End", "Market", "Location", "Pnode", "LMP"]].drop_duplicates()
    if frame.duplicated(["Location", "Interval Start"]).any():
        raise ValueError("Conflicting settlement price or Pnode revisions must be reconciled first.")
    frame = frame.sort_values(["Location", "Interval Start"]).reset_index(drop=True)
    return frame, {"source_type": "assumption", "ref": json.dumps({
        "method": f"monthly_da_lmp_utc_he_v1; exact Settlement Location selection={sorted(settlement_locations)!r}; "
                  f"LMP only, USD/MWh; null boundary padding discarded={padding}; "
                  "interior missing observations retained; no gap filling; publication vintage unverified",
        "inputs": [{**origin, "ref": f"{origin['ref']}; cached_sha256={digest}"},
                   {"source_type": "assumption", "ref": "Date UTC midnight + (HE - 1) hours is the interval start; "
                    "inferred from 2024 monthly/daily correspondence including DST, not explicitly defined by the reviewed SPP guide; "
                    f"declared America/Chicago operating month [{start.isoformat()}, {end.isoformat()})"}],
    }, sort_keys=True, separators=(",", ":"))}


def _complete_hourly_power(raw: pd.DataFrame, value_column: str, *, nonnegative=False) -> pd.DataFrame:
    """Mean MW or mean price only when interval coverage fills the whole hour.

    gridstatus returns interval-start/end timestamps. Never sum MW as if it were
    energy, forward-fill missing intervals, or combine overlapping revisions.
    Five-minute and hourly intervals are supported; an hour must use one cadence.
    """
    names = ["Interval Start", "Interval End", value_column]
    if not set(names).issubset(raw.columns) or raw.empty:
        raise ValueError(f"Cached observations require nonempty {names}.")
    frame = raw[names].drop_duplicates().copy()
    for name in names[:2]:
        parsed = [pd.Timestamp(value) for value in frame[name]]
        if any(pd.isna(value) or value.tzinfo is None for value in parsed):
            raise ValueError("Cached interval timestamps require explicit timezones.")
        frame[name] = pd.to_datetime(parsed, utc=True)
    if frame.duplicated("Interval Start").any():
        raise ValueError("Conflicting interval revisions must be reconciled before screening.")
    duration = (frame["Interval End"] - frame["Interval Start"]).dt.total_seconds()
    if not duration.isin([300., 3600.]).all():
        raise ValueError("Only complete five-minute or hourly source intervals are supported.")
    if not frame["Interval Start"].eq(frame["Interval Start"].dt.floor("5min")).all():
        raise ValueError("Source intervals must align to five-minute boundaries.")
    hour = frame["Interval Start"].dt.floor("h")
    if (frame["Interval End"] > hour + pd.Timedelta(1, unit="h")).any():
        raise ValueError("Source intervals cannot cross UTC-hour boundaries.")
    if frame[value_column].map(lambda value: isinstance(value, (bool, np.bool_))).any():
        raise ValueError("Cached numeric observations cannot be booleans.")
    frame[value_column] = _numeric_observations(frame[value_column], value_column).astype(float)
    if np.isinf(frame[value_column]).any():
        raise ValueError("Cached numeric observations cannot be infinite.")
    if nonnegative and frame[value_column].lt(0).any():
        raise ValueError("Negative net wind observations must be reconciled before hourly averaging.")
    frame["duration"] = duration
    frame["timestamp_utc"] = hour
    rows = []
    for stamp, group in frame.groupby("timestamp_utc", sort=True):
        if group.duration.nunique() != 1:
            raise ValueError("Overlapping mixed-cadence observations must be reconciled first.")
        expected = int(3600 / group.duration.iloc[0])
        known = len(group) == expected and group[value_column].notna().all()
        rows.append({"timestamp_utc": stamp, value_column: float(group[value_column].mean()) if known else np.nan})
    return pd.DataFrame(rows)


def prepare_wind_inputs(
    generation: pd.DataFrame, system_load: pd.DataFrame, prices: pd.DataFrame, *,
    price_locations: Mapping[str, str], market: str, generation_format: str = "gridstatus",
) -> pd.DataFrame:
    """Normalize existing SPP cache formats without geographic or price averaging.

    price_locations explicitly maps each output location_id to ONE exact provider
    Location. The caller must separately provide verified matching wind/load scope
    and provenance to wind_oversupply_hours. There is no guessed node-to-zone map.
    historical generation uses the existing generation.normalize_generation helper;
    adjacent archives that split an hourly bin must be concatenated as raw samples
    before normalization. Partial hourly means cannot recover missing raw samples.
    gridstatus uses Wind MW with declared interval starts/ends. Fuel-mix totals
    alone do not establish the geographic footprint (notably SPP versus SWPW).
    """
    if not isinstance(price_locations, Mapping) or not price_locations or any(
        not isinstance(key, str) or not key.strip() or not isinstance(value, str) or not value.strip()
        for key, value in price_locations.items()
    ):
        raise ValueError("Provide an explicit nonempty output-location to price-location map.")
    if not isinstance(market, str) or not market.strip():
        raise ValueError("Select one exact price market explicitly.")
    required = {"timestamp_utc", "location_id", "load_mw"}
    if not required.issubset(system_load.columns) or system_load.empty:
        raise ValueError("System load requires nonempty canonical hourly observations.")
    if not system_load.location_id.map(lambda value: isinstance(value, str) and bool(value.strip())).all():
        raise ValueError("Every system-load footprint must have a nonempty name.")
    if system_load.location_id.nunique(dropna=False) != 1:
        raise ValueError("Provide one system load footprint, not individual-zone load rows.")
    # A caller may use a different verified system label, but cannot mix footprints.
    load = system_load[["timestamp_utc", "load_mw"]].copy()
    parsed = [pd.Timestamp(value) for value in load.timestamp_utc]
    if any(pd.isna(value) or value.tzinfo is None for value in parsed):
        raise ValueError("System load timestamps need explicit timezones.")
    load["timestamp_utc"] = pd.to_datetime(parsed, utc=True)
    if load.timestamp_utc.duplicated().any() or not load.timestamp_utc.eq(load.timestamp_utc.dt.floor("h")).all():
        raise ValueError("System load must have unique hourly interval-start timestamps.")
    if generation_format == "historical":
        # The shared normalizer handles missing/conflicting observations, but raw
        # booleans or negative net wind must not disappear inside an hourly mean.
        checked = generation.copy()
        checked.columns = checked.columns.str.strip()
        if checked.columns.duplicated().any():
            raise ValueError("Historical generation has duplicate normalized column names.")
        components = ["Wind Market", "Wind Self"]
        if not set(components).issubset(checked.columns):
            raise ValueError("Historical generation requires both wind components.")
        for name in components:
            if checked[name].map(lambda value: isinstance(value, (bool, np.bool_))).any():
                raise ValueError("Historical wind observations cannot be booleans.")
            checked[name] = _numeric_observations(checked[name], name).astype(float)
            if np.isinf(checked[name]).any():
                raise ValueError("Historical wind observations cannot be infinite.")
        if checked[components].sum(axis=1, min_count=2).lt(0).any():
            raise ValueError("Negative net wind observations must be reconciled before hourly averaging.")
        wind = normalize_generation(generation)[["timestamp_utc", "wind_mw"]].rename(columns={"wind_mw": "system_wind_mw"})
    elif generation_format == "gridstatus":
        wind = _complete_hourly_power(generation, "Wind", nonnegative=True).rename(columns={"Wind": "system_wind_mw"})
    else:
        raise ValueError("generation_format must be gridstatus or historical.")
    price_columns = {"Interval Start", "Interval End", "Market", "Location", "LMP"}
    if not price_columns.issubset(prices.columns):
        raise ValueError("Cached LMP input needs interval timestamps, Market, Location and LMP.")
    selected = prices[prices.Market == market]
    if selected.empty:
        raise ValueError("The selected market has no cached prices.")
    base = load.rename(columns={"load_mw": "system_load_mw"}).merge(wind, how="left", on="timestamp_utc", validate="one_to_one")
    frames = []
    for location, node in sorted(price_locations.items()):
        local = selected[selected.Location == node]
        if local.empty:
            raise ValueError(f"No cached prices for the explicitly selected location {node}.")
        lmp = _complete_hourly_power(local, "LMP").rename(columns={"LMP": "lmp_usd_mwh"})
        frame = base.merge(lmp, how="left", on="timestamp_utc", validate="one_to_one")
        frame["location_id"] = location
        frames.append(frame)
    return pd.concat(frames, ignore_index=True).sort_values(["location_id", "timestamp_utc"]).reset_index(drop=True)


def read_cached_wind_inputs(
    system_load_path: Path, *, price_locations: Mapping[str, str], market: str,
) -> pd.DataFrame:
    """Read-only adapter around the existing ingest.load_dataset cache readers.

    Run from the repository root because the existing reader's RAW_DIR is relative.
    Missing caches raise FileNotFoundError; there is no fetch or fake-data fallback.
    Returned attrs identify cached bytes and exact market/node selections. Before
    scoring, the integration caller must supply each input's source and matching
    footprint explicitly; cache existence is not proof of source or spatial scope.
    """
    if Path.cwd().resolve() != ROOT.resolve():
        raise ValueError("Run the existing relative-path cache reader from the repository root.")
    load_path = Path(system_load_path)
    load = read_hourly(load_path)
    generation, prices = ingest.load_dataset("fuel_mix"), ingest.load_dataset("lmp")
    frame = prepare_wind_inputs(generation, load, prices, price_locations=price_locations, market=market)
    frame.attrs = {
        "load_sha256": fingerprint(load_path), "load_location_id": str(load.location_id.iloc[0]),
        "price_market": market, "price_locations": dict(sorted(price_locations.items())),
        "cache_hashes": {str(path): fingerprint(path) for dataset in ("fuel_mix", "lmp")
                         for path in sorted((ingest.RAW_DIR / dataset).glob("*.parquet"))},
        "scope_status": "caller must establish common system wind/load footprint before screening",
    }
    return frame


def read_cached_generation_archive(year: int) -> tuple[pd.DataFrame, dict]:
    """Read the existing historical downloader's cached CSV, with its manifest.

    Offline preparation only. An absent archive stays missing; call the existing
    ingest.fetch_public_evidence downloader separately to populate this cache.
    The returned raw table retains ALL fuel columns for separate carbon accounting.
    Do not use a normalized wind/solar-only table as a complete grid fuel mix.
    """
    if isinstance(year, bool) or not isinstance(year, int) or not 2019 <= year <= 2025:
        raise ValueError("Historical generation reader supports declared 2019..2025 archive years.")
    path = ROOT / f"data/raw/spp/evidence/genmix_{year}.parquet"
    cached = pd.read_parquet(path)
    if len(cached) != 1 or not cached.columns.is_unique or not {"request_url", "content", "source_json"}.issubset(cached.columns):
        raise ValueError("Invalid cached historical-generation document.")
    row = cached.iloc[0]
    url = f"https://portal.spp.org/file-browser-api/download/generation-mix-historical?path=/GenMix_{year}.csv"
    manifest = _strict_wind_json(row.source_json)
    if not isinstance(manifest, dict) or row.request_url != url or manifest.get("requested_url") != url or manifest.get("ref") != url:
        raise ValueError("Historical generation cache must match its exact SPP archive URL.")
    origin = source({key: manifest.get(key) for key in ("source_type", "ref")})
    if not isinstance(row.content, (bytes, bytearray)):
        raise ValueError("Cached generation content must contain CSV bytes.")
    digest = hashlib.sha256(row.content).hexdigest()
    if manifest.get("sha256") != digest:
        raise ValueError("Cached generation bytes do not match their source fingerprint.")
    stream = io.BytesIO(row.content)
    _archive_csv_header(stream)
    frame = pd.read_csv(stream)
    if frame.empty:
        raise ValueError("Cached generation CSV is empty.")
    return frame, {**origin, "ref": f"{origin['ref']}; cached_sha256={digest}; "
                                  "GMT MKT Interval treated as observation time by existing generation normalizer; "
                                  "left-closed hourly bins require 12 distinct five-minute observations; "
                                  + GENMIX_SOURCE_QUALIFICATION["text"] + "; " + GENMIX_SOURCE_QUALIFICATION["source"]["ref"]}


def read_cached_monthly_load(year: int = 2025) -> tuple[pd.DataFrame, dict]:
    """Read all twelve explicit 2025 monthly load evidence records, without I/O writes.

    Returns raw MarketHour + seventeen component columns for the existing
    pipeline.prepare.normalize_legacy_load producer. It does not aggregate,
    fill gaps, pick revisions, or fetch. Exact duplicate/conflicting source rows
    are retained so the shared normalizer can report and handle them as before.
    Timestamp interpretation matches that normalizer and installed gridstatus:
    MarketHour is UTC hour ending; its start belongs to the named Central month.
    Twelve files do not imply twelve complete months. Publication vintage and
    correspondence to any local data-center connection remain unestablished.
    """
    if isinstance(year, bool) or not isinstance(year, int) or year != 2025:
        raise ValueError("Monthly evidence reader is reviewed for exactly the 2025 legacy load archive.")
    columns = ["MarketHour", *LOAD_AREAS]
    frames, origins = [], {}
    for month in range(1, 13):
        key = f"{year}_{month:02d}"
        path = ROOT / f"data/raw/spp/evidence/hourly_load_{key}.parquet"
        cached = pd.read_parquet(path)
        if len(cached) != 1 or not cached.columns.is_unique or not {"request_url", "content", "source_json"}.issubset(cached.columns):
            raise ValueError("Invalid cached monthly-load evidence record.")
        row = cached.iloc[0]
        url = f"https://portal.spp.org/file-browser-api/download/hourly-load?path=/{year}/HOURLY_LOAD-{year}{month:02d}.csv"
        manifest = _strict_wind_json(row.source_json)
        if not isinstance(manifest, dict) or row.request_url != url or manifest.get("requested_url") != url or manifest.get("ref") != url:
            raise ValueError("Monthly load cache must identify its exact month and SPP archive URL.")
        origin = source({name: manifest.get(name) for name in ("source_type", "ref")})
        if not isinstance(row.content, (bytes, bytearray)) or len(row.content) > 5_000_000:
            raise ValueError("Cached monthly load must contain bounded CSV bytes.")
        digest = hashlib.sha256(row.content).hexdigest()
        if manifest.get("sha256") != digest:
            raise ValueError("Cached monthly-load bytes do not match their source fingerprint.")
        stream = io.BytesIO(row.content)
        _archive_csv_header(stream)
        frame = pd.read_csv(stream)
        frame.columns = frame.columns.str.strip()
        if frame.empty or set(frame.columns) != set(columns):
            raise ValueError("Monthly load requires MarketHour and exactly the seventeen reviewed component areas.")
        if frame.MarketHour.map(lambda value: isinstance(value, (Real, bool, np.bool_))).any():
            raise ValueError("Monthly MarketHour values cannot be numeric or boolean.")
        ends = _archive_gmt_times(frame.MarketHour)
        starts = (ends - pd.Timedelta(1, unit="h")).dt.tz_convert("America/Chicago")
        if (ends.isna().any() or not ends.eq(ends.dt.floor("h")).all()
                or not starts.dt.strftime("%Y-%m").eq(f"{year}-{month:02d}").all()):
            raise ValueError("Monthly load interval starts disagree with their Central operating month.")
        # Validate raw components; leave missingness and values unchanged for the
        # shared all-components-required sum and conflicting-revision treatment.
        # Signed regional values retain the shared normalizer's semantics; its
        # downstream validation rejects a negative system total.
        for name in LOAD_AREAS:
            if frame[name].map(lambda value: isinstance(value, (bool, np.bool_))).any():
                raise ValueError("Monthly load components cannot be boolean.")
            numeric = _numeric_observations(frame[name], f"Monthly load {name}")
            if np.isinf(numeric).any():
                raise ValueError("Monthly load components must be finite numeric values or missing.")
        frames.append(frame[columns].assign(_interval_end=ends))
        origins[key] = {**origin, "ref": f"{url}; cached_sha256={digest}"}
    result = pd.concat(frames, ignore_index=True).sort_values("_interval_end", kind="stable").drop(columns="_interval_end").reset_index(drop=True)
    kinds = {item["source_type"] for item in origins.values()}
    kind = "assumption" if "assumption" in kinds else "model" if "model" in kinds else "data"
    origin = {"source_type": kind, "ref": "; ".join(item["ref"] for item in origins.values()) + "; "
              "all twelve named monthly evidence files; MarketHour parsed as UTC interval end per "
              "pipeline.prepare.normalize_legacy_load and gridstatus.SPP._handle_market_end_to_interval; "
              "minus one hour checked against each Central operating month; no gap filling or revision selection"}
    result.attrs = {"monthly_sources": origins, "source": origin,
                    "next_step": "pipeline.prepare.normalize_legacy_load; sum requires all seventeen components"}
    return result, origin


def read_cached_historical_wind_inputs(
    system_load_path: Path, *, year: int, price_locations: Mapping[str, str], market: str,
) -> pd.DataFrame:
    """Use historical full-mix archive plus existing load/LMP caches, without fetch.

    Reuses generation.normalize_generation through prepare_wind_inputs. The
    derived generation_source remains an assumption because of the unresolved
    observation-time binning and dispatch-target interpretation. The original
    document source is retained separately as raw_generation_source.
    """
    if Path.cwd().resolve() != ROOT.resolve():
        raise ValueError("Run the existing relative-path cache reader from the repository root.")
    raw, generation_source = read_cached_generation_archive(year)
    load = read_hourly(Path(system_load_path))
    if set(load.location_id) != {"SPP_SYSTEM"} or not load.timestamp_utc.dt.year.eq(year).all():
        raise ValueError("Historical join needs matching-year SPP_SYSTEM load, not a zone load or another vintage.")
    frame = prepare_wind_inputs(raw, load, ingest.load_dataset("lmp"), price_locations=price_locations,
                                market=market, generation_format="historical")
    normalized_origin = {"source_type": "assumption", "ref": generation_source["ref"]
                         + "; derived historical wind input: declared observation-time hourly binning; "
                           "reported generation/dispatch mix, not verified metered energy or live publication availability"}
    frame.attrs = {"generation_source": normalized_origin, "raw_generation_source": generation_source,
                   "source_qualification": GENMIX_SOURCE_QUALIFICATION,
                   "load_sha256": fingerprint(Path(system_load_path)),
                   "price_market": market, "price_locations": dict(sorted(price_locations.items())),
                   "price_cache_hashes": {str(path): fingerprint(path) for path in sorted((ingest.RAW_DIR / "lmp").glob("*.parquet"))},
                   "scope_status": "historical SPP_SYSTEM wind/load; local deliverability still unobserved"}
    return frame


def wind_oversupply_hours(
    hourly: pd.DataFrame, *, sources: Mapping[str, Mapping],
    wind_scope: str, load_scope: str, policy: WindPolicy | None = None,
) -> pd.DataFrame:
    """Classify observed hours; nullable Boolean keeps absent evidence unknown.

    sources maps each INPUT_COLUMNS name to {source_type, ref}. Scope equality is
    necessary, but the caller must also establish actual common coverage. One
    system observation is allowed alongside several explicitly mapped price nodes.
    Contradictory system observations at the same timestamp are rejected.
    """
    policy = WindPolicy() if policy is None else policy
    if not isinstance(policy, WindPolicy):
        raise ValueError("policy must be a WindPolicy.")
    if not isinstance(wind_scope, str) or not wind_scope.strip() or wind_scope != load_scope:
        raise ValueError("Wind and load must have the same explicitly identified footprint.")
    if not isinstance(sources, Mapping) or set(sources) != set(INPUT_COLUMNS):
        raise ValueError(f"sources must name exactly {INPUT_COLUMNS}.")
    origins = {name: source(sources[name]) for name in INPUT_COLUMNS}
    required = {"timestamp_utc", "location_id", *INPUT_COLUMNS}
    if not required.issubset(hourly.columns) or hourly.empty:
        raise ValueError("Nonempty hourly input needs timestamp_utc, location_id, system wind/load and local LMP.")
    frame = hourly[["timestamp_utc", "location_id", *INPUT_COLUMNS]].copy()
    times = [pd.Timestamp(value) for value in frame.timestamp_utc]
    if any(pd.isna(value) or value.tzinfo is None for value in times):
        raise ValueError("Every timestamp must have an explicit timezone.")
    frame["timestamp_utc"] = pd.to_datetime(times, utc=True)
    if not frame.timestamp_utc.eq(frame.timestamp_utc.dt.floor("h")).all():
        raise ValueError("Input must contain hourly interval-start timestamps.")
    if not all(isinstance(value, str) and bool(value.strip()) for value in frame.location_id):
        raise ValueError("Every location_id must be a nonempty string.")
    if frame.duplicated(["location_id", "timestamp_utc"]).any():
        raise ValueError("Duplicate location/hour observations must be reconciled first.")
    for name in INPUT_COLUMNS:
        if any(isinstance(value, (bool, np.bool_)) for value in frame[name]):
            raise ValueError(f"{name} cannot contain boolean observations.")
        frame[name] = _numeric_observations(frame[name], name).astype(float)
        if np.isinf(frame[name]).any():
            raise ValueError(f"{name} contains infinity.")
        if name != "lmp_usd_mwh" and frame[name].lt(0).any():
            raise ValueError(f"{name} cannot be negative.")
    for name in ("system_wind_mw", "system_load_mw"):
        if frame.groupby("timestamp_utc")[name].nunique(dropna=False).gt(1).any():
            raise ValueError(f"Conflicting {name} for the same system and hour.")
    known = frame[list(INPUT_COLUMNS)].notna().all(axis=1) & frame.system_load_mw.gt(0)
    with np.errstate(over="ignore", divide="ignore", invalid="ignore"):
        frame["wind_share"] = frame.system_wind_mw / frame.system_load_mw.where(frame.system_load_mw.gt(0))
    if np.isinf(frame.wind_share).any():
        raise ValueError("Wind/load ratio overflow; inspect input units and scale.")
    flag = frame.wind_share.ge(policy.minimum_wind_share) & frame.lmp_usd_mwh.le(policy.maximum_lmp_usd_mwh)
    frame["wind_oversupply_proxy"] = flag.astype("boolean").where(known, pd.NA)
    frame = frame.sort_values(["location_id", "timestamp_utc"]).reset_index(drop=True)
    frame.attrs = {"method": "high_wind_low_local_price_v1", "scope": wind_scope,
                   "sources": origins, "policy_source": policy.source}
    return frame


def summarize_wind(
    hourly: pd.DataFrame, *, sources: Mapping[str, Mapping], wind_scope: str, load_scope: str,
    flexible_load_mw: Mapping, available_fraction: Mapping, policy: WindPolicy | None = None,
) -> list[dict]:
    """Per-location screening counts and explicitly assumed energy opportunity.

    flexible_load_mw and available_fraction each use {value,source_type,ref}.
    available_fraction is the assumed share of flexible capacity that can ramp up
    in every selected hour. It is NOT local transmission headroom or measured
    curtailed wind. No zone shares, site exposure factor or growth is inferred.
    """
    def parameter(value, name, maximum=None):
        if not isinstance(value, Mapping) or set(value) != {"value", "source_type", "ref"}:
            raise ValueError(f"{name} requires value, source_type and ref.")
        origin = source({key: value[key] for key in ("source_type", "ref")})
        return finite_number(value["value"], name, minimum=0, maximum=maximum), origin

    load, load_origin = parameter(flexible_load_mw, "flexible_load_mw")
    fraction, fraction_origin = parameter(available_fraction, "available_fraction", 1)
    frame = wind_oversupply_hours(hourly, sources=sources, wind_scope=wind_scope, load_scope=load_scope, policy=policy)
    observation_source = {
        "source_type": "assumption" if any(v["source_type"] == "assumption" for v in frame.attrs["sources"].values())
                       else "model" if any(v["source_type"] == "model" for v in frame.attrs["sources"].values()) else "data",
        "ref": "; ".join(f"{name}: {origin['ref']}" for name, origin in frame.attrs["sources"].items()),
    }
    proxy_source = {"source_type": "assumption", "ref": frame.attrs["policy_source"]["ref"] + "; " + observation_source["ref"]}
    energy_source = {
        "source_type": "assumption",
        "ref": f"{proxy_source['ref']}; flexible_load_mw={load!r} ({load_origin['ref']}); "
               f"available_fraction={fraction!r} ({fraction_origin['ref']}); "
               "one-hour intervals; scenario capacity times proxy hours, not measured recoverable wind",
    }
    output = []
    for location, group in frame.groupby("location_id", sort=True, observed=True):
        flags = group.wind_oversupply_proxy
        start, end = group.timestamp_utc.min(), group.timestamp_utc.max() + pd.Timedelta(1, unit="h")
        expected_start = pd.Timestamp(year=start.year, month=1, day=1, tz="UTC")
        expected_end = pd.Timestamp(year=start.year + 1, month=1, day=1, tz="UTC")
        complete = start == expected_start and end == expected_end and flags.notna().all()
        complete = bool(complete and len(group) == (expected_end - expected_start) / pd.Timedelta(1, unit="h"))
        count = int(flags.sum())
        mwh = wind_scenario_mwh(count, load, fraction) if flags.notna().any() else None
        missing_hours = int((end - start) / pd.Timedelta(1, unit="h")) - len(group)
        annual_source = energy_source if complete else {
            "source_type": "assumption",
            "ref": "unavailable: requires one complete calendar year of evaluable hourly observations; " + energy_source["ref"],
        }
        output.append({
            "location_id": location, "method": frame.attrs["method"], "system_scope": wind_scope,
            "period_start_utc": start.isoformat(), "period_end_exclusive_utc": end.isoformat(),
            "observed_hours": sourced(len(group), observation_source),
            "evaluable_hours": sourced(int(flags.notna().sum()), observation_source),
            "unknown_hours": sourced(int(flags.isna().sum()), observation_source),
            "missing_interval_hours": sourced(missing_hours, observation_source),
            "proxy_hours": sourced(count if flags.notna().any() else None, proxy_source),
            "wind_absorption_mwh_in_observed_hours": sourced(mwh, energy_source),
            "wind_absorption_mwh_per_year": sourced(mwh if complete else None, annual_source),
            "energy_model": "declared_available_capacity_times_proxy_hours_v1",
            "scenario_inputs": {
                "flexible_load_mw": sourced(load, load_origin),
                "available_fraction": sourced(fraction, fraction_origin),
            },
            "basis": "Flexible-load energy scenario over evaluable observed high-wind/low-price hours only; "
                     "wind curtailment, local deliverability and recoverable MW are unobserved.",
        })
    return output


# This independent event classifier is an offline research experiment. It does
# not replace the high-wind/low-price policy screen above or any estimate API.
WIND_CLASSIFIER_FEATURES = tuple(f"{name}_lag_{lag}h"
                                 for lag in (1, 24) for name in ("system_wind_mw", "system_load_mw"))
WIND_CLASSIFIER_SCHEMA = "spp-wind-event-logistic-v1"
WIND_CLASSIFIER_SPLITS = {
    "train": ("2024-01-01T00:00:00+00:00", "2024-09-01T00:00:00+00:00"),
    "calibration": ("2024-09-01T00:00:00+00:00", "2024-10-01T00:00:00+00:00"),
    "test": ("2024-10-01T00:00:00+00:00", "2025-01-01T00:00:00+00:00"),
}
WIND_CLASSIFIER_LEGACY_LIMITATION = (
    "Historical replay of reported SPP-system wind-curtailment occurrence, not a verified live forecast. "
    "Publication and revision timestamps for lagged actuals are unverified. A positive target means at least "
    "one reported five-minute event within a completely evaluable hour, not sixty minutes of curtailment, "
    "site deliverability or absorbable energy. One 2024 autumn holdout does not establish other-year or site skill. "
    "Event probability is not the product's model-confidence estimate."
)
WIND_CLASSIFIER_LIMITATION = (
    WIND_CLASSIFIER_LEGACY_LIMITATION.replace("lagged actuals", "lagged reported inputs")
    + " Historical GenMix inputs include Self dispatch targets; metered generation and exact interval semantics "
      "are not established. See the attached source qualification."
)


def _wind_json_digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _wind_frame_digest(frame):
    """Fingerprint canonical values without rounding floats or inventing zeros."""
    rows = []
    for row in frame.itertuples(index=False, name=None):
        rows.append([None if pd.isna(value) else value.isoformat() if isinstance(value, pd.Timestamp)
                     else value.item() if isinstance(value, np.generic) else value for value in row])
    return _wind_json_digest({"columns": list(frame.columns), "rows": rows})


def _wind_hour_index(values):
    stamps = [pd.Timestamp(value) if not isinstance(value, (Real, bool, np.bool_)) else pd.NaT for value in values]
    if not stamps or any(pd.isna(stamp) or stamp.tzinfo is None for stamp in stamps):
        raise ValueError("Wind classifier timestamps require explicit timezones and nonnumeric hourly values.")
    index = pd.DatetimeIndex(pd.to_datetime(stamps, utc=True), name="timestamp_utc")
    if index.duplicated().any() or not index.equals(index.floor("h")):
        raise ValueError("Wind classifier timestamps must be unique hourly interval starts.")
    return index


def _wind_observations(hourly, sources):
    columns = ("system_wind_mw", "system_load_mw")
    if not isinstance(sources, Mapping) or set(sources) != set(columns):
        raise ValueError("Supply source objects for exactly system_wind_mw and system_load_mw.")
    origins = {name: source(sources[name]) for name in columns}
    if any(value["source_type"] == "model" for value in origins.values()):
        raise ValueError("This experiment requires observed wind/load, not modeled input forecasts.")
    needed = {"timestamp_utc", "location_id", *columns}
    if not isinstance(hourly, pd.DataFrame) or hourly.empty or not hourly.columns.is_unique or not needed.issubset(hourly.columns):
        raise ValueError("Wind classifier needs nonempty canonical hourly wind/load observations.")
    # Nullable-string equality leaves NA, and all() otherwise skips those rows.
    if not hourly.location_id.notna().all() or not hourly.location_id.eq("SPP_SYSTEM").all():
        raise ValueError("Wind classifier supports only one declared SPP_SYSTEM observation per hour.")
    frame = hourly[["timestamp_utc", *columns]].copy()
    frame["timestamp_utc"] = _wind_hour_index(frame.timestamp_utc)
    for name in columns:
        if frame[name].map(lambda value: isinstance(value, (bool, np.bool_))).any():
            raise ValueError("Wind classifier actual observations cannot be boolean.")
        frame[name] = _numeric_observations(frame[name], name).astype(float)
        if np.isinf(frame[name]).any() or frame[name].lt(0).any():
            raise ValueError("Wind classifier actual observations must be finite nonnegative values or missing.")
    # A zero system-load observation is unevaluable, not an invented operating state.
    frame.loc[frame.system_load_mw.eq(0), "system_load_mw"] = np.nan
    return frame.sort_values("timestamp_utc").reset_index(drop=True), origins


def prepare_wind_classifier_features(hourly, *, sources, target_timestamps=None):
    """Four fixed exact-time lags, calculated before excluding missing examples.

    Input rows declare the common SPP_SYSTEM footprint. At target start t the
    features are hourly wind/load observations for [t-1h,t) and [t-24h,t-23h).
    These are historical reported inputs, not verified as-of-published meter readings.
    A missing current-hour input does not prevent prediction if its lag inputs
    exist. Supply target_timestamps to request such hours or future intervals.
    No interpolation, current-hour values, target labels or price features enter.
    """
    frame, origins = _wind_observations(hourly, sources)
    target = _wind_hour_index(frame.timestamp_utc if target_timestamps is None else target_timestamps).sort_values()
    observations = frame.set_index("timestamp_utc")
    result = pd.DataFrame({"timestamp_utc": target, "location_id": "SPP_SYSTEM"})
    for lag in (1, 24):
        requested = target - pd.Timedelta(lag, unit="h")
        for name in ("system_wind_mw", "system_load_mw"):
            result[f"{name}_lag_{lag}h"] = observations[name].reindex(requested).to_numpy()
    result.attrs = {"sources": origins, "system_scope": "SPP_SYSTEM", "forecast_asof_verified": False,
                    "feature_input_sha256": _wind_frame_digest(frame), "limitation": WIND_CLASSIFIER_LIMITATION}
    return result


def _wind_classifier_labels(labels):
    needed = {"timestamp_utc", "location_id", "wind_curtailment_event",
              "observed_five_minute_samples", "evaluable_five_minute_samples"}
    if not isinstance(labels, pd.DataFrame) or labels.empty or not labels.columns.is_unique or not needed.issubset(labels.columns):
        raise ValueError("Wind classifier requires independent VER hourly labels and completeness counts.")
    if labels.attrs.get("method") != "reported_system_wind_curtailment_any_category_v1" or labels.attrs.get("system_scope") != "SPP_SYSTEM":
        raise ValueError("Wind classifier requires the declared independent VER event-label method and SPP_SYSTEM scope.")
    origin = source(labels.attrs.get("source"))
    if origin["source_type"] == "model":
        raise ValueError("Independent VER targets cannot be another model's generated labels.")
    if not labels.location_id.notna().all() or not labels.location_id.eq("SPP_SYSTEM").all():
        raise ValueError("Independent wind-event labels cannot be relabeled as individual sites or zones.")
    frame = labels[["timestamp_utc", "wind_curtailment_event", "observed_five_minute_samples",
                    "evaluable_five_minute_samples"]].copy()
    frame["timestamp_utc"] = _wind_hour_index(frame.timestamp_utc)
    for name in ("observed_five_minute_samples", "evaluable_five_minute_samples"):
        values = [finite_number(value, name, minimum=0, maximum=12) for value in frame[name]]
        if any(not value.is_integer() for value in values):
            raise ValueError("VER completeness counts must be integers.")
        frame[name] = [int(value) for value in values]
    if frame.evaluable_five_minute_samples.gt(frame.observed_five_minute_samples).any():
        raise ValueError("VER evaluable samples cannot exceed observed samples.")
    if not frame.wind_curtailment_event.map(lambda value: isinstance(value, (bool, np.bool_)) or value is None or value is pd.NA or isinstance(value, Real) and np.isnan(value)).all():
        raise ValueError("VER hourly targets must be nullable Booleans, not numeric or inferred rule targets.")
    frame["wind_curtailment_event"] = frame.wind_curtailment_event.astype("boolean")
    complete = frame.observed_five_minute_samples.eq(12) & frame.evaluable_five_minute_samples.eq(12)
    if not frame.wind_curtailment_event.notna().eq(complete).all():
        raise ValueError("VER target availability must match twelve observed and evaluable intervals.")
    return frame.sort_values("timestamp_utc").reset_index(drop=True), origin


def _wind_sigmoid(scores):
    if not np.isfinite(scores).all():
        raise ValueError("Wind classifier score overflow; inspect input units or fitted parameters.")
    # Algebraically identical on either side of zero, without exp overflow.
    magnitude = np.exp(-np.abs(scores))
    return np.where(scores >= 0, 1. / (1. + magnitude), magnitude / (1. + magnitude))


def _wind_parameter_vector(values, name, length):
    if not isinstance(values, list) or len(values) != length:
        raise ValueError(f"Wind classifier {name} has the wrong shape.")
    return np.asarray([finite_number(value, name) for value in values], dtype=float)


def _validate_wind_classifier_bundle(bundle):
    if not isinstance(bundle, dict) or set(bundle) != {"schema_version", "model_id", "features", "standardizer", "base_model", "calibrator", "manifest"}:
        raise ValueError("Invalid JSON wind classifier bundle.")
    if bundle["schema_version"] != WIND_CLASSIFIER_SCHEMA or bundle["features"] != list(WIND_CLASSIFIER_FEATURES):
        raise ValueError("Unsupported wind classifier schema or feature order.")
    try:
        expected = _wind_json_digest({key: value for key, value in bundle.items() if key != "model_id"})
    except (TypeError, ValueError) as exc:
        raise ValueError("Wind classifier bundle must contain finite JSON values.") from exc
    if bundle["model_id"] != expected:
        raise ValueError("Wind classifier parameters or manifest do not match their model fingerprint.")
    scaler, base, calibration = bundle["standardizer"], bundle["base_model"], bundle["calibrator"]
    if not isinstance(scaler, dict) or set(scaler) != {"mean", "scale"} or not isinstance(base, dict) or set(base) != {"coefficients", "intercept"}:
        raise ValueError("Invalid wind classifier parameter dictionaries.")
    mean = _wind_parameter_vector(scaler["mean"], "mean", 4)
    scale = _wind_parameter_vector(scaler["scale"], "scale", 4)
    if not (scale > 0).all():
        raise ValueError("Wind classifier scales must be positive.")
    coefficient = _wind_parameter_vector(base["coefficients"], "coefficients", 4)
    intercept = finite_number(base["intercept"], "intercept")
    if calibration is not None:
        if not isinstance(calibration, dict) or set(calibration) != {"coefficient", "intercept"}:
            raise ValueError("Invalid wind classifier calibration parameters.")
        calibration = {key: finite_number(value, key) for key, value in calibration.items()}
    manifest = bundle["manifest"]
    if not isinstance(manifest, dict) or manifest.get("system_scope") != "SPP_SYSTEM" or manifest.get("forecast_asof_verified") is not False:
        raise ValueError("Wind classifier bundle must preserve its research scope and unverified publication timing.")
    expected_splits = {name: list(bounds) for name, bounds in WIND_CLASSIFIER_SPLITS.items()}
    if (manifest.get("status") != "research_only_no_production_promotion"
            or manifest.get("target_method") != "reported_system_wind_curtailment_any_category_v1"
            or manifest.get("split_bounds") != expected_splits
            or manifest.get("limitation") not in (WIND_CLASSIFIER_LEGACY_LIMITATION, WIND_CLASSIFIER_LIMITATION)
            or type(manifest.get("calibration_minimum_per_class")) is not int
            or manifest["calibration_minimum_per_class"] != 20):
        raise ValueError("Wind classifier manifest does not preserve the predeclared experiment or honesty framing.")
    requested, status = manifest.get("calibration_requested"), manifest.get("calibration_status")
    valid_statuses = {"insufficient_september_class_support", "september_calibration_did_not_converge"}
    if (type(requested) is not bool
            or calibration is not None and (not requested or status != "regularized_sigmoid_fitted_on_september_only")
            or calibration is None and (status not in valid_statuses if requested else status != "disabled_by_declared_policy")):
        raise ValueError("Wind classifier calibration parameters and declared fit policy disagree.")
    for name in ("training_cohort_sha256", "calibration_cohort_sha256"):
        digest = manifest.get(name)
        if name == "calibration_cohort_sha256" and not requested:
            if digest is not None:
                raise ValueError("Disabled calibration must not declare a fitted calibration cohort.")
        elif not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError("Wind classifier fitted cohort fingerprints are missing or invalid.")
    parameters = manifest.get("fit_parameters")
    expected_parameters = {"C": 1.0, "solver": "lbfgs", "class_weight": None, "max_iter": 2000, "tol": 1e-8}
    if (not isinstance(parameters, dict) or set(parameters) != {*expected_parameters, "random_state"}
            or any(parameters.get(name) != value or isinstance(parameters.get(name), bool) for name, value in expected_parameters.items())
            or type(parameters.get("random_state")) is not int or not 0 <= parameters["random_state"] < 2 ** 32):
        raise ValueError("Wind classifier fitted parameters do not match its declared fixed experiment.")
    policy_origin = source(manifest.get("policy_source"))
    if policy_origin["source_type"] != "assumption":
        raise ValueError("Wind classifier experiment choices must remain assumptions.")
    origins = manifest.get("input_sources")
    if not isinstance(origins, dict) or set(origins) != {"system_wind_mw", "system_load_mw", "labels"}:
        raise ValueError("Wind classifier fitted input provenance is incomplete.")
    for origin in origins.values():
        if source(origin)["source_type"] == "model":
            raise ValueError("Wind classifier fitted inputs must preserve independent observations or declared assumptions.")
    return mean, scale, coefficient, intercept, calibration


def _wind_predict_values(bundle, features):
    mean, scale, coefficient, intercept, calibration = _validate_wind_classifier_bundle(bundle)
    matrix = features[list(WIND_CLASSIFIER_FEATURES)].to_numpy(dtype=float)
    known = np.isfinite(matrix).all(axis=1)
    raw = np.full(len(matrix), np.nan)
    calibrated = np.full(len(matrix), np.nan)
    with np.errstate(over="ignore", invalid="ignore", divide="ignore"):
        scores = ((matrix[known] - mean) / scale) @ coefficient + intercept
    raw[known] = _wind_sigmoid(scores)
    if calibration is not None:
        with np.errstate(over="ignore", invalid="ignore"):
            calibrated_scores = calibration["coefficient"] * scores + calibration["intercept"]
        calibrated[known] = _wind_sigmoid(calibrated_scores)
    return raw, calibrated


def _wind_prediction_source(bundle, *, input_sources=None):
    origins = list(bundle["manifest"]["input_sources"].values()) + list((input_sources or {}).values())
    assumed = any(origin["source_type"] == "assumption" for origin in origins)
    return {"source_type": "assumption" if assumed else "model", "ref": f"wind-event-model:{bundle['model_id']}; offline research; "
            + ("includes assumption-sourced observations; " if assumed else "")
            + "input hashes, source objects, timing limits and policies retained in model/report manifests"}


def predict_wind_event_classifier(bundle, hourly, *, sources, target_timestamps=None):
    """Reconstruct JSON linear parameters offline; no pickle, training or writes.

    Returns source-bearing probability rows, not confidence badges or an energy
    schedule. target_timestamps may request hours absent from actual-input rows.
    Reading a supplied bundle never follows provenance refs or performs I/O.
    """
    _validate_wind_classifier_bundle(bundle)
    features = prepare_wind_classifier_features(hourly, sources=sources, target_timestamps=target_timestamps)
    raw, calibrated = _wind_predict_values(bundle, features)
    origin = _wind_prediction_source(bundle, input_sources=features.attrs["sources"])
    result = []
    for stamp, first, second in zip(features.timestamp_utc, raw, calibrated):
        roles = {"train": "training_period", "calibration": "calibration_period", "test": "heldout_2024_autumn"}
        period_role = next((roles[name] for name, (start, end) in WIND_CLASSIFIER_SPLITS.items()
                            if pd.Timestamp(start) <= stamp < pd.Timestamp(end)), "outside_evaluated_period")
        row_origin = {**origin, "ref": f"{origin['ref']}; target_timestamp_utc={stamp.isoformat()}; "
                      f"query_observations_sha256={features.attrs['feature_input_sha256']}"}
        def probability(value, reason):
            return sourced(float(value), row_origin) if np.isfinite(value) else sourced(None, {
                "source_type": "assumption", "ref": f"unavailable: {reason}; {row_origin['ref']}"})
        result.append({"timestamp_utc": stamp.isoformat(), "location_id": "SPP_SYSTEM",
                       "raw_probability": probability(first, "missing exact-time lag observations"),
                       "calibrated_probability": probability(second, "calibration not fitted" if bundle["calibrator"] is None else "missing exact-time lag observations"),
                       "forecast_asof_verified": False,
                       "target_period_role": period_role, "limitation": WIND_CLASSIFIER_LIMITATION,
                       "source_qualification": GENMIX_SOURCE_QUALIFICATION,
                       "period_role_basis": "Calendar period only; does not assert this row was used in fitting or evaluation.",
                       "feature_input_sha256": features.attrs["feature_input_sha256"],
                       "input_sources": features.attrs["sources"], "model_id": bundle["model_id"]})
    return json.loads(json.dumps(result, sort_keys=True, allow_nan=False))


def _wind_classifier_metrics(target, probabilities, origin, *, unavailable=None):
    from sklearn.metrics import roc_auc_score

    policy = {"source_type": "assumption", "ref": "wind-event-logistic-v1: ten predeclared equal-width probability bins; upper boundary exclusive except final bin"}
    missing = {"source_type": "assumption", "ref": f"unavailable: {unavailable or 'no evaluable held-out rows'}; {origin['ref']}"}
    target, probabilities = np.asarray(target, dtype=float), np.asarray(probabilities, dtype=float)
    if target.ndim != 1 or probabilities.ndim != 1 or target.shape != probabilities.shape:
        raise ValueError("Held-out probability metrics require aligned one-dimensional arrays.")
    if not np.isfinite(target).all() or not np.isin(target, (0., 1.)).all():
        raise ValueError("Held-out probability metrics require finite binary targets.")
    available = len(target) > 0 and unavailable is None
    if available and (not np.isfinite(probabilities).all() or ((probabilities < 0) | (probabilities > 1)).any()):
        raise ValueError("Held-out probability metrics require aligned finite probabilities.")
    def metric(value, reason=None):
        return sourced(None, {**missing, "ref": f"unavailable: {reason}; {origin['ref']}"} if reason else missing) if value is None else sourced(float(value), origin)
    clipped = np.clip(probabilities, np.finfo(float).eps, 1. - np.finfo(float).eps)
    result = {"status": "available" if available else unavailable or "no_evaluable_test_rows",
              "sample_count": sourced(len(target) if available else 0, origin),
              "brier_score": metric(float(np.mean((probabilities - target) ** 2)) if available else None),
              "log_loss": metric(float(-np.mean(target * np.log(clipped) + (1. - target) * np.log1p(-clipped))) if available else None),
              "roc_auc": metric(float(roc_auc_score(target, probabilities)) if available and len(np.unique(target)) == 2 else None,
                                "ROC-AUC requires both observed classes" if available else None),
              "reliability": []}
    for index in range(10):
        lower, upper = index / 10., (index + 1) / 10.
        selected = (probabilities >= lower) & ((probabilities <= upper) if index == 9 else (probabilities < upper)) if available else np.zeros(len(target), dtype=bool)
        count = int(selected.sum())
        result["reliability"].append({"lower_probability": sourced(lower, policy), "upper_probability": sourced(upper, policy),
                                      "sample_count": sourced(count, origin),
                                      "mean_probability": metric(float(probabilities[selected].mean()) if count else None, "empty probability bin" if not count else None),
                                      "observed_event_frequency": metric(float(target[selected].mean()) if count else None, "empty probability bin" if not count else None)})
    return result


def fit_wind_event_classifier(hourly, labels, *, sources, seed=2026, calibrate=True):
    """Fit a fixed offline experiment; return (JSON bundle, report, test rows).

    Standardization and unweighted L2 logistic fitting see Jan-Aug 2024 only.
    A separate regularized sigmoid sees September base decision scores only,
    if enabled and September has at least twenty eligible examples per class.
    Both choices are predeclared; October-December never selects or fits them.
    All source assumptions remain visible in immutable content-addressed JSON.
    This function does not write artifacts, promote a model, or call an API.
    """
    import importlib.metadata
    import warnings
    from sklearn.exceptions import ConvergenceWarning
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2 ** 32 or not isinstance(calibrate, bool):
        raise ValueError("Wind classifier requires an integer random seed and explicit Boolean calibration policy.")
    targets, label_origin = _wind_classifier_labels(labels)
    features = prepare_wind_classifier_features(hourly, sources=sources, target_timestamps=targets.timestamp_utc)
    joined = features.merge(targets, on="timestamp_utc", how="left", validate="one_to_one")
    input_sources = {**features.attrs["sources"], "labels": label_origin}
    eligible = joined.wind_curtailment_event.notna() & joined[list(WIND_CLASSIFIER_FEATURES)].notna().all(axis=1)
    splits, counts = {}, {}
    count_origin = {"source_type": "assumption" if any(item["source_type"] == "assumption" for item in input_sources.values()) else "data",
                    "ref": "Exact UTC experiment cohorts, observed VER completeness and exact-lag input availability; " + "; ".join(item["ref"] for item in input_sources.values())}
    for name, (start, end) in WIND_CLASSIFIER_SPLITS.items():
        in_window = joined.timestamp_utc.ge(pd.Timestamp(start)) & joined.timestamp_utc.lt(pd.Timestamp(end))
        chosen = joined.loc[in_window & eligible].copy()
        splits[name] = chosen
        known_target = joined.wind_curtailment_event.notna()
        known_features = joined[list(WIND_CLASSIFIER_FEATURES)].notna().all(axis=1)
        values = {"supplied_label_hours": int(in_window.sum()), "eligible_hours": len(chosen),
                  "positive_hours": int(chosen.wind_curtailment_event.sum()),
                  "negative_hours": int((~chosen.wind_curtailment_event).sum()),
                  "unknown_target_hours": int((in_window & ~known_target).sum()),
                  "missing_lag_hours": int((in_window & ~known_features).sum()),
                  "excluded_hours": int((in_window & ~eligible).sum())}
        counts[name] = {"start_utc": start, "end_exclusive_utc": end,
                        **{key: sourced(value, count_origin) for key, value in values.items()}}
    training, calibration, test = (splits[name] for name in ("train", "calibration", "test"))
    if training.empty or training.wind_curtailment_event.nunique() != 2:
        raise ValueError("Wind classifier training requires evaluable January-August examples of both independent event classes.")
    matrix = training[list(WIND_CLASSIFIER_FEATURES)].to_numpy(dtype=float)
    y_train = training.wind_curtailment_event.astype(int).to_numpy()
    parameters = {"C": 1.0, "solver": "lbfgs", "class_weight": None, "max_iter": 2000, "tol": 1e-8, "random_state": seed}
    scaler = StandardScaler()
    base = LogisticRegression(**parameters)
    with warnings.catch_warnings():
        warnings.simplefilter("error", ConvergenceWarning)
        try:
            base.fit(scaler.fit_transform(matrix), y_train)
        except ConvergenceWarning as exc:
            raise ValueError("Wind classifier did not converge; no fitted artifact returned.") from exc
    fitted_calibration = None
    cal_status = "disabled_by_declared_policy" if not calibrate else "insufficient_september_class_support"
    support = calibration.wind_curtailment_event.value_counts()
    if calibrate and support.get(False, 0) >= 20 and support.get(True, 0) >= 20:
        cal_model = LogisticRegression(**parameters)
        cal_scores = base.decision_function(scaler.transform(calibration[list(WIND_CLASSIFIER_FEATURES)].to_numpy(dtype=float)))
        with warnings.catch_warnings():
            warnings.simplefilter("error", ConvergenceWarning)
            try:
                cal_model.fit(cal_scores.reshape(-1, 1), calibration.wind_curtailment_event.astype(int).to_numpy())
            except ConvergenceWarning:
                cal_status = "september_calibration_did_not_converge"
            else:
                fitted_calibration = {"coefficient": float(cal_model.coef_[0, 0]), "intercept": float(cal_model.intercept_[0])}
                cal_status = "regularized_sigmoid_fitted_on_september_only"
    fit_columns = ["timestamp_utc", *WIND_CLASSIFIER_FEATURES, "wind_curtailment_event"]
    policy = {"source_type": "assumption", "ref": "wind-event-logistic-v1: fixed 2024 UTC calendar splits; exact 1h/24h actual lags; C=1 unweighted logistic; September sigmoid enabled only by declared flag with minimum20/class; no test selection"}
    bundle = {"schema_version": WIND_CLASSIFIER_SCHEMA, "features": list(WIND_CLASSIFIER_FEATURES),
              "standardizer": {"mean": scaler.mean_.tolist(), "scale": scaler.scale_.tolist()},
              "base_model": {"coefficients": base.coef_[0].tolist(), "intercept": float(base.intercept_[0])},
              "calibrator": fitted_calibration,
              "manifest": {"system_scope": "SPP_SYSTEM", "status": "research_only_no_production_promotion",
                           "forecast_asof_verified": False, "limitation": WIND_CLASSIFIER_LIMITATION,
                           "target_method": labels.attrs["method"], "input_sources": input_sources,
                           "training_cohort_sha256": _wind_frame_digest(training[fit_columns]),
                           "calibration_cohort_sha256": _wind_frame_digest(calibration[fit_columns]) if calibrate else None,
                           "policy_source": policy, "split_bounds": WIND_CLASSIFIER_SPLITS.copy(),
                           "fit_parameters": parameters, "standardizer_method": "training-only population mean and scale; constant-feature scale=1",
                           "calibration_requested": calibrate, "calibration_status": cal_status,
                           "calibration_minimum_per_class": 20,
                           "versions": {name: importlib.metadata.version(name) for name in ("numpy", "pandas", "scikit-learn")}}}
    # Tuples become JSON lists now, so reloads have exactly the same object shape.
    bundle = json.loads(json.dumps(bundle, sort_keys=True, allow_nan=False))
    bundle["model_id"] = _wind_json_digest(bundle)
    raw, calibrated = _wind_predict_values(bundle, test)
    y_test = test.wind_curtailment_event.astype(int).to_numpy()
    baseline = float(y_train.mean())
    model_origin = _wind_prediction_source(bundle)
    label_digest = _wind_frame_digest(targets)
    test_digest = _wind_frame_digest(test[fit_columns])
    model_origin["ref"] += (f"; evaluation_window=[{WIND_CLASSIFIER_SPLITS['test'][0]}, {WIND_CLASSIFIER_SPLITS['test'][1]}); "
                            f"observations_sha256={features.attrs['feature_input_sha256']}; "
                            f"labels_sha256={label_digest}; evaluation_cohort_sha256={test_digest}")
    report = {"schema_version": WIND_CLASSIFIER_SCHEMA, "model_id": bundle["model_id"],
              "status": "research_only_no_production_promotion", "forecast_asof_verified": False,
              "limitation": WIND_CLASSIFIER_LIMITATION, "calibration_status": cal_status,
              "source_qualification": GENMIX_SOURCE_QUALIFICATION,
              "cohorts": counts, "input_sources": input_sources, "policy_source": policy,
              "data_manifest": {"hourly_observations_sha256": features.attrs["feature_input_sha256"],
                                "labels_sha256": label_digest,
                                "test_cohort_sha256": test_digest,
                                "outside_fixed_windows": sourced(int((joined.timestamp_utc.lt(pd.Timestamp(WIND_CLASSIFIER_SPLITS["train"][0])) | joined.timestamp_utc.ge(pd.Timestamp(WIND_CLASSIFIER_SPLITS["test"][1]))).sum()), count_origin)},
              "training_event_rate": sourced(baseline, count_origin),
              "test_metrics": {"raw": _wind_classifier_metrics(y_test, raw, model_origin),
                               "calibrated": _wind_classifier_metrics(y_test, calibrated, model_origin,
                                               unavailable=cal_status if fitted_calibration is None else None),
                               "training_prevalence_baseline": _wind_classifier_metrics(y_test, np.full(len(test), baseline),
                                   {**model_origin, "ref": "constant training-only event-prevalence baseline; " + model_origin["ref"]})},
              "interpretation": "Brier and log loss assess probability performance, not calibration alone. Reliability bins are held-out empirical summaries without a future confidence guarantee. No automatic selection between raw and calibrated models."}
    prediction_times = joined.loc[joined.timestamp_utc.ge(pd.Timestamp(WIND_CLASSIFIER_SPLITS["test"][0])) & joined.timestamp_utc.lt(pd.Timestamp(WIND_CLASSIFIER_SPLITS["test"][1])), "timestamp_utc"]
    prediction_rows = predict_wind_event_classifier(bundle, hourly, sources=sources, target_timestamps=prediction_times) if len(prediction_times) else []
    label_lookup = targets.set_index("timestamp_utc").wind_curtailment_event
    for row in prediction_rows:
        target = label_lookup.loc[pd.Timestamp(row["timestamp_utc"])]
        row["observed_event"] = {"value": None if pd.isna(target) else bool(target), **label_origin}
        row["evaluated"] = not pd.isna(target) and row["raw_probability"]["value"] is not None
    # Also rejects accidental numpy scalars, timestamps, NaNs or infinity in public results.
    report, prediction_rows = json.loads(json.dumps((report, prediction_rows), sort_keys=True, allow_nan=False))
    return bundle, report, prediction_rows


def evaluate_wind_event_classifier(
    bundle, hourly, labels, *, sources, baseline_probability,
    start_utc, end_exclusive_utc,
):
    """Evaluate frozen JSON parameters on an explicitly declared later window.

    No estimator fits, threshold selection or model promotion occur here. The
    supplied constant baseline is separately sourced; its origin is the caller's
    declaration, not an inferred training-prevalence guarantee. The requested
    UTC calendar grid includes missing label hours, so a partial provider archive
    cannot silently become complete-year coverage. Counts of missing targets
    and missing lags overlap; excluded_hours counts their union.
    """
    _validate_wind_classifier_bundle(bundle)
    bounds = _wind_hour_index([start_utc, end_exclusive_utc])
    start, end = bounds[0], bounds[1]
    if start < pd.Timestamp(WIND_CLASSIFIER_SPLITS["test"][1]) or end <= start:
        raise ValueError("Independent replay must follow all fixed 2024 experiment windows.")
    if end - start > pd.Timedelta(366, unit="D"):
        raise ValueError("Declare at most one calendar year per independent replay.")
    if not isinstance(baseline_probability, Mapping) or set(baseline_probability) != {"value", "source_type", "ref"}:
        raise ValueError("The supplied constant baseline requires value, source_type and ref.")
    baseline = finite_number(baseline_probability["value"], "baseline probability", minimum=0, maximum=1)
    baseline_origin = source({key: baseline_probability[key] for key in ("source_type", "ref")})
    targets, label_origin = _wind_classifier_labels(labels)
    times = pd.date_range(start, end, freq="h", inclusive="left")
    features = prepare_wind_classifier_features(hourly, sources=sources, target_timestamps=times)
    rows = predict_wind_event_classifier(bundle, hourly, sources=sources, target_timestamps=times)
    target_lookup = targets.set_index("timestamp_utc").wind_curtailment_event.reindex(times)
    supplied = times.isin(targets.timestamp_utc)
    known_target = target_lookup.notna().to_numpy()
    raw = np.array([row["raw_probability"]["value"] for row in rows], dtype=float)
    calibrated = np.array([row["calibrated_probability"]["value"] for row in rows], dtype=float)
    known_features = np.isfinite(raw)
    eligible = known_target & known_features
    empirical_sources = {**features.attrs["sources"], "evaluation_labels": label_origin}
    metric_origin = _wind_prediction_source(bundle, input_sources=empirical_sources)
    label_digest = _wind_frame_digest(targets)
    evaluated = features.loc[eligible, ["timestamp_utc", *WIND_CLASSIFIER_FEATURES]].copy()
    evaluated["wind_curtailment_event"] = target_lookup.iloc[np.flatnonzero(eligible)].to_numpy()
    cohort_digest = _wind_frame_digest(evaluated)
    replay_reference = (f"evaluation_window=[{start.isoformat()}, {end.isoformat()}); "
                        f"observations_sha256={features.attrs['feature_input_sha256']}; "
                        f"labels_sha256={label_digest}; evaluation_cohort_sha256={cohort_digest}")
    metric_origin["ref"] += "; " + replay_reference
    empirical_kind = "assumption" if any(origin["source_type"] == "assumption" for origin in empirical_sources.values()) else "data"
    coverage_origin = {"source_type": empirical_kind, "ref": "Exact requested UTC grid, observed VER completeness and exact-lag availability; "
                       + "; ".join(origin["ref"] for origin in empirical_sources.values()) + "; " + replay_reference}
    policy = {"source_type": "assumption", "ref": f"Declared independent historical replay window [{start.isoformat()}, {end.isoformat()}); "
              "same frozen model and all eligible rows, no fitting or selection; missing-target and missing-lag counts may overlap"}
    counts = {
        "requested_hours": sourced(len(times), policy),
        "supplied_label_hours": sourced(int(supplied.sum()), coverage_origin),
        "missing_label_hours": sourced(int((~supplied).sum()), coverage_origin),
        "unknown_supplied_target_hours": sourced(int((supplied & ~known_target).sum()), coverage_origin),
        "missing_lag_hours": sourced(int((~known_features).sum()), coverage_origin),
        "eligible_hours": sourced(int(eligible.sum()), coverage_origin),
        "excluded_hours": sourced(int((~eligible).sum()), coverage_origin),
        "positive_hours": sourced(int(target_lookup.iloc[np.flatnonzero(eligible)].sum()), coverage_origin),
    }
    counts["negative_hours"] = sourced(counts["eligible_hours"]["value"] - counts["positive_hours"]["value"], coverage_origin)
    y = target_lookup.iloc[np.flatnonzero(eligible)].astype(int).to_numpy()
    combined_baseline_origin = {
        "source_type": "assumption" if "assumption" in {metric_origin["source_type"], baseline_origin["source_type"]} else "model",
        "ref": f"Caller-supplied constant probability {baseline!r} ({baseline_origin['ref']}); {metric_origin['ref']}",
    }
    metrics = {
        "raw": _wind_classifier_metrics(y, raw[eligible], metric_origin),
        "calibrated": _wind_classifier_metrics(y, calibrated[eligible], metric_origin,
            unavailable=bundle["manifest"]["calibration_status"] if bundle["calibrator"] is None else None),
        "supplied_constant_baseline": _wind_classifier_metrics(y, np.full(len(y), baseline), combined_baseline_origin),
    }
    report = {
        "schema_version": "spp-wind-event-independent-replay-v1", "model_id": bundle["model_id"],
        "status": "research_only_no_production_promotion", "forecast_asof_verified": False,
        "start_utc": start.isoformat(), "end_exclusive_utc": end.isoformat(),
        "limitation": WIND_CLASSIFIER_LIMITATION, "policy_source": policy,
        "source_qualification": GENMIX_SOURCE_QUALIFICATION,
        "coverage": counts, "input_sources": empirical_sources,
        "baseline_probability": sourced(baseline, baseline_origin), "metrics": metrics,
        "data_manifest": {"hourly_observations_sha256": features.attrs["feature_input_sha256"],
                          "labels_sha256": label_digest, "evaluation_cohort_sha256": cohort_digest},
        "interpretation": "Frozen historical replay, no automatic selection between raw and calibrated results. "
                          "Metrics use the identical eligible rows. Missing calendar hours remain explicit; "
                          "the supplied baseline's claimed origin must be verified separately.",
    }
    for index, row in enumerate(rows):
        target = target_lookup.iloc[index]
        row["observed_event"] = {"value": bool(target), **label_origin} if known_target[index] else {
            "value": None, "source_type": "assumption", "ref": "unavailable: "
            + ("incomplete supplied VER hour" if supplied[index] else "no supplied VER hour") + "; " + label_origin["ref"],
        }
        row["evaluated"] = bool(eligible[index])
    return json.loads(json.dumps((report, rows), sort_keys=True, allow_nan=False))


def _strict_wind_json(content):
    """Parse artifact/metadata JSON without overwritten keys or nonfinite numbers."""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate wind artifact JSON key: {key}")
            result[key] = value
        return result

    def nonfinite(value):
        raise ValueError(f"Nonfinite wind artifact JSON constant: {value}")

    def finite_literal(value):
        number = float(value)
        if not np.isfinite(number):
            raise ValueError(f"Nonfinite wind artifact JSON number: {value}")
        return number

    return json.loads(content, object_pairs_hook=unique, parse_constant=nonfinite,
                      parse_float=finite_literal)


def publish_wind_replay(*, bundle_path, hourly_path, labels_path, baseline_path,
                        start_utc, end_exclusive_utc, output_dir):
    """Publish a self-contained frozen replay from four explicit local artifacts.

    Each source file is read once. Those same byte buffers are parsed, hashed and
    copied into inputs/; a later change to an original path cannot misidentify the
    consumed data. File checksums and semantic-frame hashes have distinct roles.
    Baseline provenance is the caller's declaration, not authenticated here.

    Evaluation and lossless parquet round-trip checks complete before creating
    output_dir. Its exclusive creation protects existing/racing destinations.
    Files are flushed before the final manifest is atomically hard-linked into
    place. A disk failure may leave an incomplete new folder without a manifest;
    it must not be treated as published or reused. No existing files are deleted.
    Reproducibility of serialized bytes assumes the recorded runtime/settings;
    the manifest does not certify observation authenticity or forecast timing.
    """
    from importlib.metadata import version
    import sys

    output_dir = Path(output_dir)
    if output_dir.exists() or output_dir.is_symlink():
        raise FileExistsError(f"Replay output already exists: {output_dir}")
    paths = {"bundle": Path(bundle_path), "hourly": Path(hourly_path),
             "labels": Path(labels_path), "baseline": Path(baseline_path)}
    content = {name: path.read_bytes() for name, path in paths.items()}
    bundle = _strict_wind_json(content["bundle"])
    baseline = _strict_wind_json(content["baseline"])
    hourly = pd.read_parquet(io.BytesIO(content["hourly"]), engine="pyarrow")
    labels = pd.read_parquet(io.BytesIO(content["labels"]), engine="pyarrow")
    if "sources" not in hourly.attrs:
        raise ValueError("Hourly replay parquet must preserve attrs.sources; no source defaults are inferred.")
    source_file = Path(__file__).read_bytes()
    report, rows = evaluate_wind_event_classifier(
        bundle, hourly, labels, sources=hourly.attrs["sources"], baseline_probability=baseline,
        start_utc=start_utc, end_exclusive_utc=end_exclusive_utc,
    )
    predictions = pd.DataFrame(rows)
    parquet = predictions.to_parquet(index=False, engine="pyarrow", compression="zstd")
    restored = pd.read_parquet(io.BytesIO(parquet), engine="pyarrow")

    def numpy_json(value):
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, np.generic):
            return value.item()
        raise TypeError(f"Unsupported replay parquet value: {type(value).__name__}")

    restored_rows = json.loads(json.dumps(restored.to_dict("records"), default=numpy_json, allow_nan=False))
    if restored_rows != rows:
        raise ValueError("Replay prediction parquet changed a value or its provenance during serialization.")
    if Path(__file__).read_bytes() != source_file:
        raise ValueError("Replay source file changed during evaluation; publish from a stable code version.")
    inputs = {"bundle": "inputs/bundle.json", "hourly": "inputs/hourly.parquet",
              "labels": "inputs/labels.parquet", "baseline": "inputs/baseline.json"}
    payloads = {inputs[name]: value for name, value in content.items()}
    payloads["report.json"] = json.dumps(report, sort_keys=True, indent=2, allow_nan=False).encode("utf-8")
    payloads["predictions.parquet"] = parquet
    manifest = {
        "schema_version": "spp-wind-replay-publication-v1", "status": report["status"],
        "model_id": report["model_id"], "start_utc": report["start_utc"],
        "end_exclusive_utc": report["end_exclusive_utc"],
        "source_file_sha256": hashlib.sha256(source_file).hexdigest(),
        "runtime": {"python": sys.version.split()[0], "numpy": np.__version__, "pandas": pd.__version__,
                    "scikit_learn": version("scikit-learn"), "pyarrow": version("pyarrow")},
        "parquet": {"engine": "pyarrow", "compression": "zstd", "columns": list(predictions.columns)},
        "inputs": inputs, "outputs": {"report": "report.json", "predictions": "predictions.parquet"},
        "files": {name: {"sha256": hashlib.sha256(value).hexdigest(), "bytes": len(value)}
                  for name, value in sorted(payloads.items())},
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True, indent=2, allow_nan=False).encode("utf-8")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir()
    (output_dir / "inputs").mkdir()
    for name, value in payloads.items():
        with (output_dir / name).open("xb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
    fd, temporary = tempfile.mkstemp(prefix=".manifest-", suffix=".tmp", dir=output_dir)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(manifest_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, output_dir / "manifest.json")
    finally:
        Path(temporary).unlink(missing_ok=True)
    return manifest


def main(argv=None):
    """Run an explicitly requested offline frozen-model replay."""
    import argparse

    parser = argparse.ArgumentParser(description="Offline frozen wind-event replay; no fitting, fetching or production promotion.")
    actions = parser.add_subparsers(dest="action", required=True)
    replay = actions.add_parser("replay", help="Publish a self-contained research replay from explicit local inputs")
    for name in ("bundle", "hourly", "labels", "baseline"):
        replay.add_argument(f"--{name}", type=Path, required=True)
    replay.add_argument("--start-utc", required=True)
    replay.add_argument("--end-exclusive-utc", required=True)
    replay.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest = publish_wind_replay(
            bundle_path=args.bundle, hourly_path=args.hourly, labels_path=args.labels, baseline_path=args.baseline,
            start_utc=args.start_utc, end_exclusive_utc=args.end_exclusive_utc, output_dir=args.output_dir,
        )
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
        parser.error(str(error))
    print(json.dumps({"status": manifest["status"], "model_id": manifest["model_id"],
                      "schema_version": manifest["schema_version"], "manifest": str(args.output_dir / "manifest.json")},
                     sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
