# Project checkpoint — September 13, 2026

Saved before switching to the separate [Humbaba04/EastVSWestOptica2](https://github.com/Humbaba04/EastVSWestOptica2) project. No new-project requirements have been supplied yet.

## Resume this project

- Repository: https://github.com/alphabyalex/East-v-west-hackathon-2026
- Working branch: **Kristian**. The user's publication instruction is to push to Kristian, never main.
- Latest implementation commit before this checkpoint: `5dfadfc` (SPP regional location acceptance and nearby weather fallback).
- Open **Open ML Workspace.cmd** from the repository to start/reopen the local application at http://127.0.0.1:8765.
- Before the switch, the working tree was clean and GitHub's Kristian branch matched the implementation commit. The latest local workspace job completed successfully; 17 report files and three regional model files were present.

## What is saved where

Code, tests, documentation and selected example summaries are committed to GitHub. Trained models, raw downloads, generated reports and the Python environment are saved **locally**, in ignored paths; they are not included in a fresh GitHub clone.

- `data/processed/workbench/sites/`: completed reports, inputs, weather matches and generated predictions. Each completed report has `site_report.json`, `SITE_REPORT.html` and `SITE_REPORT.md`.
- `data/processed/workbench/regional/`: reference-area inputs and trained regional model caches.
- `data/processed/workbench/area_models/`: earlier site-specific model runs, where present.
- `data/processed/workbench/jobs/`: saved job requests/logs.
- `data/processed/ml_inputs/`: assembled historical model inputs.
- `data/raw/`: cached SPP, weather, city-directory and geography downloads with provenance.
- `.venv/`: existing local Python environment.

Keep these directories when moving or backing up the project. A normal computer restart preserves them. The model and saved reports do not need to be regenerated merely because the application was closed.

## Current product behavior and user preferences

The app has a simple **input → estimate** flow: location, facility MW, flexible share, site-exposure assumption and years. Warning-sign explanations are not displayed on the main screen. Existing evidence and model diagnostics remain in the full artifacts.

The regional model compares representative local weather with historical SPP demand, wind and solar and their derived features. It learns separate high-demand proxies for four reference load areas. Results are **modeled exposure**, with Low confidence, not actual interruption records or future cutoff dates.

Site exposure scales modeled regional hours by a visible user assumption. Flexible share and facility MW scale the energy exposed; they do not change the underlying stress prediction. The default 100% site exposure assigns all modeled regional hours to the site, without claiming every hour will cause an interruption.

Location matching now permits broad SPP regional comparisons and nearby areas within 100 km, including documented western utility regions. A national Census directory supplements city lookup. Unavailable/incomplete local weather triggers bounded fallback to real nearby histories. The original requested city and actual weather match remain in the report. Western estimates use historical SPP East patterns as an analogy; they are not trained on the 2026 western expansion.

## Verification and handoff references

The latest implementation passed 80 Python tests and 10 JavaScript behavior checks. Representative location checks covered all 17 SPP states. Full real-data reports and live downloads were verified for Colorado Springs, Page, Wamego and Fort Collins. A simulated weather-service outage successfully reused real nearby cached weather. Models, labels and calibration were not changed by the location fix.

- [Application instructions](ML_WORKSPACE.md)
- [Regional coverage and nearby-weather policy](SPP_LOCATION_COVERAGE.md)
- [Regional model methodology](WARNING_SIGNS.md)
- [Detailed engineering handoff](FROM_CODEX.md)
- [Repository instructions](../AGENTS.md) and [Claude's notes](FROM_CLAUDE.md)

When returning, check Git status and the latest workspace job before starting new work; additional user-created reports may exist after this checkpoint.
