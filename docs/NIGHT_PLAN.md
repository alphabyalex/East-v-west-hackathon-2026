# Overnight plan — three lanes, no collisions

Written Sun Sept 13, ~01:30 ET (hour 13.5). Target: **CP2, Sunday 12:00pm ET (hour 24).**
Everything here is sized to run unattended overnight in three separate Codex sessions.

Read your own lane. Paste your lane's prompt into your Codex. Do not read the other
lanes' prompts into your agent, it will start editing files it does not own.

---

## The two goals tonight

1. **Make sustainability a computed output, not a claim.** Right now the environmental
   case lives in the pitch script only. By morning it should be numbers on screen, each
   one sourced, exactly like every other number in the product.
2. **Make the model defensible.** The exposure model now covers 21 SPP zones with a
   calibrated dual-ensemble, which is real progress. What it does not yet have is a
   confidence signal that means anything, or a carbon dimension.

---

## Rule zero: branch discipline (read this or the night ends in a merge disaster)

Three agents editing one repo unattended for eight hours will collide unless ownership
is absolute.

| Lane | Owner | Owns these paths, exclusively | Pushes to |
|---|---|---|---|
| A | Alex | `/api/**`, `/extract/**`, `docs/ASSUMPTIONS.md`, `docs/BUILD_PLAN.md` | `Alex` |
| B | Kristian | `/pipeline/**`, `data/processed/**` | `Kristian` |
| C | Tharun | `/web/**` | `Tharun` |

- **Nobody pushes to `main` overnight.** Alex merges all three branches in the morning.
- **Only Alex edits `docs/BUILD_PLAN.md`.** It is the shared contract. If your lane needs
  a contract change, write it in your outbox file and keep building against the spec as
  written. Do not edit the contract yourself.
- Everyone logs progress to their own outbox (`docs/FROM_CODEX.md` for Alex, or
  `docs/FROM_<YOURNAME>.md`). Commit small and often so a crashed session loses minutes,
  not hours.

---

## The sustainability feature: Grid Impact

The argument in one line: **flexible load lets new demand connect to the grid that
already exists, so the peaker plant never gets built.** Tonight we turn that into three
measured numbers.

### Why this is a real insight and not greenwashing

The hours a flexible site gets curtailed are not random hours. They are precisely the
hours when the grid is stressed, which is when the dirtiest, most expensive peaking units
are running. So an hour of paused load during a stress event displaces far more carbon
than an average hour does. That differential is measurable, it is non-obvious, and
nobody in the room will have computed it.

### The three outputs

**1. Carbon intensity differential.** Grams CO2 per kWh during modeled curtailment hours
versus the SPP annual average. This is the headline insight number. Expect stress hours
to run meaningfully dirtier than average.

**2. Net carbon shifted, tonnes CO2e per year.** Be careful and be honest here: an
interruptible training job that pauses usually runs later, it does not vanish. So the
defensible number is not "avoided," it is **shifted**, and the net benefit is
`(intensity at curtailed hour − intensity at makeup hour) × MWh moved`. Label it exactly
that way. If we claim naive avoidance, the first person who knows anything about grids
will take the whole pitch apart.

**3. Firm capacity deferred.** Taking flexible instead of firm means the system does not
need to add N MW of firm capacity to serve this load's peak. Report the MW, and as a
clearly labeled scenario, what that capacity would emit annually if built as a gas
combustion turbine, which is the realistic marginal build.

### Data sources, all free, no paid API

- **EIA-930** hourly generation by fuel type for SPP. Free EIA Open Data API key, or the
  bulk CSV download. Gives the hourly fuel mix we need.
- **EPA eGRID** emission factors per fuel type, for converting fuel mix to CO2.
- **Method citation for marginal emissions:** Siler-Evans, Azevedo and Morgan (2012),
  *Marginal Emissions Factors for the U.S. Electricity System*, Environmental Science and
  Technology. The method is a regression of change in system emissions against change in
  system demand, fit by hour block and season. Cite it. A published methodology behind our
  carbon number is worth more than a cleverer number we invented.

### The contract (Alex owns this, everyone builds against it)

New `grid_impact` block on the `/api/estimate` response:

