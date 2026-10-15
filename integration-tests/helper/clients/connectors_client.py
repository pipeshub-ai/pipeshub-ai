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

    # ------------------------------------------------------------ registry

    def get_registry(
        self,
        *,
        scope: str | None = None,
        page: int | None = None,
        limit: int | None = None,
        search: str | None = None,
        **options: Any,
    ) -> requests.Response:
        """List the connector types the org can add.

        GET /registry
        """
        params = {"scope": scope, "page": page, "limit": limit, "search": search}
        options.setdefault("params", {k: v for k, v in params.items() if v is not None})
        return self.get("/registry", **options)

    def get_egress_ips(self, **options: Any) -> requests.Response:
        """Public IPs connectors reach external systems from.

        GET /network/egress-ips
        """
        return self.get("/network/egress-ips", **options)

    def get_registry_schema(self, connector_type: str, **options: Any) -> requests.Response:
        """Auth fields, sync options and filters for one connector type.

        GET /registry/{connectorType}/schema
        """
        return self.get(f"/registry/{quote(connector_type, safe='')}/schema", **options)

    def test_connection(
        self,
        connector_type: str,
        auth_config: dict[str, Any],
        *,
        connector_id: str | None = None,
        **options: Any,
    ) -> requests.Response:
        """Try auth settings before they are saved.

        POST /registry/{connectorType}/test-connection
        """
        body: dict[str, Any] = {"auth": auth_config}
        if connector_id is not None:
            body["connectorId"] = connector_id
        return self.post(
            f"/registry/{quote(connector_type, safe='')}/test-connection", json=body, **options
        )

    # ----------------------------------------------------------- instances

    def list_instances(
        self,
        *,
        scope: str | None = None,
        page: int | None = None,
        limit: int | None = None,
        search: str | None = None,
        is_authenticated: bool | None = None,
        is_active: bool | None = None,
        connector_type: str | None = None,
        **options: Any,
    ) -> requests.Response:
        """List the org's connector instances.

        GET /
        """
        params = {
            "scope": scope,
            "page": page,
            "limit": limit,
            "search": search,
            "isAuthenticated": None if is_authenticated is None else str(is_authenticated).lower(),
            "isActive": None if is_active is None else str(is_active).lower(),
            "connectorType": connector_type,
        }
        options.setdefault("params", {k: v for k, v in params.items() if v is not None})
        return self.get("/", **options)

    def list_configured(
        self, *, scope: str | None = None, limit: int | None = None, **options: Any
    ) -> requests.Response:
        """GET /configured"""
        params = {"scope": scope, "limit": limit}
        options.setdefault("params", {k: v for k, v in params.items() if v is not None})
        return self.get("/configured", **options)

    def list_active(self, **options: Any) -> requests.Response:
        """GET /active"""
        return self.get("/active", **options)

    def list_inactive(self, **options: Any) -> requests.Response:
        """GET /inactive"""
        return self.get("/inactive", **options)

    def list_active_agents(self, **options: Any) -> requests.Response:
        """GET /agents/active"""
        return self.get("/agents/active", **options)

    def create_instance(self, body: dict[str, Any], **options: Any) -> requests.Response:
        """Create a connector instance.

        POST /
        """
        return self.post("/", json=body, **options)

    def get_instance(self, connector_id: str, **options: Any) -> requests.Response:
        """GET /{connectorId}"""
        return self.get(f"/{quote(connector_id, safe='')}", **options)

    def delete_instance(self, connector_id: str, **options: Any) -> requests.Response:
        """DELETE /{connectorId}"""
        return self.delete(f"/{quote(connector_id, safe='')}", **options)

    def toggle(
        self, connector_id: str, toggle_type: str = "sync", **options: Any
    ) -> requests.Response:
        """Flip sync or agent use on or off.

        POST /{connectorId}/toggle
        """
        options.setdefault("json", {"type": toggle_type})
        return self.post(f"/{quote(connector_id, safe='')}/toggle", **options)

    # ---------------------------------------------------------------- sync

    def get_stats(self, connector_id: str, **options: Any) -> requests.Response:
        """Record counts by indexing status.

        GET /{connectorId}/stats
        """
        return self.get(f"/{quote(connector_id, safe='')}/stats", **options)

    def resync(
        self,
        connector_id: str,
        connector_name: str,
        *,
        full_sync: bool | None = None,
        **options: Any,
    ) -> requests.Response:
        """Run another sync of an active connector.

        POST /{connectorId}/resync
        """
        body: dict[str, Any] = {"connectorName": connector_name}
        if full_sync is not None:
            body["fullSync"] = full_sync
        options.setdefault("json", body)
        return self.post(f"/{quote(connector_id, safe='')}/resync", **options)

    def stop_sync(self, connector_id: str, **options: Any) -> requests.Response:
        """POST /{connectorId}/sync/stop"""
        return self.post(f"/{quote(connector_id, safe='')}/sync/stop", **options)

    # -------------------------------------------------------------- config

    def get_config(self, connector_id: str, **options: Any) -> requests.Response:
        """GET /{connectorId}/config"""
        return self.get(f"/{quote(connector_id, safe='')}/config", **options)

    def update_config(
        self, connector_id: str, body: dict[str, Any], **options: Any
    ) -> requests.Response:
        """PUT /{connectorId}/config"""
        return self.put(f"/{quote(connector_id, safe='')}/config", json=body, **options)

    def update_auth_config(
        self, connector_id: str, body: dict[str, Any], **options: Any
    ) -> requests.Response:
        """PUT /{connectorId}/config/auth"""
        return self.put(f"/{quote(connector_id, safe='')}/config/auth", json=body, **options)

    def update_filters_sync_config(
        self, connector_id: str, body: dict[str, Any], **options: Any
    ) -> requests.Response:
        """PUT /{connectorId}/config/filters-sync"""
        return self.put(
            f"/{quote(connector_id, safe='')}/config/filters-sync", json=body, **options
        )

    def get_filters(self, connector_id: str, **options: Any) -> requests.Response:
        """GET /{connectorId}/filters"""
        return self.get(f"/{quote(connector_id, safe='')}/filters", **options)

    def save_filters(
        self, connector_id: str, body: dict[str, Any], **options: Any
    ) -> requests.Response:
        """POST /{connectorId}/filters"""
        return self.post(f"/{quote(connector_id, safe='')}/filters", json=body, **options)

    def get_filter_options(
        self,
        connector_id: str,
        filter_key: str,
        *,
        page: int | None = None,
        limit: int | None = None,
        search: str | None = None,
        cursor: str | None = None,
        **options: Any,
    ) -> requests.Response:
        """Values for a dynamic filter.

        GET /{connectorId}/filters/{filterKey}/options
        """
        params = {"page": page, "limit": limit, "search": search, "cursor": cursor}
        options.setdefault("params", {k: v for k, v in params.items() if v is not None})
        return self.get(
            f"/{quote(connector_id, safe='')}/filters/{quote(filter_key, safe='')}/options",
            **options,
        )

    # --------------------------------------------------------------- OAuth

    def get_oauth_authorization_url(
        self, connector_id: str, **options: Any
    ) -> requests.Response:
        """GET /{connectorId}/oauth/authorize"""
        return self.get(f"/{quote(connector_id, safe='')}/oauth/authorize", **options)

    def handle_oauth_callback(
        self,
        *,
        code: str | None = None,
        state: str | None = None,
        base_url: str | None = None,
        **options: Any,
    ) -> requests.Response:
        """Hand the provider's code and state back to PipesHub.

        GET /oauth/callback
        """
        params = {"code": code, "state": state, "baseUrl": base_url}
        options.setdefault("params", {k: v for k, v in params.items() if v is not None})
        return self.get("/oauth/callback", **options)


