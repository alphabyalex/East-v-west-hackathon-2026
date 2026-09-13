# Local estimate API

FastAPI serves the canonical `POST /api/estimate` contract using Kristian's
precomputed reader when available, with an explicit placeholder fallback when the
reader, parquet, or provenance companions are absent, or annual reference evidence
is missing/insufficient. It never trains, simulates, or fetches grid data.
Economics comes from [docs/ASSUMPTIONS.md](../docs/ASSUMPTIONS.md), whose current
status is **mixed**: Tharun's cited scenario defaults plus explicitly unverified
margin and decision-tolerance assumptions. File presence does not establish real data.

From the repository root, using Python 3.11 or later:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r api/requirements-dev.txt
.venv/Scripts/python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

For macOS/Linux, replace `.venv/Scripts/python.exe` with `.venv/bin/python`.
For runtime dependencies only, use `api/requirements.txt`. Keep the API terminal
running alongside Vite. This command does not enable automatic code reload:
restart Uvicorn after changing API or already-imported pipeline code. Assumptions
file edits are read on the next request. OpenAPI is available at
`http://127.0.0.1:8000/openapi.json`.

```powershell
.venv/Scripts/python.exe -m pytest -q
```

For the complete offline ML suite, first install `requirements-ml.txt` into the same
environment. The API requirements include only the parquet reader's dependencies;
they do not install the training stack.

Tests cover the HTTP contract, explicit placeholder provenance, imported-reader
mapping, single site-factor application, missing versus broken pipeline behavior,
assumptions validation/reloading, arithmetic boundaries, and CORS.

## Request, response, and direct frontend access

The request contains exactly these five fields:

```json
{
  "location_id": "SPP_SPS_HUB",
  "load_mw": 250,
  "term_years": 7,
  "flexibility_split": 0.6,
  "site_exposure": 0.3
}
```

