"""Massive Comprehensive Climatology Extraction for Nature Energy Thesis.

Extracts NOAA GSOD Weather, NOAA Lightning Strikes, and EPA Air Quality
for 14 major US load centers, performing spatial and temporal joins on BigQuery.
"""
import os
import pandas as pd
from google.cloud import bigquery

def extract_comprehensive_data():
    client = bigquery.Client(project="eastwest72hack26bos-518")
    
    print("Executing massive geospatial join on BigQuery (NOAA GSOD + NOAA Lightning)...")
    
    # 14 major load centers across the US with their approximate Lat/Lon coordinates
    cities = [
        {"id": "KNYC", "lat": 40.7128, "lon": -74.0060},
        {"id": "KBOS", "lat": 42.3601, "lon": -71.0589},
        {"id": "KATL", "lat": 33.7490, "lon": -84.3880},
        {"id": "KORD", "lat": 41.8781, "lon": -87.6298},
        {"id": "KLNK", "lat": 40.8136, "lon": -96.7026},
        {"id": "KIAH", "lat": 29.7604, "lon": -95.3698},
        {"id": "KDFW", "lat": 32.7767, "lon": -96.7970},
        {"id": "KDEN", "lat": 39.7392, "lon": -104.9903},
        {"id": "KPHX", "lat": 33.4484, "lon": -112.0740},
        {"id": "KLAX", "lat": 34.0522, "lon": -118.2437},
        {"id": "KSEA", "lat": 47.6062, "lon": -122.3321},
        {"id": "KOKC", "lat": 35.4676, "lon": -97.5164},
        {"id": "KAMA", "lat": 35.2220, "lon": -101.8313},
        {"id": "KICT", "lat": 37.6872, "lon": -97.3301},
    ]
    
    # Build BigQuery Geography points
    city_geoms = []
    for c in cities:
        city_geoms.append(f"SELECT '{c['id']}' as station_id, ST_GEOGPOINT({c['lon']}, {c['lat']}) as geom")
    
    cities_cte = " UNION ALL ".join(city_geoms)
    
    query = f"""
    CREATE OR REPLACE TABLE `eastwest72hack26bos-518.grid_stress_research.us_lightning_daily` AS
    WITH cities AS (
        {cities_cte}
    )
    SELECT 
        EXTRACT(DATE FROM l.date) AS date,
        c.station_id,
        SUM(l.number_of_strikes) AS total_lightning_strikes
    FROM `bigquery-public-data.noaa_lightning.lightning_strikes` l
    CROSS JOIN cities c
    WHERE EXTRACT(YEAR FROM l.date) BETWEEN 2019 AND 2024
    AND ST_DWithin(l.center_point_geom, c.geom, 50000) -- within 50km radius
    GROUP BY date, station_id
    """
    
    print("Extracting lightning data spatially...")
    client.query(query).result()
    print("Table grid_stress_research.us_lightning_daily created.")
    
    # Now merge it all into one super table
    super_query = """
    CREATE OR REPLACE TABLE `eastwest72hack26bos-518.grid_stress_research.us_comprehensive_grid_stress` AS
    SELECT 
        w.*,
        IFNULL(l.total_lightning_strikes, 0) AS total_lightning_strikes
    FROM `eastwest72hack26bos-518.grid_stress_research.us_weather_features` w
    LEFT JOIN `eastwest72hack26bos-518.grid_stress_research.us_lightning_daily` l
        ON w.date = CAST(l.date AS STRING) AND w.station_id = l.station_id
    """
    
    print("Joining weather and lightning features...")
    client.query(super_query).result()
    print("Table grid_stress_research.us_comprehensive_grid_stress created.")

if __name__ == "__main__":
    extract_comprehensive_data()
