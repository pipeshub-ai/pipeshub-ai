"""
Custom Connector

Generic connector for user-managed, locally-stored data. Instances are backed
by a recordGroup in the graph database and behave like the Knowledge Base
connector, but with all builder fields (name, sync/agent/hide flags, app type,
app group) chosen per instance instead of being hardcoded.

Knowledge Base is one preset of this connector: the org-creation hook
auto-provisions a CustomConnector instance with KB-preset overrides
(type=KNOWLEDGE_BASE, app_group=LOCAL_STORAGE, hide_connector=True, etc.).
Future user-created custom connectors use the same class with type=CUSTOM
and their own override values.
"""

from logging import Logger
from typing import Any, Optional

import aiohttp
from fastapi import HTTPException
from fastapi.responses import Response

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import AppGroups, Connectors, OriginTypes
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import DefaultEndpoints, config_node_constants
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.interfaces.connector.apps import App
from app.connectors.core.registry.connector_builder import (
    ConnectorBuilder,
    ConnectorScope,
    SyncStrategy,
)
from app.connectors.core.registry.filters import FilterOptionsResponse
from app.models.entities import Record
from app.utils.api_call import make_api_call
from app.utils.jwt import generate_jwt

CUSTOM_CONNECTOR_NAME = "Custom"


class CustomApp(App):
    """
    App wrapper for CustomConnector.

    Accepts app_type and app_group as parameters so a single class can back
    many preset shapes (KB, user-defined customs, etc.).
    """

    def __init__(
        self,
        connector_id: str,
        app_type: Connectors = Connectors.CUSTOM,
        app_group: AppGroups = AppGroups.LOCAL_STORAGE,
    ) -> None:
        super().__init__(app_type, app_group, connector_id)


@ConnectorBuilder(CUSTOM_CONNECTOR_NAME)\
    .in_group("Local Storage")\
    .with_supported_auth_types("NONE")\
    .with_description("Generic custom connector for user-managed data")\
    .with_categories(["Custom", "Storage"])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .configure(lambda builder: builder
        .with_sync_strategies([SyncStrategy.MANUAL])\
        .with_scheduled_config(False, 0)\
        .with_sync_support(True)
        .with_agent_support(True)
        .with_hide_connector(False)
    )\
    .build_decorator()


