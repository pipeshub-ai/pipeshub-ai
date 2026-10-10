"""Client and constants for the strict OpenAPI audit of /api/v1/health."""

from __future__ import annotations

from typing import Any

import requests

from helper.http.api_client import APIClient

HEALTH_BASE = "/api/v1/health"
HEALTH_ROOT_ROUTE = HEALTH_BASE
HEALTH_SERVICES_ROUTE = f"{HEALTH_BASE}/services"

OVERALL_STATUSES = ("healthy", "unhealthy")
# "pending" is graphDb/vectorDb before Python has written the deployment config;
# "unknown" is the /services fallback when the probe itself throws.
SERVICE_STATUSES = ("healthy", "unhealthy", "unknown", "pending")

INFRA_SERVICE_KEYS = ("redis", "messageBroker", "mongodb", "graphDb", "vectorDb")
# Present in services and serviceNames only when the KV store is etcd.
ETCD_SERVICE_KEY = "KVStoreservice"
DEPLOYMENT_KEYS = (
    "kvStoreType",
    "messageBrokerType",
    "graphDbType",
    "vectorDbType",
    "redisMode",
)

PYTHON_SERVICE_KEYS = ("query", "connector", "indexing", "docling", "embedding")
# Present only when the Node process runs with USE_PARSING_SERVICE=true.
PARSING_SERVICE_KEYS = ("parsing", "extraction")

MALFORMED_JSON_BODY = "{not json"
JSON_HEADERS = {"Content-Type": "application/json"}


class HealthClient(APIClient):
    """Client for /api/v1/health; the router has no auth middleware."""

    BASE = HEALTH_BASE

    def root(self, *, auth: bool = True, **kwargs: Any) -> requests.Response:
        return self.get("", auth=auth, **kwargs)

    def services(self, *, auth: bool = True, **kwargs: Any) -> requests.Response:
        return self.get("/services", auth=auth, **kwargs)
