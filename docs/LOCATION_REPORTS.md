# Choose your own location

The default **Learn stress signs across areas** approach now adds comparison across four load areas and explanations of the parameters raising/lowering each prediction. See [Learning stress signs](WARNING_SIGNS.md) for the new expected-hours output and its limits. Select **SPP-wide annual scenarios** for the original P50/P90/P99 workflow described below.

Open `Open ML Workspace.cmd` in the repository, or visit http://127.0.0.1:8765 while the workspace is running.

1. In **Explore your location**, enter any US city and state, such as `Wichita, KS`, or coordinates such as `37.6872, -97.3301` (latitude first). Coordinates allow parcels and rural locations; there is no fixed list of supported cities. Street addresses are not geocoded directly.
2. Click **Find location**, then choose the intended search result. A city result uses its center, so enter coordinates for a particular parcel.
3. Set facility MW, the conditional share of its load, the **assumed site exposure** slider, and a 1–7 year scenario term.
4. Click **Scan data & generate report**. The first run checks the utility map, downloads local historical weather, fits/evaluates a model and performs the seasonal simulation. Leave the workspace running; progress appears in the job panel. Successful downloads remain cached if a later stage fails.
5. Read the saved **Location exposure report**. Download its formatted HTML (also printable from your browser), Markdown, or full JSON evidence and results.

The report includes annual system and assumed-site P50/P90/P99 hours, joint contract totals, conditional-load energy exposure, historical high-probability hours with prior-hour local temperature and SPP load/wind/solar, seasonal and temperature comparisons, model evaluation, source dates and hashes, and missing inputs. Confidence is **Low** for all annual research scenarios.

## What the scan collects

| Input | Geographic scope | Source and use |
|---|---|---|
| Place coordinates | Selected city or exact user coordinates | Open-Meteo / GeoNames search; the user chooses the result |
| Utility territory | Polygons intersecting the selected point | [PNNL's public HIFLD service](https://eedgis.pnnl.gov/arcgis/rest/services/Hosted/Electric_Service_Territories/FeatureServer/0); historical screening, not a current interconnection study |
| Hourly temperature | ERA5 grid cell for the selected coordinates | [Open-Meteo historical weather](https://open-meteo.com/en/docs/historical-weather-api), joined by UTC hour |
| Load and wind/solar generation | Entire historical SPP system | Cached official SPP annual load and generation archives for 2019–2024; source URLs/hashes are retained |

The program accepts location searches anywhere, but this model applies to historical SPP conditions. A territory match to another grid produces an explanation and **no hours**. An inconclusive lookup requires the visible user assertion that the point belongs to the historical SPP footprint before a scenario can run. This assertion is saved in the report. The utility data is historical and approximate; new SPP territories and exact service/interconnection arrangements require separate work.

A new location receives its own local weather inputs and fitted model. The program removes the original Amarillo weather before joining the selected location's weather; it does not substitute new temperatures into the Amarillo model. Repeating the same coordinates with different facility assumptions reuses the fitted model and simulation. The cache key includes coordinates, base-data hash, relevant pipeline code hashes and model/simulation settings. Grid archives are shared across locations. Historical weather, geocoding and territory responses are cached in parquet.

## What the hours mean

The target remains the documented **high-demand stress proxy**: SPP load at or above the reference-period 95th percentile, estimated only from training-period load. This can capture stressed periods without an emergency declaration. It does not measure all kinds of network stress or establish a tariff's interruption trigger. The classifier uses past load, local temperature, their interaction, load ramps, SPP wind/solar and net load, and calendar features.

`assumed site hours = system proxy exposure hours × user site_exposure`

`conditional MWh = assumed site hours × facility MW × conditional share`

These are assumption-scaled clock hours, not measured cutoff duration. The conditional-MWh calculation assumes the full conditional portion is interrupted during the assumed site hours. Changing facility size changes the energy calculation; it does not simulate the added facility's impact on grid demand. Term quantiles are calculated from joint trials, not by adding annual quantiles.

The 2019–2024 history and ERA5 reanalysis support retrospective research and stationary seasonal scenarios. They are not live 2026 grid conditions or forecasts of future cutoff dates. The highest historical hours and hot-weather comparison show association, not causal attribution. Reserves, outages, constraints, imports, prices, local transmission headroom and a site's operating instructions remain missing from this fitted model. Confidence is Low; annual tails, label suitability and calibration still require review.

## Local interfaces

- `POST /api/jobs`, `kind=site-scan`, `query`: explicit geocoding job; `GET /api/site-scan?id=...` reads its saved candidates.
- `POST /api/jobs`, `kind=site-report`, `scan`, integer `candidate`, `load_mw`, `conditional_share`, `site_exposure`, integer `years`, optional boolean `confirm_spp`: explicit report job. Candidates are loaded from registered local search results, never accepted as arbitrary client coordinates or URLs.
- `GET /api/site-report?id=...` reads a completed report. `GET /api/site-download?id=...&file=SITE_REPORT.html` downloads a registered report artifact.
- Files: `data/processed/workbench/sites/`, `area_models/`, and `jobs/`; private/raw/model caches remain ignored by Git.

The existing loopback-only server, token/origin checks, one-job limit and safe subprocess argument lists apply. Opening or refreshing the workspace reads saved files only. The demo `/api` and `/web` interfaces are unchanged.
