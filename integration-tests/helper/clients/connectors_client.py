"""Connector API client for integration tests."""

from typing import Any
from urllib.parse import quote

import requests

from helper.http.api_client import APIClient


class ConnectorsClient(APIClient):
    """Client for /api/v1/connectors endpoints."""

    BASE = "/api/v1/connectors"

    def get_record_content(self, record_id: str, **options: Any) -> requests.Response:
        """Get a record's full parsed content and metadata.

        GET /record/{recordId}/content
        """
        return self.get(f"/record/{quote(record_id, safe='')}/content", **options)

    def navigate(
        self,
        node_id: str | None = None,
        *,
        page: int | None = None,
        limit: int | None = None,
        depth: int | None = None,
        node_types: list[str] | None = None,
        created_after: str | None = None,
        created_before: str | None = None,
        modified_after: str | None = None,
        modified_before: str | None = None,
        **options: Any,
    ) -> requests.Response:
        """Walk the knowledge graph hierarchy.

        GET /navigate

        Omit node_id for the root listing of connected apps.
        """
        params: dict[str, Any] = {
            "nodeId": node_id,
            "page": page,
            "limit": limit,
            "depth": depth,
            # requests emits a list value as a repeated param, which is what
            # the gateway expects.
            "nodeTypes": node_types,
            "createdAfter": created_after,
            "createdBefore": created_before,
            "modifiedAfter": modified_after,
            "modifiedBefore": modified_before,
        }
        params = {k: v for k, v in params.items() if v is not None}
        options.setdefault("params", params)
        return self.get("/navigate", **options)

    def lookup_record(
        self,
        identifiers: list[str] | str,
        *,
        connector_name: str | None = None,
        **options: Any,
    ) -> requests.Response:
        """Resolve URLs, issue keys, or external IDs to Record IDs.

        GET /record/lookup
        """
        params: dict[str, Any] = {
            "identifiers": [identifiers] if isinstance(identifiers, str) else identifiers,
        }
        if connector_name is not None:
            params["connectorName"] = connector_name
        options.setdefault("params", params)
        return self.get("/record/lookup", **options)
