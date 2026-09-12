# Flexible Interconnection Underwriting Tool

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

## Running it

```bash
pip install -r requirements.txt
python -m pipeline.ingest      # pull + cache grid data to parquet
python -m pipeline.label       # build the curtailment-trigger label set
python -m pipeline.train       # train + calibrate the model
uvicorn api.main:app --reload  # serve
cd web && npm install && npm run dev
```
