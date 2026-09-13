# Kristian's local model workspace

Open **http://127.0.0.1:8765** on this computer.

To start it again later, double-click **Open ML Workspace.cmd** in the repository
folder. It starts the workspace in the background and opens your browser. Running
the launcher again reuses the existing service. You do not need to type Python
commands, activate the environment, install Node, or set up an API key.

## What you can do

1. **Explore the real data.** The prepared Amarillo example is selected initially.
   Inspect daily temperature and grid-load charts, hourly coverage, and the exact
   source and preparation records. Select another prepared dataset to compare it.
2. **Enter an area.** Enter a US city and state under Prepare an area, choose the
   original grid observations and grid ID, and click Prepare temperature data.
   The existing offline command resolves the city, reuses cached downloads, and
   creates a new temperature-enabled dataset. Its progress appears in the job log.
3. **Supply event labels.** Choose the prepared temperature dataset, select your
   reviewed event CSV, and provide its source and coverage reference. The download
   link gives you a header-only CSV template. No example labels are invented.
4. **Train and inspect.** Click Train a research model. The workspace runs the
   existing evidence join and chronological train/calibration/test workflow.
   After successful training, the saved run appears with evaluation, confidence,
   warnings, the full report, and download links for the model and test predictions.

The label CSV must contain exactly:

```csv
timestamp_utc,location_id,event_active
```

Use UTC hour-start timestamps with an explicit offset, the same grid ID as the
selected observations, and `1` for reviewed event hours, `0` for confirmed covered
non-event hours, or blank for unknown. Uploads are limited to 5 MB. They stay in this
repository; they are not sent to a hosted service. The weather provider receives
only city searches and coordinate/date requests when you explicitly prepare an area.

The first workspace training form uses the existing `observed_event` target.
The other explicit proxy targets remain available through
[the command-line workflow](ML_WALKTHROUGH.md). Neither temperature nor the workspace
changes the label rule, calibration, or simulation implementation.

## Current model status

Real SPP load and historical temperature data are available. **Reviewed matching
event labels are still missing, so there is no model trained on real event data
yet.** The workspace shows this state explicitly. Software tests create temporary
synthetic models solely to verify training and download behavior; those files are
removed after the tests and never displayed here.

The prediction target is system-level modeled exposure. A city's weather does not
establish a site's actual cutoff probability. Model outputs remain research results
pending label/calibration review and validation of their intended use.

## Files and execution

This is a local research authoring tool, separate from the team's demo at `/web`
and estimate API at `/api`. Page loads, charts and report views only read cached
files. Preparing data and training are explicit background jobs that invoke
`pipeline.workflow` outside the HTTP request. The demo app's read-only behavior and
existing response contract are unchanged.

| Location | Contents |
|---|---|
| `data/processed/ml_inputs/` | Existing prepared hourly datasets |
| `data/processed/workbench/inputs/` | New weather and evidence-joined datasets |
| `data/processed/workbench/jobs/` | Uploaded evidence, label policies and job logs |
| `data/processed/workbench/runs/` | Saved models, predictions and reports |
| `data/processed/workbench/server.log` | Startup output |
| `data/processed/workbench/server-error.log` | HTTP request log and server errors |

All these data folders are gitignored. The workspace never overwrites an existing
dataset or model. One job runs at a time, and closing the browser does not stop an
active job. Reopening the workspace reconnects to the running job. After restarting
the service, existing datasets and completed runs are rediscovered; prior job logs
remain in their folders.

The service listens only on `127.0.0.1:8765`. This URL is for this computer; it is not
a publicly hosted website. To run in a visible terminal instead of the background
launcher, use:

```powershell
.\.venv\Scripts\python.exe -m pipeline.workbench
```

Press Ctrl+C in that terminal to stop that foreground instance. If the browser says
the site is unavailable, run the launcher again. If a job fails, read its log, fix
the input and retry; successful weather downloads remain cached. Use Refresh files
to pick up files created by the command-line workflow while the page is open.

## Verification

All 40 Python pipeline/workspace tests pass. The workspace tests cover read-only
views, path restrictions, local request protection, rejected labels, single-job
execution, failed jobs, and disposable model training with preserved provenance.
A live HTTP job also prepared all 8,784 Amarillo temperature hours successfully
through the running service. The browser loaded the application, fonts and data
endpoints successfully; visual inspection was unavailable because browser control
was not connected in the development session.


Update (2026-09-13 UTC): the [multi-factor prediction model](MULTIFACTOR_PREDICTION.md) now uses real 2019?2024 load, temperature, wind and solar data to predict a high-demand stress proxy. Saved expected hours, annual scenarios and an explicit site-exposure slider are available in the local ML workspace. This is separate from the earlier emergency-only evidence catalog.
