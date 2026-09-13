# Annual readiness retraining audit

Audited 2026-09-13T23:42:30.211961+00:00. [Machine-readable evidence](annual-readiness-retraining-audit.json) includes source paths, hashes, coverage counts, candidate policies and every HTTP result.

**Result: blocked by missing original target data. No retraining or artifact promotion was performed.** Multi-year load, generation and weather caches exist, but the published observed-event model cannot be recreated from the cached high-demand proxy. All 21 published IDs remain below the 8,760-hour readiness threshold.

Published model: `spp_lgbm_20260913T060709_b63ee2577e`. Its canonical parquet, model card and simulation metadata remain byte-for-byte unchanged from the start of this task. `pipeline/label.py`, `confidence.py`, `features.py`, `train.py` and `simulate.py` also remain unchanged. The preceding confidence work was already committed before this request.

## Measurements found before training

The first inventory was reported before attempting any training. This audit subsequently checked all 1,079 parquet schemas under `data/raw/` and `data/processed/` (957 raw, 122 processed); none was unreadable. All 43 files with label-related columns were inspected. Raw annual load contains 17 component load zones; `SPP_SYSTEM` is their aggregate. The three `spp-*-demo` entries are scenario aliases, so the published 21-ID catalog is not 21 independently observed zones.

Raw load sources: `data/raw/spp/access_check/{2018..2024}_hourly_load.parquet` and `2025_monthly_load.parquet`. They span the source market years, converted using the existing UTC hour-ending convention: January 1 06:00 UTC through the following January 1 06:00 UTC, end exclusive. 2020 and 2024 have 8,784 expected hours; the other years have 8,760.

**Missing or conflicting load hours per source year** (0 means all expected hours have an unambiguous finite, nonnegative measurement):

| Load ID | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| CSWS | 8 | 0 | 0 | 2 | 12 | 3 | 3 | 1 |
| EDE | 8 | 0 | 0 | 2 | 12 | 3 | 3 | 1 |
| GRDA | 2 | 0 | 0 | 2 | 11 | 3 | 3 | 1 |
| INDN | 8 | 0 | 0 | 2 | 12 | 3 | 3 | 1 |
| KACY | 8 | 0 | 0 | 2 | 12 | 3 | 3 | 1 |
| KCPL | 8 | 0 | 0 | 2 | 12 | 3 | 3 | 1 |
| LES | 8 | 0 | 0 | 2 | 12 | 3 | 3 | 1 |
| MPS | 8 | 0 | 0 | 2 | 12 | 3 | 3 | 1 |
| NPPD | 2 | 0 | 0 | 2 | 11 | 3 | 3 | 0 |
| OKGE | 8 | 0 | 0 | 2 | 12 | 3 | 3 | 1 |
| OPPD | 8 | 0 | 0 | 2 | 12 | 3 | 3 | 0 |
| SECI | 8 | 0 | 0 | 2 | 12 | 3 | 3 | 1 |
| SPRM | 8 | 0 | 0 | 2 | 12 | 3 | 3 | 1 |
| SPS | 8 | 0 | 0 | 2 | 12 | 3 | 3 | 1 |
| WAUE | 8 | 0 | 0 | 2 | 12 | 3 | 3 | 0 |
| WFEC | 8 | 0 | 0 | 2 | 12 | 3 | 3 | 1 |
| WR | 8 | 0 | 0 | 2 | 12 | 3 | 3 | 1 |
| SPP_SYSTEM | 8 | 0 | 0 | 2 | 12 | 3 | 3 | 1 |

The raw audit reconciles each component value independently at duplicated timestamps; conflicting values remain unknown. Existing system preparation is stricter: it discards conflicting source rows even when an individual field agrees. Consequently its 2021 aggregate has 8,711 known hours, compared with 8,758 unambiguous component hours in the raw audit. No cleaning policy was changed or missing value filled.

`data/processed/ml_inputs/spp_2019_2025_multifactor.parquet` contains **only SPP_SYSTEM**: 61,362 hourly slots, 61,294 known load observations, 61,362 joined temperature observations, and 60,259 complete wind/solar observations each. Actual UTC-calendar counts in this prepared file are:

