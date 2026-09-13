"""Location choices from the configured exposure table, not a readiness claim."""

from typing import Literal

import pandas as pd

from . import pipeline_provider
from .schemas import ContractModel, NonEmpty


class LocationOption(ContractModel):
    id: NonEmpty
    label: NonEmpty
    kind: Literal["system", "zone", "scenario"]


class LocationsResponse(ContractModel):
    locations: list[LocationOption]


DEMO_LABELS = {
    "spp-wichita-demo": "Wichita, KS · scenario",
    "spp-oklahoma-city-demo": "Oklahoma City, OK · scenario",
    "spp-lincoln-demo": "Lincoln, NE · scenario",
}


def get_locations() -> LocationsResponse:
    """Read only IDs; estimate provenance and annual-readiness checks stay separate.

    The system choice and authored scenarios remain available without an artifact.
    A present unreadable or malformed catalog is an error, never a silent fallback.
    """
    try:
        frame = pd.read_parquet(pipeline_provider.PARQUET_PATH, columns=["location_id"])
    except FileNotFoundError:
        ids = set()
    except Exception as error:
        raise pipeline_provider.PipelineDataError("Precomputed location catalog is unreadable") from error
    else:
        values = frame["location_id"].tolist()
        if not values or any(not isinstance(value, str) or not value.strip() or value != value.strip() for value in values):
            raise pipeline_provider.PipelineDataError("Precomputed location catalog requires nonempty string IDs")
        ids = set(values)

    choices = [LocationOption(id="SPP_SYSTEM", label="SPP system aggregate", kind="system")]
    choices.extend(
        LocationOption(id=location_id, label=f"{location_id} · SPP load zone", kind="zone")
        for location_id in sorted(ids - {"SPP_SYSTEM"} - DEMO_LABELS.keys())
    )
    choices.extend(
        LocationOption(id=location_id, label=label, kind="scenario")
        for location_id, label in DEMO_LABELS.items()
    )
    return LocationsResponse(locations=choices)
