# Headroom frontend

A local SPP flexible-interconnection scenario workspace built with Vite, React,
TypeScript, Tailwind CSS, and Recharts. It uses a deterministic illustrative fixture;
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
npm test          # model invariants and DOM interaction tests
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
- The annual values table supplies a keyboard-accessible alternative to the fan chart.
- Reset restores the fixture defaults. Export downloads inputs, derived results, and
  their baseline fixture as local JSON. Neither action contacts a backend.
- At the default assumptions, moving site exposure from 0.4 through 0.6 to 0.9 produces
  “worth it,” “close call,” then “not worth it.” Zero exposure and zero interruption
  cost are defined, with `null` break-even values displayed as “No modeled cost.”

## FastAPI handoff

The **exact complete mock response** is [`src/model/mock-response.json`](src/model/mock-response.json).
The [contract](../docs/frontend-api-contract.md) defines the wrapper, units, response
fields, defaults, formulas, and integration constraints. The [TypeScript interface](src/model/types.ts)
is the frontend's typed contract; a test checks fixture/JSON parity.

The shell deliberately uses illustrative SPP-region location IDs, not verified grid
nodes. Replace them with pipeline-validated identifiers during integration. Every
fixture value uses `source_type: "assumption"`; mock references do not claim real
dataset or tariff support. The twenty-year selectable horizon is an illustrative
comparison range, not a representation of an allowed tariff term.

Annual summaries average the marginal quantile paths. The economics follow the median
path and do not claim to be the median of total contract losses. Public grid stress
cannot establish a particular site's actual curtailment. The slider, conditional
decision wording, and visible model limitations must remain during integration.

## Structure

```text
src/App.tsx                 Workspace, controls, chart, economics, assumptions
src/ScenarioContext.tsx     Shared inputs and synchronous derived scenario
src/components/Sourced.tsx  Reusable provenance values, info controls, SVG ticks
src/hooks/                 Reduced-motion-aware numeric animation
src/model/                 Types, fixed fixture, lightweight arithmetic, tests
src/styles.css             Tailwind theme and responsive terminal layout
```

The DOM tests use a fixed chart container because jsdom has no layout engine; they
exercise real chart components, input controls, decision changes, provenance, and
the annual values table. They do not replace visual browser QA.

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