| UTC year | Slots | Known load | Known temperature | Complete wind hours | Complete solar hours |
|---|---:|---:|---:|---:|---:|
| 2019 | 8,754 | 8,754 | 8,754 | 8,645 | 8,645 |
| 2020 | 8,784 | 8,784 | 8,784 | 8,665 | 8,665 |
| 2021 | 8,760 | 8,711 | 8,760 | 8,641 | 8,641 |
| 2022 | 8,760 | 8,748 | 8,760 | 8,409 | 8,409 |
| 2023 | 8,760 | 8,757 | 8,760 | 8,499 | 8,499 |
| 2024 | 8,784 | 8,781 | 8,784 | 8,714 | 8,714 |
| 2025 | 8,760 | 8,759 | 8,760 | 8,686 | 8,686 |

The prepared series begins at 2019-01-01 06:00 UTC, so its 2019 UTC calendar omits the first six hours. It ends at 2025-12-31 23:00 UTC. The initial annual-source inventory counted 8,651 valid generation hours in the 2019 file and 8,685 in the 2025 file; the joined UTC-calendar table above has 8,645 and 8,686 respectively because year boundaries differ and joining uses the neighboring annual caches. These are measurements, not scored or labeled hours.

Generation caches exist for every year 2019-2025 at `data/raw/spp/generation/{year}.parquet`, with source manifests. Generation completeness requires all 12 distinct valid five-minute measurements per hour. Missing/conflicting hours remain unknown. Generation was an added factor in the separate proxy runs; the canonical model card lists calendar, lagged load and temperature features.

**Weather:** 70 ERA5 parquet caches cover 10 points for every UTC calendar year 2019-2025, all 613,680 hourly temperatures present. Each point has 8,760 hours in ordinary years and 8,784 in 2020/2024. Exact coordinates, URLs, retrieval times and file hashes are recorded in the JSON. This establishes point-weather availability, not a mapping of those points to all catalog IDs.

## Exact missing inputs

1. The canonical policy is `label_method=observed_event`, `label_ref=local://spp_events.csv`. That original file was not found in the searched user/workspace paths, repository history or stash file inventories. The hourly input with SHA256 `b63ee2577e8c5566bd7142dc3724703c4a1e36924cad2a6d01825a9d3875aa73` and its original training bundle are absent. Complete matching observed-event labels and confirmed non-event observation coverage are unavailable for **every year 2019-2025 and every one of the 21 published IDs**. The 2024 model card preserves aggregate label counts, not the missing hourly label timestamps.
2. The documented emergency cache has only 102 known positive hourly buckets: 93 in 2021, 6 in 2022, and 3 in 2024, totaling 5,908 documented minutes. It has **zero confirmed non-event hours**. The remaining years/hours are unknown; absence from this partial catalog cannot establish `event_active=0`. These system-event records do not supply the missing original target definition/coverage.
3. The original weather manifest `data/processed/ml_inputs/spp_2024_multi_location_temperature.weather.json` and zone-to-weather mapping with SHA256 `31a59a6e3929512788008fd9c5030f3c6353c642cc4d0bb987e14b52a62acad0` were not found. Regional work has four explicit point/zone choices, but those do not recover the original 21-ID temperature feature mapping.

All six saved noncanonical model cards and their labeled inputs use `high_demand_stress_proxy`, with a frozen 95th-percentile aggregate demand threshold of 42,064.0228 MW. They cover SPP_SYSTEM only. Their 10,426- or 12,172-hour test sets therefore cannot be substituted for this model. [MULTIFACTOR_PREDICTION.md](MULTIFACTOR_PREDICTION.md) explicitly describes that different target.

Calling the unchanged `label_hours()` on the multi-year multifactor input with the canonical policy raises: `observed_event needs ['event_active']; do not substitute a different label silently.` No training was launched against a relabeled proxy, and no zeros were invented. Load/weather coverage alone is insufficient to create an observed-event test set.

To unblock the same-target run, recover the original observed-event definition and timestamped positive/negative observation coverage across multiple years, together with its catalog weather mapping. Then choose a chronological calendar-year holdout with prior-hour feature context and verify actual scored rows for each ID. Merely selecting a year with 8,760 calendar slots does not guarantee 8,760 scored hours; 2024/2025 load and lag-history gaps still need to be accounted for.

## Before and after: all 21 IDs

