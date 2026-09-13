# Enter an area and use its temperature in the ML pipeline

The offline pipeline accepts a city and state, resolves its coordinates, downloads
historical hourly air temperatures, and joins them to the grid observations.
The LightGBM model then has temperature history available during training.

You can now use a browser: double-click **Open ML Workspace.cmd** in the repository,
then enter your area at **http://127.0.0.1:8765**. See the
[workspace guide](ML_WORKSPACE.md). The command-line steps below remain supported.

## Access it from PowerShell

```powershell
Set-Location 'C:\Users\krist\Hackathon\East-v-west-hackathon-2026'
.\.venv\Scripts\python.exe -m pipeline.workflow --help
```

You do not need to activate the environment. Calling its Python directly uses the
packages already installed here. This is the ML command line; the current website
and API still serve the team's explicitly labeled mock results.

## 1. Enter your area

Run these lines. PowerShell will ask for your city and state:

```powershell
$weatherArea = Read-Host 'City and state, for example Amarillo, TX'
.\.venv\Scripts\python.exe -m pipeline.workflow add-temperature --hourly data/processed/ml_inputs/spp_2024_load_only.parquet --area "$weatherArea" --out data/processed/ml_inputs/spp_2024_selected_area_temperature.parquet
```

Or pass the area directly, for example `--area "Amarillo, TX"`. Both state
abbreviations and full state names work. This first version supports US cities.
An ambiguous or unknown city fails with candidate names; it never silently selects
the largest city or ignores the state. Specify a new output filename for a different
area or experiment. Existing outputs are preserved.

The command:

1. Resolves the area using Open-Meteo's GeoNames geocoding service.
2. Saves the selected coordinates and their association with the grid dataset in
   a sibling `.area.json` file.
3. Downloads ERA5 hourly 2-m air temperature in Celsius, using UTC timestamps.
4. Matches weather to each grid hour and writes a new parquet file plus a sibling
   `.weather.json` manifest containing source URLs, coordinates, weights and hashes.

The current load table contains only `SPP_SYSTEM`, so that ID stays unchanged.
The city selects a **weather input**. It does not create city-level grid observations
or verify that a site is served by SPP. Choose weather appropriate to your grid
dataset and have the area association reviewed. For a file containing multiple grid
IDs, use `--location-id YOUR_GRID_ID` to select one; only that ID is written to the
new output. No weather is silently substituted for an unmapped location.

Downloads are cached in `data/raw/weather/geocoding/` and
`data/raw/weather/open_meteo/`. Subsequent requests reuse the parquet cache.
This version accepts completed calendar years from 1940 onward. It deliberately
does not cache a partial current year as though it were complete.

## 2. Inspect the prepared data

```powershell
.\.venv\Scripts\python.exe -m pipeline.workflow inspect --hourly data/processed/ml_inputs/spp_2024_selected_area_temperature.parquet
```

A real example has already been prepared on this machine:

```powershell
.\.venv\Scripts\python.exe -m pipeline.workflow inspect --hourly data/processed/ml_inputs/spp_2024_amarillo_temperature.parquet
```

That example resolves Amarillo, Texas to latitude 35.222, longitude -101.8313.
All 8,784 grid hours have matching temperatures. The original three unknown load
hours remain unknown. The input spans 2024-01-01 06:00 UTC through 2025-01-01
05:00 UTC, so the weather cache includes both completed calendar years 2024 and
2025. These are data-quality results, not model performance or risk estimates.
Amarillo is a workflow example, not an inferred user location.

## 3. Add event evidence and train

