# Headroom frontend

A local SPP flexible-interconnection scenario workspace built with Vite, React,
TypeScript, Tailwind CSS, Three.js, and Recharts. It uses a deterministic illustrative fixture;
it calls the local FastAPI endpoint by default, with an explicit local mock fallback.
Neither provider runs a Monte Carlo simulation or trains a model.
The user's requested mock-data scope takes precedence over the repo's eventual
real-data definition of done.

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
# Then use that environment?s Python:
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
  fixed annual quantiles and changes modeled losses and the conditional decision.
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
- Reset restores fixture defaults. Export downloads the canonical request/response,
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

The shell deliberately uses illustrative SPP-region location IDs, not verified grid
nodes. Replace them with pipeline-validated identifiers during integration. Every
fixture value uses `source_type: "assumption"`; mock references do not claim real
dataset or tariff support. The one-to-seven-year horizon matches the pipeline
contract, not a claim about an allowed tariff term.

Economics are explicitly marked **MOCK ECONOMICS**, including the decision and the
slider's break-even caption. The round fixture defaults (1,000 GPUs/MW, $2/GPU-hour,
$500,000/MW-year early margin, and three years of earlier access) are dummy values,
not sourced estimates. Their provenance references identify economics placeholders
pending `docs/ASSUMPTIONS.md`; editable inputs remain user assumptions.

Kristian/Tharun's economics sourcing will arrive through `main` in
`docs/ASSUMPTIONS.md`. Check main at integration checkpoints. When it arrives, replace
the applicable defaults and their refs together using the documented units, sources,
retrieval dates, and ranges; recalculate derived break-even values. Keep any remaining
placeholder dependencies visibly marked. Do this during integration, without adding
runtime GitHub requests or data fetching to the offline demo.

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

The default is `VITE_ESTIMATE_MODE=api`. Vite proxies `/api` to
`http://127.0.0.1:8000` in dev and preview. Copy `.env.example` to `.env`, set
`VITE_ESTIMATE_MODE=local`, and restart Vite to make no HTTP requests. The visible
**Local mock** control switches immediately; **Use API defaults** restores the fixed
mock economics and returns to HTTP.

Changing an economic input switches to local mode with an explanation because the
canonical request has no economic override fields. Other inputs trigger a debounced,
cancelable estimate request. While pending or unavailable, the app shows a clearly
labeled local mock preview/fallback for the current inputs; Retry API tries again.
Old responses cannot replace the results for newer inputs. Export records
`{mode,status,response_origin,request,response,local_assumptions,result}`.

The endpoint uses the same canonical fixture and cheap arithmetic. No real pipeline
output, sourced economics, model confidence, or extracted clauses are implied by a
successful HTTP connection. See [api/README.md](../api/README.md) for backend tests
and the replaceable precomputed-provider boundary.

To test the full HTTP contract with both servers running (PowerShell):

```powershell
$env:HEADROOM_API_URL = 'http://127.0.0.1:5174' # use the actual Vite URL
npm test -- src/api/live-contract.test.ts
Remove-Item Env:HEADROOM_API_URL
```

## Structure

```text
src/App.tsx                 Workspace, controls, chart, economics, assumptions
src/ScenarioContext.tsx     Shared inputs, provider mode, and current-input results
src/api/client.ts           Validated HTTP boundary, used by the API provider
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
