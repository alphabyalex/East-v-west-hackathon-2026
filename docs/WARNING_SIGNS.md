# Learning stress signs across areas

The local estimator now shows only inputs and outputs. Enter a city/state or coordinates, set facility assumptions, and select **Estimate hours**. It chooses the regional comparison model automatically. The first run prepares reference data and trains the shared model; subsequent locations reuse it and retrieve their own historical weather. See [the current screen guide](ML_WORKSPACE.md).

Warning-sign explanations remain in saved model evidence and the full JSON download; they are not displayed on the estimator screen. The existing `site-signals` API job can still explain a saved SPP-wide model without retraining it, preserving the original report.

## What the saved evidence contains

- **Signs associated with high demand:** combinations such as high temperature with high demand, cold with high demand, rising demand, high demand with low wind, and high demand after wind/solar. The table gives the actual comparison thresholds, matched historical hours/days, held-out high-demand frequency and a comparable baseline.
- **Explained historical hours:** the evidence records which groups of inputs raised or lowered each selected prediction. The groups include temperature, demand, demand changes, their interaction, wind/solar, net demand and calendar patterns. All observed inputs end before the target hour.
- **Similar historical conditions:** training examples with similar temperature, SPP load, ramp, net demand and wind, including their area and whether its own high-demand proxy was active. No close examples means weak historical support; the software does not manufacture matches.
- **Testing on excluded areas:** the regional report shows performance on areas that contributed no rows to that fold's model fitting or calibration. Both area and time separation matter.

These features explain **stress-proxy predictions**. Neither a raised contribution nor a high predicted probability is an actual cutoff instruction. Missing local constraints, reserves, outages and site interruption logs cannot supply learned effects in this version.

## What is trained

| Reference load area | Representative weather point | Target |
|---|---|---|
| SPS | Amarillo, Texas | SPS load above its own pre-2022 reference 95th percentile |
| OKGE | Oklahoma City, Oklahoma | OKGE load above its own pre-2022 reference 95th percentile |
| LES | Lincoln, Nebraska | LES load above its own pre-2022 reference 95th percentile |
| OPPD | Omaha, Nebraska | OPPD load above its own pre-2022 reference 95th percentile |

The SPP archive contains separate measured load columns for these areas. The training rows therefore do not duplicate the same SPP-wide outcome across different city names. They use each area's separate load history to define its outcome. These are utility/load-area observations, not four data centers' cutoff logs. The threshold is an explicit research assumption identifying unusually high demand; it does not establish overload or a tariff trigger.

