# Additional SPP history and a common-window model comparison

Run date: 2026-09-13. Machine-readable results, exact source URLs, retrieval dates,
input/model hashes, coverage, and score components are in
[model-evaluation-2025.json](model-evaluation-2025.json).

The updated **high-demand proxy** model has 6.16% lower Brier error on the same
8,729 later hours. This is a modest observed gain: its paired seven-day bootstrap
interval includes zero improvement. The result does not establish better emergency
prediction, site interruption prediction, or calibrated annual tails.

## New observations

Downloaded and cached all twelve 2025 monthly load CSVs from
[SPP's hourly-load archive](https://portal.spp.org/pages/hourly-load), plus its
[2025 generation archive](https://portal.spp.org/file-browser-api/download/generation-mix-historical?path=/GenMix_2025.csv).
The existing Amarillo ERA5 cache supplies temperature. The location association
remains an explicit assumption; reanalysis is retrospective, not verified as
available at each historical prediction time.

| Prepared dataset | Before: 2019–2024 | After: 2019–2025 |
|---|---:|---:|
| Hourly rows | 52,608 | 61,362 |
| Observed system-load hours | 52,541 | 61,294 |
| Temperature hours | 52,608 | 61,362 |
| Observed wind hours | 51,579 | 60,259 |
| Observed solar hours | 51,579 | 60,259 |
| Usable labeled feature rows | 52,250 | 60,979 |

Load normalization requires all seventeen component areas. Missing/conflicting
observations stay unknown. Generation requires twelve distinct five-minute samples
per hour. The final six hours of the Central operating year fall in 2026 UTC and
are explicitly excluded to preserve the current pre-2026 SPP_SYSTEM scope.

## Equal evaluation conditions

Both models use the same target: load at least **42,064.0228 MW**, the frozen 95th
percentile from the original pre-2022 reference period. It is an assumed high-demand
stress proxy, not measured emergency evidence. Both fits use fifteen members,
100 trees, seed 2026, chronological train/calibration/test partitions and a 24-hour
embargo. Calibration logic is unchanged.

The common comparison spans **2025-01-01 06:00 UTC to 2026-01-01 00:00 UTC**, end
exclusive. It starts after the earlier run's entire saved test period. Neither
model trained or calibrated on these hours. Of 8,754 possible hours, 8,729 have
usable observations and lagged features; the same 25 hours are excluded from both
scores. There are 874 positive proxy hours. No threshold or hyperparameter search
was performed on this comparison.

| Metric | Earlier run | Updated run |
|---|---:|---:|
| Brier score (lower is better) | 0.0039015551 | 0.0036610444 |
| Log loss (lower is better) | 0.0141813806 | 0.0131984266 |
| Average precision | 0.99741120 | 0.99766610 |
| Mean predicted probability | 0.09836018 | 0.09950440 |
| Observed positive fraction | 0.10012602 | 0.10012602 |

The candidate-minus-earlier Brier change is **-0.00024051**, with a descriptive
95% paired seven-day block-bootstrap interval of **[-0.00077071, +0.00011890]**
(2,000 replicates, seed 2026, 53 blocks). The interval crosses zero.

The updated run has 12,172 held-out hours spanning August 2024–December 2025 and
passes API provenance and minimum annual-reference checks. Its classifier
agreement score is 0.995374, but median same-location neighbor support remains
zero: numeric confidence is **0.0**, level **Low**. More data does not justify
inflating confidence or loosening the precedent rule.

The run and seven-year simulation are saved under
`data/processed/workbench/runs/history_2025_20260913`. The original 21-zone
published bundle uses a different target and is not replaced by this system-level
proxy candidate. Model binaries and raw caches remain ignored by git; this report,
source hashes, and the preparation/comparison code are committed.

## Reproduce from the repository root

Use the existing Python environment with `requirements-ml.txt`. Choose fresh
output/run directories if these outputs already exist. Cached external documents
are reused. The 2025 monthly reader validates each source hash and month; annual
coverage and prepared-cache lineage are checked before joining weather.

```powershell
python -m pipeline.history --start-year 2019 --end-year 2025 --area "Amarillo, TX" --out data/processed/ml_inputs/spp_2019_2025_amarillo.parquet
python -m pipeline.generation --hourly data/processed/ml_inputs/spp_2019_2025_amarillo.parquet --out data/processed/ml_inputs/spp_2019_2025_multifactor.parquet --start-year 2019 --end-year 2025
python -m pipeline.workflow prepare-demand-proxy --hourly data/processed/ml_inputs/spp_2019_2025_multifactor.parquet --out data/processed/ml_inputs/spp_2019_2025_demand_proxy.parquet --policy data/processed/ml_inputs/spp_2019_2025_demand_proxy.policy.json --reference-end 2022-01-01T00:00:00Z
python -m pipeline.workflow train --hourly data/processed/ml_inputs/spp_2019_2025_demand_proxy.parquet --policy data/processed/ml_inputs/spp_2019_2025_demand_proxy.policy.json --run-dir data/processed/workbench/runs/history_2025_20260913
python -m pipeline.workflow simulate --run-dir data/processed/workbench/runs/history_2025_20260913 --simulations 2000 --years 7
python -m pipeline.backtest --run-dir data/processed/workbench/runs/confidence_v2_20260913 --run-dir data/processed/workbench/runs/history_2025_20260913 --hourly data/processed/ml_inputs/spp_2019_2025_multifactor.parquet --start-utc 2025-01-01T06:00:00Z --end-exclusive-utc 2026-01-01T00:00:00Z --out data/processed/workbench/evaluations/history_2025_20260913
```

The comparison command accepts trusted local model bundles only. It rejects
different target thresholds/reference periods, fitting/calibration overlap,
existing labels, model/card mismatches, and invalid predictions. It writes
per-hour probabilities and a report; it never trains or promotes a model.

Validation: all 1,753 Python tests and 30 subtests pass, including 24 new coverage,
cache-lineage, and comparison regressions. The actual training, simulation, API
artifact checks, and network-disabled cache reuse also passed. Existing dependency
deprecation warnings remain.