All entries have the same inclusive held-out window: **2024-10-20 10:00 UTC to 2024-12-31 23:00 UTC**, or **1,742 elapsed hourly slots**, of which **1,717 are scored**. Each is short by **7,043 scored hours**. There was no successful retraining, so before and after are identical. The API still requires both span and local scored count to be at least 8,760.

| Published ID | Scored hours before | Scored hours after | Readiness before -> after | POST /api/estimate | Real modeled exposure |
|---|---:|---:|---|---|---|
| CSWS | 1,717 | 1,717 | Fail -> Fail | 503 | No |
| EDE | 1,717 | 1,717 | Fail -> Fail | 503 | No |
| GRDA | 1,717 | 1,717 | Fail -> Fail | 503 | No |
| INDN | 1,717 | 1,717 | Fail -> Fail | 503 | No |
| KACY | 1,717 | 1,717 | Fail -> Fail | 503 | No |
| KCPL | 1,717 | 1,717 | Fail -> Fail | 503 | No |
| LES | 1,717 | 1,717 | Fail -> Fail | 503 | No |
| MPS | 1,717 | 1,717 | Fail -> Fail | 503 | No |
| NPPD | 1,717 | 1,717 | Fail -> Fail | 503 | No |
| OKGE | 1,717 | 1,717 | Fail -> Fail | 503 | No |
| OPPD | 1,717 | 1,717 | Fail -> Fail | 503 | No |
| SECI | 1,717 | 1,717 | Fail -> Fail | 503 | No |
| SPP_SYSTEM | 1,717 | 1,717 | Fail -> Fail | 200 (placeholder) | No |
| SPRM | 1,717 | 1,717 | Fail -> Fail | 503 | No |
| SPS | 1,717 | 1,717 | Fail -> Fail | 503 | No |
| WAUE | 1,717 | 1,717 | Fail -> Fail | 503 | No |
| WFEC | 1,717 | 1,717 | Fail -> Fail | 503 | No |
| WR | 1,717 | 1,717 | Fail -> Fail | 503 | No |
| spp-lincoln-demo | 1,717 | 1,717 | Fail -> Fail | 200 (placeholder) | No |
| spp-oklahoma-city-demo | 1,717 | 1,717 | Fail -> Fail | 200 (placeholder) | No |
| spp-wichita-demo | 1,717 | 1,717 | Fail -> Fail | 200 (placeholder) | No |

These were actual loopback HTTP POSTs against an isolated Uvicorn `api.main:app` server reading the published files, with no mocks or dependency overrides. Request controls: `load_mw=100`, `term_years=7`, `flexibility_split=0.6`, `site_exposure=0.5`. All 17 component zones return 503 with the explicit annual-coverage reason. SPP_SYSTEM and the three scenario aliases return authored placeholder responses. **0/21 real modeled responses; 0/21 annual-ready.** The isolated server was stopped normally; existing demo servers were not restarted.

## Validation and consolidation

- Full Python suite: **1,999 passed, 42 subtests passed, 1 skipped, 2 warnings** (`python -m pytest tests api -q`).
- Frontend: **316 tests across 22 files passed their assertions**, including real HTTP contract tests, but the overall run was not clean: **6 unhandled `ReferenceError: window is not defined` errors** from `ZoneLeaderboard.tsx` promises after `App.test.tsx` teardown. This is reported as a test-suite failure, not a passing full suite.
- TypeScript and Vite production build passed, with the existing large-chunk warning.
- Completed the previously started merge of `origin/main` into the backend checkpoint. The frontend is byte-for-byte identical in Git to `origin/main` commit `3e83e48`; no frontend repairs were authored for this task.
- Reconciled two imported test fixtures/expectations with existing confidence/ranking validation. Updated the imported grid-impact manifest's exposure-catalog hash after verifying identical 21-ID membership; canonical exposure values and artifact files were not regenerated.
- Publication target: **Alex only**, per the latest instruction. No push to Kristian or main.

Canonical artifact SHA256 values:

- `exposure_by_location.parquet`: `1abaafa021c4c98339de67110dda107f806647b1d5281abbd66e8093385215b0`
- `model_card.json`: `a733e5da27d477471bd88593d84577f14e3028113714fd0be17710d56b279052`
- `simulation_metadata.json`: `6487e72c5900585e54b5a86f3808d75ad039f95cee8dedf4e39486e71a3cd049`
