# Headroom frontend

A local SPP flexible-interconnection scenario workspace built with Vite, React,
TypeScript, Tailwind CSS, Three.js, and Recharts. Tharun's landing page and workspace
call the local FastAPI endpoint by default, with an explicit local mock fallback.
The API reads precomputed pipeline output when available; missing output remains
honestly labeled as a placeholder. Neither HTTP provider nor frontend trains or simulates.

## Run

Use Node.js 22.12+ (Node 24 is verified).

```sh
cd web
npm ci
npm run dev
```

Start the API in a second terminal from the repo root:

```sh
python -m venv .venv
# Windows: .venv\Scripts\python -m pip install -r api/requirements.txt
# macOS/Linux: .venv/bin/python -m pip install -r api/requirements.txt
# Then use that environment's Python:
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Activate the virtual environment before the final command, or use its full Python path.
On the current Windows workspace it is already ready at `.venv/Scripts/python.exe`.
Open the local URL printed by Vite. Dependencies must be installed once; after that,
the dev server and production build require no external network connection. Fonts,
icons, fixture data, and chart code are local. This is not an installed PWA: a local
server still serves the app.

```sh
npm test          # model, surface geometry/renderer, and DOM interaction tests
npm run build    # TypeScript check + production assets in dist/
npm run preview  # serve the production build locally
```

## Interactions

- Location, load size, term, and interruptible share update the scenario immediately.
- The sticky site exposure slider is an explicit user assumption. It scales the
  supplied annual quantiles and changes modeled losses and the conditional decision.
- Earlier-access advantage, compute density, lost compute value, and early operating
  margin stay visible and editable beneath the outputs.
- Every numeric output uses `Sourced`; inputs use `SourceInfo` and native provenance
  titles; Recharts axis ticks use `SourcedTick`. Hover, focus, or click to inspect the
  exact `{value, source_type, ref}` JSON. Click to pin; Escape or outside click closes.
- The opt-in Three.js surface plots relative contract year × percentile × modeled
  exposure (hours/year). Drag or use the camera buttons to rotate and zoom. With the
  plot focused, arrow keys rotate, plus/minus zoom, and Home resets the view.
- Hover or select a supplied vertex, or use the year/percentile inspector, to inspect
  its sourced value. The vertical scale stays fixed as site exposure changes, so
  the surface visibly rises or falls. Motion stops between updates and respects
  reduced-motion preferences.
- The default Fan chart view uses the Recharts median line, p50–p90 band, and p99 line. It is also
  the automatic fallback if WebGL is unavailable or the graphics context is lost.
  The annual values table includes p50/p90/p99 and remains keyboard accessible.
- Confidence badges accompany exposure readouts, the surface inspector, and annual
  values. They preserve the supplied level and expose the exact signal score/basis.
  The current fixed Medium / 0.5 signal is explicitly MOCK, not an ensemble result
  or the probability of a future outcome.
- The economics panel includes annual cost scenarios for each supplied quantile.
  Tariff evidence displays the mock term record and its provenance, without claiming
  that a real clause has been extracted.
- Reset restores scenario defaults and the latest loaded economic defaults. Export downloads the canonical request/response,
  sourced local assumptions, and view results as JSON. Reset in API mode requests the default scenario; Export saves the currently displayed response and its origin.
- At the default assumptions, moving site exposure from 0.4 through 0.55 to 0.9 produces
  “worth it,” “close call,” then “not worth it.” Zero exposure and zero interruption
  cost are defined, with `null` hours thresholds displayed as “No modeled cost.”
  The slider marks the median cost crossover, not a decision-rule boundary.

## FastAPI handoff

The canonical shared endpoint is **`POST /api/estimate` in
[`docs/BUILD_PLAN.md`](../docs/BUILD_PLAN.md)**. The shell now consumes that response
shape through the FastAPI endpoint, with a local fallback provider. The complete current mock is
[`src/model/mock-response.json`](src/model/mock-response.json), and the canonical
[TypeScript interface](src/model/contract.ts) matches it. A parity test checks both.
The [integration notes](../docs/frontend-api-contract.md) define block-to-value
provenance mapping, formulas, nullable break-even, and complete yearly horizons.
API-scaled exposure is never scaled again by the view adapter.

The initial location is `SPP_SYSTEM`, explicitly labeled as a system aggregate with
no site-specific grid data. This is Kristian's actual pipeline identifier, never an
alias for a Wichita node or a particular interconnection. Illustrative city examples
remain available as explicit demo locations. Local exposure is always an authored
fixture with `source_type: "assumption"`; selecting the real system identifier does
not turn a missing model into real output. The one-to-seven-year horizon matches the
pipeline contract, not a claim about an allowed tariff term.

Economics carry small **Mock economics** labels beside their results, including
the decision and the slider's break-even caption; there is no global warning banner.
Each result block checks its supplied `mock://` reference independently, so sourced
exposure can replace its mock label while economics or diagnostics remain marked.
Tharun's documented assumptions are now 575 GPUs per grid-interconnection MW,
$3/GPU-hour gross rental value, and four years of earlier access. The $317,000/MW-year
early operating margin remains an explicit, unverified placeholder: Tharun applied an
assumed 3% operating margin to scenario gross revenue. It is not a verified net margin.
The backend's full per-input provenance is loaded from `GET /api/economics-assumptions`.
The identical bundled snapshot in `src/model/economics-assumptions.json` supports
offline fallback. Derived local economics stay marked as mock because their exposure
is a fixture; a sourced input never upgrades the source of an invented output.

