# Economics assumptions

**PLACEHOLDERS ONLY. None of these numbers has been sourced or verified.**
This file exists to wire the API while sourcing is pending. Its presence on `main`
does **not** make its contents real data or justify removing mock labels. Replace
the values, source references, retrieval dates, and illustrative ranges together
when the team supplies reviewed sources from `docs/DATA_NEEDED.md`.

| Input | Fake value | Unit | Fake low / high | Source URL / retrieval date |
|---|---:|---|---|---|
| GPU rental price | 2 | USD per GPU-hour | 1 / 4 | None; not researched |
| Industrial electricity price | 50 | USD per MWh | 0 / 100 | None; not researched |
| GPUs per MW | 1000 | GPUs per MW of total facility load | 500 / 1500 | None; not researched |
| Earlier connection | 3 | years | 0 / 5 | None; not researched |
| Net operating margin | 500000 | USD per MW-year | 0 / 1000000 | None; not researched |
| Close-call tolerance | 0.05 | fraction | 0 / 0.1 | None; design assumption |

The ranges above are arbitrary development ranges, **not plausible market bounds**.
No GPU generation, tariff eligibility, utilization, or facility efficiency has been
established. The inputs are round fixture values, not investment guidance.

## Calculation policy

`api/economics.py` reads the JSON block below on each request. The table above is
explanatory; the JSON is the machine-readable source of truth. There are no numeric
fallbacks in code if the file is missing or invalid. Such a configuration problem
must produce an explicit API error instead of a fabricated result.

- Interruptible capacity = requested load MW × flexibility split.
- Lost GPU-hours per year = modeled exposure hours × interruptible MW × GPUs/MW,
  independently for each reported quantile.
- Annual cost = lost GPU-hours × GPU rental price. This is gross lost rental value;
  it does not yet model electricity savings, restart overhead, recoverable work, or
  SLA penalties.
- Value of early connection = min(earlier connection years, contract term) × load
  MW × **net** operating margin per MW-year. This is an independent placeholder
  margin input; it has not yet been derived from rental utilization and power cost.
- Industrial electricity price is **informational only in this version**. It is
  recorded for the future sourced margin calculation. It is not subtracted again
  from the net margin or silently applied as an avoided-cost credit.
- Break-even hours/year = early-connection value ÷ (contract years × interruptible
  MW × GPUs/MW × GPU rental price). If the denominator is zero there is no finite
  crossover, represented by `null` in the canonical API response.
- Decision: `not_worth_it` when the median annual-cost path × contract term exceeds
  early value × (1 + tolerance); `worth_it` when the p90 annual-cost path × term is
  below early value × (1 − tolerance); otherwise `close_call`. Equality stays a
  close call. These summed marginal-quantile paths are not quantiles of total loss.

## Machine-readable assumptions

Keep one marked JSON block. Values are finite, with nonnegative prices, margin,
years, and ranges; positive GPU density; and tolerance in `[0, 1)`. Each value must
lie within its recorded range. A placeholder entry must retain `source_type:
"assumption"`, an explicit `mock://` placeholder reference, and null source URL and
retrieval date. Reviewed entries may use `source_type: "data"` with an actual HTTPS
source URL and retrieval date. Top-level status is `placeholder`, `mixed`, or
`sourced` according to whether all, some, or none of the entries remain placeholders.
The derived API economics block still carries `source_type: "assumption"` because
the calculation uses the user's scenario assumptions; its reference records its
input values and sources. A real exposure model does not make placeholder economics
real.

<!-- headroom:economics-assumptions:v1 -->
```json
{
  "schema_version": 1,
  "status": "placeholder",
  "gpu_rental_price_usd_per_hour": {
    "value": 2,
    "source_type": "assumption",
    "ref": "mock://economics-placeholder/docs/ASSUMPTIONS.md#gpu-rental-price; placeholder, not sourced",
    "unit": "USD/GPU-hour",
    "source_url": null,
    "retrieved_on": null,
    "low": 1,
    "high": 4
  },
  "industrial_electricity_price_usd_per_mwh": {
    "value": 50,
    "source_type": "assumption",
    "ref": "mock://economics-placeholder/docs/ASSUMPTIONS.md#industrial-electricity-price; placeholder, not sourced",
    "unit": "USD/MWh",
    "source_url": null,
    "retrieved_on": null,
    "low": 0,
    "high": 100
  },
  "gpus_per_mw": {
    "value": 1000,
    "source_type": "assumption",
    "ref": "mock://economics-placeholder/docs/ASSUMPTIONS.md#gpus-per-mw; placeholder, not sourced",
    "unit": "GPU/MW",
    "source_url": null,
    "retrieved_on": null,
    "low": 500,
    "high": 1500
  },
  "early_connection_years": {
    "value": 3,
    "source_type": "assumption",
    "ref": "mock://economics-placeholder/docs/ASSUMPTIONS.md#early-connection-years; placeholder, not sourced",
    "unit": "year",
    "source_url": null,
    "retrieved_on": null,
    "low": 0,
    "high": 5
  },
  "early_margin_usd_per_mw_year": {
    "value": 500000,
    "source_type": "assumption",
    "ref": "mock://economics-placeholder/docs/ASSUMPTIONS.md#net-operating-margin; placeholder, not sourced",
    "unit": "USD/MW-year",
    "source_url": null,
    "retrieved_on": null,
    "low": 0,
    "high": 1000000
  },
  "close_call_fraction": {
    "value": 0.05,
    "source_type": "assumption",
    "ref": "mock://economics-placeholder/docs/ASSUMPTIONS.md#close-call-tolerance; placeholder, unreviewed decision rule",
    "unit": "fraction",
    "source_url": null,
    "retrieved_on": null,
    "low": 0,
    "high": 0.1
  }
}
```
