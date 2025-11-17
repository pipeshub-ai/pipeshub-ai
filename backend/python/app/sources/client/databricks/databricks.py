"""
databricks.py
--------------
Client for interacting with the Databricks REST API using a Personal Access Token.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import requests


class DataBricksClient:
    """Client encapsulating Databricks REST API calls."""

    def __init__(self, host: str, token: str):
        """
        Initialize the Databricks client.

        Args:
            host (str): Base URL of your Databricks instance (e.g., https://adb-12345.6.clouddatabricks.com)
            token (str): Databricks Personal Access Token
        """
        if not host.startswith("http"):
            raise ValueError("Host must start with http:// or https://")

        self.host = host.rstrip("/")
        self.token = token
        self.base_url = f"{self.host}/api/2.0"
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {self.token}"})

    # Internal Request Helpers
    def _get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Generic GET request handler with error handling."""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        response = self.session.get(url, params=params or {})
        response.raise_for_status()
        return response.json()


    #  Public API Methods
    def list_clusters(self) -> Dict[str, Any]:
        """Fetch the list of clusters in the Databricks workspace."""
        return self._get("clusters/list")

    def get_cluster_info(self, cluster_id: str) -> Dict[str, Any]:
        """Fetch detailed info for a specific cluster."""
        return self._get("clusters/get", params={"cluster_id": cluster_id})

    def list_jobs(self) -> Dict[str, Any]:
        """Fetch the list of jobs configured in the Databricks workspace."""
        return self._get("jobs/list")

    def get_job_info(self, job_id: str) -> Dict[str, Any]:
        """Fetch detailed info for a specific job."""
        return self._get("jobs/get", params={"job_id": job_id})



    # Health Check or Version
    def get_workspace_status(self) -> Dict[str, Any]:
        """Check the Databricks workspace status or version info."""
        try:
            return self._get("workspace/get-status")
        except Exception:
            return {"status": "reachable"}
