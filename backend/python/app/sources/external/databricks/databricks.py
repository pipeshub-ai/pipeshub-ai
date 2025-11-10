"""
databricks.py
--------------
Datasource wrapper for the Databricks client to integrate with the PipesHub platform.
"""

from backend.python.app.sources.client.databricks.databricks import DataBricksClient


class DataBricksDataSource:
    """Data source abstraction that exposes Databricks APIs for PipesHub."""

    def __init__(self, client: DataBricksClient):
        self.client = client

    def fetch_clusters(self):
        """
        Retrieve clusters using the Databricks client.
        Returns a list of cluster metadata dictionaries.
        """
        data = self.client.list_clusters()
        return data.get("clusters", [])

    def fetch_jobs(self):
        """
        Retrieve jobs using the Databricks client.
        Returns a list of job configuration dictionaries.
        """
        data = self.client.list_jobs()
        return data.get("jobs", [])

    def fetch_cluster_info(self, cluster_id: str):
        """Fetch detailed information for a specific cluster."""
        return self.client.get_cluster_info(cluster_id)

    def fetch_job_info(self, job_id: str):
        """Fetch detailed information for a specific job."""
        return self.client.get_job_info(job_id)
