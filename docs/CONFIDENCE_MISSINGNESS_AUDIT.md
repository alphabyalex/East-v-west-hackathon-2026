# Missing sensor values and historical support

Confidence previously replaced missing query features with their training
medians before looking for neighbors. Training references were also stored
after imputation, without preserving which vectors were fully observed. This
could turn unknown grid conditions into apparently well-supported conditions.

The regression is reproducible with identical ensemble agreement and a full
year of scored hourly observations. These are software fixtures, not grid data:

| Evidence | Previous neighbors / score | Corrected neighbors / score |
|---|---:|---:|
| All features observed; 200 matching reference hours | 200 / 0.909091 | 200 / 0.909091 |
| Every sampled query lacks its sensor value | 200 / 0.909091 | 0 / 0 |
| Only 5 of 200 reference vectors were fully observed | 200 / 0.909091 | 5 / 0.2 |
| Most sampled queries have missing sensors | 200 / 0.909091 | 0 / 0 |
| Legacy bundle lacks pre-imputation completeness evidence | 200 / 0.909091 | 0 / 0, with an explicit limitation |

New training bundles preserve `density_training_complete`, a boolean mask of
fully observed reference vectors before imputation. Confidence counts only
complete same-location reference vectors for complete queries. Incomplete
queries contribute zero demonstrated neighbors and remain in the median
denominator; dropping them would conceal missing evidence. This is conservative
support in the full recorded feature space, not proof that the classifier cannot
make a useful prediction with fewer sensors.

Model fitting and sigmoid calibration are unchanged. The only `train.py` change
preserves reference completeness. New model cards record
`confidence_precedent_policy`. Score arithmetic, radius, thresholds, and the API
contract remain unchanged. Legacy bundles cannot recover completeness from
imputed zeros alone; they receive zero demonstrated support and a limitation
when confidence is recomputed without that evidence.

## Saved-run replay

For each saved run, the original training split, medians, scales, standardized
reference matrix, and location order were reconstructed from its saved feature
table and matched exactly to the model bundle. Completeness was then recovered
from those original features in memory. No weights were refitted and no saved
run was modified.

| Run | Fully observed training vectors | Fully observed sampled test vectors | Before and after confidence |
|---|---:|---:|---|
| Six-year history | 22,959 / 31,350 | 218 / 256 | Low, score 0, median neighbors 0 |
| History through 2025 | 26,122 / 36,587 | 222 / 256 | Low, score 0, median neighbors 0 |
| Outage-outlook candidate | 26,032 / 36,587 | 97 / 256 | Low, score 0, median neighbors 0 |

The separate canonical 21-zone artifacts retain their previously recorded
evidence. Their original model/features remain unavailable, so this work does
not claim to revalidate those original neighbor matches. The API's existing
annual-coverage guard still prevents treating that short history as validated
annual exposure.

Exact before/after fixtures, saved-run evidence, feature/model fingerprints,
and the completeness policy are in
[confidence-missingness-audit.json](confidence-missingness-audit.json).

Validation: full Python suite **1,870 tests and 42 subtests passed**. The new
event-timing check subsequently passed with its complete 14-test module, for
1,871 distinct tests overall. Only two existing API dependency warnings remain.
