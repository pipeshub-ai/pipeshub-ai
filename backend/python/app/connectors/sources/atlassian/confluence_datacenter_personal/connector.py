"""
Confluence Data Center Personal Connector

Single-user sync without permission APIs. Inherits from BaseConnector directly.

Authentication: API token (personal access token or HTTP basic with API token).
"""

import uuid
import re
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from logging import Logger
from typing import Any, Literal, Optional
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.constants import IconPaths
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
from app.connectors.core.registry.auth_builder import AuthBuilder, AuthType
from app.connectors.core.registry.connector_builder import (
    AuthField,
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
    DocumentationLink,
    SyncStrategy,
)
from app.connectors.core.constants import CONNECTOR_EMAIL_IDENTITY_INFO
from app.connectors.core.registry.filters import (
    FilterCategory,
    FilterCollection,
    FilterField,
    FilterOperator,
    FilterOption,
    FilterOptionsResponse,
    FilterType,
    IndexingFilterKey,
    OptionSourceType,
    SyncFilterKey,
    load_connector_filters,
)
from app.connectors.sources.atlassian.core.apps import ConfluenceDataCenterPersonalApp
from app.connectors.sources.atlassian.core.confluence_html import prepare_streaming_html
from app.sources.client.http.http_retry import call_with_retry
from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
from app.models.entities import (
    CommentRecord,
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    WebpageRecord,
)
from app.models.permission import Permission, PermissionType
from app.sources.client.confluence.confluence import (
    ConfluenceClient as ExternalConfluenceClient,
)
from app.sources.external.confluence.confluence import ConfluenceDataSource
from app.utils.streaming import create_stream_record_response

# Time offset (in hours) applied to date filters to handle timezone differences
# between the application and Confluence server, ensuring no data is missed during sync
TIME_OFFSET_HOURS = 24

def _extract_item_last_modified_when(item_data: dict[str, Any]) -> Optional[str]:
    """Extract last modified timestamp from Confluence item data.
    
    Tries history.lastUpdated.when first, then falls back to version.when or version.createdAt.
    """
    history = item_data.get("history")
    if isinstance(history, dict):
        last_updated = history.get("lastUpdated")
        if isinstance(last_updated, dict):
            when = last_updated.get("when")
            if when:
                return when
    version = item_data.get("version")
    if isinstance(version, dict):
        return version.get("when") or version.get("createdAt")
    return None

# Expand parameters for fetching pages and blogposts with required metadata
# Includes: ancestors, history, space, attachments, and comments
CONTENT_EXPAND_PARAMS = (
    "ancestors,"
    "history.lastUpdated,"
    "space,"
    "children.attachment,"
    "children.attachment.history.lastUpdated,"
    "children.attachment.version,"
    "childTypes.comment"
)

# Single-item v1 GET /content/{id} expand (streaming + reindex)
CONTENT_V1_SINGLE_EXPAND = (
    "body.export_view,version,history,space,ancestors"
)

CONTENT_V1_COMMENT_EXPAND = (
    "body.storage,body.export_view,version,history.lastUpdated,extensions.inlineProperties"
)
CONTENT_V1_ATTACHMENT_EXPAND = "version,history,metadata,extensions"

@ConnectorBuilder("Confluence Data Center Personal")\
    .in_group("Atlassian")\
    .with_description("Sync pages, spaces visible to your account into your personal workspace")\
    .with_categories(["Knowledge Management", "Collaboration"])\
    .with_scopes([ConnectorScope.PERSONAL.value])\
    .with_auth([
        AuthBuilder.type(AuthType.API_TOKEN).fields([
            AuthField(
                name="baseUrl",
                display_name="Base URL",
                placeholder="https://confluence.yourcompany.com",
                description="The base URL of your Confluence Data Center instance",
                field_type="URL",
                required=True,
                max_length=2000,
                is_secret=False,
            ),
            AuthField(
                name="apiToken",
                display_name="Personal Access Token",
                placeholder="your-personal-access-token",
                description="Confluence Data Center personal access token (PAT)",
                field_type="PASSWORD",
                required=True,
                max_length=2000,
                is_secret=True,
            ),
        ]),
        AuthBuilder.type(AuthType.BASIC_AUTH).fields([
            AuthField(
                name="baseUrl",
                display_name="Base URL",
                placeholder="https://confluence.yourcompany.com",
                description="The base URL of your Confluence Data Center instance",
                field_type="URL",
                required=True,
                max_length=2000,
                is_secret=False,
            ),
            AuthField(
                name="username",
                display_name="Username",
                placeholder="your-username",
                description="Confluence username for basic authentication",
                field_type="TEXT",
                required=True,
                max_length=500,
                is_secret=False,
            ),
            AuthField(
                name="password",
                display_name="Password",
                placeholder="your-password",
                description="Confluence password for basic authentication",
                field_type="PASSWORD",
                required=True,
                max_length=2000,
                is_secret=True,
            ),
        ])
    ])\
    .with_info(
        "Syncs only spaces and pages visible to the credentials you provide. "
        "Access is limited to you — the user who created this connector — without "
        "reading Confluence permission schemes or other users."
        + "\n\n"
        + CONNECTOR_EMAIL_IDENTITY_INFO
    )\
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.CONFLUENCE_DATA_CENTER_PERSONAL.value))
        .with_realtime_support(False)
        .add_documentation_link(DocumentationLink(
            "Personal access tokens (Confluence Server / Data Center)",
            "https://confluence.atlassian.com/doc/using-personal-access-tokens-1007111435.html",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/confluence/confluence',
            'pipeshub'
        ))
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_agent_support(False)
        .add_filter_field(FilterField(
            name="space_keys",
            display_name="Space Name",
            description="Filter pages and blogposts by space name",
            filter_type=FilterType.LIST,
            category=FilterCategory.SYNC,
            option_source_type=OptionSourceType.DYNAMIC
        ))
        .add_filter_field(FilterField(
            name="page_ids",
            display_name="Page Name",
            description="Filter specific pages by their name.",
            filter_type=FilterType.LIST,
            category=FilterCategory.SYNC,
            option_source_type=OptionSourceType.DYNAMIC
        ))
        .add_filter_field(FilterField(
            name="blogpost_ids",
            display_name="Blogpost Name",
            description="Filter specific blogposts by their name.",
            filter_type=FilterType.LIST,
            category=FilterCategory.SYNC,
            option_source_type=OptionSourceType.DYNAMIC
        ))
        .add_filter_field(CommonFields.modified_date_filter("Filter pages and blogposts by modification date."))
        .add_filter_field(CommonFields.created_date_filter("Filter pages and blogposts by creation date."))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        # Indexing filters - Pages
        .add_filter_field(FilterField(
            name="pages",
            display_name="Index Pages",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of pages",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="page_attachments",
            display_name="Index Page Attachments",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of page attachments",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="page_comments",
            display_name="Index Page Comments",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of page comments",
            default_value=True
        ))
        # Indexing filters - Blogposts
        .add_filter_field(FilterField(
            name="blogposts",
            display_name="Index Blogposts",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of blogposts",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="blogpost_attachments",
            display_name="Index Blogpost Attachments",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of blogpost attachments",
            default_value=True
        ))
        .add_filter_field(FilterField(
            name="blogpost_comments",
            display_name="Index Blogpost Comments",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of blogpost comments",
            default_value=True
        ))
    )\
    .build_decorator()
