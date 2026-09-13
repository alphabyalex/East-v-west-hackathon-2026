# Fluxline

**East v West Hackathon — Energy, Grid & Sustainability**

## The problem

AI data centers need enormous amounts of electricity and the grid cannot connect them
fast enough. A normal "firm" connection takes years.

So grid operators have just started offering a different deal: connect much sooner, but
accept that the operator can cut your power whenever the system is strained. SPP's
version of this (CHILLS) went live on **July 1, 2026**, lasts up to **seven years**, and
places **no cap on curtailment hours**. In June 2026 FERC ordered all six US grid
operators to justify or reform their large-load rules, and their responses landed
through August and September.

The problem: **the contracts do not say how often you will be cut off.** So a company
facing a multi-million-dollar, multi-year commitment has no way to size the risk. Today
that question is answered with a law firm memo and a spreadsheet, or not at all.

## What we built

Enter a location, a load size, and how much of your compute can pause or shift. The tool
returns:

- **Modeled exposure** — how often the grid at that location was under a
  curtailment-triggering condition, from years of public grid data
- **A forward distribution** over the contract term, not a point estimate
- **Economics** — what that exposure costs in lost compute and dollars
- **The break-even point** where connecting early stops being worth it
- **Traceability** — click any number to see the contract clause, dataset, or
  assumption behind it

## The ML layer

The local estimator uses a [model trained across multiple areas](docs/WARNING_SIGNS.md). Its main screen shows location/facility inputs and estimated hours; detailed model evidence is retained in downloads.

**Estimate hours for your location:** open `Open ML Workspace.cmd`, then visit http://127.0.0.1:8765. Enter a city/state or parcel coordinates, facility assumptions and years, then select **Estimate hours**. Matching locations are resolved automatically unless a choice is needed. Read the annual site hours at the top of **Your estimate**. See [Location reports](docs/LOCATION_REPORTS.md) for the workflow and interpretation. Results are modeled exposure to high demand, with Low confidence; actual site cutoffs and future dates are not established.

The contracts are deliberately vague. SPP's says curtailment happens "when the
transmission system is constrained or under emergency conditions." No number can be
derived from that sentence, which is exactly why these deals are unpriceable today.

So we do not read the contract for the answer. **We learn it from behavior** — training
a calibrated classifier on historical grid conditions against the record of when
operators actually declared emergencies and curtailed load.

> The contract won't tell you when they'll cut your power. So we learned it from what
> they actually do.

A second AI layer reads the operators' filed tariff documents and extracts the
curtailment conditions into a comparable structure with source citations.

A third layer answers a question most tools like this skip: **how much should you
trust the number above?** We train the classifier as an ensemble and measure how much
the members agree, plus how much historical precedent actually exists for a given
grid state. That becomes a plain confidence read — high, medium, low — attached to
every exposure figure. We're not just honest about what we can't know; we quantify
how much to lean on what we do report.

## What we do not claim

We model **system-level** grid stress. We cannot prove a specific site would have been
curtailed, because that depends on local transmission headroom that is not public. That
mapping is an explicit user-set assumption, shown on screen as a slider.

This is deliberate. The useful question is not "what is the exact number" but "what
would flip this decision" — and that we can answer honestly.

## Why it matters

There are two ways to connect all this new AI demand: build new power plants, usually
gas, or let the new loads be flexible so they back off when the grid is strained.
Flexibility is the option that avoids the buildout, and it is why FERC pushed these
products into existence. Almost nobody signs up, because the risk is unpriced, so they
demand firm power instead and a plant gets built.

The thing blocking the cleaner path is an unpriced risk. We price it.

## Stack

Python · DuckDB · LightGBM · FastAPI · Vite + React + Tailwind
Data: `gridstatus`, EIA-930, Open-Meteo, FERC eLibrary. All free, no credentialing.

## Running the current checkpoint

The API attempts to read `pipeline.simulate.get_location_estimate()` and its
precomputed parquet, with explicitly labeled placeholders while those files are
absent. Real SPP 2024 load ingestion has succeeded, but missing reviewed event labels
currently block training and exposure output. Economics reads `docs/ASSUMPTIONS.md`:
its source-backed scenario defaults are mixed with an explicitly unverified margin
assumption. The revenue derivation is gross revenue; it is not observed net margin.
The pipeline, calibration, and tariff descriptions above are the intended full
product; they are not a claim that real outputs are already available.

For Kristian's implemented offline ML workflow, start with the
[local browser workspace](docs/ML_WORKSPACE.md): double-click **Open ML Workspace.cmd**
and open **http://127.0.0.1:8765** to enter a location and generate a modeled-exposure
estimate. For manual preparation and reviewed event-label training, follow the
[step-by-step walkthrough](docs/ML_WALKTHROUGH.md). It covers the local Python
environment, real SPP load preparation, required event labels, training, evaluation,
and the experimental simulation. Use `python -m pipeline.workflow --help` for its
commands. The walkthrough explains the remaining ML data and review requirements;
the commands below run the current mock UI and API. To enter a city/state and use
its historical weather in training, see [temperature inputs](docs/TEMPERATURE_DATA.md).

Install Node.js 22.12+ and Python 3.11+, then use two terminals:

```sh
python -m venv .venv
# Activate .venv (Windows: .venv\Scripts\activate; macOS/Linux: source .venv/bin/activate)
python -m pip install -r api/requirements.txt
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

```sh
cd web
npm ci
npm run dev
```

Open Vite's printed URL. The UI calls the local API by default and visibly falls
back to local mocks if it is unavailable. **Local mock** works without the backend;
`VITE_ESTIMATE_MODE=local` makes that the startup mode. Direct browser calls to
`http://127.0.0.1:8000/api/estimate` are also allowed from `http://127.0.0.1:5174`
through CORS. This is the frontend's default direct connection; set
`VITE_API_BASE_URL=` for a same-origin proxy setup. The UI also reads
`GET /api/economics-assumptions` so economic controls match the server's file-backed
values and provenance. Neither path trains,
simulates, or fetches external grid data during the demo.

See [web/README.md](web/README.md) for interactions and frontend checks,
[api/README.md](api/README.md) for backend checks and the pipeline adapter,
and [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md) for the shared contract and deadlines.

Historical SPP cases, the load/temperature event join, and CHILLS rule interpretation
are in [docs/SPP_EVENT_AND_CHILLS_ANALYSIS.md](docs/SPP_EVENT_AND_CHILLS_ANALYSIS.md).


Update (2026-09-13 UTC): the [multi-factor prediction model](docs/MULTIFACTOR_PREDICTION.md) now uses real 2019?2024 load, temperature, wind and solar data to predict a high-demand stress proxy. Saved expected hours, annual scenarios and an explicit site-exposure slider are available in the local ML workspace. This is separate from the earlier emergency-only evidence catalog.
