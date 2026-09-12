# Current illustrative frontend fixture

**The shared API contract is now [BUILD_PLAN.md](BUILD_PLAN.md), section 2 (`POST /api/estimate`).** It supersedes this document as the contract for the real endpoint. This document records the existing local shell and its arithmetic so the migration is explicit; the shell has not yet been migrated to the new endpoint shape.

The current app imports a local, deterministic fixture. It makes no backend calls and performs no training or Monte Carlo sampling in the browser. All fixture values and results have `source_type: "assumption"` with `mock://illustrative/` references. These references name the demo assumptions; they are not citations to measurements, tariffs, or a fitted model.

The current fixture JSON is [web/src/model/mock-response.json](../web/src/model/mock-response.json). The app's typed fixture is [web/src/model/fixture.ts](../web/src/model/fixture.ts); a test checks that both stay identical. Public TypeScript types are in [web/src/model/types.ts](../web/src/model/types.ts). These are internal shell types, not a replacement for the shared endpoint contract.

## Pending canonical API migration

- Map `contract_years` to request `term_years`, and `flexibility_percent / 100` to request `flexibility_split`. Use pipeline-validated location IDs and the precomputed horizon from the shared contract.
- Consume `modeled_exposure.by_year`, `confidence`, `economics`, and `tariff` from the canonical response. Its exposure quantiles are already scaled by the API's `site_exposure`; do not scale them a second time in the browser. Keep slider updates immediate using a matching local mock adapter until the endpoint is available.
- Carry each response block's source into the `Sourced` view primitives, retain `inputs_echo` as assumptions, and extend provenance to support `source_type: "model"`. Render confidence from its supplied level/score/basis, never infer it from the quantile spread.
- The canonical response supplies p50/p90/p99, with no p10. The current fixture supports a p10–p90 fan and a four-quantile surface. During migration, show the supplied three-quantile surface and an explicitly labeled supported band, or obtain a documented additive p10 field. Do not invent p10 or reconstruct a probability density.
- Replace the shell's economics with the canonical economics block once sourced assumptions are available. Review annual versus contract-total comparison units and the rounding/provenance of example numbers during integration; the shared contract is published unchanged.

## Response structure

Every numerical value is wrapped in the same provenance primitive:

```ts
type SourcedValue<T = number> = {
  value: T;
  source_type: "data" | "clause" | "assumption";
  ref: string;
};

type ScenarioResponse = {
  schema_version: "1.0.0";
  grid_operator: "SPP";
  mode: "illustrative";
  locations: Array<{
    id: string;
    label: string;
    annual_series: Array<{
      year: SourcedValue; // relative contract year: 1 through 20
      p10: SourcedValue;  // baseline modeled exposure, hours/year
      p50: SourcedValue;
      p90: SourcedValue;
      p99: SourcedValue;
    }>;
  }>;
  defaults: {
    location_id: SourcedValue<string>;
    load_mw: SourcedValue;
    contract_years: SourcedValue;
    flexibility_percent: SourcedValue;
    site_exposure: SourcedValue;
    firm_wait_years: SourcedValue;
    gpu_per_mw: SourcedValue;
    gpu_hour_value_usd: SourcedValue;
    early_margin_usd_per_mw_year: SourcedValue;
  };
  decision_policy: {
    close_call_fraction: SourcedValue;
  };
};
```

For example, the first location's first year is represented exactly as:

```json
{
  "year": {
    "value": 1,
    "source_type": "assumption",
    "ref": "mock://illustrative/locations/spp-wichita-demo/annual_series/1/year"
  },
  "p10": {
    "value": 102,
    "source_type": "assumption",
    "ref": "mock://illustrative/locations/spp-wichita-demo/annual_series/1/p10-hours-per-year"
  },
  "p50": {
    "value": 200,
    "source_type": "assumption",
    "ref": "mock://illustrative/locations/spp-wichita-demo/annual_series/1/p50-hours-per-year"
  },
  "p90": {
    "value": 336,
    "source_type": "assumption",
    "ref": "mock://illustrative/locations/spp-wichita-demo/annual_series/1/p90-hours-per-year"
  },
  "p99": {
    "value": 510,
    "source_type": "assumption",
    "ref": "mock://illustrative/locations/spp-wichita-demo/annual_series/1/p99-hours-per-year"
  }
}
```

`year` is a relative year, not a calendar date. All quantiles in `locations[].annual_series` are supplied at `site_exposure = 1`. Rows are ordered by consecutive year, and each row satisfies `0 <= p10 <= p50 <= p90 <= p99`. The fixture supplies twenty rows per location; the UI selects a horizon of one through twenty years. Quantiles are hours in that individual year, not cumulative hours over the contract.

