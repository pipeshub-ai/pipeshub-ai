"""
Knowledge Base Connector

Connector for managing local knowledge bases. This connector treats KB as a unified
connector type that manages all KBs collectively, with no-op sync methods since KBs
are local storage.
"""

from logging import Logger
from typing import Dict, List, Optional

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
)
from app.connectors.core.registry.filters import FilterOptionsResponse
from app.models.entities import Record
from app.utils.api_call import make_api_call
from app.utils.jwt import generate_jwt

KB_CONNECTOR_NAME = "Collections"

class KBApp(App):
    """App class for Knowledge Base connector"""

    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.KNOWLEDGE_BASE, AppGroups.LOCAL_STORAGE, connector_id)


@ConnectorBuilder(KB_CONNECTOR_NAME)\
    .in_group("Local Storage")\
    .with_supported_auth_types("NONE")\
    .with_description("Local knowledge base for organizing and managing documents")\
    .with_categories(["Knowledge Management", "Storage"])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .configure(lambda builder: builder
        .with_sync_strategies(["MANUAL"])\
        .with_scheduled_config(False, 0)\
        .with_sync_support(True)
        .with_agent_support(True)
        .with_hide_connector(True)
    )\
    .build_decorator()


class KnowledgeBaseConnector(BaseConnector):
    """
    Knowledge Base connector for managing local knowledge bases.

    This connector manages all KBs collectively (not per-KB instances).
    Since KBs are local storage, sync methods are no-ops.
    """

    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str
    ) -> None:
        super().__init__(
            KBApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id
        )
        self.connector_name = Connectors.KNOWLEDGE_BASE

        # KB connector doesn't need to maintain KB service instance
        # The existing KB router and service continue to work independently
        # This connector is mainly for registration in the connector factory

    async def init(self) -> bool:
        """Initialize the KB connector"""
        try:
            # KB connector doesn't need external configuration
            # It's always available for local storage
            self.logger.info("✅ Knowledge Base connector initialized (local storage)")
            return True
        except Exception as e:
            self.logger.error(f"❌ Failed to initialize KB connector: {e}")
            return False

    async def test_connection_and_access(self) -> bool:
        """Test connection - always returns True for local KB"""
        # KB is local storage, so connection is always available
        return True

    async def get_signed_url(self, record: Record) -> Optional[str]:
        """
        Get signed URL for a KB record.

        KB records are stored in the storage service. This method calls the storage
        service to get a pre-signed URL for downloading the file.
        """
        try:
            # Only KB records with UPLOAD origin are stored in storage service
            if record.origin != OriginTypes.UPLOAD:
                self.logger.warning(
                    f"Record {record.id} is not an uploaded record (origin: {record.origin})"
                )
                return None

            if not record.external_record_id:
                self.logger.warning(f"Record {record.id} has no externalRecordId")
                return None

            # Get storage endpoint and JWT secret from config
            endpoints = await self.config_service.get_config(
                config_node_constants.ENDPOINTS.value
            )
            storage_url = endpoints.get("storage", {}).get(
                "endpoint", DefaultEndpoints.STORAGE_ENDPOINT.value
            )

            # Create a scoped JWT token for storage service authentication
            jwt_payload = {
                "orgId": record.org_id,
                "scopes": ["storage:token"],
            }
            storage_token = await generate_jwt(self.config_service, jwt_payload)

            # Call storage service to get the signed URL
            download_endpoint = f"{storage_url}/api/v1/document/internal/{record.external_record_id}/download"

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    download_endpoint,
                    headers={"Authorization": f"Bearer {storage_token}"}
                ) as response:
                    if response.status == HttpStatusCode.OK.value:
                        content_type = response.headers.get("Content-Type", "")

                        # Check if response is JSON (cloud storage) or file (local storage)
                        if "application/json" in content_type:
                            # Cloud storage: response contains signed URL
                            data = await response.json()
                            signed_url = data.get("signedUrl")
                            if signed_url:
                                return signed_url
                            else:
                                self.logger.error(f"No signedUrl in storage service response for record {record.id}")
                                return None
                        else:
                            # Local storage: the endpoint serves the file directly
                            # Return None to indicate we need to handle this differently in stream_record
                            self.logger.info(f"Local storage detected for record {record.id}, will stream with auth")
                            return None
                    else:
                        error_text = await response.text()
                        self.logger.error(
                            f"Storage service returned {response.status} for record {record.id}: {error_text}"
                        )
                        return None
        except Exception as e:
            self.logger.error(f"Failed to get signed URL for record {record.id}: {e}", exc_info=True)
            return None

    async def stream_record(
        self, record: Record, user_id: Optional[str] = None, convertTo: Optional[str] = None
    ) -> Response:
        """
        Stream a record from KB.

        KB records are stored in the storage service. This method fetches the buffer
        from the storage service and returns it as a response.
        """
        try:
            # Only KB records with UPLOAD origin are stored in storage service
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

            # Get storage endpoint from config
            endpoints = await self.config_service.get_config(
                config_node_constants.ENDPOINTS.value
            )
            storage_url = endpoints.get("storage", {}).get(
                "endpoint", DefaultEndpoints.STORAGE_ENDPOINT.value
            )

            # Build the buffer endpoint
            buffer_url = f"{storage_url}/api/v1/document/internal/{record.external_record_id}/buffer"

            # Create a scoped JWT token for storage service authentication
            jwt_payload = {
                "orgId": record.org_id,
                "scopes": ["storage:token"],
            }
            storage_token = await generate_jwt(self.config_service, jwt_payload)

            # Fetch the buffer from storage service
            response = await make_api_call(route=buffer_url, token=storage_token)

            # Extract buffer content
            if isinstance(response["data"], dict):
                data = response['data'].get('data')
                buffer = bytes(data) if isinstance(data, list) else data
            else:
                buffer = response['data']

            return Response(content=buffer or b'', media_type=record.mime_type if record.mime_type else "application/octet-stream")

        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to stream record {record.id}: {e}", exc_info=True)
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail=f"Failed to stream record: {str(e)}"
            )

    async def run_sync(self) -> None:
        """No-op for KB connector - KBs are local storage"""
        self.logger.debug("KB connector sync skipped (local storage)")
        return

    async def run_incremental_sync(self) -> None:
        """No-op for KB connector - KBs are local storage"""
        self.logger.debug("KB connector incremental sync skipped (local storage)")
        return

    def handle_webhook_notification(self, notification: Dict) -> None:
        """KB connector doesn't support webhooks"""
        self.logger.debug("KB connector webhook notification ignored (not supported)")
        return

    async def cleanup(self) -> None:
        """Cleanup resources"""
        # KB connector doesn't maintain state, so cleanup is a no-op
        self.logger.info("✅ Knowledge Base connector cleanup completed")

    async def reindex_records(self, record_results: List[Record]) -> None:
        """
        Reindex KB records.

        This delegates to existing KB reindexing logic if available.
        For now, it's a placeholder that can be extended.
        """
        self.logger.info(f"Reindexing {len(record_results)} KB records")
        # TODO: Implement KB-specific reindexing logic if needed
        # This could delegate to KB service or trigger indexing events
        pass

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str
    ) -> "KnowledgeBaseConnector":
        """Factory method to create a KnowledgeBaseConnector instance"""
        data_entities_processor = DataSourceEntitiesProcessor(
            logger, data_store_provider, config_service
        )
        await data_entities_processor.initialize()
        return KnowledgeBaseConnector(
            logger, data_entities_processor, data_store_provider, config_service, connector_id
        )

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> FilterOptionsResponse:
        """KB connector does not support dynamic filter options"""
        # KBs don't have external sources to filter, so return empty
        from app.connectors.core.registry.filters import (
            FilterOptionsResponse,
        )
        return FilterOptionsResponse(
            options=[],
            total=0,
            page=page,
            limit=limit,
            has_more=False
        )
