"""Run from the repo root: python -m uvicorn api.main:app --host 127.0.0.1 --port 8000."""

from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from .economics import ArithmeticRangeError, AssumptionsError, EconomicsAssumptions, load_assumptions
from .estimate import build_estimate
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


def get_location_provider() -> LocationProvider:
    """Try the precomputed reader, falling back only for missing pipeline pieces."""
    return get_pipeline_location


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


from .zone_ranking import ZoneRankingsResponse, ZoneRankingsError, load_zone_rankings

@app.get("/api/zone-rankings", response_model=ZoneRankingsResponse)
def get_zone_rankings(response: Response) -> ZoneRankingsResponse:
    """Expose the precomputed SPP composite sustainability/fit rankings."""
    response.headers["Cache-Control"] = "no-store"
    try:
        return load_zone_rankings()
    except ZoneRankingsError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
