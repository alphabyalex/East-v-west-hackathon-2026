# AGENTS.md — instructions for AI coding agents working in this repo

Read this file before writing any code. It is the contract.

## What this project is

A decision tool for companies deciding whether to accept a **flexible (non-firm) grid
interconnection** for a large electrical load, typically an AI data center.

The deal on the table: get connected to the power grid years sooner, in exchange for
agreeing that the grid operator can cut your power whenever the system is strained.
This is called **curtailment**. The contracts do not say how often it will happen.

We answer: is that trade worth it, and what would change the answer.

## Team and branch map

Four builders (three people + Claude directing/auditing), three active branches. This
is a **general** division of labor, not a hard wall — see the note at the bottom.

| Who | Branch | Primary focus | Produces |
|---|---|---|---|
| Alex (+ you, Codex) | `Alex` | Backend, wiring, infra — `/api`, glue between pipeline output and frontend, deployment/local-run plumbing | FastAPI endpoints, the contract that defines what shape data moves between `/pipeline`, `/api`, and `/web` |
| Kristian | `Kristian` | ML — `/pipeline`: ingest, label, features, train, confidence, simulate | Cached parquet, the label set, the trained+calibrated ensemble model, the confidence estimate, Monte Carlo output |
| Tharun | `Tharun` | Frontend — `/web` | The UI: charts, the assumption sliders, the provenance/confidence badges, the visual design |
| Claude (director/AI) | commits to `Alex`, merges to `main` | Architecture, code review/audit across all three areas, unblocking whoever's stuck, `/extract` (tariff LLM extraction) | Review notes in `docs/FROM_CLAUDE.md`, `extract/tariff.py`'s `tariffs.json`, ad hoc pipeline/backend code where it helps Kristian or Alex move faster |

**Reality check — we will bleed into each other's areas, and that's fine:**
This is a 72-hour build with 3 people, not a company with clean team boundaries. Alex
may end up writing pipeline code if Kristian's blocked; Kristian may need to touch the
API contract to match what the model actually outputs; Tharun may need real data
earlier than planned and pull straight from `/pipeline` output himself; Claude may
write backend or ML code directly rather than just reviewing it. **Don't treat the
table above as permission to block on "that's not my branch."** If you can unblock
something faster than waiting for the owner, do it, then flag it in the relevant
outbox file so the actual owner knows what changed in their area.

**What this means for you (Codex) concretely:**
- The ML outputs (`modeled_exposure`, `confidence`, the distribution) are **coming from
  Kristian's work on `Kristian`**, not from you and not automatically from Claude either
  now — build the frontend against the mock shape in the frontend API contract, and
  don't wait on his branch to start.
- The economics numbers (GPU-hours-to-dollars, break-even, GPUs-per-MW, rental prices)
  land as `docs/ASSUMPTIONS.md`, sourced separately (see `docs/DATA_NEEDED.md`) —
  whoever ends up owning that, keep economics mocked in the frontend until that file
  exists on `main`, and swap in real numbers the moment it does.
- You will not see commits appear on `Kristian` or `Tharun` in your own history unless
  you explicitly fetch/merge `main` — check `main` periodically for merges from their
  branches, don't pull their branches directly (per the branch policy below).
