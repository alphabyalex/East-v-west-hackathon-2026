# AGENTS.md — instructions for AI coding agents working in this repo

Read this file before writing any code. It is the contract.

## What this project is

A decision tool for companies deciding whether to accept a **flexible (non-firm) grid
interconnection** for a large electrical load, typically an AI data center.

The deal on the table: get connected to the power grid years sooner, in exchange for
agreeing that the grid operator can cut your power whenever the system is strained.
This is called **curtailment**. The contracts do not say how often it will happen.

We answer: is that trade worth it, and what would change the answer.

## The single most important rule

**We never claim to know a specific site's actual curtailment hours.**

Public data can establish when the grid *as a system* was under stress. It cannot
establish whether a hypothetical load at a specific point of interconnection would
have been cut off, because that depends on local transmission headroom we do not have.

Therefore:
- All model output is labelled **"modeled exposure"**, never "curtailment hours"
- The site-specific mapping is a **user-set assumption** (`site_exposure`, 0.0–1.0)
  exposed as a visible slider in the UI, never a hidden constant
- Every displayed number must be traceable to a clause, a dataset, or an assumption

Any code or copy that implies false precision is a bug. Flag it, do not ship it.

## Architecture

```
/data/raw          grid data pulled from APIs, parquet, gitignored
/data/processed    feature table + labels, parquet
/data/tariffs      downloaded RTO filing PDFs
/pipeline          ingest -> label -> features -> train -> simulate -> economics
/extract           LLM tariff extraction, outputs tariffs.json with citations
/api               FastAPI, serves precomputed results
/web               Vite + React + Tailwind frontend
/docs              specs
```

## Hard constraints

1. **Precompute everything heavy.** Model training, backtests and Monte Carlo runs
   write to parquet offline. The API and UI read results only. Nothing trains or
   fetches from an external API during a demo. This is non-negotiable — the demo
   must be instant and must work with no network.
2. **Cache every external pull to parquet on first fetch.** Never re-hit `gridstatus`
   or EIA for data we already have. Rate limits will bite otherwise.
3. **No secrets in the repo.** API keys via `.env`, which is gitignored.
4. **Deterministic where possible.** Seed every random process. A demo that produces
   different numbers on each run is not credible.
5. **Every number in the UI carries provenance.** A `source` field travels with each
   value: `{value, source_type: "data"|"clause"|"assumption", ref}`.

## Scope discipline

- **One grid operator (SPP) until the core is finished.** Do not add ERCOT, PJM or
  CAISO until the SPP path is complete end to end. Depth beats breadth.
- Do not add authentication, user accounts, databases, or deployment infrastructure.
- Do not refactor working code for elegance. This is a 72-hour build.

## What AI agents should and should not touch

**Good tasks to take on:**
- React components, layout, styling, charts, interaction polish
- Data cleaning and parsing utilities
- Tests
- API endpoint plumbing
- Documentation

**Do not modify without a human reviewing it closely:**
- `pipeline/label.py` — defines which historical hours count as a curtailment
  trigger. Everything downstream depends on it. A subtly wrong label set produces
  confident garbage that is very hard to detect.
- `pipeline/train.py` calibration logic — the calibration is a core credibility
  claim, not a formality.
- Any copy or label that states what the model knows or does not know.

## Frontend direction

Dark, instrument-like, dense. This is a professional risk tool, not a consumer app.
Think trading terminal rather than SaaS landing page. Numbers are the hero; use
tabular figures. Charts get real axes, real units and hover states.

Avoid the generic AI-app look: no purple-to-blue gradient hero, no giant centered
headline over a stock illustration, no rounded cards everywhere with one accent bar.

Assumptions must always be visible and adjustable, never buried in a settings panel.
The most important interaction in the whole product is changing an assumption and
watching the decision flip.

## Definition of done for any task

- It runs
- It uses real data, not placeholders
- Its numbers carry provenance
- It does not overstate what we can establish

## Working with Claude (the director)

Claude (Alex's other AI, working in `/pipeline`, `/extract`, and the ML core) and Codex
are working the same repo in parallel with no live channel to each other. We talk
through files. Two rules make this work:

1. **Never edit this file (AGENTS.md).** It's the shared contract, not a chat log. If
   you edit it, whoever reads it next can't tell what's a real instruction from Alex
   versus something you added to leave yourself a note. If something here seems wrong
   or outdated, say so in your outbox file (below) instead of changing it.
2. **Each side writes only to its own outbox file.** No shared file both sides append
   to — that's how entries get lost or overwritten mid-task.

**Your outbox: `docs/FROM_CODEX.md`.** Create it if it doesn't exist. Append (never
overwrite) an entry whenever you finish a task or hit something Claude needs to know:

```
## [what you did] — (timestamp or commit hash)
- Changed: ...
- New/changed interface: ... (e.g. the exact JSON shape an endpoint expects/returns)
- Needs review: ... (anything a human or Claude should double check)
- Blocked on: ... (only if true)
```

**Claude's outbox: `docs/FROM_CLAUDE.md`.** Read this at the start of every task, same
as AGENTS.md. It'll have pipeline/API interface changes, data quirks, and anything
that affects what you're building.

Keep entries short — this is a shift-change log, not a diary.

**Repo and git ownership:** Codex owns git for this repo.

Repo: https://github.com/alphabyalex/East-v-west-hackathon-2026

If it isn't initialized yet: `git init`, add this repo as `origin`, and push `main`.
Create branches named `Alex`, `Kristian`, and `Tharun` off main and push them once
so they exist on GitHub for the other two teammates — but **do not push further
commits to `Kristian` or `Tharun`**. Those branches belong to those teammates; they
push to their own. **Your commits go to `Alex` and, when something is ready to share
across the team, `main`.** Never push to `Kristian` or `Tharun` after their initial
creation.

If `gh` is authenticated locally, also add `Tharun.ekam@gmail.com` and
`skbridge04@gmail.com` as collaborators (`gh api
repos/:owner/:repo/collaborators/<username-or-email> -X PUT -f permission=push`; if
that fails because GitHub needs a username, not an email, say so in `docs/HANDOFF.md`
rather than silently skipping it, so it can be done manually from the GitHub UI).
Commit early and often — small commits, real messages, no giant end-of-day dumps.
