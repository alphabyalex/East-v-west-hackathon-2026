# Assumptions — Fluxline Economics Model

The marked JSON block at the end of this file is the source of truth for the API's
economics inputs. `api/economics.py` reads it on each request; there are no hardcoded
backend defaults. The research notes below were supplied by Tharun in commits
`7669f42` and `59cbdda`; their retrieval dates and source claims are preserved as
reported, **not independently verified by this integration**. A cited planning
default is still an assumption, not a measurement of a proposed site.

The selected defaults are 575 GPUs per grid MW, $3 per GPU-hour, and a four-year
earlier-connection gap. The $317,000/MW-year early-value input is **Tharun's
unverified operating-margin assumption**, not sourced net profit: it applies an
assumed 3% to $10,577,700 of scenario gross revenue and rounds the result. Its
`mock://` placeholder tag remains. Electricity is a Kansas reference only and is
informational; it is not subtracted from economics a second time. The recorded
ranges are scenario ranges, not statistical confidence intervals.

---

## 1. Hardware & power conversion

Turns megawatts of interconnection capacity into a GPU count.

| Name | Value | Range | Unit | Source | Date retrieved |
|---|---|---|---|---|---|
| H100 SXM GPU — max thermal design power | 700 | 650–700 | W per GPU | [NVIDIA H100 Tensor Core GPU Datasheet](https://resources.nvidia.com/en-us-gpu-resources/h100-datasheet-24306) | 2026-09-12 |
| B200 GPU — max TDP, air-cooled 8-GPU (HGX/DGX B200) config | 1,000 | 900–1,000 | W per GPU | [NVIDIA Blackwell Datasheet](https://nor-tech.com/wp-content/uploads/2026/03/blackwell-datasheet-B200.pdf) | 2026-09-12 |
| DGX H100 — 8-GPU node max system power | 10.2 | 9.5–10.2 | kW per node | [NVIDIA DGX H100 Datasheet](https://xenon.com.au/wp-content/uploads/2023/11/XENON-NVIDIA-DGX-H100-datasheet.pdf) | 2026-09-12 |
| DGX B200 — 8-GPU node max system power | 14.3 | 13.5–14.5 | kW per node | [NVIDIA DGX B200 Datasheet](https://resources.nvidia.com/en-us-dgx-systems/dgx-b200-datasheet) | 2026-09-12 |
| Server node overhead multiplier — H100 generation | 1.82 | 1.7–1.9 | × (node W/GPU ÷ GPU-only W) | Derived from the DGX H100 datasheet above; cross-validated by [SemiAnalysis, "100,000 H100 Clusters"](https://newsletter.semianalysis.com/p/100000-h100-clusters-power-network) | 2026-09-12 |
| Server node overhead multiplier — B200 generation | 1.79 | 1.7–1.9 | × (node W/GPU ÷ GPU-only W) | Derived from the DGX B200 datasheet above (no independent third-party cross-check found yet) | 2026-09-12 |
| Cluster-level overhead beyond one node (switches, storage, optics) | 10 | 5–15 | % added to node-level IT power at multi-thousand-GPU scale | [SemiAnalysis, "100,000 H100 Clusters"](https://newsletter.semianalysis.com/p/100000-h100-clusters-power-network) | 2026-09-12 |
| PUE — hyperscale-class facilities | 1.15 | 1.09–1.2 | ratio, facility power ÷ IT power | [Google Data Centers efficiency page](https://datacenters.google/efficiency/) (1.09 fleet TTM) + Uptime Institute commentary (hyperscalers "1.2 or lower at some sites") | 2026-09-12 |
| PUE — industry average, all data centers | 1.54 | 1.44–1.6 | ratio, facility power ÷ IT power | [Uptime Institute Global Data Center Survey 2025](https://datacenter.uptimeinstitute.com/rs/711-RIA-145/images/2025.Annual.Survey.Report.pdf) (n=681) | 2026-09-12 |
| **DERIVED** — GPUs per MW, H100, grid-interconnection basis (post-PUE) | 575 | 430–720 | GPUs per MW of interconnected grid capacity | Derived (this project); see arithmetic below | 2026-09-12 |
| **DERIVED** — GPUs per MW, H100, IT-critical-power basis (pre-PUE) | 720 | 660–785 | GPUs per MW of IT-critical power (not grid power) | Derived (this project); see arithmetic below | 2026-09-12 |
| **DERIVED** — GPUs per MW, B200, grid-interconnection basis (post-PUE) | 425 | 330–515 | GPUs per MW of interconnected grid capacity | Derived (this project); cross-checked against a public GB200 NVL72 estimate at [techplustrends.com](https://techplustrends.com/1gw-data-center-power-consumption-guide/) (≈468 GPUs/MW, inside range) | 2026-09-12 |
| **ASSUMPTION** — conservative real-world provisioned density (sanity-check floor only, not a primary figure) | 215 | 180–250 | GPUs per MW | Industry capacity-planning commentary, [Aterio](https://www.aterio.io/blog/how-much-power-would-a-data-center-with-30-000-gpus-consume-in-a-year) | 2026-09-12 |

**Derivation — GPUs per MW.**

1. *GPU → node:* `node W/GPU = GPU TDP × overhead multiplier`.
   H100: 700 × 1.82 = **1,275 W/GPU** (matches SemiAnalysis's independently published ~575 W/GPU of CPU/NIC/PSU overhead on top of the 700 W GPU — same number, different method).
   B200: 1,000 × 1.79 = **1,787.5 W/GPU**.
2. *Node → cluster (optional, +10%):* H100 → 1,402.5 W/GPU; B200 → 1,966.25 W/GPU.
3. *IT power → grid power, dividing by PUE:* `GPUs/MW = 1,000,000 W ÷ (W-per-GPU × PUE)`.
   - H100, hyperscale PUE 1.09, node-only: 1,000,000 ÷ (1,275 × 1.09) = **719**
   - H100, PUE 1.2, cluster-inclusive: 1,000,000 ÷ (1,402.5 × 1.2) = **594**
   - H100, PUE 1.54, cluster-inclusive: 1,000,000 ÷ (1,402.5 × 1.54) = **463**
   - H100, PUE 1.54, node-only: 1,000,000 ÷ (1,275 × 1.54) = **509**
   → range **430–720**, central ≈**575**.
   - B200 (1,787.5 / 1,966.25 W/GPU): PUE 1.09 node-only = 513; PUE 1.09 cluster = 467; PUE 1.54 node-only = 363; PUE 1.54 cluster = 331 → range **330–515**, central ≈**425**.
4. *Pre-PUE (IT-power) basis, no facility overhead:* H100 node-only 1,000,000/1,275 = **784**; cluster-inclusive 1,000,000/1,402.5 = **713**; SemiAnalysis's own 100,000 GPUs / 150 MW critical-IT-power ratio implies **667** → range **660–785**. This band lines up almost exactly with `docs/DATA_NEEDED.md`'s own stated anchor ("rough anchor is 700 to 850 GPUs per MW all in") — the strong implication is that public anchor describes **IT-critical power**, not grid-metered power.

**Recommendation:** default the UI to the post-PUE "grid MW" row (575, range 430–720) since SPP CHILLS capacity is metered at the interconnection point, not the IT room. Expose the pre-PUE IT-power figure (720, range 660–785) as a labeled alternate toggle, and treat H100 vs. B200 as a generation selector rather than one blended constant — moving to B200 lowers GPUs/MW by roughly 25–35% at the grid level because each Blackwell GPU draws ~43% more power while overhead ratios stay flat.

---

## 2. Cost of interrupted compute

Converts lost GPU-hours (and lost availability) into dollars.

| Name | Value | Range | Unit | Source | Date retrieved |
|---|---|---|---|---|---|
| H100 rental — CoreWeave (HGX/SXM 8-GPU node, on-demand list) | 6.16 | 4.25–6.16 (PCIe to SXM) | USD/GPU-hour | [CoreWeave Cloud Pricing](https://www.coreweave.com/pricing) | 2026-09-12 |
| H100 rental — Lambda Labs (SXM, on-demand) | 3.99 | 3.29–4.29 | USD/GPU-hour | [Lambda Cloud GPU Pricing](https://lambda.ai/service/gpu-cloud/pricing) | 2026-09-12 |
| H100 rental — RunPod (Community–Secure, PCIe/SXM) | 2.89 | 1.99–3.49 | USD/GPU-hour | [RunPod Pricing](https://www.runpod.io/pricing) | 2026-09-12 |
| H100 rental — Vast.ai (peer-to-peer spot marketplace) | 2.00 | 0.90–2.53 | USD/GPU-hour | [Vast.ai pricing](https://vast.ai/pricing/gpu/H100-NVL) + third-party trackers (JS-rendered page, corroborated via aggregators) | 2026-09-12 |
| **H100 rental — cross-provider composite (recommended `gpu_hour_value_usd` default)** | 3.00 | 1.49–6.16 | USD/GPU-hour | Compiled from CoreWeave, Lambda, RunPod, Vast.ai above | 2026-09-12 |
| B200 rental — cross-provider composite | 7.00 | 5.98–8.60 | USD/GPU-hour | [RunPod](https://www.runpod.io/pricing) $5.98–6.79, [Lambda](https://lambda.ai/service/gpu-cloud/pricing) $6.69–6.99, [CoreWeave](https://www.coreweave.com/pricing) $8.60 | 2026-09-12 |
| Silicon Data H100/B200 Rental Index (CME compute futures reference) | N/A — no published index level yet (pre-launch) | expected to track $1.49–6.16 (H100) / $5.98–8.60 (B200) once live on NYMEX Oct 5, 2026 | USD/GPU-hour | [CME Group press release](https://www.cmegroup.com/media-room/press-releases/2026/8/11/cme_group_and_silicondatatolaunchcomputefuturesonoctober5tounloc.html) | 2026-09-12 |
| Industrial electricity price — Kansas | 8.21 | 7.6–8.6 | ¢/kWh | [EIA Electric Power Monthly, Table 5.6.A](https://www.eia.gov/electricity/monthly/epm_table_grapher.php?t=epmt_5_6_a) (June 2026, preliminary) | 2026-09-12 |
| Industrial electricity price — Oklahoma | 7.37 | 6.5–7.5 | ¢/kWh | [EIA Electric Power Monthly, Table 5.6.A](https://www.eia.gov/electricity/monthly/epm_table_grapher.php?t=epmt_5_6_a) (June 2026, preliminary) | 2026-09-12 |
| Industrial electricity price — Nebraska | 8.30 | 7.0–9.7 | ¢/kWh | [EIA Electric Power Monthly, Table 5.6.A](https://www.eia.gov/electricity/monthly/epm_table_grapher.php?t=epmt_5_6_a) (see discrepancy note) | 2026-09-12 |
| Cluster restart/checkpoint-resume delay — hyperscale-grade infra | 6.5 | 4–10 | minutes per interruption | [TrainMover (arXiv preprint)](https://arxiv.org/abs/2412.12636), measured on an 8,192-GPU production job | 2026-09-12 |
| **ASSUMPTION** — cluster restart delay, general/less-optimized infra (no hyperscale-grade fast-failover) | 35 | 15–60 | minutes per interruption | No defensible primary source located; `docs/DATA_NEEDED.md`'s own suggested fallback range | 2026-09-12 |
| Checkpoint write-interval steady-state overhead | 0.3 | 0.1–5 | % of training time | NVIDIA guidance, cited via [VAST Data blog](https://arxiv.org/pdf/2407.20018) and an arXiv survey (moderately sourced — no primary NVIDIA URL located for the exact figure) | 2026-09-12 |
| Context only — Meta Llama 3 405B training interruption profile (no per-event minutes reported) | >90% effective training time; 466 interruptions over 54 days (47 planned, 419 unplanned, ~78% hardware-related) | n/a — directly reported, not a range | % / count | [Meta, "The Llama 3 Herd of Models," §3.3.4](https://arxiv.org/abs/2407.21783) | 2026-09-12 |
| SLA credit structure — AWS EC2 | 10% credit (uptime 99.0–99.99%) | 10% / 30% / 100% across three uptime tiers (region SLO 99.99%, instance SLO 99.5%) | % bill credit | [Amazon Compute SLA](https://aws.amazon.com/compute/sla/) | 2026-09-12 |
| SLA credit structure — Azure Virtual Machines | 10% credit (uptime 99.0–SLO) | 10% / 25% / 100% across three uptime tiers (SLO 99.9/99.95/99.99% by config) | % bill credit | [Azure VM SLA](https://azure.microsoft.com/en-us/support/legal/sla/virtual-machines/v1_9/) (moderate confidence — tier % corroborated via secondary sources, not a verbatim legal-doc fetch) | 2026-09-12 |
| SLA credit structure — Google Compute Engine | 10% credit (uptime 99.0–99.99%) | 10% / 25% tiers confirmed; behavior below 95% uptime uncertain (older doc shows a 50%-of-monthly-charge cap) | % bill credit | [Compute Engine SLA](https://cloud.google.com/compute/sla) | 2026-09-12 |

**Note on the Nebraska discrepancy.** The EIA Electric Power Monthly figure (8.58–9.64 ¢/kWh across June 2025/2026) runs meaningfully higher than Nebraska's own state energy office citation of the EIA's annual State Energy Data System (~7.0–7.6 ¢/kWh for 2023, [dwee.nebraska.gov](https://dwee.nebraska.gov/state-energy-information/energy-statistics/pricesrates/energy-prices-nebraskas-industrial-sector)). Nebraska's industrial sector has few large reporting utilities, which makes the monthly EPM series more volatile than the annual SEDS series. The 7.0–9.7 range and 8.30 default reflect that genuine cross-source disagreement honestly rather than picking a side.

**Note on rental-price defaults.** $3.00/GPU-hr (H100) and $7.00/GPU-hr (B200) are picked from the middle of the "typical mainstream secure/dedicated on-demand" band, not the midpoint of the full low-to-high spread — the spread's low end (RunPod Community, Vast.ai unverified hosts) trades reliability for price in a way that is itself a real cost this model should let a user dial in via the range, not the default.

---

## 3. Value of connecting early

The other side of the ledger: what a flexible (CHILLS) connection buys you versus waiting for firm interconnection.

| Name | Value | Range | Unit | Source | Date retrieved |
|---|---|---|---|---|---|
| Firm interconnection queue duration — national (request → commercial operation) | 61 (5.1 yr) | 43–85 months (3.6–7.1 yr), ~25th–75th percentile for 2025 completions | months | [LBNL, "Queued Up: 2026 Edition"](https://emp.lbl.gov/sites/default/files/2026-06/Queued%20Up%202026%20Edition.pdf) | 2026-09-12 |
| Firm interconnection queue duration — SPP-specific | 50 (4.2 yr) | 37–75 months (3.1–6.3 yr), 25th–75th percentile, 2018–2025 cohort | months | [LBNL, "Queued Up: 2026 Edition,"](https://emp.lbl.gov/sites/default/files/2026-06/Queued%20Up%202026%20Edition.pdf) slide 52 | 2026-09-12 |
| SPP's own stated firm network-upgrade timeline | 6+ (no stated ceiling) | 6–7+ years, bounded in practice by CHILLS' own 7-year term cap | years | [FERC, 195 FERC ¶ 61,196](https://spp.org/documents/76880/20260605_order%20-%20revisions%20to%20add%20the%20conditional%20high%20impact%20large%20load%20service_er26-1323.pdf), ¶20 & n.20 | 2026-09-12 |
| Flexible (HILL/CHILLS) study-and-decision timeline | 90 | fixed target, not a modeled distribution | days | [SPP press release](https://www.spp.org/news-list/southwest-power-pool-board-approves-accelerated-pathway-for-large-load-connection/) | 2026-09-12 |
| **ASSUMPTION** — total time to energization under flexible/CHILLS (incl. application intake, credit review, any facilities work) | 12 | 6–18 | months | Not stated anywhere by SPP in sources checked; a defensible planning range | 2026-09-12 |
| CHILLS authorized service term (tariff hard limit) | 7 (statutory max) | 1–7 years, hard tariff limits — not a modeled distribution | years | [FERC, 195 FERC ¶ 61,196](https://spp.org/documents/76880/20260605_order%20-%20revisions%20to%20add%20the%20conditional%20high%20impact%20large%20load%20service_er26-1323.pdf), ¶9 | 2026-09-12 |
| Flexible vs. firm connection timeline — industry cross-check | 15 mo flexible / 6.5 yr firm | 12–18 mo flexible / 5–8 yr firm | months / years | [Camus Energy blog](https://www.camus.energy/blog/how-flexible-interconnections-can-help-data-centers-connect-faster-without-overloading-the-grid) — interested party (sells flexible-interconnection software), used as a cross-check only | 2026-09-12 |
| **DERIVED** — value-of-early-connection horizon, recommended `firm_wait_years` default | 4 | 3–7 | years | Derived (this project) from the rows above; see arithmetic below | 2026-09-12 |
| Realistic GPU-fleet utilization rate | 70 | 60–85 | % of calendar hours revenue-earning | [Park Place Technologies](https://www.parkplacetechnologies.com/blog/data-center-gpu-deployment-strategy-why-it-matters-more-than-ever/); SemiAnalysis GPU-cluster cost analysis | 2026-09-12 |
| **DERIVED** — historical IT-basis revenue illustration (not the selected `early_margin_usd_per_mw_year`) | 19,000,000 | 7,400,000–38,000,000 | USD per MW per year, **gross revenue, not margin** | Derived (this project); see arithmetic below | 2026-09-12 |
| Capex per MW, AI-optimized fully-built data center (optional/nice-to-have) | 28,000,000 | 20,000,000–37,000,000 | USD per MW | [JLL, "2026 Market Outlook for Global Data Centers"](https://www.jll.com/en-us/insights/market-outlook/data-center-outlook) | 2026-09-12 |
| Capex per MW, shell-and-core only (for reference; not this product's scope) | 11,000,000 | 10,700,000–11,300,000 | USD per MW | [JLL, "2026 Market Outlook for Global Data Centers"](https://www.jll.com/en-us/insights/market-outlook/data-center-outlook) | 2026-09-12 |

**Derivation — value-of-early-connection horizon (`firm_wait_years`).** Gap = firm wait − flexible wait.
- SPP-specific: 4.2 yr (LBNL median) − ~1 yr (flexible/CHILLS total assumption) ≈ **3.2 yr**
- National: 5.1 yr (LBNL median) − ~1 yr ≈ **4.1 yr**
- SPP's own "6+ year" claim − ~1 yr ≈ **5+ yr**, trending toward the CHILLS 7-year statutory cap (which SPP designed specifically to bridge this exact gap)
→ central estimate **4 years**, range **3–7 years** (upper bound set by the CHILLS term limit itself). This is a synthesized range across sources 1–7 above, not one directly reported "gap" statistic — flag that provenance to anyone auditing the number.

**Derivation — revenue per MW per year.** `Revenue/MW/yr = (GPUs per MW) × (rental $/GPU-hr) × (utilization) × (8,760 hr/yr)`.
- LOW: 700 GPUs/MW × $2.00/hr × 0.60 × 8,760 hr = **$7,358,400**
- MID: 775 GPUs/MW × $4.00/hr × 0.70 × 8,760 hr = **$19,009,200**
- HIGH: 850 GPUs/MW × $6.00/hr × 0.85 × 8,760 hr = **$37,974,600**

**Internal-consistency flag:** this derivation deliberately reused `docs/DATA_NEEDED.md`'s own stated 700–850 GPUs/MW anchor (the pre-PUE, IT-power-basis figure — see §1) rather than this document's own post-PUE grid-basis figure (575, range 430–720), because Category C's brief only asked to combine the MW→GPU anchor with rental price and utilization, not re-derive it. If recomputed with the grid-basis GPUs/MW instead — 575 central, 430–720 range — the result is materially lower: LOW ≈ 430 × $1.49 × 0.60 × 8,760 ≈ **$3.37M**, MID ≈ 575 × $3.00 × 0.70 × 8,760 ≈ **$10.58M**, HIGH ≈ 720 × $6.16 × 0.85 × 8,760 ≈ **$33.0M**. Both derivations are legitimate depending on which GPUs/MW basis the team standardizes on — pick one and use it consistently across the exposure calculation and the revenue calculation, since right now they are not required to match.

**Naming caveat:** every revenue derivation above is **gross revenue**: power,
staffing, financing, and capex amortization have not been deducted. None belongs
directly in `early_margin_usd_per_mw_year`. Tharun subsequently selected an assumed
operating-margin conversion below; there is still no reviewed cost model proving
that result is attainable operating income or net profit.

---

## What changes in the app

**Integrated 2026-09-12:** the selected values from Tharun's `59cbdda` are now in the
marked JSON block consumed by `api/economics.py`. The old branch-local
`ECONOMICS_DEFAULTS` constant is superseded by this file. The canonical
`POST /api/estimate` body is unchanged: each economics number inherits
`economics.source`, whose reference lists all input values and references.
`source_type` remains `"assumption"`; the aggregate retains `mock://` while the
operating margin or decision tolerance is unreviewed, or exposure is a placeholder.
Sourcing an input does not promote exposure, confidence, or tariff fixtures to real
data. Local frontend overrides remain assumptions and do not change this server file.

| Machine JSON field (frontend field) | Old mock value | Selected value | Recorded range | Source (§ in this doc) |
|---|---|---|---|---|
| `gpus_per_mw` (`gpu_per_mw`) | 1000 | **575** GPUs/MW (H100, grid-interconnection basis) | 430–720 | §1, derived planning assumption |
| `gpu_rental_price_usd_per_hour` (`gpu_hour_value_usd`) | 2 | **3.00** USD/GPU-hr (H100 composite assumption) | 1.49–6.16 | §2, cross-provider selection |
| `early_connection_years` (`firm_wait_years`) | 3 | **4** years (assumed firm-vs-flexible gap) | 3–7 | §3, synthesized planning scenario |
| `early_margin_usd_per_mw_year` | 500,000 | **317,000** USD/MW-year — unverified operating-margin assumption | 0–1,000,000, retained placeholder range | §3 gross scenario × Tharun's assumed 3% |
| `industrial_electricity_price_usd_per_mwh` | 50 | **82.1** USD/MWh, Kansas reference only | 76–86 | §2, 8.21 cents/kWh × 10; informational only |
| `close_call_fraction` | 0.05 | **0.05**, unreviewed decision tolerance | 0–0.1 | Original explicit placeholder decision policy |

**Gross revenue versus operating margin — explicit accounting basis.** Tharun's
`59cbdda` selected the grid-interconnection basis and an assumed operating-margin
conversion. The arithmetic is:

- Scenario gross revenue: `575 × $3 × 0.70 × 8,760 = $10,577,700/MW-year`.
- Tharun's **unverified 3% operating-margin assumption**:
  `$10,577,700 × 0.03 = $317,331`, rounded to the selected **$317,000/MW-year**.
- API early value: `min(4, term_years) × load_mw × $317,000`.
  The default 100 MW, seven-year contract therefore uses $126,800,000.

The first number is modeled gross revenue under assumed density, rental price, and
utilization; it is not observed revenue from a real site. The second is an assumed
operating-margin proxy after a blanket haircut, **not verified net margin or net
profit**. The research notes supply no supporting cost breakdown for the 3% choice.
It remains explicitly unreviewed and tagged `mock://`; this integration adopts
Tharun's stated scenario without asserting that the haircut is economically valid.
An attractive decision flip is not evidence for choosing a margin. Review the
actual revenue and cost assumptions together before presenting an investment case.

Interruption cost currently values lost GPU-hours at the **gross rental-price
proxy**; it does not estimate avoided power expense, restart losses, SLA credits, or
net lost profit. Early benefit uses the **assumed operating-margin proxy** above.
These are different accounting bases and must not be described as a complete net
present-value or profitability calculation. Electricity is informational and is not
deducted again from the blanket margin. The Kansas reference is not a site tariff or
an automatically selected Oklahoma/Nebraska rate.

Additional fields the model does **not** currently apply (`api/economics.py` has no restart-minutes or SLA-credit term), retained as research notes per `docs/DATA_NEEDED.md` §B/§D:

| Proposed field | Suggested default | Suggested range | Source (§ in this doc) |
|---|---|---|---|
| `restart_overhead_minutes` (hyperscale-grade infra assumption) | 6.5 min | 4–10 min | §2, "Cluster restart/checkpoint-resume delay — hyperscale-grade infra" |
| `restart_overhead_minutes` (general/typical-shop assumption toggle) | **ASSUMPTION** 35 min | 15–60 min | §2, "ASSUMPTION — cluster restart delay, general/less-optimized infra" |
| `checkpoint_overhead_pct` | 0.3% | 0.1–5% | §2, "Checkpoint write-interval steady-state overhead" |
| `utilization_pct` (currently baked into the revenue derivation, not exposed) | 70% | 60–85% | §3, "Realistic GPU-fleet utilization rate" |
| SLA credit lookup (not a scalar — a tier table) | n/a | AWS 10/30/100%, Azure 10/25/100%, GCP 10/25%+ | §2, SLA rows |

## Integration note

The API contract keeps its existing field names and formulas. The machine block
supersedes prose examples as runtime configuration. Defaults selected from cited
research use `source_type: "assumption"`, including the Kansas electricity figure
recorded by Tharun; integration has not independently verified the external tables.
The references attribute those choices to the immutable teammate commit. Unreviewed
margin and tolerance retain placeholder provenance. Status is therefore `mixed`.
No `real`, `derived`, or compound provenance enum is introduced.

## Machine-readable assumptions

Keep one marked JSON block. Values are finite, with nonnegative prices, margin,
years, and ranges; positive GPU density; and tolerance in `[0, 1)`. Each value must
lie within its recorded range. A placeholder entry must retain `source_type:
"assumption"`, an explicit `mock://` placeholder reference, and null source URL and
retrieval date. Referenced entries require an HTTPS source URL and retrieval date;
they use `source_type: "assumption"` for chosen defaults and derived scenarios, or
`"data"` for directly supported observations. A URL alone does not establish review
or convert an assumption into a fact. Top-level status is `placeholder`, `mixed`, or
`sourced` according to whether all, some, or none of the entries remain placeholders.
The derived API economics block still carries `source_type: "assumption"` because
the calculation uses the user's scenario assumptions; its reference records its
input values and sources. A real exposure model does not make placeholder economics
real.

<!-- headroom:economics-assumptions:v1 -->
```json
{
  "schema_version": 1,
  "status": "mixed",
  "gpu_rental_price_usd_per_hour": {
    "value": 3,
    "source_type": "assumption",
    "ref": "docs/ASSUMPTIONS.md#2-cost-of-interrupted-compute; Tharun 59cbdda; assumed H100 cross-provider rental-price default, not a site quote",
    "unit": "USD/GPU-hour",
    "source_url": "https://github.com/alphabyalex/East-v-west-hackathon-2026/blob/59cbdda/docs/ASSUMPTIONS.md#2-cost-of-interrupted-compute",
    "retrieved_on": "2026-09-12",
    "low": 1.49,
    "high": 6.16
  },
  "industrial_electricity_price_usd_per_mwh": {
    "value": 82.1,
    "source_type": "assumption",
    "ref": "docs/ASSUMPTIONS.md#2-cost-of-interrupted-compute; Tharun 59cbdda records EIA June 2026 Kansas industrial 8.21 cents/kWh x 10 = 82.1 USD/MWh; not independently verified; informational Kansas reference, not a site tariff",
    "unit": "USD/MWh",
    "source_url": "https://github.com/alphabyalex/East-v-west-hackathon-2026/blob/59cbdda/docs/ASSUMPTIONS.md#2-cost-of-interrupted-compute",
    "retrieved_on": "2026-09-12",
    "low": 76,
    "high": 86
  },
  "gpus_per_mw": {
    "value": 575,
    "source_type": "assumption",
    "ref": "docs/ASSUMPTIONS.md#1-hardware--power-conversion; Tharun 59cbdda; assumed H100 density per grid-interconnection MW, post-PUE, not IT MW or measured site capacity",
    "unit": "GPU/MW",
    "source_url": "https://github.com/alphabyalex/East-v-west-hackathon-2026/blob/59cbdda/docs/ASSUMPTIONS.md#1-hardware--power-conversion",
    "retrieved_on": "2026-09-12",
    "low": 430,
    "high": 720
  },
  "early_connection_years": {
    "value": 4,
    "source_type": "assumption",
    "ref": "docs/ASSUMPTIONS.md#3-value-of-connecting-early; Tharun 59cbdda; assumed firm-minus-flexible connection gap, not a guaranteed SPP energization schedule",
    "unit": "year",
    "source_url": "https://github.com/alphabyalex/East-v-west-hackathon-2026/blob/59cbdda/docs/ASSUMPTIONS.md#3-value-of-connecting-early",
    "retrieved_on": "2026-09-12",
    "low": 3,
    "high": 7
  },
  "early_margin_usd_per_mw_year": {
    "value": 317000,
    "source_type": "assumption",
    "ref": "mock://economics-placeholder/docs/ASSUMPTIONS.md#what-changes-in-the-app; placeholder, Tharun 59cbdda unverified 3% operating-margin assumption x 10577700 USD/MW-year scenario gross revenue = 317331, rounded to 317000; not verified net margin",
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
