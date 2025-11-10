"""
example.py
-----------
Example runner script demonstrating Databricks data source integration.
"""

import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../..")))

from backend.python.app.sources.client.databricks.databricks import DataBricksClient
from backend.python.app.sources.external.databricks.databricks import (
    DataBricksDataSource,
)


def main():

    host = os.getenv("DATABRICKS_HOST")
    token = os.getenv("DATABRICKS_TOKEN")

    # Validate environment
    if not host or not token:
        print("Missing DATABRICKS_HOST or DATABRICKS_TOKEN environment variables.")
        print("Please set them before running:")
        print('   $env:DATABRICKS_HOST="https://your-workspace.cloud.databricks.com"')
        print('   $env:DATABRICKS_TOKEN="dapiXXXXXXXXXXXXXX"')
        sys.exit(1)

    print("Initializing Databricks Client...")
    client = DataBricksClient(host=host, token=token)
    datasource = DataBricksDataSource(client)

    # Fetch Clusters
    print("\n Fetching Clusters...")
    try:
        clusters = datasource.fetch_clusters()
        for cluster in clusters:
            print(f" - {cluster.get('cluster_name', 'Unnamed Cluster')}")
    except Exception as e:
        print(" Error fetching clusters:", e)

    # Fetch Jobs
    print("\n Fetching Jobs...")
    try:
        jobs = datasource.fetch_jobs()
        for job in jobs:
            print(f" - {job.get('settings', {}).get('name', 'Unnamed Job')}")
    except Exception as e:
        print(" Error fetching jobs:", e)


if __name__ == "__main__":
    main()