class CustomConnector(BaseConnector):
    """
    Generic custom connector.

    Local-storage semantics (no-op sync). Per-instance overrides for
    `app_type` and `app_group` let the same class back both the default
    KB instance (type=KNOWLEDGE_BASE, app_group=LOCAL_STORAGE) and
    user-created custom connector instances (type=CUSTOM, app_group of
    their choice).
    """

    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        scope: str,
        created_by: str,
        app_type: Connectors = Connectors.CUSTOM,
        app_group: AppGroups = AppGroups.LOCAL_STORAGE,
    ) -> None:
        super().__init__(
            CustomApp(connector_id, app_type=app_type, app_group=app_group),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
        self.connector_name = app_type

    async def init(self) -> bool:
        """Initialize the custom connector"""
        try:
            self.logger.info(
                f"✅ Custom connector initialized (connector_id={self.connector_id}, "
                f"type={self.connector_name}) (local storage)"
            )
            return True
        except Exception as e:
            self.logger.error(f"❌ Failed to initialize custom connector: {e}")
            return False

    async def test_connection_and_access(self) -> bool:
        """Test connection - always returns True for local storage"""
        return True

    async def get_signed_url(self, record: Record) -> Optional[str]:
        """
        Get signed URL for a record stored in the storage service.

        Custom/KB records with origin=UPLOAD are stored in the storage service.
        Calls the storage service to get a pre-signed URL for downloading.
        """
        try:
            if record.origin != OriginTypes.UPLOAD:
                self.logger.warning(
                    f"Record {record.id} is not an uploaded record (origin: {record.origin})"
                )
                return None

            if not record.external_record_id:
                self.logger.warning(f"Record {record.id} has no externalRecordId")
                return None

            endpoints = await self.config_service.get_config(
                config_node_constants.ENDPOINTS.value
            )
            storage_url = endpoints.get("storage", {}).get(
                "endpoint", DefaultEndpoints.STORAGE_ENDPOINT.value
            )

            jwt_payload = {
                "orgId": record.org_id,
                "scopes": ["storage:token"],
            }
            storage_token = await generate_jwt(self.config_service, jwt_payload)

            download_endpoint = f"{storage_url}/api/v1/document/internal/{record.external_record_id}/download"

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    download_endpoint,
                    headers={"Authorization": f"Bearer {storage_token}"}
                ) as response:
                    if response.status == HttpStatusCode.OK.value:
                        content_type = response.headers.get("Content-Type", "")

                        if "application/json" in content_type:
                            data = await response.json()
                            signed_url = data.get("signedUrl")
                            if signed_url:
                                return signed_url
                            else:
                                self.logger.error(
                                    f"No signedUrl in storage service response for record {record.id}"
                                )
                                return None
                        else:
                            self.logger.info(
                                f"Local storage detected for record {record.id}, will stream with auth"
                            )
                            return None
                    else:
                        error_text = await response.text()
                        self.logger.error(
                            f"Storage service returned {response.status} for record {record.id}: {error_text}"
                        )
                        return None
        except Exception as e:
            self.logger.error(
                f"Failed to get signed URL for record {record.id}: {e}", exc_info=True
            )
            return None

    async def stream_record(
        self, record: Record, user_id: Optional[str] = None, convertTo: Optional[str] = None
    ) -> Response:
        """
        Stream a record from the storage service.

        Fetches the buffer from the storage service and returns it as a FastAPI
        Response.
        """
        try:
            if record.origin != OriginTypes.UPLOAD:
                raise HTTPException(
                    status_code=HttpStatusCode.BAD_REQUEST.value,
                    detail=f"Cannot stream record with origin {record.origin}. Only uploaded records can be streamed."
                )

            if not record.external_record_id:
                raise HTTPException(
                    status_code=HttpStatusCode.NOT_FOUND.value,
                    detail="Record has no externalRecordId"
                )

            endpoints = await self.config_service.get_config(
                config_node_constants.ENDPOINTS.value
            )
            storage_url = endpoints.get("storage", {}).get(
                "endpoint", DefaultEndpoints.STORAGE_ENDPOINT.value
            )

            buffer_url = f"{storage_url}/api/v1/document/internal/{record.external_record_id}/buffer"

            jwt_payload = {
                "orgId": record.org_id,
                "scopes": ["storage:token"],
            }
            storage_token = await generate_jwt(self.config_service, jwt_payload)

            response = await make_api_call(route=buffer_url, token=storage_token)

            if isinstance(response["data"], dict):
                data = response['data'].get('data')
                buffer = bytes(data) if isinstance(data, list) else data
            else:
                buffer = response['data']

            return Response(
                content=buffer or b'',
                media_type=record.mime_type if record.mime_type else "application/octet-stream"
            )

        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to stream record {record.id}: {e}", exc_info=True)
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail=f"Failed to stream record: {str(e)}"
            ) from e

    async def run_sync(self) -> None:
        """No-op for custom connector - local storage"""
        self.logger.debug("Custom connector sync skipped (local storage)")

    async def run_incremental_sync(self) -> None:
        """No-op for custom connector - local storage"""
        self.logger.debug("Custom connector incremental sync skipped (local storage)")

    def handle_webhook_notification(self, notification: dict) -> None:
        """Custom connector doesn't support webhooks"""
        self.logger.debug("Custom connector webhook notification ignored (not supported)")

    async def cleanup(self) -> None:
        """Cleanup resources"""
        self.logger.info("✅ Custom connector cleanup completed")

    async def reindex_records(self, record_results: list[Record]) -> None:
        """Reindex custom connector records (placeholder)."""
        self.logger.info(f"Reindexing {len(record_results)} custom connector records")

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        scope: str,
        created_by: str,
        app_type: Connectors = Connectors.CUSTOM,
        app_group: AppGroups = AppGroups.LOCAL_STORAGE,
        **kwargs: Any,
    ) -> "CustomConnector":
        """
        Factory method.

        Accepts optional `app_type` / `app_group` so callers (notably the
        org-creation hook) can pick the preset the instance represents.
        Extra kwargs are accepted and ignored for forward-compat with the
        generic ConnectorFactory plumbing.
        """
        data_entities_processor = DataSourceEntitiesProcessor(
            logger, data_store_provider, config_service
        )
        await data_entities_processor.initialize()
        return CustomConnector(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
            app_type=app_type,
            app_group=app_group,
        )

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> FilterOptionsResponse:
        """Custom connector does not support dynamic filter options"""
        from app.connectors.core.registry.filters import (
            FilterOptionsResponse,
        )
        return FilterOptionsResponse(
            success=True,
            options=[],
            page=page,
            limit=limit,
            has_more=False
        )
