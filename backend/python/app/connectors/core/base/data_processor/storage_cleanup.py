"""Storage cleanup helper for blob lifecycle management.

Invoked from DataSourceEntitiesProcessor when records are deleted or moved to
ensure the corresponding blobs are purged / relocated in the storage backend.
The helper calls the Node.js storage service using the same scoped-JWT auth
pattern as BlobStorage.
"""

from typing import Any

import jwt
import aiohttp

from app.config.constants.arangodb import CollectionNames
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import (
    DefaultEndpoints,
    Routes,
    TokenScopes,
    config_node_constants,
)
from app.utils.request_context import inject_request_headers
from app.utils.storage_path import (
    build_hierarchical_storage_path,
    build_record_group_path as _build_record_group_path,
)


class StorageCleanupHelper:
    """Handles blob storage cleanup when records are deleted or moved."""

    def __init__(self, logger, graph_provider, config_service) -> None:
        self.logger = logger
        self.graph_provider = graph_provider
        self.config_service = config_service

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

        jwt_token = jwt.encode(payload, scoped_jwt_secret, algorithm="HS256")
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
        async with aiohttp.ClientSession() as session:
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

        async with aiohttp.ClientSession() as session:
            async with session.post(move_url, json=body, headers=headers) as resp:
                if resp.status != HttpStatusCode.SUCCESS.value:
                    error_text = await resp.text()
                    raise Exception(
                        f"move-tree failed: {resp.status} {error_text[:200]}"
                    )
        self.logger.info("✅ Moved storage tree %s -> %s", old_path, new_path)
