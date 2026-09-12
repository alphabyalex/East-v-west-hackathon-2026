"""Run from the repo root: python -m uvicorn api.main:app --host 127.0.0.1 --port 8000."""

from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .estimate import ArithmeticRangeError, build_estimate
from .mock_provider import LocationNotFoundError, LocationProvider, get_mock_location
from .schemas import EstimateRequest, EstimateResponse


app = FastAPI(
    title="Headroom estimate API",
    description="Deterministic mock data only. Site exposure is a user assumption.",
    version="0.1.0",
)


def get_location_provider() -> LocationProvider:
    """Replace this dependency with a cached pipeline/parquet adapter when ready."""
    return get_mock_location


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
        return build_estimate(request, provider)
    except LocationNotFoundError as error:
        raise HTTPException(status_code=404, detail="Location is not in the supplied SPP estimate set") from error
    except ArithmeticRangeError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
