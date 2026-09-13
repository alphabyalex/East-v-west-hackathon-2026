# Historical generation capacity collection

Cached **2,557 daily SPP generation-capacity reports** covering Central calendar
years 2019 through 2025. Annual ZIPs supply 2019–2024; 365 individually cached CSVs
supply 2025. The raw files contain 61,318 rows representing 61,314 distinct hourly
timestamps. All downloads have source URLs, retrieval times, and SHA-256 hashes
in [capacity-data-sources.json](capacity-data-sources.json).

| Source year | Distinct hours | Missing clock hours | Conflicting duplicate hours |
|---|---:|---:|---:|
| 2019 | 8,748 | 12 | 0 |
| 2020 | 8,780 | 4 | 0 |
| 2021 | 8,755 | 5 | 0 |
| 2022 | 8,742 | 18 | 0 |
| 2023 | 8,756 | 4 | 0 |
| 2024 | 8,780 | 4 | 0 |
| 2025 | 8,753 | 7 | 0 |

The audit identified four identical duplicate timestamps around daylight-saving
transitions and **54 missing clock hours**. It also recovered 264 valid numeric
cells in 2019 whose source strings use thousands separators. Strict numeric
parsing needs to accept correctly grouped separators without silently coercing
malformed data to missing values. No negative or infinite capacities were found
after that parsing, and the apparently blank cells were formatted numbers.

The [SPP data guide](https://www.spp.org/Documents/75871/SPP%20Markets%20Public%20Data%20Guide%20and%20Samples%20v35.zip)
describes the [hourly capacity dataset](https://portal.spp.org/pages/hourly-generation-capacity-by-fuel-type)
as prior-day capacity by fuel based on EcoMax used in real-time operations.
This does not establish deliverable reserves, actual site interruptions, or
historical publication and revision times. The six UTC boundary hours on
January 1, 2026 belong to the final Central operating day and must be excluded
from the model's pre-2026 UTC scope.

The tested importer now runs offline from the validated byte caches:

```powershell
.venv\Scripts\python.exe -m pipeline.capacity --out data/processed/research/spp_capacity_2019_2025.parquet
```

Choose a new output path when reproducing; the importer preserves existing
artifacts and sidecars. It requires every dated report in an annual archive,
accepts valid thousands separators, rejects malformed/non-finite/negative
values and changed balancing-area schemas, and reconciles only identical
duplicate observations. Every retained observation carries the report URL,
operating date, and exact report-byte SHA-256. Conflicting duplicates stop the
import instead of selecting a revision silently.

The actual seven-year import, with network access disabled in the fetch call,
produced **61,362 clock rows: 61,308 observed and 54 explicitly missing**. Four
observed timestamps retain references to both identical source reports. The six
post-2025 UTC boundary observations are excluded. The output was reloaded with
pandas and checked against its manifest. Exact counts, missing timestamps,
duplicate references, and the output hash are in
[capacity-normalization-audit.json](capacity-normalization-audit.json).
The cleaned parquet and its `.capacity.json` provenance manifest are committed
under `data/processed/research/` so teammates can inspect the observations
without downloading the original reports. The raw download caches remain local.

`observation_timestamp_utc` preserves `GMT TIME` exactly. The importer does not
assume that this field identifies an interval start, an interval end, or when
the data became available to a historical forecaster. The standalone research
table cannot be passed directly to the training-input reader, and its EcoMax
columns are not registered as model predictors. These observations have not
been added to training, and no model improvement is claimed. Predictor timing
and revision availability still need review before a model comparison.
