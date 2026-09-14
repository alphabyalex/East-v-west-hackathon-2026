"""BigQuery Weather Extractor for SPP Climatology Study.

Queries the NOAA Global Summary of the Day (GSOD) public dataset on BigQuery
to fetch daily weather features (mean temperature, wind speed, max gust) for
critical SPP weather stations from 2019 through 2024.
"""
import os
import pandas as pd
from google.cloud import bigquery

def extract_spp_weather():
    client = bigquery.Client(project="eastwest72hack26bos-518")
    
    # Station definitions for key SPP load centers:
    # Oklahoma City (KOKC), Amarillo (KAMA), Wichita (KICT), Lincoln (KLNK)
    stations = {
        "KOKC": {"usaf": "723530", "wban": "13967"},
        "KAMA": {"usaf": "723630", "wban": "23047"},
        "KICT": {"usaf": "724500", "wban": "03928"},
        "KLNK": {"usaf": "725510", "wban": "14939"},
    }
    
    print("Extracting weather data from BigQuery public NOAA GSOD tables...")
    
    # Build a query that combines data from 2019 to 2024
    years = [2019, 2020, 2021, 2022, 2023, 2024]
    union_queries = []
    
    for year in years:
        table_name = f"bigquery-public-data.noaa_gsod.gsod{year}"
        sub_query = f"""
        SELECT 
            CONCAT(year, '-', mo, '-', da) AS date,
            stn AS usaf,
            wban,
            temp AS mean_temp_f,
            CAST(wdsp AS FLOAT64) AS mean_wind_speed_knots,
            gust AS max_gust_knots
        FROM `{table_name}`
        WHERE 
            (stn = '723530' AND wban = '13967') OR -- KOKC
            (stn = '723630' AND wban = '23047') OR -- KAMA
            (stn = '724500' AND wban = '03928') OR -- KICT
            (stn = '725510' AND wban = '14939')    -- KLNK
        """
        union_queries.append(sub_query)
        
    full_query = "\nUNION ALL\n".join(union_queries) + "\nORDER BY date, usaf, wban"
    
    print("Executing SQL query...")
    query_job = client.query(full_query)
    results = query_job.to_dataframe()
    
    print(f"Extracted {len(results)} rows.")
    
    # Map USAF/WBAN back to callsigns
    reverse_map = {
        ("723530", "13967"): "KOKC",
        ("723630", "23047"): "KAMA",
        ("724500", "03928"): "KICT",
        ("725510", "14939"): "KLNK"
    }
    
    results["station_id"] = results.apply(
        lambda row: reverse_map.get((row["usaf"], row["wban"]), "UNKNOWN"), axis=1
    )
    
    # Remove entries where station could not be matched
    results = results[results["station_id"] != "UNKNOWN"].reset_index(drop=True)
    
    # Clean default missing values represented as 999.9 or 99.9 in GSOD
    results["max_gust_knots"] = results["max_gust_knots"].apply(
        lambda x: None if x > 900 else x
    )
    
    os.makedirs("data/processed/research", exist_ok=True)
    out_path = "data/processed/research/bq_spp_multi_station_weather.parquet"
    results.to_parquet(out_path, index=False)
    print(f"Weather data successfully saved to {out_path}")
    
    # Display some summaries
    summary = results.groupby("station_id").agg({
        "mean_temp_f": ["mean", "min", "max"],
        "mean_wind_speed_knots": ["mean", "max"],
        "max_gust_knots": ["mean", "max"]
    })
    print("\nSummary statistics of extracted climate variables:")
    print(summary)

if __name__ == "__main__":
    extract_spp_weather()
