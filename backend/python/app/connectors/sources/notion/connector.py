"""
Notion Connector

Authentication: OAuth 2.0
"""

import asyncio
import base64
import mimetypes
from collections import defaultdict
from datetime import datetime, timezone
from logging import Logger
from typing import Any, AsyncGenerator, Dict, List, NoReturn, Optional, Tuple
from urllib.parse import unquote, urlparse
from uuid import uuid4

import aiohttp
import httpx
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

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
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthScopeConfig,
)
from app.connectors.core.registry.connector_builder import (
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
    DocumentationLink,
)
from app.connectors.core.registry.filters import (
    FilterCategory,
    FilterCollection,
    FilterField,
    FilterType,
    IndexingFilterKey,
    load_connector_filters,
)
from app.connectors.sources.notion.block_parser import NotionBlockParser
from app.connectors.sources.notion.common.apps import NotionApp
from app.models.blocks import (
    Block,
    BlockComment,
    BlockContainerIndex,
    BlockGroup,
    BlocksContainer,
    BlockSubType,
    BlockType,
    ChildRecord,
    ChildType,
    CommentAttachment,
    DataFormat,
    GroupSubType,
    GroupType,
    TableRowMetadata,
)
from app.models.entities import (
    AppUser,
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

# Notion OAuth URLs
# Note: Notion OAuth doesn't use traditional scopes. Permissions are configured
# when creating the integration in Notion's developer portal. The scope parameter
# below is a placeholder to satisfy the OAuth validator.
AUTHORIZE_URL = "https://api.notion.com/v1/oauth/authorize"
TOKEN_URL = "https://api.notion.com/v1/oauth/token"

@ConnectorBuilder("Notion")\
    .in_group("Notion")\
    .with_description("Sync pages, databases, and users from Notion")\
    .with_categories(["Knowledge Management", "Collaboration"])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Notion",
            authorize_url=AUTHORIZE_URL,
            token_url=TOKEN_URL,
            redirect_uri="connectors/oauth/callback/Notion",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=["read_content"],  # Placeholder: Notion uses capabilities, not URL scopes
                agent=[]
            ),
            fields=[
                CommonFields.client_id("Notion OAuth App"),
                CommonFields.client_secret("Notion OAuth App")
            ],
            icon_path="/assets/icons/connectors/notion.svg",
            app_group="Notion",
            app_description="OAuth application for accessing Notion API",
            app_categories=["Knowledge Management", "Collaboration"],
            additional_params={}
        )
    ])\
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
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_agent_support(True)
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        # Indexing filters
        .add_filter_field(FilterField(
            name="pages",
            display_name="Index Pages",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of Notion pages",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="databases",
            display_name="Index Databases",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of Notion databases",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="files",
            display_name="Index Files",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of files (attachments and comment attachments)",
            default_value=True
        ))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
    )\
    .build_decorator()
