# Frontend consumption of the shared estimate contract

The canonical team contract is [BUILD_PLAN.md](BUILD_PLAN.md), section 2:
**`POST /api/estimate`**. The frontend consumes this response through the local FastAPI endpoint by default.
Both the endpoint and the optional local fallback use deterministic illustrative
fixtures. Neither runs training, inference, Monte Carlo sampling, or external data pulls.

The complete current mock response is
[`web/src/model/mock-response.json`](../web/src/model/mock-response.json).
[`contract.ts`](../web/src/model/contract.ts) defines the request/response types;
[`estimate.ts`](../web/src/model/estimate.ts) contains the mock provider and view
adapter. A parity test verifies that the JSON example matches the provider.

## Request

```json
{
  "location_id": "spp-wichita-demo",
  "load_mw": 100,
  "term_years": 7,
  "flexibility_split": 0.6,
  "site_exposure": 0.4
}
```

The example location is an illustrative SPP-region ID, not a validated pricing node.
Replace dropdown IDs with Kristian's supplied precomputed identifiers during wiring.
The supported term is one through seven years, matching the current pipeline contract.

`toEstimateRequest(inputs)` maps the internal UI names `contract_years` to
`term_years`, and `flexibility_percent / 100` to `flexibility_split`. Only the five
canonical fields enter the request. Internal UI names are not API field aliases.

## Response and provenance

The response has exactly these top-level blocks:

```ts
type EstimateResponse = {
  inputs_echo: EstimateRequest;
  modeled_exposure: {
    unit: 'hours/year';
    p50: number; p90: number; p99: number;
    worst_contiguous_outage_hours: number;
    by_year: Array<{ year: number; p50: number; p90: number; p99: number }>;
    source: Source;
  };
  confidence: {
    level: 'High' | 'Medium' | 'Low'; score: number; basis: string; source: Source;
  };
  economics: {
    gpus_per_mw: number;
    lost_gpu_hours_per_year: { p50: number; p90: number; p99: number };
    annual_cost_usd: { p50: number; p90: number; p99: number };
    value_of_early_connection_usd: number;
    breakeven_exposure_hours_per_year: number | null;
    decision: 'worth_it' | 'not_worth_it' | 'close_call';
    source: Source;
  };
  tariff: {
    operator: 'SPP'; service: string;
    curtailment_triggers: Array<{ text: string; observable: boolean; source: Source }>;
  };
};
type Source = { source_type: 'data' | 'clause' | 'assumption' | 'model'; ref: string };
```

Each block's source applies to its numeric descendants. `adaptEstimateResponse`
wraps each displayed value as `{value, source_type, ref}`, appending its field path
(e.g. `#modeled_exposure/by_year/0/p99`) to the supplied reference. Echoed request
values are user assumptions. Derived ledger values carry their arithmetic in the
reference. `Sourced`, `SourceInfo`, and `SourcedTick` support all four source types.

**The response's exposure values are already scaled by `site_exposure`.** The adapter
preserves them and never multiplies them again. Confidence levels/scores and tariff
sources are also passed through, without deriving a new confidence category.

Two boundary conventions refine the abbreviated example in BUILD_PLAN.md:

- `breakeven_exposure_hours_per_year` is `null` when cost per exposure hour is zero;
  JSON must not contain Infinity or NaN.
- `by_year` must contain the complete selected horizon, ordered from year one through
  `inputs_echo.term_years`. Do not silently show an incomplete contract term.

BUILD_PLAN.md is unchanged; these conventions are logged in FROM_CODEX.md for review.

## Canonical data in the UI

The Three.js surface and annual table contain only the supplied p50/p90/p99 values.
The default Recharts fan uses a p50–p90 band, median line, and separate p99 line. There is
no p10 in the canonical response and none is invented for display.

Surface axes are relative contract year, percentile, and modeled exposure hours/year.
Percentile spacing is proportional (the intervals are 40 and 9 percentile points).
Connected faces are piecewise-linear display interpolation, not extra samples or a
probability-density estimate. A one-year term is a cross-section with no invented
width. The hours axis stays fixed as site exposure changes.

Every exposure readout, including chart inspection and annual cells, includes a
`ConfidenceBadge`. It preserves the supplied level, exposes the exact score and
basis on hover/click, and marks mock signals explicitly. Confidence describes support
for an estimate, never the probability of a future outcome. Current mock confidence
is fixed at Medium / 0.5, with assumption provenance and a non-model fixture basis;
it does not change when the site-exposure assumption moves.

The economics panel shows annual p50/p90/p99 cost scenarios plus a median-path term
ledger. Tariff records are visible with source controls, but the current record is
explicitly a placeholder with no extracted clause or fabricated FERC citation.

