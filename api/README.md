# Local estimate API

FastAPI serves the canonical `POST /api/estimate` response from deterministic,
clearly marked mock data. There is no model run, external fetch, database, or auth.
The backend reads `web/src/model/mock-response.json` once and applies the same cheap
scenario arithmetic as the frontend's local mock provider.

From the repository root, using Python 3.11 or later:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r api/requirements-dev.txt
.venv/Scripts/python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

For macOS/Linux, replace `.venv/Scripts/python.exe` with `.venv/bin/python`.
For runtime dependencies only, use `api/requirements.txt`. Keep the API terminal
running alongside Vite. The frontend's Vite proxy forwards `/api` to this local
server; browser requests do not need cross-origin access or an external network.
OpenAPI is available at `http://127.0.0.1:8000/openapi.json`.

```powershell
.venv/Scripts/python.exe -m pytest api -q
```

The tests cover exact fixture shape/provenance, floating-point numeric parity,
all request dimensions, three decision states, complete contract horizons, invalid
input, zero exposure, zero flexibility, determinism, and replacing the data provider.

## Contract and mock values

The request contains exactly these five fields:

```json
{
  "location_id": "spp-wichita-demo",
  "load_mw": 100,
  "term_years": 7,
  "flexibility_split": 0.6,
  "site_exposure": 0.4
}
```

`load_mw` must be positive and finite; `term_years` is an integer from 1 through 7;
both fractions are finite values from 0 through 1. Unknown locations return 404.
Missing/extra fields, invalid types/ranges, and arithmetic overflow return 422.
The supported illustrative location IDs are `spp-wichita-demo`,
`spp-oklahoma-city-demo`, and `spp-lincoln-demo`; none is a validated pricing node.

The response is the exact shape in [BUILD_PLAN.md](../docs/BUILD_PLAN.md#2-the-api-contract-alex-builds-this-tharun-builds-against-it).
The complete default response is [mock-response.json](../web/src/model/mock-response.json).
Each numeric descendant inherits its block's source; input echoes inherit the
submitted user assumptions. The existing frontend convention allows
`economics.breakeven_exposure_hours_per_year: null` when interruptible load is zero.
`modeled_exposure.by_year` always contains every requested year in ascending order.

Economics uses the existing round placeholders: 1,000 GPUs/MW, $2/GPU-hour,
$500,000/MW-year early-access margin, and 3 years of early access, capped at the
contract term. These remain explicit assumptions with `mock://` references pending
the team's `docs/ASSUMPTIONS.md` on main. No economics overrides are accepted by the
five-field endpoint. Local-only frontend economics controls must stay in local mode.

Annual summary quantiles are means of supplied marginal annual quantiles. The
decision compares full-term costs: `not_worth_it` when median annual cost multiplied
by the term exceeds 105% of early-access value; `worth_it` when the p90 cost path
over the term is below 95% of that value; otherwise `close_call`. Those full-term
paths are comparison proxies, not quantiles of total contract loss. Confidence is
copied from the mock fixture and does not change with the site assumption.

## Replacing the precomputed source

`api.main.get_location_provider` is the dependency seam. It currently returns
`get_mock_location`, which provides unscaled `BaselineYear` records plus confidence,
exposure provenance, and tariff metadata. `api.estimate.build_estimate` alone applies
the user-set site factor and economic arithmetic. This prevents accidental double
scaling when real parquet arrives.

When `pipeline.simulate.get_location_estimate(location_id)` and
`exposure_by_location.parquet` exist, add a provider adapter that maps its documented
`year_offset`, `p50_hours`, `p90_hours`, `p99_hours`, and
`worst_contiguous_hours` fields to `BaselineYear`. Pass the real model version and
confidence basis/source through; do not manufacture them in the HTTP layer. Map
pipeline `LocationNotFoundError` to the API exception, then replace the dependency's
default callable. Read only cached/precomputed results. The route and frontend JSON
contract remain unchanged. Load extracted tariff metadata in this provider when
Claude publishes its schema; mock clauses currently carry no fabricated citations.

Implementation references: [FastAPI dependency injection](https://fastapi.tiangolo.com/tutorial/dependencies/)
and [Pydantic strict validation](https://docs.pydantic.dev/latest/concepts/strict_mode/).
