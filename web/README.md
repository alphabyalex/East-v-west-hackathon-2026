# Headroom frontend

A local SPP flexible-interconnection scenario workspace built with Vite, React,
TypeScript, Tailwind CSS, Three.js, and Recharts. It uses a deterministic illustrative fixture;
it makes no API requests and does not run a Monte Carlo simulation or train a model.
The user's requested mock-data scope takes precedence over the repo's eventual
real-data definition of done.

## Run

Use Node.js 22.12+ (Node 24 is verified).

```sh
cd web
npm ci
npm run dev
```

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
- The default Three.js surface plots relative contract year × percentile × modeled
  exposure (hours/year). Drag or use the camera buttons to rotate and zoom. With the
  plot focused, arrow keys rotate, plus/minus zoom, and Home resets the view.
- Hover or select a supplied vertex, or use the year/percentile inspector, to inspect
  its sourced value. The vertical scale stays fixed as site exposure changes, so
  the surface visibly rises or falls. Motion stops between updates and respects
  reduced-motion preferences.
- The Fan chart view retains the Recharts median line and p10–p90 band. It is also
  the automatic fallback if WebGL is unavailable or the graphics context is lost.
  The annual values table includes p10/p50/p90/p99 and remains keyboard accessible.
- Reset restores the fixture defaults. Export downloads inputs, derived results, and
  their baseline fixture as local JSON. Neither action contacts a backend.
- At the default assumptions, moving site exposure from 0.4 through 0.55 to 0.9 produces
  “worth it,” “close call,” then “not worth it.” Zero exposure and zero interruption
  cost are defined, with `null` break-even values displayed as “No modeled cost.”

## FastAPI handoff

The canonical shared endpoint is **`POST /api/estimate` in
[`docs/BUILD_PLAN.md`](../docs/BUILD_PLAN.md)**. The existing shell still uses the
earlier local fixture, [`src/model/mock-response.json`](src/model/mock-response.json).
The [fixture notes and migration checklist](../docs/frontend-api-contract.md) document
the differences, units, formulas, and provenance mapping. The [TypeScript interface](src/model/types.ts)
describes the current local shell; a test checks fixture/JSON parity. The new endpoint
contract and confidence block are not wired into the shell yet.

The shell deliberately uses illustrative SPP-region location IDs, not verified grid
nodes. Replace them with pipeline-validated identifiers during integration. Every
fixture value uses `source_type: "assumption"`; mock references do not claim real
dataset or tariff support. The twenty-year selectable horizon is an illustrative
comparison range, not a representation of an allowed tariff term.

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

Annual summaries average the marginal quantile paths. The economics follow the median
path and do not claim to be the median of total contract losses. Public grid stress
cannot establish a particular site's actual curtailment. The slider, conditional
decision wording, and visible model limitations must remain during integration.

The surface connects four supplied quantiles per year, with proportional percentile
spacing. Faces are display interpolation, not additional samples or a probability
density estimate. A single-year horizon shows a quantile cross-section with no
invented time width. A true density surface requires additional precomputed density
bins or samples from the pipeline. The response schema is unchanged: yearly p99 was
already present in the fixture and is now retained in derived results and exports.

## Structure

```text
src/App.tsx                 Workspace, controls, chart, economics, assumptions
src/ScenarioContext.tsx     Shared inputs and synchronous derived scenario
src/components/Sourced.tsx  Reusable provenance values, info controls, SVG ticks
src/components/ExposureSurface.tsx  Sourced controls and labels for the 3D plot
src/components/exposure-surface/   Three.js renderer and pure quantile geometry
src/hooks/                 Reduced-motion-aware numeric animation
src/model/                 Types, fixed fixture, lightweight arithmetic, tests
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
