# Use the location estimator

Open **http://127.0.0.1:8765** on this computer. To start the app later, double-click **Open ML Workspace.cmd** in the repository. The launcher starts the service in the background and opens your browser; running it again reuses the service.

## Enter your inputs

1. Enter a **Location**: a US city and state, or latitude and longitude for a particular site.
2. Set **Facility power demand (MW)** and **Flexible share of power (%)**.
3. Set the **Site-exposure assumption**: the share of regional high-demand hours you assume applies to the site.
4. Choose **Years to estimate**, from 1 to 7.
5. Select **Estimate hours**. The app finds the location, checks SPP regional coverage, retrieves local or nearby historical weather and calculates the result. If several places match, choose one and select **Estimate hours** again.

The model approach is selected automatically. Temperature, load, wind and solar histories do not need manual entry. The first preparation can take several minutes; cached data and the shared fitted model are reused.

## Read your output

The largest number is **Estimated site exposure per year**, in hours. Below it are the estimated site hours over your selected term, annual energy exposure in MWh, and the regional high-demand hours before the site assumption is applied.

**Inputs used for this estimate** records the exact saved assumptions. Editing the form does not silently change a saved result: select **Estimate hours** to generate an updated result. A notice identifies when displayed results belong to earlier inputs.

Use **View a saved estimate** to reopen a result. The app restores that result's inputs. **Save estimate (.txt)** saves a readable summary; **Download full result (.json)** includes the complete saved evidence and unrounded values. The source links beside the outputs open **Data sources and calculation**.

Older SPP-wide simulation results remain readable and explicitly show **Typical ... (P50)**, the middle simulated outcome. They are not relabeled as expected-value estimates. New requests use the regional comparison model.

## What the result means

All hours are **modeled exposure to high-demand conditions**, with **Low confidence**. They are not actual cutoff durations or future interruption dates. Local transmission constraints and site interruption records are unavailable.

Site hours multiply regional modeled hours by your site-exposure assumption. Energy exposure also multiplies by facility MW and flexible share, assuming that flexible portion is interrupted throughout the assumed site hours. Changing facility size affects energy, not grid-stress hours. The new model compares historical monthly conditions with a stationary 365-day year, without growth or climate projections.

Historical SPP grid inputs cover 2019–2024. Locations inside or within 100 km of the historical SPP outline, and areas near documented western expansion utilities, can now receive regional comparisons without manual confirmation. This also permits cities surrounded by SPP territory whose own utility is on another grid; these results are marked approximate. Western estimates use historical SPP East patterns as an analogy, not a model trained on the 2026 expansion. Distant locations such as Houston and Seattle remain outside the automatic comparison area.

Search accepts `Colorado Springs CO`, `Colorado Springs, Colorado`, and coordinates. A cached national Census directory supplements the primary city search. Weather is requested at your coordinates first; if unavailable or incomplete, the app tries complete cached histories and nearby weather points within 100 km. The selected city stays unchanged, and the output/export records the weather match. See [coverage and nearby-data policy](SPP_LOCATION_COVERAGE.md).

## Local operation

Opening or refreshing the app only reads saved data. Selecting **Estimate hours** explicitly authorizes the location lookup followed by the report job. Only an unambiguous match continues automatically. The service accepts one background job at a time.

If the browser is closed during location search, reopen it and select **Estimate hours** again to continue; a reopened page does not silently start the next job. If it is closed during report generation, that job continues and its saved result becomes available when it finishes.

Use **Refresh results** to reconnect after a connection problem. Failed jobs expose **Processing details**; successful downloads remain cached. The app is local to this computer, not a publicly hosted website.

Manual data preparation, reviewed event-label training, saved model inspection and simulations remain available through the [command-line workflow](ML_WALKTHROUGH.md) and existing local API. Those developer controls and warning-sign explanations are no longer displayed on the estimator screen. The underlying learned relationships and complete evidence remain in saved model artifacts; see [model methodology](WARNING_SIGNS.md).

The local service is separate from the team's demo at `/web` and `/api`. Its files are under ignored `data/processed/workbench/`; raw data and trained models are not published in Git.

## Checks

The simplified flow has ten JavaScript behavior checks, including nearby-data notes, exports, input/output units, saved-input integrity, automatic and ambiguous searches, stale responses, legacy medians and safe source URLs. Run `node tests/test_estimator_ui.js` where Node is installed. Run the 80 Python checks with `python -m unittest discover -s tests`. Geographic lookup, weather fallback and the automatic regional gate have dedicated regression checks. Live static assets and saved results were checked; a connected browser was unavailable for visual inspection.
