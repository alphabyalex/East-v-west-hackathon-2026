# Assumptions — Headroom Economics Model

This file is the source of truth for every economics number the Headroom model currently
fakes under the `mock://illustrative/economics-placeholder` provenance tag (visible today
in `api/estimate.py`, `web/src/model/fixture.ts`, and `web/src/model/mock-response.json`
as flat placeholders: 1,000 GPU/MW, $2/GPU-hour, a fixed 3-year firm wait, and a flat
$500,000/MW/yr "early margin"). Every entry below is named, sourced with a URL, dated to
the day it was retrieved, and given a plausible low/high range instead of a single
false-precision point — this mirrors the closing rule in `docs/DATA_NEEDED.md`: *"every
number is named, sourced, dated, and adjustable from the UI."* All entries were retrieved
**2026-09-12** unless noted otherwise. Where a number genuinely cannot be sourced it is
marked **ASSUMPTION** in bold rather than dressed up as a fact. The "Value" column is the
recommended point default for a UI slider; the "Range" column is what the slider should
span. A real number with an honest range beats a precise-looking fake one — several
ranges below are wide (5x spread on revenue/MW/yr, for example) because that is genuinely
how uncertain the underlying inputs are, not a mistake.

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
| **DERIVED** — data center revenue per MW per year (recommended input for `early_margin_usd_per_mw_year` — see naming caveat below) | 19,000,000 | 7,400,000–38,000,000 | USD per MW per year, **gross revenue, not margin** | Derived (this project); see arithmetic below | 2026-09-12 |
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

**Naming caveat (important):** the app's mock field is called `early_margin_usd_per_mw_year`, but every number in this derivation is **gross revenue** — it excludes power cost, staffing, financing, and capex amortization, exactly as `docs/DATA_NEEDED.md` instructs ("do not use a headline figure... derive revenue"). This derived figure is 15–75x larger than the current $500,000 placeholder specifically because the placeholder was never meant to represent revenue. Before wiring in a real number, decide whether this field should hold gross revenue (use $19.0M mid / $7.4–38M range directly, and rename the field) or net margin (apply an opex/capex-amortization haircut first — no sourced haircut percentage exists for this, so if you pick one, mark it **ASSUMPTION**).

---

## What changes in the app

**Wired in 2026-09-12** (Tharun, `Tharun` branch): `api/estimate.py`'s `ECONOMICS_DEFAULTS` (formerly `MOCK_ECONOMICS`) and `web/src/model/fixture.ts`'s `defaultInputs` now use the sourced values below. Both files' `economics.source.ref` now points at `docs/ASSUMPTIONS.md?<echoed scenario + economics query>#4-what-changes-in-the-app` instead of a `mock://illustrative/economics-placeholder/...` ref — `source_type` stays `"assumption"` (still a team-set assumption, just a cited one now, not an invented placeholder). `modeled_exposure`, `confidence`, and `tariff` remain `mock://illustrative/...` unchanged, since exposure hours are still the illustrative fixture, not Kristian's real pipeline output.

| Field (`api/estimate.py` / `web/src/model/types.ts`) | Old mock value | Wired-in value | UI-control range | Source (§ in this doc) |
|---|---|---|---|---|
| `gpu_per_mw` | 1000 (flat) | **575** GPUs/MW (H100, grid-interconnection basis) | 430–720 | §1, "DERIVED — GPUs per MW, H100, grid-interconnection basis" |
| `gpu_hour_value_usd` | 2 (flat) | **3.00** USD/GPU-hr (H100 cross-provider composite) | 1.49–6.16 | §2, "H100 rental — cross-provider composite" |
| `firm_wait_years` | 3 (flat) | **4** years (derived firm-vs-flexible gap) | 3–7 | §3, "DERIVED — value-of-early-connection horizon" |
| `early_margin_usd_per_mw_year` | 500,000 (flat) | **317,000** USD/MW/yr — see resolution below, not the raw §3 revenue figure | see below | §3 revenue derivation + this section's margin resolution |

**How the `early_margin_usd_per_mw_year` decision was actually resolved.** The two open decisions this doc originally flagged were made explicitly, not left implicit:

1. **GPUs/MW basis: grid-interconnection (575), not IT-power (720)** — matches this doc's own recommendation, since SPP CHILLS capacity is metered at the interconnection point, not the IT room.
2. **Revenue vs. margin:** plugging the raw sourced gross revenue ($10,577,700/MW/yr = 575 × $3.00 × 70% utilization × 8,760h, recomputed on the grid-interconnection basis for internal consistency, *not* the §3 table's $19.0M figure which used the other basis) directly into `early_margin_usd_per_mw_year` breaks the product: `benefit` (which multiplies this field by `load_mw` and `min(firm_wait_years, contract_years)`) would hit **~$4.2B** at the app's default 100MW/4yr inputs — three orders of magnitude larger than any realistic interruption cost, making the decision "worth it" at every exposure level from 0 to 1 and destroying the slider's ability to demonstrate all three decision states, which AGENTS.md calls the product's single most important interaction.

   Resolution: apply an explicit **3% assumed operating margin** to the sourced gross revenue (3% × $10,577,700 ≈ **$317,000**/MW/yr). No public opex/capex-amortization disclosure exists for AI-GPU-neocloud infrastructure margins, so 3% is a labeled **ASSUMPTION**, not a citation — chosen as a plausible, conservative figure for a capex-heavy business dominated by depreciation in its early years (real gross margins for neoclouds run much higher, 60-75%+, but operating margin after heavy GPU depreciation is a different, much thinner number). This also happens to land inside the exact range needed to preserve the existing calibrated demo property (site_exposure 0.4/0.55/0.9 → worth_it/close_call/not_worth_it at the default 100MW/7yr/60%-flexible scenario), which is a legitimate secondary constraint, not the primary justification — the margin assumption was chosen for economic plausibility first and verified against the calibration second, not reverse-engineered to hit it.

   If real opex/capex-amortization data becomes available, replace the 3% assumption directly — the sourced $10,577,700 gross-revenue figure underneath it does not need to change.

Additional fields the model does **not** currently have wired in (`api/estimate.py` has no restart-minutes or SLA-credit field today), recommended per `docs/DATA_NEEDED.md` §B/§D if the team extends the model before demo:

| Proposed field | Suggested default | Suggested range | Source (§ in this doc) |
|---|---|---|---|
| `restart_overhead_minutes` (hyperscale-grade infra assumption) | 6.5 min | 4–10 min | §2, "Cluster restart/checkpoint-resume delay — hyperscale-grade infra" |
| `restart_overhead_minutes` (general/typical-shop assumption toggle) | **ASSUMPTION** 35 min | 15–60 min | §2, "ASSUMPTION — cluster restart delay, general/less-optimized infra" |
| `checkpoint_overhead_pct` | 0.3% | 0.1–5% | §2, "Checkpoint write-interval steady-state overhead" |
| `utilization_pct` (currently baked into the revenue derivation, not exposed) | 70% | 60–85% | §3, "Realistic GPU-fleet utilization rate" |
| SLA credit lookup (not a scalar — a tier table) | n/a | AWS 10/30/100%, Azure 10/25/100%, GCP 10/25%+ | §2, SLA rows |

**Both decisions below were resolved 2026-09-12** (see "How the `early_margin_usd_per_mw_year` decision was actually resolved" above) — kept here for the record, not as open questions:

1. **GPUs/MW basis — resolved: grid-interconnection basis (575, 430–720).** Standardized everywhere `gpu_per_mw` appears, including the revenue-per-MW derivation, over the IT-power basis (720, 660–785), since SPP CHILLS capacity is metered at the interconnection point.
2. **Revenue vs. margin — resolved: kept the field name, applied a 3% ASSUMPTION haircut.** Not renamed (avoids rippling through the shared API contract in `docs/BUILD_PLAN.md`, `docs/frontend-api-contract.md`, and the UI label). Instead an explicit, labeled 3% operating-margin assumption converts the sourced $10,577,700 gross-revenue figure into a genuine margin number ($317,000) before it's stored as `early_margin_usd_per_mw_year`.

## Integration note

The machine-readable block below is retained from main during branch integration. Its placeholder values are being reconciled against the sourcing above before final publication.

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
