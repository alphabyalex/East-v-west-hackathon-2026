# Data needed — economics model + tariff extraction

Owner: Teammate 1. Everything here is public. Nothing needs an account application.

Record every value in `docs/ASSUMPTIONS.md` as: name, value, unit, source URL, date
retrieved, plausible low/high range. If a number cannot be sourced, mark it
`ASSUMPTION` rather than dressing it up as a fact.

---

## A. Hardware and power conversion (turns megawatts into GPUs)

| What | Why | Where to get it |
|---|---|---|
| GPU power draw, H100 and B200 | Base of the MW to GPU conversion | NVIDIA datasheets. H100 SXM is 700W TDP; confirm B200 yourself |
| Server node overhead | A GPU is not the whole machine. CPU, memory, networking, storage add real draw | Vendor spec sheets for DGX/HGX class nodes |
| PUE (power usage effectiveness) | Cooling and facility overhead. Total site power divided by IT power | Uptime Institute annual survey. Hyperscale is roughly 1.1 to 1.2, industry average closer to 1.5. Pick one, cite it, expose it as adjustable |
| Derived: GPUs per MW | The number the whole model rests on | You compute it. Rough anchor is 700 to 850 GPUs per MW all in. Show your arithmetic in the doc |

---

## B. Cost of interrupted compute

| What | Why | Where to get it |
|---|---|---|
| GPU rental price, per GPU-hour | Converts lost GPU-hours to dollars | Public pricing pages: CoreWeave, Lambda, RunPod, Vast.ai. Also the Silicon Data H100 and B200 rental indices, which are what the new CME compute futures settle against. Use a range, not one number, prices move |
| Industrial electricity price by state | Operating cost, and part of the early-connection benefit | EIA Electric Power Monthly, industrial sector rates, cents per kWh by state |
| Cluster restart and checkpoint overhead | An interruption costs more than the idle hours. Resuming a large distributed training run takes real time | Search published engineering writeups on large-scale training checkpointing. If nothing solid, set a defensible range (for example 15 to 60 minutes) and label it ASSUMPTION |
| SLA penalty structure for inference | What a missed availability commitment actually costs | Public cloud SLA pages (AWS, Azure, GCP) show credit tiers by uptime percentage. Use those as a proxy |

---

## C. Value of connecting early (the other side of the ledger)

| What | Why | Where to get it |
|---|---|---|
| Firm interconnection wait time, by region | The thing the flexible deal is buying you. If firm takes 5 years and flexible takes 1, you gained 4 years of operation | LBNL "Queued Up" report (emp.lbl.gov/queues), free download. Also RTO queue statistics |
| Flexible/non-firm connection timeline | The comparison case | SPP CHILLS filing and SPP's High Impact Large Load page. Camus Energy has published a 12 to 18 month figure for flexible connections, useful as a cross-check |
| Data center revenue per MW per year | What a year of earlier operation is worth | Derive it: GPUs per MW times rental price times realistic utilization. Do not use a headline figure from a press release |
| Realistic utilization rate | Nobody runs at 100% | Industry commentary and cloud provider disclosures. Expose as adjustable |
| Capex per MW | Only needed if we model payback rather than just revenue. Optional | Data center construction cost reporting. Treat as nice to have |

---

## D. Operational behavior (the flexibility split)

| What | Why | Where to get it |
|---|---|---|
| Typical training vs inference mix in AI data centers | Determines how much load can actually pause | Industry analysis and operator disclosures. This is genuinely uncertain, so it should be a prominent user slider, not a fixed number |
| What fraction of training work is checkpointable | Flexible work delayed is not work destroyed | Engineering writeups from large training runs |
| Ramp time, cold start to full load | Curtailment has a tail cost on both ends | Same sources as restart overhead. Range plus ASSUMPTION label is fine |

---

## E. Contract documents (for tariff extraction)

Download these into `data/tariffs/`. All public.

| Document | What it gives us |
|---|---|
| FERC order approving SPP CHILLS, 195 FERC 61,196, on spp.org | The primary source. Curtailment triggers, term, priority, obligations |
| SPP High Impact Large Load (HILL) integration page | Supporting process detail |
| SPP tariff sheets for the CHILLS service | The actual tariff language, which is what we quote |
| FERC eLibrary, dockets EL26-67 through EL26-72 | The June 2026 show-cause orders on all six operators. Background and future scope, not needed for the SPP build |

Priority order: get the CHILLS order first. Everything else is optional until the SPP path works end to end.

---

## F. Do NOT source these, the pipeline provides them

These come from the data pipeline. Take them as inputs, do not go looking for them.

- Historical grid stress and curtailment-trigger hours by location
- The calibrated probability of a trigger condition given grid state
- The forward Monte Carlo distribution of exposure hours over the contract term
- Locational marginal prices and historical load

While you wait, develop against dummy values. Use 200 exposure hours per year as a
placeholder and make sure nothing in your code assumes a single point estimate, since
the real input will be a distribution.

---

## Must-have vs nice-to-have

**Must have before CP2 (hour 24):** GPU power draw, PUE, GPUs per MW, GPU rental price
range, firm vs flexible connection timelines, the SPP CHILLS order downloaded.

**Nice to have:** capex per MW, detailed SLA penalty tiers, ramp time precision,
documents for operators other than SPP.

---

## The rule that governs all of it

Every number is named, sourced, dated, and adjustable from the UI. When a judge says
"this is all assumptions," the answer is "correct, here they all are, here is where each
came from, and here is the one that actually changes the decision."