Primary median exposure and net value have greater numeric scale; upper-tail
quantiles remain visible with separate confidence/provenance. Numeric changes use
bounded cubic easing that retargets from the displayed value during rapid dragging.
Chart selection, confidence-level and decision changes have brief eased feedback;
native inputs remain direct, and neither new views nor numbers count up on first
load. All motion respects reduced-motion preferences and stops when idle/hidden.

`docs/ASSUMPTIONS.md` is the backend's economic source of truth. API mode loads the
marked JSON block from the local server, including units, references, retrieval dates,
ranges, and the decision tolerance. Future updates to those inputs need no frontend
hardcode change. Refresh the bundled snapshot when updating offline defaults; keep
remaining placeholders marked. No runtime GitHub or external data fetches occur.

Annual summaries average the marginal quantile paths. The ledger follows the median
path; the decision compares p50/p90 term-cost proxies with total earlier benefit using
5% margins. These are not quantiles of total contract losses. Public grid stress
cannot establish a particular site's actual curtailment. The slider, conditional
decision wording, and visible model limitations must remain during integration.

The surface connects three supplied quantiles per year, with proportional percentile
spacing. Faces are display interpolation, not additional samples or a probability
density estimate. A single-year horizon shows a quantile cross-section with no
invented time width. A true density surface requires additional precomputed density
bins or samples from the pipeline. The canonical response has no p10, so the UI does
not invent a lower-tail quantile.

## Provider controls

The default is `VITE_ESTIMATE_MODE=api`, calling `http://127.0.0.1:8000` directly.
The backend permits the frontend origin `http://127.0.0.1:5174` through CORS; no proxy
is required at that origin. Set `VITE_API_BASE_URL` to change the API origin, or set it
to an empty string to use same-origin requests through the existing Vite `/api` proxy.
Copy `.env.example` to `.env`, set
`VITE_ESTIMATE_MODE=local`, and restart Vite to make no HTTP requests. The visible
**Local mock** control switches immediately; **Use API defaults** clears economic
overrides and returns to the server's documented assumptions, including placeholders.

Changing an economic input switches to local mode with an explanation because the
canonical request has no economic override fields. Other inputs trigger a debounced,
cancelable estimate request. While pending or unavailable, the app shows a clearly
labeled local mock preview/fallback for the current inputs; Retry API tries again.
Both the estimate and its companion economic metadata must validate before the UI
accepts an API result. GPU-hour/cost quantiles, early benefit, break-even, and the decision
are checked against those defaults to catch a file change between GET and POST. Missing
metadata or inconsistent results use the visible local fallback. Old responses cannot
replace the results for newer inputs. Export records
`{mode,status,response_origin,request,response,local_assumptions,result}`.

The endpoint calls Kristian's reader for valid precomputed output. Missing output
retains assumption provenance; an HTTP connection alone is not evidence of real model
confidence or extracted clauses. See [api/README.md](../api/README.md) for backend tests
and the replaceable precomputed-provider boundary.

To test the full HTTP contract with both servers running (PowerShell):