class ConfluenceDataCenterPersonalConnector(BaseConnector):
    """Personal Confluence DC: creator-only permissions, no user/group/permission API calls."""

    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        scope: str,
        created_by: str,
    ) -> None:
        """Initialize the Confluence Data Center connector."""
        super().__init__(
            ConfluenceDataCenterPersonalApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )

        # Client instances
        self.external_client: Optional[ExternalConfluenceClient] = None
        self.data_source: Optional[ConfluenceDataSource] = None
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

        # Cache for server version (fetched once during first space sync)
        self._server_version: Optional[tuple[int, ...]] = None

    async def init(self) -> bool:
        """Initialize the Confluence Data Center connector with credentials and client.

        Base URL origin and usage
        -------------------------
        The user supplies the Confluence instance root URL (``baseUrl``) in the
        connector's CONFIGURE form, e.g. ``https://confluence.example.com``.

        ``ExternalConfluenceClient.build_from_services`` stores it as ``self.base_url``
        without modification for DC auth types (PAT and BASIC_AUTH) — i.e. plain host,
        no ``/wiki/api/v2`` suffix. (For Cloud Basic-with-email the same builder does
        append ``/wiki/api/v2``; this connector exposes only DC-shaped auth fields.)

        All endpoint paths are resolved through
        ``ConfluenceDataSource._v1_rest_api_base()``, which handles all three URL
        shapes (DC plain host, Cloud ``/wiki``, Cloud ``/wiki/api/v2``) and produces
        the correct ``/rest/api`` base.
        """
        try:
            self.logger.info("🔧 Initializing Confluence Data Center connector...")

            # Build client from services (handles config loading, base URL, API token internally)
            self.external_client = await ExternalConfluenceClient.build_from_services(
                logger=self.logger,
                config_service=self.config_service,
                connector_instance_id=self.connector_id
            )

            # Initialize data source
            self.data_source = ConfluenceDataSource(self.external_client)

            self.logger.info("✅ Confluence Data Center connector initialized successfully")
            return True

        except Exception as e:
            self.logger.error(f"❌ Failed to initialize Confluence Data Center connector: {e}", exc_info=True)
            return False

    async def _get_fresh_datasource(self) -> ConfluenceDataSource:
        """
        Return a ConfluenceDataSource backed by the configured client.

        This connector supports API token auth only; credentials are fixed at init time.
        """
        if not self.external_client:
            raise Exception("Confluence client not initialized. Call init() first.")

        return ConfluenceDataSource(self.external_client)

    async def test_connection_and_access(self) -> bool:
        """Test connection and access to Confluence API."""
        try:
            if not self.external_client:
                self.logger.error("External client not initialized")
                return False

            # Test by fetching spaces with a limit of 1 (v1 REST)
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_spaces_v1(limit=1, expand="history")

            if not response or response.status != HttpStatusCode.SUCCESS.value:
                self.logger.error(f"Connection test failed with status: {response.status if response else 'No response'}")
                return False

            self.logger.info("✅ Confluence connector connection test passed")
            return True

        except Exception as e:
            self.logger.error(f"Connection test failed: {e}", exc_info=True)
            return False

    async def run_sync(self) -> None:
        """
        Run simplified sync for Confluence DC Personal connector.
        
        No user/group sync, no permission APIs. Uses ConnectorGroup pattern.
        
        Sync order:
        1. Ensure ConnectorGroup permission (creator-only access)
        2. Spaces (with ConnectorGroup permission)
        3. Pages per space (inherit from space)
        4. Blogposts per space (inherit from space)
        """
        try:
            org_id = self.data_entities_processor.org_id
            self.logger.info(
                f"▶️ Confluence DC Personal connector {self.connector_id}: run_sync starting for org {org_id}"
            )

            # Ensure client is initialized
            if not self.external_client or not self.data_source:
                if not await self.init():
                    raise RuntimeError(
                        f"Confluence Data Center Personal connector {self.connector_id} init failed"
                    )

            # Force fresh ConnectorGroup permission on each run (Jira DC Personal pattern)
            self._connector_group_permission = None

            # Resolve creator email
            if not self.creator_email and self.created_by:
                try:
                    creator = await self.data_entities_processor.get_user_by_user_id(
                        self.created_by
                    )
                    if creator and getattr(creator, "email", None):
                        self.creator_email = creator.email
                except Exception as e:
                    self.logger.warning(
                        "Confluence DC Personal connector %s: could not resolve creator "
                        "email for created_by %s: %s",
                        self.connector_id,
                        self.created_by,
                        e,
                    )

            if not self.creator_email:
                self.logger.warning(
                    "Confluence DC Personal connector %s: no creator email — "
                    "spaces will sync without user permissions",
                    self.connector_id,
                )
            else:
                self.logger.info(
                    "Confluence DC Personal connector %s: creator_email=%s",
                    self.connector_id,
                    self.creator_email,
                )

            # Ensure ConnectorGroup permission
            group_permission = await self.ensure_connector_group_permission()
            self.logger.info(
                "Confluence DC Personal connector %s: connector group permission ready (granted=%s)",
                self.connector_id,
                bool(group_permission),
            )

            # Load sync and indexing filters
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service,
                "confluencedatacenter",
                self.connector_id,
                self.logger,
            )
            self.logger.info(
                "Confluence DC Personal connector %s: filters loaded (sync_keys=%s, indexing_keys=%s)",
                self.connector_id,
                list((self.sync_filters or {}).keys()),
                list((self.indexing_filters or {}).keys()),
            )

            # Sync spaces (simplified - no permission API calls)
            spaces = await self._sync_spaces()
            self.logger.info(
                "Confluence DC Personal connector %s: %s space record groups prepared for sync",
                self.connector_id,
                len(spaces),
            )

            # Sync content per space
            for space in spaces:
                space_key = space.short_name

                # Pages
                self.logger.info(f"Syncing pages for space: {space.name} ({space_key})")
                await self._sync_content(space_key, RecordType.CONFLUENCE_PAGE)

                # Blogposts
                self.logger.info(f"Syncing blogposts for space: {space.name} ({space_key})")
                await self._sync_content(space_key, RecordType.CONFLUENCE_BLOGPOST)

            self.logger.info(
                "✅ Confluence DC Personal connector %s sync completed",
                self.connector_id,
            )

        except Exception as e:
            self.logger.error(
                "Error during Confluence DC Personal sync: %s",
                e,
                exc_info=True
            )
            raise

    async def _sync_spaces(self) -> list[RecordGroup]:
        """
        Sync spaces from Confluence - personal connector uses ConnectorGroup permission.
        
        Steps:
        1. Fetch all spaces with pagination
        2. Apply exclusion filters if NOT_IN operator is used
        3. Grant ConnectorGroup READ to all spaces (no permission API calls)
        4. Create RecordGroup with ConnectorGroup Permission
        """
        try:
            self.logger.info("Starting space synchronization...")

            # Get sync filter values for API
            space_keys_filter = self.sync_filters.get(SyncFilterKey.SPACE_KEYS)
            included_space_keys = None
            excluded_space_keys = None

            # Determine filter mode
            if space_keys_filter is not None:
                filter_operator = space_keys_filter.get_operator()
                if filter_operator == FilterOperator.IN:
                    included_space_keys = space_keys_filter.get_value()
                    self.logger.info(f"Filtering to include space keys: {included_space_keys}")
                elif filter_operator == FilterOperator.NOT_IN:
                    excluded_space_keys = space_keys_filter.get_value()
                    self.logger.info(f"Filtering to exclude space keys: {excluded_space_keys}")

            # Get ConnectorGroup permission (Jira DC Personal pattern)
            group_permission = self._connector_group_permission
            if group_permission is None:
                # Idempotent — returns the cached permission if already created upstream
                group_permission = await self.ensure_connector_group_permission()
            space_permissions: list[Permission] = (
                [group_permission] if group_permission else []
            )

            # Pagination: v1 REST uses start/limit
            batch_size = 25
            start_offset = 0
            total_spaces_synced = 0
            base_url = None  # Extract from first response
            record_groups = []

            while True:
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_spaces_v1(
                    limit=batch_size,
                    start=start_offset,
                    keys=included_space_keys,
                    expand="history",  # NO "permissions" expand - personal connector
                    status="current",
                )

                # Check response
                if not response or response.status != HttpStatusCode.SUCCESS.value:
                    self.logger.error(f"❌ Failed to fetch spaces: {response.status if response else 'No response'}")
                    break

                response_data = response.json()
                spaces_data = response_data.get("results", [])

                # Extract base URL from first response
                if not base_url and response_data.get("_links", {}).get("base"):
                    base_url = response_data["_links"]["base"]
                    self.logger.debug(f"Base URL extracted: {base_url}")

                if not spaces_data:
                    break

                # Apply client-side exclusion filter if NOT_IN
                if excluded_space_keys:
                    original_count = len(spaces_data)
                    spaces_data = [
                        space for space in spaces_data
                        if space.get("key") not in excluded_space_keys
                    ]
                    filtered_count = original_count - len(spaces_data)
                    if filtered_count > 0:
                        self.logger.debug(f"Filtered out {filtered_count} excluded spaces from batch")

                # Process each space
                batch_groups_with_permissions = []
                for space_data in spaces_data:
                    try:
                        space_id = space_data.get("id")
                        space_name = space_data.get("name")
                        space_key = space_data.get("key")

                        if not space_id or not space_name or not space_key:
                            continue

                        self.logger.debug(f"Processing space: {space_name} ({space_id})")

                        # Create RecordGroup for space
                        record_group = self._transform_to_space_record_group(space_data, base_url)
                        if not record_group:
                            continue

                        # Grant ConnectorGroup permission (Jira DC Personal pattern)
                        batch_groups_with_permissions.append((record_group, list(space_permissions)))
                        record_groups.append(record_group)
                        total_spaces_synced += 1

                        if space_permissions:
                            self.logger.debug(
                                f"Space {space_name}: granted access via ConnectorGroup"
                            )

                    except Exception as space_error:
                        self.logger.error(f"❌ Failed to process space {space_data.get('name')}: {space_error}")
                        continue

                # Save batch to database
                if batch_groups_with_permissions:
                    await self.data_entities_processor.on_new_record_groups(batch_groups_with_permissions)
                    self.logger.info(f"Synced batch of {len(batch_groups_with_permissions)} spaces")

                # Next page: prefer _links.next (cursor or start), else bump start by batch size
                next_url = response_data.get("_links", {}).get("next")
                token = self._pagination_token_from_next_link(next_url)
                if token is None:
                    if len(spaces_data) < batch_size:
                        break
                    start_offset += batch_size
                else:
                    try:
                        start_offset = int(token)
                    except ValueError:
                        # Cursor-style token not usable as start offset; stop to avoid tight loop
                        self.logger.warning(
                            "Unsupported pagination token in spaces v1 next link; stopping space sync early"
                        )
                        break

            self.logger.info(f"✅ Space sync complete. Spaces: {total_spaces_synced}")

            return record_groups

        except Exception as e:
            self.logger.error(f"❌ Space sync failed: {e}", exc_info=True)
            raise

    async def _fetch_space_homepage_info(
        self, space_key: str
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """Return (homepage_id, title, last_modified) from GET /rest/api/space?expand=homepage."""
        try:
            datasource = await self._get_fresh_datasource()
            space_resp = await datasource.get_spaces_v1(
                keys=[space_key], limit=1, expand="homepage"
            )
            if not space_resp or space_resp.status != HttpStatusCode.SUCCESS.value:
                return None, None, None
            space_results = space_resp.json().get("results", [])
            if not space_results:
                return None, None, None
            homepage = space_results[0].get("homepage") or {}
            homepage_id = str(homepage.get("id")) if homepage.get("id") else None
            homepage_title = homepage.get("title")
            homepage_last_modified: Optional[str] = None
            if homepage_id:
                hp_resp = await datasource.get_content_v1(
                    homepage_id,
                    expand="history.lastUpdated,version,space",
                )
                if hp_resp and hp_resp.status == HttpStatusCode.SUCCESS.value:
                    hp_data = hp_resp.json()
                    homepage_last_modified = _extract_item_last_modified_when(hp_data)
                    homepage_title = hp_data.get("title") or homepage_title
            return homepage_id, homepage_title, homepage_last_modified
        except Exception as e:
            self.logger.warning(
                "Failed to fetch space homepage for %s: %s", space_key, e
            )
            return None, None, None

    async def _backfill_space_homepage_from_api(
        self,
        space_homepage_id: str,
        space_key: str,
        record_type: RecordType,
        content_indexing_enabled: bool,
    ) -> int:
        """Sync space homepage via GET /content/{id} when CQL search omits it."""
        try:
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_content_v1(
                space_homepage_id,
                expand=CONTENT_EXPAND_PARAMS,
            )
            if not response or response.status != HttpStatusCode.SUCCESS.value:
                self.logger.warning(
                    "Failed to backfill space homepage %s for space %s: HTTP %s",
                    space_homepage_id,
                    space_key,
                    response.status if response else "no response",
                )
                return 0

            item_data = response.json()
            item_id = item_data.get("id")
            item_title = item_data.get("title")
            if not item_id or not item_title:
                return 0

            existing_record = await self.data_entities_processor.get_record_by_external_id(
                connector_id=self.connector_id,
                external_record_id=item_id,
            )
            # Personal connector: all records inherit permissions from space ConnectorGroup
            permissions = []
            webpage_record_update = await self._process_webpage_with_update(
                item_data, record_type, existing_record, permissions
            )
            if not webpage_record_update.record:
                return 0

            webpage_record = webpage_record_update.record
            if not content_indexing_enabled:
                webpage_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

            await self.data_entities_processor.on_new_records(
                [(webpage_record, permissions)]
            )
            self.logger.info(
                "Backfilled space homepage %s (%s) for space %s — omitted from CQL search",
                item_title,
                item_id,
                space_key,
            )
            return 1
        except Exception as e:
            self.logger.error(
                "Failed to backfill homepage %s for space %s: %s",
                space_homepage_id,
                space_key,
                e,
                exc_info=True,
            )
            return 0

    async def _sync_content(self, space_key: str, record_type: RecordType) -> None:
        """
        Unified sync for pages and blogposts from Confluence using v1 API.

        Uses cursor-based pagination with modification time filtering for incremental sync.
        Creates WebpageRecord for each content item with attachments, comments, and permissions.

        Args:
            space_key: The space key to sync content from
            record_type: RecordType.CONFLUENCE_PAGE or RecordType.CONFLUENCE_BLOGPOST
        """
        # Derive content_type from record_type for logging and sync point
        content_type = "page" if record_type == RecordType.CONFLUENCE_PAGE else "blogpost"

        try:
            self.logger.info(f"Starting {content_type} synchronization for space {space_key}...")
            # Get indexing filter settings based on content type (default=True means index if not configured)
            content_ids_filter = None
            if record_type == RecordType.CONFLUENCE_PAGE:
                content_indexing_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.PAGES)
                content_comments_indexing_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.PAGE_COMMENTS)
                content_attachments_indexing_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.PAGE_ATTACHMENTS)
                content_ids_filter = self.sync_filters.get(SyncFilterKey.PAGE_IDS)
            else:  # CONFLUENCE_BLOGPOST
                content_indexing_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.BLOGPOSTS)
                content_comments_indexing_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.BLOGPOST_COMMENTS)
                content_attachments_indexing_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.BLOGPOST_ATTACHMENTS)
                content_ids_filter = self.sync_filters.get(SyncFilterKey.BLOGPOST_IDS)

            # Get content IDs filter based on content type
            content_ids = None
            content_ids_operator_str = None
            if content_ids_filter is not None:
                content_ids = content_ids_filter.get_value()
                content_ids_operator = content_ids_filter.get_operator()
                # Extract operator value string for datasource
                content_ids_operator_str = content_ids_operator.value if hasattr(content_ids_operator, 'value') else str(content_ids_operator)
                if content_ids:
                    action = "Excluding" if content_ids_operator_str == "not_in" else "Including"
                    self.logger.info(f"🔍 Filter: {action} {content_type}s by IDs: {content_ids}")

            # Get last sync checkpoint (use content_type as suffix)
            sync_point_key = generate_record_sync_point_key(
                RecordType.WEBPAGE.value, f"confluence_{content_type}s", space_key
            )
            last_sync_data = await self.pages_sync_point.read_sync_point(sync_point_key)
            last_sync_time = last_sync_data.get("last_sync_time") if last_sync_data else None
            if last_sync_time:
                self.logger.info(f"🔄 Incremental sync: Fetching {content_type}s modified after {last_sync_time}")

            # Build date filter parameters from sync filters
            # Get modified filter
            modified_filter = self.sync_filters.get(SyncFilterKey.MODIFIED)
            modified_after = None
            modified_before = None

            if modified_filter:
                modified_after, modified_before = modified_filter.get_datetime_iso()

            # Get created filter
            created_filter = self.sync_filters.get(SyncFilterKey.CREATED)
            created_after = None
            created_before = None

            if created_filter:
                created_after, created_before = created_filter.get_datetime_iso()

            # Merge modified_after with checkpoint (use the latest)
            if modified_after and last_sync_time:
                modified_after = max(modified_after, last_sync_time)
                self.logger.info(f"🔄 Using latest modified_after: {modified_after} (filter: {modified_after}, checkpoint: {last_sync_time})")
            elif modified_after:
                self.logger.info(f"🔍 Using filter: Fetching {content_type}s modified after {modified_after}")
            elif last_sync_time:
                modified_after = last_sync_time
                self.logger.info(f"🔄 Incremental sync: Fetching {content_type}s modified after {modified_after}")
            else:
                self.logger.info(f"🆕 Full sync: Fetching all {content_type}s (first time)")

            # Log other filters if set
            if modified_before:
                self.logger.info(f"🔍 Filter: Fetching {content_type}s modified before {modified_before}")
            if created_after:
                self.logger.info(f"🔍 Filter: Fetching {content_type}s created after {created_after}")
            if created_before:
                self.logger.info(f"🔍 Filter: Fetching {content_type}s created before {created_before}")

            space_homepage_id: Optional[str] = None
            homepage_seen_in_search = False
            if record_type == RecordType.CONFLUENCE_PAGE:
                space_homepage_id, _, _ = await self._fetch_space_homepage_info(space_key)

            # Pagination variables
            # Supports both offset-based (start/limit) and cursor-based pagination
            batch_size = 50
            pagination_token: Optional[str] = None
            total_synced = 0
            total_attachments_synced = 0
            total_comments_synced = 0

            if record_type == RecordType.CONFLUENCE_PAGE and space_homepage_id:
                homepage_in_db = await self.data_entities_processor.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_record_id=space_homepage_id,
                )
                if not homepage_in_db:
                    backfilled = await self._backfill_space_homepage_from_api(
                        space_homepage_id,
                        space_key,
                        record_type,
                        content_indexing_enabled,
                    )
                    if backfilled:
                        total_synced += backfilled
                        homepage_seen_in_search = True

            # Paginate through all content items
            while True:
                datasource = await self._get_fresh_datasource()

                # Determine pagination parameters based on token type
                start_offset, cursor_token = self._split_pagination_token(pagination_token)

                if record_type == RecordType.CONFLUENCE_PAGE:
                    response = await datasource.get_pages_v1(
                        modified_after=modified_after,
                        modified_before=modified_before,
                        created_after=created_after,
                        created_before=created_before,
                        start=start_offset,
                        cursor=cursor_token,
                        limit=batch_size,
                        space_key=space_key,
                        page_ids=content_ids,
                        page_ids_operator=content_ids_operator_str,
                        include_children=True,
                        order_by="lastModified",
                        sort_order="asc",
                        expand=CONTENT_EXPAND_PARAMS,
                        time_offset_hours=TIME_OFFSET_HOURS
                    )
                else:  # CONFLUENCE_BLOGPOST
                    response = await datasource.get_blogposts_v1(
                        modified_after=modified_after,
                        modified_before=modified_before,
                        created_after=created_after,
                        created_before=created_before,
                        start=start_offset,
                        cursor=cursor_token,
                        limit=batch_size,
                        space_key=space_key,
                        blogpost_ids=content_ids,
                        blogpost_ids_operator=content_ids_operator_str,
                        order_by="lastModified",
                        sort_order="asc",
                        expand=CONTENT_EXPAND_PARAMS,
                        time_offset_hours=TIME_OFFSET_HOURS
                    )

                # Check response
                if not response or response.status != HttpStatusCode.SUCCESS.value:
                    self.logger.error(f"❌ Failed to fetch {content_type}s: {response.status if response else 'No response'}")
                    break

                response_data = response.json()
                items_data = response_data.get("results", [])

                # Extract api_base_url from response root for URL construction
                api_base_url = (response_data.get("_links") or {}).get("base")

                if not items_data:
                    break


                # Transform items to WebpageRecords with permissions
                records_with_permissions = []
                for item_data in items_data:
                    try:
                        item_id = item_data.get("id")
                        item_title = item_data.get("title")

                        if not item_id or not item_title:
                            continue

                        if (
                            record_type == RecordType.CONFLUENCE_PAGE
                            and space_homepage_id
                            and str(item_id) == space_homepage_id
                        ):
                            homepage_seen_in_search = True

                        self.logger.debug(f"Processing {content_type}: {item_title} ({item_id})")

                        # Check if record exists in DB
                        existing_record = await self.data_entities_processor.get_record_by_external_id(
                            connector_id=self.connector_id,
                            external_record_id=item_id
                        )

                        # Personal connector: all records inherit permissions from space ConnectorGroup
                        permissions = []

                        # Transform to WebpageRecord with update tracking
                        webpage_record_update = await self._process_webpage_with_update(
                            item_data, record_type, existing_record, permissions, api_base_url
                        )

                        if not webpage_record_update.record:
                            continue

                        webpage_record = webpage_record_update.record

                        # Set indexing status based on filter
                        if not content_indexing_enabled:
                            webpage_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

                        # Add item to batch
                        records_with_permissions.append((webpage_record, permissions))
                        total_synced += 1

                        # Extract space_id for children
                        space_data = item_data.get("space", {})
                        space_id = str(space_data.get("id")) if space_data.get("id") else None

                        # Get parent_node_id for dependent nodes (comments and attachments)
                        parent_node_id = webpage_record.id

                        # Process attachments first (keep list in memory for comment embedded attachment resolution)
                        children = item_data.get("children", {})
                        attachment_data = children.get("attachment", {})
                        inline_attachments = attachment_data.get("results", [])
                        attachment_size = attachment_data.get("size", 0)

                        # Check if inline results are truncated (Confluence limits inline expansion to ~5-25 items)
                        # If so, fetch attachments separately to avoid silent data loss
                        if inline_attachments and len(inline_attachments) < attachment_size:
                            self.logger.debug(
                                f"Inline attachments truncated for {content_type} {item_title} "
                                f"({len(inline_attachments)}/{attachment_size}), fetching separately"
                            )
                            # Fetch all attachments via separate pagination
                            page_attachments, fetched_api_base_url = await self._fetch_all_attachments(item_id)
                            # Use fetched base URL if we don't already have one
                            if not api_base_url and fetched_api_base_url:
                                api_base_url = fetched_api_base_url
                        else:
                            # Use inline results if complete
                            page_attachments = inline_attachments

                        if page_attachments:
                            self.logger.debug(f"Found {len(page_attachments)} attachments for {content_type} {item_title}")

                            for attachment in page_attachments:
                                try:
                                    attachment_id = attachment.get("id")
                                    if not attachment_id:
                                        continue

                                    # Check if attachment exists in DB
                                    existing_attachment = await self.data_entities_processor.get_record_by_external_id(
                                        connector_id=self.connector_id,
                                        external_record_id=attachment_id
                                    )

                                    attachment_record = self._transform_to_attachment_file_record(
                                        attachment,
                                        item_id,
                                        space_id,
                                        existing_record=existing_attachment,
                                        parent_node_id=parent_node_id,
                                        api_base_url=api_base_url
                                    )

                                    if attachment_record:
                                        # Set indexing status based on filter
                                        if not content_attachments_indexing_enabled:
                                            attachment_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
                                        # Attachments inherit permissions from parent
                                        records_with_permissions.append((attachment_record, permissions))
                                        total_attachments_synced += 1
                                        self.logger.debug(f"Attachment: {attachment_record.record_name}")

                                except Exception as att_error:
                                    self.logger.error(f"❌ Failed to process attachment: {att_error}")
                                    continue

                        # Process comments (always fetch; DC /content/search omits childTypes.comment even when present)
                        self.logger.debug(f"Fetching comments for {content_type} {item_title}...")

                        # Fetch comments (footer and inline) with page attachments for embedded resolution
                        for comment_type in ["footer", "inline"]:
                            comments = await self._fetch_comments_recursive(
                                item_id,
                                item_title,
                                comment_type,
                                permissions,
                                space_id,
                                content_type,
                                parent_node_id=parent_node_id,
                                page_attachments=page_attachments,
                                attachments_indexing_enabled=content_attachments_indexing_enabled
                            )
                            # Comments already have indexing status set; just count them
                            # (Note: comments now includes attachment records too)
                            comment_count = sum(1 for rec, _ in comments if rec.record_type in [RecordType.COMMENT, RecordType.INLINE_COMMENT])
                            records_with_permissions.extend(comments)
                            total_comments_synced += comment_count

                    except Exception as item_error:
                        self.logger.error(f"❌ Failed to process {content_type} {item_data.get('title')}: {item_error}")
                        continue

                # Save batch to database
                if records_with_permissions:
                    await self.data_entities_processor.on_new_records(records_with_permissions)
                    self.logger.info(f"Synced batch of {len(records_with_permissions)} items ({content_type}s + attachments + comments)")

                # Extract next page token from _links.next
                # May contain either start=N (offset) or cursor=<token> depending on API version
                next_url = response_data.get("_links", {}).get("next")
                if not next_url:
                    break

                pagination_token = self._pagination_token_from_next_link(next_url)
                if pagination_token is None:
                    # No pagination token — stop if fewer results than batch_size
                    if len(items_data) < batch_size:
                        break
                    # Confluence occasionally omits _links.next despite having more:
                    # advance by the number of items actually returned to stay correct.
                    start_offset = (start_offset or 0) + len(items_data)
                    pagination_token = str(start_offset)
                    continue

            if (
                record_type == RecordType.CONFLUENCE_PAGE
                and space_homepage_id
                and not homepage_seen_in_search
            ):
                backfilled = await self._backfill_space_homepage_from_api(
                    space_homepage_id,
                    space_key,
                    record_type,
                    content_indexing_enabled,
                )
                if backfilled:
                    total_synced += backfilled


            # Update sync checkpoint with current time (only if we synced something)
            # Using current time instead of last item's time avoids re-fetching due to the 24-hour offset
            if total_synced > 0:
                current_sync_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                await self.pages_sync_point.update_sync_point(sync_point_key, {"last_sync_time": current_sync_time})
                self.logger.info(f"Updated {content_type}s sync checkpoint to {current_sync_time}")

            self.logger.info(f"✅ {content_type.capitalize()} sync complete. {content_type.capitalize()}s: {total_synced}, Attachments: {total_attachments_synced}, Comments: {total_comments_synced}")

        except Exception as e:
            self.logger.error(f"❌ {content_type.capitalize()} sync failed: {e}", exc_info=True)
            raise

    async def _fetch_all_attachments(self, content_id: str) -> tuple[list[dict[str, Any]], Optional[str]]:
        """
        Fetch all attachments for a content item using separate pagination.

        Used when inline attachment expansion is truncated (Confluence limits inline
        children expansion to ~5-25 items depending on version/config).

        Args:
            content_id: The page or blogpost ID

        Returns:
            Tuple of (list of attachment data dictionaries, api_base_url from response)
        """
        all_attachments = []
        api_base_url = None
        batch_size = 100
        start = 0

        try:
            while True:
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_content_attachments_v1(
                    content_id=content_id,
                    start=start,
                    limit=batch_size,
                    expand="version,history,container"
                )

                if not response or response.status != HttpStatusCode.SUCCESS.value:
                    self.logger.warning(
                        f"⚠️ Failed to fetch attachments for content {content_id}: "
                        f"{response.status if response else 'No response'}"
                    )
                    break

                response_data = response.json()
                
                # Capture api_base_url from first response
                if api_base_url is None:
                    api_base_url = (response_data.get("_links") or {}).get("base")
                
                attachments = response_data.get("results", [])

                if not attachments:
                    break

                all_attachments.extend(attachments)

                # Check if we've reached the end
                if len(attachments) < batch_size:
                    break

                start += batch_size

            return all_attachments, api_base_url

        except Exception as e:
            self.logger.error(f"❌ Failed to fetch attachments for content {content_id}: {e}")
            return [], None

    async def _fetch_attachment_file_records(
        self,
        comment_id: str,
        comment_record_type: RecordType,
        comment_node_id: str,
        parent_space_id: Optional[str],
        attachments_indexing_enabled: bool,
        permissions: list[Permission]
    ) -> list[tuple[FileRecord, list[Permission]]]:
        """
        Fetch and transform attachment file records for a comment.

        Args:
            comment_id: External record ID of the comment
            comment_record_type: RecordType.COMMENT or RecordType.INLINE_COMMENT
            comment_node_id: Internal record ID of the comment
            parent_space_id: Space ID from parent page
            attachments_indexing_enabled: Whether attachments should be indexed
            permissions: Permissions inherited from parent page

        Returns:
            List of (FileRecord, permissions) tuples
        """
        try:
            attachments, attachment_api_base_url = await self._fetch_all_attachments(comment_id)
            if not attachments:
                return []

            records_with_permissions = []
            for attachment_data in attachments:
                file_record = self._transform_to_attachment_file_record(
                    attachment_data=attachment_data,
                    parent_external_record_id=comment_id,
                    parent_external_record_group_id=parent_space_id,
                    parent_node_id=comment_node_id,
                    parent_record_type=comment_record_type,
                    api_base_url=attachment_api_base_url
                )
                if file_record:
                    # Apply indexing filter
                    if not attachments_indexing_enabled:
                        file_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
                    # Inherit page permissions
                    records_with_permissions.append((file_record, permissions))

            return records_with_permissions

        except Exception as e:
            self.logger.error(f"❌ Failed to fetch attachment file records for comment {comment_id}: {e}")
            return []

    async def _fetch_comments_recursive(
        self,
        page_id: str,
        page_title: str,
        comment_type: str,
        page_permissions: list[Permission],
        parent_space_id: Optional[str],
        parent_type: str = "page",
        parent_node_id: Optional[str] = None,
        page_attachments: Optional[list[dict[str, Any]]] = None,
        attachments_indexing_enabled: bool = True
    ) -> list[tuple[Record, list[Permission]]]:
        """
        Recursively fetch all comments (footer or inline) for a page or blogpost.

        Fetches top-level comments and all nested replies in a flat list, plus their attachments.
        Each comment inherits permissions from the parent.

        Args:
            page_id: The page/blogpost ID
            page_title: The page/blogpost title (for logging)
            comment_type: "footer" or "inline"
            page_permissions: Permissions inherited from parent
            parent_space_id: Space ID for external_record_group_id
            parent_type: "page" or "blogpost" (determines which API to call)
            parent_node_id: Internal record ID of parent page
            page_attachments: List of page attachment data for resolving embedded filenames
            attachments_indexing_enabled: Whether comment attachments should be indexed

        Returns:
            List of tuples (Record, permissions list) for comments and their attachments
        """
        try:
            all_comments = []
            batch_size = 100
            start_offset = 0

            self.logger.debug(f"Fetching {comment_type} comments for {parent_type}: {page_title}")

            # v1: GET .../content/{id}/child/comment (footer + inline); filter by extensions.location
            while True:
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_content_comments_v1(
                    content_id=str(page_id),
                    start=start_offset,
                    limit=batch_size,
                    depth="",
                )

                if not response or response.status != HttpStatusCode.SUCCESS.value:
                    self.logger.warning(
                        f"⚠️ Failed to fetch {comment_type} comments for page {page_title}: "
                        f"{response.status if response else 'No response'}"
                    )
                    break

                response_data = response.json()
                comments_data = response_data.get("results", [])

                if not comments_data:
                    break

                response_links = response_data.get("_links", {})
                base_url = response_links.get("base")

                for comment_data in comments_data:
                    loc = (comment_data.get("extensions") or {}).get("location") or "footer"
                    if comment_type == "inline" and loc != "inline":
                        continue
                    if comment_type == "footer" and loc == "inline":
                        continue

                    try:
                        cid = comment_data.get("id")
                        if not cid:
                            continue
                        comment_id_str = str(cid)

                        existing_comment = await self.data_entities_processor.get_record_by_external_id(
                            connector_id=self.connector_id,
                            external_record_id=comment_id_str,
                        )

                        normalized = self._normalize_v1_comment_for_transform(comment_data, comment_type)
                        comment_record = self._transform_to_comment_record(
                            normalized,
                            page_id,
                            parent_space_id,
                            comment_type,
                            None,
                            base_url=base_url,
                            existing_record=existing_comment,
                            parent_node_id=parent_node_id,
                        )

                        if comment_record:
                            all_comments.append((comment_record, page_permissions))

                            # Sync comment attachments (explicit child attachments)
                            comment_record_type = RecordType.INLINE_COMMENT if comment_type == "inline" else RecordType.COMMENT
                            comment_file_records = await self._fetch_attachment_file_records(
                                comment_id=comment_id_str,
                                comment_record_type=comment_record_type,
                                comment_node_id=comment_record.id,
                                parent_space_id=parent_space_id,
                                attachments_indexing_enabled=attachments_indexing_enabled,
                                permissions=page_permissions
                            )
                            all_comments.extend(comment_file_records)

                            # Extract and resolve embedded attachments from comment body.storage
                            body = comment_data.get("body", {})
                            storage = body.get("storage", {})
                            body_storage_html = storage.get("value", "")
                            
                            if body_storage_html:
                                embedded_filenames = self._extract_storage_attachment_filenames(body_storage_html)
                                
                                if embedded_filenames:
                                    # Build combined attachment list for resolution (comment attachments + page attachments)
                                    comment_attachments_data, comment_api_base_url = await self._fetch_all_attachments(comment_id_str)
                                    # Use base_url from parent comment list response if comment attachments don't have one
                                    effective_base_url = comment_api_base_url or base_url
                                    combined_attachments = comment_attachments_data + (page_attachments or [])
                                    
                                    # Track already-synced attachment IDs to avoid duplicates
                                    synced_attachment_ids = {
                                        rec[0].external_record_id 
                                        for rec in comment_file_records
                                    }
                                    
                                    # Resolve each embedded filename and create FileRecord if not already synced
                                    for filename in embedded_filenames:
                                        attachment_id = self._resolve_attachment_by_filename(filename, combined_attachments)
                                        
                                        if attachment_id and attachment_id not in synced_attachment_ids:
                                            # Find the attachment data
                                            attachment_data = next(
                                                (att for att in combined_attachments if att.get("id") == attachment_id),
                                                None
                                            )
                                            
                                            if attachment_data:
                                                # Check if attachment already exists in DB (e.g., page attachment)
                                                existing_attachment = await self.data_entities_processor.get_record_by_external_id(
                                                    connector_id=self.connector_id,
                                                    external_record_id=attachment_id
                                                )
                                                
                                                if not existing_attachment:
                                                    # Create FileRecord for embedded attachment
                                                    file_record = self._transform_to_attachment_file_record(
                                                        attachment_data=attachment_data,
                                                        parent_external_record_id=comment_id_str,
                                                        parent_external_record_group_id=parent_space_id,
                                                        parent_node_id=comment_record.id,
                                                        parent_record_type=comment_record_type,
                                                        api_base_url=effective_base_url
                                                    )
                                                    
                                                    if file_record:
                                                        if not attachments_indexing_enabled:
                                                            file_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
                                                        all_comments.append((file_record, page_permissions))
                                                        synced_attachment_ids.add(attachment_id)

                        children = await self._fetch_comment_children_recursive(
                            comment_id_str,
                            comment_type,
                            page_id,
                            parent_space_id,
                            page_permissions,
                            parent_node_id=parent_node_id,
                            page_attachments=page_attachments,
                            attachments_indexing_enabled=attachments_indexing_enabled
                        )
                        all_comments.extend(children)

                    except Exception as comment_error:
                        self.logger.error(f"❌ Failed to process comment {comment_data.get('id')}: {comment_error}")
                        continue

                next_url = response_data.get("_links", {}).get("next")
                token = self._pagination_token_from_next_link(next_url) if next_url else None
                if token is not None:
                    try:
                        start_offset = int(token)
                        continue
                    except ValueError:
                        break
                if len(comments_data) < batch_size:
                    break
                start_offset += batch_size

            self.logger.debug(f"✓ Fetched {len(all_comments)} {comment_type} comments (including replies) for page {page_title}")
            return all_comments

        except Exception as e:
            self.logger.error(f"❌ Failed to fetch {comment_type} comments for page {page_title}: {e}")
            return []

    async def _fetch_comment_children_recursive(
        self,
        comment_id: str,
        comment_type: str,
        page_id: str,
        parent_space_id: Optional[str],
        page_permissions: list[Permission],
        parent_node_id: Optional[str] = None,
        page_attachments: Optional[list[dict[str, Any]]] = None,
        attachments_indexing_enabled: bool = True
    ) -> list[tuple[Record, list[Permission]]]:
        """
        Recursively fetch all children (replies) of a comment.

        Args:
            comment_id: The parent comment ID
            comment_type: "footer" or "inline"
            page_id: The parent page ID
            parent_space_id: Space ID for external_record_group_id
            page_permissions: Permissions inherited from parent page
            parent_node_id: Internal record ID of parent page
            page_attachments: List of page attachment data for resolving embedded filenames
            attachments_indexing_enabled: Whether comment attachments should be indexed

        Returns:
            List of tuples (Record, permissions list) for comments and their attachments
        """
        try:
            all_children = []
            batch_size = 100
            start_offset = 0

            while True:
                datasource = await self._get_fresh_datasource()
                response = await datasource.get_content_comments_v1(
                    content_id=str(comment_id),
                    start=start_offset,
                    limit=batch_size,
                    depth="",
                )

                if not response or response.status != HttpStatusCode.SUCCESS.value:
                    break

                response_data = response.json()
                children_data = response_data.get("results", [])

                if not children_data:
                    break

                response_links = response_data.get("_links", {})
                base_url = response_links.get("base")

                for child_data in children_data:
                    try:
                        cid = child_data.get("id")
                        if not cid:
                            continue
                        child_id_str = str(cid)

                        existing_child = await self.data_entities_processor.get_record_by_external_id(
                            connector_id=self.connector_id,
                            external_record_id=child_id_str,
                        )

                        normalized = self._normalize_v1_comment_for_transform(child_data, comment_type)
                        child_record = self._transform_to_comment_record(
                            normalized,
                            page_id,
                            parent_space_id,
                            comment_type,
                            comment_id,
                            base_url=base_url,
                            existing_record=existing_child,
                            parent_node_id=parent_node_id,
                        )

                        if child_record:
                            all_children.append((child_record, page_permissions))

                            # Sync comment attachments (explicit child attachments)
                            child_record_type = RecordType.INLINE_COMMENT if comment_type == "inline" else RecordType.COMMENT
                            child_file_records = await self._fetch_attachment_file_records(
                                comment_id=child_id_str,
                                comment_record_type=child_record_type,
                                comment_node_id=child_record.id,
                                parent_space_id=parent_space_id,
                                attachments_indexing_enabled=attachments_indexing_enabled,
                                permissions=page_permissions
                            )
                            all_children.extend(child_file_records)

                            # Extract and resolve embedded attachments from comment body.storage
                            body = child_data.get("body", {})
                            storage = body.get("storage", {})
                            body_storage_html = storage.get("value", "")
                            
                            if body_storage_html:
                                embedded_filenames = self._extract_storage_attachment_filenames(body_storage_html)
                                
                                if embedded_filenames:
                                    # Build combined attachment list for resolution
                                    child_attachments_data, child_api_base_url = await self._fetch_all_attachments(child_id_str)
                                    # Use base_url from parent comment list response if child attachments don't have one
                                    effective_base_url = child_api_base_url or base_url
                                    combined_attachments = child_attachments_data + (page_attachments or [])
                                    
                                    # Track already-synced attachment IDs
                                    synced_attachment_ids = {
                                        rec[0].external_record_id 
                                        for rec in child_file_records
                                    }
                                    
                                    # Resolve each embedded filename and create FileRecord if not already synced
                                    for filename in embedded_filenames:
                                        attachment_id = self._resolve_attachment_by_filename(filename, combined_attachments)
                                        
                                        if attachment_id and attachment_id not in synced_attachment_ids:
                                            # Find the attachment data
                                            attachment_data = next(
                                                (att for att in combined_attachments if att.get("id") == attachment_id),
                                                None
                                            )
                                            
                                            if attachment_data:
                                                # Check if attachment already exists in DB
                                                existing_attachment = await self.data_entities_processor.get_record_by_external_id(
                                                    connector_id=self.connector_id,
                                                    external_record_id=attachment_id
                                                )
                                                
                                                if not existing_attachment:
                                                    # Create FileRecord for embedded attachment
                                                    file_record = self._transform_to_attachment_file_record(
                                                        attachment_data=attachment_data,
                                                        parent_external_record_id=child_id_str,
                                                        parent_external_record_group_id=parent_space_id,
                                                        parent_node_id=child_record.id,
                                                        parent_record_type=child_record_type,
                                                        api_base_url=effective_base_url
                                                    )
                                                    
                                                    if file_record:
                                                        if not attachments_indexing_enabled:
                                                            file_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
                                                        all_children.append((file_record, page_permissions))
                                                        synced_attachment_ids.add(attachment_id)

                        grandchildren = await self._fetch_comment_children_recursive(
                            child_id_str,
                            comment_type,
                            page_id,
                            parent_space_id,
                            page_permissions,
                            parent_node_id=parent_node_id,
                            page_attachments=page_attachments,
                            attachments_indexing_enabled=attachments_indexing_enabled
                        )
                        all_children.extend(grandchildren)

                    except Exception as child_error:
                        self.logger.error(f"❌ Failed to process child comment {child_data.get('id')}: {child_error}")
                        continue

                next_url = response_data.get("_links", {}).get("next")
                token = self._pagination_token_from_next_link(next_url) if next_url else None
                if token is not None:
                    try:
                        start_offset = int(token)
                        continue
                    except ValueError:
                        break
                if len(children_data) < batch_size:
                    break
                start_offset += batch_size

            return all_children

        except Exception as e:
            self.logger.error(f"❌ Failed to fetch children for comment {comment_id}: {e}")
            return []

    def _transform_to_comment_record(
        self,
        comment_data: dict[str, Any],
        page_id: str,
        parent_space_id: Optional[str],
        comment_type: str,
        parent_comment_id: Optional[str],
        base_url: Optional[str] = None,
        existing_record: Optional[Record] = None,
        parent_node_id: Optional[str] = None
    ) -> Optional[CommentRecord]:
        """
        Transform Confluence comment data to CommentRecord entity.

        Args:
            comment_data: Raw comment data from Confluence API
            page_id: Parent page external_record_id
            parent_space_id: Space ID from parent page
            comment_type: "footer" or "inline"
            parent_comment_id: Parent comment ID (None for top-level comments)
            base_url: Base URL from response level (v2 API) - if None, will extract from _links.self (v1 API)
            existing_record: Optional existing record to check for updates
            parent_node_id: Internal record ID of parent page

        Returns:
            CommentRecord object or None if transformation fails
        """
        try:
            comment_id = comment_data.get("id")
            title = comment_data.get("title", "")

            if not comment_id:
                return None

            # Extract author accountId
            author = comment_data.get("version", {}).get("authorId")
            if not author:
                self.logger.warning(f"Comment {comment_id} has no author - skipping")
                return None

            # Parse timestamps
            source_created_at = None

            created_at_str = comment_data.get("version", {}).get("createdAt")
            if created_at_str:
                source_created_at = self._parse_confluence_datetime(created_at_str)

            # Extract resolution status (for inline comments)
            resolution_status = None
            if comment_type == "inline":
                is_resolved = comment_data.get("resolutionStatus", False)
                resolution_status = "resolved" if is_resolved else "open"

            # Extract inline original selection (for inline comments)
            inline_original_selection = None
            if comment_type == "inline":
                inline_properties = comment_data.get("properties", {})
                if inline_properties:
                    inline_original_selection = inline_properties.get("inlineOriginalSelection")

            # Determine parent record ID and type
            parent_external_record_id = parent_comment_id if parent_comment_id else page_id
            parent_record_type = RecordType.COMMENT if parent_comment_id else RecordType.WEBPAGE

            # Determine record ID and version
            is_new = existing_record is None
            comment_record_id = str(uuid.uuid4()) if is_new else existing_record.id

            version_number = comment_data.get("version", {}).get("number", 0)

            # Calculate version based on changes
            record_version = 0
            if not is_new:
                # Check if content changed (version number changed)
                if str(version_number) != existing_record.external_revision_id:
                    record_version = existing_record.version + 1
                else:
                    record_version = existing_record.version

            # Construct web URL for comment
            links = comment_data.get("_links", {})
            web_url = self._construct_web_url(links, base_url)

            return CommentRecord(
                id=comment_record_id,
                org_id=self.data_entities_processor.org_id,
                record_name=title,
                record_type=RecordType.INLINE_COMMENT if comment_type == "inline" else RecordType.COMMENT,
                external_record_id=comment_id,
                external_revision_id=str(version_number) if version_number else None,
                version=record_version,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.CONFLUENCE_DATA_CENTER,
                connector_id=self.connector_id,
                mime_type=MimeTypes.HTML.value,
                parent_external_record_id=parent_external_record_id,
                parent_record_type=parent_record_type,
                external_record_group_id=parent_space_id,
                record_group_type=RecordGroupType.CONFLUENCE_SPACES,
                source_created_at=source_created_at,
                source_updated_at=source_created_at,
                weburl=web_url,
                author_source_id=author,
                resolution_status=resolution_status,
                comment_selection=inline_original_selection,
                is_dependent_node=True,  # Comments are dependent nodes
                parent_node_id=parent_node_id,  # Internal record ID of parent page
            )

        except Exception as e:
            self.logger.error(f"❌ Failed to transform comment: {e}")
            return None

    def _construct_web_url(self, links: dict[str, Any], base_url: Optional[str] = None) -> Optional[str]:
        """
        Construct web URL from _links dictionary.

        Supports both v1 and v2 API response formats:
        - v2 API: base_url is at response level (passed as parameter)
        - v1 API: base_url needs to be extracted from _links.self

        Args:
            links: The _links dictionary from API response
            base_url: Optional base URL from response level (v2 API)

        Returns:
            Constructed web URL or None if not possible
        """
        web_path = links.get("webui")
        if not web_path:
            return None

        # Use base_url from parameter (v2 API - from response level)
        if base_url:
            return f"{base_url}{web_path}"

        # Fall back to v1 format (extract from _links.self)
        self_link = links.get("self")
        if self_link and "://" in self_link:
            # Cloud pattern: /wiki/
            if "/wiki/" in self_link:
                extracted_base_url = self_link.split("/wiki/")[0] + "/wiki"
                return f"{extracted_base_url}{web_path}"
            # DC pattern: /rest/api/
            if "/rest/api/" in self_link:
                extracted_base_url = self_link.split("/rest/api/")[0]
                return f"{extracted_base_url}{web_path}"

        return None

    def _normalize_v1_comment_for_transform(
        self, comment_data: dict[str, Any], comment_type: str
    ) -> dict[str, Any]:
        """Map v1 REST comment JSON into fields ``_transform_to_comment_record`` expects (v2-oriented)."""
        normalized = dict(comment_data)
        if normalized.get("id") is not None:
            normalized["id"] = str(normalized["id"])
        version = dict(comment_data.get("version") or {})
        by = version.get("by") or {}
        if by and not version.get("authorId"):
            version["authorId"] = (
                by.get("accountId") or by.get("username") or by.get("userKey") or by.get("key") or ""
            )
        if version.get("when") and not version.get("createdAt"):
            version["createdAt"] = version["when"]
        if version.get("number") is None:
            version["number"] = 1
        normalized["version"] = version

        extensions = comment_data.get("extensions") or {}
        if comment_type == "inline":
            ip = extensions.get("inlineProperties") or {}
            props = dict(comment_data.get("properties") or {})
            if ip.get("originalSelection") is not None:
                props["inlineOriginalSelection"] = ip.get("originalSelection")
            normalized["properties"] = props
            res = extensions.get("resolution")
            if isinstance(res, dict) and "resolved" in res:
                normalized["resolutionStatus"] = bool(res.get("resolved"))
        return normalized

    @staticmethod
    def _split_pagination_token(token: Optional[str]) -> tuple[Optional[int], Optional[str]]:
        """
        Split a pagination token into offset (int) or cursor (str) components.
        
        Handles both offset-based pagination (Data Center, Cloud v1) and 
        cursor-based pagination (Cloud v2).
        
        Args:
            token: Pagination token from _links.next (could be "123" or "base64cursor")
            
        Returns:
            Tuple of (start_offset, cursor_token) where one is None
        """
        if not token:
            return None, None
        try:
            return int(token), None
        except (TypeError, ValueError):
            return None, token

    def _pagination_token_from_next_link(self, next_link: Optional[str]) -> Optional[str]:
        """Return ``cursor`` or ``start`` token from a v1/v2 ``_links.next`` URL for filter pagination."""
        if not next_link:
            return None
        try:
            parsed = urlparse(next_link)
            qp = parse_qs(parsed.query)
            if qp.get("cursor"):
                return qp.get("cursor", [None])[0]
            if qp.get("start"):
                return qp.get("start", [None])[0]
            return None
        except Exception:
            return None

    @staticmethod
    def _parse_server_version_numbers(data: dict) -> tuple[int, ...]:
        """Parse ``versionNumbers`` or dotted ``version`` from server-information JSON."""
        version_numbers = data.get("versionNumbers") or []
        if isinstance(version_numbers, list) and version_numbers:
            return tuple(int(v) for v in version_numbers)

        version_str = data.get("version")
        if isinstance(version_str, str) and version_str:
            parts = [int(part) for part in version_str.split(".") if part.isdigit()]
            if parts:
                return tuple(parts)

        return ()

    @staticmethod
    def _extract_storage_attachment_filenames(body_storage_html: str) -> list[str]:
        """
        Extract attachment filenames from Confluence storage HTML.
        
        Parses ri:attachment tags with ri:filename attributes from comment/page body.
        Example: <ri:attachment ri:filename="Screenshot 2025-10-09 104312.PNG" />
        
        Args:
            body_storage_html: HTML content from body.storage.value
            
        Returns:
            Deduplicated list of filenames found in the HTML
        """
        if not body_storage_html:
            return []
        
        # Match ri:filename="..." in ri:attachment tags
        # Handles both self-closing tags and nested within ac:image
        pattern = r'ri:filename="([^"]+)"'
        matches = re.findall(pattern, body_storage_html)
        
        # Dedupe while preserving order
        seen = set()
        filenames = []
        for filename in matches:
            if filename not in seen:
                seen.add(filename)
                filenames.append(filename)
        
        return filenames

    @staticmethod
    def _resolve_attachment_by_filename(
        filename: str,
        attachments: list[dict[str, Any]]
    ) -> Optional[str]:
        """
        Resolve attachment ID by filename from an attachment list.
        
        Matches attachment["title"] case-insensitively (same logic as Cloud connector).
        
        Args:
            filename: Filename to search for (from ri:filename)
            attachments: List of attachment data dictionaries with "id" and "title" keys
            
        Returns:
            Attachment external_record_id (e.g., "att123") if found, None otherwise
        """
        if not filename or not attachments:
            return None
        
        filename_lower = filename.lower()
        for attachment in attachments:
            attachment_title = attachment.get("title", "")
            if attachment_title.lower() == filename_lower:
                return attachment.get("id")
        
        return None

    @staticmethod
    def _resolve_page_id_from_comment_json(
        comment_data: dict[str, Any],
        fallback_parent_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Extract parent page/blogpost ID from comment JSON.
        
        Args:
            comment_data: Comment JSON from Confluence API
            fallback_parent_id: Optional fallback ID (from record.parent_external_record_id)
            
        Returns:
            Page ID if found, None otherwise
        """
        container = comment_data.get("container") or {}
        if container.get("type") in ("page", "blogpost"):
            page_id = container.get("id")
            if page_id:
                return str(page_id)
        
        return fallback_parent_id

    async def _resolve_attachment_parent_context(
        self,
        record: Record,
        attachment_data: Optional[dict[str, Any]] = None
    ) -> Optional[tuple[str, RecordType, Optional[str], str]]:
        """
        Resolve attachment parent context for reindex.
        
        Args:
            record: FileRecord being reindexed
            attachment_data: Optional attachment metadata from API (may contain container info)
            
        Returns:
            Tuple of (immediate_parent_external_id, parent_record_type, parent_node_id, page_id_for_permissions)
            or None if resolution fails
        """
        try:
            immediate_parent_id = record.parent_external_record_id
            if not immediate_parent_id:
                self.logger.warning(f"Attachment {record.external_record_id} has no parent_external_record_id")
                return None
            
            # Determine parent_record_type (default to WEBPAGE for legacy records)
            parent_record_type = record.parent_record_type or RecordType.WEBPAGE
            
            # Page parent case
            if parent_record_type == RecordType.WEBPAGE:
                parent_record = await self.data_entities_processor.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_record_id=immediate_parent_id
                )
                parent_node_id = parent_record.id if parent_record else None
                return (immediate_parent_id, parent_record_type, parent_node_id, immediate_parent_id)
            
            # Comment parent case
            if parent_record_type in (RecordType.COMMENT, RecordType.INLINE_COMMENT):
                # Get comment's internal node ID
                comment_record = await self.data_entities_processor.get_record_by_external_id(
                    connector_id=self.connector_id,
                    external_record_id=immediate_parent_id
                )
                parent_node_id = comment_record.id if comment_record else None
                
                # Resolve page ID for permissions
                page_id = None
                
                # Try to extract from attachment_data container if available
                if attachment_data:
                    container = attachment_data.get("container") or {}
                    if container.get("type") in ("page", "blogpost"):
                        page_id = str(container.get("id")) if container.get("id") else None
                
                # If not found, fetch comment to get container
                if not page_id:
                    datasource = await self._get_fresh_datasource()
                    response = await datasource.get_content_v1(
                        content_id=str(immediate_parent_id),
                        expand="container"
                    )
                    if response and response.status == HttpStatusCode.SUCCESS.value:
                        comment_data = response.json()
                        page_id = self._resolve_page_id_from_comment_json(comment_data)
                
                # Final fallback: check record's parent if it's a WEBPAGE
                if not page_id and comment_record:
                    if comment_record.parent_record_type == RecordType.WEBPAGE:
                        page_id = comment_record.parent_external_record_id
                
                if not page_id:
                    self.logger.warning(
                        f"Attachment {record.external_record_id}: could not resolve page ID from comment {immediate_parent_id}"
                    )
                    return None
                
                return (immediate_parent_id, parent_record_type, parent_node_id, page_id)
            
            self.logger.warning(
                f"Attachment {record.external_record_id}: unsupported parent_record_type {parent_record_type}"
            )
            return None
            
        except Exception as e:
            self.logger.error(
                f"Error resolving attachment parent context for {record.external_record_id}: {e}",
                exc_info=True
            )
            return None

    async def _get_server_version(self) -> tuple[int, ...]:
        """
        Get Confluence server version as tuple (e.g. (9, 1, 0)).

        Caches the result after first call. Used to check if DC version supports
        certain endpoints (e.g. space permissions REST requires DC 9.1+).

        Returns:
            Version tuple like (9, 1, 0) for DC 9.1.0, or (0,) if probe fails.
        """
        if self._server_version is not None:
            return self._server_version

        try:
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_server_information()

            if response and response.status == HttpStatusCode.SUCCESS.value:
                data = response.json()
                parsed_version = self._parse_server_version_numbers(data)
                if parsed_version:
                    self._server_version = parsed_version
                    version_str = data.get("version", "unknown")
                    deployment_type = data.get("deploymentType", "unknown")
                    self.logger.info(
                        f"Confluence server version: {version_str} ({deployment_type}), "
                        f"parsed as {self._server_version}"
                    )
                    return self._server_version

            # Fallback: assume modern version if probe fails
            self.logger.warning(
                "Failed to probe Confluence server version, assuming modern (>= 9.1)"
            )
            self._server_version = (9, 1, 0)
            return self._server_version

        except Exception as e:
            self.logger.warning(
                f"Error probing Confluence server version: {e}, assuming modern (>= 9.1)"
            )
            self._server_version = (9, 1, 0)
            return self._server_version

    def _extract_cursor_from_next_link(self, next_url: str) -> Optional[str]:
        """
        Extract cursor value from _links.next URL.

        Args:
            next_url: The next URL from API response
            Example: "/wiki/api/v2/spaces?limit=20&cursor=eyJ..."

        Returns:
            Cursor string or None if not found
        """
        try:
            if not next_url:
                return None

            parsed = urlparse(next_url)
            query_params = parse_qs(parsed.query)

            # Get cursor value (could be list if multiple, take first)
            cursor_values = query_params.get("cursor", [])
            if cursor_values:
                return cursor_values[0]

            return None

        except Exception as e:
            self.logger.error(f"❌ Failed to extract cursor from URL '{next_url}': {e}")
            return None

    def _transform_to_space_record_group(
        self,
        space_data: dict[str, Any],
        base_url: Optional[str] = None
    ) -> Optional[RecordGroup]:
        """
        Transform Confluence space data to RecordGroup entity.

        Args:
            space_data: Raw space data from Confluence API
            base_url: Base URL from API response (_links.base)

        Returns:
            RecordGroup object or None if transformation fails
        """
        try:
            space_id = space_data.get("id")
            space_name = space_data.get("name")
            space_description = space_data.get("description", "")
            space_key = space_data.get("key", "")

            if not space_id or not space_name:
                return None

            # Parse timestamps.
            # Cloud v2: top-level "createdAt" field (ISO 8601)
            # Cloud v1 / DC v1: "history.createdDate" (requires expand=history on the spaces call)
            # DC v1 does NOT have a top-level "createdAt"; always use history.createdDate on DC.
            source_created_at = None
            created_at_str = (
                space_data.get("createdAt")                                # Cloud v2 / Cloud v1 (if present)
                or space_data.get("history", {}).get("createdDate")        # Cloud v1 / DC v1 via expand=history
            )
            if created_at_str:
                source_created_at = self._parse_confluence_datetime(created_at_str)

            # Construct web URL: base + webui
            web_url = None
            if base_url:
                webui = space_data.get("_links", {}).get("webui")
                if webui:
                    web_url = f"{base_url}{webui}"

            return RecordGroup(
                org_id=self.data_entities_processor.org_id,
                name=space_name,
                short_name=space_key,
                description=space_description,
                external_group_id=str(space_id),
                connector_name=Connectors.CONFLUENCE_DATA_CENTER,
                connector_id=self.connector_id,
                group_type=RecordGroupType.CONFLUENCE_SPACES,
                web_url=web_url,
                source_created_at=source_created_at,
                source_updated_at=source_created_at,  # Confluence doesn't provide updated timestamp for spaces
            )

        except Exception as e:
            self.logger.error(f"❌ Failed to transform space: {e}")
            return None

    def _transform_to_webpage_record(
        self,
        data: dict[str, Any],
        record_type: RecordType,
        existing_record: Optional[Record] = None,
        api_base_url: Optional[str] = None
    ) -> Optional[WebpageRecord]:
        """
        Unified transform for page/blogpost data to WebpageRecord.

        Args:
            data: Raw data from Confluence API
            record_type: RecordType.CONFLUENCE_PAGE or RecordType.CONFLUENCE_BLOGPOST
            existing_record: Optional existing record to check for updates
            api_base_url: Optional base URL from response level (_links.base)

        Returns:
            WebpageRecord object or None if transformation fails
        """
        # Derive content_type for logging
        if record_type == RecordType.CONFLUENCE_PAGE:
            content_type = "page"
        elif record_type == RecordType.CONFLUENCE_BLOGPOST:
            content_type = "blogpost"
        else:
            content_type = "content"

        try:
            item_id = data.get("id")
            item_title = data.get("title")

            if not item_id or not item_title:
                return None

            # Parse timestamps - v1 vs v2 have different structures
            source_created_at = None
            source_updated_at = None
            version_number = 0

            # Try v2 format first (createdAt at top level)
            created_at_v2 = data.get("createdAt")
            if created_at_v2:
                source_created_at = self._parse_confluence_datetime(created_at_v2)
            else:
                # Fall back to v1 format (history.createdDate)
                history = data.get("history", {})
                created_date = history.get("createdDate")
                if created_date:
                    source_created_at = self._parse_confluence_datetime(created_date)

            # Try v2 format for updated date and version (version.createdAt, version.number)
            version_data = data.get("version", {})
            if isinstance(version_data, dict):
                version_created_at = version_data.get("createdAt")
                if version_created_at:
                    source_updated_at = self._parse_confluence_datetime(version_created_at)
                version_number = version_data.get("number", 0)

            # Fall back to v1 format (history.lastUpdated.when, history.lastUpdated.number)
            if not source_updated_at:
                history = data.get("history", {})
                last_updated = history.get("lastUpdated", {})
                if isinstance(last_updated, dict):
                    updated_when = last_updated.get("when")
                    if updated_when:
                        source_updated_at = self._parse_confluence_datetime(updated_when)
                    if not version_number:
                        version_number = last_updated.get("number", 0)

            # Extract space ID - v2 has spaceId at top level, v1 has space.id
            external_record_group_id = None
            space_id_v2 = data.get("spaceId")  # v2 format
            if space_id_v2:
                external_record_group_id = str(space_id_v2)
            else:
                # v1 format
                space_data = data.get("space", {})
                space_id = space_data.get("id")
                external_record_group_id = str(space_id) if space_id else None

            if not external_record_group_id:
                self.logger.warning(f"{content_type.capitalize()} {item_id} has no space - skipping")
                return None

            # Extract parent ID and type — v2: parentId + parentType; v1: last ancestor (+ type when expanded)
            parent_external_record_id = None
            parent_type_str: Optional[str] = None
            parent_id_v2 = data.get("parentId")
            parent_type_v2 = data.get("parentType")
            if parent_id_v2:
                parent_external_record_id = str(parent_id_v2)
                parent_type_str = parent_type_v2
            else:
                ancestors = data.get("ancestors", [])
                if ancestors and len(ancestors) > 0:
                    direct_parent = ancestors[-1]
                    parent_external_record_id = direct_parent.get("id")
                    parent_type_str = direct_parent.get("type")

            # Construct web URL using unified helper
            links = data.get("_links", {})
            web_url = self._construct_web_url(
                links,
                api_base_url or links.get("base")
            )

            # Cloud v2 / DC v1: parent may be folder, page, or blogpost — map to RecordType for graph edges.
            # When ancestor ``type`` is missing (older expands), fall back to same type as this record.
            parent_record_type = None
            if parent_external_record_id:
                if parent_type_str == "folder":
                    parent_record_type = RecordType.FILE
                elif parent_type_str == "page":
                    parent_record_type = RecordType.CONFLUENCE_PAGE
                elif parent_type_str == "blogpost":
                    parent_record_type = RecordType.CONFLUENCE_BLOGPOST
                else:
                    parent_record_type = record_type

            # Determine record ID and version
            is_new = existing_record is None
            record_id = str(uuid.uuid4()) if is_new else existing_record.id

            # Calculate version based on changes
            record_version = 0
            if not is_new:
                # Check if content changed (version number changed)
                if str(version_number) != existing_record.external_revision_id:
                    record_version = existing_record.version + 1
                else:
                    record_version = existing_record.version

            return WebpageRecord(
                id=record_id,
                org_id=self.data_entities_processor.org_id,
                record_name=item_title,
                record_type=record_type,
                external_record_id=item_id,
                external_revision_id=str(version_number) if version_number else None,
                version=record_version,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.CONFLUENCE_DATA_CENTER,
                connector_id=self.connector_id,
                record_group_type=RecordGroupType.CONFLUENCE_SPACES,
                external_record_group_id=external_record_group_id,
                parent_external_record_id=parent_external_record_id,
                parent_record_type=parent_record_type,
                weburl=web_url,
                mime_type=MimeTypes.HTML.value,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
                is_dependent_node=False,  # Pages are root nodes
                parent_node_id=None,  # Pages have no parent node
            )

        except Exception as e:
            self.logger.error(f"❌ Failed to transform {content_type}: {e}")
            return None

    def _transform_to_attachment_file_record(
        self,
        attachment_data: dict[str, Any],
        parent_external_record_id: str,
        parent_external_record_group_id: Optional[str],
        existing_record: Optional[Record] = None,
        parent_node_id: Optional[str] = None,
        parent_record_type: RecordType = RecordType.WEBPAGE,
        api_base_url: Optional[str] = None
    ) -> Optional[FileRecord]:
        """
        Transform Confluence attachment to FileRecord entity.
        Supports both v1 and v2 API response formats.

        Args:
            attachment_data: Raw attachment data from v1 (children.attachment.results) or v2 API
            parent_external_record_id: Parent page/comment external_record_id
            parent_external_record_group_id: Space ID from parent page
            existing_record: Optional existing record to check for updates
            parent_node_id: Internal record ID of parent page/comment
            parent_record_type: RecordType of parent (WEBPAGE, COMMENT, or INLINE_COMMENT)
            api_base_url: Optional base URL from response level (_links.base)

        Returns:
            FileRecord object or None if transformation fails
        """
        try:
            # Get attachment ID - both v1 and v2 use "id" field with "att" prefix
            attachment_id = attachment_data.get("id")
            if not attachment_id:
                return None
            # Get filename - same field in both v1 and v2
            file_name = attachment_data.get("title")
            if not file_name:
                return None

            # Clean query params from filename if present
            if '?' in file_name:
                file_name = file_name.split('?')[0]

            # Parse timestamps - v1 vs v2 have different structures
            source_created_at = None
            source_updated_at = None
            version_number = 0

            # Try v2 format first (createdAt at top level)
            created_at_v2 = attachment_data.get("createdAt")
            if created_at_v2:
                source_created_at = self._parse_confluence_datetime(created_at_v2)
            else:
                # Fall back to v1 format (history.createdDate)
                history = attachment_data.get("history", {})
                created_date = history.get("createdDate")
                if created_date:
                    source_created_at = self._parse_confluence_datetime(created_date)

            # Try v2 format for updated date and version (version.createdAt, version.number)
            version_data = attachment_data.get("version", {})
            if isinstance(version_data, dict):
                version_created_at = version_data.get("createdAt")
                if version_created_at:
                    source_updated_at = self._parse_confluence_datetime(version_created_at)
                version_number = version_data.get("number", 0)

            # Fall back to v1 format (history.lastUpdated.when, history.lastUpdated.number)
            if not source_updated_at or not version_number:
                history = attachment_data.get("history", {})
                last_updated = history.get("lastUpdated", {})
                if isinstance(last_updated, dict):
                    if not source_updated_at:
                        updated_when = last_updated.get("when")
                        if updated_when:
                            source_updated_at = self._parse_confluence_datetime(updated_when)
                    if not version_number:
                        version_number = last_updated.get("number", 0)

            # Extract file size - v2 has it at top level, v1 in extensions
            file_size = attachment_data.get("fileSize")  # v2 format
            if file_size is None:
                extensions = attachment_data.get("extensions", {})
                file_size = extensions.get("fileSize")  # v1 format

            # Extract mime type - v2 has it at top level (mediaType), v1 in extensions or metadata
            media_type = attachment_data.get("mediaType")  # v2 format
            if not media_type:
                extensions = attachment_data.get("extensions", {})
                media_type = extensions.get("mediaType")  # v1 format (extensions)
            if not media_type:
                metadata = attachment_data.get("metadata", {})
                media_type = metadata.get("mediaType")  # v1 format (metadata)

            mime_type = None
            if media_type:
                # Try to map to MimeTypes enum
                for mime in MimeTypes:
                    if mime.value == media_type:
                        mime_type = mime
                        break

                # If not found in enum, use the raw value
                if not mime_type:
                    mime_type = media_type

            # Extract extension from filename
            extension = None
            if '.' in file_name:
                extension = file_name.split('.')[-1].lower()

            # Construct web URL using helper method
            links = attachment_data.get("_links", {})
            web_url = self._construct_web_url(links, api_base_url or links.get("base"))

            # Determine record ID and version
            is_new = existing_record is None
            attachment_record_id = str(uuid.uuid4()) if is_new else existing_record.id

            # Calculate version based on changes
            record_version = 0
            if not is_new:
                # Check if content changed (version number changed)
                if str(version_number) != existing_record.external_revision_id:
                    record_version = existing_record.version + 1
                else:
                    record_version = existing_record.version

            return FileRecord(
                id=attachment_record_id,
                org_id=self.data_entities_processor.org_id,
                record_name=file_name,
                record_type=RecordType.FILE,
                external_record_id=attachment_id,
                external_revision_id=str(version_number) if version_number else None,
                version=record_version,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.CONFLUENCE_DATA_CENTER,
                connector_id=self.connector_id,
                mime_type=mime_type,
                parent_external_record_id=parent_external_record_id,
                parent_record_type=parent_record_type,
                external_record_group_id=parent_external_record_group_id,
                record_group_type=RecordGroupType.CONFLUENCE_SPACES,
                weburl=web_url,
                is_file=True,
                size_in_bytes=file_size,
                extension=extension,
                source_created_at=source_created_at,
                source_updated_at=source_updated_at,
                is_dependent_node=True,  # Attachments are dependent nodes
                parent_node_id=parent_node_id,  # Internal record ID of parent page/comment
            )

        except Exception as e:
            self.logger.error(f"❌ Failed to transform attachment: {e}")
            return None

    async def _process_webpage_with_update(
        self,
        data: dict[str, Any],
        record_type: RecordType,
        existing_record: Optional[Record],
        permissions: list[Permission],
        api_base_url: Optional[str] = None
    ) -> RecordUpdate:
        """Process webpage with change detection.

        Args:
            data: Raw data from Confluence API
            record_type: RecordType.CONFLUENCE_PAGE or RecordType.CONFLUENCE_BLOGPOST
            existing_record: Existing record from database (if any)
            permissions: Permissions for the record
            api_base_url: Optional base URL from response level (_links.base)

        Returns:
            RecordUpdate object with change tracking
        """
        # Transform with existing record context
        webpage_record = self._transform_to_webpage_record(
            data, record_type, existing_record, api_base_url
        )

        if not webpage_record:
            return RecordUpdate(
                record=None,
                is_new=False,
                is_updated=False,
                is_deleted=False,
                content_changed=False,
                metadata_changed=False,
                permissions_changed=False,
                new_permissions=None,
                external_record_id=None
            )

        # Detect changes
        is_new = existing_record is None
        content_changed = False
        metadata_changed = False

        if not is_new:
            # Check if version changed (content update)
            version_data = data.get("version", {})
            if isinstance(version_data, dict):
                current_version = version_data.get("number")
            else:
                current_version = version_data

            if str(current_version) != existing_record.external_revision_id:
                content_changed = True

            # Check if parent changed (moved between pages / folders)
            current_parent_v2 = data.get("parentId")
            current_parent_v1 = None
            ancestors = data.get("ancestors", [])
            if ancestors and len(ancestors) > 0:
                current_parent_v1 = ancestors[-1].get("id")
            current_parent = current_parent_v2 or current_parent_v1

            if current_parent != existing_record.parent_external_record_id:
                metadata_changed = True

        return RecordUpdate(
            record=webpage_record,
            is_new=is_new,
            is_updated=content_changed or metadata_changed,
            is_deleted=False,
            content_changed=content_changed,
            metadata_changed=metadata_changed,
            permissions_changed=bool(permissions),
            new_permissions=permissions,
            external_record_id=webpage_record.external_record_id
        )

    def _parse_confluence_datetime(self, datetime_str: str) -> Optional[int]:
        """
        Parse Confluence datetime string to epoch timestamp in milliseconds.

        Confluence format: "2025-11-13T07:51:50.526Z" (ISO 8601 with Z suffix)

        Args:
            datetime_str: Confluence datetime string

        Returns:
            int: Epoch timestamp in milliseconds or None if parsing fails
        """
        try:
            # Parse ISO 8601 format: '2025-11-13T07:51:50.526Z'
            # Replace 'Z' with '+00:00' for proper ISO format parsing
            dt = datetime.fromisoformat(datetime_str.replace('Z', '+00:00'))
            return int(dt.timestamp() * 1000)
        except Exception as e:
            self.logger.warning(f"Failed to parse datetime '{datetime_str}': {e}")
            return None

    async def get_signed_url(self, record: Record) -> str:
        """Get a signed URL for a record (not implemented for Confluence)."""
        # Confluence attachments use API access, not signed URLs
        return ""

    async def stream_record(self, record: Record) -> StreamingResponse:
        """
        Stream record content (page HTML, comment HTML, or attachment file) from Confluence.

        For pages (WebpageRecord): Fetches HTML content from page body.export_view
        For comments (CommentRecord): Fetches HTML content from comment body.storage.value
        For attachments (FileRecord): Downloads file from attachment download URL

        Args:
            record: The record to stream (page, comment, or attachment)

        Returns:
            StreamingResponse: Streaming response with page/comment HTML or file content
        """
        try:
            self.logger.info(f"📥 Streaming record: {record.record_name} ({record.external_record_id})")

            if record.record_type in [RecordType.CONFLUENCE_PAGE, RecordType.CONFLUENCE_BLOGPOST]:
                # Page or blogpost - fetch HTML content based on record type
                html_content = await self._fetch_page_content(record.external_record_id, record.record_type)

                async def generate_page() -> AsyncGenerator[bytes, None]:
                    yield html_content.encode('utf-8')

                return StreamingResponse(
                    generate_page(),
                    media_type='text/html',
                    headers={"Content-Disposition": f'inline; filename="{record.external_record_id}.html"'}
                )

            elif record.record_type in [RecordType.COMMENT, RecordType.INLINE_COMMENT]:
                # Comment - fetch HTML content based on comment type
                html_content = await self._fetch_comment_content(record)

                async def generate_comment() -> AsyncGenerator[bytes, None]:
                    yield html_content.encode('utf-8')

                return StreamingResponse(
                    generate_comment(),
                    media_type='text/html',
                    headers={"Content-Disposition": f'inline; filename="comment_{record.external_record_id}.html"'}
                )

            elif record.record_type == RecordType.FILE:
                filename = record.record_name or f"{record.external_record_id}"
                return create_stream_record_response(
                    self._fetch_attachment_content(record),
                    filename=filename,
                    mime_type=record.mime_type,
                    fallback_filename=f"record_{record.id}"
                )

            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported record type for streaming: {record.record_type}"
                )

        except HTTPException:
            raise  # Re-raise HTTP exceptions as-is
        except Exception as e:
            self.logger.error(f"❌ Failed to stream record: {e}", exc_info=True)
            raise HTTPException(
                status_code=500, detail=f"Failed to stream record: {str(e)}"
            )

    async def _fetch_page_content(self, page_id: str, record_type: RecordType) -> str:
        """
        Fetch page or blogpost HTML from Confluence using v1 unified content API.

        Args:
            page_id: The page or blogpost ID
            record_type: RecordType.CONFLUENCE_PAGE or RecordType.CONFLUENCE_BLOGPOST

        Returns:
            str: HTML content of the page/blogpost

        Raises:
            HTTPException: If content not found or fetch fails
        """
        try:
            self.logger.debug(f"Fetching content for {page_id} (type: {record_type})")

            if record_type not in (
                RecordType.CONFLUENCE_PAGE,
                RecordType.CONFLUENCE_BLOGPOST,
            ):
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported record type: {record_type}"
                )

            datasource = await self._get_fresh_datasource()
            response = await call_with_retry(
                lambda: datasource.get_content_v1(
                    content_id=str(page_id),
                    expand=CONTENT_V1_SINGLE_EXPAND,
                ),
                logger=self.logger,
                label=f"content/{page_id}",
            )

            # Check response
            if not response or response.status != HttpStatusCode.SUCCESS.value:
                raise HTTPException(
                    status_code=404,
                    detail=f"Content not found: {page_id}"
                )

            response_data = response.json()
            body = response_data.get("body", {}) or {}
            export_view = body.get("export_view") or {}
            html_content = str(export_view.get("value") or "")

            if not html_content:
                self.logger.warning(f"Content {page_id} has no body")
                html_content = "<p>No content available</p>"

            async def _download(url: str):
                return await datasource.fetch_authenticated_binary(url)

            html_content = await prepare_streaming_html(
                html_content,
                response_data,
                _download,
                logger=self.logger,
            )

            self.logger.debug(f"✅ Fetched {len(html_content)} bytes of HTML for {page_id}")
            return html_content

        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to fetch content: {e}", exc_info=True)
            # Extract original HTTP status if available
            status_code = 500
            if isinstance(e, httpx.HTTPStatusError):
                status_code = e.response.status_code
            detail = str(e)
            raise HTTPException(status_code=status_code, detail=detail) from e

    async def _fetch_comment_content(self, record: CommentRecord) -> str:
        """
        Fetch comment HTML content from Confluence based on record type.

        Args:
            record: CommentRecord with external_record_id and record_type

        Returns:
            str: HTML content of the comment

        Raises:
            HTTPException: If comment not found or fetch fails
        """
        try:
            comment_id = record.external_record_id
            self.logger.debug(f"Fetching comment content for {comment_id} (type: {record.record_type})")

            datasource = await self._get_fresh_datasource()

            if record.record_type not in (RecordType.COMMENT, RecordType.INLINE_COMMENT):
                raise HTTPException(
                    status_code=400,
                    detail=f"Unsupported comment type: {record.record_type}"
                )

            response = await call_with_retry(
                lambda: datasource.get_content_v1(
                    content_id=str(comment_id),
                    expand=CONTENT_V1_COMMENT_EXPAND,
                ),
                logger=self.logger,
                label=f"comment/{comment_id}",
            )

            # Check response
            if not response or response.status != HttpStatusCode.SUCCESS.value:
                raise HTTPException(
                    status_code=404,
                    detail=f"Comment not found: {comment_id}"
                )

            response_data = response.json()

            body = response_data.get("body", {}) or {}
            export_view = body.get("export_view") or {}
            html_content = str(export_view.get("value") or "")

            if not html_content:
                self.logger.warning(f"Comment {comment_id} has no content")
                html_content = "<p>No content available</p>"

            async def _download(url: str):
                return await datasource.fetch_authenticated_binary(url)

            html_content = await prepare_streaming_html(
                html_content,
                response_data,
                _download,
                title=record.record_name,
                logger=self.logger,
            )

            self.logger.debug(f"✅ Fetched {len(html_content)} bytes of HTML for comment {comment_id}")
            return html_content

        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"Failed to fetch comment content: {e}", exc_info=True)
            # Extract original HTTP status if available
            status_code = 500
            if isinstance(e, httpx.HTTPStatusError):
                status_code = e.response.status_code
            detail = str(e)
            raise HTTPException(status_code=status_code, detail=detail) from e

    async def _fetch_attachment_content(self, record: Record) -> AsyncGenerator[bytes, None]:
        """
        Stream attachment file content from Confluence.

        Downloads the full file with retry before yielding so transient upstream
        failures never corrupt a partially-sent HTTP response.

        Args:
            record: Record with external_record_id and parent_external_record_id

        Yields:
            bytes: File content in 8KB chunks

        Raises:
            HTTPException: If attachment not found or download fails
        """
        attachment_id = record.external_record_id
        parent_content_id = record.parent_external_record_id

        if not attachment_id:
            raise HTTPException(
                status_code=400,
                detail=f"No attachment ID available for record {record.id}"
            )

        if not parent_content_id:
            raise HTTPException(
                status_code=400,
                detail=f"No parent content ID available for attachment {attachment_id}"
            )

        try:
            datasource = await self._get_fresh_datasource()

            meta_response = await call_with_retry(
                lambda: datasource.get_content_v1(
                    content_id=str(attachment_id),
                    expand="",
                ),
                logger=self.logger,
                label=f"attachment_meta/{attachment_id}",
            )

            if not meta_response or meta_response.status != HttpStatusCode.SUCCESS.value:
                raise HTTPException(
                    status_code=404,
                    detail=f"Attachment {attachment_id} not found at source",
                )

            download_path = (meta_response.json() or {}).get("_links", {}).get("download")
            if not download_path:
                raise HTTPException(
                    status_code=404,
                    detail=f"No download link for attachment {attachment_id}",
                )

            async def _read_attachment_bytes() -> bytes:
                chunks: list[bytes] = []
                async for chunk in datasource.download_attachment_from_link(download_path):
                    chunks.append(chunk)
                return b"".join(chunks)

            data = await call_with_retry(
                _read_attachment_bytes,
                logger=self.logger,
                label=f"attachment/{attachment_id}",
            )
        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(
                f"Failed to download attachment {attachment_id}: {e}",
                exc_info=True,
            )
            # Extract original HTTP status if available
            status_code = 500
            if isinstance(e, httpx.HTTPStatusError):
                status_code = e.response.status_code
            detail = str(e)
            raise HTTPException(status_code=status_code, detail=detail) from e

        chunk_size = 8192
        for offset in range(0, len(data), chunk_size):
            yield data[offset : offset + chunk_size]

    async def run_incremental_sync(self) -> None:
        """Run incremental sync (delegates to full sync)."""
        await self.run_sync()

    async def reindex_records(self, records: list[Record]) -> None:
        """Reindex a list of Confluence records.

        This method:
        1. For each record, checks if it has been updated at the source
        2. If updated, upserts the record in DB
        3. Publishes reindex events for all records via data_entities_processor

        Args:
            records: List of properly typed Record instances (WebpageRecord, FileRecord, CommentRecord, etc.)
        """
        try:
            if not records:
                self.logger.info("No records to reindex")
                return

            self.logger.info(f"Starting reindex for {len(records)} Confluence records")

            # Ensure external clients are initialized
            if not self.external_client or not self.data_source:
                self.logger.error("External API clients not initialized. Call init() first.")
                raise Exception("External API clients not initialized. Call init() first.")

            # Check records at source for updates
            org_id = self.data_entities_processor.org_id
            updated_records = []
            non_updated_records = []
            for record in records:
                try:
                    updated_record_data = await self._check_and_fetch_updated_record(org_id, record)
                    if updated_record_data:
                        updated_record, permissions = updated_record_data
                        updated_records.append((updated_record, permissions))
                    else:
                        non_updated_records.append(record)
                except Exception as e:
                    self.logger.error(f"Error checking record {record.id} at source: {e}")
                    continue

            # Update DB and publish updateRecord events for records that changed at source
            if updated_records:
                for updated_record, permissions in updated_records:
                    # Update record content and publish updateRecord event
                    await self.data_entities_processor.on_record_content_update(updated_record)

                    # Update permissions if they exist
                    if permissions:
                        await self.data_entities_processor.on_updated_record_permissions(updated_record, permissions)

                self.logger.info(f"Published update events for {len(updated_records)} records that changed at source")

            # Publish reindex events for non-updated records
            if non_updated_records:
                await self.data_entities_processor.reindex_existing_records(non_updated_records)
                self.logger.info(f"Published reindex events for {len(non_updated_records)} non-updated records")
        except Exception as e:
            self.logger.error(f"Error during Confluence reindex: {e}", exc_info=True)
            raise

    async def _check_and_fetch_updated_record(
        self, org_id: str, record: Record
    ) -> Optional[tuple[Record, list[Permission]]]:
        """Fetch record from source and return data for reindexing.

        Args:
            org_id: Organization ID
            record: Record to check

        Returns:
            Tuple of (Record, List[Permission]) if updated, None if not updated or error
        """
        try:
            if record.record_type == RecordType.CONFLUENCE_PAGE:
                return await self._check_and_fetch_updated_page(org_id, record)
            elif record.record_type == RecordType.CONFLUENCE_BLOGPOST:
                return await self._check_and_fetch_updated_blogpost(org_id, record)
            elif record.record_type in [RecordType.COMMENT, RecordType.INLINE_COMMENT]:
                return await self._check_and_fetch_updated_comment(org_id, record)
            elif record.record_type == RecordType.FILE:
                return await self._check_and_fetch_updated_attachment(org_id, record)
            else:
                self.logger.warning(f"Unsupported record type for reindex: {record.record_type}")
                return None

        except Exception as e:
            self.logger.error(f"Error checking record {record.id} at source: {e}")
            return None

    async def _check_and_fetch_updated_page(
        self, org_id: str, record: Record
    ) -> Optional[tuple[Record, list[Permission]]]:
        """Fetch page from source for reindexing."""
        try:
            page_id = record.external_record_id

            # Fetch page from source using v1 unified content API
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_content_v1(
                content_id=str(page_id),
                expand=CONTENT_V1_SINGLE_EXPAND,
            )

            if not response or response.status != HttpStatusCode.SUCCESS.value:
                self.logger.warning(f"Page {page_id} not found at source, may have been deleted")
                return None

            page_data = response.json()

            # Check if version changed
            current_version = page_data.get("version", {}).get("number")
            if current_version is None:
                self.logger.warning(f"Page {page_id} has no version number")
                return None

            # Compare versions
            if record.external_revision_id and str(current_version) == record.external_revision_id:
                self.logger.debug(f"Page {page_id} has not changed at source (version {current_version})")
                return None

            self.logger.info(f"Page {page_id} has changed at source (version {record.external_revision_id} -> {current_version})")

            # Transform page to WebpageRecord with existing record context
            webpage_record = self._transform_to_webpage_record(
                page_data,
                RecordType.CONFLUENCE_PAGE,
                existing_record=record
            )
            if not webpage_record:
                return None

            # Personal connector: all records inherit permissions from space ConnectorGroup
            permissions = []

            return (webpage_record, permissions)

        except Exception as e:
            self.logger.error(f"Error fetching page {record.external_record_id}: {e}")
            return None

    async def _check_and_fetch_updated_blogpost(
        self, org_id: str, record: Record
    ) -> Optional[tuple[Record, list[Permission]]]:
        """Fetch blogpost from source for reindexing."""
        try:
            blogpost_id = record.external_record_id

            datasource = await self._get_fresh_datasource()
            response = await datasource.get_content_v1(
                content_id=str(blogpost_id),
                expand=CONTENT_V1_SINGLE_EXPAND,
            )

            if not response or response.status != HttpStatusCode.SUCCESS.value:
                self.logger.warning(f"Blogpost {blogpost_id} not found at source, may have been deleted")
                return None

            blogpost_data = response.json()

            # Check if version changed
            current_version = blogpost_data.get("version", {}).get("number")
            if current_version is None:
                self.logger.warning(f"Blogpost {blogpost_id} has no version number")
                return None

            # Compare versions
            if record.external_revision_id and str(current_version) == record.external_revision_id:
                self.logger.debug(f"Blogpost {blogpost_id} has not changed at source (version {current_version})")
                return None

            self.logger.info(f"Blogpost {blogpost_id} has changed at source (version {record.external_revision_id} -> {current_version})")

            # Transform blogpost to WebpageRecord with existing record context
            webpage_record = self._transform_to_webpage_record(
                blogpost_data,
                RecordType.CONFLUENCE_BLOGPOST,
                existing_record=record
            )
            if not webpage_record:
                return None

            # Personal connector: all records inherit permissions from space ConnectorGroup
            permissions = []

            return (webpage_record, permissions)

        except Exception as e:
            self.logger.error(f"Error fetching blogpost {record.external_record_id}: {e}")
            return None

    async def _check_and_fetch_updated_comment(
        self, org_id: str, record: Record
    ) -> Optional[tuple[Record, list[Permission]]]:
        """Fetch comment from source for reindexing."""
        try:
            comment_id = record.external_record_id
            is_inline = record.record_type == RecordType.INLINE_COMMENT

            datasource = await self._get_fresh_datasource()
            response = await datasource.get_content_v1(
                content_id=str(comment_id),
                expand=CONTENT_V1_COMMENT_EXPAND,
            )

            if not response or response.status != HttpStatusCode.SUCCESS.value:
                self.logger.warning(f"Comment {comment_id} not found at source, may have been deleted")
                return None

            response_data = response.json()
            comment_data = response_data

            response_links = response_data.get("_links", {})
            base_url = response_links.get("base")

            # Check if version changed
            current_version = comment_data.get("version", {}).get("number")
            if current_version is None:
                self.logger.warning(f"Comment {comment_id} has no version number")
                return None

            # Compare versions using external_revision_id
            if record.external_revision_id and str(current_version) == record.external_revision_id:
                self.logger.debug(f"Comment {comment_id} has not changed at source (version {current_version})")
                return None

            self.logger.info(f"Comment {comment_id} has changed at source (version {record.external_revision_id} -> {current_version})")

            comment_type = "inline" if is_inline else "footer"
            normalized = self._normalize_v1_comment_for_transform(comment_data, comment_type)

            container = comment_data.get("container") or {}
            page_id = None
            if container.get("type") in ("page", "blogpost"):
                page_id = str(container.get("id")) if container.get("id") else None
            if not page_id and record.parent_record_type == RecordType.WEBPAGE:
                page_id = record.parent_external_record_id

            parent_comment_id: Optional[str] = None
            if record.parent_record_type == RecordType.COMMENT:
                parent_comment_id = record.parent_external_record_id
            else:
                ancestors = comment_data.get("ancestors") or []
                cid_str = str(comment_data.get("id"))
                for anc in reversed(ancestors):
                    if anc.get("type") == "comment" and str(anc.get("id")) != cid_str:
                        parent_comment_id = str(anc.get("id"))
                        break

            if not page_id:
                self.logger.warning(f"Comment {comment_id}: could not resolve parent page id")
                return None

            parent_node_id = None
            parent_page_record = await self.data_entities_processor.get_record_by_external_id(
                connector_id=self.connector_id,
                external_record_id=page_id,
            )
            if parent_page_record:
                parent_node_id = parent_page_record.id

            comment_record = self._transform_to_comment_record(
                normalized,
                page_id,
                record.external_record_group_id,
                comment_type,
                parent_comment_id,
                base_url=base_url,
                existing_record=record,
                parent_node_id=parent_node_id
            )

            if not comment_record:
                return None

            # Comments inherit permissions from parent page - fetch page permissions
            permissions = []  # Personal connector: inherit from space

            return (comment_record, permissions)

        except Exception as e:
            self.logger.error(f"Error fetching comment {record.external_record_id}: {e}")
            return None

    async def _check_and_fetch_updated_attachment(
        self, org_id: str, record: Record
    ) -> Optional[tuple[Record, list[Permission]]]:
        """Fetch attachment from source for reindexing."""
        try:
            attachment_id = record.external_record_id
            parent_content_id = record.parent_external_record_id

            if not parent_content_id:
                self.logger.warning(f"Attachment {attachment_id} has no parent content ID")
                return None

            # Fetch attachment metadata from source using v1 unified content API
            datasource = await self._get_fresh_datasource()
            response = await datasource.get_content_v1(
                content_id=str(attachment_id),
                expand=CONTENT_V1_ATTACHMENT_EXPAND,
            )

            if not response or response.status != HttpStatusCode.SUCCESS.value:
                self.logger.warning(f"Attachment {attachment_id} not found at source, may have been deleted")
                return None

            attachment_data = response.json()

            # Check if version changed
            current_version = attachment_data.get("version", {}).get("number")
            if current_version is None:
                self.logger.warning(f"Attachment {attachment_id} has no version number")
                return None

            # Compare versions using external_revision_id
            if record.external_revision_id and str(current_version) == record.external_revision_id:
                self.logger.debug(f"Attachment {attachment_id} has not changed at source (version {current_version})")
                return None

            self.logger.info(f"Attachment {attachment_id} has changed at source (version {record.external_revision_id} -> {current_version})")

            # Resolve parent context (handles both page and comment parents)
            parent_context = await self._resolve_attachment_parent_context(record, attachment_data)
            if not parent_context:
                return None
            
            immediate_parent_id, parent_record_type, parent_node_id, page_id_for_permissions = parent_context

            # Get space_id from existing record
            parent_space_id = record.external_record_group_id

            # Transform attachment to FileRecord with existing record context
            attachment_record = self._transform_to_attachment_file_record(
                attachment_data,
                immediate_parent_id,
                parent_space_id,
                existing_record=record,
                parent_node_id=parent_node_id,
                parent_record_type=parent_record_type
            )

            if not attachment_record:
                return None

            # Attachments inherit permissions from parent page - fetch page permissions
            permissions = []  # Personal connector: inherit from space

            return (attachment_record, permissions)

        except Exception as e:
            self.logger.error(f"Error fetching attachment {record.external_record_id}: {e}")
            return None

    async def cleanup(self) -> None:
        """Cleanup resources."""
        self.logger.info("Cleaning up Confluence connector resources")
        # Add cleanup logic if needed

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> FilterOptionsResponse:
        """
        Get dynamic filter options for Confluence filters with cursor-based pagination.

        Supports:
        - space_keys: All available Confluence spaces
        - page_ids: Pages (with CQL fuzzy search when search term provided)
        - blogpost_ids: Blogposts (with CQL fuzzy search when search term provided)

        Args:
            filter_key: Filter field name
            page: Page number (for API compatibility, not used with cursor)
            limit: Items per page
            search: Search text to filter options (uses CQL fuzzy matching)
            cursor: Cursor for pagination (Confluence uses cursor-based pagination)

        Returns:
            FilterOptionsResponse with options and pagination metadata
        """
        if filter_key == "space_keys":
            return await self._get_space_options(page, limit, search, cursor)
        elif filter_key == "page_ids":
            return await self._get_page_options(page, limit, search, cursor)
        elif filter_key == "blogpost_ids":
            return await self._get_blogpost_options(page, limit, search, cursor)
        else:
            raise ValueError(f"Unsupported filter key: {filter_key}")

    async def _get_space_options(
        self,
        page: int,
        limit: int,
        search: Optional[str],
        cursor: Optional[str]
    ) -> FilterOptionsResponse:
        """Fetch available Confluence spaces with pagination.

        Uses CQL search for fuzzy matching when ``search`` is provided,
        otherwise lists all spaces via v1 REST. Both paths use
        ``_pagination_token_from_next_link`` so they work on Cloud v1 (cursor)
        and Data Center (``start`` offset) without branching.
        """
        # Get datasource backed by configured API client
        datasource = await self._get_fresh_datasource()

        spaces_list = []
        next_cursor = None

        if search:
            # Determine pagination parameters based on token type
            start_offset, cursor_token = self._split_pagination_token(cursor)
            
            # Use CQL search for fuzzy matching on space name/key
            spaces_response = await datasource.search_spaces_cql(
                search_term=search,
                limit=limit,
                start=start_offset,
                cursor=cursor_token,
            )

            if not spaces_response or spaces_response.status != HttpStatusCode.SUCCESS.value:
                raise RuntimeError(
                    f"Failed to search spaces: HTTP {spaces_response.status if spaces_response else 'No response'}"
                )

            response_data = spaces_response.json()

            # CQL search returns results with nested 'space' object
            # Note: CQL response space object has key and name but NOT id
            for result in response_data.get("results", []):
                space = result.get("space", {})
                space_key = space.get("key")
                space_name = space.get("name") or space.get("title")

                # CQL response doesn't include space id, so we use key as identifier
                if space_key and space_name:
                    spaces_list.append({
                        "id": space_key,  # Use key as id since CQL doesn't return id
                        "key": space_key,
                        "name": space_name
                    })

            next_cursor_link = response_data.get("_links", {}).get("next")
            next_cursor = self._pagination_token_from_next_link(next_cursor_link)
        else:
            # v1 REST: list spaces (offset via ``start`` in ``_links.next``)
            try:
                start_offset = int(cursor) if cursor is not None else 0
            except (TypeError, ValueError):
                start_offset = 0

            spaces_response = await datasource.get_spaces_v1(
                limit=limit,
                start=start_offset,
                status="current",
                expand="",
            )

            if not spaces_response or spaces_response.status != HttpStatusCode.SUCCESS.value:
                raise RuntimeError(
                    f"Failed to fetch spaces: HTTP {spaces_response.status if spaces_response else 'No response'}"
                )

            response_data = spaces_response.json()
            spaces_list = response_data.get("results", [])

            next_cursor_link = response_data.get("_links", {}).get("next")
            next_cursor = self._pagination_token_from_next_link(next_cursor_link)

        # Convert to FilterOption objects
        # Use key as id since the filter is "space_keys" and backend expects keys
        options = [
            FilterOption(
                id=space.get("key"),  # Frontend will use this value, backend expects keys
                label=space.get("name")
            )
            for space in spaces_list
            if space.get("key") and space.get("name")
        ]

        # Return success response
        return FilterOptionsResponse(
            success=True,
            options=options,
            page=page,
            limit=limit,
            has_more=next_cursor is not None,
            cursor=next_cursor
        )

    async def _get_page_options(
        self,
        page: int,
        limit: int,
        search: Optional[str],
        cursor: Optional[str]
    ) -> FilterOptionsResponse:
        """Fetch pages with offset-based pagination (v1 ``start``).

        Uses CQL search for fuzzy title matching when ``search`` is provided,
        otherwise lists all pages via v1 ``content/search``. Both Cloud v1 and
        Data Center return ``start=N`` in ``_links.next`` for these endpoints,
        so ``cursor`` here is the string form of an integer offset.
        """
        # Get datasource backed by configured API client
        datasource = await self._get_fresh_datasource()

        pages_list = []
        next_cursor = None

        if search:
            # Determine pagination parameters based on token type
            start_offset, cursor_token = self._split_pagination_token(cursor)
            
            # Use CQL search for fuzzy title matching
            pages_response = await datasource.search_pages_cql(
                search_term=search,
                limit=limit,
                start=start_offset,
                cursor=cursor_token,
            )

            if not pages_response or pages_response.status != HttpStatusCode.SUCCESS.value:
                raise RuntimeError(
                    f"Failed to search pages: HTTP {pages_response.status if pages_response else 'No response'}"
                )

            response_data = pages_response.json()

            # CQL search returns results with nested 'content' object
            for result in response_data.get("results", []):
                content = result.get("content", {})
                if content.get("id") and content.get("title") and content.get("type") == "page":
                    pages_list.append(content)

            next_cursor_link = response_data.get("_links", {}).get("next")
            next_cursor = self._pagination_token_from_next_link(next_cursor_link)
        else:
            # v1 CQL content search — all pages (no space filter)
            # NOTE: If performance is an issue, add space key filter to scope results
            try:
                start_offset = int(cursor) if cursor is not None else None
            except (TypeError, ValueError):
                start_offset = None

            pages_response = await datasource.get_pages_v1(
                start=start_offset,
                limit=limit,
                order_by="title",
                sort_order="asc",
                expand="version",
            )

            if not pages_response or pages_response.status != HttpStatusCode.SUCCESS.value:
                raise RuntimeError(
                    f"Failed to fetch pages: HTTP {pages_response.status if pages_response else 'No response'}"
                )

            response_data = pages_response.json()
            pages_list = response_data.get("results", [])

            next_cursor_link = response_data.get("_links", {}).get("next")
            next_cursor = self._pagination_token_from_next_link(next_cursor_link)

        # Convert to FilterOption objects
        options = [
            FilterOption(
                id=p.get("id"),
                label=p.get('title')
            )
            for p in pages_list
            if p.get("id") and p.get("title")
        ]

        return FilterOptionsResponse(
            success=True,
            options=options,
            page=page,
            limit=limit,
            has_more=next_cursor is not None,
            cursor=next_cursor
        )

    async def _get_blogpost_options(
        self,
        page: int,
        limit: int,
        search: Optional[str],
        cursor: Optional[str]
    ) -> FilterOptionsResponse:
        """Fetch blogposts with offset-based pagination (v1 ``start``).

        Uses CQL search for fuzzy title matching when ``search`` is provided,
        otherwise lists all blogposts via v1 ``content/search``. Both Cloud v1
        and Data Center return ``start=N`` in ``_links.next`` for these
        endpoints, so ``cursor`` here is the string form of an integer offset.
        """
        # Get datasource backed by configured API client
        datasource = await self._get_fresh_datasource()

        blogposts_list = []
        next_cursor = None

        if search:
            # Determine pagination parameters based on token type
            start_offset, cursor_token = self._split_pagination_token(cursor)
            
            # Use CQL search for fuzzy title matching
            blogposts_response = await datasource.search_blogposts_cql(
                search_term=search,
                limit=limit,
                start=start_offset,
                cursor=cursor_token,
            )

            if not blogposts_response or blogposts_response.status != HttpStatusCode.SUCCESS.value:
                raise RuntimeError(
                    f"Failed to search blogposts: HTTP {blogposts_response.status if blogposts_response else 'No response'}"
                )

            response_data = blogposts_response.json()

            # CQL search returns results with nested 'content' object
            for result in response_data.get("results", []):
                content = result.get("content", {})
                if content.get("id") and content.get("title") and content.get("type") == "blogpost":
                    blogposts_list.append(content)

            next_cursor_link = response_data.get("_links", {}).get("next")
            next_cursor = self._pagination_token_from_next_link(next_cursor_link)
        else:
            try:
                start_offset = int(cursor) if cursor is not None else None
            except (TypeError, ValueError):
                start_offset = None

            blogposts_response = await datasource.get_blogposts_v1(
                start=start_offset,
                limit=limit,
                order_by="title",
                sort_order="asc",
                expand="version",
            )

            if not blogposts_response or blogposts_response.status != HttpStatusCode.SUCCESS.value:
                raise RuntimeError(
                    f"Failed to fetch blogposts: HTTP {blogposts_response.status if blogposts_response else 'No response'}"
                )

            response_data = blogposts_response.json()
            blogposts_list = response_data.get("results", [])

            next_cursor_link = response_data.get("_links", {}).get("next")
            next_cursor = self._pagination_token_from_next_link(next_cursor_link)

        # Convert to FilterOption objects
        options = [
            FilterOption(
                id=bp.get("id"),
                label=bp.get('title')
            )
            for bp in blogposts_list
            if bp.get("id") and bp.get("title")
        ]

        return FilterOptionsResponse(
            success=True,
            options=options,
            page=page,
            limit=limit,
            has_more=next_cursor is not None,
            cursor=next_cursor
        )

    async def handle_webhook_notification(self, notification: dict) -> None:
        """Handle webhook notifications (not implemented)."""
        self.logger.warning("Webhook notifications not yet supported for Confluence")
        pass

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        scope: str,
        created_by: str,
    ) -> "ConfluenceDataCenterPersonalConnector":
        """Factory method to create a Confluence Data Center Personal connector instance."""
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
            connector_id,
            scope,
            created_by,
        )
