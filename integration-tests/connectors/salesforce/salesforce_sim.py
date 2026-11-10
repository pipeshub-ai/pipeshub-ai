"""Client for the Salesforce simulator's control API (pipeshub-ai/connectors-simulator,
``connsim/sources/salesforce``; ``/_sim/...``)."""

from __future__ import annotations

import json
from typing import Any

import requests

from connectors.salesforce.constants import SALESFORCE_SIM_URL


class SalesforceSim:
    def __init__(self, base_url: str = SALESFORCE_SIM_URL, timeout: float = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _call(self, method: str, path: str, body: Any = None, **params: Any) -> Any:
        resp = requests.request(method, f"{self.base_url}{path}", json=body, params=params or None,
                                timeout=self.timeout)
        resp.raise_for_status()
        return resp.json() if resp.content else None

    def reachable(self) -> bool:
        try:
            return requests.get(f"{self.base_url}/_sim/health", timeout=5).ok
        except requests.RequestException:
            return False

    def load(self, scenario: dict[str, Any]) -> dict[str, Any]:
        self._call("DELETE", "/_sim/faults")
        self._call("DELETE", "/_sim/salesforce/faults")
        return self._call("PUT", "/_sim/salesforce/scenario", scenario)

    def ops(self, *ops: dict[str, Any]) -> list[Any]:
        return self._call("POST", "/_sim/salesforce/ops", list(ops))["results"]

    def expected(self, email: str, filters: dict[str, Any] | None = None) -> dict[str, str]:
        """external record id -> record type, for what ``email`` may read in Salesforce, under the
        connector's sync filters as PipesHub stores them."""
        params: dict[str, Any] = {"email": email}
        if filters:
            params["filters"] = json.dumps(filters)
        return self._call("GET", "/_sim/salesforce/expected", **params)["records"]

    def unmodeled(self) -> list[str]:
        return self._call("GET", "/_sim/salesforce/unmodeled")

    def fail_queries(self, soql_contains: str, status: int = 503, times: int = 1) -> None:
        """Fail the next ``times`` SOQL queries whose text contains ``soql_contains``. The simulator's core
        faults (``/_sim/faults``) match paths only, and every SOQL query has the same path."""
        self._call("PUT", "/_sim/salesforce/faults",
                   {"rules": [{"soql_contains": soql_contains, "status": status, "times": times}]})

    def clear_faults(self) -> None:
        self._call("DELETE", "/_sim/salesforce/faults")
        self._call("DELETE", "/_sim/faults")
