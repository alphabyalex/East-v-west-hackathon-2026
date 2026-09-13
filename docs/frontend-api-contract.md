# Frontend consumption of the shared estimate contract

The canonical team contract is [BUILD_PLAN.md](BUILD_PLAN.md), section 2:
**`POST /api/estimate`**. The frontend consumes this response through the local FastAPI endpoint by default.
The endpoint tries Kristian's precomputed reader and returns explicitly sourced
placeholders when that reader or its parquet is absent. The optional frontend local
fallback remains an illustrative fixture. Neither path runs training, inference,
Monte Carlo sampling, or external data pulls.

The complete current mock response is
[`web/src/model/mock-response.json`](../web/src/model/mock-response.json).
[`contract.ts`](../web/src/model/contract.ts) defines the request/response types;
[`estimate.ts`](../web/src/model/estimate.ts) contains the mock provider and view
adapter. A parity test verifies that the JSON example matches the provider.

## Request

```json
{
  "location_id": "SPP_SYSTEM",
  "load_mw": 100,
  "term_years": 7,
  "flexibility_split": 0.6,
  "site_exposure": 0.4
}
```

The initial location matches Kristian's real load input ID, `SPP_SYSTEM`: a system
aggregate, not a site or pricing node. Its modeled exposure remains placeholder
until a matching valid precomputed artifact exists. City demo choices remain
explicitly illustrative and are never aliases for this aggregate.
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

These conventions are logged in FROM_CODEX.md for review; the integrated producer's
artifact requirements are also recorded in BUILD_PLAN.md section 1.

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

The local fallback uses the documented scenario defaults, with per-input provenance:

| Internal input | Scenario value | Unit |
|---|---:|---|
| `firm_wait_years` | 4 | years of earlier access |
| `gpu_per_mw` | 575 | GPUs per grid-interconnection MW |
| `gpu_hour_value_usd` | 3 | USD per GPU-hour |
| `early_margin_usd_per_mw_year` | 317000 | assumed operating-margin proxy, USD per MW-year |

The API reads the marked JSON block in [`docs/ASSUMPTIONS.md`](ASSUMPTIONS.md).
The frontend's offline snapshot is `web/src/model/economics-assumptions.json`;
API mode reads the same block over HTTP, including every input's provenance.
Status is `mixed`: Tharun's cited planning assumptions plus unverified margin and
decision-tolerance placeholders. $10,577,700/MW-year is derived scenario gross
revenue; $317,000/MW-year applies his unverified 3% operating-margin assumption,
rounded. Neither is observed site net profit. The latter retains `mock://`
provenance. The cited defaults are assumptions, not independently verified facts.
API economic refs start with `mock://economics-placeholder/` while any economic input
or exposure dependency remains a placeholder. Replace applicable values, units,
sources, retrieval dates, and ranges together when reviewed sourcing lands. Retain
visible mock labels for every remaining placeholder dependency.

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
three states. Earlier benefit is USD126.8 million. Median-hours break-even is about
175.02 h/year. This economic equality is labeled **median cost crossover** in the UI;
it is not either boundary of the p50/p90 decision rule. Its site factor is null at
zero exposure because a zero-scaled response cannot recover the baseline.

The mock's `worst_contiguous_outage_hours` is a round authored 40-hour placeholder
multiplied by site exposure. This field is retained for contract completeness, not
shown as evidence of a site's actual outage duration. Kristian's implemented reader
supplies p99 of annual longest modeled episodes; the API takes the maximum over the
term and scales it by the site assumption. This is not a guaranteed maximum.
Restart overhead, discounting, and SLA penalties remain
outside the current economics.

## HTTP provider and explicit local fallback

The workspace calls `postEstimate(request, {signal})` directly at
`http://127.0.0.1:8000/api/estimate`. `VITE_API_BASE_URL` overrides that origin;
an explicitly empty value selects a same-origin Vite proxy instead.
`VITE_ESTIMATE_MODE=api` is the default. Set it to `local` (see
`web/.env.example`) and restart Vite to make no HTTP requests. Visible controls can
also select Local mock or return to API defaults without restarting.

