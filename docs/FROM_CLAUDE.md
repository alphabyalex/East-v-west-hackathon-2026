# Claude's outbox — updates from Claude to Codex

Append-only. Newest entry on top. Codex: read this before starting any new task,
alongside AGENTS.md.

---

## Claude — repo scaffold + pipeline started (2026-09-12)
- Changed: added AGENTS.md, README.md, requirements.txt, .gitignore,
  docs/DATA_NEEDED.md, docs/FROM_CLAUDE.md (this file), pipeline/ingest.py
- New interface: `pipeline/ingest.py` caches SPP data to `data/raw/spp/<dataset>/*.parquet`.
  Run it with `python -m pipeline.ingest --start YYYY-MM-DD --end YYYY-MM-DD`. Read cached
  data downstream with `pipeline.ingest.load_dataset("load"|"reserves"|"binding_constraints"|"lmp"|"fuel_mix")`.
- Needs review: I could not test ingest.py against live SPP data from my own environment —
  its network is blocked from reaching portal.spp.org entirely (confirmed: 403 at the
  gateway, not an SPP-side issue). The chunking/CLI/caching logic is unit-tested, but the
  actual gridstatus calls are untested end-to-end. **Whoever runs this first on a real
  machine: watch for failures and report back here**, especially around `get_load_by_baa`
  (fetches per-5-minute files, slow and the most likely to break).
- Also confirmed: `spp.get_status()` (which would have given real EEA/emergency
  declarations) is not implemented in gridstatus for SPP — raises `NotImplementedError`.
  So `pipeline/label.py` will use the fallback priority order from the roadmap (binding
  transmission constraints -> reserve-margin shortfalls -> scarcity pricing) rather than
  a direct emergency-declaration feed. This is expected, not a bug.
- Blocked on: nothing yet. Working on `pipeline/label.py` next.
