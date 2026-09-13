You are working ONLY Tharun's lane on the Fluxline project. Do not touch pipeline/confidence.py, pipeline/label.py, pipeline/train.py, pipeline/simulate.py, pipeline/wind_signal.py, or pipeline/carbon.py — those belong to other lanes tonight and will not exist in final form until morning merge.

Branch: checkout/create "Tharun" from latest main, commit and push to "Tharun" only. Never push to main. Push after every meaningful change.

Files you own (only these, create new ones as needed):
- pipeline/site_rank.py (new)
- api/zone_ranking.py (new)
- web/ frontend files for displaying the ranking (new components; you may extend the existing frontend you already restyled)
- tests/ files for the above
- docs/FROM_THARUN.md (your log — create if missing)

GOAL: build the "Best Zones for Flexible Load" ranking — a composite score per SPP zone combining: (a) low curtailment risk [from Kristian's model], (b) high wind-absorption potential [from Alex's model], (c) carbon intensity profile. This is the synthesis feature that shows the commercial tool and the public-good tool are the same computation.

IMPORTANT — Kristian's and Alex's real outputs will not exist in finished form until the morning merge. Do not wait for them. Build against CONTRACTS instead:
- Kristian's existing real output already exists at data/processed/exposure_by_location.parquet with columns: location_id, year_offset, p50/p90/p99_hours, confidence_level, confidence_score. Use this now, it's real.
- For Alex's wind-absorption output (not yet built), define a placeholder JSON shape yourself: {"location_id": str, "wind_absorption_mwh_per_year": float, "carbon_absorbed_tonnes_per_year": float, "source": {...}}. Write a mock/stub generator that produces plausible fake values in this exact shape, clearly labeled as placeholder, so you can build and test the ranking end-to-end tonight. Document the exact contract in docs/FROM_THARUN.md so Alex's real output can drop in at merge time with zero changes to your code.

Build in this order, without stopping:
1. In pipeline/site_rank.py: compute a composite sustainability/fit score per zone from the real exposure data + your placeholder wind-absorption data. Document your weighting method plainly (this should be defensible to a judge, not a black box).
2. In api/zone_ranking.py: expose a ranked list endpoint following the existing honesty/provenance pattern (every number sourced, placeholder data clearly marked source_type: "assumption" or similar, never dressed up as real).
3. Build the frontend leaderboard view: ranked zones, the composite score, and a breakdown showing why each zone ranks where it does (risk, wind absorption, carbon) — this needs to look good, it's likely a demo centerpiece.
4. Write tests: ranking is stable/reproducible, placeholder data is never presented as real, breakdown values sum/relate correctly to the composite score.
5. If time remains: refine the frontend (sorting, filtering, a simple bar/scatter visualization of the ranking), and tighten the scoring methodology.

Run pytest after every change; commits must leave tests passing. Every 30-45 minutes, append a short summary (5-10 lines, no raw dumps) to docs/FROM_THARUN.md.

Do not stop to ask permission on implementation or design choices — make the most defensible call, document it, and keep moving. Only stop if truly blocked, and log why clearly.

CRITICAL — RUN NON-STOP FOR HOURS: This session needs to run unattended overnight for as long as possible, likely 8-9+ hours. Do not stop, pause, or wait for input at any point. Do not ask permission before making implementation or design decisions — make the most defensible call, log it, and keep moving. There is no "done" checkpoint where you should halt and report back: once you finish the listed tasks, do not declare the work complete. Instead, loop back and audit yourself:
- Re-read every file you touched as if you were a skeptical reviewer seeing it for the first time. Look for bugs, sloppy edge cases, weak tests, and anything that would embarrass you in front of judges — especially whether placeholder data is ever accidentally presented as real.
- Re-run the full test suite and re-verify the ranking output is stable and correctly sourced.
- Tighten documentation, add tests you missed, polish the frontend further, handle edge cases you skipped the first time.
- Repeat this self-audit cycle for as long as the session runs. Each pass should leave the code measurably better than the last — this is a "perfect itself" loop, not a one-shot task list.
Only stop the loop if something makes ALL further progress genuinely impossible (e.g. missing credentials, a corrupted environment you cannot recover from). A finished task list is never a reason to stop.
