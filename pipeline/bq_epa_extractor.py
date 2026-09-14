"""EPA Air Quality Extractor for Grid Stress Thesis.

Extracts daily NO2 and PM2.5 levels from BigQuery EPA Historical Air Quality
public dataset to measure the environmental impact (peaker plant emissions)
during severe grid stress events.
"""
import os
import pandas as pd
from google.cloud import bigquery

def extract_epa_air_quality():
    client = bigquery.Client(project="eastwest72hack26bos-518")
    
    print("Extracting NO2 emissions data from BigQuery public EPA tables (2019-2024)...")
    
    # We query NO2 because it is a direct byproduct of fossil fuel combustion 
    # (specifically Natural Gas peaker plants starting up).
    query = """
    SELECT 
        CAST(date_local AS STRING) AS date,
        state_name,
        city_name,
        AVG(aqi) AS avg_no2_aqi,
        MAX(first_max_value) AS max_no2_value
    FROM `bigquery-public-data.epa_historical_air_quality.no2_daily_summary`
    WHERE EXTRACT(YEAR FROM date_local) BETWEEN 2019 AND 2025
    AND city_name IN ('New York', 'Boston', 'Atlanta', 'Chicago', 'Lincoln', 'Houston', 'Dallas', 'Denver', 'Phoenix', 'Los Angeles', 'Seattle', 'Oklahoma City', 'Amarillo', 'Wichita')
    GROUP BY date, state_name, city_name
    """
    
    print("Executing SQL query for EPA NO2...")
    df_no2 = client.query(query).to_dataframe()
    print(f"Extracted {len(df_no2)} rows of NO2 data.")
    
    os.makedirs("data/processed/research", exist_ok=True)
    out_path = "data/processed/research/bq_epa_no2_emissions.parquet"
    df_no2.to_parquet(out_path, index=False)
    print(f"EPA emissions data successfully saved to {out_path}")

if __name__ == "__main__":
    extract_epa_air_quality()
