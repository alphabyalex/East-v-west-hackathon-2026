# Confidence coverage correction

The confidence scorer used elapsed calendar span as a substitute for observed
validation hours. In a synthetic regression case, just 30 scored hours spaced
14 days apart could receive High confidence. Conversely, exactly 8,760 consecutive
hourly records received a coverage penalty because subtracting their interval
starts yields 364 days and 23 hours.

| Synthetic software case | Before | After |
|---|---|---|
| 30 scored hours across 406 days | High, 0.909091 | Low, 0.69 |
| Exactly 8,760 consecutive scored hours | Low, 0.69 | High, 0.909091 |

These fixtures use 200 matching training records and identical ensemble outputs;
they are not product data or measured prediction accuracy. The correction
requires at least 8,760 distinct scored hours for the location and uses inclusive
hourly interval starts when measuring span. Duplicate hours, missing or naive
timestamps, unknown labels, and nonbinary targets are rejected before scoring.
Historical precedent counts and the existing version-2 score formula are unchanged.

Replayed confidence against three saved real models without refitting:

| Saved run | Scored test hours | Span in hours | Missing hours within span | Confidence changed |
|---|---:|---:|---:|---|
| confidence_v2_20260913 | 10,426 | 10,501 | 75 | No |
| history_2025_20260913 | 12,172 | 12,222 | 50 | No |
| outage_outlook_20260913 | 12,172 | 12,222 | 50 | No |

All three remain Low with score 0 because their median same-location precedent
is zero. Their existing API provenance and minimum annual-reference checks pass.
The canonical 147-row, 21-zone artifacts are unchanged; their original training
inputs and weights remain unavailable, so no recomputation is claimed for them.

New training reports include `confidence_coverage` per location and
`confidence_coverage_policy`, recording the gate and counts. This minimum
classifier-validation requirement does not establish annual-tail accuracy.

The [machine-readable audit](confidence-coverage-audit.json) records actual model
and feature hashes, before/after outputs, and the unchanged canonical distribution.
Regression checks are in `tests/test_confidence.py`; the training integration is
covered in `tests/test_ml_pipeline.py`.

The final full Python suite passed: **1,798 tests and 42 subtests**, with two
existing dependency warnings. The explicit daylight-saving fold test confirms
that timezone-aware repeated local clock hours retain their distinct identities.
