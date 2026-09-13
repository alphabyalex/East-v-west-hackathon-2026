# Multi-factor exposure model

Updated September 13, 2026 UTC. A real-data research model is trained and available at **http://127.0.0.1:8765**. Select `spp_multifactor_demand_20260913` under saved runs. The workspace shows expected exposure, annual scenarios, highest scored historical hours, confidence and downloadable artifacts.

## Target and assumptions

The target is a **high-demand stress proxy**, including non-emergency hours: aggregate SPP load at or above **42,064.0228 MW**. This is the 95th percentile of observed reference-period load from January 2019 through December 2021. The percentile is an explicit assumption, not a tariff trigger or measured capacity limit. It is frozen before calibration/testing.

This target is narrower than network stress generally. Local transmission constraints and insufficient reserves can occur at lower demand, and high demand can be served without interruption. The emergency catalog remains independent evidence. Actual site cutoffs require local operating evidence or a separate site assumption.

## Factors used together

| Factor | Data and predictors |
|---|---|
| System load | 52,541 known hours out of 52,608 slots across the 2019–2024 archives; level, prior-day patterns and demand ramps. |
| Area temperature | 52,608 Amarillo ERA5 observations; recent values, extremes, changes and temperature × load. Amarillo remains the example area. |
| Wind and solar generation | 51,579 complete hourly observations each; recent output and changes. |
| Net demand | Load minus observed wind and solar; prior-hour value and ramp. Not available reserve capacity. |
| Calendar | Hour, season and weekend indicators. |

Sources: [SPP hourly load](https://portal.spp.org/pages/hourly-load), [SPP generation mix](https://portal.spp.org/pages/generation-mix-historical), and [Open-Meteo historical ERA5 weather](https://open-meteo.com/en/docs/historical-weather-api). Cached parquet and JSON manifests preserve retrieval times and hashes. Generation observations use left-closed UTC bins with 12 distinct valid five-minute observations required; incomplete/conflicting hours remain missing. Source load uses the existing hour-ending conversion.

All non-calendar predictors use observations before the scored hour. The trees learn relationships rather than applying a fixed hot-weather cutoff probability. Historical reanalysis is not an as-issued forecast archive; source timestamps alone do not verify real-time availability. A city's weather is not a complete regional weather representation.

Available/required reserves, transmission constraints, generation outages, net imports and prices are **absent from this fitted run**. Their input columns remain supported, but values and effects were not invented. Wind/solar are actual grid generation, not inferred from city weather.

## Validation and hours

Fifteen LightGBM members train on 31,350 usable hours, calibrate on a later 10,426, and test on the final 10,426. Adjacent splits have a 24-hour embargo. The test spans October 21, 2023 17:00 UTC to January 1, 2025 06:00 UTC (end exclusive), with 75 unscored hours inside that span due to missing labels/load history.

| Held-out result | Value |
|---|---:|
| Expected exposure: sum of hourly probabilities | 819.39 hours |
| Observed hours meeting the demand proxy | 835 hours |
| Model Brier score | 0.003016 |
| Training-prevalence baseline Brier | 0.074131 |
| Previous-hour proxy-status persistence Brier | 0.018415 |

These metrics evaluate the demand proxy, not physical overload or site interruptions. Expected hours are not a count of probabilities above 50%. Member min/max expected hours measure model variation, not outcome percentiles. Confidence is **Low**: few close examples were found by the density check, and annual tails lack independent annual validation.

The existing seasonal simulator produced 2,000 seven-year trials using complete seven-day blocks of held-out history. It assumes future seasons resemble that history; no load growth, climate change or CHILLS adoption forecast is included.

| First simulated year | High-demand proxy hours/year |
|---|---:|
| P50 | 834.00 |
| P90 | 950.10 |
| P99 | 1,033.01 |

Seven-year total P50/P90/P99: **5,876.5 / 6,161.1 / 6,387.0 hours**. These are quantiles of each trial's joint total, not sums of yearly quantiles. They are experimental system-exposure scenarios, not verified cutoff hours or a future outage schedule.

The visible **Assumed site exposure** slider multiplies system results downstream. For illustration, a user-selected 10% gives **83.4 assumed site exposure hours** for first-year P50. The 10% is not fitted. The slider starts at a clearly labeled 100% comparison. Backup power and workload migration further separate grid reductions from computing downtime.

## Reproduce

Use the environment in [ML_WALKTHROUGH.md](ML_WALKTHROUGH.md). Existing preparation outputs are protected; use new paths for another run. Source downloads are cached.

```powershell
.\.venv\Scripts\python.exe -m pipeline.history --area "Amarillo, TX" --out data/processed/ml_inputs/spp_2019_2024_amarillo.parquet
.\.venv\Scripts\python.exe -m pipeline.generation --hourly data/processed/ml_inputs/spp_2019_2024_amarillo.parquet --out data/processed/ml_inputs/spp_2019_2024_multifactor.parquet
.\.venv\Scripts\python.exe -m pipeline.workflow prepare-demand-proxy --hourly data/processed/ml_inputs/spp_2019_2024_multifactor.parquet --out data/processed/ml_inputs/spp_2019_2024_demand_proxy.parquet --policy data/processed/ml_inputs/spp_2019_2024_demand_proxy.policy.json --reference-end 2022-01-01T00:00:00Z --quantile 0.95
.\.venv\Scripts\python.exe -m pipeline.workflow train --hourly data/processed/ml_inputs/spp_2019_2024_demand_proxy.parquet --policy data/processed/ml_inputs/spp_2019_2024_demand_proxy.policy.json --run-dir data/processed/workbench/runs/spp_multifactor_demand_20260913
.\.venv\Scripts\python.exe -m pipeline.workflow simulate --run-dir data/processed/workbench/runs/spp_multifactor_demand_20260913 --simulations 2000 --years 7
```

Training automatically writes `hours_summary.json` and `monthly_expected_hours.csv`. Older runs can use `python -m pipeline.workflow hours --run-dir PATH` or **Calculate scored-period hours**. Annual simulation is a separate explicit offline job and rejects insufficient history. Opening the workspace only reads saved results.

The model and generated artifacts remain under ignored `data/`, available on this computer and through workspace downloads. This report and `multifactor-model-summary.json` record the results in Git. The `/api` and `/web` demo providers are unchanged; this system model is not silently mapped to a pricing node. Review of the proxy, calibration and annual methodology remains necessary before production use.