```json
"grid_impact": {
  "carbon_intensity": {
    "unit": "g_co2_per_kwh",
    "curtailment_hours": 612.0,
    "annual_average": 438.0,
    "differential_pct": 39.7,
    "source": { "source_type": "data",
                "ref": "EIA-930 SPP hourly fuel mix; EPA eGRID factors" }
  },
  "carbon_shifted_tonnes_per_year": {
    "p50": 8100.0, "p90": 23700.0, "p99": 41600.0,
    "basis": "net of makeup-hour intensity, load shifted not eliminated",
    "source": { "source_type": "model",
                "ref": "pipeline/carbon.py model_version=..." }
  },
  "firm_capacity_deferred_mw": {
    "value": 250.0,
    "source": { "source_type": "assumption",
                "ref": "docs/ASSUMPTIONS.md#capacity-deferral" }
  },
  "avoided_peaker_build": {
    "mw": 250.0,
    "annual_tonnes_co2e_if_built_as_gas_ct": 96000.0,
    "basis": "scenario, not a forecast",
    "source": { "source_type": "assumption",
                "ref": "docs/ASSUMPTIONS.md#gas-ct-emissions" }
  }
}
```

Every field carries a `source` object, same as everything else. Placeholder values stay
marked `assumption` with an obvious placeholder ref until the real pipeline output lands.

---

## Lane A — Alex: API, contract, tariff extraction

Paste this into your Codex:

> Three tasks tonight, in this order. Work only in `/api`, `/extract`, `docs/ASSUMPTIONS.md`
> and `docs/BUILD_PLAN.md`. Do not touch `/pipeline` or `/web`, teammates are editing those
> live. Push only to the `Alex` branch, never to main. Commit after each task rather than
> once at the end.
>
> **Task 1: add the `grid_impact` block to `/api/estimate`.** The exact JSON shape is in
> `docs/NIGHT_PLAN.md` under "The contract". Add it to `docs/BUILD_PLAN.md` as section 2b
> so the team builds against one spec. Implement it in `/api` reading from
> `pipeline.carbon.get_location_carbon(location_id)` if that function exists, and falling
> back to clearly labeled placeholder values with `source_type: "assumption"` and an
> obvious placeholder ref if it does not, using exactly the same fallback pattern
> `api/pipeline_provider.py` already uses for exposure. Kristian is building that function
> tonight in parallel, so it will probably not exist when you start. That is expected, build
> the fallback path first and make the real path work the moment the function appears.
>
> **Task 2: build tariff extraction in `/extract`.** This does not exist yet and the
> `tariff` block in the API response is still `placeholder_tariff()`. Extract structured
> terms from SPP's CHILLS tariff filing into `data/tariffs/tariffs.json`: curtailment
> triggers, service priority, duration limits, notice requirements, obligations, exit
> terms. Every field carries a real citation, meaning document name, page number and the
> quoted clause text. Flag separately any condition that is discretionary or unobservable,
> for example "when the transmission system is constrained", because that ambiguity is a
> selling point rather than a gap. Use an LLM call over the actual filing text, do not
> hand-type values. If you cannot reach the real filing, say so explicitly in your outbox
> and leave the placeholder in place rather than inventing plausible clauses. Then wire
> the API's `tariff` block to read this file instead of `placeholder_tariff()`, keeping
> the response shape identical. Add tests asserting every returned tariff field carries a
> real citation rather than a placeholder ref.
>
> **Task 3: emission factor assumptions.** Add a section to `docs/ASSUMPTIONS.md` for the
> carbon math: CO2 emission factors by fuel type from EPA eGRID, a gas combustion turbine
> heat rate and capacity factor for the deferred-build scenario, and the assumed makeup
> window for shifted load. Each with a real source URL and a retrieved date. Where a value
> cannot be sourced, mark it ASSUMPTION rather than dressing it up as a fact.
>
> Run the full test suite and the production build before each commit. Log everything to
> `docs/FROM_CODEX.md`, including anything you could not complete and why.

---

## Lane B — Kristian: the carbon model, and fixing confidence

Paste this into your Codex:

