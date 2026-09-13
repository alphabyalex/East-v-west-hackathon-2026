# Estimate exposure for your location

Open **Open ML Workspace.cmd**, or visit **http://127.0.0.1:8765** while the app is running.

1. Under **Your inputs**, enter a city and state or latitude and longitude. A city uses its center; use coordinates for a specific parcel.
2. Enter facility power demand, flexible share, the site-exposure assumption and years to estimate.
3. Select **Estimate hours**. The app searches for the place and automatically calculates a result when there is a single match. If several places match, choose the intended one and select **Estimate hours** again.
4. Under **Your estimate**, read the large **Estimated site exposure per year** number, followed by term hours and annual energy exposure.
5. Use **Save estimate (.txt)** for a readable summary, or download the full source JSON. Previous results are available under **View a saved estimate**.

The main screen contains inputs and outputs. Warning-sign explanations, historical comparison tables and training controls are not displayed. The underlying regional model and saved evidence remain available; [model methodology](WARNING_SIGNS.md) explains their interpretation.

## Inputs collected automatically

| Input | Geographic scope | Source and use |
|---|---|---|
| Place coordinates | Selected city or user coordinates | Open-Meteo / GeoNames search |
| Utility territory | Polygons intersecting the point | [PNNL historical HIFLD service](https://eedgis.pnnl.gov/arcgis/rest/services/Hosted/Electric_Service_Territories/FeatureServer/0); approximate historical screening |
| Hourly temperature | ERA5 grid cell at the selected coordinates | [Open-Meteo historical weather](https://open-meteo.com/en/docs/historical-weather-api) |
| Demand, wind and solar | Historical SPP system | Cached official 2019–2024 archives with source URLs and hashes |

The regional model learns from separate measured load histories for SPS, OKGE, LES and OPPD, paired with representative weather. It applies those relationships using the query point's weather and shared SPP measurements. It does not invent local grid demand for the query point. Subsequent estimates reuse the shared model and cached data.

Search accepts arbitrary US cities or coordinates, with a Census place-directory fallback. The regional estimator accepts the historical SPP region and nearby comparison areas within 100 km, including regions around documented 2026 western participants and their member utilities. An old utility-map mismatch alone no longer blocks a regional estimate. These approximate matches do not establish exact interconnections; western estimates transfer historical SPP East patterns. See [the coverage and nearby-data policy](SPP_LOCATION_COVERAGE.md) for sources, bounds and verification.

## Output definitions

- **Estimated site exposure per year:** expected regional high-demand hours multiplied by the site-exposure assumption.
- **Estimated site exposure over N years:** annual expected site hours multiplied by the selected term.
- **Estimated energy exposure per year:** site hours × facility MW × flexible share, in MWh.
- **Regional high-demand hours per year:** expected modeled hours before assigning a share to the site.

These are **modeled exposure** outputs, with **Low confidence**. They do not establish actual site interruptions or future cutoff dates. Annual expectations compare historical monthly conditions with a stationary 365-day year. Facility MW scales energy; it does not simulate the new facility's effect on grid demand.

Saved inputs always accompany the numbers. Form edits show a notice until a new estimate is generated; the old values are never silently changed. Saved results from the earlier system-wide simulation explicitly retain P50 labels, meaning the middle simulated outcome. New regional estimates do not infer P50/P90/P99 outcome quantiles for an unobserved local target.

## Local interfaces and evidence

- `POST /api/jobs`, `kind=site-scan`, `query`: explicit lookup; `GET /api/site-scan?id=...` reads registered candidates.
- `POST /api/jobs`, `kind=site-transfer`, `scan`, integer `candidate`, `load_mw`, `conditional_share`, `site_exposure`, integer `years`, optional `confirm_spp`: explicit regional report job. Shares are fractions from 0 to 1.
- Existing `site-report` and `site-signals` jobs remain available for research through the API.
- `GET /api/site-report?id=...` reads a saved report; `GET /api/site-download?id=...&file=site_report.json` returns the full original artifact.
- The browser's plain-text download summarizes the saved report, including assumptions and sources, without warning-sign explanations.
- Cached files remain under ignored `data/processed/workbench/sites/`, `regional/`, `area_models/` and `jobs/`.

The loopback-only server, Host/Origin/token checks, registered-input validation, one-job limit and safe subprocess arguments remain in force. Viewing a page or refreshing it does not fetch external data or train. The demo `/api` and `/web` contracts are unchanged.