```powershell
$env:HEADROOM_API_URL = 'http://127.0.0.1:8000'
npm test -- src/api/live-contract.test.ts
Remove-Item Env:HEADROOM_API_URL
```

## Structure

```text
src/App.tsx                 Workspace, controls, chart, economics, assumptions
src/ScenarioContext.tsx     Shared inputs, provider mode, and current-input results
src/api/client.ts           Validated HTTP boundary, used by the API provider
src/api/assumptions.ts      Validated companion metadata and economics consistency check
src/components/Sourced.tsx  Reusable provenance values, info controls, SVG ticks
src/components/ConfidenceBadge.tsx  Supplied confidence level/score with mock status
src/components/ExposureSurface.tsx  Sourced controls and labels for the 3D plot
src/components/exposure-surface/   Three.js renderer and pure quantile geometry
src/hooks/                 Reduced-motion-aware numeric animation
src/model/contract.ts       Canonical request/response types
src/model/estimate.ts       Local mock provider and canonical response view adapter
src/model/                  Supporting types, fixture, and contract/math tests
src/styles.css             Tailwind theme and responsive terminal layout
```

The DOM tests use a fixed chart container because jsdom has no layout engine. Surface
geometry tests cover coordinates, ordering, source identity, and degenerate horizons.
Renderer tests use real Three.js geometry, camera, and OrbitControls, with only the
WebGLRenderer stubbed; they check animation, selection, resize, and resource cleanup.
These checks do not verify GPU pixels or replace desktop/mobile visual browser QA.

## Visual reference

The visual pass follows the compact workspaces shown by [Coinbase Prime](https://www.coinbase.com/en-de/prime)
and [Palantir Workshop](https://www.palantir.com/docs/foundry/workshop/overview):
neutral charcoal surfaces, aligned controls, fine dividing rules, tabular ledgers,
and color reserved for state or data. [Jane Street](https://www.janestreet.com/),
[Optiver](https://www.optiver.com/), and [Millennium](https://www.mlp.com/) supplied
additional references for concise navigation and typographic hierarchy. No brand
assets are copied. The interface uses no gradients, glows, glass effects, rounded
card grid, emoji icons, or marketing hero treatment.

Build configuration follows the official [Vite guide](https://vite.dev/guide/) and
[Tailwind Vite integration](https://tailwindcss.com/docs/installation/using-vite).
The fan uses a range [Recharts Area](https://recharts.github.io/en-US/api/Area/) with
a separate median line.
The surface uses local [Three.js BufferGeometry](https://threejs.org/docs/pages/BufferGeometry.html)
and [OrbitControls](https://threejs.org/docs/pages/OrbitControls.html), loaded as a separate
local JavaScript chunk. No remote chart service or runtime simulation is involved.

## Type and color system

Fraunces handles the brand and section headings; IBM Plex Sans handles controls and prose; IBM Plex Mono handles numbers with tabular figures. The requested [Google Fonts](https://developers.google.com/fonts/docs/css2) files and their OFL licenses are bundled in `public/fonts`, with local `@font-face` rules in `src/fonts.css`, so the demo does not depend on a font CDN. Near-black surfaces, warm off-white text, amber active controls, steel-teal chart series, and muted semantic colors share the tokens in `src/styles.css`.

## Surface stability

The fan chart is the initial view; Three.js is imported and initialized only after selecting **Surface**. This keeps WebGL startup off the demo entry path. The reported real-Chrome screenshot hang cannot be reproduced through the available browser tools (no browser is connected), so this release does not claim GPU-driver verification. Surface failures return to the fan and preserve the sourced annual table.

The surface scheduler coalesces camera, hover picking, resize, and data events into one animation frame, reuses geometry/materials when only heights change, and only publishes axis labels when their placement/provenance changes. It suspends draws in hidden tabs, cancels on disposal/context loss, and keeps automatic rotation/damping disabled. Numeric interpolation also has finite completion/cancellation tests. These are CPU/lifecycle regressions; they do not emulate the browser GPU driver.

## Model and evidence panel

Use **Model & evidence** beside the exposure heading to expand the honesty-layer
explanation, current site assumption/confidence, mock reliability diagnostics, and
tariff evidence in place. The mock Brier values and authored reliability points are
not computed validation results; the panel says so explicitly. All diagnostic
numbers, including axes and the exact-value table, carry sources. Tariff placeholders
have no invented document citations. Close or Escape returns to the analysis.
