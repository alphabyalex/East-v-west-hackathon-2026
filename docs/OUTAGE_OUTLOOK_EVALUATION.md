# Generation outage outlook evaluation

The added outage predictors did **not** improve the existing demand-proxy model.
The candidate remains a research artifact and was not promoted. The data importer,
validation, and comparison tooling are retained for further evaluation.

| Common 2025 evaluation | Existing model | Outage candidate |
|---|---:|---:|
| Scored hours | 8,729 | 8,729 |
| Positive hours | 874 | 874 |
| Brier score | 0.0036610444 | 0.0036697464 |
| Log loss | 0.0131984266 | 0.0132297689 |
| Average precision | 0.9976661007 | 0.9976407056 |

Brier error increased 0.238%. The paired candidate-minus-baseline change was
0.000008702, with a descriptive 95% seven-day block-bootstrap interval of
[-0.000007355, +0.000027059] (2,000 replicates, seed 2026). The interval includes
zero. This evaluation window was already examined in earlier research, so this
is an exploratory comparison rather than a fresh unseen test.

Both runs use the same 42,064.0228 MW threshold, pre-2022 reference, training and
calibration splits, and 15-member LightGBM configuration. The candidate adds 20
lagged/rolling features from total, gas, coal, and wind outage outlooks, increasing
the feature count from 44 to 64. No label or calibration policy changed.

## Data and quality

Collected **2,557 daily reports** for 2019–2025, containing **429,555 forecast
rows**. These are overlapping seven-day outlooks, not independent observed hours.
Annual ZIPs supplied 2019–2024; 365 individually cached daily CSVs supplied 2025.

The hourly join has **55,240 usable outlook records** out of 61,362 rows. There
are 6,098 hours whose selected reports have unreconciled fuel totals and 24 hours
before an eligible report exists. Those predictors remain unknown. Three source
reports also contain fewer than 168 forecast hours; the missing rows are recorded.
All 102 hourly buckets touched by the documented 2021, 2022, and 2024 emergency
intervals have usable outlooks.

The [SPP data guide and samples](https://www.spp.org/Documents/75871/SPP%20Markets%20Public%20Data%20Guide%20and%20Samples%20v35.zip)
describe a daily seven-day outlook containing submitted CROW outages and offered
OUTAGE commitment status. This includes planned outages and does not directly
measure forced outages or reserve shortages. Data comes from the
[SPP outage portal](https://portal.spp.org/pages/capacity-of-generation-on-outage).

The importer interprets Market Hour as UTC interval end, consistent with the
[gridstatus SPP reader](https://github.com/gridstatus/gridstatus/blob/main/gridstatus/spp.py).
It conservatively makes a report eligible at the start of the following Central
calendar day, then selects the latest eligible report containing the exact hour.
This date-based assumption does not establish historical publication or revision
times. Predictors are lagged again before training. Fuel-total differences above
0.1 MW quarantine the capacities; the original downloaded documents are preserved.

Source URLs, hashes, coverage, model identifiers, and complete comparison metrics
are saved in [outage-outlook-evaluation.json](outage-outlook-evaluation.json).
Raw caches and model binaries remain local under ignored data directories.

## Reproduce

Use fresh output directories if these files already exist. Prepare the original
seven-year inputs using [MODEL_EVALUATION_2025.md](MODEL_EVALUATION_2025.md).

```powershell
python -m pipeline.outages --hourly data/processed/ml_inputs/spp_2019_2025_multifactor.parquet --out data/processed/ml_inputs/spp_2019_2025_outlooks.parquet
python -m pipeline.workflow prepare-demand-proxy --hourly data/processed/ml_inputs/spp_2019_2025_outlooks.parquet --out data/processed/ml_inputs/spp_2019_2025_outlook_demand_proxy.parquet --policy data/processed/ml_inputs/spp_2019_2025_outlook_demand_proxy.policy.json --reference-end 2022-01-01T00:00:00Z
python -m pipeline.workflow train --hourly data/processed/ml_inputs/spp_2019_2025_outlook_demand_proxy.parquet --policy data/processed/ml_inputs/spp_2019_2025_outlook_demand_proxy.policy.json --run-dir data/processed/workbench/runs/outage_outlook_20260913
python -m pipeline.workflow simulate --run-dir data/processed/workbench/runs/outage_outlook_20260913 --simulations 2000 --years 7
python -m pipeline.backtest --run-dir data/processed/workbench/runs/history_2025_20260913 --run-dir data/processed/workbench/runs/outage_outlook_20260913 --hourly data/processed/ml_inputs/spp_2019_2025_outlooks.parquet --start-utc 2025-01-01T06:00:00Z --end-exclusive-utc 2026-01-01T00:00:00Z --out data/processed/workbench/evaluations/outage_outlook_20260913
```

Validation: **1,786 Python tests and 42 subtests passed**, with two existing
dependency warnings. Actual preparation, training, simulation, API provenance,
and minimum annual-reference checks passed. Candidate confidence remains **Low,
score 0.0**, because median same-location precedent is zero despite agreement
0.9953304. Passing artifact checks does not establish emergency or annual-tail
accuracy.
