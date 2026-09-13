"""Run from the repo root: python -m uvicorn api.main:app --host 127.0.0.1 --port 8000."""

from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Path, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from .economics import ArithmeticRangeError, AssumptionsError, EconomicsAssumptions, load_assumptions
from .estimate import build_estimate
from .grid_impact import GridImpactSnapshotCache, LIVE_LOCATION_PATTERN, read_live_grid_impact
from .locations import LocationsResponse, get_locations
from .mock_provider import LocationNotFoundError, LocationProvider
from .pipeline_provider import PipelineDataError, get_pipeline_location
from .schemas import EstimateRequest, EstimateResponse


app = FastAPI(
    title="Fluxline estimate API",
    description="Precomputed pipeline estimates with explicit placeholder fallback. Site exposure is a user assumption.",
    version="0.2.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5174"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
    expose_headers=["X-Headroom-Exposure-Source"],
)
grid_impact_cache = GridImpactSnapshotCache(max_entries=32)


def get_location_provider() -> LocationProvider:
    """Try the precomputed reader, falling back only for missing pipeline pieces."""
    return get_pipeline_location


@app.get("/api/locations", response_model=LocationsResponse)
def locations(response: Response) -> LocationsResponse:
    """List available IDs without claiming validated annual or site coverage."""
    response.headers["Cache-Control"] = "no-store"
    try:
        return get_locations()
    except PipelineDataError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.get("/api/grid-impact/{location_id}")
def grid_impact(location_id: Annotated[str, Path(pattern=LIVE_LOCATION_PATTERN)], response: Response) -> dict:
    """Read a fixed precompiled grid-impact-v1 scenario with its complete evidence.

    Each of the six wind/carbon quantities is {value, source_type, ref}; units,
    basis, coverage and the local evidence graph accompany them. Null means
    unavailable, never zero. A 200 may contain partial or unavailable coverage.
    This scenario has its own declared capacity; /api/estimate inputs do not
    change it. Reviewed zone references add location_mapping identifying the
    source point and scope; unavailable mapped evidence stays null. Unknown IDs
    without snapshots are 404; invalid/unreadable snapshots are 503.
    """
    response.headers["Cache-Control"] = "no-store"
    try:
        return read_live_grid_impact(location_id, cache=grid_impact_cache)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail="No precompiled grid-impact snapshot for this exact location",
                            headers={"Cache-Control": "no-store"}) from error
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=503, detail="Precompiled grid-impact snapshot is invalid or unreadable",
                            headers={"Cache-Control": "no-store"}) from error


@app.get("/api/economics-assumptions", response_model=EconomicsAssumptions)
def economics_assumptions(response: Response) -> EconomicsAssumptions:
    """Expose the exact file-backed defaults/provenance used by estimate arithmetic."""
    response.headers["Cache-Control"] = "no-store"
    try:
        return load_assumptions()
    except AssumptionsError as error:
        raise HTTPException(status_code=503, detail="Economic assumptions are missing or invalid; check docs/ASSUMPTIONS.md") from error


@app.exception_handler(RequestValidationError)
async def invalid_request(_request: Request, error: RequestValidationError) -> JSONResponse:
    # Omit raw inputs/context: NaN/Infinity are rejected, but copying them into the
    # default error response would itself fail JSON serialization and return 500.
    return JSONResponse(status_code=422, content={
        "detail": [
            {"loc": item["loc"], "msg": item["msg"], "type": item["type"]}
            for item in error.errors()
        ],
    })


@app.post("/api/estimate", response_model=EstimateResponse)
def estimate(
    request: EstimateRequest,
    response: Response,
    provider: Annotated[LocationProvider, Depends(get_location_provider)],
) -> EstimateResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        result = build_estimate(request, provider)
        response.headers["X-Headroom-Exposure-Source"] = (
            "placeholder" if result.modeled_exposure.source.ref.startswith("mock://") else "pipeline"
        )
        return result
    except LocationNotFoundError as error:
        raise HTTPException(status_code=404, detail="Location is not in the supplied SPP estimate set") from error
    except ArithmeticRangeError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except PipelineDataError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except AssumptionsError as error:
        raise HTTPException(status_code=503, detail="Economic assumptions are missing or invalid; check docs/ASSUMPTIONS.md") from error