> Work only in `/pipeline` and `data/processed`. Do not touch `/api` or `/web`, teammates
> are editing those live. Push only to the `Kristian` branch, never to main. Commit after
> each task.
>
> **Task 1, highest priority: fix the confidence signal so it means something.** The
> current `exposure_by_location.parquet` reports `confidence_score` around 0.988 with
> `n_similar_historical_hours` of 0 to 2 for every one of the 21 zones. That combination
> is not defensible: it says the ensemble agrees strongly about a situation with
> essentially no historical precedent behind it, which is exactly when an ensemble is most
> likely to be confidently wrong. Two things to fix. First, implement the data-density
> signal described in `pipeline/confidence.py`'s spec, nearest-neighbour distance in
> feature space from the query point to the training set, and combine it with ensemble
> agreement so thin precedent actually drags the score down instead of leaving it at 0.99.
> Second, investigate why `n_similar_historical_hours` is near zero. Either the similarity
> threshold is far too tight, or the feature space is too high dimensional for the
> neighbour count to be meaningful. Fix the underlying cause, do not paper over it by
> loosening the threshold until the number looks better. Document what you found.
>
> **Task 2: build `pipeline/carbon.py`, the marginal emissions model.** Ingest EIA-930
> hourly generation by fuel type for SPP, using the free EIA Open Data API or the bulk CSV
> download, and cache it to parquet in `data/raw` like the other ingests. Convert hourly
> fuel mix to hourly system CO2 using EPA eGRID emission factors per fuel type. Then fit a
> marginal emissions factor model following Siler-Evans, Azevedo and Morgan (2012),
> *Marginal Emissions Factors for the U.S. Electricity System*: regress the hourly change
> in system emissions against the hourly change in system demand, fit separately by hour
> block and season. Report the fit quality honestly, including R squared by segment. Cite
> the paper in the module docstring.
>
> **Task 3: join carbon to exposure and write the output.** For each of the 21 locations,
> take the hours the exposure model flags as stress hours and compute: mean carbon
> intensity during those hours, mean carbon intensity across all hours, and the resulting
> differential. Then compute net carbon shifted per year at p50, p90 and p99, defined as
> `(intensity at curtailed hours − intensity at makeup hours) × MWh shifted`. This is
> shifted load, not eliminated load, because a paused training job runs later. Do not
> compute or label it as naive avoidance. Write `data/processed/carbon_by_location.parquet`
> plus a model card and metadata sidecar matching the pattern of the exposure artifacts,
> and expose a reader `get_location_carbon(location_id) -> dict` in `pipeline/carbon.py`
> following the same shape and error behaviour as `get_location_estimate`. Alex's API
> calls this function, so the signature matters.
>
> Add tests for each. Log to `docs/FROM_KRISTIAN.md`, including honest notes on fit quality
> and anything that did not work.

---

## Lane C — Tharun: Grid Impact panel and the hero visual

Paste this into your Codex:

> Work only in `/web`. Do not touch `/api` or `/pipeline`, teammates are editing those
> live. Push only to the `Tharun` branch, never to main. Commit after each task.
>
> **Task 1: finish and verify the Ink and Signal restyle.** Confirm the new palette,
> Archivo headings and Roboto Mono numerals are applied consistently across every view, not
> just the ones that were easy. Pay particular attention to the tornado chart and the
> confidence badges, they carry colour-encoded meaning and a palette swap can silently
> break the semantics. The signal accent `#7FD8CF` is reserved for live measured values and
> active states only, never decoration.
>
> **Task 2: build the Grid Impact panel.** The exact JSON shape of the new `grid_impact`
> block is in `docs/NIGHT_PLAN.md` under "The contract". Build against that shape as a mock
> immediately, do not wait for the real endpoint, it will return identically shaped data.
> Show three things: the carbon intensity differential between curtailment hours and the
> annual average, net carbon shifted per year at p50/p90/p99, and firm capacity deferred
> with the gas turbine build scenario. Every number carries its provenance badge exactly
> like the rest of the app. Critically, the copy must say **shifted**, not avoided, and the
> panel must make clear that paused load runs later rather than disappearing. Do not let
> the copy drift into claiming we eliminate emissions.
>
> **Task 3, the most important visual in the pitch: the carbon intensity duration curve.**
> Plot SPP's hourly carbon intensity sorted from dirtiest to cleanest across the year, then
> highlight where the modeled curtailment hours fall on that curve. They should cluster on
> the dirty end. This single chart is the entire sustainability argument in one image: the
> hours you get curtailed are the hours the grid is burning its worst fuel. Give it the
> same care as the exposure fan chart. Mock the data against a plausible shape for now,
> Kristian's real series lands overnight.
>
> Run the frontend test suite and the production build before each commit. Log to
> `docs/FROM_THARUN.md`.

---

## Morning checklist, for Alex at roughly 08:00

1. Merge `Kristian`, then `Tharun`, then `Alex` into main, in that order. Resolve conflicts
   in favour of the lane that owns the file.
2. Verify `/api/estimate` returns a real `grid_impact` block, not the placeholder fallback.
3. Check the confidence numbers actually changed. If `n_similar_historical_hours` is still
   near zero, that is the single most important thing to fix before CP2, because it is the
   most attackable number in the product.
4. Look at the duration curve chart. If the curtailment hours do not visibly cluster on the
   dirty end, the sustainability thesis needs re-checking against the data rather than
   restating louder.
5. Record the CP2 video against real numbers.

## What we are deliberately not doing tonight

- No second grid operator. SPP only, per the CP3 gate. Depth over breadth.
- No new model architectures. The dual ensemble is enough, make it defensible instead.
- No design exploration beyond finishing the restyle already in flight.
