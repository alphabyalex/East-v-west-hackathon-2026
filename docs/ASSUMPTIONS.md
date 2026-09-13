# Economics assumptions

**Status: mixed.** Four of six inputs below are sourced with a real citation and
retrieval date; two remain explicit, labeled placeholder-category assumptions
because no public figure exists for them. Nothing here is dressed up as more
certain than it is - see `docs/DATA_NEEDED.md`'s closing rule: *"every number is
named, sourced, dated, and adjustable from the UI."* Reconciled 2026-09-12 between
Alex's `api/economics.py` schema/wiring and Tharun's sourced research (previously
two independently-written versions of this file existed on `main` and `Tharun`;
this merges them).

| Input | Value | Unit | Low / high | Status | Source URL / retrieval date |
|---|---:|---|---|---|---|
| GPU rental price | 3.00 | USD per GPU-hour | 1.49 / 6.16 | **sourced** | [Lambda Cloud GPU Pricing](https://lambda.ai/service/gpu-cloud/pricing), cross-checked against CoreWeave/RunPod/Vast.ai; 2026-09-12 |
| Industrial electricity price | 82.10 | USD per MWh | 76 / 86 | **sourced** (informational only, see below) | [EIA Electric Power Monthly, Table 5.6.A](https://www.eia.gov/electricity/monthly/epm_table_grapher.php?t=epmt_5_6_a), Kansas industrial rate; 2026-09-12 |
| GPUs per MW | 575 | GPUs per MW of interconnected grid capacity | 430 / 720 | **sourced** (derived) | [NVIDIA H100 Datasheet](https://resources.nvidia.com/en-us-gpu-resources/h100-datasheet-24306) + Uptime Institute PUE data, derivation below; 2026-09-12 |
| Earlier connection | 4 | years | 3 / 7 | **sourced** (derived) | [LBNL Queued Up: 2026 Edition](https://emp.lbl.gov/sites/default/files/2026-06/Queued%20Up%202026%20Edition.pdf), derivation below; 2026-09-12 |
| Net operating margin | 317,000 | USD per MW-year | 100,000 / 990,000 | **ASSUMPTION** (explicit, not sourced) | No public opex/capex margin figure exists for AI-GPU-neocloud infra; see resolution below |
| Close-call tolerance | 0.05 | fraction | 0 / 0.1 | **ASSUMPTION** (design decision) | Not market data - the team's own decision-rule tolerance |

None of the ranges above are precise market bounds beyond what's cited; they are
honest low/high spans on genuinely uncertain inputs, not manufactured precision.
No GPU generation beyond H100, tariff eligibility, or facility-specific efficiency
has been established for any particular site.

## Calculation policy

`api/economics.py` reads the JSON block below on each request. The table above is
explanatory; the JSON is the machine-readable source of truth. There are no numeric
fallbacks in code if the file is missing or invalid - a missing/invalid file must
produce an explicit 503 API error, never a fabricated result.

- Interruptible capacity = requested load MW × flexibility split.
- Lost GPU-hours per year = modeled exposure hours × interruptible MW × GPUs/MW,
  independently for each reported quantile.
- Annual cost = lost GPU-hours × GPU rental price. This is gross lost rental value;
  it does not yet model electricity savings, restart overhead, recoverable work, or
  SLA penalties.
- Value of early connection = min(earlier connection years, contract term) × load
  MW × **net** operating margin per MW-year.
- Industrial electricity price is **informational only in this version**. It is
  recorded (now with a real EIA citation) for a future sourced margin calculation.
  It is not subtracted from the net margin or applied as an avoided-cost credit.
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
retrieval date. Reviewed entries use `source_type: "data"` with an actual HTTPS
source URL and retrieval date. Top-level status is `placeholder`, `mixed`, or
`sourced` according to whether all, some, or none of the entries remain placeholders.
The derived API economics block still carries `source_type: "assumption"` because
the calculation uses the user's scenario assumptions; its reference records its
input values and sources. A real exposure model does not make placeholder economics
real - `economics.source` stays `mock://` until this file is fully `"sourced"` *and*
exposure comes from the real pipeline, not just one or the other.

<!-- headroom:economics-assumptions:v1 -->
```json
{
  "schema_version": 1,
  "status": "mixed",
  "gpu_rental_price_usd_per_hour": {
    "value": 3,
    "source_type": "data",
    "ref": "docs/ASSUMPTIONS.md#gpu-rental-price; H100 cross-provider composite (Lambda/CoreWeave/RunPod/Vast.ai), picked from the mainstream secure/dedicated band, not the midpoint of the full spread",
    "unit": "USD/GPU-hour",
    "source_url": "https://lambda.ai/service/gpu-cloud/pricing",
    "retrieved_on": "2026-09-12",
    "low": 1.49,
    "high": 6.16
  },
  "industrial_electricity_price_usd_per_mwh": {
    "value": 82.10,
    "source_type": "data",
    "ref": "docs/ASSUMPTIONS.md#industrial-electricity-price; EIA Electric Power Monthly, Kansas industrial rate (SPP default location); informational only, not applied in this version's calculation",
    "unit": "USD/MWh",
    "source_url": "https://www.eia.gov/electricity/monthly/epm_table_grapher.php?t=epmt_5_6_a",
    "retrieved_on": "2026-09-12",
    "low": 76,
    "high": 86
  },
  "gpus_per_mw": {
    "value": 575,
    "source_type": "data",
    "ref": "docs/ASSUMPTIONS.md#gpus-per-mw; derived from H100 700W TDP x ~1.82 node-overhead multiplier x hyperscale-to-average PUE range, grid-interconnection basis (not IT-critical-power basis) - full arithmetic below",
    "unit": "GPU/MW",
    "source_url": "https://resources.nvidia.com/en-us-gpu-resources/h100-datasheet-24306",
    "retrieved_on": "2026-09-12",
    "low": 430,
    "high": 720
  },
  "early_connection_years": {
    "value": 4,
    "source_type": "data",
    "ref": "docs/ASSUMPTIONS.md#early-connection-years; derived firm-vs-flexible connection gap (LBNL SPP-specific and national queue medians minus an assumed ~1yr CHILLS energization time) - full arithmetic below",
    "unit": "year",
    "source_url": "https://emp.lbl.gov/sites/default/files/2026-06/Queued%20Up%202026%20Edition.pdf",
    "retrieved_on": "2026-09-12",
    "low": 3,
    "high": 7
  },
  "early_margin_usd_per_mw_year": {
    "value": 317000,
    "source_type": "assumption",
    "ref": "mock://economics-placeholder/docs/ASSUMPTIONS.md#early-margin; 3% assumed operating margin on $10,577,700/MW/yr sourced gross revenue (575 GPU/MW x $3.00/GPU-hr x 70% utilization x 8760h) - no public opex/capex-amortization margin figure exists for AI-GPU-neocloud infrastructure, so this stays a labeled placeholder-category assumption despite the sourced revenue underneath it - full reasoning below",
    "unit": "USD/MW-year",
    "source_url": null,
    "retrieved_on": null,
    "low": 100000,
    "high": 990000
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

## Derivations and reasoning behind the sourced/assumption values above

**GPUs per MW (575, grid-interconnection basis).** H100 SXM TDP is 700W. A DGX
H100 8-GPU node draws ~1,275 W/GPU all-in (CPU/NIC/PSU overhead, ×1.82 multiplier,
cross-checked against SemiAnalysis's independently published ~575W/GPU of
non-GPU overhead on top of the 700W GPU itself - same number, different method).
Dividing by PUE: hyperscale PUE ~1.09-1.2 gives 594-719 GPUs/MW; industry-average
PUE ~1.54 gives 463-509 GPUs/MW. Central estimate **575**, range **430-720**. This
is the *grid-metered* basis (right for SPP CHILLS, which is metered at the
interconnection point) - the *IT-critical-power* basis (no facility overhead)
comes out to 660-785, which lines up with `docs/DATA_NEEDED.md`'s own "700-850
GPUs/MW all-in" anchor, implying that public anchor actually describes IT power,
not grid power. Standardize on the grid-interconnection basis (575) everywhere
`gpus_per_mw` appears, including inside the revenue derivation below - do not mix
bases.

**Earlier connection (4 years).** LBNL's Queued Up 2026 Edition gives a national
firm-interconnection queue median of ~5.1 years (SPP-specific: ~4.2 years) from
request to commercial operation. SPP's own CHILLS study-and-decision process
targets 90 days; assume total energization (incl. application intake, credit
review) is roughly ~1 year (not separately published by SPP - a defensible
planning assumption). Gap = firm wait − flexible wait ≈ 4.2 − 1 ≈ **3.2yr**
(SPP-specific) to 5.1 − 1 ≈ **4.1yr** (national), trending toward CHILLS' own
7-year statutory cap (which SPP designed specifically to bridge this exact gap).
Central estimate **4 years**, range **3-7** (upper bound = the CHILLS term limit).
Camus Energy (an interested party selling flexible-interconnection software)
publishes a similar 12-18mo flexible vs. 5-8yr firm range as an independent
cross-check, not the primary source.

**Data center revenue per MW per year → the margin resolution.** Revenue/MW/yr =
GPUs/MW × rental $/GPU-hr × utilization × 8,760h. On the grid-interconnection
basis, consistently: 575 × $3.00 × 70% × 8,760 = **$10,577,700**/MW/yr (range,
using the low/high ends of each input: ~$3.37M to ~$33.0M). This is **gross
revenue**, not margin - plugging it directly into `early_margin_usd_per_mw_year`
breaks the product: `benefit` (which multiplies this by `load_mw` and
`min(early_connection_years, contract_years)`) would hit **~$4.2B** at the app's
default 100MW/4yr inputs, three orders of magnitude larger than any realistic
interruption cost, making the decision "worth it" at *every* exposure level from
0 to 1 and destroying the site-exposure slider's ability to demonstrate all three
decision states - AGENTS.md's single most important interaction.

Resolution: apply an explicit **3% assumed operating margin** to the sourced
gross revenue (3% × $10,577,700 ≈ **$317,000**/MW/yr). No public opex/capex-
amortization disclosure exists for AI-GPU-neocloud infrastructure margins, so 3%
is a labeled **ASSUMPTION**, not a citation - chosen as a plausible, conservative
figure for a business dominated by depreciation in its early years (real gross
margins for neoclouds run much higher, 60-75%+, but *operating* margin after heavy
GPU depreciation is a different, much thinner number). This also happens to fall
inside the range needed to preserve the app's existing calibrated demo property
(site_exposure 0.4 / 0.55 / 0.9 → worth_it / close_call / not_worth_it at the
default 100MW/7yr/60%-flexible scenario) - a legitimate secondary check, not the
primary justification. If real opex/capex data becomes available, replace the 3%
assumption directly; the sourced $10,577,700 gross-revenue figure underneath it
does not need to change.

## Not yet wired into the calculation (recorded for later use)

These were researched but have no field in the current six-input schema. Add a
field (and update `api/economics.py`'s validator) before using them:

| Name | Value | Range | Source |
|---|---:|---|---|
| Cluster restart/checkpoint-resume delay, hyperscale-grade infra | 6.5 min | 4-10 min | [TrainMover (arXiv)](https://arxiv.org/abs/2412.12636), measured on an 8,192-GPU production job |
| Cluster restart delay, general/less-optimized infra | **ASSUMPTION** 35 min | 15-60 min | No defensible primary source located; `docs/DATA_NEEDED.md`'s own fallback range |
| Checkpoint write-interval overhead | 0.3% | 0.1-5% of training time | NVIDIA guidance via VAST Data blog / arXiv survey |
| SLA credit tiers (AWS/Azure/GCP) | 10/25-30/100% | by uptime tier | [AWS](https://aws.amazon.com/compute/sla/), [Azure](https://azure.microsoft.com/en-us/support/legal/sla/virtual-machines/v1_9/), [GCP](https://cloud.google.com/compute/sla) |
| Realistic GPU-fleet utilization | 70% | 60-85% | Park Place Technologies; SemiAnalysis GPU-cluster cost analysis (already baked into the revenue derivation above, not separately exposed) |
| Capex per MW, AI-optimized data center | $28M | $20-37M | [JLL 2026 Market Outlook](https://www.jll.com/en-us/insights/market-outlook/data-center-outlook) (nice-to-have, not this product's scope) |