`load_mw` must be positive and finite; `term_years` is an integer from 1 through 7;
both fractions are finite values from 0 through 1. The response body retains the
exact [BUILD_PLAN.md section 2](../docs/BUILD_PLAN.md#2-the-api-contract-alex-builds-this-tharun-builds-against-it)
shape. Numeric descendants inherit their block's source; echoed inputs are user
assumptions. `by_year` covers every requested year in ascending order. The agreed
zero-cost boundary remains `breakeven_exposure_hours_per_year: null`.

CORS permits browser calls from exactly `http://127.0.0.1:5174`, using `GET`, `POST`, and
the `Content-Type` request header, without credentials. Other hostnames/ports are
different origins and are not allowed. For example, run from that frontend:

```js
const response = await fetch('http://127.0.0.1:8000/api/estimate', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  credentials: 'omit',
  body: JSON.stringify({
    location_id: 'SPP_SPS_HUB', load_mw: 250, term_years: 7,
    flexibility_split: 0.6, site_exposure: 0.3,
  }),
});
if (!response.ok) throw new Error(`Estimate request failed: ${response.status}`);
const exposureOrigin = response.headers.get('X-Headroom-Exposure-Source');
const estimate = await response.json();
```

Successful responses expose `X-Headroom-Exposure-Source: pipeline|placeholder` and
send `Cache-Control: no-store`. This header describes exposure only; inspect each
block's provenance for economics and tariffs. The web client uses direct access
by default; `VITE_API_BASE_URL=` selects the same-origin Vite proxy instead.
No API body fields were added for transport status.

`GET /api/economics-assumptions` returns the exact validated machine-readable block
from `docs/ASSUMPTIONS.md`, with `Cache-Control: no-store`. Its keys and per-value
shape are listed below. The frontend validates this response alongside the estimate
before showing API results, so controls and provenance match the server's actual
defaults. A missing/invalid assumptions file returns 503 on either endpoint.

## Precomputed reader and explicit fallback

`api.main.get_location_provider()` selects
`api.pipeline_provider.get_pipeline_location(location_id)`. It imports
`pipeline.simulate`, checks that `get_location_estimate` is callable and
`data/processed/exposure_by_location.parquet` exists, validates its sibling
`model_card.json` and `simulation_metadata.json`, then calls:

```text
get_location_estimate(location_id: str) -> dict
# Exact result from BUILD_PLAN.md section 1:
{
    "location_id": str,
    "by_year": [
        {"year_offset": int, "p50_hours": float, "p90_hours": float,
         "p99_hours": float, "worst_contiguous_hours": float},
    ],
    "confidence": {
        "level": "High" | "Medium" | "Low",
        "score": float,
        "n_similar_historical_hours": int,
    },
    "model_version": str,
}
```

The reader must only read precomputed output. The adapter validates finite ordered
quantiles, complete ordered years starting at one, confidence bounds/counts, model
version, and the requested location. Companion metadata must describe the same
model, explicit data/label sources, input hashes, and unscaled simulation output.
Keep these files together when publishing an offline run to `data/processed`;
the workflow's run directory is not automatically promoted into the API.
It retains the model version, precedent count, and experimental annual-tail
limitations in `source_type: "model"` references. Confidence uses the contracted
`ensemble_disagreement` basis and is not changed by the site assumption.
The current simulator caps annual confidence at Low; its score measures classifier
agreement, not annual-tail calibration. `worst_contiguous_hours` is the p99 of
annual longest modeled episodes, not a guaranteed upper bound. The API reports
the maximum of those annual statistics over the requested term, scaled by the
user's site factor; it is not an observed outage length.
`api.estimate.build_estimate` applies `site_exposure` exactly once, downstream of
the reader; the frontend must not apply it again.

Annual eligibility additionally checks the model card's `splits.test.start` and
`splits.test.end` (inclusive hourly timestamps) and
`test_by_location[location_id].n_hours`. At least 365 days of held-out span and
8,760 scored hours for that particular location are required. Combining many
locations does not create a year of reference history. This is a minimum readiness
check, not validation of annual tails or site applicability; confidence remains Low.
The currently tracked 2024 bundle has only about 72 held-out days and is not eligible.

If the module, callable, parquet, or either companion is missing, annual reference
evidence is missing/insufficient, or a file becomes unavailable
during import/read, the API returns the identical body shape with explicit
assumption sources. Exposure/confidence refs include
`mock://placeholder/...; placeholder, pipeline not wired yet; <reason>`.
The fallback reuses the authored frontend fixture's numbers; these are not new
measurements. It supports only `spp-wichita-demo`, `spp-oklahoma-city-demo`,
`spp-lincoln-demo`, the canonical example `SPP_SPS_HUB`, and `SPP_SYSTEM`. The latter
is the real load input's system-aggregate ID, but its fallback exposure remains
authored. These placeholders do not establish real node coverage. Once the reader is available, its returned
location set governs coverage. Tariffs remain unextracted placeholders, including
when exposure comes from the pipeline.

| Condition | HTTP result |
|---|---|
| Missing pipeline pieces, supported placeholder ID | 200, clearly sourced placeholder |
| Missing/insufficient annual reference evidence, supported placeholder ID | 200, placeholder with model version and readiness reason |
| Unknown placeholder ID or reader's exported `LocationNotFoundError` | 404 |
| Invalid request types/ranges/fields or arithmetic overflow | 422 |
| Present reader has a broken dependency/import, runtime failure, or malformed output | 503 |
| Precomputed output does not cover the requested term | 503 |
| Missing or invalid economics assumptions file | 503 |

Broken data is never silently replaced with a successful placeholder response.
A newly appearing module/file is checked on subsequent requests; restart the API
when changing a module already imported into the process.

## File-backed economics

`api/economics.py` reads exactly one JSON block immediately after
`<!-- headroom:economics-assumptions:v1 -->` in `docs/ASSUMPTIONS.md` on each request.
There are no hardcoded numeric defaults if that file is missing or invalid.
The block requires `schema_version: 1`, `status: "placeholder"|"mixed"|"sourced"`,
and these six entries:

- `gpu_rental_price_usd_per_hour` (`USD/GPU-hour`)
- `industrial_electricity_price_usd_per_mwh` (`USD/MWh`)
- `gpus_per_mw` (`GPU/MW`)
- `early_connection_years` (`year`)
- `early_margin_usd_per_mw_year` (`USD/MW-year`)
- `close_call_fraction` (`fraction`)

Every entry has `{value, source_type, ref, unit, source_url, retrieved_on, low, high}`.
Values/ranges must be finite and nonnegative, density positive, tolerance less than
one, and `low <= value <= high`. Placeholder entries require assumption provenance,
an explicit `mock://` placeholder ref, and null URL/date. Reviewed entries require
an HTTPS source URL and retrieval date. Status must match per-entry provenance.
Duplicate JSON keys, unknown fields, or multiple marked blocks are rejected.

Current defaults preserve Tharun's selected grid basis: 575 GPUs/MW, $3/GPU-hour,
four years of earlier access capped at the contract term, and $317,000/MW-year
of **assumed** net operating margin. That last input is his unverified 3% margin
assumption applied to $10,577,700/MW-year of derived gross revenue, rounded;
neither a citation nor a decision flip validates the margin. It keeps placeholder
provenance, as does the unreviewed 5% decision tolerance. Kansas electricity at
$82.1/MWh is **informational only**, not a regional/site tariff; it is not deducted from gross
lost rental value or deducted again from the independent net-margin input.

Annual summaries are means of annual marginal quantiles. Costs compare their
full-term paths against early-access value; they are not quantiles of total loss.
See the assumptions file for formulas and sourcing requirements. Derived economics
retains `source_type: "assumption"` and references every economic input plus the
exposure source. Any remaining placeholder dependency retains a `mock://` ref.
No economic overrides are accepted by the five-field request; local frontend
economic edits remain local-only.

Implementation references: [FastAPI dependency injection](https://fastapi.tiangolo.com/tutorial/dependencies/),
[FastAPI CORS](https://fastapi.tiangolo.com/tutorial/cors/), and
[Pydantic strict validation](https://docs.pydantic.dev/latest/concepts/strict_mode/).
