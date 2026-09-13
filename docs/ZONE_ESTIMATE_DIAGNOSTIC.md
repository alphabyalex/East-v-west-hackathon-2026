# Zone estimate diagnostic - 2026-09-13

Request: `POST http://127.0.0.1:8000/api/estimate` with `load_mw=100`, `term_years=7`, `flexibility_split=0.6`, `site_exposure=0.5`; location changes for every row.

The parquet has 147 rows, seven annual rows for each of 21 choices: 17 real SPP load areas, the SPP_SYSTEM aggregate, and three named scenario aliases. All 21 exact-ID calls to pipeline.simulate.get_location_estimate succeeded. No location/column lookup failure or 503 was reproduced on the local baseline.

## Before: every location

| Location | HTTP status | Exposure source / exact error body |
|---|---|---|
| CSWS | 404 | `{"detail":"Location is not in the supplied SPP estimate set"}` |
| EDE | 404 | `{"detail":"Location is not in the supplied SPP estimate set"}` |
| GRDA | 404 | `{"detail":"Location is not in the supplied SPP estimate set"}` |
| INDN | 404 | `{"detail":"Location is not in the supplied SPP estimate set"}` |
| KACY | 404 | `{"detail":"Location is not in the supplied SPP estimate set"}` |
| KCPL | 404 | `{"detail":"Location is not in the supplied SPP estimate set"}` |
| LES | 404 | `{"detail":"Location is not in the supplied SPP estimate set"}` |
| MPS | 404 | `{"detail":"Location is not in the supplied SPP estimate set"}` |
| NPPD | 404 | `{"detail":"Location is not in the supplied SPP estimate set"}` |
| OKGE | 404 | `{"detail":"Location is not in the supplied SPP estimate set"}` |
| OPPD | 404 | `{"detail":"Location is not in the supplied SPP estimate set"}` |
| SECI | 404 | `{"detail":"Location is not in the supplied SPP estimate set"}` |
| SPP_SYSTEM | 200 | `assumption; mock://placeholder/exposure; annual reference not ready` |
| SPRM | 404 | `{"detail":"Location is not in the supplied SPP estimate set"}` |
| SPS | 404 | `{"detail":"Location is not in the supplied SPP estimate set"}` |
| WAUE | 404 | `{"detail":"Location is not in the supplied SPP estimate set"}` |
| WFEC | 404 | `{"detail":"Location is not in the supplied SPP estimate set"}` |
| WR | 404 | `{"detail":"Location is not in the supplied SPP estimate set"}` |
| spp-lincoln-demo | 200 | `assumption; mock://placeholder/exposure; annual reference not ready` |
| spp-oklahoma-city-demo | 200 | `assumption; mock://placeholder/exposure; annual reference not ready` |
| spp-wichita-demo | 200 | `assumption; mock://placeholder/exposure; annual reference not ready` |

## Cause

The API validates a real matching row, then its annual-readiness check finds only 1,717 same-location scored hours over a 1,742-hour held-out clock span. It requires at least 8,760 for both. The provider then asks the authored four-location mock catalog for a fallback. That catalog has no record for the 17 zones, so it raises LocationNotFoundError and HTTP emits a misleading 404. The system and demo aliases return authored placeholders, not their saved model values.

## Upstream confidence regeneration

Fetched origin/Kristian through 6c19dc5. Commit 54c7571 regenerated only confidence columns from recorded evidence, preserving annual exposure values. Commit 4d95840 added confidence-policy validation; its outbox explicitly confirms all 21 canonical locations still fail annual readiness at 1,717 local held-out hours. Commit 01c543b describes a separate multi-year SPP_SYSTEM high-demand-proxy run, with a different target; it is not a replacement for 21 zone models. Neither that run nor a new 21-zone annual-ready bundle is present under data/processed in this checkout. Main remains at 9e8a6a0. A confidence-fixed parquet mixed with older sidecars can also fail matching-value validation with 503; the current local parquet/card match.

## Fix scope and remaining requirement

Correct the false not-found error for an existing but unready zone, and distinguish zone-model coverage from the separate site-exposure assumption in the caption. Do not fabricate 8,760 hours, borrow the system-only proxy, or silently promote saved annual values past the coverage rule. Real 200 acceptance for the current bundle remains blocked pending an annual-ready matching artifact or an explicit choice to expose saved results as experimental with the missing-history limitation visible.

## After the error/caption fix: every live location

The blocker is not hidden: no saved annual outputs were promoted. The 17 false 404s now report an explicit 503 explaining coverage; the four original authored fallback responses are unchanged. **Zero of the 21 locations currently returns real modeled-exposure data through this endpoint.**