class NotionConnector(BaseConnector):
    """Notion connector for syncing pages, databases, and users."""

    # Constants for external_record_id parsing
    MIN_PARTS_NEW_FORMAT = 2  # Minimum parts for ID format: {id}_{hash}

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

        Routes to appropriate handler based on external_record_id prefix:
        - Comment attachments: "ca_" prefix
        - Block files: no prefix (default)
        """
        try:
            if not self.data_source:
                return None

            external_id = record.external_record_id
            if external_id.startswith("ca_") or external_id.startswith("comment_attachment_"):
                return await self._get_comment_attachment_url(record)
            else:
                return await self._get_block_file_url(record)

        except Exception as e:
            self.logger.error(f"Failed to get signed URL for {record.external_record_id}: {e}", exc_info=True)
            raise e

    async def _get_comment_attachment_url(self, record: Record) -> Optional[str]:
        """
        Get signed URL for a comment attachment by fetching from Notion API.

        Extracts comment_id and normalized_filename from external_record_id format: ca_{comment_id}_{normalized_filename}
        Matches attachment by normalized filename.
        """
        external_id = record.external_record_id

        # Extract comment_id and normalized_filename from format: ca_{comment_id}_{normalized_filename}
        if not external_id.startswith("ca_"):
            raise ValueError(
                f"Invalid comment attachment external_record_id format: {external_id}. "
                f"Expected format: ca_{{comment_id}}_{{normalized_filename}}"
            )

        # Remove "ca_" prefix and split
        # The normalized_filename may contain underscores, so we need to split carefully
        # Format: ca_{comment_id}_{normalized_filename}
        # We know comment_id is a UUID (no underscores), so we can split on first underscore after ca_
        parts = external_id[3:].split("_", 1)
        if not parts or not parts[0]:
            raise ValueError(f"Failed to extract comment_id from external_record_id: {external_id}")

        comment_id = parts[0]
        # normalized_filename is everything after the first underscore (may contain more underscores)
        normalized_filename = parts[1] if len(parts) > 1 else None

        if not normalized_filename:
            self.logger.warning(f"No normalized filename found in external_record_id: {external_id}")
            return record.signed_url

        # Fetch comment data from Notion API
        datasource = await self._get_fresh_datasource()
        response = await datasource.retrieve_comment(comment_id)
        if not response.success or not response.data:
            self.logger.warning(f"Failed to fetch comment {comment_id} for attachment")
            return record.signed_url

        comment_data = response.data.json() if hasattr(response.data, 'json') else {}
        attachments = comment_data.get("attachments", [])

        if not attachments:
            return record.signed_url

        # Match attachment by normalized filename
        for attachment in attachments:
            if "file" in attachment and isinstance(attachment["file"], dict):
                url = attachment["file"].get("url", "")
                if url:
                    # Extract and normalize filename from attachment
                    parsed_url = urlparse(url)
                    path = unquote(parsed_url.path)
                    url_filename = path.split("/")[-1] if "/" in path else ""
                    file_name = url_filename if url_filename else (attachment.get("name") or "attachment")
                    attachment_normalized = self._normalize_filename_for_id(file_name)

                    # Match by normalized filename
                    if attachment_normalized == normalized_filename:
                        return url

        # No match found - return None (removed fallback to first attachment)
        self.logger.warning(
            f"Could not find attachment with normalized filename '{normalized_filename}' "
            f"for comment {comment_id}"
        )
        return record.signed_url

    async def _get_block_file_url(self, record: Record) -> Optional[str]:
        """
        Get signed URL for a block file by fetching from Notion API.

        The external_record_id is the block_id itself.
        """
        block_id = record.external_record_id

        if not block_id:
            raise ValueError(f"Invalid block file external_record_id: {block_id}")

        datasource = await self._get_fresh_datasource()
        response = await datasource.retrieve_block(block_id)
        if not response.success or not response.data:
            return record.signed_url

        block_data = response.data.json() if hasattr(response.data, 'json') else {}
        block_type = block_data.get("type", "")
        type_data = block_data.get(block_type, {})

        for key in ["file", "external"]:
            if key in type_data and isinstance(type_data[key], dict):
                url = type_data[key].get("url")
                if url:
                    return url

        return None

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
            elif record.record_type == RecordType.DATASOURCE:
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
                    media_type='application/octet-stream',
                    headers={
                        "Content-Disposition": f'inline; filename="{record.external_record_id}_data_source.json"'
                    }
                )
            elif record.record_type == RecordType.WEBPAGE:
                parser = NotionBlockParser(self.logger, self.config_service)
                # Extract page URL from record.weburl (if available)
                parent_page_url = record.weburl if hasattr(record, 'weburl') and record.weburl else None

                # Fetch comments for the page (will be attached to blocks)
                comments_by_block = {}
                try:
                    _, comments_by_block = await self._fetch_page_attachments_and_comments(
                        record.external_record_id, parent_page_url
                    )
                except Exception as e:
                    self.logger.warning(f"Failed to fetch comments for page {record.external_record_id}: {e}")
                    comments_by_block = {}

                # Fetch page blocks recursively with comments
                blocks_container = await self._fetch_page_as_blocks(
                    record.external_record_id,
                    parser,
                    parent_page_url=parent_page_url,
                    comments_by_block=comments_by_block
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
                    media_type='application/octet-stream',
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
        """Notion connector does not support dynamic filter options."""
        raise NotImplementedError("Notion connector does not support dynamic filter options")

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
            raise

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

            # Check indexing filters
            files_indexing_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.FILES)

            if object_type == "page":
                pages_indexing_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.PAGES)
            else:  # data_source (database)
                databases_indexing_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.DATABASES)

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
                        # Set indexing status based on filter
                        if object_type == "page":
                            if not pages_indexing_enabled:
                                record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value
                        else:  # data_source (database)
                            if not databases_indexing_enabled:
                                record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value

                        records_with_permissions.append((record, []))
                        total_synced += 1
                        self.logger.debug(f"Synced {object_type}: {record.record_name} (last_edited: {last_edited_time})")

                    # Fetch attachments and comment attachments from blocks (for pages only)
                    # Comments themselves are attached to blocks in the BlocksContainer
                    if object_type == "page" and obj_id:
                        try:
                            page_url = obj_data.get("url", "")
                            attachment_records, comments_by_block = await self._fetch_page_attachments_and_comments(obj_id, page_url)

                            # Save block attachment FileRecords
                            for file_record in attachment_records:
                                # Set indexing status based on filter
                                if not files_indexing_enabled:
                                    file_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value
                                records_with_permissions.append((file_record, []))
                                total_files += 1

                            # Process comment attachments and save their FileRecords
                            if comments_by_block:
                                comment_attachment_records = await self._extract_comment_attachment_file_records(
                                    comments_by_block, obj_id, page_url
                                )
                                for file_record in comment_attachment_records:
                                    # Set indexing status based on filter
                                    if not files_indexing_enabled:
                                        file_record.indexing_status = IndexingStatus.AUTO_INDEX_OFF.value
                                    records_with_permissions.append((file_record, []))
                                    total_files += 1

                        except Exception as error:
                            self.logger.warning(
                                f"Failed to fetch attachments for page {obj_id}: {error}. "
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
        parent_page_url: Optional[str] = None,
        comments_by_block: Optional[Dict[str, List[Tuple[Dict[str, Any], str]]]] = None
    ) -> BlocksContainer:
        """
        Fetch all blocks from a Notion page recursively and build BlocksContainer.

        Args:
            page_id: Notion page ID
            parser: NotionBlockParser instance
            parent_page_url: Optional parent page URL to use for image blocks without weburl
            comments_by_block: Optional dict of block_id -> List of (comment_dict, block_id) for attaching comments

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
            parent_page_url=parent_page_url,
            parent_page_id=page_id  # Root page ID
        )

        # Post-process blocks: finalize indices, calculate indent, fix numbering, group list items
        parser.post_process_blocks(blocks, block_groups)

        # Convert image blocks to base64 format
        await self._convert_image_blocks_to_base64(blocks, parent_page_url)

        # Attach comments to blocks and create page-level comment BlockGroups
        if comments_by_block:
            await self._attach_comments_to_blocks(blocks, block_groups, comments_by_block, page_id, parent_page_url, parser)

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

        # Step 3: Delegate parsing to NotionBlockParser with callbacks
        # Use unified callbacks: one for records, one for users
        blocks, block_groups = await parser.parse_data_source_to_blocks(
            data_source_metadata=metadata,
            data_source_rows=all_rows,
            data_source_id=data_source_id,
            get_record_child_callback=lambda external_id: self.get_record_child_by_external_id(external_id, data_source_id),
            get_user_child_callback=self.get_user_child_by_external_id
        )

        self.logger.info(f"Fetched data source {data_source_id}: {len(block_groups)} tables, {len(blocks)} rows")

        return BlocksContainer(blocks=blocks, block_groups=block_groups)

    async def _fetch_page_attachments_and_comments(
        self,
        page_id: str,
        page_url: str = ""
    ) -> Tuple[List[FileRecord], Dict[str, List[Tuple[Dict[str, Any], str]]]]:
        """
        Fetch all attachments and comments from a Notion page in a single traversal.

        Uses efficient single-traversal approach:
        - Single recursive pass collects attachment blocks AND all block IDs
        - Then fetches comments for page + collected block IDs
        - Returns comments grouped by block_id for later processing

        Args:
            page_id: Notion page ID
            page_url: Optional page URL (will be fetched if not provided)

        Returns:
            Tuple of (List of FileRecord objects, Dict of block_id -> List of (comment_dict, block_id) tuples)
        """
        try:
            file_records: List[FileRecord] = []

            # Single traversal to collect attachment blocks and all block IDs
            attachment_blocks, all_block_ids = await self._fetch_attachment_blocks_and_block_ids_recursive(page_id)

            # Create file records from attachment blocks
            for block in attachment_blocks:
                file_record = self._transform_to_file_record(block, page_id, page_url)
                if file_record:
                    file_records.append(file_record)

            # Fetch comments for page and all collected block IDs
            all_comments = await self._fetch_comments_for_blocks(page_id, all_block_ids)

            # Group comments by block_id
            comments_by_block: Dict[str, List[Tuple[Dict[str, Any], str]]] = {}
            for comment, block_id in all_comments:
                if block_id not in comments_by_block:
                    comments_by_block[block_id] = []
                comments_by_block[block_id].append((comment, block_id))

            return file_records, comments_by_block

        except Exception as e:
            self.logger.error(f"Failed to fetch attachments and comments for page {page_id}: {e}", exc_info=True)
            return [], {}

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
        - Collects attachment block types: file, video, pdf, audio
        - Collects all block IDs (for comment fetching)
        - Skips: image (as FileRecord), bookmark, embed (parsed as LINK blocks), child_page, child_database, data_source
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
                    self.logger.error(f"Failed to fetch block children for {block_id}: {error_msg}")
                    raise Exception(f"Notion API error while fetching block children for {block_id}: {error_msg}")

                data = response.data.json() if response.data else {}

                if not isinstance(data, dict):
                    self.logger.warning(f"Expected dictionary but got {type(data)} for block {block_id}")
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

                    # Check if this is an attachment block (file, video, pdf, audio)
                    # Note: bookmark and embed are handled by parser as LINK blocks, not FileRecords
                    if block_type in ["file", "video", "pdf", "audio"]:
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
                self.logger.error(f"Error fetching attachment blocks and block IDs for {block_id}: {e}", exc_info=True)
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
                self.logger.error(f"Error fetching comments for block {block_id}: {e}", exc_info=True)
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
        block_id: str,
        parser: 'NotionBlockParser'
    ) -> Optional[str]:
        """
        Fetch a block and extract plain text content from its rich_text fields.

        Args:
            block_id: Notion block ID
            parser: NotionBlockParser instance to use for text extraction

        Returns:
            Plain text string extracted from the block, or None if block has no text content.
        """
        try:
            # Fetch block from Notion API
            datasource = await self._get_fresh_datasource()
            response = await datasource.retrieve_block(block_id=block_id)

            if not response.success:
                self.logger.warning(f"Failed to fetch block {block_id} for text extraction: {response.error}")
                return None

            data = response.data.json() if response.data else {}

            # Extract rich_text using parser utility method
            rich_text = parser.extract_rich_text_from_block_data(data)

            if rich_text:
                # Extract plain text (without markdown formatting)
                return parser.extract_rich_text(rich_text, plain_text=True)

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
                datasource = await self._get_fresh_datasource()
                response = await datasource.retrieve_block_children(
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
        parent_page_id: Optional[str] = None,
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
            parent_page_id: ID of root page (stays same throughout recursion)

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
                self.logger.warning(
                    f"Skipping unsupported block type (id: {notion_block.get('id', 'unknown')}) "
                    f"and its children"
                )
                continue

            # Parse the block
            parsed_block, parsed_group, _ = await parser.parse_block(
                notion_block,
                parent_group_index,
                0,  # Index will be set when appending
                parent_page_url,  # Pass parent page URL
                parent_page_id  # Pass parent page ID for file references
            )

            has_children = notion_block.get("has_children", False)
            block_id = notion_block.get("id", "")
            # Get type_data for synced_block handling
            type_data = notion_block.get(block_type, {}) if block_type else {}

            # Handle parsed block
            if parsed_block:
                block_index = len(blocks)
                parsed_block.index = block_index
                parsed_block.parent_index = parent_group_index
                blocks.append(parsed_block)

                # Fix #1: If block has children, create wrapper BlockGroup to preserve hierarchy
                if has_children and block_id:
                    # Determine appropriate sub-type based on block type
                    sub_type_map = {
                        "callout": GroupSubType.CALLOUT,
                        "quote": GroupSubType.QUOTE,
                    }
                    sub_type = sub_type_map.get(block_type, GroupSubType.NESTED_BLOCK)

                    # Create wrapper BlockGroup for the block with children
                    wrapper_group = BlockGroup(
                        id=str(uuid4()),
                        index=len(block_groups),
                        parent_index=parent_group_index,
                        type=GroupType.TEXT_SECTION,
                        sub_type=sub_type,
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

                    # Special handling for synced_block references
                    # If this is a synced_block reference, fetch children from the original block
                    children_block_id = block_id
                    if block_type == "synced_block":
                        synced_from = type_data.get("synced_from")
                        if synced_from and isinstance(synced_from, dict):
                            if synced_from.get("type") == "block_id":
                                original_block_id = synced_from.get("block_id")
                                if original_block_id:
                                    children_block_id = original_block_id
                                    self.logger.debug(
                                        f"Synced block {block_id} is a reference to {original_block_id}, "
                                        f"fetching children from original block"
                                    )

                    # Process children with wrapper as parent
                    child_indices = await self._process_blocks_recursive(
                        children_block_id,
                        parser,
                        blocks,
                        block_groups,
                        wrapper_group.index,  # Wrapper becomes the parent
                        parent_page_url,  # Pass parent page URL
                        parent_page_id  # Pass parent page ID
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
                    # Special handling for synced_block references
                    # If this is a synced_block reference, fetch children from the original block
                    children_block_id = block_id
                    if block_type == "synced_block":
                        synced_from = type_data.get("synced_from")
                        if synced_from and isinstance(synced_from, dict):
                            if synced_from.get("type") == "block_id":
                                original_block_id = synced_from.get("block_id")
                                if original_block_id:
                                    children_block_id = original_block_id
                                    self.logger.debug(
                                        f"Synced block {block_id} is a reference to {original_block_id}, "
                                        f"fetching children from original block"
                                    )

                    child_indices = await self._process_blocks_recursive(
                        children_block_id,
                        parser,
                        blocks,
                        block_groups,
                        group_index,  # This group becomes the parent for its children
                        parent_page_url,  # Pass parent page URL
                        parent_page_id  # Pass parent page ID
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
                    parent_page_url,  # Pass parent page URL
                    parent_page_id  # Pass parent page ID
                )

        return current_level_indices

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
        - Block.weburl remains as Notion block URL
        - Block.public_data_link is cleared after conversion

        Args:
            blocks: List of Block objects (modified in-place)
            parent_page_url: Optional parent page URL (not used, kept for backwards compatibility)

        Raises:
            Exception: If any image fails to download or convert (includes block ID and URL in message)
        """
        # Filter image blocks with public_data_link (signed URLs from Notion)
        image_blocks = [
            block for block in blocks
            if block.type == BlockType.IMAGE and block.public_data_link is not None
        ]

        if not image_blocks:
            return

        # Initialize ImageParser for SVG conversion
        image_parser = ImageParser(self.logger)

        # Batch fetch images in parallel
        async def fetch_image(block: Block) -> Tuple[Block, Optional[str], Optional[Exception]]:
            """Fetch a single image and return block, base64_data_url, and any error"""
            image_url = str(block.public_data_link)  # Signed URL from Notion
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
        for result in results:
            # Handle case where gather returned an exception directly
            if isinstance(result, Exception):
                raise result

            # Unpack the tuple
            block, base64_data_url, error = result

            if error:
                # Re-raise exception with block ID and URL
                raise error

            if not base64_data_url:
                block_id = block.source_id or block.id
                raise Exception(f"Failed to convert image for block {block_id}: no base64 data returned")

            # Update block data and format
            block.data = {"uri": base64_data_url}
            block.format = DataFormat.BASE64

            # Keep weburl as Notion block URL (already set correctly by parser)
            # Clear public_data_link since image is now embedded as base64
            block.public_data_link = None

    async def _batch_get_or_create_child_records(
        self,
        children_to_resolve: Dict[str, Tuple[str, RecordType, Optional[str]]]
    ) -> Dict[str, ChildRecord]:
        """
        Batch get or create child records for multiple external IDs.

        Uses a single transaction for lookup and a single on_new_records call for creation,
        avoiding race conditions and improving performance.

        Args:
            children_to_resolve: Dict mapping external_id -> (name, record_type, parent_external_id)

        Returns:
            Dict mapping external_id -> ChildRecord
        """
        if not children_to_resolve:
            return {}

        external_ids = list(children_to_resolve.keys())
        child_record_map: Dict[str, ChildRecord] = {}

        # Step 1: Batch lookup all external IDs in one transaction
        existing_records: Dict[str, Record] = {}
        async with self.data_store_provider.transaction() as tx_store:
            for ext_id in external_ids:
                record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_id=ext_id
                )
                if record:
                    existing_records[ext_id] = record

        # Step 2: Build ChildRecord for existing records
        for ext_id, record in existing_records.items():
            self.logger.debug(f"Resolved child: {ext_id} -> {record.id}")
            child_record_map[ext_id] = ChildRecord(
                child_type=ChildType.RECORD,
                child_id=record.id,
                child_name=record.record_name
            )

        # Step 3: Create minimal records for missing ones
        missing_ids = [ext_id for ext_id in external_ids if ext_id not in existing_records]
        if missing_ids:
            records_to_create: List[Tuple[Record, List]] = []

            for ext_id in missing_ids:
                name, record_type, parent_ext_id = children_to_resolve[ext_id]

                if record_type == RecordType.FILE:
                    minimal_record = FileRecord(
                        org_id=self.data_entities_processor.org_id,
                        record_name=name,
                        record_type=record_type,
                        external_record_id=ext_id,
                        external_revision_id="minimal",
                        connector_id=self.connector_id,
                        connector_name=Connectors.NOTION,
                        record_group_type=RecordGroupType.NOTION_WORKSPACE,
                        external_record_group_id=self.workspace_id or "",
                        mime_type=MimeTypes.BIN.value,
                        indexing_status=IndexingStatus.AUTO_INDEX_OFF.value,
                        version=1,
                        origin=OriginTypes.CONNECTOR,
                        inherit_permissions=True,
                        parent_external_record_id=parent_ext_id,
                        is_file=True,
                        size_in_bytes=0,
                        weburl="",
                    )
                else:
                    minimal_record = WebpageRecord(
                        org_id=self.data_entities_processor.org_id,
                        record_name=name,
                        record_type=record_type,
                        external_record_id=ext_id,
                        external_revision_id="minimal",
                        connector_id=self.connector_id,
                        connector_name=Connectors.NOTION,
                        record_group_type=RecordGroupType.NOTION_WORKSPACE,
                        external_record_group_id=self.workspace_id or "",
                        mime_type=MimeTypes.BLOCKS.value,
                        indexing_status=IndexingStatus.AUTO_INDEX_OFF.value,
                        version=1,
                        origin=OriginTypes.CONNECTOR,
                        inherit_permissions=True,
                        parent_external_record_id=parent_ext_id,
                    )

                records_to_create.append((minimal_record, []))
                # Pre-populate mapping (record.id is set before on_new_records)
                child_record_map[ext_id] = ChildRecord(
                    child_type=ChildType.RECORD,
                    child_id=minimal_record.id,
                    child_name=minimal_record.record_name
                )

            # Batch create all missing records in one call
            await self.data_entities_processor.on_new_records(records_to_create)
            self.logger.info(f"Created {len(records_to_create)} minimal placeholder records")

        return child_record_map

    async def _resolve_database_to_data_sources(self, database_id: str) -> List[ChildRecord]:
        """
        Resolve a database to its data_sources and return ChildRecords for each.

        Fetches the database from Notion API, extracts its data_sources array,
        and resolves each data_source to a ChildRecord.

        Args:
            database_id: Notion database ID

        Returns:
            List of ChildRecord objects, one for each data_source in the database
        """
        try:
            # Fetch database from Notion API
            datasource = await self._get_fresh_datasource()
            response = await datasource.retrieve_database(database_id)

            if not response.success or not response.data:
                self.logger.warning(f"Failed to retrieve database {database_id}: {response.error if response else 'No response'}")
                return []

            database_data = response.data.json() if response.data else {}
            data_sources = database_data.get("data_sources", [])

            if not data_sources:
                self.logger.debug(f"Database {database_id} has no data_sources")
                return []

            # Batch resolve all data_sources to ChildRecords
            # Collect data_sources to resolve
            data_sources_to_resolve: Dict[str, Tuple[str, RecordType, Optional[str]]] = {}
            for data_source in data_sources:
                data_source_id = data_source.get("id")
                data_source_name = data_source.get("name", "Untitled Data Source")

                if not data_source_id:
                    continue

                # Use DATASOURCE as the record type for data sources
                data_sources_to_resolve[data_source_id] = (
                    data_source_name,
                    RecordType.DATASOURCE,
                    None  # No parent for data sources
                )

            # Batch resolve/create all data_sources using the same logic as other child records
            if data_sources_to_resolve:
                child_record_map = await self._batch_get_or_create_child_records(data_sources_to_resolve)
                child_records = list(child_record_map.values())
            else:
                child_records = []

            self.logger.info(f"Resolved database {database_id} to {len(child_records)} data_sources")
            return child_records

        except Exception as e:
            self.logger.error(f"Error resolving database {database_id} to data_sources: {e}", exc_info=True)
            return []

    async def _resolve_child_reference_blocks(
        self,
        blocks: List[Block],
        parent_record: Optional[Record] = None
    ) -> None:
        """
        Resolve internal record IDs for child reference blocks.
        Creates real records with minimal info for children that haven't synced yet.
        These records will be automatically updated with full data when they sync.

        Populates table_row_metadata.children_records with ChildRecord objects
        by querying ArangoDB for records with matching external_record_id.
        If not found, creates a minimal record.

        Uses deduplication to prevent race conditions: collects unique child references,
        resolves each once sequentially, then maps back to all blocks.

        Args:
            blocks: List of blocks (modified in-place)
            parent_record: Optional parent record for permission inheritance
        """
        # Filter child reference blocks that need resolution
        child_ref_blocks = [
            block for block in blocks
            if block.sub_type == BlockSubType.CHILD_RECORD
            and block.source_id
            and (not block.table_row_metadata or not block.table_row_metadata.children_records)
        ]

        if not child_ref_blocks:
            return

        # Step 1: Separate database references from other references
        database_blocks: List[Block] = []
        other_blocks: List[Block] = []

        for block in child_ref_blocks:
            reference_type = block.source_type or ""
            if reference_type == "link_to_database":
                database_blocks.append(block)
            else:
                other_blocks.append(block)

        # Step 2: Handle database references - resolve to data_sources
        database_child_records_map: Dict[str, List[ChildRecord]] = {}
        if database_blocks:
            # Collect unique database IDs
            unique_database_ids = set()
            for block in database_blocks:
                database_id = block.source_id
                if database_id:
                    unique_database_ids.add(database_id)

            # Resolve each database to its data_sources
            for database_id in unique_database_ids:
                data_source_child_records = await self._resolve_database_to_data_sources(database_id)
                if data_source_child_records:
                    database_child_records_map[database_id] = data_source_child_records

        # Step 3: Handle non-database references using existing logic
        child_record_map: Dict[str, ChildRecord] = {}
        if other_blocks:
            # Collect unique child references (deduplicate by external_id)
            parent_ext_id = parent_record.external_record_id if parent_record else None
            children_to_resolve: Dict[str, Tuple[str, RecordType, Optional[str]]] = {}
            for block in other_blocks:
                ext_id = block.source_id
                if ext_id and ext_id not in children_to_resolve:
                    # data is now just the text/name string
                    name = block.data if isinstance(block.data, str) else "Untitled"
                    # name field stores child_record_type
                    type_str = block.name or "WEBPAGE"
                    children_to_resolve[ext_id] = (name, RecordType[type_str], parent_ext_id)

            # Batch resolve/create all unique children
            child_record_map = await self._batch_get_or_create_child_records(children_to_resolve)

        # Step 4: Apply mapping to all blocks
        for block in child_ref_blocks:
            ext_id = block.source_id
            reference_type = block.source_type or ""

            if reference_type == "link_to_database":
                # Database reference - use List[ChildRecord] from database mapping
                if ext_id and ext_id in database_child_records_map:
                    if not block.table_row_metadata:
                        block.table_row_metadata = TableRowMetadata()
                    block.table_row_metadata.children_records = database_child_records_map[ext_id]
            else:
                # Non-database reference - use single ChildRecord
                if ext_id and ext_id in child_record_map:
                    if not block.table_row_metadata:
                        block.table_row_metadata = TableRowMetadata()
                    block.table_row_metadata.children_records = [child_record_map[ext_id]]

    async def _resolve_table_row_children(
        self,
        blocks: List[Block],
        parent_data_source_record: Optional[Record] = None
    ) -> None:
        """
        Resolve child records for table rows that have child pages.

        Uses deduplication to prevent race conditions: fetches child pages for all rows,
        collects unique child IDs, resolves each once sequentially, then maps back.
        """
        table_row_blocks = [
            block for block in blocks
            if block.type == BlockType.TABLE_ROW
            and block.source_id  # Row page ID
        ]

        if not table_row_blocks:
            return

        # Step 1: Fetch child pages for each row in parallel (API calls are safe to parallelize)
        # Store as: row_page_id -> list of (child_page_id, child_title)
        row_children_map: Dict[str, List[Tuple[str, str]]] = {}

        async def fetch_row_children(block: Block) -> Tuple[str, List[Tuple[str, str]]]:
            row_page_id = block.source_id
            datasource = await self._get_fresh_datasource()
            response = await datasource.retrieve_block_children(
                block_id=row_page_id,
                page_size=100
            )

            if not response.success:
                return (row_page_id, [])

            data = response.data.json() if response.data else {}
            child_blocks = data.get("results", [])

            child_pages = []
            for b in child_blocks:
                if b.get("type") == "child_page" and not b.get("archived", False):
                    child_id = b.get("id")
                    child_title = b.get("child_page", {}).get("title", "Untitled")
                    if child_id:
                        child_pages.append((child_id, child_title))

            return (row_page_id, child_pages)

        # Fetch all row children in parallel
        results = await asyncio.gather(*[fetch_row_children(block) for block in table_row_blocks])
        for row_page_id, children in results:
            row_children_map[row_page_id] = children

        # Step 2: Collect unique child page IDs across all rows and build batch input
        children_to_resolve: Dict[str, Tuple[str, RecordType, Optional[str]]] = {}
        for row_page_id, children in row_children_map.items():
            for child_id, title in children:
                if child_id not in children_to_resolve:
                    # Note: parent is the row page, not the data source
                    children_to_resolve[child_id] = (title, RecordType.WEBPAGE, row_page_id)

        if not children_to_resolve:
            return

        # Step 3: Batch resolve/create all unique children
        child_record_map = await self._batch_get_or_create_child_records(children_to_resolve)

        # Step 4: Apply mapping back to each block
        for block in table_row_blocks:
            row_page_id = block.source_id
            children = row_children_map.get(row_page_id, [])
            if not children:
                continue

            children_records = [child_record_map[cid] for cid, _ in children if cid in child_record_map]
            if children_records:
                if not block.table_row_metadata:
                    block.table_row_metadata = TableRowMetadata()
                block.table_row_metadata.children_records = children_records

    async def _extract_comment_attachment_file_records(
        self,
        comments_by_block: Dict[str, List[Tuple[Dict[str, Any], str]]],
        page_id: str,
        page_url: Optional[str] = None
    ) -> List[FileRecord]:
        """
        Extract FileRecords from comment attachments for database storage during sync.

        This is called during the sync process to save comment attachment FileRecords.
        The actual BlockComment objects are created later during BlocksContainer streaming.

        Args:
            comments_by_block: Dict of block_id -> List of (comment_dict, block_id) tuples
            page_id: Parent page ID
            page_url: Page URL

        Returns:
            List of FileRecord objects from all comment attachments
        """
        all_file_records: List[FileRecord] = []

        # Process all comments to extract attachment FileRecords
        for block_id, comment_list in comments_by_block.items():
            for comment_dict, _ in comment_list:
                try:
                    comment_id = comment_dict.get("id")
                    if not comment_id:
                        continue

                    # Track seen normalized filenames per comment to prevent duplicates
                    seen_filenames: set[str] = set()

                    # Process attachments
                    attachments = comment_dict.get("attachments", [])
                    for attachment in attachments:
                        try:
                            # Extract filename to check for duplicates
                            file_url = None
                            if "file" in attachment:
                                file_obj = attachment["file"]
                                if isinstance(file_obj, dict):
                                    file_url = file_obj.get("url", "")

                            if file_url:
                                # Extract and normalize filename
                                parsed_url = urlparse(file_url)
                                path = unquote(parsed_url.path)
                                url_filename = path.split("/")[-1] if "/" in path else ""
                                file_name = url_filename if url_filename else (attachment.get("name") or "attachment")
                                normalized_filename = self._normalize_filename_for_id(file_name)

                                # Skip if we've already seen this normalized filename for this comment
                                if normalized_filename in seen_filenames:
                                    self.logger.debug(
                                        f"Skipping duplicate comment attachment: {normalized_filename} "
                                        f"for comment {comment_id}"
                                    )
                                    continue

                                # Create FileRecord
                                file_record = await self._transform_to_comment_file_record(
                                    attachment, comment_id, page_id, page_url
                                )
                                if file_record:
                                    all_file_records.append(file_record)
                                    seen_filenames.add(normalized_filename)
                        except Exception as e:
                            self.logger.warning(f"Failed to create FileRecord from comment attachment: {e}")
                            continue

                except Exception as e:
                    self.logger.error(f"Failed to process comment attachments for block {block_id}: {e}")
                    continue

        return all_file_records

    async def _attach_comments_to_blocks(
        self,
        blocks: List[Block],
        block_groups: List[BlockGroup],
        comments_by_block: Dict[str, List[Tuple[Dict[str, Any], str]]],
        page_id: str,
        page_url: Optional[str],
        parser: 'NotionBlockParser'
    ) -> List[FileRecord]:
        """
        Attach comments to blocks and create COMMENT_THREAD BlockGroups for page-level comments.

        For block-level comments: Groups by thread_id and attaches to Block.comments as List[List[BlockComment]]
        For page-level comments: Creates one COMMENT_THREAD BlockGroup per thread with COMMENT Blocks

        Args:
            blocks: List of Block objects to attach comments to
            block_groups: List of BlockGroup objects (page-level comment groups will be appended)
            comments_by_block: Dict of block_id -> List of (comment_dict, block_id) tuples
            page_id: Parent page ID
            page_url: Page URL for comment weburl construction
            parser: NotionBlockParser instance for text extraction

        Returns:
            List of FileRecord objects from comment attachments
        """
        all_file_records: List[FileRecord] = []

        # Create a map of block source_id -> Block for quick lookup
        block_by_source_id: Dict[str, Block] = {}
        for block in blocks:
            if block.source_id:
                block_by_source_id[block.source_id] = block

        # Fetch block text content for all blocks that have comments
        block_text_map: Dict[str, Optional[str]] = {}
        blocks_with_comments = [bid for bid in comments_by_block if bid != page_id]
        if blocks_with_comments:
            block_text_tasks = [
                self._extract_block_text_content(block_id, parser)
                for block_id in blocks_with_comments
            ]
            block_text_results = await asyncio.gather(*block_text_tasks, return_exceptions=True)
            for block_id, text_or_error in zip(blocks_with_comments, block_text_results):
                if isinstance(text_or_error, Exception):
                    block_text_map[block_id] = None
                else:
                    block_text_map[block_id] = text_or_error

        # Process block-level comments
        for block_id, comment_list in comments_by_block.items():
            if block_id == page_id:
                continue  # Handle page-level comments separately

            # Group comments by thread_id (discussion_id)
            comments_by_thread: Dict[str, List[BlockComment]] = defaultdict(list)

            for comment_dict, _ in comment_list:
                try:
                    block_comment, file_records = await self._create_block_comment_from_notion_comment(
                        notion_comment=comment_dict,
                        page_id=page_id,
                        parser=parser,
                        page_url=page_url,
                        quoted_text=block_text_map.get(block_id)
                    )

                    if block_comment:
                        thread_id = block_comment.thread_id or "default"
                        comments_by_thread[thread_id].append(block_comment)
                        all_file_records.extend(file_records)

                except Exception as e:
                    self.logger.error(f"Failed to create BlockComment for block {block_id}: {e}")
                    continue

            # Attach threaded comments to block
            if block_id in block_by_source_id:
                block = block_by_source_id[block_id]
                # Convert dict to List[List[BlockComment]]
                block.comments = list(comments_by_thread.values())

        # Create COMMENT_THREAD BlockGroups for page-level comments
        page_level_comments = comments_by_block.get(page_id, [])
        if page_level_comments:
            file_records_from_page_comments = await self._create_page_level_comment_groups(
                block_groups, blocks, page_level_comments, page_id, parser, page_url
            )
            all_file_records.extend(file_records_from_page_comments)

        return all_file_records

    async def _resolve_author_name(self, notion_comment: Dict[str, Any]) -> Optional[str]:
        """
        Resolve author name from Notion comment via user lookup.

        Args:
            notion_comment: Raw comment data from Notion API

        Returns:
            Author name or None
        """
        created_by = notion_comment.get("created_by", {})
        author_id = created_by.get("id", "") if isinstance(created_by, dict) else ""

        if not author_id:
            return None

        try:
            user_child = await self.get_user_child_by_external_id(author_id)
            return user_child.child_name if user_child else None
        except Exception:
            return None

    async def _process_comment_attachments(
        self,
        notion_comment: Dict[str, Any],
        comment_id: str,
        page_id: str,
        page_url: Optional[str]
    ) -> Tuple[List[FileRecord], List[CommentAttachment]]:
        """
        Process comment attachments and create FileRecords.

        Args:
            notion_comment: Raw comment data from Notion API
            comment_id: Comment ID
            page_id: Parent page ID
            page_url: Page URL

        Returns:
            Tuple of (List of FileRecord objects, List of CommentAttachment objects)
        """
        file_records: List[FileRecord] = []
        comment_attachments: List[CommentAttachment] = []

        # Track seen normalized filenames per comment to prevent duplicates
        seen_filenames: set[str] = set()

        attachments = notion_comment.get("attachments", [])
        for attachment in attachments:
            try:
                # Extract filename to check for duplicates
                file_url = None
                if "file" in attachment:
                    file_obj = attachment["file"]
                    if isinstance(file_obj, dict):
                        file_url = file_obj.get("url", "")

                if not file_url:
                    continue

                # Extract and normalize filename
                parsed_url = urlparse(file_url)
                path = unquote(parsed_url.path)
                url_filename = path.split("/")[-1] if "/" in path else ""
                file_name = url_filename if url_filename else (attachment.get("name") or "attachment")
                normalized_filename = self._normalize_filename_for_id(file_name)

                # Skip if we've already seen this normalized filename for this comment
                if normalized_filename in seen_filenames:
                    self.logger.debug(
                        f"Skipping duplicate comment attachment: {normalized_filename} "
                        f"for comment {comment_id}"
                    )
                    continue

                # Create FileRecord
                file_record = await self._transform_to_comment_file_record(
                    attachment, comment_id, page_id, page_url
                )
                if file_record:
                    file_records.append(file_record)
                    comment_attachments.append(CommentAttachment(
                        name=file_record.record_name,
                        id=file_record.id
                    ))
                    seen_filenames.add(normalized_filename)
            except Exception as e:
                self.logger.warning(f"Failed to create FileRecord from comment attachment: {e}")
                continue

        return file_records, comment_attachments

    async def _create_block_comment_from_notion_comment(
        self,
        notion_comment: Dict[str, Any],
        page_id: str,
        parser: 'NotionBlockParser',
        page_url: Optional[str] = None,
        quoted_text: Optional[str] = None
    ) -> Tuple[Optional[BlockComment], List[FileRecord]]:
        """
        Create BlockComment from a Notion comment object.

        Handles async operations (user lookup, FileRecord creation) then delegates
        to parser for BlockComment creation.

        Args:
            notion_comment: Raw comment data from Notion API
            page_id: Parent page ID
            parser: NotionBlockParser instance
            page_url: Page URL
            quoted_text: The text that was commented on (for block-level comments)

        Returns:
            Tuple of (BlockComment object or None, List of FileRecord objects from attachments)
        """
        try:
            comment_id = notion_comment.get("id")
            if not comment_id:
                return None, []

            # Async: Resolve author name via user lookup
            author_name = await self._resolve_author_name(notion_comment)

            # Async: Create FileRecords for attachments
            file_records, comment_attachments = await self._process_comment_attachments(
                notion_comment, comment_id, page_id, page_url
            )

            # Sync: Parse to BlockComment (parser handles all parsing logic)
            block_comment = parser.parse_notion_comment_to_block_comment(
                notion_comment=notion_comment,
                author_name=author_name,
                quoted_text=quoted_text,
                comment_attachments=comment_attachments if comment_attachments else None
            )

            return block_comment, file_records

        except Exception as e:
            self.logger.error(f"Error creating BlockComment: {e}")
            return None, []

    async def _create_page_level_comment_groups(
        self,
        block_groups: List[BlockGroup],
        blocks: List[Block],
        page_level_comments: List[Tuple[Dict[str, Any], str]],
        page_id: str,
        parser: 'NotionBlockParser',
        page_url: Optional[str] = None
    ) -> List[FileRecord]:
        """
        Create COMMENT_THREAD BlockGroups for page-level comments.

        Orchestrates async operations and uses parser to create Block/BlockGroup objects.
        One BlockGroup per discussion thread.

        Args:
            block_groups: List to append COMMENT_THREAD BlockGroups to
            blocks: List to append COMMENT Blocks to
            page_level_comments: List of (comment_dict, block_id) tuples for page-level comments
            page_id: Parent page ID
            parser: NotionBlockParser instance
            page_url: Page URL

        Returns:
            List of FileRecord objects from comment attachments
        """
        all_file_records: List[FileRecord] = []

        # Group comments by discussion_id (thread)
        comments_by_thread: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for comment_dict, _ in page_level_comments:
            discussion_id = comment_dict.get("discussion_id", "default")
            comments_by_thread[discussion_id].append(comment_dict)

        # Create one BlockGroup per thread
        for discussion_id, thread_comments in comments_by_thread.items():
            thread_group_indices: List[BlockContainerIndex] = []

            # Calculate thread group index (will be after all comment groups in this thread)
            thread_group_index = len(block_groups) + len(thread_comments)

            # Create COMMENT BlockGroups for each comment in the thread
            for comment_dict in thread_comments:
                try:
                    comment_id = comment_dict.get("id", "")
                    if not comment_id:
                        continue

                    # Async: Create BlockComment with FileRecords
                    block_comment, file_records = await self._create_block_comment_from_notion_comment(
                        notion_comment=comment_dict,
                        page_id=page_id,
                        parser=parser,
                        page_url=page_url,
                        quoted_text=None  # Page-level comments don't have quoted text
                    )

                    if not block_comment:
                        continue

                    all_file_records.extend(file_records)

                    # Calculate comment group index
                    comment_group_index = len(block_groups)

                    # Create CHILD_RECORD blocks for attachments
                    attachment_block_indices: List[BlockContainerIndex] = []
                    if block_comment.attachments:
                        for attachment in block_comment.attachments:
                            # attachment.id is the FileRecord.id (internal DB ID)
                            # We need to find the FileRecord to get its external_record_id
                            file_record = next(
                                (fr for fr in file_records if fr.id == attachment.id),
                                None
                            )
                            if file_record:
                                # Create CHILD_RECORD block for attachment
                                attachment_block = Block(
                                    id=str(uuid4()),
                                    index=len(blocks),
                                    parent_index=comment_group_index,  # Parent is the comment group
                                    type=BlockType.TEXT,
                                    sub_type=BlockSubType.CHILD_RECORD,
                                    format=DataFormat.TXT,
                                    data=file_record.record_name,
                                    source_name=file_record.record_name,
                                    source_id=file_record.external_record_id,  # ca_{comment_id}_{normalized_filename}
                                    source_type="file",
                                    name="FILE",
                                    weburl=file_record.weburl or page_url,
                                )
                                blocks.append(attachment_block)
                                attachment_block_indices.append(BlockContainerIndex(block_index=attachment_block.index))

                    # Sync: Create COMMENT BlockGroup (parser)
                    comment_group = parser.create_comment_group(
                        block_comment=block_comment,
                        group_index=comment_group_index,
                        parent_group_index=thread_group_index,  # Parent is the thread group
                        source_id=comment_id,
                        attachment_block_indices=attachment_block_indices if attachment_block_indices else None
                    )

                    # Orchestration: Add to list
                    block_groups.append(comment_group)
                    thread_group_indices.append(BlockContainerIndex(block_group_index=comment_group.index))

                except Exception as e:
                    self.logger.error(f"Failed to create COMMENT BlockGroup for page-level comment: {e}")
                    continue

            # Sync: Create COMMENT_THREAD BlockGroup (parser)
            if thread_group_indices:
                thread_group = parser.create_comment_thread_group(
                    discussion_id=discussion_id,
                    group_index=len(block_groups),
                    comment_group_indices=thread_group_indices
                )

                # Orchestration: Add to list
                block_groups.append(thread_group)

        return all_file_records

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
                record_type = RecordType.DATABASE
            elif object_type == "data_source":
                # Data Source: title is directly in the response (same as database)
                title_parts = obj_data.get("title", [])
                title = "".join([t.get("plain_text", "") for t in title_parts]) or "Untitled Data Source"
                record_type = RecordType.DATASOURCE
            else:  # page
                # Page: title is in properties
                title = self._extract_page_title(obj_data)
                record_type = RecordType.WEBPAGE

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

    def _transform_to_file_record(
        self,
        notion_block: Dict[str, Any],
        page_id: str,
        page_url: str = ""
    ) -> Optional[FileRecord]:
        """
        Create FileRecord from a Notion attachment block.

        Strategy:
        - Downloadable/streamable content → FileRecord created
          * Images (Notion-hosted or external URLs)
          * Files/PDFs (Notion-hosted or external)
          * Notion-hosted video/audio
        - Non-downloadable embeds → Skip (become LINK blocks)
          * External video (YouTube, Vimeo, etc.)
          * External audio embeds

        Args:
            notion_block: Raw block data from Notion API
            page_id: Parent page ID
            page_url: Parent page URL

        Returns:
            FileRecord object or None if block should be skipped or creation fails
        """
        try:
            block_id = notion_block.get("id")
            block_type = notion_block.get("type", "")

            if not block_id:
                return None

            # Skip bookmark and embed blocks - they're handled as LINK blocks by the parser
            if block_type in ["bookmark", "embed"]:
                self.logger.debug(f"Skipping {block_type} block {block_id} - handled as LINK block by parser")
                return None

            # Extract file info based on block type
            type_data = notion_block.get(block_type, {})
            is_external = type_data.get("type") == "external"

            # Skip external video/audio if they're embed platforms (not direct file URLs)
            if is_external and block_type in ("video", "audio"):
                if "external" in type_data and isinstance(type_data["external"], dict):
                    check_url = type_data["external"].get("url", "")
                    if self._is_embed_platform_url(check_url):
                        self.logger.debug(f"Skipping {block_type} embed platform {block_id} (YouTube/Vimeo etc.)")
                        return None

            # Validate block type is supported
            supported_types = ["image", "file", "video", "audio", "pdf"]
            if block_type not in supported_types:
                self.logger.debug(f"Unsupported file block type: {block_type}")
                return None

            # Extract file URL (same logic for all types: try "file" then "external")
            file_url = None
            for source_key in ["file", "external"]:
                if source_key in type_data:
                    source_obj = type_data[source_key]
                    if isinstance(source_obj, dict):
                        file_url = source_obj.get("url", "")
                        if file_url:
                            break

            if not file_url:
                return None

            # Extract or generate file name
            file_name = type_data.get("name", "")
            if not file_name:
                if block_type == "pdf":
                    file_name = "document.pdf"  # Default for PDFs
                else:
                    # Extract from URL
                    file_name = file_url.split("/")[-1].split("?")[0]
                    if not file_name:
                        file_name = f"attachment_{block_id[:8]}"

            # Determine extension and MIME type
            extension = file_name.split(".")[-1].lower() if "." in file_name else None

            # Try to guess MIME type from filename, with fallbacks per block type
            mime_type, _ = mimetypes.guess_type(file_name)
            if not mime_type:
                mime_type_defaults = {
                    "image": MimeTypes.PNG.value,
                    "pdf": MimeTypes.PDF.value,
                    "video": "video/mp4",
                    "audio": "audio/mpeg",
                }
                mime_type = mime_type_defaults.get(block_type, MimeTypes.UNKNOWN.value)

            file_id = block_id

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
                parent_record_type=RecordType.WEBPAGE,
                parent_external_record_id=page_id,
                record_group_type=RecordGroupType.NOTION_WORKSPACE,
                external_record_group_id=self.workspace_id or "",
                version=1,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.NOTION,
                connector_id=self.connector_id,
                mime_type=mime_type,
                signed_url=file_url,
                weburl=page_url or "",
                is_file=True,
                extension=extension,
                size_in_bytes=0,  # Notion doesn't provide file size in block data
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
            )

        except Exception as e:
            self.logger.error(f"Failed to create file record from block {notion_block.get('id')}: {e}")
            return None

    async def _transform_to_comment_file_record(
        self,
        attachment: Dict[str, Any],
        comment_id: str,
        page_id: str,
        page_url: Optional[str] = None
    ) -> Optional[FileRecord]:
        """
        Create FileRecord from a comment attachment.

        Args:
            attachment: Attachment dict from Notion comment
            comment_id: Comment ID
            page_id: Parent page ID
            page_url: Page URL

        Returns:
            FileRecord object or None
        """
        try:
            # Extract file URL and name
            file_url = None
            file_name = None

            if "file" in attachment:
                file_obj = attachment["file"]
                if isinstance(file_obj, dict):
                    file_url = file_obj.get("url", "")

            if not file_url:
                return None

            # Extract filename from URL
            parsed_url = urlparse(file_url)
            path = unquote(parsed_url.path)
            url_filename = path.split("/")[-1] if "/" in path else ""
            file_name = url_filename if url_filename else (attachment.get("name") or "attachment")

            # Normalize filename for external_record_id
            normalized_filename = self._normalize_filename_for_id(file_name)

            # Determine MIME type
            category = attachment.get("category", "")
            mime_type = MimeTypes.BIN.value

            if category:
                category_mime_map = {
                    "productivity": MimeTypes.BIN.value,
                    "image": MimeTypes.PNG.value,
                    "video": "video/mp4",
                    "audio": "audio/mpeg",
                }
                mime_type = category_mime_map.get(category, MimeTypes.BIN.value)
            else:
                guessed_type, _ = mimetypes.guess_type(file_url)
                if guessed_type:
                    mime_type = guessed_type

            # Extract extension
            extension = ""
            if file_name and "." in file_name:
                extension = file_name.split(".")[-1]

            # Use normalized filename in external_record_id format: ca_{comment_id}_{normalized_filename}
            file_id = f"ca_{comment_id}_{normalized_filename}"

            file_record = FileRecord(
                org_id=self.data_entities_processor.org_id,
                record_name=file_name,
                record_type=RecordType.FILE,
                external_record_id=file_id,
                parent_record_type=RecordType.WEBPAGE,
                parent_external_record_id=page_id,
                record_group_type=RecordGroupType.NOTION_WORKSPACE,
                external_record_group_id=self.workspace_id or "",
                version=1,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.NOTION,
                connector_id=self.connector_id,
                mime_type=mime_type,
                signed_url=file_url,
                weburl=page_url or "",
                is_file=True,
                extension=extension,
                size_in_bytes=0,
            )

            return file_record

        except Exception as e:
            self.logger.error(f"Error creating FileRecord from comment attachment: {e}")
            return None

    def _normalize_filename_for_id(self, filename: str) -> str:
        """
        Normalize filename for use in external_record_id.
        - URL decode the filename
        - Remove/replace invalid characters
        - Trim whitespace
        - Handle empty filenames

        Args:
            filename: Original filename (may be URL-encoded)

        Returns:
            Normalized filename safe for use in external_record_id
        """
        if not filename:
            return "attachment"

        # URL decode the filename
        normalized = unquote(filename)

        # Remove/replace invalid characters: /, \, :, *, ?, ", <, >, |
        invalid_chars = ['/', '\\', ':', '*', '?', '"', '<', '>', '|']
        for char in invalid_chars:
            normalized = normalized.replace(char, '_')

        # Trim whitespace
        normalized = normalized.strip()

        # Handle empty after normalization
        if not normalized:
            return "attachment"

        return normalized

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

    async def resolve_page_title_by_id(self, page_id: str) -> Optional[str]:
        """
        Resolve a Notion page ID to its title.

        Fetches the page from Notion API and extracts the title.
        Can also check ArangoDB for existing record first.

        Args:
            page_id: Notion page ID

        Returns:
            Page title string, or None if not found
        """
        try:
            # First check if we have the record in ArangoDB
            async with self.data_store_provider.transaction() as tx_store:
                record = await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_id=page_id
                )
                if record and record.record_name:
                    return record.record_name

            # If not in DB, fetch from Notion API
            datasource = await self._get_fresh_datasource()
            response = await datasource.retrieve_page(page_id)

            if response.success and response.data:
                page_data = response.data.json()
                return self._extract_page_title(page_data)

            return None
        except Exception as e:
            self.logger.warning(f"Failed to resolve page title for {page_id}: {e}")
            return None

    async def resolve_user_name_by_id(self, user_id: str) -> Optional[str]:
        """
        Resolve a Notion user ID to the user's name.

        Fetches the user from Notion API and extracts name/email.

        Args:
            user_id: Notion user ID

        Returns:
            User name (or email if name not available), or None if not found
        """
        try:
            datasource = await self._get_fresh_datasource()
            response = await datasource.retrieve_user(user_id)

            if response.success and response.data:
                user_data = response.data.json()
                user_obj = user_data.get("object")

                if user_obj == "user":
                    user_type = user_data.get("type", "")

                    if user_type == "person":
                        # Person user - get name or email
                        person = user_data.get("person", {})
                        name = user_data.get("name", "")
                        if name:
                            return name
                        # Fallback to email if available
                        email = person.get("email", "")
                        if email:
                            return email
                    elif user_type == "bot":
                        # Bot user - get name
                        bot = user_data.get("bot", {})
                        name = user_data.get("name", "")
                        if name:
                            return name
                        # Fallback to owner info
                        owner = bot.get("owner", {})
                        if owner.get("type") == "user":
                            return owner.get("user", {}).get("name", "Bot")

            return None
        except Exception as e:
            self.logger.warning(f"Failed to resolve user name for {user_id}: {e}")
            return None

    async def get_record_by_external_id(self, external_id: str) -> Optional[Record]:
        """
        Get record by external ID from ArangoDB.

        Args:
            external_id: Notion external record ID

        Returns:
            Record object if found, None otherwise
        """
        try:
            async with self.data_store_provider.transaction() as tx_store:
                return await tx_store.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_id=external_id
                )
        except Exception as e:
            self.logger.warning(f"Failed to get record for {external_id}: {e}")
            return None

    async def get_record_child_by_external_id(
        self,
        external_id: str,
        parent_data_source_id: Optional[str] = None
    ) -> Optional[ChildRecord]:
        """
        Get or create ChildRecord for a record (page/datasource) by external ID.

        Combines record lookup, title resolution, and ChildRecord creation.

        Args:
            external_id: Notion page/datasource ID
            parent_data_source_id: Optional parent data source ID (for creating temporary records)

        Returns:
            ChildRecord object if found/created, None otherwise
        """
        try:
            # Query for existing record by external ID
            record = await self.get_record_by_external_id(external_id)

            if record:
                # Existing record found - return ChildRecord
                return ChildRecord(
                    child_type=ChildType.RECORD,
                    child_id=record.id,
                    child_name=record.record_name
                )

            # Record doesn't exist - resolve title and create temporary record if parent provided
            page_title = await self.resolve_page_title_by_id(external_id)

            if parent_data_source_id:
                # Create temporary record for row pages
                self.logger.debug(f"⚠️ Record not found: {external_id}, creating temporary record")

                minimal_record = WebpageRecord(
                    org_id=self.data_entities_processor.org_id,
                    record_name=page_title or "Database Row",
                    record_type=RecordType.WEBPAGE,
                    external_record_id=external_id,
                    external_revision_id="temporary",
                    connector_id=self.connector_id,
                    connector_name=Connectors.NOTION,
                    record_group_type=RecordGroupType.NOTION_WORKSPACE,
                    external_record_group_id=self.workspace_id or "",
                    mime_type=MimeTypes.BLOCKS.value,
                    indexing_status=IndexingStatus.AUTO_INDEX_OFF.value,
                    version=1,
                    origin=OriginTypes.CONNECTOR,
                    inherit_permissions=True,
                    parent_external_record_id=parent_data_source_id,
                )

                await self.data_entities_processor.on_new_records([(minimal_record, [])])

                self.logger.info(f"✨ Created temporary record: {external_id} -> {minimal_record.id}")

                return ChildRecord(
                    child_type=ChildType.RECORD,
                    child_id=minimal_record.id,
                    child_name=minimal_record.record_name
                )
            else:
                # For relation pages without parent, just return ChildRecord with title
                return ChildRecord(
                    child_type=ChildType.RECORD,
                    child_id=external_id,
                    child_name=page_title or f"Page {external_id[:8]}"
                )

        except Exception as e:
            self.logger.error(f"Error getting record child for {external_id}: {e}")
            return None

    async def get_user_child_by_external_id(self, user_id: str) -> Optional[ChildRecord]:
        """
        Get ChildRecord for a user by external ID (Notion user ID).

        Queries the database to get the user's database ID and name.
        Falls back to resolving name from Notion API if user not in DB.

        Args:
            user_id: Notion user ID (external/source user ID)

        Returns:
            ChildRecord object with database user ID if found, None otherwise
        """
        try:
            # Query user from database by source_user_id
            async with self.data_store_provider.transaction() as tx_store:
                user = await tx_store.get_user_by_source_id(
                    source_user_id=user_id,
                    connector_id=self.connector_id
                )

            if user:
                # User found in database - use database user ID
                self.logger.debug(f"✅ Found user in DB: {user_id} -> {user.id}")
                return ChildRecord(
                    child_type=ChildType.USER,
                    child_id=user.id,  # Database user ID
                    child_name=user.full_name or user.email or f"User {user_id[:8]}",
                )

            # User not in database - resolve name from Notion API
            user_name = await self.resolve_user_name_by_id(user_id)

            # Return ChildRecord with external ID as fallback
            # Note: This user should eventually sync via user sync process
            self.logger.debug(f"⚠️ User not in DB yet: {user_id}, using external ID")
            return ChildRecord(
                child_type=ChildType.USER,
                child_id=user_id,  # External user ID as fallback
                child_name=user_name or f"User {user_id[:8]}",
            )

        except Exception as e:
            self.logger.error(f"Error getting user child for {user_id}: {e}")
            return None

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

    def _is_embed_platform_url(self, url: Optional[str]) -> bool:
        """
        Check if a URL is from an embed platform (YouTube, Vimeo, etc.) vs. a direct file URL.

        Embed platforms require special player/iframe and can't be directly downloaded/streamed.
        Direct file URLs (*.mp4, *.mp3, etc.) can be downloaded and streamed.

        Args:
            url: The URL to check

        Returns:
            True if it's an embed platform URL, False if it's a direct file URL
        """
        if not url:
            return False

        url_lower = url.lower()

        # Common embed platforms
        embed_domains = [
            'youtube.com', 'youtu.be',
            'vimeo.com',
            'soundcloud.com',
            'spotify.com',
            'dailymotion.com',
            'twitch.tv',
            'tiktok.com',
            'facebook.com/watch',
            'instagram.com',
        ]

        # Check if URL contains any embed platform domain
        for domain in embed_domains:
            if domain in url_lower:
                return True

        # Check if URL has a direct file extension (not an embed)
        video_extensions = ['.mp4', '.webm', '.ogg', '.mov', '.avi', '.mkv', '.flv', '.wmv', '.m4v']
        audio_extensions = ['.mp3', '.wav', '.ogg', '.m4a', '.flac', '.aac', '.wma', '.opus']

        for ext in video_extensions + audio_extensions:
            if url_lower.endswith(ext) or f'{ext}?' in url_lower:
                return False  # Direct file URL

        # If we can't determine, assume it's an embed (safer default)
        # This prevents trying to download platform pages
        return True

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
