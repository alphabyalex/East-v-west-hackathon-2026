You are working ONLY Alex's lane on the Fluxline project. Do not touch pipeline/confidence.py, pipeline/label.py, pipeline/train.py, pipeline/simulate.py, or any frontend files — those belong to other lanes tonight.

Branch: checkout/create "Alex" from latest main, commit and push to "Alex" only. Never push to main. Push after every meaningful change.

Files you own (only these, create new ones as needed):
- pipeline/wind_signal.py (new)
- pipeline/carbon.py (new)
- api/grid_impact.py (new)
- tests/ files for the above
- docs/FROM_ALEX.md (your log — create if missing)
- You may READ (never modify) pipeline/ingest.py and pipeline/generation.py to reuse their existing SPP fuel-mix pull (get_fuel_mix via gridstatus) — do not add a new EIA-930 fetch, the fuel mix data already includes wind generation.

GOAL: build the "wind curtailment absorption" signal — hours where SPP has more wind generation than the grid can use, meaning a flexible load ramping UP during those hours would absorb power that would otherwise be wasted/curtailed. This is a second, opposite-direction signal from Kristian's curtailment-RISK model (which flags hours to shut down; yours flags hours to ramp up).

Build in this order, without stopping:
1. Read pipeline/ingest.py and pipeline/generation.py to understand the existing fuel-mix pull. Reuse it, do not duplicate it.
2. In pipeline/wind_signal.py: define and compute "wind oversupply hours" per zone — hours where wind generation is high relative to load/price signals (use binding constraints, negative or near-zero LMP, or curtailment proxies already available in the ingested data; document your exact method and its limitation).
3. In pipeline/carbon.py: compute the honesty-correct carbon numbers:
   - carbon SHIFTED (never "avoided") during Kristian's curtailment-risk hours: (intensity at curtailment hour - intensity at makeup hour) x MWh moved
   - carbon ABSORBED during your wind-oversupply hours: MWh absorbed x emissions-per-MWh of the wind that would've been curtailed
   Use EPA eGRID-style emission factors by fuel type against the fuel-mix data already available.
4. In api/grid_impact.py: expose both numbers behind the same provenance/honesty pattern the rest of the API uses (every number gets {value, source_type, ref}; source_type is "data", "model", or "assumption" as appropriate, never silently upgraded). Do NOT wire this into api/estimate.py yet or touch api/pipeline_provider.py — Tharun's lane will do the final integration at merge time. Just make grid_impact.py produce correct, well-tested, well-documented output standalone.
5. Write tests proving: numbers never claim "avoided" language, source_type is honest, and the calculation is reproducible.
6. If time remains: work on making the wind-oversupply detection more rigorous (a real classifier instead of a simple threshold rule) and document precisely what data it needs from Kristian's or the existing pipeline so Tharun can integrate cleanly in the morning.

Run pytest after every change; commits must leave tests passing. Every 30-45 minutes, append a short summary (5-10 lines, no raw dumps) to docs/FROM_ALEX.md: what you built, what's verified, what Tharun will need to know to integrate it.

Do not stop to ask permission on implementation choices — make the most defensible, best-documented call and keep moving. Only stop if truly blocked, and log why clearly.

CRITICAL — RUN NON-STOP FOR HOURS: This session needs to run unattended overnight for as long as possible, likely 8-9+ hours. Do not stop, pause, or wait for input at any point. Do not ask permission before making implementation or design decisions — make the most defensible call, log it, and keep moving. There is no "done" checkpoint where you should halt and report back: once you finish the listed tasks, do not declare the work complete. Instead, loop back and audit yourself:
- Re-read every file you touched as if you were a skeptical reviewer seeing it for the first time. Look for bugs, sloppy edge cases, weak tests, and anything that would embarrass you in front of judges (especially the "shifted not avoided" carbon-language distinction — recheck it every pass).
- Re-run the full test suite and re-verify your numbers are actually reproducible and correctly sourced.
- Tighten documentation, add tests you missed, handle edge cases you skipped the first time.
- Repeat this self-audit cycle for as long as the session runs. Each pass should leave the code measurably better than the last — this is a "perfect itself" loop, not a one-shot task list.
Only stop the loop if something makes ALL further progress genuinely impossible (e.g. missing credentials, a corrupted environment you cannot recover from). A finished task list is never a reason to stop.
