"""
Notion Connector

Authentication: OAuth 2.0
"""

import asyncio
import base64
import json
import mimetypes
from datetime import datetime, timezone
from hashlib import md5
from logging import Logger
from typing import Any, AsyncGenerator, Dict, List, NoReturn, Optional, Tuple
from urllib.parse import unquote, urlparse
from uuid import uuid4

import aiohttp
import httpx
from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    SyncPoint,
    generate_record_sync_point_key,
)
from app.connectors.core.registry.connector_builder import (
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
    DocumentationLink,
)
from app.connectors.core.registry.filters import (
    FilterCollection,
    load_connector_filters,
)
from app.connectors.sources.notion.block_parser import NotionBlockParser
from app.connectors.sources.notion.common.apps import NotionApp
from app.models.blocks import (
    Block,
    BlockContainerIndex,
    BlockGroup,
    BlocksContainer,
    BlockType,
    ChildRecord,
    DataFormat,
    GroupType,
    TableMetadata,
    TableRowMetadata,
)
from app.models.entities import (
    AppUser,
    CommentRecord,
    FileRecord,
    IndexingStatus,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    WebpageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.modules.parsers.image_parser.image_parser import ImageParser
from app.sources.client.notion.notion import NotionClient
from app.sources.external.notion.notion import NotionDataSource
from app.utils.mimetype_to_extension import get_extension_from_mimetype
from app.utils.time_conversion import get_epoch_timestamp_in_ms, parse_timestamp
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

# Notion OAuth URLs
# Note: Notion OAuth doesn't use traditional scopes. Permissions are configured
# when creating the integration in Notion's developer portal. The scope parameter
# below is a placeholder to satisfy the OAuth validator.
AUTHORIZE_URL = "https://api.notion.com/v1/oauth/authorize"
TOKEN_URL = "https://api.notion.com/v1/oauth/token"

@ConnectorBuilder("Notion")\
    .in_group("Notion")\
    .with_auth_type("OAUTH")\
    .with_description("Sync pages, databases, and users from Notion")\
    .with_categories(["Knowledge Management", "Collaboration"])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/notion.svg")
        .with_realtime_support(False)
        .add_documentation_link(DocumentationLink(
            "Notion OAuth Setup",
            "https://developers.notion.com/docs/authorization",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/notion/notion',
            'pipeshub'
        ))
        .with_redirect_uri("connectors/oauth/callback/Notion", True)
        .with_oauth_urls(AUTHORIZE_URL, TOKEN_URL, ["read"])
        .add_auth_field(CommonFields.client_id("Notion OAuth App"))
        .add_auth_field(CommonFields.client_secret("Notion OAuth App"))
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
    )\
    .build_decorator()
