# backend/python/app/sources/external/workday/workday.py
"""
WorkdayDataSource — generated data source adapter for Workday API.
"""

import logging
from typing import Any, Dict, Optional

from app.sources.client.workday.workday import WorkdayClient


class WorkdayDataSource:
    def __init__(self, client: WorkdayClient) -> None:
        self._client = client
        self.logger = logging.getLogger(__name__)
        if self._client is None:
            raise ValueError("Workday client is not initialized")

    def get_data_source(self) -> "WorkdayDataSource":
        return self

    # Example: list workers
    def list_workers(self, limit: int = 100, offset: Optional[int] = None) -> Dict[str, Any]:
        """Fetch a list of workers."""
        try:
            params = {"limit": limit}
            if offset:
                params["offset"] = offset
            self.logger.info(f"Fetching Workday workers (limit={limit}, offset={offset})")
            result = self._client.request("get", "workers", params=params)
            return result
        except Exception as exc:
            self.logger.error(f"Failed to fetch Workday workers: {exc}")
            raise

    # Example: get a single worker
    def get_worker(self, worker_id: str) -> Dict[str, Any]:
        """Fetch a single worker by ID."""
        try:
            self.logger.info(f"Fetching worker with ID: {worker_id}")
            result = self._client.request("get", f"workers/{worker_id}")
            return result
        except Exception as exc:
            self.logger.error(f"Failed to get Workday worker {worker_id}: {exc}")
            raise

    # Example: run a Workday custom report (RaaS)
    def run_report(self, report_path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Run a Workday custom report using RaaS."""
        try:
            self.logger.info(f"Running Workday report: {report_path}")
            result = self._client.request("get", report_path, params=params or {})
            return result
        except Exception as exc:
            self.logger.error(f"Failed to run Workday report {report_path}: {exc}")
            raise