- If you're blocked because a number you need isn't sourced yet, don't guess a fake
  precise value — use a round, obviously-placeholder number (matching the pattern in
  `docs/DATA_NEEDED.md`'s "develop against dummy values" note) and flag it in your
  `docs/FROM_CODEX.md` entry so it gets swapped before the final demo.
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
- Every modeled exposure number also carries a **confidence estimate** (see below) —
  we don't just admit what we can't establish, we quantify how much to trust what we
  did establish

Any code or copy that implies false precision is a bug. Flag it, do not ship it.

## Model confidence estimate (new, second ML component)

We don't just output "modeled exposure: p50/p90/p99." We also output **how much to
trust that number**, computed from the model itself:

- **Ensemble disagreement:** the trained model is actually an ensemble (bootstrap
  resamples / varied seeds, ~15-25 members). The spread across ensemble predictions
  at a given grid state is the confidence signal — tight agreement = high confidence,
  wide disagreement = low confidence.
- **Data density (if time allows):** a nearest-neighbor distance in feature space to
  the training set — how many historical hours actually looked like this one. Thin
  precedent = lower confidence even if the ensemble happens to agree.
- Surfaced as a plain **High / Medium / Low** badge next to every modeled exposure
  figure, with the number behind it available on click (same provenance pattern as
  everything else: `{value, source_type: "model", ref: "ensemble spread, n=..."}`).

**Critical framing, same rule as `site_exposure`:** this is confidence in the
*estimate*, not a probability that the future matches it. Never write copy like "95%
confident this will happen" — the correct framing is "this estimate is well-supported
by historical precedent" vs. "we've seen very few situations like this one, treat this
number loosely." If a teammate or Codex drafts copy that blurs that line, it's a bug.

This lives in a new `pipeline/confidence.py`, downstream of `train.py`, and its output
travels alongside every exposure number through `simulate.py` and the API. Frontend:
this is a second axis next to the site_exposure slider — expect a `confidence` field
on the same API response as `modeled_exposure`.

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

This has to look like professional financial/trading software, not a hackathon demo
and not a generic AI app. Judges will see a hundred purple-gradient chatbot UIs this
weekend. Ours needs to look like it belongs on a trading desk.

**Reference bar — study these, don't imitate any one exactly:** Coinbase Institutional
/ Coinbase Prime, Jane Street's site, Optiver's site, Millennium's site, Palantir's
product UI (Foundry/Gotham marketing pages and product screenshots). What they share:
restraint, real information density, confident whitespace used structurally (not
decoratively), typography as the main design tool, muted/near-monochrome palettes
with one precise accent color used sparingly, and zero decoration that isn't load-
bearing. Nothing about them is trying to look "friendly." It's trying to look correct.

**Ban list — these are the tells of an AI-generated frontend. None of them appear
anywhere in this product:**
- Purple-to-blue (or pink-to-orange, etc.) gradient backgrounds or gradient text,
  anywhere, on anything
- A hero section with a giant centered headline, a subheadline, and a centered CTA
  button, especially over a blurred gradient blob or abstract illustration
- Generic 3D-rendered blob / glass-morphism / floating-orb illustrations
- Rounded cards everywhere, all the same radius, each with a soft drop shadow and a
  single accent-colored icon at the top — the "SaaS landing page" card grid
- Emoji used as icons or section markers instead of a real icon set
- Bento-grid layouts used decoratively rather than because the content actually
  groups that way
- Overly rounded buttons/pills with a glow or gradient fill
- Feature sections structured as "icon + 3-word bold title + 1-sentence description"
  repeated in a 3-column grid
- Fake social proof (logo walls, star ratings, "trusted by" banners) — we have none
  and won't fabricate any
- Inter/Poppins-and-a-gradient as the entire visual identity — pick a typeface and
  a system that does actual work (tabular numerals, real hierarchy, a monospace for
  data), not a font choice as decoration
- Excessive whitespace used to hide the fact that there isn't much real content —
  ours has real content, so let it be dense

**What we do instead:** dark, instrument-like, dense. Numbers are the hero — tabular
figures everywhere a number appears, real units, no rounding that hides precision
unless labeled. Charts get real axes, gridlines, and hover states, not smoothed
decorative sparkline art. Layout is driven by the data's actual structure, not a
template. One accent color, used only for the thing that matters most on screen (the
decision readout, the slider you're dragging) — never for decoration.

Assumptions must always be visible and adjustable, never buried in a settings panel.
The most important interaction in the whole product is changing an assumption and
watching the decision flip. If a screen looks impressive but a judge can't find the
lever that changes the number, it has failed regardless of how polished it looks.

**3D and motion — yes, but as instrumentation, not decoration.** This can and should
feel impressive. The difference between "sexy" and "slop" is whether the 3D/motion
*represents real data* or is just there to look cool.

Good uses of 3D, if you have time for them:
- A 3D surface plot of the Monte Carlo exposure distribution over the contract term
  (time x exposure-hours x probability density) — this is a real, information-dense
  chart that happens to look striking, not decoration bolted onto a 2D page
- An interactive node/network graph of the SPP grid topology (nodes = interconnection
  points, edges = transmission lines), where hovering or selecting a node updates the
  main panel — real navigation, not a spinning logo
- Depth and layering used to show causality/hierarchy (e.g. a contract clause "casting
  a shadow" onto the number it produced) — subtle, purposeful, not a hero animation
- Smooth, physically-plausible transitions when the exposure slider moves and every
  downstream number/chart recomputes — motion that shows the model is live and
  connected, not an entrance animation

Still banned regardless of 3D: floating glass orbs, blurred gradient blobs, generic
low-poly/particle-field backgrounds, anything spinning purely for visual interest with
no data behind it, and any 3D element that exists only on a hero/landing area rather
than inside the working tool. If you can't explain what a 3D element represents in one
sentence, cut it.

If in doubt on any specific choice: would this be at home on a Jane Street or
Palantir product screen? If not, it's slop — redo it.

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