Historical code/name references: [SPP resource-adequacy filing](https://spp.org/documents/56732/20180330_tariff%20revisions%20to%20implement%20a%20set%20of%20resource%20adequacy%20policies_er18-1268-000.pdf) and [SPP Nebraska member descriptions](https://www.spp.org/Documents/37741/RSC%20Materials%2020160425%20PGD.pdf). Choosing one city's ERA5 weather to represent a broad load area is an assumption, recorded separately from the measured loads. City coordinates come from the cached geocoder. These reference cities do not limit the user's location search.

Predictors available at reference and query points are **local weather plus shared SPP load, wind and solar**, their past changes/rolling histories and calendar features. The label-defining `area_load_mw`, target-hour outcome and area identity are excluded from the model inputs. Query-point local load is unavailable and is not invented. The resulting transferred likelihood describes analogous regional high-demand behavior; interpreting it as a particular site's interruption probability would be unsupported.

The existing 15-member LightGBM ensemble and chronological sigmoid calibration are reused. The earliest 60% of times fit trees, the middle 20% calibrate, and the latest 20% test, with the existing embargo. Entire SPS, OKGE, and paired LES/OPPD groups are separately withheld to assess transfer. Grouping neighboring Nebraska areas prevents treating their similar weather as fully separate geography in validation. Each evaluation begins after both the common test cutoff and that fold's actual cutoff, even when areas have unequal source coverage. The final pooled model uses all four references; evaluation on an excluded area is produced by a separate model. Transferred hours are withheld if the combined cross-area Brier score does not beat the training-prevalence baseline.

## How explanations are computed

[LightGBM's documented feature contributions](https://lightgbm.readthedocs.io/en/stable/pythonapi/lightgbm.Booster.html#lightgbm.Booster.predict) reconstruct each tree model's raw score. Multiplying them by its fitted sigmoid-calibration slope, and adding the calibrator intercept to the baseline, reconstructs that member's calibrated log odds. The implementation checks the reconstructed probability against the fitted predictor before displaying the explanation. No new calibration method is fitted.

Groups are ordered by absolute mean contribution. A positive contribution raises the model's score relative to its learned baseline; a negative contribution lowers it. Direction agreement counts ensemble members with the same sign. It is not a probability that the explanation is correct. Contributions do not add as percentage-point changes to the ensemble's mean probability, and correlated predictors/interactions prevent a causal interpretation.

The historical-combination table is a separate descriptive check. High/low cutoffs use training-only 90th/10th percentiles. Frequencies use later held-out labels. A comparison needs at least 100 matched area-hours across 20 distinct days for the ordinary support label; otherwise it says limited support. Area-hours and overlapping weather events can be correlated, so those counts are not independent trials. These rules do not set the classifier's probabilities.

Similar examples use training-only scaling and complete observations of temperature, SPP load, load ramp, net load and wind. Examples must be within 0.75 RMS standardized distance; up to eight are displayed with at most one per area/day. This is an illustrative similarity rule, not an empirical confidence interval or a learned physical boundary.

## Hours and uncertainty

The across-area report supplies an **expected number of proxy hours**, calculated by summing monthly mean probabilities times hours in a stationary 365-day comparison year. Each month requires at least 90% coverage over the query window, and the window must span at least a year. Missing periods are reported. The query has no fabricated 0/1 labels.

The expected site hours multiply this estimate by the visible site-exposure assumption. Conditional MWh additionally multiplies by facility MW and the conditional share. The term expectation multiplies the stationary annual expectation by the requested years. Adding a facility's MW to actual grid demand is not simulated.

**No P50/P90/P99 outcome quantiles are inferred for the transferred query target.** Its true outcomes are unobserved, so borrowing the old system model's residuals/tail quantiles would misrepresent validation. Ensemble minimum/maximum annual expectations describe model disagreement only. The earlier SPP-wide annual-scenario model remains a separate selectable approach with its own target and experimental quantiles.

Confidence stays **Low**. Inputs are historical 2019–2024 archives, with completed 2025 weather supplying the archive's final UTC boundary. This is not a real-time alert service, future outage schedule, climate projection or validated data-center cutoff model. Real local interruption/constraint and operating-rule data would be needed to learn actual cutoff behavior.

## Artifacts and interfaces

The [recorded Wichita example](regional-warning-summary.json) uses model `spp_regional_cc7d60f6ff429cb1e0cb`. Its highest-scored historical hour was 2024-08-26 20:00 UTC, using 34.5°C preceding-hour temperature and 50,887.832 MW of SPP demand. Temperature together with demand was the largest positive explanation group. This is a model explanation, not evidence that Wichita experienced a cutoff.

Transfer accuracy varies materially by area: the excluded OPPD test estimated 302.5 high-demand-proxy hours against 1,181 observed in the same evaluation window. Beating a constant-prevalence baseline does not establish calibrated annual totals for a new site. The full JSON and snapshot retain each excluded area's predicted and observed hours alongside the low-confidence Wichita expectation.

- New modules: `pipeline/regional.py` and `pipeline/signals.py`. `pipeline/train.py` and `pipeline/label.py` are unchanged.
- `features.build_features(..., require_target=False)` supports unlabeled inference while preserving unknown targets as missing.
- `POST /api/jobs` accepts `kind=site-transfer` with the same saved-search candidate and assumptions as a site report, or `kind=site-signals` with a registered existing report ID.
- Reports use `status=research_transfer_exposure`, `estimates`, `cross_area_validation`, `warning_signs`, source manifests and input hashes. Existing system reports retain their schema and gain optional `warning_signs`.
- Raw weather/geocoding is cached in parquet. Reference inputs and shared models live under ignored `data/processed/workbench/regional`; query observations and predictions live with the saved report. Viewing reports never downloads, trains or recalibrates.
- HTML, Markdown and JSON downloads include the warning-sign evidence. The demo `/api` and `/web` contracts remain separate from this explicit local research workflow.