The backend also accepts direct browser calls to
`http://127.0.0.1:8000/api/estimate` from exactly `http://127.0.0.1:5174` using
`GET`, `POST`, and `Content-Type`, without credentials. CORS exposes the successful response
header `X-Headroom-Exposure-Source: pipeline|placeholder`; this describes exposure
only and does not certify economics or tariffs. No proxy is required for direct
calls. See
[api/README.md](../api/README.md) for a direct `fetch` example.

While a request is pending, the outputs show a visibly labeled local mock preview
for the current inputs. Failed, timed-out, or invalid responses leave the local
fallback usable and visibly identified, with a Retry control. Superseded requests
are canceled and stale responses cannot overwrite current-input results. The lower
level client still validates provenance, quantiles, complete horizons, finite values,
and the exact inputs echo; it never repairs a response or silently falls back.

The four editable economic controls remain local-only: editing one switches the
workspace to local mock mode with an explanation. Returning to API mode restores
the file-backed economic defaults fetched from `GET /api/economics-assumptions`.
That endpoint returns the exact marked JSON block defined in BUILD_PLAN.md section 3;
the frontend validates it alongside the estimate and checks their arithmetic agrees.
If either response fails validation, the UI uses an explicit local mock fallback.
No economic override fields are sent to the server.
This preserves the five-field shared request until the team agrees an additive
interface. The API's returned economics are displayed directly, never overwritten
with unsent local assumptions.

`api/main.py` selects `api.pipeline_provider.get_pipeline_location`, which imports
and calls `pipeline.simulate.get_location_estimate(location_id, path=...)` only when the
callable, `data/processed/exposure_by_location.parquet`, and matching model-card and
simulation provenance companions are available. Its exact
reader return shape is in BUILD_PLAN.md section 1. It validates unscaled annual
quantiles, ordered years, confidence level/score/precedent count, model version, and
the requested location, then applies the site factor once in `api.estimate`.
Exposure/confidence from a valid reader retain model provenance, experimental
annual-tail limitations, and the current Low annual-confidence cap. The numeric
score describes classifier agreement, not annual-tail validation. Tariff extraction
is still unwired and explicitly marked as placeholder.

A missing module/callable/file, including a file becoming unavailable at import or
read time, produces an explicit placeholder response in the same shape. Exposure
and confidence refs include `mock://placeholder/...; placeholder, pipeline not wired
yet; <reason>` with `source_type: "assumption"`. Numeric fallback values retain
the frontend fixture's authored values. The placeholder set contains the three
demo IDs plus `SPP_SPS_HUB` and `SPP_SYSTEM`; none of this establishes actual node coverage.

Invalid requests return 422; unknown locations return 404, including the reader's
exported `LocationNotFoundError`. A broken dependency/import, failed reader,
malformed/mismatched output, or insufficient requested horizon returns 503, without
silently substituting invented exposure. Missing/invalid economics configuration
also returns 503. Load must be positive and finite (the UI caps its control at
2000 MW); arithmetic overflow returns 422. Term is one through seven years, and both
fractions are within zero to one. The nullable zero-cost threshold and complete
yearly horizon remain as documented above. No response-body fields or training
hooks were added.

`api/economics.py` reads the single `headroom:economics-assumptions:v1` marked JSON
block on every request. Its six inputs are GPU rental price, industrial electricity
price, GPUs/MW, earlier-connection years, net margin/MW-year, and close-call tolerance;
each records `{value, source_type, ref, unit, source_url, retrieved_on, low, high}`.
The exact keys and validation policy are in the assumptions file and API README.
There are no silent numeric defaults. Electricity is informational in this version,
not an avoided-cost credit or a second deduction from net margin. Unverified margin
and tolerance remain placeholders; derived results also keep mock labels while
their exposure dependency remains authored.
The documented Uvicorn command has no automatic code reload: restart it after API
or already-imported pipeline code changes; assumptions edits apply on the next request.

Run instructions and the provider replacement boundary are in [api/README.md](../api/README.md).
Export includes `{mode,status,response_origin,request,response,local_assumptions,result,sensitivity}`
so a local preview or fallback cannot be mistaken for a server result.

A live differential check in `web/src/api/live-contract.test.ts` compares server
responses with the frontend mock and feeds them through the existing client and
view adapter. Set `HEADROOM_API_URL` to the API or Vite origin for an explicit run;
ordinary unit tests do not need a server.

