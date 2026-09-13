# Use the location estimator

Open **http://127.0.0.1:8765** on this computer. To start the app later, double-click **Open ML Workspace.cmd** in the repository. The launcher starts the service in the background and opens your browser; running it again reuses the service.

## Enter your inputs

1. Enter a **Location**: a US city and state, or latitude and longitude for a particular site.
2. Set **Facility power demand (MW)** and **Flexible share of power (%)**.
3. Set the **Site-exposure assumption**: the share of regional high-demand hours you assume applies to the site.
4. Choose **Years to estimate**, from 1 to 7.
5. Select **Estimate hours**. The app finds the location, checks historical SPP coverage, retrieves historical weather and calculates the result. If several places match, choose one and select **Estimate hours** again.

The model approach is selected automatically. Temperature, load, wind and solar histories do not need manual entry. The first preparation can take several minutes; cached data and the shared fitted model are reused.

## Read your output

The largest number is **Estimated site exposure per year**, in hours. Below it are the estimated site hours over your selected term, annual energy exposure in MWh, and the regional high-demand hours before the site assumption is applied.

**Inputs used for this estimate** records the exact saved assumptions. Editing the form does not silently change a saved result: select **Estimate hours** to generate an updated result. A notice identifies when displayed results belong to earlier inputs.

Use **View a saved estimate** to reopen a result. The app restores that result's inputs. **Save estimate (.txt)** saves a readable summary; **Download full result (.json)** includes the complete saved evidence and unrounded values. The source links beside the outputs open **Data sources and calculation**.

Older SPP-wide simulation results remain readable and explicitly show **Typical ... (P50)**, the middle simulated outcome. They are not relabeled as expected-value estimates. New requests use the regional comparison model.

## What the result means

All hours are **modeled exposure to high-demand conditions**, with **Low confidence**. They are not actual cutoff durations or future interruption dates. Local transmission constraints and site interruption records are unavailable.

Site hours multiply regional modeled hours by your site-exposure assumption. Energy exposure also multiplies by facility MW and flexible share, assuming that flexible portion is interrupted throughout the assumed site hours. Changing facility size affects energy, not grid-stress hours. The new model compares historical monthly conditions with a stationary 365-day year, without growth or climate projections.

Historical SPP grid inputs cover 2019–2024. An apparent match to another grid produces no estimate. An inconclusive coverage lookup exposes an optional confirmation only if you independently know the point belongs to the historical SPP footprint; this is recorded as your assumption.

## Local operation

Opening or refreshing the app only reads saved data. Selecting **Estimate hours** explicitly authorizes the location lookup followed by the report job. Only an unambiguous match continues automatically. The service accepts one background job at a time.

If the browser is closed during location search, reopen it and select **Estimate hours** again to continue; a reopened page does not silently start the next job. If it is closed during report generation, that job continues and its saved result becomes available when it finishes.

Use **Refresh results** to reconnect after a connection problem. Failed jobs expose **Processing details**; successful downloads remain cached. The app is local to this computer, not a publicly hosted website.

Manual data preparation, reviewed event-label training, saved model inspection and simulations remain available through the [command-line workflow](ML_WALKTHROUGH.md) and existing local API. Those developer controls and warning-sign explanations are no longer displayed on the estimator screen. The underlying learned relationships and complete evidence remain in saved model artifacts; see [model methodology](WARNING_SIGNS.md).

The local service is separate from the team's demo at `/web` and `/api`. Its files are under ignored `data/processed/workbench/`; raw data and trained models are not published in Git.

## Checks

The simplified flow has nine JavaScript behavior checks, covering input/output units, saved-input integrity, automatic and ambiguous location search, stale responses, explicit SPP confirmation, legacy medians, missing values and safe source URLs. Run `node tests/test_estimator_ui.js` where Node is installed. The existing seven workspace and nine site Python tests also pass. Live static assets and saved results were checked; a connected browser was unavailable for visual inspection.
