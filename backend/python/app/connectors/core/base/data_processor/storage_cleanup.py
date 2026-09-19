"""Storage cleanup helper for blob lifecycle management.

Invoked from DataSourceEntitiesProcessor when records are deleted or moved to
ensure the corresponding blobs are purged / relocated in the storage backend.
The helper calls the Node.js storage service using the same scoped-JWT auth
pattern as BlobStorage.
"""

from typing import Any

import aiohttp

from app.config.constants.arangodb import CollectionNames
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import (
    DefaultEndpoints,
    Routes,
    TokenScopes,
    config_node_constants,
)
from app.utils.jwt import mint_service_token
from app.utils.request_context import inject_request_headers
from app.utils.storage_path import (
    build_hierarchical_storage_path,
    build_record_group_path as _build_record_group_path,
    build_record_group_prefix_from_chain,
)


class StorageCleanupHelper:
    """Handles blob storage cleanup when records are deleted or moved."""

    def __init__(self, logger, graph_provider, config_service) -> None:
        self.logger = logger
        self.graph_provider = graph_provider
        self.config_service = config_service
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

    # ------------------------------------------------------------------
    # Path building helpers (delegates to shared app.utils.storage_path)
    # ------------------------------------------------------------------

    async def build_record_path(
        self, record: Any, transaction: str | None = None
    ) -> str | None:
        """Build the hierarchical storage path for any record (file or folder).

        Returns None when the path cannot be reliably computed — callers
        must treat None as "skip this move".
        """
        return await build_hierarchical_storage_path(
            record,
            self.graph_provider,
            virtual_record_id=getattr(record, "virtual_record_id", None),
            transaction=transaction,
            logger=self.logger,
        )

    def build_record_group_path(
        self, connector_id: str | None, group_name: str | None
    ) -> str | None:
        """Build the storage path prefix for a record group."""
        return _build_record_group_path(connector_id, group_name)

    async def build_record_group_hierarchical_prefix(
        self,
        record_group_id: str,
        connector_id: str,
        *,
        override_leaf_name: str | None = None,
        transaction: str | None = None,
    ) -> str | None:
        """Build full hierarchical prefix for a record group via graph traversal.

        *override_leaf_name* replaces the leaf group's own name — used to
        compute the old prefix during renames when the graph already holds
        the new name.  Falls back to the flat single-segment prefix when the
        traversal returns nothing.
        """
        try:
            gp_kwargs: dict = {}
            if transaction is not None:
                gp_kwargs["transaction"] = transaction
            group_names = await self.graph_provider.get_record_group_path(
                record_group_id, **gp_kwargs
            )
        except Exception as e:
            self.logger.warning("get_record_group_path failed: %s", str(e))
            group_names = []

        if group_names and override_leaf_name is not None:
            group_names = group_names[:-1] + [override_leaf_name]

        if group_names:
            return build_record_group_prefix_from_chain(connector_id, group_names)

        leaf = override_leaf_name
        if not leaf:
            try:
                gp_kwargs = {}
                if transaction is not None:
                    gp_kwargs["transaction"] = transaction
                group = await self.graph_provider.get_record_group_by_id(
                    record_group_id, **gp_kwargs
                )
                if group:
                    leaf = group.get("groupName") or group.get("name", "")
            except Exception:
                pass
        return _build_record_group_path(connector_id, leaf) if leaf else None

    # ------------------------------------------------------------------
    # Internal auth / config helpers (mirrors BlobStorage._get_auth_and_config)
    # ------------------------------------------------------------------

    async def _get_auth_headers_and_endpoint(self, org_id: str) -> tuple[dict, str]:
        """Return (headers, nodejs_endpoint) for internal storage API calls."""
        payload = {
            "orgId": org_id,
            "scopes": [TokenScopes.STORAGE_TOKEN.value],
        }
        secret_keys = await self.config_service.get_config(
            config_node_constants.SECRET_KEYS.value
        )
        scoped_jwt_secret = secret_keys.get("scopedJwtSecret")
        if not scoped_jwt_secret:
            raise ValueError("Missing scoped JWT secret")

        jwt_token = mint_service_token(scoped_jwt_secret, payload)
        headers = inject_request_headers({"Authorization": f"Bearer {jwt_token}"})

        endpoints = await self.config_service.get_config(
            config_node_constants.ENDPOINTS.value
        )
        nodejs_endpoint = endpoints.get("cm", {}).get(
            "endpoint", DefaultEndpoints.NODEJS_ENDPOINT.value
        )
        if not nodejs_endpoint:
            raise ValueError("Missing CM endpoint configuration")

        return headers, nodejs_endpoint

    # ------------------------------------------------------------------
    # Storage document operations
    # ------------------------------------------------------------------

    async def delete_connector_storage(
        self, org_id: str, connector_id: str
    ) -> int:
        """Delete all blobs and MongoDB storage documents for a connector.

        Returns the number of storage documents deleted.
        """
        headers, nodejs_endpoint = await self._get_auth_headers_and_endpoint(
            org_id
        )
        delete_url = (
            f"{nodejs_endpoint}"
            f"{Routes.STORAGE_DELETE_CONNECTOR.value.format(connector_id=connector_id)}"
        )
        session = await self._get_session()
        async with session.delete(delete_url, headers=headers) as resp:
            if resp.status != HttpStatusCode.SUCCESS.value:
                error_text = await resp.text()
                raise Exception(
                    f"Connector storage delete failed: "
                    f"{resp.status} {error_text[:200]}"
                )
            body = await resp.json()
            deleted = body.get("deleted", 0)
        self.logger.info(
            "Deleted %d storage documents for connector %s",
            deleted,
            connector_id,
        )
        return deleted

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def move_record_tree(
        self,
        org_id: str,
        old_path: str,
        new_path: str,
    ) -> None:
        """Relocate a record's own content (if any) and every descendant
        currently stored under old_path, in one call to Node's move-tree
        endpoint. See docs/superpowers/specs/2026-07-07-blob-move-tree-design.md.

        Safe to call with old_path == new_path -- becomes a no-op with no
        network call, since there would be nothing to move.
        """
        if old_path == new_path:
            return

        headers, nodejs_endpoint = await self._get_auth_headers_and_endpoint(org_id)
        move_url = f"{nodejs_endpoint}{Routes.STORAGE_MOVE_TREE.value}"
        body: dict = {"oldPath": old_path, "newPath": new_path}

        session = await self._get_session()
        async with session.post(move_url, json=body, headers=headers) as resp:
            if resp.status != HttpStatusCode.SUCCESS.value:
                error_text = await resp.text()
                raise Exception(
                    f"move-tree failed: {resp.status} {error_text[:200]}"
                )
        self.logger.info("✅ Moved storage tree %s -> %s", old_path, new_path)
