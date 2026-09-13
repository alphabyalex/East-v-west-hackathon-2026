You are working ONLY Kristian's lane on the Fluxline project. Do not touch any file outside the list below. Do not work on wind/community features, frontend, or ranking — that is someone else's lane tonight.

Branch: checkout/create "Kristian" from latest main, commit and push to "Kristian" only. Never push to main. Push after every meaningful change, not just at the end.

Files you own (only these):
- pipeline/confidence.py
- pipeline/label.py
- pipeline/features.py
- pipeline/train.py
- pipeline/simulate.py
- tests/ files for the above
- docs/FROM_KRISTIAN.md (your log — create if missing)

THE BUG TO FIX (top priority): in data/processed/exposure_by_location.parquet, confidence_score is ~0.988 for all 147 rows regardless of zone, while n_similar_historical_hours is 0-5 for every row. The ensemble is reporting near-maximum confidence in situations it has almost no historical precedent for. This is the single most attackable weakness in the product. Fix the confidence computation so score actually degrades as n_similar_historical_hours drops toward zero, and so confidence varies meaningfully across zones instead of clustering at one value. Re-run training and re-generate the parquet after the fix. Do not just relabel the output — the underlying ensemble-agreement computation must change.

After that, in priority order, keep working the full session without stopping:
1. Fix the confidence bug above. Verify by re-inspecting the parquet (score should spread out, correlate with n_similar_historical_hours).
2. Add calibration diagnostics: a held-out reliability check comparing predicted probabilities to observed outcomes. Write results to docs/FROM_KRISTIAN.md.
3. Harden train.py and simulate.py: add data validation, handle edge cases (zones with very few historical events), add regression tests that would have caught the confidence bug.
4. If time remains, try to reduce the ensemble's uncertainty in low-data zones — more bootstrap samples, better feature engineering in features.py, or flagging zones as "insufficient data" rather than forcing a Low/Medium/High label.

Run the full test suite after every change (python -m pytest). Every commit must leave tests passing. Every 30-45 minutes of work, append a short (5-10 line) summary to docs/FROM_KRISTIAN.md: what you changed, what you verified, any open question. Do not paste raw command output there — summarize it.

Do not stop to ask permission for implementation choices — make the most defensible call, note it in the log, and continue. Only stop if something makes further progress genuinely impossible (e.g. missing credentials), and log that clearly.

Keep going until you run out of productive work in this file list. If you finish everything above, re-read your own code critically and look for anything a skeptical judge would poke at.
