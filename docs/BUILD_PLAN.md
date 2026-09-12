# Build plan — hand this file to your coding LLM

This is the file each of you pastes into your own coding assistant (Codex/Astra,
Claude, whatever) as its first instruction, alongside `AGENTS.md` in the repo root.
`AGENTS.md` is the project-wide contract (tone, scope, honesty rules, ban list).
**This file is the interface contract** — the exact shapes of data that pass between
your part and everyone else's, decided now so three people building independently
still wire together without a live meeting every hour.

If you change a shape in this file, you break someone else's code silently. Don't
change a contract below without posting the change in your outbox file
(`docs/FROM_CLAUDE.md`, `docs/FROM_CODEX.md`, or a new `docs/FROM_<you>.md` — pick one
and stick to it) the moment you do it.

---

## 0. Who owns what (full detail in `docs/build-roadmap` context / ask Alex)

- **Alex — backend/wiring:** `/api`. Reads whatever Kristian's pipeline writes to
  `data/processed/`, combines it with Tharun's request shape, returns the JSON
  contract in section 2.
- **Kristian — ML:** `/pipeline`. Produces the precomputed files in section 1.
- **Tharun — frontend:** `/web`. Calls the API in section 2, renders the response.
  Build against the mock in section 2 immediately — don't wait for a real backend.
- **Claude — director/audit.** Available to unblock any of the three, owns `/extract`.

Expect to bleed into each other's lanes. That's normal on a 3-person 72-hour build,
not a process failure — just flag it in your outbox file when you do.

---

## 1. What the ML pipeline (Kristian) writes to disk

Per the "precompute everything heavy" rule in AGENTS.md, nothing in `/api` or `/web`
ever triggers a model run or a live data fetch. Kristian's pipeline writes finished
results to parquet; the API only reads them and does cheap arithmetic (multiplying by
a user-set slider value, not retraining or resimulating).

**`data/processed/exposure_by_location.parquet`** — one row per (location_id, year
offset into the contract term). Columns:

| column | type | meaning |
|---|---|---|
| `location_id` | string | SPP pricing node / hub identifier |
| `year_offset` | int | 1..7, year into the contract term |
| `p50_hours` | float | median modeled exposure hours that year |
| `p90_hours` | float | 90th percentile |
| `p99_hours` | float | 99th percentile |
| `worst_contiguous_hours` | float | longest single modeled outage that year |
| `confidence_level` | string | "High" \| "Medium" \| "Low" |
| `confidence_score` | float | 0.0-1.0, ensemble agreement (+ data density if built) |
| `n_similar_historical_hours` | int | precedent count backing the confidence score |
| `model_version` | string | e.g. "ensemble_v1_2026-09-13" — bump this string any time the model changes so stale cached numbers are traceable |

This is a **system-level** number. The `site_exposure` slider (0-1, user-set) is
applied live, downstream, by multiplying `p50_hours` / `p90_hours` / `p99_hours` by
`site_exposure` — that math happens in `/api`, not in the pipeline. Kristian's output
never bakes in `site_exposure`.