class NotionConnector(BaseConnector):
    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str
    ) -> None:
        """Initialize the Notion connector."""
        super().__init__(
            NotionApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id
        )

        # Client instances
        self.notion_client: Optional[NotionClient] = None
        self.data_source: Optional[NotionDataSource] = None
        self.connector_id: str = connector_id

        # Initialize sync points for incremental sync
        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_id=self.connector_id,
                org_id=self.data_entities_processor.org_id,
                sync_data_point_type=sync_data_point_type,
                data_store_provider=self.data_store_provider,
            )

        self.pages_sync_point = _create_sync_point(SyncDataPointType.RECORDS)

        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()
        
        # Tracking state for deduplication
        # Workspace information from bot owner
        self.workspace_id: Optional[str] = None
        self.workspace_name: Optional[str] = None

    async def init(self) -> bool:
        """Initialize the Notion connector with credentials and client."""
        try:
            self.logger.info("🔧 Initializing Notion Connector...")

            # Build client from services
            self.notion_client = await NotionClient.build_from_services(
                logger=self.logger,
                config_service=self.config_service,
                connector_instance_id=self.connector_id
            )

            # Initialize data source
            self.data_source = NotionDataSource(self.notion_client)

            self.logger.info("✅ Notion connector initialized successfully")
            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to initialize Notion connector: {e}", exc_info=True)
            return False

    async def test_connection_and_access(self) -> bool:
        """Test connection and access to Notion API."""
        try:
            if not self.notion_client:
                self.logger.error("Notion client not initialized")
                return False

            datasource = await self._get_fresh_datasource()
            response = await datasource.retrieve_bot_user()

            if not response or not response.success:
                self.logger.error(f"Connection test failed: {response.error if response else 'No response'}")
                return False

            self.logger.info("✅ Notion connector connection test passed")
            return True

        except Exception as e:
            self.logger.error(f"Connection test failed: {e}", exc_info=True)
            return False

    async def run_sync(self) -> None:
        """
        Run full synchronization of Notion data.

        Sync order:
        1. Users
        2. All Data Sources (via Search API)
        3. All Pages (via Search API)
        - a. All Page Attachments
        - b. All Page Comments (with their attachments)
        
        Note: Search API returns ALL pages/data_sources regardless of hierarchy.
        Parent relationships are preserved in the response data.
        """
        try:
            org_id = self.data_entities_processor.org_id
            self.logger.info(f"🚀 Starting Notion sync for org: {org_id}")
            
            # Load filters
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "notion", self.connector_id, self.logger
            )
            
            # Step 1: Sync users
            await self._sync_users()    
            
            # Step 2: Sync all data sources (Search API returns all, regardless of hierarchy)
            await self._sync_objects_by_type("data_source")
            
            # Step 3: Sync all pages (Search API returns all, regardless of hierarchy)
            # along with all page attachments and comments
            await self._sync_objects_by_type("page")

            self.logger.info("✅ Notion sync completed successfully")

        except Exception as e:
            self.logger.error(f"❌ Error during Notion sync: {e}", exc_info=True)
            raise

    async def run_incremental_sync(self) -> None:
        """Run incremental sync (delegates to full sync)."""
        await self.run_sync()

    async def get_signed_url(self, record: Record) -> Optional[str]:
        """
        Get a signed URL for a file record by fetching the latest URL from Notion API.
        
        For file records, retrieves the block and extracts the file URL.
        For comment attachment files, the URL is already stored in signed_url field.
        
        Args:
            record: The file record to get signed URL for
            
        Returns:
            Signed URL string or None if not available
        """
        try:
            if record.record_type != RecordType.FILE:
                return None
            
            if not self.data_source:
                return None
            
            # For comment attachments, fetch comment from API and extract attachment URL
            if record.parent_record_type == RecordType.COMMENT:
                # Extract comment ID from parent_external_record_id (format: comment_{comment_id})
                parent_id = record.parent_external_record_id
                if not parent_id or not parent_id.startswith("comment_"):
                    return None
                
                comment_id = parent_id[len("comment_"):]
                
                # Fetch comment from Notion API
                response = await self.data_source.retrieve_comment(comment_id)
                
                if not response.success or not response.data:
                    return None
                
                comment_data = response.data.json() if hasattr(response.data, 'json') else {}
                attachments = comment_data.get("attachments", [])
                
                # Extract attachment index from external_record_id (format: comment_attachment_{comment_id}_{index})
                external_id = record.external_record_id
                attachment_index = None
                if external_id.startswith("comment_attachment_"):
                    parts = external_id.split("_")
                    if len(parts) >= 4:
                        try:
                            attachment_index = int(parts[3])
                        except (ValueError, IndexError):
                            pass
                
                # Try to find attachment by index first, then by filename
                if attachment_index is not None and attachment_index < len(attachments):
                    attachment = attachments[attachment_index]
                else:
                    # Fallback: try to match by filename
                    record_name = record.record_name
                    for attachment in attachments:
                        if "file" in attachment:
                            file_obj = attachment["file"]
                            if isinstance(file_obj, dict):
                                file_url = file_obj.get("url", "")
                                if file_url:
                                    parsed_url = urlparse(file_url)
                                    path = unquote(parsed_url.path)
                                    url_filename = path.split("/")[-1] if "/" in path else ""
                                    if url_filename == record_name:
                                        break
                    else:
                        # No match found, use first attachment as fallback
                        attachment = attachments[0] if attachments else None
                
                if not attachment:
                    return None
                
                # Extract file URL from attachment
                if "file" in attachment:
                    file_obj = attachment["file"]
                    if isinstance(file_obj, dict):
                        return file_obj.get("url", "")
                
                return None
            
            # For block attachments, extract block_id from external_record_id
            # Format: file_{page_id}_{block_id}
            external_id = record.external_record_id
            self.logger.info(f"External ID: {external_id}")
            if not external_id.startswith("file_"):
                return record.signed_url
            self.logger.info(f"External ID: {external_id}")
            # Extract block_id from external_record_id
            parts = external_id.split("_", 2)
            self.logger.info(f"Parts: {parts}")
            if len(parts) < 3:
                return record.signed_url
            
            block_id = parts[2]
            
            # Fetch block from Notion API
            response = await self.data_source.retrieve_block(block_id)

            self.logger.info(f"Block response: {response}")
            
            if not response.success or not response.data:
                return record.signed_url
            
            block_data = response.data.json() if hasattr(response.data, 'json') else {}
            self.logger.info(f"Block data: {block_data}")
            block_type = block_data.get("type", "")
            self.logger.info(f"Block type: {block_type}")
            type_data = block_data.get(block_type, {})
            self.logger.info(f"Type data: {type_data}")
            # Extract file URL based on block type
            file_url = None
            if "file" in type_data:
                file_obj = type_data["file"]
                if isinstance(file_obj, dict):
                    file_url = file_obj.get("url", "")
            elif "external" in type_data:
                external_obj = type_data["external"]
                if isinstance(external_obj, dict):
                    file_url = external_obj.get("url", "")
            
            return file_url
            
        except Exception as e:
            self.logger.error(f"Failed to get signed URL for record {record.external_record_id}: {e}", exc_info=True)
            raise e

    async def stream_record(self, record: Record) -> StreamingResponse:
        """
        Stream record content from Notion.

        For pages: Fetches all block children recursively and converts to BlocksContainer
        For data_sources: Fetches properties and rows, converts to TABLE block structure
        For comments: Fetches comment details using retrieve_comment API
        For files: Not applicable (files are already stored as FileRecord)

        Args:
            record: The record to stream

        Returns:
            StreamingResponse: JSON streaming response with BlocksContainer or comment data
        """
        try:
            self.logger.info(f"📥 Streaming record: {record.record_name} ({record.external_record_id})")

            if not self.data_source:
                raise HTTPException(
                    status_code=500,
                    detail="Notion data source not initialized"
                )

            # Handle file records
            if record.record_type == RecordType.FILE:
                signed_url = await self.get_signed_url(record)
                
                if not signed_url:
                    raise HTTPException(
                        status_code=404,
                        detail="File URL not available"
                    )
                
                # Stream file from signed URL
                async def generate_file_stream() -> AsyncGenerator[bytes, None]:
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        async with client.stream("GET", signed_url) as response:
                            response.raise_for_status()
                            async for chunk in response.aiter_bytes():
                                yield chunk
                
                # Determine content type from record
                media_type = record.mime_type if record.mime_type else "application/octet-stream"
                
                return StreamingResponse(
                    generate_file_stream(),
                    media_type=media_type,
                    headers={
                        "Content-Disposition": f'attachment; filename="{record.record_name}"'
                    }
                )
            elif record.record_type == RecordType.COMMENT:
                # Extract actual Notion comment ID from external_record_id
                # external_record_id is stored as "comment_{comment_id}"
                comment_id = record.external_record_id
                if comment_id.startswith("comment_"):
                    comment_id = comment_id[len("comment_"):]
                
                self.logger.info(f"📥 Fetching comment: {comment_id}")
                
                # Fetch comment from Notion API
                response = await self.data_source.retrieve_comment(comment_id)
                
                if not response.success:
                    error_msg = response.error if response.error else "Unknown error"
                    self.logger.error(f"❌ Failed to retrieve comment {comment_id}: {error_msg}")
                    raise HTTPException(
                        status_code=500,
                        detail=f"Failed to retrieve comment: {error_msg}"
                    )
                
                # Parse comment data
                comment_data = {}
                if response.data:
                    try:
                        comment_data = response.data.json() if hasattr(response.data, 'json') else {}
                    except Exception as parse_error:
                        self.logger.error(f"❌ Failed to parse comment response: {parse_error}", exc_info=True)
                        raise HTTPException(
                            status_code=500,
                            detail=f"Failed to parse comment response: {str(parse_error)}"
                        )
                
                # Extract and format comment text as markdown
                rich_text_array = comment_data.get("rich_text", [])
                
                # Use block parser to convert rich text to markdown
                parser = NotionBlockParser(self.logger, self.data_entities_processor)
                markdown_text = parser.extract_rich_text(rich_text_array)
                
                # Stream comment as markdown in chunks
                async def generate_comment_markdown() -> AsyncGenerator[bytes, None]:
                    # Yield in chunks of 8KB for efficient streaming
                    chunk_size = 8192
                    encoded = markdown_text.encode('utf-8')
                    for i in range(0, len(encoded), chunk_size):
                        yield encoded[i:i + chunk_size]
                
                return StreamingResponse(
                    generate_comment_markdown(),
                    media_type='text/markdown',
                    headers={
                        "Content-Disposition": f'inline; filename="{record.external_record_id}_comment.md"'
                    }
                )
            
            elif record.record_type == RecordType.NOTION_DATA_SOURCE:
                # Fetch data source as table blocks
                parser = NotionBlockParser(self.logger, self.config_service)
                blocks_container = await self._fetch_data_source_as_blocks(
                    record.external_record_id,
                    parser
                )
                
                # Resolve child records for table rows (database row pages with children)
                await self._resolve_table_row_children(blocks_container.blocks, parent_data_source_record=record)
                
                # Stream blocks container as JSON in chunks
                async def generate_blocks_json() -> AsyncGenerator[bytes, None]:
                    json_str = blocks_container.model_dump_json(indent=2)
                    # Yield in chunks of 8KB for efficient streaming
                    chunk_size = 8192
                    encoded = json_str.encode('utf-8')
                    for i in range(0, len(encoded), chunk_size):
                        yield encoded[i:i + chunk_size]
                
                return StreamingResponse(
                    generate_blocks_json(),
                    media_type='application/json',
                    headers={
                        "Content-Disposition": f'inline; filename="{record.external_record_id}_data_source.json"'
                    }
                )
            elif record.record_type == RecordType.NOTION_PAGE:
                parser = NotionBlockParser(self.logger, self.config_service)
                # Extract page URL from record.weburl (if available)
                parent_page_url = record.weburl if hasattr(record, 'weburl') and record.weburl else None
                # Fetch page blocks recursively
                blocks_container = await self._fetch_page_as_blocks(
                    record.external_record_id,
                    parser,
                    parent_page_url=parent_page_url
                )
                
                # Resolve child reference block IDs (creates minimal records for unsynced children)
                await self._resolve_child_reference_blocks(blocks_container.blocks, parent_record=record)
                
                # Stream blocks container as JSON in chunks
                async def generate_blocks_json() -> AsyncGenerator[bytes, None]:
                    json_str = blocks_container.model_dump_json(indent=2)
                    # Yield in chunks of 8KB for efficient streaming
                    chunk_size = 8192
                    encoded = json_str.encode('utf-8')
                    for i in range(0, len(encoded), chunk_size):
                        yield encoded[i:i + chunk_size]
                
                return StreamingResponse(
                    generate_blocks_json(),
                    media_type='application/json',
                    headers={
                        "Content-Disposition": f'inline; filename="{record.external_record_id}_page.json"'
                    }
                )
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Streaming not supported for record type: {record.record_type}"
                )

        

        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"❌ Failed to stream record: {e}", exc_info=True)
            raise HTTPException(
                status_code=500, detail=f"Failed to stream record: {str(e)}"
            )

    async def reindex_records(self, records: List[Record]) -> None:
        """
        Reindex a list of Notion records.

        This method:
        1. For each record, checks if it has been updated at the source
        2. If updated, upserts the record in DB
        3. Publishes reindex events for all records via data_entities_processor

        Args:
            records: List of properly typed Record instances
        """
        try:
            if not records:
                self.logger.info("No records to reindex")
                return

            self.logger.info(f"Starting reindex for {len(records)} Notion records")

            # TODO: Implement reindex logic
            # 1. Check each record at source for updates
            # 2. Update DB only for records that changed at source
            # 3. Publish reindex events for all records

        except Exception as e:
            self.logger.error(f"Error during Notion reindex: {e}", exc_info=True)
            raise

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> NoReturn:
        """Outlook connector does not support dynamic filter options."""
        raise NotImplementedError("Outlook connector does not support dynamic filter options")

    async def cleanup(self) -> None:
        """
        Cleanup resources when shutting down the connector.

        Notion connector cleanup includes:
        - Clearing client references
        - Clearing datasource reference
        - Logging completion

        Note: Notion uses stateless HTTP requests, so no persistent connections
        or subscriptions to clean up.
        """
        try:
            self.logger.info("🧹 Starting Notion connector cleanup")

            # Clear client references
            if hasattr(self, 'notion_client'):
                self.notion_client = None

            if hasattr(self, 'data_source'):
                self.data_source = None

            self.logger.info("✅ Notion connector cleanup completed")

        except Exception as e:
            self.logger.error(f"❌ Error during Notion connector cleanup: {e}", exc_info=True)

    async def handle_webhook_notification(self, notification: Dict) -> None:
        """Handle webhook notifications (not implemented)."""
        self.logger.warning("Webhook notifications not yet supported for Notion")
        pass

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str
    ) -> "NotionConnector":
        """Factory method to create a Notion connector instance."""
        data_entities_processor = DataSourceEntitiesProcessor(
            logger,
            data_store_provider,
            config_service
        )

        await data_entities_processor.initialize()

        return cls(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id
        )

    # ==================== Main Sync Methods ====================

    async def _sync_users(self) -> None:
        """
        Sync users from Notion using cursor-based pagination.

        Process:
        1. Call list_users to get all users (paginated)
        2. Filter for "person" type users only (skip "bot" type)
        3. For each person user, call retrieve_user to get their email
        4. Transform and save to database

        Note: list_users doesn't return email, need to fetch each user individually
        """
        try:
            self.logger.info("🔄 Starting user synchronization...")

            # Pagination variables
            page_size = 20  # Max allowed by Notion API : 100
            cursor = None
            total_synced = 0
            total_skipped = 0

            # Paginate through all users
            while True:
                datasource = await self._get_fresh_datasource()
                response = await datasource.list_users(
                    start_cursor=cursor,
                    page_size=page_size
                )

                if not response or not response.success:
                    error_msg = response.error if response else "No response"
                    self.logger.error(f"❌ Failed to fetch users: {error_msg}")
                    raise Exception(f"Notion API error while fetching users: {error_msg}")

                response_data = response.data.json() if response.data else {}
                users_data = response_data.get("results", [])

                if not users_data:
                    self.logger.info("No more users to process")
                    break

                # First pass: Identify bot user and extract workspace info
                # Also collect person user IDs
                person_user_ids = []
                bot_user = None
                
                for user in users_data:
                    user_type = user.get("type")
                    
                    if user_type == "person" and user.get("id"):
                        person_user_ids.append(user.get("id"))
                    elif user_type == "bot":
                        # Bot users contain workspace information
                        bot_data = user.get("bot", {})
                        workspace_id = bot_data.get("workspace_id")
                        
                        # If bot has workspace info, use it
                        if workspace_id:
                            bot_user = user
                            self.logger.info(f"Found bot user with workspace: {user.get('id')}")
                    else:
                        self.logger.debug(
                            f"Skipping user: {user.get('name', 'Unknown')} "
                            f"(type: {user_type}, id: {user.get('id', 'N/A')})"
                        )
                        total_skipped += 1
                
                # Extract workspace info from bot user (only on first page if not already set)
                if bot_user and not self.workspace_id:
                    bot_data = bot_user.get("bot", {})
                    workspace_id = bot_data.get("workspace_id")
                    workspace_name = bot_data.get("workspace_name")
                    
                    if workspace_id:
                        self.workspace_id = workspace_id
                        self.workspace_name = workspace_name
                        
                        self.logger.info(f"Extracted workspace info - ID: {self.workspace_id}, Name: {self.workspace_name}")
                        
                        # Create RecordGroup for workspace
                        await self._create_workspace_record_group()
                    else:
                        self.logger.warning("Bot user found but missing workspace_id")

                if not person_user_ids:
                    continue

                # Fetch full user details in parallel to get emails
                user_detail_tasks = [datasource.retrieve_user(user_id) for user_id in person_user_ids]
                user_detail_responses = await asyncio.gather(*user_detail_tasks, return_exceptions=True)

                # Process fetched user details
                app_users = []
                for i, result in enumerate(user_detail_responses):
                    user_id = person_user_ids[i]

                    if isinstance(result, Exception):
                        self.logger.error(f"❌ Failed to process user {user_id}: {result}", exc_info=False)
                        total_skipped += 1
                        continue

                    if not result or not result.success:
                        self.logger.warning(
                            f"Failed to retrieve user details for {user_id}: "
                            f"{result.error if result else 'No response'}"
                        )
                        total_skipped += 1
                        continue

                    user_detail = result.data.json() if result.data else {}
                    app_user = self._transform_to_app_user(user_detail)
                    if app_user:
                        app_users.append(app_user)
                    else:
                        # _transform_to_app_user logs warnings for invalid data
                        total_skipped += 1

                # Save batch to database
                if app_users:
                    await self.data_entities_processor.on_new_app_users(app_users)
                    total_synced += len(app_users)
                    self.logger.info(f"✅ Synced {len(app_users)} users in this batch")
                    
                    # Add permissions for these users to workspace record group (if workspace exists)
                    if self.workspace_id:
                        await self._add_users_to_workspace_permissions([app_user.email for app_user in app_users])

                has_more = response_data.get("has_more", False)
                cursor = response_data.get("next_cursor")

                if not has_more or not cursor:
                    break

            self.logger.info(f"✅ User sync complete. Synced: {total_synced}, Skipped: {total_skipped}")

        except Exception as e:
            self.logger.error(f"❌ User sync failed: {e}", exc_info=True)
            raise

    async def _add_users_to_workspace_permissions(self, user_emails: List[str]) -> None:
        """
        Add READ permissions for users to the workspace record group.
        
        Uses on_new_record_groups to create/update the record group along with permission edges,
        following the same pattern as other connectors (e.g., Confluence).
        
        Args:
            user_emails: List of user email addresses to grant permissions
        """
        try:
            if not self.workspace_id or not user_emails:
                return

            # Get the existing record group by external_id (if it exists)
            async with self.data_store_provider.transaction() as tx_store:
                record_group = await tx_store.get_record_group_by_external_id(
                    connector_id=self.connector_id,
                    external_id=self.workspace_id
                )
            
            # Create record group if it doesn't exist
            if not record_group:
                record_group = RecordGroup(
                    org_id=self.data_entities_processor.org_id,
                    name=self.workspace_name,
                    external_group_id=self.workspace_id,
                    connector_name=Connectors.NOTION,
                    connector_id=self.connector_id,
                    group_type=RecordGroupType.NOTION_WORKSPACE,
                    created_at=get_epoch_timestamp_in_ms(),
                    updated_at=get_epoch_timestamp_in_ms(),
                )
            
            # Create READ permissions for all users
            permissions = [
                Permission(
                    email=email,
                    type=PermissionType.READ,
                    entity_type=EntityType.USER,
                )
                for email in user_emails
            ]
            
            # Use on_new_record_groups to handle record group upsert and permission edges
            await self.data_entities_processor.on_new_record_groups([(record_group, permissions)])
            
            self.logger.info(f"✅ Added permissions for {len(user_emails)} users to workspace record group")
                
        except Exception as e:
            self.logger.error(f"❌ Failed to add workspace permissions: {e}", exc_info=True)
  
    async def _sync_objects_by_type(self, object_type: str) -> None:
        """
        Generic method to sync objects (pages or data_sources) using Search API with delta sync.
        
        Implements delta sync by:
        1. Sorting by last_edited_time descending (newest first)
        2. Reading sync point to get last sync time
        3. Syncing records until we encounter records with last_edited_time <= sync point time
        4. Updating sync point with latest last_edited_time after sync
        
        Args:
            object_type: "page" or "data_source"
        """
        try:
            type_display = object_type.capitalize()
            self.logger.info(f"🔄 Starting {type_display} synchronization...")
            
            # Get sync point key for this object type
            sync_point_key = generate_record_sync_point_key(
                RecordType.WEBPAGE.value, f"notion_{object_type}s", "global"
            )
            last_sync_data = await self.pages_sync_point.read_sync_point(sync_point_key)
            last_sync_time = last_sync_data.get("last_sync_time") if last_sync_data else None

            if last_sync_time:
                self.logger.info(f"🔄 Incremental sync: Fetching {object_type}s edited after {last_sync_time}")
            else:
                self.logger.info(f"🆕 Full sync: Fetching all {object_type}s (first time)")

            cursor = None
            page_size = 20  # Max allowed by Notion API : 100
            total_synced = 0
            total_files = 0
            latest_edit_time = None
            should_stop = False
            
            while True:
                if should_stop:
                    break
                    
                datasource = await self._get_fresh_datasource()
                
                # Build search request body with sorting by last_edited_time in descending order
                request_body = {
                    "filter": {"property": "object", "value": object_type},
                    "sort": {"timestamp": "last_edited_time", "direction": "descending"},
                    "page_size": page_size,
                }
                
                if cursor:
                    request_body["start_cursor"] = cursor
                
                # Search for objects by type with sorting
                response = await datasource.search(request_body=request_body)
                
                if not response or not response.success:
                    error_msg = response.error if response else 'No response'
                    self.logger.error(f"Failed to search {object_type}s: {error_msg}")
                    raise Exception(f"Notion API error while searching {object_type}s: {error_msg}")
                
                data = response.data.json() if response.data else {}
                objects = data.get("results", [])
                
                if not objects:
                    self.logger.info(f"No {object_type}s found after time {last_sync_time}")
                    break
                
                records_with_permissions: List[Tuple[Record, List[Permission]]] = []
                
                for obj_data in objects:
                    obj_id = obj_data.get("id")
                    last_edited_time = obj_data.get("last_edited_time")
                    
                    # Skip archived/trashed
                    if obj_data.get("archived") or obj_data.get("in_trash"):
                        self.logger.info(f"Skipping archived {object_type}: {obj_id}")
                        continue

                    # Delta sync check: if have a sync point, stop when records older than it is found
                    # Since records are sorted in descending order, records are newest first
                    if last_sync_time and last_edited_time:
                        # Compare timestamps (ISO format strings)
                        if last_edited_time <= last_sync_time:
                            self.logger.info(
                                f"Reached sync point threshold for {object_type}s. "
                                f"Record {obj_id} has last_edited_time {last_edited_time} <= sync point {last_sync_time}. "
                            )
                            should_stop = True
                            break

                    # Track latest edit time for sync point update
                    if last_edited_time and (not latest_edit_time or last_edited_time > latest_edit_time):
                        latest_edit_time = last_edited_time

                    # Transform (returns tuple for both types)
                    record = self._transform_to_webpage_record(obj_data, object_type)

                    if record:
                        records_with_permissions.append((record, []))
                        total_synced += 1
                        self.logger.debug(f"Synced {object_type}: {record.record_name} (last_edited: {last_edited_time})")

                    # Fetch attachments and comments from blocks (for pages only)
                    if object_type == "page" and obj_id:
                        try:
                            page_url = obj_data.get("url", "")
                            attachment_records, comment_records = await self._fetch_page_attachments_and_comments(obj_id, page_url)
                            for file_record in attachment_records:
                                records_with_permissions.append((file_record, []))
                                total_files += 1
                            for comment_record in comment_records:
                                records_with_permissions.append((comment_record, []))
                        except Exception as error:
                            self.logger.warning(
                                f"Failed to fetch attachments and comments for page {obj_id}: {error}. "
                                f"Continuing with page sync."
                            )

                # Save batch
                if records_with_permissions:
                    await self.data_entities_processor.on_new_records(records_with_permissions)
                    self.logger.info(f"Saved batch of {len(records_with_permissions)} {object_type}(s) and files")

                # Update sync point after each iteration with latest edit time from this batch
                # Note: still use the original last_sync_time for comparison in the next iteration
                if latest_edit_time:
                    await self.pages_sync_point.update_sync_point(
                        sync_point_key,
                        {"last_sync_time": latest_edit_time}
                    )
                    self.logger.debug(f"Updated {object_type}s sync checkpoint to {latest_edit_time} after batch")

                # Pagination - only continue if we haven't hit the sync point threshold
                if should_stop:
                    break

                if not data.get("has_more") or not data.get("next_cursor"):
                    break
                cursor = data.get("next_cursor")

            # Final sync point update (in case no records were found but this is first sync)
            if not latest_edit_time and not last_sync_time:
                # First sync - initialize sync point even if no records found
                current_time = self._get_current_iso_time()
                await self.pages_sync_point.update_sync_point(
                    sync_point_key,
                    {"last_sync_time": current_time}
                )
                self.logger.info(f"Initialized {object_type}s sync checkpoint to {current_time}")

            self.logger.info(
                f"✅ {type_display} sync complete. "
                f"{type_display}s: {total_synced}, Files: {total_files}"
            )

        except Exception as e:
            self.logger.error(f"❌ {type_display} sync failed: {e}", exc_info=True)
            raise

    # ==================== Fetching Methods ======================

    async def _fetch_page_as_blocks(
        self,
        page_id: str,
        parser: NotionBlockParser,
        parent_page_url: Optional[str] = None
    ) -> BlocksContainer:
        """
        Fetch all blocks from a Notion page recursively and build BlocksContainer.

        Args:
            page_id: Notion page ID
            parser: NotionBlockParser instance
            parent_page_url: Optional parent page URL to use for image blocks without weburl

        Returns:
            BlocksContainer with all blocks and block groups
        """
        blocks: List[Block] = []
        block_groups: List[BlockGroup] = []
        
        # Recursively process all blocks starting from root
        await self._process_blocks_recursive(
            page_id,
            parser,
            blocks,
            block_groups,
            parent_group_index=None,
            parent_page_url=parent_page_url
        )
        
        # Post-processing: Update indices and metadata
        self._finalize_indices_and_metadata(blocks, block_groups)
        
        # Convert image blocks to base64 format
        await self._convert_image_blocks_to_base64(blocks, parent_page_url)
        
        return BlocksContainer(blocks=blocks, block_groups=block_groups)

    async def _fetch_data_source_as_blocks(
        self,
        data_source_id: str,
        parser: NotionBlockParser
    ) -> BlocksContainer:
        """
        Fetch data source properties and rows, convert to TABLE block structure.

        Delegates the actual parsing to NotionBlockParser.parse_data_source_to_blocks().

        Args:
            data_source_id: Notion data source ID
            parser: NotionBlockParser instance

        Returns:
            BlocksContainer with TABLE BlockGroup and TABLE_ROW blocks
        """
        # Step 1: Fetch data source metadata to get column definitions
        datasource = await self._get_fresh_datasource()
        metadata_response = await datasource.retrieve_data_source_by_id(data_source_id)
        
        if not metadata_response.success:
            self.logger.error(f"Failed to fetch data source metadata: {metadata_response.error}")
            return BlocksContainer(blocks=[], block_groups=[])
        
        metadata = metadata_response.data.json() if metadata_response.data else {}
        
        # Step 2: Fetch all rows with pagination
        all_rows: List[Dict[str, Any]] = []
        cursor = None
        page_size = 100
        
        while True:
            try:
                query_body = {"page_size": page_size}
                if cursor:
                    query_body["start_cursor"] = cursor
                
                response = await datasource.query_data_source_by_id(
                    data_source_id=data_source_id,
                    request_body=query_body
                )
                
                if not response.success:
                    self.logger.warning(f"Failed to query data source: {response.error}")
                    break
                
                data = response.data.json() if response.data else {}
                results = data.get("results", [])
                
                if not results:
                    break
                
                all_rows.extend(results)
                
                # Check for more pages
                if not data.get("has_more") or not data.get("next_cursor"):
                    break
                cursor = data.get("next_cursor")
                
            except Exception as e:
                self.logger.error(f"Error querying data source {data_source_id}: {e}", exc_info=True)
                break
        
        # Step 3: Delegate parsing to NotionBlockParser
        blocks, block_groups = await parser.parse_data_source_to_blocks(
            data_source_metadata=metadata,
            data_source_rows=all_rows,
            data_source_id=data_source_id
        )
        
        self.logger.info(f"Fetched data source {data_source_id}: {len(block_groups)} tables, {len(blocks)} rows")
        
        return BlocksContainer(blocks=blocks, block_groups=block_groups)

    async def _fetch_page_attachments_and_comments(
        self, 
        page_id: str,
        page_url: str = ""
    ) -> Tuple[List[FileRecord], List[CommentRecord]]:
        """
        Fetch all attachments and comments from a Notion page in a single traversal.
        
        Uses efficient single-traversal approach:
        - Single recursive pass collects attachment blocks AND all block IDs
        - Then fetches comments for page + collected block IDs
        - Processes comments into CommentRecords with block content in comment_selection
        
        Args:
            page_id: Notion page ID
            page_url: Optional page URL (will be fetched if not provided)
            
        Returns:
            Tuple of (List of FileRecord objects, List of CommentRecord objects)
        """
        try:
            file_records: List[FileRecord] = []
            
            # Single traversal to collect attachment blocks and all block IDs
            attachment_blocks, all_block_ids = await self._fetch_attachment_blocks_and_block_ids_recursive(page_id)
            
            # Create file records from attachment blocks
            for block in attachment_blocks:
                file_record = self._create_file_record_from_block(block, page_id, page_url)
                if file_record:
                    file_records.append(file_record)
            
            # Fetch comments for page and all collected block IDs
            all_comments = await self._fetch_comments_for_blocks(page_id, all_block_ids)
            
            # Process comments into CommentRecords
            # All comments use page_id as parent (threading can be added later via thread_parent_map)
            comment_records, comment_attachments = await self._process_comments_to_records(
                comments=all_comments,
                page_id=page_id,
                page_url=page_url,
                thread_parent_map=None  # No threading for now, can be added in future
            )
            
            # Add comment attachments to file records
            file_records.extend(comment_attachments)
            
            return file_records, comment_records
            
        except Exception as e:
            self.logger.error(
                f"Failed to fetch attachments and comments for page {page_id}: {e}", 
                exc_info=True
            )
            return [], []

    async def _fetch_attachment_blocks_and_block_ids_recursive(
        self,
        block_id: str
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        """
        Recursively fetch attachment blocks and collect all block IDs in a single traversal.
        
        This is more efficient than separate traversals - we collect both attachment blocks
        and block IDs in one pass, then fetch comments for those block IDs separately.
        
        Traversal strategy:
        - Fetches children with pagination (page_size=50)
        - Collects attachment block types: file, video, pdf, embed, bookmark
        - Collects all block IDs (for comment fetching)
        - Skips: image (as FileRecord), child_page, child_database, data_source
        - Recurses into blocks with has_children=true (except skipped types)
        
        Args:
            block_id: Notion block or page ID
            
        Returns:
            Tuple of (List of attachment blocks, List of all block IDs encountered)
        """
        attachment_blocks: List[Dict[str, Any]] = []
        all_block_ids: List[str] = []
        cursor: Optional[str] = None
        page_size = 50  # Notion API max
        
        while True:
            try:
                datasource = await self._get_fresh_datasource()
                response = await datasource.retrieve_block_children(
                    block_id=block_id,
                    start_cursor=cursor,
                    page_size=page_size
                )
                
                if not response.success:
                    error_msg = response.error if response else "No response"
                    self.logger.error(
                        f"Failed to fetch block children for {block_id}: {error_msg}"
                    )
                    # Raise exception to propagate API error
                    raise Exception(f"Notion API error while fetching block children for {block_id}: {error_msg}")
                
                data = response.data.json() if response.data else {}
                
                if not isinstance(data, dict):
                    self.logger.warning(
                        f"Expected dictionary but got {type(data)} for block {block_id}"
                    )
                    break
                
                results = data.get("results", [])
                if not results:
                    break
                
                # Process each block
                for block in results:
                    block_type = block.get("type", "")
                    block_id_found = block.get("id")
                    
                    # Skip child pages, databases, and data sources (don't recurse)
                    if block_type in ["child_page", "child_database", "data_source"]:
                        continue

                    # Collect block ID for comment fetching (all blocks except skipped types)
                    # Note: Image blocks are included for comment fetching, but skipped as FileRecords
                    if block_id_found:
                        all_block_ids.append(block_id_found)

                    # Skip image blocks (we don't want images as FileRecords)
                    if block_type == "image":
                        # Still recurse into image blocks if they have children
                        has_children = block.get("has_children", False)
                        if has_children and block_id_found:
                            child_attachments, child_block_ids = await self._fetch_attachment_blocks_and_block_ids_recursive(block_id_found)
                            attachment_blocks.extend(child_attachments)
                            all_block_ids.extend(child_block_ids)
                        continue
                    
                    # Check if this is an attachment block (file, video, pdf, embed, bookmark)
                    if block_type in ["file", "video", "pdf", "embed", "bookmark"]:
                        attachment_blocks.append(block)
                    
                    # Recurse into blocks with children (except skipped types)
                    has_children = block.get("has_children", False)
                    if has_children and block_id_found:
                        child_attachments, child_block_ids = await self._fetch_attachment_blocks_and_block_ids_recursive(block_id_found)
                        attachment_blocks.extend(child_attachments)
                        all_block_ids.extend(child_block_ids)
                
                # Check for more pages
                has_more = data.get("has_more", False)
                cursor = data.get("next_cursor")
                
                if not has_more or not cursor:
                    break
                    
            except Exception as e:
                self.logger.error(
                    f"Error fetching attachment blocks and block IDs for {block_id}: {e}",
                    exc_info=True
                )
                # Re-raise to propagate error to caller
                raise
        
        return attachment_blocks, all_block_ids

    async def _fetch_comments_for_block(
        self,
        block_id: str
    ) -> List[Dict[str, Any]]:
        """
        Fetch all comments for a specific block with pagination.
        
        Args:
            block_id: Notion block or page ID
            
        Returns:
            List of comment objects from Notion API
        """
        # Validate block_id before making API call
        if not block_id or not block_id.strip():
            return []
        
        all_comments: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        page_size = 100  # Notion API max for comments
        
        while True:
            try:
                datasource = await self._get_fresh_datasource()
                response = await datasource.retrieve_comments(
                    block_id=block_id,
                    start_cursor=cursor,
                    page_size=page_size
                )

                # Check if response.data exists before trying to parse
                if response.data:
                    try:
                        response_data = response.data.json()
                        if isinstance(response_data, dict) and response_data.get("object") == "error":
                            self.logger.error(f"Notion API error for block {block_id}: {response_data}")
                    except Exception as parse_error:
                        self.logger.error(f"Failed to parse response.data: {parse_error}")
                
                if not response.success:
                    error_msg = response.error if response else "No response"
                    self.logger.warning(f"API call failed for block {block_id}: {error_msg}")
                    break
                
                # Only try to parse JSON if response is successful
                data = {}
                if response.data:
                    try:
                        data = response.data.json()
                    except Exception as parse_error:
                        self.logger.error(f"Failed to parse response.data as JSON: {parse_error}")
                        break
                else:
                    break
                
                # Check if the response is an error object
                if isinstance(data, dict) and data.get("object") == "error":
                    error_msg = data.get("message", "Unknown error")
                    error_code = data.get("code", "unknown")
                    self.logger.error(f"Notion API returned error for block {block_id}: [{error_code}] {error_msg}")
                    break
                
                if not isinstance(data, dict):
                    break
                
                results = data.get("results", [])
                if not results:
                    break
                
                all_comments.extend(results)
                
                # Check for more pages
                has_more = data.get("has_more", False)
                cursor = data.get("next_cursor")
                
                if not has_more or not cursor:
                    break
                    
            except Exception as e:
                self.logger.error(
                    f"Error fetching comments for block {block_id}: {e}",
                    exc_info=True
                )
                break
        
        return all_comments

    async def _fetch_comments_for_blocks(
        self,
        page_id: str,
        block_ids: List[str]
    ) -> List[Tuple[Dict[str, Any], str]]:
        """
        Fetch comments for a page and a list of block IDs.
        
        Returns a flat list of (comment_dict, block_id) tuples where:
        - block_id is page_id for page-level comments
        - block_id is the actual block_id for block-level comments
        
        Args:
            page_id: Notion page ID
            block_ids: List of block IDs to fetch comments for
            
        Returns:
            List of (comment_dict, block_id) tuples. All original comment fields
            including discussion_id are preserved for future threading support.
        """
        all_comments: List[Tuple[Dict[str, Any], str]] = []
        
        try:
            # Fetch comments for the page itself
            page_comments = await self._fetch_comments_for_block(page_id)
            for comment in page_comments:
                all_comments.append((comment, page_id))
            
            # Fetch comments for all collected block IDs
            # Use asyncio.gather for parallel fetching to improve performance
            comment_tasks = [
                self._fetch_comments_for_block(block_id) 
                for block_id in block_ids
            ]
            
            comment_results = await asyncio.gather(*comment_tasks, return_exceptions=True)
            
            for block_id, comments_or_error in zip(block_ids, comment_results):
                if isinstance(comments_or_error, Exception):
                    self.logger.warning(
                        f"Failed to fetch comments for block {block_id}: {comments_or_error}. "
                        f"Continuing with other blocks."
                    )
                    continue
                
                for comment in comments_or_error:
                    all_comments.append((comment, block_id))
            
            self.logger.info(f"Fetched {len(all_comments)} total comments for page {page_id} ({len(block_ids)} blocks)")
            
        except Exception as e:
            self.logger.error(
                f"Error fetching comments for page {page_id} and blocks: {e}",
                exc_info=True
            )
        
        return all_comments

    async def _extract_block_text_content(
        self,
        block_id: str
    ) -> Optional[str]:
        """
        Fetch a block and extract plain text content from its rich_text fields.
        
        Args:
            block_id: Notion block ID
            
        Returns:
            Plain text string extracted from the block, or None if block has no text content.
        """
        try:
            datasource = await self._get_fresh_datasource()
            response = await datasource.retrieve_block(block_id=block_id)
            
            if not response.success:
                self.logger.warning(f"Failed to fetch block {block_id} for text extraction: {response.error}")
                return None
            
            data = response.data.json() if response.data else {}
            if not isinstance(data, dict):
                return None
            
            block_type = data.get("type", "")
            if not block_type:
                return None
            
            # Extract rich_text based on block type
            rich_text = None
            block_data = data.get(block_type, {})
            
            if isinstance(block_data, dict):
                rich_text = block_data.get("rich_text", [])
            
            # If no rich_text found, try common patterns
            if not rich_text:
                # Some blocks might have text directly
                if "text" in block_data:
                    text_obj = block_data["text"]
                    if isinstance(text_obj, list):
                        rich_text = text_obj
                    elif isinstance(text_obj, dict) and "rich_text" in text_obj:
                        rich_text = text_obj["rich_text"]
            
            if rich_text and isinstance(rich_text, list):
                return self._extract_plain_text_from_rich_text(rich_text)
            
            return None
            
        except Exception as e:
            self.logger.warning(
                f"Failed to extract text content from block {block_id}: {e}",
                exc_info=True
            )
            return None

    async def _fetch_block_children_recursive(
        self,
        block_id: str
    ) -> List[Dict[str, Any]]:
        """
        Fetch all children blocks from a Notion block/page with pagination.

        Args:
            block_id: Notion block or page ID

        Returns:
            List of all child blocks (flattened, no pagination)
        """
        all_blocks: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        page_size = 100  # Notion API max
        
        while True:
            try:
                response = await self.data_source.retrieve_block_children(
                    block_id=block_id,
                    start_cursor=cursor,
                    page_size=page_size
                )
                
                if not response.success:
                    self.logger.warning(
                        f"Failed to fetch block children for {block_id}: {response.error}"
                    )
                    break
                
                # Convert response.data to dictionary (response.data is a Response object with .json() method)
                data = response.data.json() if response.data else {}
                
                if not isinstance(data, dict):
                    self.logger.warning(
                        f"Expected dictionary but got {type(data)} for block {block_id}: {data}"
                    )
                    break
                
                results = data.get("results", [])
                if not results:
                    break
                
                all_blocks.extend(results)
                
                # Check for more pages
                has_more = data.get("has_more", False)
                cursor = data.get("next_cursor")
                
                if not has_more or not cursor:
                    break
                    
            except Exception as e:
                self.logger.error(
                    f"Error fetching block children for {block_id}: {e}",
                    exc_info=True
                )
                break
        
        return all_blocks

    # ==================== Data Processing Methods ===================

    async def _process_blocks_recursive(
        self,
        parent_id: str,
        parser: NotionBlockParser,
        blocks: List[Block],
        block_groups: List[BlockGroup],
        parent_group_index: Optional[int],
        parent_page_url: Optional[str] = None,
    ) -> List[BlockContainerIndex]:
        """
        Recursively process Notion blocks and their children.

        Args:
            parent_id: Notion block/page ID to fetch children from
            parser: NotionBlockParser instance
            blocks: List to append blocks to
            block_groups: List to append block groups to
            parent_group_index: Index of parent BlockGroup (if nested)
            parent_page_url: URL of parent page for constructing block URLs

        Returns:
            List of BlockContainerIndex for immediate children processed at this level.
            This allows parent BlockGroups to know which blocks/groups are their direct children.
        """
        # Fetch children blocks
        child_blocks = await self._fetch_block_children_recursive(parent_id)
        
        if not child_blocks:
            return []
        
        current_level_indices: List[BlockContainerIndex] = []
        
        # Process each child block
        for notion_block in child_blocks:
            # Skip archived/trashed blocks
            if notion_block.get("archived", False) or notion_block.get("in_trash", False):
                continue
            
            # Skip unsupported block types (and their children)
            block_type = notion_block.get("type", "")
            if block_type == "unsupported":
                self.logger.debug(
                    f"Skipping unsupported block type (id: {notion_block.get('id', 'unknown')}) "
                    f"and its children"
                )
                continue
            
            # Parse the block
            parsed_block, parsed_group, _ = await parser.parse_block(
                notion_block,
                parent_group_index,
                0,  # Index will be set when appending
                parent_page_url  # Pass parent page URL
            )
            
            has_children = notion_block.get("has_children", False)
            block_id = notion_block.get("id", "")
            
            # Handle parsed block
            if parsed_block:
                block_index = len(blocks)
                parsed_block.index = block_index
                parsed_block.parent_index = parent_group_index
                blocks.append(parsed_block)
                
                # Fix #1: If block has children, create wrapper BlockGroup to preserve hierarchy
                if has_children and block_id:
                    # Create wrapper BlockGroup for the block with children
                    wrapper_group = BlockGroup(
                        id=str(uuid4()),
                        index=len(block_groups),
                        parent_index=parent_group_index,
                        type=GroupType.INLINE,
                        data=parsed_block.data,  # Store block content in group data
                        source_group_id=block_id,
                        description=f"Wrapper for {parsed_block.type.value} with children",
                        format=parsed_block.format,
                    )
                    block_groups.append(wrapper_group)
                    
                    # Update block's parent to point to wrapper
                    parsed_block.parent_index = wrapper_group.index
                    
                    # The wrapper's first child is the block itself
                    wrapper_children = [BlockContainerIndex(block_index=block_index)]
                    
                    # Process children with wrapper as parent
                    child_indices = await self._process_blocks_recursive(
                        block_id,
                        parser,
                        blocks,
                        block_groups,
                        wrapper_group.index,  # Wrapper becomes the parent
                        parent_page_url  # Pass parent page URL
                    )
                    
                    # Combine block and its children
                    wrapper_group.children = wrapper_children + child_indices
                    
                    # Add wrapper group to current level indices (not the block)
                    current_level_indices.append(BlockContainerIndex(block_group_index=wrapper_group.index))
                else:
                    # Block without children - add directly
                    current_level_indices.append(BlockContainerIndex(block_index=block_index))
            
            # Handle parsed group
            elif parsed_group:
                group_index = len(block_groups)
                parsed_group.index = group_index
                parsed_group.parent_index = parent_group_index
                block_groups.append(parsed_group)
                current_level_indices.append(BlockContainerIndex(block_group_index=group_index))
                
                # Fix #2: Process children and set them on the group
                if has_children and block_id:
                    child_indices = await self._process_blocks_recursive(
                        block_id,
                        parser,
                        blocks,
                        block_groups,
                        group_index,  # This group becomes the parent for its children
                        parent_page_url  # Pass parent page URL
                    )
                    # Set children on the nested group
                    parsed_group.children = child_indices
            
            # Fix #3: Handle unknown/ignored blocks with children
            elif has_children and block_id:
                # Unknown block type with children - still process children to avoid data loss
                self.logger.warning(
                    f"Block type {notion_block.get('type')} returned None from parser "
                    f"but has children (id: {block_id}). Processing children anyway to avoid data loss."
                )
                # Process children with same parent
                await self._process_blocks_recursive(
                    block_id,
                    parser,
                    blocks,
                    block_groups,
                    parent_group_index,
                    parent_page_url  # Pass parent page URL
                )
        
        return current_level_indices

    def _finalize_indices_and_metadata(
        self,
        blocks: List[Block],
        block_groups: List[BlockGroup]
    ) -> None:
        """
        Helper to clean up indices and table rows after recursion finishes.
        
        Updates final indices and sets table row header flags based on table metadata.
        
        Args:
            blocks: List of all blocks
            block_groups: List of all block groups
        """
        # Update final indices (defensive, though append order usually suffices)
        for i, block in enumerate(blocks):
            block.index = i
        
        for i, group in enumerate(block_groups):
            group.index = i
        
        # Update table row metadata with correct row numbers and header flags
        # Group table rows by their parent table
        table_row_counts: Dict[int, int] = {}
        table_header_flags: Dict[int, bool] = {}  # Track which tables have headers
        
        # First pass: Identify tables with headers
        for group in block_groups:
            if group.type == GroupType.TABLE and group.table_metadata:
                if group.table_metadata.has_header:
                    table_header_flags[group.index] = True
        
        # Second pass: Update row numbers and header flags
        for block in blocks:
            if block.type == BlockType.TABLE_ROW and block.parent_index is not None:
                table_index = block.parent_index
                
                # Initialize counter for this table if needed
                if table_index not in table_row_counts:
                    table_row_counts[table_index] = 0
                
                # Update row number
                if block.table_row_metadata:
                    table_row_counts[table_index] += 1
                    block.table_row_metadata.row_number = table_row_counts[table_index]
                    
                    # Set header flag if this is the first row and table has headers
                    if table_row_counts[table_index] == 1 and table_header_flags.get(table_index, False):
                        block.table_row_metadata.is_header = True

    async def _convert_image_blocks_to_base64(
        self,
        blocks: List[Block],
        parent_page_url: Optional[str] = None
    ) -> None:
        """
        Convert image blocks to base64 format by fetching images from URLs.
        
        Modifies blocks in-place:
        - Updates Block.data to {"uri": "data:image/{ext};base64,{base64}"}
        - Sets Block.format to DataFormat.BASE64
        - Updates Block.weburl: keeps image block's weburl, or uses parent_page_url if missing
        
        Args:
            blocks: List of Block objects (modified in-place)
            parent_page_url: Optional parent page URL to use if image block has no weburl
            
        Raises:
            Exception: If any image fails to download or convert (includes block ID and URL in message)
        """
        # Filter image blocks with weburl (signed URLs from Notion)
        image_blocks = [
            block for block in blocks 
            if block.type == BlockType.IMAGE and block.weburl is not None
        ]
        
        if not image_blocks:
            return
        
        # Store original weburls before conversion
        original_weburls = {block.id: block.weburl for block in image_blocks}
        
        # Initialize ImageParser for SVG conversion
        image_parser = ImageParser(self.logger)
        
        # Batch fetch images in parallel
        async def fetch_image(block: Block) -> Tuple[Block, Optional[str], Optional[Exception]]:
            """Fetch a single image and return block, base64_data_url, and any error"""
            image_url = str(block.weburl)  # Signed URL from Notion
            block_id = block.source_id or block.id
            
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        image_url, 
                        timeout=aiohttp.ClientTimeout(total=10),
                        allow_redirects=True
                    ) as response:
                        response.raise_for_status()
                        
                        # Get Content-Type
                        content_type = response.headers.get('content-type', '').lower()
                        content_type_clean = content_type.split(';')[0].strip()
                        
                        # Validate it's an image
                        if not content_type_clean.startswith('image/'):
                            raise Exception(f"Invalid content type: {content_type_clean}")
                        
                        # Read image bytes
                        image_bytes = await response.read()
                        
                        if not image_bytes:
                            raise Exception("Empty image content received")
                        
                        # Determine if SVG
                        is_svg = (
                            content_type_clean == 'image/svg+xml' or 
                            'svg' in content_type_clean or
                            image_url.lower().endswith('.svg')
                        )
                        
                        if is_svg:
                            # Convert SVG to base64, then to PNG base64
                            svg_base64 = base64.b64encode(image_bytes).decode('utf-8')
                            png_base64 = image_parser.svg_base64_to_png_base64(svg_base64)
                            base64_data_url = f"data:image/png;base64,{png_base64}"
                        else:
                            # Get extension from MIME type
                            extension = get_extension_from_mimetype(content_type_clean)
                            if not extension:
                                # Fallback: try to get from URL
                                parsed_url = urlparse(image_url)
                                path = parsed_url.path
                                if '.' in path:
                                    extension = path.split('.')[-1].lower()
                                else:
                                    extension = 'png'  # Default fallback
                            
                            # Convert to base64
                            image_base64 = base64.b64encode(image_bytes).decode('utf-8')
                            base64_data_url = f"data:image/{extension};base64,{image_base64}"
                        
                        return block, base64_data_url, None
                        
            except Exception as e:
                return block, None, Exception(f"Failed to fetch image for block {block_id} from URL {image_url}: {str(e)}")
        
        # Fetch all images in parallel
        fetch_tasks = [fetch_image(block) for block in image_blocks]
        results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
        
        # Process results and update blocks
        for block, base64_data_url, error in results:
            if error:
                # Re-raise exception with block ID and URL
                raise error
            
            if not base64_data_url:
                block_id = block.source_id or block.id
                raise Exception(f"Failed to convert image for block {block_id}: no base64 data returned")
            
            # Update block data and format
            block.data = {"uri": base64_data_url}
            block.format = DataFormat.BASE64
            
            # Update weburl: keep original image block's weburl, or use parent_page_url if missing
            original_weburl = original_weburls.get(block.id)
            if original_weburl:
                block.weburl = original_weburl
            elif parent_page_url:
                block.weburl = parent_page_url
            else:
                block.weburl = None

    async def _resolve_child_reference_blocks(
        self, 
        blocks: List[Block], 
        parent_record: Optional[Record] = None
    ) -> None:
        """
        Resolve internal record IDs for child reference blocks.
        Creates real records with minimal info for children that haven't synced yet.
        These records will be automatically updated with full data when they sync.
        
        Updates block.data['child_record_id'] by querying ArangoDB for records
        with matching external_record_id. If not found, creates a real record.
        
        Args:
            blocks: List of blocks (modified in-place)
            parent_record: Optional parent record for permission inheritance
        """
        # Filter child reference blocks that need resolution
        child_ref_blocks = [
            block for block in blocks 
            if block.type == BlockType.CHILD_RECORD
            and isinstance(block.data, dict)
            and block.data.get("child_external_id")
            and not block.data.get("child_record_id")
        ]
        
        if not child_ref_blocks:
            return
        
        # Resolve in parallel
        async def resolve_or_create_child_record(block: Block) -> None:
            """Resolve existing record or create minimal record for unsynced child"""
            try:
                child_external_id = block.data["child_external_id"]
                child_record_name = block.data.get("child_record_name", "Untitled")
                child_record_type = block.data.get("child_record_type", "NOTION_PAGE")
                
                # Query for the child record using get_record_by_external_id
                async with self.data_store_provider.transaction() as tx_store:
                    child_record = await tx_store.get_record_by_external_id(
                        connector_id=self.connector_id,
                        external_id=child_external_id
                    )
                
                if child_record:
                    # Existing record found (real or previously created minimal record)
                    block.data["child_record_id"] = child_record.id
                    self.logger.debug(
                        f"✅ Resolved child reference: {child_external_id} -> {child_record.id}"
                    )
                else:
                    # Create real record with minimal info
                    # This will be updated with full data when the child actually syncs
                    minimal_record = WebpageRecord(
                        org_id=self.data_entities_processor.org_id,
                        record_name=child_record_name,
                        record_type=RecordType[child_record_type],
                        external_record_id=child_external_id,
                        external_revision_id="minimal",  # Will trigger update when real content syncs
                        connector_id=self.connector_id,
                        connector_name=Connectors.NOTION,
                        record_group_type=RecordGroupType.NOTION_WORKSPACE,
                        external_record_group_id=self.workspace_id,
                        mime_type=MimeTypes.BLOCKS.value,
                        indexing_status=IndexingStatus.AUTO_INDEX_OFF.value,  # Don't index minimal records
                        version=1,
                        origin=OriginTypes.CONNECTOR,
                        # Inherit permissions from parent for proper access control
                        inherit_permissions=True,
                        parent_external_record_id=parent_record.external_record_id if parent_record else None,
                    )
                    
                    # Create record with empty permissions (will inherit from parent)
                    await self.data_entities_processor.on_new_records([(minimal_record, [])])
                    
                    block.data["child_record_id"] = minimal_record.id
                    
                    self.logger.info(
                        f"✨ Created minimal record for unsynced child: {child_external_id} -> {minimal_record.id}. "
                        f"Will be enriched when child syncs."
                    )

            except Exception as e:
                self.logger.error(
                    f"❌ Error resolving/creating child reference: {e}",
                    exc_info=True
                )
        
        # Resolve all references in parallel
        await asyncio.gather(
            *[resolve_or_create_child_record(block) for block in child_ref_blocks], 
            return_exceptions=True
        )

    async def _resolve_table_row_children(
        self,
        blocks: List[Block],
        parent_data_source_record: Optional[Record] = None
    ) -> None:
        """
        Resolve child records for table rows (database row pages that have child pages).
        
        Fetches child pages for each table row page and populates
        table_row_metadata.children_records with ChildRecord objects.
        Creates minimal records for children that haven't synced yet.
        
        Args:
            blocks: List of blocks (modified in-place)
            parent_data_source_record: Optional parent data source record for permission inheritance
        """
        # Filter TABLE_ROW blocks that might have children
        table_row_blocks = [
            block for block in blocks 
            if block.type == BlockType.TABLE_ROW
            and block.source_id  # Row page ID
        ]
        
        if not table_row_blocks:
            return
        
        # Resolve children for each table row in parallel
        async def resolve_row_children(block: Block) -> None:
            """Fetch and resolve child pages for a single table row"""
            try:
                row_page_id = block.source_id
                
                # Fetch child blocks to find child_page blocks
                datasource = await self._get_fresh_datasource()
                response = await datasource.retrieve_block_children(
                    block_id=row_page_id,
                    page_size=100
                )
                
                if not response.success:
                    # No children or error - skip
                    return
                
                data = response.data.json() if response.data else {}
                child_blocks = data.get("results", [])
                
                if not child_blocks:
                    return
                
                # Find child_page blocks
                child_pages = [
                    b for b in child_blocks
                    if b.get("type") == "child_page" and not b.get("archived", False)
                ]
                
                if not child_pages:
                    return
                
                # Create ChildRecord objects for each child page
                children_records = []
                
                for child_page_block in child_pages:
                    child_page_id = child_page_block.get("id")
                    child_page_data = child_page_block.get("child_page", {})
                    child_title = child_page_data.get("title", "Untitled")
                    
                    # Query for existing record or create minimal one
                    async with self.data_store_provider.transaction() as tx_store:
                        child_record = await tx_store.get_record_by_external_id(
                            connector_id=self.connector_id,
                            external_id=child_page_id
                        )
                    
                    if child_record:
                        # Existing record found
                        children_records.append(ChildRecord(
                            record_id=child_record.id,
                            record_name=child_record.record_name,
                            record_type=child_record.record_type
                        ))
                        self.logger.debug(f"✅ Resolved row child: {child_page_id} -> {child_record.id}")
                    else:
                        # Create minimal record for unsynced child
                        minimal_record = WebpageRecord(
                            org_id=self.data_entities_processor.org_id,
                            record_name=child_title,
                            record_type=RecordType.NOTION_PAGE,
                            external_record_id=child_page_id,
                            external_revision_id="minimal",
                            connector_id=self.connector_id,
                            connector_name=Connectors.NOTION,
                            record_group_type=RecordGroupType.NOTION_WORKSPACE,
                            external_record_group_id=self.workspace_id,
                            mime_type=MimeTypes.BLOCKS.value,
                            indexing_status=IndexingStatus.AUTO_INDEX_OFF.value,
                            version=1,
                            origin=OriginTypes.CONNECTOR,
                            inherit_permissions=True,
                            parent_external_record_id=row_page_id,  # Parent is the row page
                        )
                        
                        await self.data_entities_processor.on_new_records([(minimal_record, [])])
                        
                        children_records.append(ChildRecord(
                            record_id=minimal_record.id,
                            record_name=child_title,
                            record_type=RecordType.NOTION_PAGE
                        ))
                        
                        self.logger.info(f"✨ Created minimal record for row child: {child_page_id} -> {minimal_record.id}")
                
                # Update table_row_metadata with children
                if children_records:
                    if not block.table_row_metadata:
                        block.table_row_metadata = TableRowMetadata()
                    block.table_row_metadata.children_records = children_records
                    self.logger.debug(f"📎 Row has {len(children_records)} child page(s)")
                    
            except Exception as e:
                self.logger.error(
                    f"❌ Error resolving children for table row {block.source_id}: {e}",
                    exc_info=True
                )
        
        # Resolve children for all rows in parallel
        await asyncio.gather(
            *[resolve_row_children(block) for block in table_row_blocks],
            return_exceptions=True
        )

    async def _process_comments_to_records(
        self,
        comments: List[Tuple[Dict[str, Any], str]],
        page_id: str,
        page_url: str,
        thread_parent_map: Optional[Dict[str, str]] = None
    ) -> Tuple[List[CommentRecord], List[FileRecord]]:
        """
        Process comments into CommentRecords with attachments.
        
        Uses optimized batch processing:
        - Collects unique block IDs from block-level comments
        - Batch fetches block text content in parallel
        - Processes comments using cached block text mapping
        
        Args:
            comments: List of (comment_dict, block_id) tuples
            page_id: Parent page ID (used as parent for all comments currently)
            page_url: Page URL for attachments
            thread_parent_map: Optional mapping of comment_id -> parent_comment_id for threading.
                             If None, all comments use page_id as parent (current behavior).
            
        Returns:
            Tuple of (List of CommentRecord objects, List of FileRecord objects from comment attachments)
        """
        comment_records: List[CommentRecord] = []
        attachment_files: List[FileRecord] = []
        
        # Get page URL if not provided
        if not page_url:
            try:
                page_response = await self.data_source.retrieve_page(page_id)
                if page_response.success and page_response.data:
                    page_data = page_response.data.json() if hasattr(page_response.data, 'json') else {}
                    page_url = page_data.get("url", "")
            except Exception:
                pass
        
        # First pass: Collect unique block IDs from block-level comments
        unique_block_ids = set()
        for comment, block_id in comments:
            if block_id != page_id:  # Block-level comment
                unique_block_ids.add(block_id)
        
        # Batch fetch block text content for all unique blocks in parallel
        block_text_map: Dict[str, Optional[str]] = {}
        if unique_block_ids:
            block_text_tasks = [
                self._extract_block_text_content(block_id)
                for block_id in unique_block_ids
            ]
            
            block_text_results = await asyncio.gather(*block_text_tasks, return_exceptions=True)
            
            for block_id, text_or_error in zip(unique_block_ids, block_text_results):
                if isinstance(text_or_error, Exception):
                    self.logger.warning(
                        f"Failed to extract text content from block {block_id}: {text_or_error}"
                    )
                    block_text_map[block_id] = None
                else:
                    block_text_map[block_id] = text_or_error
        
        # Second pass: Process comments using cached block text mapping
        for comment, block_id in comments:
            try:
                comment_id = comment.get("id")
                if not comment_id:
                    continue
                
                # Determine if this is a page-level or block-level comment
                is_page_level = (block_id == page_id)
                
                # Get block text content from cache (or None for page-level)
                comment_selection = None
                if not is_page_level:
                    comment_selection = block_text_map.get(block_id)
                
                # Determine parent: use thread_parent_map if provided, otherwise use page_id
                parent_external_record_id = None
                if thread_parent_map and comment_id in thread_parent_map:
                    parent_external_record_id = thread_parent_map[comment_id]
                else:
                    parent_external_record_id = page_id
                
                # Create comment record
                comment_record, comment_attachments = self._create_comment_record_from_notion_comment(
                    notion_comment=comment,
                    page_id=page_id,
                    comment_selection=comment_selection,
                    page_url=page_url,
                    parent_external_record_id=parent_external_record_id
                )
                
                if comment_record:
                    comment_records.append(comment_record)
                    attachment_files.extend(comment_attachments)
                    
            except Exception as e:
                self.logger.error(
                    f"Failed to process comment {comment.get('id', 'unknown')}: {e}",
                    exc_info=True
                )
                continue
        
        return comment_records, attachment_files

    # ==================== Transform Helpers ====================

    async def _create_workspace_record_group(self) -> None:
        """
        Create a RecordGroup for the Notion workspace.
        
        This should be called once when workspace info is first extracted from bot owner.
        """
        try:
            if not self.workspace_id or not self.workspace_name:
                self.logger.warning("Cannot create workspace record group: missing workspace info")
                return
            
            record_group = RecordGroup(
                org_id=self.data_entities_processor.org_id,
                name=self.workspace_name,
                external_group_id=self.workspace_id,
                connector_name=Connectors.NOTION,
                connector_id=self.connector_id,
                group_type=RecordGroupType.NOTION_WORKSPACE,
                created_at=get_epoch_timestamp_in_ms(),
                updated_at=get_epoch_timestamp_in_ms(),
            )
            
            # Create record group with empty permissions initially
            # Permissions will be added as users are synced
            await self.data_entities_processor.on_new_record_groups([(record_group, [])])
            
            self.logger.info(
                f"✅ Created workspace record group: {self.workspace_name} (ID: {self.workspace_id})"
            )
            
        except Exception as e:
            self.logger.error(f"❌ Failed to create workspace record group: {e}", exc_info=True)
            raise

    def _transform_to_app_user(self, user_data: Dict[str, Any]) -> Optional[AppUser]:
        """
        Transform Notion user data to AppUser entity.

        Args:
            user_data: Full user data from retrieve_user API (includes email)

        Returns:
            AppUser object or None if transformation fails

        Expected user_data format:
        {
            "object": "user",
            "id": "6794760a-1f15-45cd-9c65-0dfe42f5135a",
            "name": "Aman Gupta",
            "avatar_url": null,
            "type": "person",
            "person": {
                "email": "aman@example.com"
            }
        }
        """
        try:
            user_id = user_data.get("id")
            user_type = user_data.get("type")
            name = user_data.get("name")

            # Only process person users
            if user_type != "person":
                self.logger.debug(f"Skipping non-person user type: {user_type}")
                return None

            # Extract email from nested person object
            person_data = user_data.get("person", {}) or {}
            email = person_data.get("email", "").strip()

            # Validate required fields
            if not user_id:
                self.logger.warning("User data missing ID")
                return None

            if not email:
                self.logger.warning(f"User {user_id} ({name}) has no email address")
                return None

            return AppUser(
                app_name=Connectors.NOTION,
                connector_id=self.connector_id,
                source_user_id=user_id,
                org_id=self.data_entities_processor.org_id,
                email=email,
                full_name=name,
            )

        except Exception as e:
            self.logger.error(f"❌ Failed to transform user: {e}", exc_info=True)
            return None

    def _transform_to_webpage_record(
        self, 
        obj_data: Dict[str, Any],
        object_type: str
    ) -> WebpageRecord:
        """
        Unified transform for pages, databases, and data_sources to WebpageRecord.
        
        Args:
            obj_data: Raw data from Notion API (page, database, or data_source)
            object_type: "page", "database", or "data_source"
        
        Returns:
            For database/data_source: (WebpageRecord)
            For page: (WebpageRecord)
        """
        try:
            obj_id = obj_data.get("id")
            
            # Extract title based on type
            if object_type == "database":
                # Database: title is directly in the response
                title_parts = obj_data.get("title", [])
                title = "".join([t.get("plain_text", "") for t in title_parts]) or "Untitled Database"
                record_type = RecordType.NOTION_DATABASE
            elif object_type == "data_source":
                # Data Source: title is directly in the response (same as database)
                title_parts = obj_data.get("title", [])
                title = "".join([t.get("plain_text", "") for t in title_parts]) or "Untitled Data Source"
                record_type = RecordType.NOTION_DATA_SOURCE
            else:  # page
                # Page: title is in properties
                title = self._extract_page_title(obj_data)
                record_type = RecordType.NOTION_PAGE
            
            # Parse timestamps (same for all)
            created_time = obj_data.get("created_time")
            last_edited_time = obj_data.get("last_edited_time")
            
            source_created_at = self._parse_iso_timestamp(created_time) if created_time else None
            source_updated_at = self._parse_iso_timestamp(last_edited_time) if last_edited_time else None
            
            # Determine parent based on type
            parent_id = None
            
            if object_type == "data_source":
                # Data Source: parent is in parent.database_id
                parent = obj_data.get("parent", {})
                if parent.get("type") == "database_id":
                    parent_id = parent.get("database_id")
            else:
                # Page/Database: standard parent structure
                parent = obj_data.get("parent", {})
                parent_type = parent.get("type")
                
                if parent_type == "page_id":
                    parent_id = parent.get("page_id")
                elif parent_type == "database_id":
                    parent_id = parent.get("database_id")
                elif parent_type == "block_id":
                    parent_id = parent.get("block_id")
                elif parent_type == "data_source_id":
                    parent_id = parent.get("data_source_id")

            # Create WebpageRecord
            workspace_group_id = self.workspace_id if self.workspace_id else None
            
            return WebpageRecord(
                org_id=self.data_entities_processor.org_id,
                record_name=title,
                record_type=record_type,
                external_record_id=obj_id,
                record_group_type=RecordGroupType.NOTION_WORKSPACE,
                external_record_group_id=workspace_group_id,
                external_revision_id=last_edited_time,
                parent_external_record_id=parent_id,
                version=1,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.NOTION,
                connector_id=self.connector_id,
                mime_type=MimeTypes.BLOCKS.value,
                weburl=obj_data.get("url"),
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
            )
                        
        except Exception as e:
            self.logger.error(f"Failed to transform {object_type}: {e}", exc_info=True)
            return None

    def _create_comment_record_from_notion_comment(
        self,
        notion_comment: Dict[str, Any],
        page_id: str,
        comment_selection: Optional[str],
        page_url: str = "",
        parent_external_record_id: Optional[str] = None
    ) -> Tuple[Optional[CommentRecord], List[FileRecord]]:
        """
        Create CommentRecord from a Notion comment object and extract attachments.
        
        Args:
            notion_comment: Raw comment data from Notion API
            page_id: The parent page ID (used as fallback parent)
            comment_selection: Block text content for block-level comments, None for page-level comments
            page_url: The parent page web URL
            parent_external_record_id: Parent record ID. If None, uses page_id (current behavior).
                                      Can be set to comment_id for threading in future.
            
        Returns:
            Tuple of (CommentRecord object or None, List of FileRecord objects from comment attachments)
        """
        attachment_files: List[FileRecord] = []
        
        try:
            comment_id = notion_comment.get("id")
            if not comment_id:
                return None, []
            
            # Extract comment text from rich_text (handles mentions)
            rich_text = notion_comment.get("rich_text", [])
            comment_text = self._extract_plain_text_from_rich_text(rich_text)
            
            # Use comment text as record name, or fallback to a default
            record_name = comment_text if comment_text else "Comment"
            if not record_name or len(record_name.strip()) == 0:
                record_name = "Comment"
            
            # Use provided parent_external_record_id, or default to page_id
            if not parent_external_record_id:
                parent_external_record_id = page_id
            
            # Determine parent record type
            # If parent starts with "comment_", it's a comment reply (for future threading)
            if parent_external_record_id and parent_external_record_id.startswith("comment_"):
                parent_record_type = RecordType.COMMENT
            else:
                parent_record_type = RecordType.NOTION_PAGE
            
            # Extract author information
            created_by = notion_comment.get("created_by", {})
            author_source_id = created_by.get("id", "") if isinstance(created_by, dict) else ""
            
            # Parse timestamps
            created_time = notion_comment.get("created_time")
            last_edited_time = notion_comment.get("last_edited_time")
            source_created_at = self._parse_iso_timestamp(created_time) if created_time else None
            source_updated_at = self._parse_iso_timestamp(last_edited_time) if last_edited_time else None
            
            # Generate unique comment ID
            external_record_id = f"comment_{comment_id}"
            
            # Extract and create FileRecords for comment attachments
            attachments = notion_comment.get("attachments", [])
            for attachment in attachments:
                try:
                    # Handle different attachment structures
                    file_url = None
                    file_name = None
                    
                    if "file" in attachment:
                        file_obj = attachment["file"]
                        if isinstance(file_obj, dict):
                            file_url = file_obj.get("url", "")
                    
                    if file_url:
                        # Extract filename from URL
                        # Example: https://prod-files-secure.s3.us-west-2.amazonaws.com/.../accelerate_%281%29.txt?X-Amz-...
                        # Extract the filename part before query parameters
                        parsed_url = urlparse(file_url)
                        path = unquote(parsed_url.path)  # Decode URL encoding
                        # Get the last part of the path as filename
                        url_filename = path.split("/")[-1] if "/" in path else ""
                        # Use filename from URL if available, otherwise try attachment name
                        file_name = url_filename if url_filename else (attachment.get("name") or attachment.get("category", "attachment"))
                        # Determine MIME type from URL or category
                        category = attachment.get("category", "")
                        mime_type = MimeTypes.UNKNOWN.value
                        extension = ""
                        
                        if category:
                            # Map category to MIME type
                            category_mime_map = {
                                "productivity": MimeTypes.APPLICATION_OCTET_STREAM.value,
                                "image": MimeTypes.IMAGE_PNG.value,
                                "video": MimeTypes.VIDEO_MP4.value,
                                "audio": MimeTypes.AUDIO_MPEG.value,
                            }
                            mime_type = category_mime_map.get(category, MimeTypes.APPLICATION_OCTET_STREAM.value)
                        else:
                            # Try to infer from URL
                            mime_type, _ = mimetypes.guess_type(file_url)
                            if not mime_type:
                                mime_type = MimeTypes.APPLICATION_OCTET_STREAM.value
                        
                        # Extract extension from filename or MIME type
                        if file_name:
                            extension = file_name.split(".")[-1] if "." in file_name else ""
                        if not extension and mime_type:
                            extension = mimetypes.guess_extension(mime_type) or ""
                            if extension.startswith("."):
                                extension = extension[1:]
                        
                        # Generate unique file ID
                        file_id = f"comment_attachment_{comment_id}_{len(attachment_files)}"
                        
                        attachment_file = FileRecord(
                            org_id=self.data_entities_processor.org_id,
                            record_name=file_name or "Comment Attachment",
                            record_type=RecordType.FILE,
                            external_record_id=file_id,
                            parent_record_type=RecordType.COMMENT,
                            parent_external_record_id=external_record_id,
                            record_group_type=RecordGroupType.NOTION_WORKSPACE,
                            external_record_group_id=self.workspace_id,
                            version=1,
                            origin=OriginTypes.CONNECTOR,
                            connector_name=Connectors.NOTION,
                            connector_id=self.connector_id,
                            mime_type=mime_type,
                            signed_url=file_url,
                            weburl=page_url,
                            is_file=True,
                            extension=extension,
                            size_in_bytes=0,  # Notion doesn't provide file size in comment attachments
                            source_created_at=source_created_at,
                            source_updated_at=source_updated_at,
                        )
                        attachment_files.append(attachment_file)
                except Exception as e:
                    self.logger.warning(f"Failed to create file record from comment attachment: {e}", exc_info=True)
                    continue
            
            comment_record = CommentRecord(
                org_id=self.data_entities_processor.org_id,
                record_name=record_name,
                record_type=RecordType.COMMENT,
                external_record_id=external_record_id,
                parent_record_type=parent_record_type,
                parent_external_record_id=parent_external_record_id,
                record_group_type=RecordGroupType.NOTION_WORKSPACE,
                external_record_group_id=self.workspace_id,
                version=1,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.NOTION,
                connector_id=self.connector_id,
                mime_type=MimeTypes.PLAIN_TEXT.value,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
                author_source_id=author_source_id,
                resolution_status=None,  # Notion API returns unresolved comments, so None
                comment_selection=comment_selection,  # Block content for block-level comments, None for page-level
            )
            
            return comment_record, attachment_files
            
        except Exception as e:
            self.logger.error(f"Failed to create comment record from comment {notion_comment.get('id')}: {e}", exc_info=True)
            return None, []

    def _create_file_record_from_block(
        self,
        notion_block: Dict[str, Any],
        page_id: str,
        page_url: str = ""
    ) -> Optional[FileRecord]:
        """
        Create FileRecord from a Notion attachment block.
        
        Handles different block types:
        - file: Extract from block.file or block.external
        - video: Extract from block.video.file or block.video.external
        - pdf: Extract from block.pdf.file or block.pdf.external
        - embed: Extract from block.embed.url (external only)
        - bookmark: Extract from block.bookmark.url (external only)
        
        Skips blocks with MIME type starting with "image/" (all image types).
        
        Args:
            notion_block: Raw block data from Notion API
            page_id: Parent page ID
            
        Returns:
            FileRecord object or None if block should be skipped or creation fails
        """
        try:
            block_id = notion_block.get("id")
            block_type = notion_block.get("type", "")
            
            if not block_id:
                return None
            
            # Extract file info based on block type
            file_url = None
            file_name = None
            type_data = notion_block.get(block_type, {})
            
            if block_type == "file":
                file_name = type_data.get("name", "")
                if "file" in type_data:
                    file_obj = type_data["file"]
                    if isinstance(file_obj, dict):
                        file_url = file_obj.get("url", "")
                elif "external" in type_data:
                    external_obj = type_data["external"]
                    if isinstance(external_obj, dict):
                        file_url = external_obj.get("url", "")
                        
            elif block_type == "video":
                if "file" in type_data:
                    file_obj = type_data["file"]
                    if isinstance(file_obj, dict):
                        file_url = file_obj.get("url", "")
                elif "external" in type_data:
                    external_obj = type_data["external"]
                    if isinstance(external_obj, dict):
                        file_url = external_obj.get("url", "")
                        
            elif block_type == "pdf":
                if "file" in type_data:
                    file_obj = type_data["file"]
                    if isinstance(file_obj, dict):
                        file_url = file_obj.get("url", "")
                elif "external" in type_data:
                    external_obj = type_data["external"]
                    if isinstance(external_obj, dict):
                        file_url = external_obj.get("url", "")
                file_name = "document.pdf"  # Default name for PDFs
                
            elif block_type == "embed":
                # Embed blocks have URL directly in type_data
                file_url = type_data.get("url", "")
                    
            elif block_type == "bookmark":
                # Bookmark blocks have URL directly in type_data
                file_url = type_data.get("url", "")
            
            if not file_url:
                return None
            
            # Extract filename from URL if not provided
            if not file_name:
                file_name = file_url.split("/")[-1].split("?")[0]
                # If still no name, use block ID
                if not file_name or file_name == "":
                    file_name = f"attachment_{block_id[:8]}"
            
            # Determine extension and MIME type
            extension = None
            if "." in file_name:
                extension = file_name.split(".")[-1].lower()
            
            mime_type, _ = mimetypes.guess_type(file_name)
            if not mime_type:
                # Fallback based on block type
                if block_type == "pdf":
                    mime_type = "application/pdf"
                elif block_type == "video":
                    mime_type = "video/mp4"  # Default assumption
                elif block_type == "embed":
                    mime_type = MimeTypes.UNKNOWN.value
                elif block_type == "bookmark":
                    mime_type = MimeTypes.UNKNOWN.value
                else:
                    mime_type = MimeTypes.UNKNOWN.value
            
            # Skip image files (all image MIME types)
            if mime_type.startswith("image/"):
                self.logger.debug(f"Skipping image block {block_id} (MIME type: {mime_type})")
                return None
            
            # Generate unique file ID using block ID
            file_id = f"file_{page_id}_{block_id}"
            
            # Parse timestamps from block
            created_time = notion_block.get("created_time")
            last_edited_time = notion_block.get("last_edited_time")
            source_created_at = self._parse_iso_timestamp(created_time) if created_time else None
            source_updated_at = self._parse_iso_timestamp(last_edited_time) if last_edited_time else None
            
            return FileRecord(
                org_id=self.data_entities_processor.org_id,
                record_name=file_name,
                record_type=RecordType.FILE,
                external_record_id=file_id,
                parent_record_type=RecordType.NOTION_PAGE,
                parent_external_record_id=page_id,
                record_group_type=RecordGroupType.NOTION_WORKSPACE,
                external_record_group_id=self.workspace_id,
                version=1,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.NOTION,
                connector_id=self.connector_id,
                mime_type=mime_type,
                signed_url=file_url,
                weburl=page_url,
                is_file=True,
                extension=extension,
                size_in_bytes=None,  # Notion doesn't provide file size in block data
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
            )
            
        except Exception as e:
            self.logger.error(f"Failed to create file record from block {notion_block.get('id')}: {e}")
            return None

    # ==================== Utility Methods ====================

    def _extract_page_title(self, page_data: Dict[str, Any]) -> str:
        """Extract title from page data."""
        properties = page_data.get("properties", {})
        
        # Try common title property names
        for prop_name in ["title", "Title", "Name", "name"]:
            prop = properties.get(prop_name, {})
            if prop.get("type") == "title":
                title_array = prop.get("title", [])
                return "".join([t.get("plain_text", "") for t in title_array]) or "Untitled"
        
        # Fallback: look for any title-type property
        for prop in properties.values():
            if isinstance(prop, dict) and prop.get("type") == "title":
                title_array = prop.get("title", [])
                return "".join([t.get("plain_text", "") for t in title_array]) or "Untitled"
        
        return "Untitled"

    def _parse_iso_timestamp(self, timestamp_str: str) -> Optional[int]:
        """Parse ISO 8601 timestamp to epoch milliseconds."""
        try:
            return parse_timestamp(timestamp_str)
        except Exception as e:
            self.logger.debug(f"Failed to parse timestamp {timestamp_str}: {e}")
            return None

    def _get_current_iso_time(self) -> str:
        """Get current time in ISO 8601 format with Z suffix (matching Notion format)."""
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def _extract_plain_text_from_rich_text(self, rich_text_array: List[Dict[str, Any]]) -> str:
        """
        Extract plain text from Notion rich text array.
        Handles text, mentions, and other rich text types.
        
        Args:
            rich_text_array: List of rich text objects from Notion API
            
        Returns:
            Plain text string
        """
        if not rich_text_array:
            return ""
        
        text_parts = []
        for item in rich_text_array:
            item_type = item.get("type", "text")
            
            # Handle different rich text types
            if item_type == "text":
                # Regular text
                text_content = item.get("plain_text", "")
                if not text_content and "text" in item and isinstance(item["text"], dict):
                    text_content = item["text"].get("content", "")
                if text_content:
                    text_parts.append(text_content)
            elif item_type == "mention":
                # Mention - use plain_text which already includes @username format
                mention_text = item.get("plain_text", "")
                if mention_text:
                    text_parts.append(mention_text)
                else:
                    # Fallback: extract from mention object
                    mention_obj = item.get("mention", {})
                    if mention_obj.get("type") == "user":
                        user_obj = mention_obj.get("user", {})
                        user_name = user_obj.get("name", "")
                        if user_name:
                            text_parts.append(f"@{user_name}")
            else:
                # For other types (equation, etc.), use plain_text if available
                text_content = item.get("plain_text", "")
                if text_content:
                    text_parts.append(text_content)
        
        return "".join(text_parts)

    async def _get_fresh_datasource(self) -> NotionDataSource:
        """
        Get NotionDataSource with ALWAYS-FRESH access token.

        This method:
        1. Fetches current OAuth token from config
        2. Compares with existing client's token
        3. Updates client ONLY if token changed (mutation)
        4. Returns datasource with current token

        Returns:
            NotionDataSource with current valid token
        """
        if not self.notion_client:
            raise Exception("Notion client not initialized. Call init() first.")

        # Fetch current config from etcd (async I/O)
        config = await self.config_service.get_config(f"/services/connectors/{self.connector_id}/config")

        if not config:
            raise Exception("Notion configuration not found")

        # Extract fresh OAuth access token from credentials section
        credentials = config.get("credentials", {}) or {}
        fresh_token = credentials.get("access_token", "")

        if not fresh_token:
            raise Exception("No OAuth access token available")

        # Get current token from client
        internal_client = self.notion_client.get_client()
        current_token = internal_client.access_token

        # Update client's token if it changed (mutation)
        if current_token != fresh_token:
            self.logger.debug("🔄 Updating client with refreshed access token")
            internal_client.access_token = fresh_token
            internal_client.headers["Authorization"] = f"Bearer {fresh_token}"

        return NotionDataSource(self.notion_client)