The identifiers `spp-wichita-demo`, `spp-oklahoma-city-demo`, and `spp-lincoln-demo` are illustrative SPP-region selectors. They are not verified network node IDs or proposed points of interconnection. Differences among their illustrative series carry no geographic finding. Replace the options with validated identifiers when connecting pipeline output.

## Inputs and units

The React state uses a plain `ScenarioInputs` object, derived from `defaults` by extracting each `value`:

```json
{
  "location_id": "spp-wichita-demo",
  "load_mw": 100,
  "contract_years": 10,
  "flexibility_percent": 60,
  "site_exposure": 0.4,
  "firm_wait_years": 3,
  "gpu_per_mw": 1000,
  "gpu_hour_value_usd": 2,
  "early_margin_usd_per_mw_year": 500000
}
```

| Input | Meaning |
| --- | --- |
| `load_mw` | Total facility load, MW. |
| `contract_years` | Comparison horizon in whole years, one through twenty. |
| `flexibility_percent` | Percentage of the load assumed interruptible, zero through one hundred. |
| `site_exposure` | User-assumed mapping of baseline grid stress exposure to this scenario, zero through one. |
| `firm_wait_years` | Additional years the firm alternative would take relative to flexible operation. This is an earlier-access advantage, not the absolute queue duration. |
| `gpu_per_mw` | Facility-wide conversion of MW into GPUs; facility overhead is assumed included. |
| `gpu_hour_value_usd` | Assumed economic loss per interrupted GPU-hour, USD. This is not a sourced market quote. |
| `early_margin_usd_per_mw_year` | Assumed incremental contribution from a year of earlier operation per MW, USD/MW/year. |

`deriveScenario(inputs)` rejects non-finite and negative numeric values, site exposure above one, flexibility above one hundred, unsupported location identifiers, and a contract term outside the supported whole-year horizon. Zero cost inputs remain mathematically defined. The input controls may apply narrower practical bounds.

## Browser-derived result

`deriveScenario(inputs)` returns this structure; all fields marked `SV` have the same `{value, source_type, ref}` shape above:

```ts
type DerivedScenario = {
  inputs: { [K in keyof ScenarioInputs]: SourcedValue<ScenarioInputs[K]> };
  annual_exposure: { p50: SV; p90: SV; p99: SV };
  annual_series: Array<{ year: SV; p10: SV; p50: SV; p90: SV; p99: SV }>;
  economics: {
    interruptible_mw: SV;
    annual_lost_gpu_hours: SV;
    annual_loss_usd: SV;
    term_loss_usd: SV;
    early_access_value_usd: SV;
    net_value_usd: SV;
    break_even_exposure_hours: SourcedValue<number | null>;
    break_even_site_exposure: SourcedValue<number | null>;
  };
  decision: "worth it" | "not worth it" | "close call";
  decision_policy: { close_call_fraction: SV };
};
type SV = SourcedValue<number>;
```

The calculation is deliberately transparent. Let `N` be contract years and `e` be site exposure. Each displayed year/quantile is its baseline quantile multiplied by `e`. `annual_exposure.pXX` is the arithmetic mean of these selected yearly `pXX` values. It is an annualized summary of marginal quantile paths: **it is not a quantile of the sum or mean of a stochastic multiyear trajectory.** Those joint-distribution results require precomputed paths from the offline pipeline.

### Exposure surface coordinates

The three-dimensional chart consumes the derived `annual_series` directly. Its axes are relative contract year (`x`), modeled exposure in hours/year (height, `y`), and percentile (`z`). Each year supplies four vertices at percentiles `10`, `50`, `90`, and `99`; percentile positions use proportional spacing, so the gap between `90` and `99` is smaller than the gap between `50` and `90`. Each vertex retains its sourced year and sourced quantile value for inspection.

Adjacent supplied vertices are connected piecewise-linearly for visual display. These connections do not reconstruct a probability density, add simulated samples, or establish a multiyear joint distribution. Only supplied quantile vertices are numerical observations of the fixture; the connecting surface is display geometry. With a one-year horizon, the chart shows that year's quantile cross-section without inventing a second year or an artificial time width. The existing fixture is illustrative and is not the output of a fitted Monte Carlo model.