class ConnectorOAuthConfigsClient(APIClient):
    """Client for /api/v1/oauth: the OAuth apps connectors sign in with."""

    BASE = "/api/v1/oauth"

    def list_all_configs(
        self, *, page: int | None = None, limit: int | None = None, **options: Any
    ) -> requests.Response:
        """GET /  (OAuth apps of every connector type)"""
        params = {"page": page, "limit": limit}
        options.setdefault("params", {k: v for k, v in params.items() if v is not None})
        return self.get("/", **options)

    def list_configs(
        self,
        connector_type: str,
        *,
        page: int | None = None,
        limit: int | None = None,
        search: str | None = None,
        **options: Any,
    ) -> requests.Response:
        """GET /{connectorType}"""
        params = {"page": page, "limit": limit, "search": search}
        options.setdefault("params", {k: v for k, v in params.items() if v is not None})
        return self.get(f"/{quote(connector_type, safe='')}", **options)

    def get_registry(
        self,
        *,
        page: int | None = None,
        limit: int | None = None,
        search: str | None = None,
        **options: Any,
    ) -> requests.Response:
        """GET /registry  (connector types that sign in with an OAuth app)"""
        params = {"page": page, "limit": limit, "search": search}
        options.setdefault("params", {k: v for k, v in params.items() if v is not None})
        return self.get("/registry", **options)

    def get_registry_entry(self, connector_type: str, **options: Any) -> requests.Response:
        """GET /registry/{connectorType}"""
        return self.get(f"/registry/{quote(connector_type, safe='')}", **options)

    def create_config(
        self, connector_type: str, body: dict[str, Any], **options: Any
    ) -> requests.Response:
        """POST /{connectorType}"""
        return self.post(f"/{quote(connector_type, safe='')}", json=body, **options)

    def get_config(
        self, connector_type: str, config_id: str, **options: Any
    ) -> requests.Response:
        """GET /{connectorType}/{configId}"""
        return self.get(
            f"/{quote(connector_type, safe='')}/{quote(config_id, safe='')}", **options
        )

    def update_config(
        self, connector_type: str, config_id: str, body: dict[str, Any], **options: Any
    ) -> requests.Response:
        """PUT /{connectorType}/{configId}"""
        return self.put(
            f"/{quote(connector_type, safe='')}/{quote(config_id, safe='')}", json=body, **options
        )

    def delete_config(
        self, connector_type: str, config_id: str, **options: Any
    ) -> requests.Response:
        """DELETE /{connectorType}/{configId}"""
        return self.delete(
            f"/{quote(connector_type, safe='')}/{quote(config_id, safe='')}", **options
        )