## Transparency diagnostics handoff

The expandable **Model & evidence** panel is part of the working page, next to
modeled exposure. It explains the system/site boundary, exposes the current sourced
site assumption, preserves the estimate-confidence framing, and contains tariff
records from the canonical response. Mock clauses are identified as unextracted and
have no fabricated citation links.

Reliability diagnostics are a separate local display fixture in
`web/src/model/transparency.ts`, **not an extra field added to POST /api/estimate**:

```ts
{
  status: 'mock',
  target: 'system_stress',
  brier_score: SourcedValue<number>,
  naive_brier_score: SourcedValue<number>,
  reliability_curve: Array<{
    mean_predicted: SourcedValue<number>,
    observed_fraction: SourcedValue<number>
  }>
}
// SourcedValue<T> = { value: T, source_type: 'assumption', ref: 'mock://...' }
```

These authored curve points and score placeholders are not a held-out evaluation,
not the confidence signal, and not a site-outage probability. The chart, hover
readouts, metric values, and exact-value table all retain provenance. Kristian must
supply a validation artifact with the evaluation target, split/period, score/baseline,
bin counts, and source/model version before the mock validation label can be removed.
Agree that diagnostic interface separately; the existing estimate contract remains
unchanged.

## Break-even sensitivity handoff

`POST /api/estimate` and `GET /api/economics-assumptions` keep their existing exact
shapes. Sensitivity is a frontend derivation over the active response, exported as
the additional `sensitivity` field in the downloaded scenario JSON. Its policy and
ranges are recorded in [ASSUMPTIONS.md](ASSUMPTIONS.md#sensitivity-policy).

```ts
type Source = { source_type: 'data' | 'clause' | 'assumption' | 'model'; ref: string }
type SourcedValue<T = number> = Source & { value: T }
type Quantiles<T> = { p50: T; p90: T; p99: T }
type Decision = 'worth_it' | 'not_worth_it' | 'close_call'
type Snapshot = {
  net_value_usd: Quantiles<SourcedValue> // after each annual cost path, not value quantiles
  early_value_usd: SourcedValue
  annual_cost_usd: Quantiles<SourcedValue>
  decision: Decision
  breakeven_hours: SourcedValue<number | null>
  breakeven_note?: string
  source: Source
}
type Endpoint = {
  input: SourcedValue
  delta_value_usd: SourcedValue // change in p50-path comparison versus current
  snapshot: Snapshot
  source: Source
}
type UnmodeledEndpoint = {
  input: SourcedValue
  delta_value_usd: null
  snapshot: null
  source: Source
}
type Row = {
  key: 'gpu_rental_price' | 'electricity_price' | 'utilization' | 'flexibility_split' | 'site_exposure'
  label: string
  unit: 'USD/GPU-hour' | 'USD/MWh' | 'fraction'
  baseline_input: SourcedValue
  source: Source
} & ({
  status: 'modeled'
  low: Endpoint
  high: Endpoint
  swing_usd: SourcedValue
} | {
  status: 'not_modeled'
  reason: string
  low: UnmodeledEndpoint
  high: UnmodeledEndpoint
  swing_usd: null
})
type SensitivityResult = {
  baseline: Snapshot
  rows: Row[] // descending modeled swing, then the two not-modeled rows
  source: Source
  notes: string[]
}
```

The implementation is `web/src/model/sensitivity.ts`. Low/high always name the
**input** bounds, even when low input produces higher value. Electricity and
utilization are explicitly unmodeled per the user's instruction; null outcomes must
never become zero bars or hypothetical decisions. Their documented ranges still
carry sources. Computed outputs remain assumptions, with `mock://` preserved whenever
their dependencies include placeholders. The policy holds early contribution fixed
and uses the existing p50/p90 decision test at both endpoints.

`web/src/api/sensitivity.ts` restores unscaled exposure arithmetically when the
received site factor is positive. At exactly zero it sends one extra five-field
`POST /api/estimate` for the same scenario with `site_exposure: 1`. Both requests share
the transport's abort/timeout lifecycle. Metadata/evidence mismatches, malformed
companions and failures use the existing explicit local fallback; the UI never
combines server values with fixture exposure. No training, simulation, external
market pull or API contract extension runs on slider changes.
