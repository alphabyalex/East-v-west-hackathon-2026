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

This is a raw-source collection and quality audit. These observations have not
been added to training, and no model improvement is claimed. The next step is a
tested importer that preserves timestamp identity and unknown hours, followed by
a review of predictor availability before any model comparison.