## Mock arithmetic and decision rule

All input values remain explicitly illustrative. Round economics defaults are:

| Internal input | Dummy value | Unit |
|---|---:|---|
| `firm_wait_years` | 3 | years of earlier access |
| `gpu_per_mw` | 1000 | GPUs per MW |
| `gpu_hour_value_usd` | 2 | USD per GPU-hour |
| `early_margin_usd_per_mw_year` | 500000 | USD per MW-year |

Economic default refs start with `mock://illustrative/economics-placeholder/` and
identify `docs/ASSUMPTIONS.md` as pending sourcing, not an existing citation.
Kristian/Tharun's sourcing arrives on main. Check at each integration checkpoint;
replace applicable values, units, sources, dates, and ranges together when it lands.
Retain visible mock labels for every remaining placeholder dependency.

For each annual quantile, the mock provider multiplies baseline exposure by the
site assumption once. Summary p50/p90/p99 are arithmetic averages of their selected
annual marginal paths. These averages are **not** quantiles of a multiyear sum or
average. The same caveat applies to the corresponding term-cost comparison paths.

```text
interruptible_MW = load_mw * flexibility_split
lost_GPU_hours[q] = exposure[q] * interruptible_MW * GPUs_per_MW
annual_cost[q] = lost_GPU_hours[q] * USD_per_GPU_hour
term_cost[q] = annual_cost[q] * term_years
benefit = load_mw * early_margin * min(earlier_access_years, term_years)
```

Compare amounts over the same term; do not compare one year's cost with a total
multi-year benefit. The chosen mock decision margin is 5% of benefit:

- `not_worth_it` when `term_cost.p50 > benefit * 1.05`;
- `worth_it` when `term_cost.p90 < benefit * 0.95`;
- `close_call` otherwise, including equality and zero benefit with zero cost.

At current defaults (seven years), site exposure 0.4 / 0.55 / 0.9 demonstrates all
three states. Earlier benefit is USD150 million. Median-hours break-even is about
178.57 h/year. This economic equality is labeled **median cost crossover** in the UI;
it is not either boundary of the p50/p90 decision rule. Its site factor is null at
zero exposure because a zero-scaled response cannot recover the baseline.

The mock's `worst_contiguous_outage_hours` is a round authored 40-hour placeholder
multiplied by site exposure. This field is retained for contract completeness, not
shown as evidence of a site's actual outage duration. Its semantics require pipeline
review before real-data use. Restart overhead, discounting, and SLA penalties remain
outside the current economics.

## HTTP provider and explicit local fallback

The workspace calls `postEstimate(request, {signal})` through the same-origin
`/api/estimate`; Vite dev and preview proxy that path to `http://127.0.0.1:8000`.
`VITE_ESTIMATE_MODE=api` is the default. Set it to `local` (see
`web/.env.example`) and restart Vite to make no HTTP requests. Visible controls can
also select Local mock or return to API defaults without restarting.

While a request is pending, the outputs show a visibly labeled local mock preview
for the current inputs. Failed, timed-out, or invalid responses leave the local
fallback usable and visibly identified, with a Retry control. Superseded requests
are canceled and stale responses cannot overwrite current-input results. The lower
level client still validates provenance, quantiles, complete horizons, finite values,
and the exact inputs echo; it never repairs a response or silently falls back.

The four editable economic controls remain local-only: editing one switches the
workspace to local mock mode with an explanation. Returning to API mode restores
the fixed mock economic defaults. No economic override fields are sent to the server.
This preserves the five-field shared request until the team agrees an additive
interface. The API's returned economics are displayed directly, never overwritten
with unsent local assumptions.

`api/main.py` serves POST /api/estimate using a replaceable precomputed-location
provider and cheap scenario arithmetic. Its mock provider reads the canonical
`web/src/model/mock-response.json` fixture. Invalid requests return 422; unknown
fixture locations return 404. Load must be positive and finite (the UI caps its control at 2000 MW); arithmetic
overflow is rejected. Term is one through seven years, and both fractions are within
zero to one.
The nullable zero-cost threshold and complete yearly horizon remain as documented
above. No new response fields or training hooks were added.

Run instructions and the provider replacement boundary are in [api/README.md](../api/README.md).
Export includes `{mode,status,response_origin,request,response,local_assumptions,result}`
so a local preview or fallback cannot be mistaken for a server result.

A live differential check in `web/src/api/live-contract.test.ts` compares server
responses with the frontend mock and feeds them through the existing client and
view adapter. Set `HEADROOM_API_URL` to the API or Vite origin for an explicit run;
ordinary unit tests do not need a server.