**`pipeline/simulate.py` also exposes one function for `/api` to import directly**
(don't make Alex re-parse parquet by hand):

```python
def get_location_estimate(location_id: str) -> dict:
    """Reads exposure_by_location.parquet for this location, returns:
    {
      "location_id": str,
      "by_year": [ {"year_offset": int, "p50_hours": float, "p90_hours": float,
                     "p99_hours": float, "worst_contiguous_hours": float}, ... ],
      "confidence": {"level": str, "score": float, "n_similar_historical_hours": int},
      "model_version": str,
    }
    Raises LocationNotFoundError if location_id isn't in the precomputed set.
    """
```

**`data/tariffs/tariffs.json`** (from `/extract`, Claude's) — per-operator extracted
tariff terms with citations. Alex's API reads this for the `tariff` block in section 2.
Ask Claude for the exact schema once `/extract` is built if you need it before then.

---

## 2. The API contract (Alex builds this, Tharun builds against it)

**`POST /api/estimate`**

Request body:
```json
{
  "location_id": "SPP_SPS_HUB",
  "load_mw": 250,
  "term_years": 7,
  "flexibility_split": 0.6,
  "site_exposure": 0.3
}
```
- `flexibility_split`: 0-1, fraction of the load that's interruptible/pausable
- `site_exposure`: 0-1, the user-set honesty-layer assumption (AGENTS.md's core rule)

Response body:
```json
{
  "inputs_echo": { "...": "same fields as the request" },
  "modeled_exposure": {
    "unit": "hours/year",
    "p50": 42.0,
    "p90": 123.0,
    "p99": 216.0,
    "worst_contiguous_outage_hours": 28.8,
    "by_year": [
      { "year": 1, "p50": 42.0, "p90": 123.0, "p99": 216.0 }
    ],
    "source": { "source_type": "model", "ref": "pipeline/simulate.py model_version=ensemble_v1_2026-09-13" }
  },
  "confidence": {
    "level": "Medium",
    "score": 0.62,
    "basis": "ensemble_disagreement",
    "source": { "source_type": "model", "ref": "n_similar_historical_hours=340" }
  },
  "economics": {
    "gpus_per_mw": 780,
    "lost_gpu_hours_per_year": { "p50": 32760, "p90": 95940, "p99": 168480 },
    "annual_cost_usd": { "p50": 1800000, "p90": 5300000, "p99": 9200000 },
    "value_of_early_connection_usd": 42000000,
    "breakeven_exposure_hours_per_year": 310,
    "decision": "worth_it",
    "source": { "source_type": "assumption", "ref": "docs/ASSUMPTIONS.md#gpu-rental-price" }
  },
  "tariff": {
    "operator": "SPP",
    "service": "CHILLS",
    "curtailment_triggers": [
      { "text": "when the transmission system is constrained or under emergency conditions",
        "observable": false,
        "source": { "source_type": "clause", "ref": "FERC order 195 FERC 61,196, p.14" } }
    ]
  }
}
```

`decision` is one of `"worth_it" | "not_worth_it" | "close_call"` — computed by
comparing `economics.annual_cost_usd` against `economics.value_of_early_connection_usd`
at the p50/p90 levels; exact thresholds for "close_call" are Alex's call, document
whatever you pick in your outbox file.

**Tharun: build the frontend against this exact JSON shape as a mock immediately.**
Don't wait for Alex's real endpoint. When the real one exists it should return
identically-shaped data, so swapping the mock for a real fetch is a one-line change.

**Alex: if `docs/frontend-api-contract.md` already exists with a different shape**
(Codex may have invented one before this file existed), reconcile the two — this file
is the canonical one going forward since it also pins the pipeline-side shapes in
section 1. Post the reconciliation in your outbox file so Tharun/Codex knows which
shape actually shipped.

---

## 3. Economics inputs (from `docs/ASSUMPTIONS.md`, not invented per-component)

`gpus_per_mw`, GPU rental price, industrial electricity price, etc. all come from
`docs/ASSUMPTIONS.md` (see `docs/DATA_NEEDED.md` for what goes in it and who's
sourcing it). Whoever wires `economics.py`/the API's economics block should **read
that file for the numbers, not hardcode invented ones** — and while it doesn't exist
yet, use round, obviously-fake placeholders (per `docs/DATA_NEEDED.md`'s "develop
against dummy values" note) rather than a precise-looking fake number.

---

## 4. Definition of "wired in correctly"

- `/web` never talks to `/pipeline` directly — always through `/api`
- `/api` never runs a model or fetches external data live — only reads precomputed
  parquet/json and does the cheap `site_exposure` / `flexibility_split` arithmetic
- Every number in the API response has a `source` object, per AGENTS.md's provenance
  rule — no exceptions, including on mocked/placeholder values (mark those
  `"source_type": "assumption"` too, just with an obvious placeholder `ref`)
- If your part changes a shape in this file, you posted that change in an outbox file
  in the same commit