The sourced `p99` field is now preserved in the derived yearly rows alongside `p10`, `p50`, and `p90`; it uses the same site-exposure multiplication and derivation-reference format. The upstream JSON response already contained yearly `p99`, so this chart requires no fixture or response-schema change. A true probability-density surface would require separate density bins or sample information from the offline pipeline, with provenance; four quantiles alone do not define it.

The economics follow the illustrative median path:

```text
interruptible_mw = load_mw × flexibility_percent / 100
annual_lost_gpu_hours = annual_exposure.p50 × interruptible_mw × gpu_per_mw
annual_loss_usd = annual_lost_gpu_hours × gpu_hour_value_usd
term_loss_usd = annual_loss_usd × N
early_access_value_usd = min(firm_wait_years, N) × load_mw × early_margin_usd_per_mw_year
net_value_usd = early_access_value_usd − term_loss_usd
break_even_exposure_hours = early_access_value_usd /
  (N × interruptible_mw × gpu_per_mw × gpu_hour_value_usd)
break_even_site_exposure = break_even_exposure_hours /
  mean(selected baseline p50 values)
```

The term loss is a **median-path scenario proxy**, not the p50 of total losses and not an expected-loss estimate. The comparison treats earlier access as benefiting the full selected load. The flexible arrangement and selected split are assumed available; the shell does not verify a contract's feasibility. Only years inside the comparison horizon receive earlier-access value.

If interruptible capacity or the value of interrupted compute is zero, both break-even values are `null`; the UI must render an explanatory label rather than Infinity or NaN. A finite break-even factor can exceed one. That means no crossover within the allowed slider range; it must not be clamped into a fictitious crossover.

The close-call zone is explicitly supplied as an assumption: `close_call_fraction = 0.05`. A result is `"close call"` when `abs(net_value_usd) <= early_access_value_usd × close_call_fraction`; otherwise its sign determines `"worth it"` or `"not worth it"`. The decision is conditional on this simplified comparison, not an underwriting recommendation.

At the defaults, average annualized median-path exposure is `87.8` hours/year and earlier-access value is `$150,000,000`. Break-even is `125` hours/year, or a site factor of approximately `0.5695`. Slider values `0.4`, `0.55`, and `0.9` demonstrate “worth it,” “close call,” and “not worth it.” These are arithmetic outputs from round dummy economics, not findings about SPP or any site.

Economic default refs use `mock://illustrative/economics-placeholder/inputs/<key>?pending=docs/ASSUMPTIONS.md` with `source_type: "assumption"`. These pending refs identify missing sourcing, not an existing citation. The panel, decision, and slider crossover remain explicitly marked mock. Kristian/Tharun's sourced values arrive through `main` in `docs/ASSUMPTIONS.md`; integrate those defaults and refs together as soon as available, retaining assumption labels for any unsourced or user-edited inputs. Derive break-even from the adopted values instead of treating the current demo crossover as an independent sourced fact.

## Provenance and integration rules

Derived mock references use `mock://illustrative/derived/<field>?<all scenario inputs>`. The query records the inputs used, and the formulas above define the referenced derivation. Edited input controls can use `user://scenario/<input-name>` references while preserving `source_type: "assumption"`.

When real baseline results arrive, preserve the wrapper on every input, chart coordinate, summary, and economic output. Use `data` for identifiable offline dataset/model artifacts and `clause` only for an actual cited contract provision. Any result that depends on site exposure remains conditional on that assumption; do not relabel the whole result as an observed site fact. A future integration must update the fixture adapter, illustrative mode/banner, location identifiers, and provenance together. This shell intentionally accepts only `mode: "illustrative"` today so a real response cannot silently retain the demo labeling.

No public grid dataset alone establishes whether a specific point of interconnection would have been interrupted. The site exposure factor remains a visible user assumption. This shell also omits discounting, taxes, capex, backup power, workload recovery/checkpoint overhead, penalties, changing compute prices, and contract-specific dispatch constraints. It assumes interrupted GPU-hours are economically lost at the selected rate; the user can lower that rate to reflect recoverable work. These simplifications are material to the illustrated economics.

To regenerate the JSON example after intentionally editing the fixture, run from `web` with Node supporting TypeScript type stripping:

```powershell
node --input-type=module -e "import { mockResponse } from './src/model/fixture.ts'; import { writeFileSync } from 'node:fs'; writeFileSync('src/model/mock-response.json', JSON.stringify(mockResponse, null, 2) + '\n');"
```

`npm test` checks numerical invariants, the visible crossover, zero-cost handling, deterministic horizons, source coverage, validation failures, and parity between the typed fixture and the complete JSON response.
