# Documented emergency evidence and the demand proxy

The catalog now contains **98 hours 28 minutes** of confirmed SPP system EEA
observations, up from **2 hours 30 minutes**. The 14 added intervals cover
Winter Storm Uri (2021) and Elliott (2022). They intersect 102 hourly records in
the 61,362-row 2019–2025 dataset. All 102 have observed load.

| Event year | Confirmed EEA minutes | Hourly buckets touched | Buckets above frozen demand threshold | EEA minutes in those buckets |
|---|---:|---:|---:|---:|
| 2021 | 5,485 | 93 | 10 | 600 (10.94%) |
| 2022 | 273 | 6 | 6 | 273 (100%) |
| 2024 | 150 | 3 | 3 | 150 (100%) |

The unchanged high-demand threshold is **42,064.0228 MW**, the 95th percentile
of the original pre-2022 reference data. This case analysis shows why strong
accuracy on the demand proxy cannot establish emergency-prediction accuracy:
most documented Uri emergency minutes fall in hours below that threshold.
Supply availability and extreme-weather conditions need independent evidence.

The 2021 and 2022 cases fall within the model's fitting history. This is an audit
of the target's meaning, not a held-out evaluation or an annual frequency estimate.
No labels were created or model probabilities changed. The other **61,260 rows
remain unknown**, with zero confirmed negative hours. A selected event catalog
cannot establish false-alarm rates or specificity. Minute weights describe event
overlap with hourly average load, not minute-resolution demand.

## Sources and timing

The [2021 SPP comprehensive review](https://www.spp.org/documents/65037/comprehensive%20review%20of%20spp's%20response%20to%20the%20feb.%202021%20winter%20storm%202021%2007%2019.pdf),
Figure 1 on printed page 25 (PDF page 26), provides the EEA level transitions.
Its narrative on printed pages 28–29 corroborates the main transitions. The
February 18 pause is preserved; EEA3 duration is not treated as load-shed duration.

The [2022 SPP review](https://www.spp.org/documents/69218/review%20of%20spp's%20response%20to%20the%20dec.%202022%20winter%20storm.pdf),
printed page 5 and Appendix A (PDF pages 9 and 40), confirms two December 23 EEA1
intervals: 08:27–10:00 and 17:20–20:20 Central Standard Time. The report states
that SPP did not progress to EEA2/3 or direct system load management.

The existing August 26, 2024 interval is documented in the
[SPP summer report](https://spp.org/documents/72631/20241101_2024%20summer%20quarterly%20report_08-136-u.pdf).
All intervals use explicit UTC offsets and half-open boundaries. None establishes
whether a hypothetical site lost power.

Source URLs, retrieval times, SHA-256 hashes, interval identifiers, and per-hour
comparisons are recorded in [emergency-evidence-audit.json](emergency-evidence-audit.json).
Original PDFs and their parquet byte caches remain under ignored `data/raw/spp/evidence/`.

## Reproduce

Prepare the seven-year inputs as described in [MODEL_EVALUATION_2025.md](MODEL_EVALUATION_2025.md), then run:

```powershell
python -m pipeline.events --hourly data/processed/ml_inputs/spp_2019_2025_multifactor.parquet --catalog docs/spp-event-evidence.json --out data/processed/ml_inputs/spp_2019_2025_documented_events.parquet
```

Choose a fresh output path on a repeat run. The command writes the observed
minutes, source catalog fingerprint, per-hour CSV, and evidence summary. For the
table above, group matched rows by UTC year, count `load_mw >= 42064.0228`, and sum
`observed_eea_minutes` within that subset.

The join now rejects missing source provenance, duplicate event IDs, unresolved
source references, and malformed hourly timestamps. It correctly preserves
distinct hours sharing a pandas index label. Cached document reuse verifies the
document hash and source metadata and fails without replacing a damaged cache.

Validation: **1,759 Python tests and 42 subtests passed**. Only two existing
FastAPI/Starlette dependency warnings remain; the event-join timedelta warnings
are resolved. The actual seven-year join and direct parquet inspection completed.
