import os
from google.cloud import bigquery

client = bigquery.Client(project="eastwest72hack26bos-518")

def check_latest_dates():
    print("Checking maximum available dates in GCP public datasets...")
    
    # 1. NOAA GSOD 2025
    try:
        query_gsod = "SELECT MAX(PARSE_DATE('%Y%m%d', CONCAT(year, mo, da))) as max_date FROM `bigquery-public-data.noaa_gsod.gsod2025`"
        res = client.query(query_gsod).to_dataframe()
        print(f"Latest NOAA GSOD 2025 Date: {res['max_date'].iloc[0]}")
    except Exception as e:
        print(f"Error checking GSOD 2025: {e}")

check_latest_dates()