| Location | HTTP status | Exposure source / exact error body |
|---|---|---|
| CSWS | 503 | `{"detail":"Precomputed location CSWS exists, but model_version=spp_lgbm_20260913T060709_b63ee2577e; annual reference not ready: held-out span=1742 hours, local scored hours=1717 for CSWS; requires at least 8760 hours (365 days) for both; missing seasons must not be substituted"}` |
| EDE | 503 | `{"detail":"Precomputed location EDE exists, but model_version=spp_lgbm_20260913T060709_b63ee2577e; annual reference not ready: held-out span=1742 hours, local scored hours=1717 for EDE; requires at least 8760 hours (365 days) for both; missing seasons must not be substituted"}` |
| GRDA | 503 | `{"detail":"Precomputed location GRDA exists, but model_version=spp_lgbm_20260913T060709_b63ee2577e; annual reference not ready: held-out span=1742 hours, local scored hours=1717 for GRDA; requires at least 8760 hours (365 days) for both; missing seasons must not be substituted"}` |
| INDN | 503 | `{"detail":"Precomputed location INDN exists, but model_version=spp_lgbm_20260913T060709_b63ee2577e; annual reference not ready: held-out span=1742 hours, local scored hours=1717 for INDN; requires at least 8760 hours (365 days) for both; missing seasons must not be substituted"}` |
| KACY | 503 | `{"detail":"Precomputed location KACY exists, but model_version=spp_lgbm_20260913T060709_b63ee2577e; annual reference not ready: held-out span=1742 hours, local scored hours=1717 for KACY; requires at least 8760 hours (365 days) for both; missing seasons must not be substituted"}` |
| KCPL | 503 | `{"detail":"Precomputed location KCPL exists, but model_version=spp_lgbm_20260913T060709_b63ee2577e; annual reference not ready: held-out span=1742 hours, local scored hours=1717 for KCPL; requires at least 8760 hours (365 days) for both; missing seasons must not be substituted"}` |
| LES | 503 | `{"detail":"Precomputed location LES exists, but model_version=spp_lgbm_20260913T060709_b63ee2577e; annual reference not ready: held-out span=1742 hours, local scored hours=1717 for LES; requires at least 8760 hours (365 days) for both; missing seasons must not be substituted"}` |
| MPS | 503 | `{"detail":"Precomputed location MPS exists, but model_version=spp_lgbm_20260913T060709_b63ee2577e; annual reference not ready: held-out span=1742 hours, local scored hours=1717 for MPS; requires at least 8760 hours (365 days) for both; missing seasons must not be substituted"}` |
| NPPD | 503 | `{"detail":"Precomputed location NPPD exists, but model_version=spp_lgbm_20260913T060709_b63ee2577e; annual reference not ready: held-out span=1742 hours, local scored hours=1717 for NPPD; requires at least 8760 hours (365 days) for both; missing seasons must not be substituted"}` |
| OKGE | 503 | `{"detail":"Precomputed location OKGE exists, but model_version=spp_lgbm_20260913T060709_b63ee2577e; annual reference not ready: held-out span=1742 hours, local scored hours=1717 for OKGE; requires at least 8760 hours (365 days) for both; missing seasons must not be substituted"}` |
| OPPD | 503 | `{"detail":"Precomputed location OPPD exists, but model_version=spp_lgbm_20260913T060709_b63ee2577e; annual reference not ready: held-out span=1742 hours, local scored hours=1717 for OPPD; requires at least 8760 hours (365 days) for both; missing seasons must not be substituted"}` |
| SECI | 503 | `{"detail":"Precomputed location SECI exists, but model_version=spp_lgbm_20260913T060709_b63ee2577e; annual reference not ready: held-out span=1742 hours, local scored hours=1717 for SECI; requires at least 8760 hours (365 days) for both; missing seasons must not be substituted"}` |
| SPP_SYSTEM | 200 | `assumption; mock://placeholder/exposure; annual reference not ready` |
| SPRM | 503 | `{"detail":"Precomputed location SPRM exists, but model_version=spp_lgbm_20260913T060709_b63ee2577e; annual reference not ready: held-out span=1742 hours, local scored hours=1717 for SPRM; requires at least 8760 hours (365 days) for both; missing seasons must not be substituted"}` |
| SPS | 503 | `{"detail":"Precomputed location SPS exists, but model_version=spp_lgbm_20260913T060709_b63ee2577e; annual reference not ready: held-out span=1742 hours, local scored hours=1717 for SPS; requires at least 8760 hours (365 days) for both; missing seasons must not be substituted"}` |
| WAUE | 503 | `{"detail":"Precomputed location WAUE exists, but model_version=spp_lgbm_20260913T060709_b63ee2577e; annual reference not ready: held-out span=1742 hours, local scored hours=1717 for WAUE; requires at least 8760 hours (365 days) for both; missing seasons must not be substituted"}` |
| WFEC | 503 | `{"detail":"Precomputed location WFEC exists, but model_version=spp_lgbm_20260913T060709_b63ee2577e; annual reference not ready: held-out span=1742 hours, local scored hours=1717 for WFEC; requires at least 8760 hours (365 days) for both; missing seasons must not be substituted"}` |
| WR | 503 | `{"detail":"Precomputed location WR exists, but model_version=spp_lgbm_20260913T060709_b63ee2577e; annual reference not ready: held-out span=1742 hours, local scored hours=1717 for WR; requires at least 8760 hours (365 days) for both; missing seasons must not be substituted"}` |
| spp-lincoln-demo | 200 | `assumption; mock://placeholder/exposure; annual reference not ready` |
| spp-oklahoma-city-demo | 200 | `assumption; mock://placeholder/exposure; annual reference not ready` |
| spp-wichita-demo | 200 | `assumption; mock://placeholder/exposure; annual reference not ready` |
