# ruff: noqa
from __future__ import annotations

import splunklib.client as splunk_client  # type: ignore[import-untyped]
import splunklib.results as splunk_results  # type: ignore[import-untyped]
from typing import Dict, List, Optional, Union, cast

from app.sources.client.splunk.splunk import SplunkResponse

class SplunkDataSource:
    """
    Strict, typed wrapper over splunk-sdk for common Splunk operations.

    Accepts either a splunklib `Service` instance *or* any object with `.get_sdk() -> Service`.
    """

    def __init__(self, client_or_sdk: Union[splunk_client.Service, object]) -> None:
        super().__init__()
        # Support a raw SDK or a wrapper that exposes `.get_sdk()`
        if hasattr(client_or_sdk, "get_sdk"):
            sdk_obj = getattr(client_or_sdk, "get_sdk")()
            self._sdk: splunk_client.Service = cast(splunk_client.Service, sdk_obj)
        else:
            self._sdk = cast(splunk_client.Service, client_or_sdk)

    # ---- helpers ----
    @staticmethod
    def _params(**kwargs: object) -> Dict[str, object]:
        # Filter out Nones to avoid overriding SDK defaults
        out: Dict[str, object] = {}
        for k, v in kwargs.items():
            if v is None:
                continue
            if isinstance(v, (list, dict)) and len(v) == 0:
                continue
            out[k] = v
        return out
    def get_server_info(self) -> SplunkResponse:
        """Get Splunk server information."""
        info = self._sdk.info
        return SplunkResponse(success=True, data=info)
    def search(self, query: str, earliest_time: Optional[str] = None, latest_time: Optional[str] = None, max_count: Optional[int] = None, exec_mode: Optional[str] = None) -> SplunkResponse:
        """Run a search query and return results."""
        params = self._params(earliest_time=earliest_time, latest_time=latest_time, max_count=max_count, exec_mode=exec_mode)
        job = self._sdk.jobs.create(query, **params)
        while not job.is_done():
            import time
            time.sleep(0.5)
        rr = splunk_results.JSONResultsReader(job.results(output_mode='json'))
        results = [result for result in rr if isinstance(result, dict)]
        return SplunkResponse(success=True, data=results)
    def list_saved_searches(self) -> SplunkResponse:
        """List all saved searches."""
        items = list(self._sdk.saved_searches)
        return SplunkResponse(success=True, data=items)
    def get_saved_search(self, name: str) -> SplunkResponse:
        """Get a saved search by name."""
        ss = self._sdk.saved_searches[name]
        return SplunkResponse(success=True, data=ss)
    def list_indexes(self) -> SplunkResponse:
        """List all indexes."""
        items = list(self._sdk.indexes)
        return SplunkResponse(success=True, data=items)
    def get_index(self, name: str) -> SplunkResponse:
        """Get an index by name."""
        idx = self._sdk.indexes[name]
        return SplunkResponse(success=True, data=idx)
    def list_apps(self) -> SplunkResponse:
        """List all installed apps."""
        items = list(self._sdk.apps)
        return SplunkResponse(success=True, data=items)
    def get_app(self, name: str) -> SplunkResponse:
        """Get an app by name."""
        app = self._sdk.apps[name]
        return SplunkResponse(success=True, data=app)
    def list_users(self) -> SplunkResponse:
        """List all users."""
        items = list(self._sdk.users)
        return SplunkResponse(success=True, data=items)
    def list_jobs(self) -> SplunkResponse:
        """List all search jobs."""
        items = list(self._sdk.jobs)
        return SplunkResponse(success=True, data=items)
    def get_job(self, sid: str) -> SplunkResponse:
        """Get a search job by SID."""
        job = self._sdk.jobs[sid]
        return SplunkResponse(success=True, data=job)
    def list_inputs(self) -> SplunkResponse:
        """List all data inputs."""
        items = list(self._sdk.inputs)
        return SplunkResponse(success=True, data=items)

