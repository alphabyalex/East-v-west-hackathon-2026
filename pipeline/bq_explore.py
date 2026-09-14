import os
from google.cloud import bigquery

client = bigquery.Client(project="eastwest72hack26bos-518")

def test_query():
    query = """
    SELECT DISTINCT state
    FROM `bigquery-public-data.noaa_historic_severe_storms.storms_2023`
    ORDER BY state
    """
    df = client.query(query).to_dataframe()
    print("States in Storms 2023:")
    print(df.to_string(index=False))

test_query()
