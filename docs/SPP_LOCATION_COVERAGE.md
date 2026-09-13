# SPP locations and nearby data

The local estimator accepts geographic regions rather than a fixed list of cities. Enter a city with a state name/abbreviation, or latitude and longitude. A unique match continues automatically; ambiguous names still require selection. Open-Meteo/GeoNames is supplemented by the [2025 Census national places directory](https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.2025.html), containing 32,350 places in the downloaded snapshot. `St.`/`Saint`, `Ft.`/`Fort`, accents and common Census place suffixes are normalized for lookup.

## Regional eligibility

1. An existing historical SPP utility match remains accepted.
2. A point inside or within **100 km** of the historical SPP outline receives a regional comparison. The outline is a [University of Oklahoma ArcGIS mirror of historical HIFLD geography](https://services.arcgis.com/3xOwF6p0r7IHIjfn/ArcGIS/rest/services/SPP_Boundary/FeatureServer/0), with 2019 source-year attributes. It is not an official 2026 electrical boundary. Polygon holes are respected; distances are approximate surface distances, not transmission-path distances.
3. Western areas also qualify when a point or its 100-km neighborhood intersects older retail polygons associated with documented expansion participants or their member utilities. Names are reconciled against the [PNNL historical utility map](https://eedgis.pnnl.gov/arcgis/rest/services/Hosted/Electric_Service_Territories/FeatureServer/0). This intentionally broad comparison policy uses whole retail territories; wholesale membership does not establish that every customer is electrically inside the RTO.

The 100-km allowance is an explicit **modeling assumption**, following the user's request for general regional coverage. It can admit a nearby city served by another grid. Such results say **regional comparison**, not verified SPP membership. Houston and Seattle did not qualify in the validation run. Grid-service products such as Markets+ are not treated as RTO territory.

Western name mappings were reviewed September 13, 2026 against [SPP's April expansion announcement](https://spp.org/news-list/spp-and-member-utilities-successfully-complete-historic-western-expansion/), [Tri-State's members](https://tristate.coop/member-list), [Deseret's cooperatives](https://deseretpower.com/member-cooperatives/), [Platte River's communities](https://prpa.org/about-prpa/who-we-serve/), and [Central Montana's purchasing cooperatives](https://cmepc.org/member-cooperatives). Broad NorthWestern/WAPA company polygons are not blanket approvals for entire western states; nonpurchasing Central Montana members are excluded from the mappings.

**Western estimates use historical SPP East patterns as an analogy.** The existing model's 2019–2024 grid history and four reference-area tests do not validate 2026 SPP West operation, individual cities or actual data-center interruptions. Confidence remains Low and site exposure remains a visible user assumption.

## Weather fallback

The app first requests the original coordinates. ERA5 is gridded reanalysis, so even this request returns a nearby grid cell. If that history is unavailable or has less than **99% nonmissing temperature in any input month**, the app tries up to three complete cached histories within 100 km, closest first, then up to three nearby quarter-degree requests. Returned provider grid coordinates must also be within 100 km. These distance/completeness limits are recorded assumptions, not validated weather-equivalence thresholds; terrain can vary within that distance.

One weather location supplies the entire history. The query retains shared historical SPP demand/wind/solar, with its local load and outcomes unknown. Another area's load or stress labels are never substituted. If every bounded attempt fails, the app reports unavailable data rather than inventing temperatures or hours.

`site_report.json` retains the requested coordinates, actual weather request, provider grid coordinates, offsets, attempted fallbacks, monthly coverage, source URLs, retrieval dates and hashes. All external pulls are cached in ignored parquet files. The result screen and text export include a short data-match note. Saved-result reads do not fetch or train.

## Verification on real data

The live lookup matrix accepted representative locations in all 17 SPP states: Wamego KS, Scottsbluff NE, Fargo ND, Sioux Falls SD, Rock Rapids IA, Luverne MN, Joplin MO, Texarkana AR, Shreveport LA, Clovis NM, Amarillo TX, Pine Bluffs WY, Durango CO, Page AZ, Roosevelt UT, Havre MT and Tulsa OK. Lewistown MT, Colorado Springs CO and Fort Collins CO also passed. This is representative verification, not a claim that every city or parcel has been independently verified.

Complete real-weather reports succeeded for Colorado Springs, Page and Wamego with no manual coverage override, reusing the trained model. A simulated provider outage at coordinates near Wichita successfully selected an existing real weather history 0.9 km away (provider grid cell 9.7 km away), with complete monthly temperature coverage. All 80 Python tests and 10 JavaScript behavior checks passed. The underlying labels, calibration and regional model implementation were unchanged.