Temperature is a predictor; it does not tell us which hours were actual grid
events. Obtain reviewed hourly event/proxy evidence and fill the policy's source
references as described in [the ML walkthrough](ML_WALKTHROUGH.md#3-add-the-evidence-that-defines-a-positive-hour).
Once those files exist, use the temperature-enabled input in the same workflow:

```powershell
.\.venv\Scripts\python.exe -m pipeline.workflow join-evidence --hourly data/processed/ml_inputs/spp_2024_selected_area_temperature.parquet --evidence data/raw/spp/reviewed_events.csv --out data/processed/ml_inputs/spp_2024_weather_labeled.parquet
.\.venv\Scripts\python.exe -m pipeline.workflow train --hourly data/processed/ml_inputs/spp_2024_weather_labeled.parquet --policy docs/ml-policy.local.json --run-dir data/processed/ml_weather_run_01
```

`reviewed_events.csv` and the completed local policy are prerequisites; this task
has not fabricated those files. The evidence join preserves temperature columns
and their source reference. Training records that reference and the temperature
feature policy in the model card.

After successful training, access the saved outputs in
`data/processed/ml_weather_run_01/`:

| File | Purpose |
|---|---|
| `model.joblib` | Saved ensemble and calibrators, used by Python |
| `REPORT.md` | Readable held-out evaluation |
| `model_card.json` | Scores, feature importance, source references and settings |
| `test_predictions.parquet` | Hourly probabilities for the held-out period |
| `features.parquet` | Inputs used by the model, including temperature history |

No model has yet been trained on real event labels. Software tests train temporary
models on explicitly synthetic labels and remove them afterward. Those models and
their scores never populate the real-data cache or website.

## What temperature changes

The model receives temperatures from 1, 24 and 168 hours earlier, the previous
24-hour mean and standard deviation, the previous 24-hour minimum and maximum,
and the change between one hour ago and 25 hours ago. Every feature ends before
the target hour. Missing observations remain missing; there is no forward filling
or future interpolation.

Trees can learn different relationships for hot, mild and cold conditions, together
with load and the other available predictors. There is no hardcoded temperature
threshold or fixed increase in cutoff probability. Whether temperature improves
real predictions must be measured with reviewed labels and a held-out backtest.
Adding it to an existing trained model requires a new training run.

Historical reanalysis incorporates information assembled after the weather
occurred. Even prior-hour reanalysis features do not prove the inputs were available
at a historical prediction time. This integration supports retrospective research;
a live forecast requires weather with verified publication times or an as-issued
forecast archive. This command does not fetch current weather during an API/demo
request and does not add a future climate trend to the annual simulation.

The target remains system-level modeled exposure. A weather point does not supply
local transmission headroom or establish a particular site's actual power cutoff.
The existing downstream `site_exposure` assumption remains separate.

## Advanced: coordinates or several weather points

Instead of `--area`, use `--locations path/to/your-weather-locations.json`.
[weather-locations.json](weather-locations.json) shows the format with six regional
points. Its equal weights and point selection are explicit prototype assumptions,
not official SPP boundaries or load weights. That regional example is optional and
is not selected automatically.

For a site whose coordinates you already know, use one point under the existing
grid dataset ID, with positive weight 1. For a regional proxy, supply several
points and positive weights. The command adds a weighted mean plus the minimum and
maximum across those points. An hour is unknown if any point is missing, keeping
the area definition stable. For a single point, all three columns have the same
temperature. Changing names or weights reuses cached coordinate/year downloads.

## Sources and verification

- [Open-Meteo historical weather API](https://open-meteo.com/en/docs/historical-weather-api)
- [Open-Meteo geocoding API](https://open-meteo.com/en/docs/geocoding-api)
- Saved `.area.json`, `.weather.json`, and raw `.source.json` files record the exact
  request parameters and returned coordinates for each local dataset.
- Run `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` to check the
  pipeline. Weather tests cover ambiguity, UTC alignment, leap years, cache reuse,
  missing inputs, provenance, prior-hour feature timing, and synthetic heat/cold
  learning. The synthetic thresholds in those tests are not production rules.


Update (2026-09-13 UTC): the [multi-factor prediction model](MULTIFACTOR_PREDICTION.md) now uses real 2019?2024 load, temperature, wind and solar data to predict a high-demand stress proxy. Saved expected hours, annual scenarios and an explicit site-exposure slider are available in the local ML workspace. This is separate from the earlier emergency-only evidence catalog.
