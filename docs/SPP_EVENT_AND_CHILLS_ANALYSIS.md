# SPP event evidence and CHILLS exposure

Research snapshot: September 12, 2026 Pacific / September 13 UTC. Historical reports complement the current-grid-conditions page: a system-wide banner can miss local transmission problems.

## Confirmed cases

| Period (Central local time) | Observation | Interpretation |
|---|---|---|
| July 16; August 1, 2, 26 and 27, 2024 | Conservative Operations on five dates; exact intraday intervals unavailable in this source. | Watch-list dates, not five full days of emergencies or outages. |
| August 26, 2024, 12:30–15:00 CDT | SPP-wide EEA1: **150 minutes**, with high heat/load, low wind and resource outages. | An observed system-stress window in the existing dataset. |
| April 26, 2025, Shreveport | 140 MW firm-load-shed instruction at 15:12 CDT; completed by 16:15, lifted at 19:11; all load restored by 21:23. | Actual local interruption evidence. These milestones do not establish a uniform customer outage duration. |

Advisory dates: [SPP's December 2024 Stakeholder Report](https://spp.org/newsroom/stakeholder-report/). The EEA interval and drivers are documented on PDF pages 65–66 (exhibit pages 62–63) of the [2024 summer report](https://spp.org/documents/72631/20241101_2024%20summer%20quarterly%20report_08-136-u.pdf). SPP also called on behind-the-meter generation during that EEA.

The [Shreveport event report](https://spp.org/Documents/74283/SPP%27s%20Summary%20of%20the%20April%2026,%202025,%20Shreveport-Area%20Load%20Shed%20Event.pdf), PDF pages 13, 15 and 22, connects warmer-than-forecast weather with local demand and voltage problems amid generation/transmission limitations. The high reached 90°F, 3–5°F above forecast; area demand was approximately 300 MW above forecast. At 14:27, MISO reported a contingency-related transformer loading concern. This supports tracking local constraints and weather forecast errors; it does not establish a general 90°F cutoff threshold.

## Join to the existing data

The existing example associates Amarillo weather with aggregate `SPP_SYSTEM` load. Amarillo is an example, not a confirmed user site. The August 26 emergency overlaps these hourly interval-start observations:

| Hour start CDT | Hour start UTC | SPP load MW | Amarillo °C | EEA overlap minutes |
|---|---|---:|---:|---:|
| 12:00 | 17:00 | 48,570.310 | 30.6 | 30 |
| 13:00 | 18:00 | 50,114.133 | 31.6 | 60 |
| 14:00 | 19:00 | 50,887.832 | 32.4 | 60 |

This is **2.5 observed emergency hours across three hourly buckets**, not site downtime, a complete annual emergency total, or a future prediction. Contemporaneous weather describes the case; predictive features still use prior observations. Reanalysis also requires an availability-aware evaluation before operational use.

Load source: [SPP 2024 archive](https://portal.spp.org/file-browser-api/download/hourly-load?path=/2024/2024.zip). Temperature source: cached ERA5 hourly 2 m temperature through Open-Meteo; see [temperature preparation/provenance](TEMPERATURE_DATA.md). All 8,784 input hours and the three unknown load observations are preserved.

## Applying the new rules

The [June 5, 2026 FERC order](https://spp.org/documents/76880/20260605_order%20-%20revisions%20to%20add%20the%20conditional%20high%20impact%20large%20load%20service_er26-1323.pdf) accepted CHILLS revisions, subject to a compliance condition, effective July 1, 2026. CHILLS has lower priority than firm transmission and priority equivalent to monthly non-firm point-to-point service. Pages 13–14 describe whole or partial reductions for transmission limitations, emergencies, local reliability issues and system CHILL curtailments. Under the supporting-generation route, insufficient available supporting capacity is another trigger. Application depends on the site's operating arrangement.

SPP's [September 2 clarification filing](https://www.spp.org/Documents/77674/20260902_Revisions%20to%20Clarify%20the%20Conditional%20High%20Impact%20Large%20Load%20Policy_ER26-3685-000.pdf) requests a later Commission order and targets implementation in Q2 2027. Treat it as a proposal in this snapshot, not an already operating rule. The 2024/2025 events predate CHILLS: applying them to CHILLS is a counterfactual stress analysis.

| Evidence | Conditional-service screening implication |
|---|---|
| High temperature or Conservative Operations alone | Monitor conditions; insufficient to conclude a particular site will be reduced. |
| EEA1 with high demand, low wind and outages | Evaluate an observed exposure episode against the site's contract and local constraints. No automatic site cutoff follows from EEA1. |
| Worsening reserve emergency plus limited imports/generation | Stronger system-stress evidence; combine with applicable operating provisions. |
| Local transmission/voltage issue, applicable reduction instruction, or supporting-generation deficit | More direct evidence relevant to reduction triggers; the system-wide banner may still appear normal. |

These are qualitative inferences, not model risk bands or calibrated probabilities. Loss of grid supply also differs from computing downtime when backup generation, storage or workload migration is available. Site inputs still needed: city/point of interconnection, utility, MW, firm/CHILLS split, supporting-resource availability and operating agreement.

## Reproduction and coverage

[spp-event-evidence.json](spp-event-evidence.json) records source URLs, retrieval times, hashes, region IDs and time precision. Public downloads are cached to ignored parquet under `data/raw/spp/evidence`. This selected catalog is not a complete alert archive. OATI's historical archive could not be retrieved with valid TLS here; certificate checks were not disabled.

After preparing the load/weather input:

```powershell
.\.venv\Scripts\python.exe -m pipeline.events --hourly data/processed/ml_inputs/spp_2024_amarillo_temperature.parquet --out data/processed/ml_inputs/spp_2024_amarillo_grid_events.parquet
```

Outputs contain `observed_eea_minutes`, source event IDs, and `.evidence.json`/`.events.csv` companions. Duplicate overlaps are unioned. Date-only advisories and local Shreveport events cannot become system EEA hours. Unreported hours remain unknown. The local workspace can inspect the dataset and evidence provenance.

This join creates no `event_active` training labels. A reviewed target and adequately covered positive and negative periods remain necessary; one EEA cannot support chronological training, calibration and held-out evaluation. Existing minimum-data checks remain intact. No real-data model or predicted cutoff hours were fabricated. Priority additions are local constraint/voltage records, reserves, wind forecasts, outages, imports and weather/load forecasts with issue timestamps. PJM observations cannot substitute for SPP labels.
