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

**Reference bar, updated — two families, blended:** Coinbase Institutional/Prime,
Jane Street, Optiver, Millennium, and Palantir's product UI still govern density,
honesty, and restraint. Add **Coinbase's main consumer app and Robinhood** for
polish and confidence: bold hero numbers with real scale contrast (the headline
figure is dramatically bigger than supporting text, not everything one size),
buttery-smooth eased transitions when values change (slider drag, recompute — think
Robinhood's chart animations, not an instant snap), a more considered color rhythm,
and generous-but-purposeful spacing between sections. The first pass over-indexed on
severe/dense and read as unfinished rather than serious. Borrow the institutional
side's rigor (real data density, honesty labeling, no fake friendliness) and the
consumer side's craft (motion, hierarchy, confident typographic scale) — not either
one alone. Nothing about it should look "friendly" in tone, but it should feel
polished and deliberate rather than cold and unfinished.

**Honesty labeling — be quiet about it, not loud.** The per-value provenance system
(the small clickable source tags) is core and never gets removed or watered down —
it's the actual product differentiator. But large persistent top-of-screen badges
("ILLUSTRATIVE DATA" pills, big "MOCK ECONOMICS" banners) read as an unfinished
prototype to a judge glancing at the screen, even though the underlying honesty is
correct. Fix the presentation, not the truth:
- Genuinely fake/placeholder data (economics, confidence, tariff citations right now)
  stays clearly marked, but as a smaller, integrated label near the affected numbers
  rather than a shouting banner — and the real fix is landing Kristian's real pipeline
  data and sourced `docs/ASSUMPTIONS.md` numbers so these labels legitimately go away
  because the data earned it, not because we hid something. Do not remove a
  mock/assumption label from data that is still actually fake.
- Permanent, accurate caveats that are core to the pitch — `site_exposure` being a
  user-set assumption, a location having no site-specific grid data — stay exactly as
  prominent as they are now. These aren't bugs to hide, they're the honesty layer that
  wins the hardest Q&A question. Don't confuse "this looks unfinished" with "this is
  the whole point of the product."

**Typography and color — concrete, not vibes.** The first pass used default-feeling
type and no real palette, which read as generic regardless of layout discipline. Fix:

*Type system (three fonts, each doing a distinct job — load via Google Fonts or a
self-hosted equivalent, not a system-ui fallback):*
- **Brand/headline serif: Fraunces.** Used sparingly — the product name, section
  titles, maybe the decision readout. This is what gives the product a distinct
  identity instead of reading as another Inter-everywhere SaaS tool. A serif brand
  mark against an otherwise technical UI is a deliberate, specific choice — not
  decoration.
- **UI/body sans: IBM Plex Sans.** Labels, copy, controls. Chosen because it has real
  institutional pedigree (IBM's own enterprise type system) and is specifically NOT
  one of the handful of fonts every AI-generated site defaults to (Inter, Poppins,
  Manrope, Space Grotesk-as-body). If Plex Sans is unavailable for some reason,
  Söhne/Suisse-style alternatives are fine — just not Inter or Poppins as the primary.
- **Data/numbers monospace: IBM Plex Mono** (or JetBrains Mono). Every number in a
  table, chart axis, or metric tile uses this with real tabular alignment — this is
  what makes dense numeric panels actually scannable, not just stylistically "terminal."

*Color system (dark-first, one confident accent, not a rainbow):*
- Background base: near-black charcoal, not pure `#000` (reads cheap) — around
  `#0A0B0D`, with panels one step lighter (`#14161A`) separated by hairline borders
  (`#262930`), never drop shadows.
- Primary text: warm off-white, `#EDEBE6` — avoid stark pure-white on pure-black,
  it's harsh at data density.
- **Primary accent: a confident amber/gold** (`#D98E2B` range) — used ONLY for the
  single most important thing on screen at any moment (the decision readout, the
  slider handle, an active state). This is deliberately NOT blue or purple — it reads
  as instrument/terminal (think analog gauges, Bloomberg-style amber-on-black) rather
  than generic tech-startup blue.
- **Secondary accent: a muted steel-teal** (`#5C8A86` range) — for a second data
  series or a secondary interactive element only. Primary and secondary accents never
  blend into a gradient between them — that recreates the exact thing that's banned.
- Semantic states (worth it / not worth it / close call): muted, desaturated green
  and brick-red, not neon traffic-light colors — `close call` uses the primary amber
  accent itself rather than a third hue.

This palette is a strong starting proposal, not scripture — refine the exact hex
values once real data is on screen and you can see how it reads, but keep the
structure (one warm accent, one cool secondary, muted semantics, three fonts each
with a distinct job) rather than drifting back toward a generic single-sans, blue-
accent system.

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
