import asyncio
import json
import mimetypes
import httpx
import re
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from logging import Logger
from typing import Any, AsyncGenerator, DefaultDict, Dict, List, Optional, Set, Tuple
from urllib.parse import quote
from uuid import uuid4
from bs4 import BeautifulSoup  # pyright: ignore[reportMissingModuleSource]
from html_to_markdown import convert as html_to_markdown  # type: ignore[import-untyped]

import aiohttp
import html2text

from pydantic import HttpUrl

from app.utils.oauth_config import fetch_oauth_config_by_id
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    SyncPoint,
)
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
    OAuthScopeConfig,
)
from app.connectors.core.registry.connector_builder import (
    AuthField,
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
    DocumentationLink,
    SyncStrategy,
)
from app.connectors.core.registry.filters import (
    FilterCollection,
    IndexingFilterKey,
    load_connector_filters,
)
from app.connectors.sources.salesforce.common.apps import SalesforceApp
from app.models.entities import (
    AppRole,
    AppUser,
    AppUserGroup,
    DealRecord,
    FileRecord,
    Org,
    Person,
    ProductRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    TicketRecord,
)
from collections import defaultdict

from app.models.blocks import (
    BlockGroup,
    BlockGroupChildren,
    BlocksContainer,
    ChildRecord,
    ChildType,
    DataFormat,
    GroupSubType,
    GroupType,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.salesforce.salesforce import (
    SalesforceClient,
    SalesforceConfig,
    SalesforceResponse,
)
from app.connectors.core.base.token_service.startup_service import startup_service
from app.sources.external.salesforce.salesforce_data_source import SalesforceDataSource
from app.utils.streaming import create_stream_record_response
from app.utils.time_conversion import get_epoch_timestamp_in_ms, parse_timestamp


@dataclass
class RecordUpdate:
    """Track updates to a record (e.g. Salesforce file)."""
    record: Optional[FileRecord]
    is_new: bool
    is_updated: bool
    is_deleted: bool
    metadata_changed: bool
    content_changed: bool
    permissions_changed: bool
    old_permissions: Optional[List[Permission]] = None
    new_permissions: Optional[List[Permission]] = None
    external_record_id: Optional[str] = None


# Sync point keys for incremental sync (time-based)
USERS_SYNC_POINT_KEY = "users"
ROLES_SYNC_POINT_KEY = "roles"
USER_GROUPS_SYNC_POINT_KEY = "user_groups"
CONTACTS_SYNC_POINT_KEY = "contacts"
LEADS_SYNC_POINT_KEY = "leads"
PRODUCTS_SYNC_POINT_KEY = "products"
SOLD_IN_SYNC_POINT_KEY = "sold_in"
DEALS_SYNC_POINT_KEY = "deals"
CASES_SYNC_POINT_KEY = "cases"
TASKS_SYNC_POINT_KEY = "tasks"
FILES_SYNC_POINT_KEY = "files"
ACCOUNTS_SYNC_POINT_KEY = "accounts"
DISCUSSIONS_SYNC_POINT_KEY = "discussions"

# Permission hierarchy for comparing and upgrading permissions
# Higher number = higher permission level
PERMISSION_HIERARCHY = {
    "READER": 1,
    "COMMENTER": 2,
    "WRITER": 3,
    "OWNER": 4,
}

def _epoch_ms_to_soql_datetime(epoch_ms: int) -> str:
    """Convert epoch milliseconds to Salesforce SOQL datetime string (ISO 8601)."""
    dt = datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")

def _parse_salesforce_timestamp(value: Optional[Any]) -> Optional[int]:
    """Parse a Salesforce date string (normalizes +0000 to +00:00) to epoch ms. Returns None if invalid or missing."""
    if value is None or not isinstance(value, str):
        return None
    try:
        s = value.replace("+0000", "+00:00")
        return parse_timestamp(s)
    except Exception:
        return None


@ConnectorBuilder("Salesforce")\
    .in_group("Salesforce")\
    .with_description("Sync content from your Salesforce instance")\
    .with_categories(["CRM", "Sales"])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Salesforce",
            authorize_url="https://login.salesforce.com/services/oauth2/authorize",
            token_url="https://login.salesforce.com/services/oauth2/token",
            redirect_uri="connectors/oauth/callback/Salesforce",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=[
                    "api",
                    "refresh_token",
                    "offline_access"
                ],
                agent=[]
            ),
            fields=[
                AuthField(
                    name="instance_url",
                    display_name="Salesforce Instance URL",
                    placeholder="https://login.salesforce.com",
                    description="The base URL of your Salesforce instance",
                    field_type="TEXT",
                    max_length=2048
                ),
                CommonFields.client_id("Salesforce Connected App"),
                CommonFields.client_secret("Salesforce Connected App")
            ],
            icon_path="/assets/icons/connectors/salesforce.svg",
            app_group="Salesforce",
            app_description="OAuth application for accessing Salesforce API",
            app_categories=["CRM", "Sales"],
            token_access_type="offline"
        )
    ])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/salesforce.svg")
        .add_documentation_link(DocumentationLink(
            "Salesforce OAuth Setup",
            "https://help.salesforce.com/s/articleView?id=sf.remoteaccess_oauth_web_server_flow.htm",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/salesforce/salesforce',
            'pipeshub'
        ))
        .add_filter_field(CommonFields.modified_date_filter("Filter content by modification date."))
        .add_filter_field(CommonFields.created_date_filter("Filter content by creation date."))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_agent_support(False)
    )\
    .build_decorator()
class SalesforceConnector(BaseConnector):
    """
    Connector for synchronizing data from a Salesforce instance.
    Syncs roles, users, and permissions.
    """
    salesforce_instance_url: str

    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str
    ) -> None:
        super().__init__(
            SalesforceApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id
        )

        self.connector_name = Connectors.SALESFORCE
        self.connector_id = connector_id

        # Sync point for incremental user sync (time-based)
        self.user_sync_point = SyncPoint(
            connector_id=self.connector_id,
            org_id=self.data_entities_processor.org_id,
            sync_data_point_type=SyncDataPointType.USERS,
            data_store_provider=self.data_store_provider,
        )
        # Sync point for incremental sync of roles, groups, contacts, leads, products, opportunities, cases, tasks
        self.records_sync_point = SyncPoint(
            connector_id=self.connector_id,
            org_id=self.data_entities_processor.org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=self.data_store_provider,
        )

        # Data source and configuration
        self.data_source: Optional[SalesforceDataSource] = None
        self.api_version = "59.0"

        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()

    async def _get_api_version(self) -> str:
        """Get API version from config or use default."""
        try:
            config = await self.config_service.get_config(
                f"/services/connectors/{self.connector_id}/config"
            )
            if config and config.get("apiVersion"):
                return str(config.get("apiVersion"))
        except Exception:
            pass
        return self.api_version

    async def _soql_query_paginated(
        self,
        api_version: str,
        q: str,
        queryAll: bool = False,
    ) -> SalesforceResponse:
        """
        Execute a SOQL query with pagination, following nextRecordsUrl until done.
        Returns a SalesforceResponse with data['records'] and data['totalSize'] containing all records.
        """
        if not self.data_source:
            return SalesforceResponse(success=False, data=None, error="Data source not initialized")

        response = await self.data_source.soql_query(api_version=api_version, q=q)
        if queryAll:
            response = await self.data_source.soql_query_all(api_version=api_version, q=q)

        if not response.success:
            return response

        all_records: List[Dict[str, Any]] = list(response.data.get("records") or [])

        while not response.data.get("done", True):
            next_url = response.data.get("nextRecordsUrl")
            if not next_url:
                break
            response = await self.data_source.soql_query_next(next_url=next_url)
            if not response.success:
                self.logger.warning(
                    f"SOQL pagination failed at nextRecordsUrl {next_url}: {response.error}. "
                    f"Returning {len(all_records)} records so far."
                )
                break
            all_records.extend(response.data.get("records") or [])

        return SalesforceResponse(
            success=True,
            data={"records": all_records, "totalSize": len(all_records)},
        )

    async def init(self) -> bool:
        """
        Initializes the Salesforce client using credentials from the config service.

        Returns:
            True if initialization was successful, False otherwise
        """
        try:
            config = await self.config_service.get_config(
                f"/services/connectors/{self.connector_id}/config"
            )
            if not config:
                self.logger.error("Salesforce configuration not found.")
                return False

            credentials_config = config.get("credentials", {})
            auth_config = config.get("auth", {})
            oauth_config_id = auth_config.get("oauthConfigId")

            # Fetch OAuth config
            oauth_config = await fetch_oauth_config_by_id(
                oauth_config_id=oauth_config_id,
                connector_type=Connectors.SALESFORCE.value,
                config_service=self.config_service,
                logger=self.logger
            )

            # Extract access token and instance URL
            auth_config_data = oauth_config.get("config")
            access_token = credentials_config.get("access_token")
            refresh_token = credentials_config.get("refresh_token")
            instance_url = auth_config_data.get("instance_url")

            if not access_token:
                self.logger.error("Salesforce access token not found in configuration.")
                return False
            if not instance_url:
                self.logger.error("Salesforce instance URL not found in configuration.")
                return False

            # Initialize Salesforce client
            self.api_version = config.get("apiVersion", "59.0")
            salesforce_config = SalesforceConfig(
                instance_url=instance_url,
                access_token=access_token,
                api_version=self.api_version,
                refresh_token=refresh_token
            )

            try:
                client = SalesforceClient.build_with_config(salesforce_config)
            except ValueError as e:
                self.logger.error(f"Failed to initialize Salesforce client: {e}", exc_info=True)
                return False

            self.data_source = SalesforceDataSource(client)
            self.salesforce_instance_url = instance_url

            self.logger.info("Salesforce client initialized successfully.")
            return True

        except Exception as e:
            self.logger.error(f"Failed to initialize Salesforce client: {e}", exc_info=True)
            return False

    async def test_connection_and_access(self) -> bool:
        """
        Tests the connection to Salesforce by attempting to query the API.

        Returns:
            True if connection test was successful, False otherwise
        """
        if not self.data_source:
            self.logger.error("Salesforce data source not initialized")
            return False

        try:
            api_version = await self._get_api_version()
            response = await self.data_source.limits(version=api_version)
            if response.success:
                self.logger.info("Salesforce connection test successful.")
                return True
            else:
                self.logger.error(f"Salesforce connection test failed: {response.error}")
                return False

        except Exception as e:
            self.logger.error(f"Salesforce connection test failed: {e}", exc_info=True)
            return False

    async def _reinitialize_token_if_needed(self) -> bool:
        """
        Call a basic API using the data source; if the response is 401 Unauthorized,
        perform token refresh via TokenRefreshService and re-initialize the connector.

        Returns:
            True if no reinit was needed (API succeeded) or reinit succeeded;
            False if data source is not initialized, or 401 was received but refresh failed.
        """
        if not self.data_source:
            self.logger.warning("Salesforce data source not initialized; cannot reinitialize token.")
            return False

        try:
            api_version = await self._get_api_version()
            response = await self.data_source.limits(version=api_version)
        except Exception as e:
            self.logger.warning(f"Salesforce limits API call failed: {e}")
            return False

        if response.success:
            self.logger.info(f"Salesforce access token still active for connector {self.connector_id}")
            return True

        # Check for 401 Unauthorized (error is e.g. "HTTP 401")
        if not response.error or "401" not in response.error:
            return False

        self.logger.info(f"Salesforce API returned 401 for connector {self.connector_id}; attempting token refresh.")
        refresh_service = startup_service.get_token_refresh_service()
        if not refresh_service:
            self.logger.error("Token refresh service not available; cannot reinitialize token.")
            return False

        try:
            config = await self.config_service.get_config(
                f"/services/connectors/{self.connector_id}/config"
            )
            if not config:
                self.logger.error("Connector config not found; cannot refresh token.")
                return False
            refresh_token = (config.get("credentials") or {}).get("refresh_token")
            if not refresh_token:
                self.logger.error("No refresh token in connector config; cannot refresh.")
                return False
            connector_type = (
                self.connector_name.value
                if hasattr(self.connector_name, "value")
                else str(self.connector_name)
            )
            await refresh_service._perform_token_refresh(
                self.connector_id, connector_type, refresh_token
            )
        except Exception as e:
            self.logger.error(f"Token refresh failed for connector {self.connector_id}: {e}", exc_info=True)
            return False

        # Re-initialize the connector so the data source uses the new token
        try:
            return await self.init()
        except Exception as e:
            self.logger.error(f"Re-initialization after token refresh failed: {e}", exc_info=True)
            return False

    async def get_signed_url(self, record: Record) -> Optional[str]:
        """
        Get a signed URL for accessing a Salesforce record.

        For FILE records: returns the shepherd version download URL in the format
        {instance_url}/sfc/servlet.shepherd/version/download/{content_version_id}
        where content_version_id is taken from record.external_revision_id.

        For other records: returns the web URL {instance_url}/{external_record_id}.

        Args:
            record: The record to get URL for

        Returns:
            The download/web URL of the record or None
        """
        if not record.external_record_id:
            return None
        if record.record_type == RecordType.FILE and record.external_revision_id:
            base = (self.salesforce_instance_url or "").rstrip("/")
            return f"{base}/sfc/servlet.shepherd/version/download/{record.external_revision_id}"
        return f"{self.salesforce_instance_url}/{record.external_record_id}"

    async def _get_access_token(self) -> Optional[str]:
        """Fetch current access token from connector config (for authenticated API calls)."""
        try:
            config = await self.config_service.get_config(
                f"/services/connectors/{self.connector_id}/config"
            )
            if not config:
                return None
            return config.get("credentials", {}).get("access_token")
        except Exception as e:
            self.logger.error(f"Failed to get access token: {e}")
            return None

# ============================= STREAMINGS CONTENT FROM SALESFORCE =============================

    async def _stream_salesforce_file_content(self, record: Record) -> AsyncGenerator[bytes, None]:
        """
        Stream file content from Salesforce ContentVersion VersionData API.

        Uses record.external_revision_id (ContentVersion Id) and Bearer token auth.
        Same REST path as data_source.s_object_blob_retrieve(record_id, "ContentVersion", version, "VersionData"),
        but we stream with aiohttp here because the data source's _execute_request parses responses as JSON;
        VersionData is binary, and we need chunked streaming for create_stream_record_response.
        """
        if not record.external_revision_id:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="File record has no version id (external_revision_id)"
            )
        access_token = await self._get_access_token()
        if not access_token:
            raise HTTPException(
                status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value,
                detail="Salesforce connector not authenticated"
            )
        api_version = await self._get_api_version()
        base = (self.salesforce_instance_url or "").rstrip("/")
        path = f"/services/data/v{api_version}/sobjects/ContentVersion/{record.external_revision_id}/VersionData"
        url = f"{base}{path}"
        timeout = aiohttp.ClientTimeout(total=300, connect=10)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    url,
                    headers={"Authorization": f"Bearer {access_token}"},
                    allow_redirects=True,
                ) as response:
                    if response.status != HttpStatusCode.SUCCESS.value:
                        try:
                            body = await response.text()
                        except Exception:
                            body = ""
                        self.logger.error(
                            f"Salesforce VersionData request failed: {response.status} {body[:500]}"
                        )
                        raise HTTPException(
                            status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                            detail=f"Failed to fetch file content: {response.status}"
                        )
                    async for chunk in response.content.iter_chunked(8192):
                        yield chunk
        except aiohttp.ClientError as e:
            self.logger.error(f"Error streaming Salesforce file {record.id}: {e}")
            raise HTTPException(
                status_code=HttpStatusCode.INTERNAL_SERVER_ERROR.value,
                detail=f"Failed to fetch file content: {str(e)}"
            )

    async def _message_segments_to_html(self, segments: List[Dict[str, Any]]) -> str:
        """
        Convert Chatter messageSegments to HTML, fetching InlineImage segments
        as base64 data URIs so the content is self-contained.
        """
        html_parts = []

        for seg in segments:
            seg_type = seg.get("type")

            if seg_type == "MarkupBegin":
                html_tag = seg.get("htmlTag") or "span"
                html_parts.append(f"<{html_tag}>")

            elif seg_type == "MarkupEnd":
                html_tag = seg.get("htmlTag") or "span"
                # MarkupEnd text often contains the newline, include it
                html_parts.append(f"</{html_tag}>")

            elif seg_type == "Text":
                text = seg.get("text") or ""
                html_parts.append(text)

            elif seg_type == "InlineImage":
                alt = seg.get("altText") or ""
                file_url = seg.get("url") or ""  # e.g. /services/data/v59.0/connect/files/{id}/content?versionNumber=1
                image_url = f"{self.salesforce_instance_url}{file_url}"
                b64_data_uri = await self._fetch_file_as_base64_uri(image_url)
                if b64_data_uri:
                    html_parts.append(f'<img alt="{alt}" src="{b64_data_uri}" />')
                else:
                    html_parts.append(f'<img alt="{alt}" src="" />')

            elif seg_type == "EntityLink":
                text = seg.get("text") or ""
                ref = seg.get("reference") or {}
                url = ref.get("url") or ""
                html_parts.append(f'<a href="{url}">{text}</a>')

            elif seg_type in ("FieldChange", "FieldChangeName", "FieldChangeValue"):
                text = seg.get("text") or ""
                html_parts.append(text)

            else:
                # Fallback: just use text if present
                text = seg.get("text") or ""
                html_parts.append(text)

        return "".join(html_parts)

    async def _fetch_file_as_base64_uri(self, full_url: str) -> Optional[str]:
        """
        Fetch a Salesforce file by its URL and return a base64 data URI.
        - file-asset-public URLs: fetched without auth (CDN-hosted public assets)
        - connect/files URLs: fetched with Bearer token (API-protected files)
        """
        if not full_url or not self.data_source:
            return None

        try:
            is_public_asset = "file-asset-public" in full_url
            is_api_file = "/connect/files/" in full_url or "/services/data/" in full_url

            headers = {}
            if is_api_file and not is_public_asset:
                access_token = await self._get_access_token()
                if not access_token:
                    return None
                headers["Authorization"] = f"Bearer {access_token}"

            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    full_url,
                    headers=headers,
                    allow_redirects=True,
                ) as response:
                    # If public fetch 404s, retry with auth token
                    if response.status == 404 and is_public_asset:
                        access_token = await self._get_access_token()
                        if access_token:
                            async with session.get(
                                full_url,
                                headers={"Authorization": f"Bearer {access_token}"},
                                allow_redirects=True,
                            ) as retry_response:
                                if retry_response.status != 200:
                                    return None
                                response_to_use = retry_response
                                raw = await response_to_use.read()
                                content_type = response_to_use.headers.get("Content-Type", "")
                        else:
                            return None
                    elif response.status != 200:
                        return None
                    else:
                        raw = await response.read()
                        content_type = response.headers.get("Content-Type", "")

                    # Determine mime type: prefer Content-Type header, fallback to magic bytes
                    if "image/jpeg" in content_type or "image/jpg" in content_type:
                        mime = "image/jpeg"
                    elif "image/gif" in content_type:
                        mime = "image/gif"
                    elif "image/webp" in content_type:
                        mime = "image/webp"
                    elif "image/png" in content_type:
                        mime = "image/png"
                    elif raw[:2] == b'\xff\xd8':
                        mime = "image/jpeg"
                    elif raw[:4] == b'\x89PNG':
                        mime = "image/png"
                    elif raw[:3] == b'GIF':
                        mime = "image/gif"
                    else:
                        mime = "image/png"

                    b64 = base64.b64encode(raw).decode("utf-8")
                    return f"data:{mime};base64,{b64}"

        except Exception as e:
            self.logger.error(f"Error fetching file as base64 URI: {e}", exc_info=True)
            return None
    
    async def _fetch_and_build_discussion_block_groups(
        self,
        parent_id: str,
        start_index: int,
        weburl: Optional[str] = None,
    ) -> List[BlockGroup]:
        """
        Fetch Chatter feed via REST API and build discussion BlockGroups.
        Combines fetching and building into a single function.
        """
        if not self.data_source:
            return []

        api_version = await self._get_api_version()

        # --- Fetch all pages ---
        all_elements: List[Dict[str, Any]] = []

        resp = await self.data_source.record_feed_elements(
            record_group_id=parent_id,
            version=api_version,
        )
        if not resp.success or not resp.data:
            return []

        all_elements.extend(resp.data.get("elements") or [])

        next_page_url = resp.data.get("nextPageUrl")
        while next_page_url:
            paged_resp = await self.data_source._execute_request(
                method="GET",
                path=next_page_url,
                params=None,
                body=None,
                content_type="application/json",
            )
            if not paged_resp.success or not paged_resp.data:
                break
            all_elements.extend(paged_resp.data.get("elements") or [])
            next_page_url = paged_resp.data.get("nextPageUrl")

        # --- Helpers ---

        def _author_from_user(user: Optional[Dict[str, Any]]) -> str:
            if isinstance(user, dict):
                return (user.get("name") or user.get("displayName") or "").strip()
            return ""

        def _get_tracked_changes(element: Dict[str, Any]) -> List[Dict[str, Any]]:
            caps = element.get("capabilities") or {}
            tc = caps.get("trackedChanges") or {}
            return tc.get("changes") or []

        def _get_comments(element: Dict[str, Any]) -> List[Dict[str, Any]]:
            caps = element.get("capabilities") or {}
            comments_cap = caps.get("comments") or {}
            page = comments_cap.get("page") or {}
            return page.get("items") or []

        def _get_feed_files(element: Dict[str, Any]) -> List[Dict[str, Any]]:
            caps = element.get("capabilities") or {}
            files_cap = caps.get("files") or {}
            return files_cap.get("items") or []

        def _tracked_changes_to_text(changes: List[Dict[str, Any]]) -> str:
            lines = []
            for tc in changes:
                fn = tc.get("fieldName") or ""
                ov = str(tc.get("oldValue")) if tc.get("oldValue") is not None else ""
                nv = str(tc.get("newValue")) if tc.get("newValue") is not None else ""
                lines.append(f"{fn}: {ov} → {nv}")
            return "\n".join(lines)

        def _get_call_log_post_data(element: Dict[str, Any]) -> Optional[str]:
            """
            Build the markdown content for a CallLogPost feed element.

            Salesforce stores call log content in capabilities.enhancedLink:
              - title:       the subject of the logged call (may be null — fall back
                             to the first EntityLink text in header.messageSegments)
              - description: the full call notes body

            Returns a formatted markdown string, or None if this element is not a
            CallLogPost or has no usable content.
            """
            if element.get("type") != "CallLogPost":
                return None

            caps = element.get("capabilities") or {}
            enhanced_link = caps.get("enhancedLink") or {}

            # Title: prefer enhancedLink.title, fall back to first EntityLink in header
            title: str = (enhanced_link.get("title") or "").strip()
            if not title:
                for seg in (element.get("header") or {}).get("messageSegments") or []:
                    if seg.get("type") == "EntityLink":
                        title = (seg.get("text") or "").strip()
                        break

            description: str = (enhanced_link.get("description") or "").strip()

            if not title and not description:
                return None

            parts = []
            if title:
                parts.append(f"**{title}**")
            if description:
                # description is plain text with \r\n line endings — normalise to markdown
                parts.append(html_to_markdown(description.replace("\r\n", "\n")))

            return "\n\n".join(parts)

        def _get_comment_files(comment: Dict[str, Any]) -> List[Dict[str, Any]]:
            """
            Extract all file attachments from a comment or reply.
            Prefers capabilities.files.items (multiple files), falls back to
            capabilities.content (single file, older-style attachment).
            """
            caps = comment.get("capabilities") or {}

            file_items = (caps.get("files") or {}).get("items") or []
            if file_items:
                return file_items

            # Single-file fallback — normalise to a list for uniform handling
            content = caps.get("content")
            if content and isinstance(content, dict) and content.get("id"):
                return [content]

            return []

        async def _process_body(body: Optional[Dict[str, Any]]) -> str:
            """
            Convert a Chatter body dict to markdown.
            Prefers messageSegments (to capture InlineImages) over plain text.
            """
            if not body:
                return ""

            segments = body.get("messageSegments") or []
            is_rich = body.get("isRichText") or False

            if segments and is_rich:
                # Build HTML from segments (images become base64 data URIs)
                html = await self._message_segments_to_html(segments)
                return html_to_markdown(html) if html else ""
            else:
                # Plain text fallback
                plain = (body.get("text") or "").strip()
                if plain:
                    converted = await self._process_html_images(plain)
                    self.logger.info(f"Converted: {converted}")
                    return html_to_markdown(converted) if converted else ""
                return ""

        def _prepend_ts(data: str, ts: Optional[str]) -> str:
            ts_str = self._format_discussion_timestamp(ts)
            if ts_str:
                return f"*{ts_str}*\n\n{data}" if data else f"*{ts_str}*"
            return data

        def _is_task_comment(comment: Dict[str, Any]) -> bool:
            comment_parent = comment.get("parent") or {}
            comment_parent_id = comment_parent.get("id") or ""
            # If the comment belongs to a different record than what we're processing, skip it
            return comment_parent_id.startswith("00T") and comment_parent_id != parent_id

        async def _resolve_child_records(external_ids: List[str]) -> List[ChildRecord]:
            """Look up each external_id and build a proper ChildRecord."""
            children = []
            for ext_id in external_ids:
                try:
                    record = await self.data_entities_processor.get_record_by_external_id(
                        self.connector_id,
                        ext_id,
                    )
                    if record:
                        children.append(
                            ChildRecord(
                                child_type=ChildType.RECORD,
                                child_id=record.id,
                                child_name=record.record_name,
                            )
                        )
                except Exception:
                    pass  # skip unresolvable attachments
            return children

        def _flatten_elements(elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            flat = []
            for el in elements:
                if el.get("feedElementType") == "Bundle":
                    bundle_caps = el.get("capabilities") or {}
                    bundle = bundle_caps.get("bundle") or {}
                    page = bundle.get("page") or {}
                    sub_elements = page.get("elements") or []
                    flat.extend(_flatten_elements(sub_elements))
                else:
                    flat.append(el)
            return flat

        flat_elements = _flatten_elements(all_elements)
        flat_elements.sort(key=lambda e: e.get("createdDate") or "", reverse=True)

        # --- Build BlockGroups ---
        block_groups: List[BlockGroup] = []
        idx = start_index

        async def _process_comment(comment: Dict[str, Any], thread_index: int) -> None:
            """
            Build a BlockGroup for a comment/reply, resolve all its file attachments
            (supports both single and multiple files via _get_comment_files), then
            recurse into any nested replies so no level of the thread is missed.
            Comments on CallLogPosts are treated identically to any other comment —
            only the parent post itself gets the special call log formatting.
            """
            nonlocal idx

            comment_author = _author_from_user(comment.get("user"))
            comment_id = comment.get("id") or ""
            comment_data = await _process_body(comment.get("body"))

            # Resolve all file attachments on this comment/reply
            comment_files = _get_comment_files(comment)
            comment_ext_ids = [f"{f['id']}-{parent_id}" for f in comment_files if f.get("id")]
            comment_children = await _resolve_child_records(comment_ext_ids)

            comment_bg = BlockGroup(
                id=str(uuid4()),
                index=idx,
                parent_index=thread_index,
                name=f"Comment by {comment_author}",
                type=GroupType.TEXT_SECTION,
                sub_type=GroupSubType.COMMENT,
                description=f"Comment by {comment_author}",
                source_group_id=comment_id,
                data=comment_data,
                format=DataFormat.MARKDOWN,
                weburl=weburl,
                requires_processing=True,
                children_records=comment_children if comment_children else None,
            )
            block_groups.append(comment_bg)

            # Recurse into nested replies on this comment
            for reply in _get_comments(comment):
                idx += 1
                await _process_comment(reply, thread_index)

        for element in flat_elements:
            el_id = element.get("id") or ""
            author = _author_from_user(element.get("actor"))
            el_timestamp = element.get("createdDate")

            # Thread BlockGroup
            thread_index = idx
            thread_name = f"Chatter: {author}"
            thread_bg = BlockGroup(
                id=str(uuid4()),
                index=thread_index,
                parent_index=0,
                name=thread_name,
                type=GroupType.TEXT_SECTION,
                sub_type=GroupSubType.COMMENT_THREAD,
                description=f"Discussion thread - {thread_name}",
                source_group_id=f"{parent_id}_discussion_{el_id}",
                weburl=weburl,
                requires_processing=False,
            )
            block_groups.append(thread_bg)
            idx += 1

            # Post body
            # Priority order:
            #   1. CallLogPost  → title + description from capabilities.enhancedLink
            #   2. TrackedChange (field edits with no body text) → formatted field diffs
            #   3. Normal post  → body segments / plain text converted to markdown
            body_dict = element.get("body") or {}
            tracked = _get_tracked_changes(element)

            call_log_data = _get_call_log_post_data(element)
            if call_log_data is not None:
                # CallLogPost: use enhancedLink title + description only
                post_data = call_log_data
                self.logger.info(
                    f"Rendered CallLogPost content for element {el_id}"
                )
            elif not (body_dict.get("text") or "").strip() and tracked:
                post_data = _tracked_changes_to_text(tracked)
            else:
                post_data = await _process_body(body_dict)

            post_data = _prepend_ts(post_data, el_timestamp)

            # Post-level file attachments
            feed_files = _get_feed_files(element)
            post_ext_ids = [f"{f['id']}-{parent_id}" for f in feed_files if f.get("id")]
            attachment_children = await _resolve_child_records(post_ext_ids)

            post_bg = BlockGroup(
                id=str(uuid4()),
                index=idx,
                parent_index=thread_index,
                name=f"Post by {author}",
                type=GroupType.TEXT_SECTION,
                sub_type=GroupSubType.COMMENT,
                description=f"Post by {author}",
                source_group_id=el_id,
                data=post_data,
                format=DataFormat.MARKDOWN,
                weburl=weburl,
                requires_processing=True,
                children_records=attachment_children if attachment_children else None,
            )
            block_groups.append(post_bg)
            idx += 1

            # Comments / replies — _process_comment handles multi-file attachments
            # and recurses into nested replies. CallLogPost replies are treated the
            # same as any other comment (no special formatting needed there).
            for comment in _get_comments(element):
                await _process_comment(comment, thread_index)
                idx += 1

        return block_groups

    async def _get_record_linked_file_child_records(
        self,
        api_version: str,
        record_salesforce_id: str,
    ) -> List[ChildRecord]:
        """
        Retrieves files attached to a record using ContentDocumentLink associations in Salesforce.
        """
        if not record_salesforce_id or not self.data_source:
            return []

        soql = (
            "SELECT ContentDocumentId, LinkedEntityId, ContentDocument.Title, "
            "ContentDocument.FileExtension, ContentDocument.LatestPublishedVersionId, "
            "ContentDocument.CreatedDate FROM ContentDocumentLink "
            f"WHERE LinkedEntityId = '{record_salesforce_id}'"
        )

        response = await self._soql_query_paginated(api_version=api_version, q=soql)
        if not response.success or not response.data:
            return []

        child_records: List[ChildRecord] = []
        for row in response.data.get("records", []):
            content_document_id = row.get("ContentDocumentId")
            linked_entity_id = row.get("LinkedEntityId")
            if not content_document_id or not linked_entity_id:
                continue
            external_record_id = f"{content_document_id}-{linked_entity_id}"
            try:
                record = await self.data_entities_processor.get_record_by_external_id(
                    self.connector_id,
                    external_record_id,
                )
                if record:
                    child_records.append(
                        ChildRecord(
                            child_type=ChildType.RECORD,
                            child_id=record.id,
                            child_name=record.record_name,
                        )
                    )
            except Exception as e:
                self.logger.error(f"Error resolving child record: {e}", exc_info=True)

        return child_records

    async def _get_opportunity_related_child_records(
        self,
        api_version: str,
        opportunity_id: str,
    ) -> List[ChildRecord]:
        """
        Fetch Tasks, OpportunityLineItems (products), and Cases related to an Opportunity
        via a single composite request, resolve each to Arango record id, and return as ChildRecords.
        """
        if not opportunity_id or not self.data_source:
            return []
        # Escape single quotes in SOQL literal
        safe_id = opportunity_id.replace("'", "''")
        q_tasks = f"SELECT Id, Subject, Status FROM Task WHERE WhatId='{safe_id}'"
        q_products = (
            f"SELECT Id, Product2.Id, Product2.Name, Quantity, UnitPrice "
            f"FROM OpportunityLineItem WHERE OpportunityId='{safe_id}'"
        )
        q_cases = (
            f"SELECT Id, CaseNumber, Status FROM Case "
            f"WHERE AccountId IN (SELECT AccountId FROM Opportunity WHERE Id='{safe_id}')"
        )
        base = f"/services/data/v{api_version}/query/"
        composite_request = [
            {"method": "GET", "referenceId": "RelatedTasks", "url": f"{base}?q={quote(q_tasks)}"},
            {"method": "GET", "referenceId": "RelatedProducts", "url": f"{base}?q={quote(q_products)}"},
            {"method": "GET", "referenceId": "RelatedCases", "url": f"{base}?q={quote(q_cases)}"},
        ]
        response = await self.data_source.composite(
            version=api_version,
            data={"compositeRequest": composite_request},
        )
        if not response.success or not response.data:
            self.logger.error(f"Composite request for opportunity {opportunity_id} failed: {response.error}")
            return []
        composite_response = response.data.get("compositeResponse") or []
        child_records: List[ChildRecord] = []
        for item in composite_response:
            if item.get("httpStatusCode") != 200:
                continue
            body = item.get("body")
            if isinstance(body, str):
                try:
                    body = json.loads(body)
                except json.JSONDecodeError:
                    continue
            records_list = (body or {}).get("records") or []
            ref_id = item.get("referenceId", "")
            for row in records_list:
                external_id = None
                name = None
                if ref_id == "RelatedTasks":
                    external_id = row.get("Id")
                    name = row.get("Subject") or (f"Task {external_id}" if external_id else None)
                elif ref_id == "RelatedProducts":
                    product2 = row.get("Product2") or {}
                    external_id = product2.get("Id")
                    name = product2.get("Name") or (f"Product {external_id}" if external_id else None)
                elif ref_id == "RelatedCases":
                    external_id = row.get("Id")
                    name = row.get("CaseNumber") or (f"Case {external_id}" if external_id else None)
                if not external_id:
                    continue
                try:
                    record = await self.data_entities_processor.get_record_by_external_id(
                        self.connector_id,
                        external_id,
                    )
                    if record:
                        child_records.append(
                            ChildRecord(
                                child_type=ChildType.RECORD,
                                child_id=record.id,
                                child_name=record.record_name or name,
                            )
                        )
                except Exception as e:
                    self.logger.error(f"Could not resolve related record for external_id={external_id}: {e}")
        return child_records

    def _set_block_group_children(self, block_groups: List[BlockGroup]) -> None:
        """Wire parent_index -> children for all block groups (description, emails, discussion threads)."""
        blockgroup_children_map: Dict[int, List[int]] = defaultdict(list)
        for bg in block_groups:
            if bg.parent_index is not None:
                blockgroup_children_map[bg.parent_index].append(bg.index)
        for bg in block_groups:
            if bg.index in blockgroup_children_map:
                bg.children = BlockGroupChildren.from_indices(
                    block_group_indices=sorted(blockgroup_children_map[bg.index])
                )
    
    async def _process_html_images(self, html_content: str) -> str:
        if not html_content:
            return html_content

        soup = BeautifulSoup(html_content, 'html.parser')
        img_tags = soup.find_all('img')

        if not img_tags:
            self.logger.warning(f"No img tags found in html_content: {html_content}")
            return html_content

        async with httpx.AsyncClient() as client:
            for img_tag in img_tags:
                src = img_tag.get('src', '')
                if not src:
                    self.logger.warning(f"No src found for image: {src}")
                    continue

                # 1. Updated Regex to match your new URL format
                # Looks for 'file-asset-public/' followed by the asset name
                asset_match = re.search(r'/file-asset-public/([^?]+)', src)

                if not asset_match:
                    continue

                try:
                    # 2. Download the image directly from the URL
                    response = await client.get(src)
                    
                    if response.status_code != 200:
                        self.logger.error(f"Failed to download image: {src} - Status: {response.status_code}")
                        continue
                    response.raise_for_status()
                    image_bytes = response.content
                    
                    # 3. Determine MIME type from headers or content
                    mime_type = response.headers.get('Content-Type', 'image/png')
                    
                    # Manual fallback for type detection if header is generic
                    if "octet-stream" in mime_type:
                        if image_bytes.startswith(b'\xff\xd8\xff'):
                            mime_type = "image/jpeg"
                        elif image_bytes.startswith(b'\x89PNG'):
                            mime_type = "image/png"
                        elif image_bytes.startswith(b'GIF'):
                            mime_type = "image/gif"

                    # 4. Convert to base64
                    base64_encoded = base64.b64encode(image_bytes).decode('utf-8')
                    data_uri = f"data:{mime_type};base64,{base64_encoded}"

                    # 5. Replace the src attribute
                    img_tag['src'] = data_uri

                except Exception as e:
                    self.logger.error(f"Error processing image {src}: {e}", exc_info=True)
                    continue

        return str(soup)

    async def stream_record(self, record: Record) -> StreamingResponse:
        """
        Stream record content.

        For FILE records: streams file bytes from Salesforce ContentVersion (like Dropbox),
        using create_stream_record_response with filename and mime_type.

        For PRODUCT, DEAL, CASE, TASK: streams BlocksContainer JSON.
        """

        self.logger.info("Reinitializing Salesforce token if needed...")
        await self._reinitialize_token_if_needed()
        if not self.data_source:
            self.logger.error("Salesforce data source not initialized")
            return

        try:
            if record.record_type == RecordType.FILE:
                return create_stream_record_response(
                    self._stream_salesforce_file_content(record),
                    filename=record.record_name,
                    mime_type=record.mime_type,
                    fallback_filename=f"record_{record.id}",
                )
            if record.record_type == RecordType.PRODUCT:
                content_bytes = await self._process_product_record(record)
            elif record.record_type == RecordType.DEAL:
                content_bytes = await self._process_deal_record(record)
            elif record.record_type == RecordType.CASE:
                content_bytes = await self._process_case_record(record)
            elif record.record_type == RecordType.TASK:
                content_bytes = await self._process_task_record(record)
            else:
                raise ValueError(f"Unsupported record type for streaming: {record.record_type}")
            

            return StreamingResponse(
                iter([content_bytes]),
                media_type=MimeTypes.BLOCKS.value,
                headers={
                    "Content-Disposition": f'inline; filename="{record.external_record_id}_blocks.json"'
                }
            )

        except Exception as e:
            self.logger.error(f"❌ Error streaming record {record.id}: {e}", exc_info=True)
            raise

    async def _process_product_record(self, record: ProductRecord) -> bytes:
        """
        Fetch product description using external_record_id and return content as BlocksContainer bytes.
        """
        if not self.data_source:
            raise HTTPException(
                status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value,
                detail="Salesforce connector not initialized"
            )

        product_id = record.external_record_id
        api_version = await self._get_api_version()

        soql_query = (
            f"SELECT Id, Name, Description, ProductCode, Family FROM Product2 WHERE Id = '{product_id}'"
        )
        response = await self._soql_query_paginated(api_version=api_version, q=soql_query)

        if not response.success or not response.data:
            self.logger.warning(f"Failed to fetch product {product_id}: {response.error}")
            description_content = f"# {record.record_name or 'Product'}"
        else:
            records_list = response.data.get("records", [])
            product_data = records_list[0] if records_list else {}
            description_raw = product_data.get("Description") or ""
            description_content = (
                description_raw
                if description_raw
                else f"# {record.record_name or 'Product'}\n\nNo description available."
            )


        weburl = None
        if self.salesforce_instance_url and product_id:
            try:
                weburl = HttpUrl(f"{self.salesforce_instance_url}/{product_id}")
            except Exception:
                pass

        description_block_group = BlockGroup(
            id=str(uuid4()),
            index=0,
            name=record.record_name or "Product Description",
            type=GroupType.TEXT_SECTION,
            sub_type=GroupSubType.CONTENT,
            description=f"Product description for {record.record_name or 'Product'}",
            source_group_id=f"{product_id}_description",
            data=description_content,
            format=DataFormat.MARKDOWN,
            weburl=str(weburl) if weburl is not None else None,
            requires_processing=True,
        )

        blocks_container = BlocksContainer(
            blocks=[],
            block_groups=[description_block_group]
        )
        return blocks_container.model_dump_json(indent=2).encode("utf-8")

    async def _process_deal_record(self, record: Record) -> bytes:
        """
        Fetch deal (Opportunity) description using external_record_id and return content as BlocksContainer bytes.
        """
        if not self.data_source:
            raise HTTPException(
                status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value,
                detail="Salesforce connector not initialized"
            )

        opportunity_id = record.external_record_id
        api_version = await self._get_api_version()

        soql_query = (
            f"SELECT Id, Description FROM Opportunity WHERE Id = '{opportunity_id}'"
        )
        response = await self._soql_query_paginated(api_version=api_version, q=soql_query)

        if not response.success or not response.data:
            self.logger.error(f"❌ Failed to fetch opportunity {opportunity_id}: {response.error}")
            description_content = f"# {record.record_name or 'Deal'}"
        else:
            records_list = response.data.get("records", [])
            opportunity_data = records_list[0] if records_list else {}
            description_raw = opportunity_data.get("Description") or ""
            description_content = (
                description_raw
                if description_raw
                else f"# {record.record_name or 'Deal'}\n\nNo description available."
            )

        weburl = None
        if self.salesforce_instance_url and opportunity_id:
            try:
                weburl = HttpUrl(f"{self.salesforce_instance_url}/{opportunity_id}")
            except Exception:
                pass

        description_linked_files = await self._get_record_linked_file_child_records(
            api_version, opportunity_id
        )
        related_children = await self._get_opportunity_related_child_records(
            api_version, opportunity_id
        )
        all_children = list(description_linked_files) + list(related_children)
        description_block_group = BlockGroup(
            id=str(uuid4()),
            index=0,
            name=record.record_name or "Deal Description",
            type=GroupType.TEXT_SECTION,
            sub_type=GroupSubType.CONTENT,
            description=f"Deal description for {record.record_name or 'Deal'}",
            source_group_id=f"{opportunity_id}_description",
            data=description_content,
            format=DataFormat.MARKDOWN,
            weburl=str(weburl) if weburl is not None else None,
            requires_processing=True,
            children_records=all_children if all_children else None,
        )
        block_groups: List[BlockGroup] = [description_block_group]

        # structure_discussion = await self._fetch_structure_discussion(opportunity_id)
        discussion_groups = await self._fetch_and_build_discussion_block_groups(
            parent_id=opportunity_id,
            start_index=len(block_groups),
            weburl=str(weburl) if weburl is not None else None,
        )
        block_groups.extend(discussion_groups)
        self._set_block_group_children(block_groups)

        blocks_container = BlocksContainer(
            blocks=[],
            block_groups=block_groups,
        )
        return blocks_container.model_dump_json(indent=2).encode("utf-8")

    async def _process_case_record(self, record: Record) -> bytes:
        """
        Fetch case description using external_record_id and return content as BlocksContainer bytes.
        """
        if not self.data_source:
            raise HTTPException(
                status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value,
                detail="Salesforce connector not initialized"
            )

        case_id = record.external_record_id
        api_version = await self._get_api_version()

        soql_query = (
            f"SELECT Id, Subject, Description FROM Case WHERE Id = '{case_id}'"
        )
        response = await self._soql_query_paginated(api_version=api_version, q=soql_query)

        if not response.success or not response.data:
            self.logger.warning(f"Failed to fetch case {case_id}: {response.error}")
            description_content = f"# {record.record_name or 'Case'}"
        else:
            records_list = response.data.get("records", [])
            case_data = records_list[0] if records_list else {}
            subject = case_data.get("Subject") or ""
            description_raw = case_data.get("Description") or ""
            if subject and description_raw:
                description_content = f"# {subject}\n\n{description_raw}"
            elif subject:
                description_content = f"# {subject}\n\nNo description available."
            elif description_raw:
                description_content = description_raw
            else:
                description_content = f"# {record.record_name or 'Case'}\n\nNo description available."

        weburl = None
        if self.salesforce_instance_url and case_id:
            try:
                weburl = HttpUrl(f"{self.salesforce_instance_url}/{case_id}")
            except Exception:
                pass

        description_linked_files = await self._get_record_linked_file_child_records(
            api_version, case_id
        )
        description_block_group = BlockGroup(
            id=str(uuid4()),
            index=0,
            name=record.record_name or "Case Description",
            type=GroupType.TEXT_SECTION,
            sub_type=GroupSubType.CONTENT,
            description=f"Case description for {record.record_name or 'Case'}",
            source_group_id=f"{case_id}_description",
            data=description_content,
            format=DataFormat.MARKDOWN,
            weburl=str(weburl) if weburl is not None else None,
            requires_processing=True,
            children_records=(
                description_linked_files if description_linked_files else None
            ),
        )
        block_groups = [description_block_group]

        # structure_discussion = await self._fetch_structure_discussion(case_id)
        discussion_groups = await self._fetch_and_build_discussion_block_groups(
            parent_id=case_id,
            start_index=len(block_groups),
            weburl=str(weburl) if weburl is not None else None,
        )
        block_groups.extend(discussion_groups)
        self._set_block_group_children(block_groups)

        blocks_container = BlocksContainer(
            blocks=[],
            block_groups=block_groups,
        )
        return blocks_container.model_dump_json(indent=2).encode("utf-8")

    async def _process_task_record(self, record: Record) -> bytes:
        """
        Fetch task subject and description using external_record_id and return content as BlocksContainer bytes.
        """
        self.logger.info("_process_task_record start for record_id=%s, external_record_id=%s", record.id, record.external_record_id)
        if not self.data_source:
            self.logger.error("_process_task_record: data_source not initialized for record %s", record.id)
            raise HTTPException(
                status_code=HttpStatusCode.SERVICE_UNAVAILABLE.value,
                detail="Salesforce connector not initialized"
            )

        task_id = record.external_record_id
        api_version = await self._get_api_version()

        soql_query = (
            f"SELECT Id, Subject, Description FROM Task WHERE Id = '{task_id}'"
        )
        response = await self._soql_query_paginated(api_version=api_version, q=soql_query)

        email_query = (
            f"SELECT Id, Subject, HtmlBody, TextBody, HasAttachment "
            f"FROM EmailMessage WHERE ActivityId = '{task_id}' LIMIT 1"
        )
        email_response = await self._soql_query_paginated(api_version=api_version, q=email_query)

        if not response.success or not response.data:
            self.logger.warning("_process_task_record: Failed to fetch task %s: %s", task_id, response.error)
            description_content = f"# {record.record_name or 'Task'}"
        else:
            records_list = response.data.get("records", [])
            task_data = records_list[0] if records_list else {}
            subject = task_data.get("Subject") or ""
            description_raw = task_data.get("Description") or ""
            if subject and description_raw:
                description_content = f"# {subject}\n\n{description_raw}"
            elif subject:
                description_content = f"# {subject}\n\nNo description available."
            elif description_raw:
                description_content = description_raw
            else:
                description_content = f"# {record.record_name or 'Task'}\n\nNo description available."

        weburl = None
        if self.salesforce_instance_url and task_id:
            try:
                weburl = HttpUrl(f"{self.salesforce_instance_url}/{task_id}")
            except Exception as e:
                self.logger.error(f"Weburl build failed: {e}", exc_info=True)
        description_linked_files = await self._get_record_linked_file_child_records(
            api_version, task_id
        )

        description_block_group = BlockGroup(
            id=str(uuid4()),
            index=0,
            name=record.record_name or "Task Description",
            type=GroupType.TEXT_SECTION,
            sub_type=GroupSubType.CONTENT,
            description=f"Task description for {record.record_name or 'Task'}",
            source_group_id=f"{task_id}_description",
            data=description_content,
            format=DataFormat.MARKDOWN,
            weburl=str(weburl) if weburl is not None else None,
            requires_processing=True,
            children_records=(
                description_linked_files if description_linked_files else None
            ),
        )
        block_groups = [description_block_group]

        if email_response.success and email_response.data and email_response.data.get("totalSize") > 0:
            email_records = email_response.data.get("records", [])
            email_data = email_records[0] if email_records else {}
            # 2. Extract metadata
            email_message_id = email_data.get("Id") or ""
            from_addr = email_data.get("FromAddress") or "Unknown Sender"
            to_addr = email_data.get("ToAddress") or "Unknown Recipient"
            subject = email_data.get("Subject") or "(No Subject)"
            
            # 3. Process the Body
            html_body = email_data.get("HtmlBody") or ""
            if html_body:
                # Convert images to base64 and then to Markdown
                processed_html = await self._process_html_images(html_body)
                body_md = html_to_markdown(processed_html)
            else:
                # Fallback to TextBody if HtmlBody is missing
                body_md = email_data.get("TextBody") or "No body content available."
            full_email_context = "\n\n".join([
                f"# {subject}",
                f"**From:** {from_addr}",
                f"**To:** {to_addr}",
                "---",
                body_md
            ])

            # 5. Create the BlockGroup using the full context
            email_bg = BlockGroup(
                id=str(uuid4()),
                index=1,
                parent_index=0,
                name=subject,
                type=GroupType.TEXT_SECTION,
                sub_type=GroupSubType.CONTENT,
                description=f"Task Email: {subject}",
                source_group_id=f"{task_id}_email_{email_message_id}",
                data=full_email_context,  # PASSING THE WHOLE CONTEXT HERE
                format=DataFormat.MARKDOWN,
                requires_processing=True
            )
            block_groups.append(email_bg)
        
        self._set_block_group_children(block_groups)
        
        try:
            blocks_container = BlocksContainer(
                blocks=[],
                block_groups=block_groups,
            )
            out = blocks_container.model_dump_json(indent=2).encode("utf-8")
            self.logger.info(f"Process task record done for record_id={record.id}, output_bytes={len(out)}")
            return out
        except Exception as e:
            self.logger.error(f"BlocksContainer build/serialize failed for record_id={record.id}: {e}", exc_info=True)
            raise

# --------------- Data Sync Methods ---------------

    def _format_discussion_timestamp(self, timestamp: Optional[str]) -> str:
        """Format discussion timestamp for context (e.g. 2026-02-07T06:38:23.000+0000 -> readable)."""
        if not timestamp or not isinstance(timestamp, str):
            return ""
        try:
            # Leave ISO as-is or shorten; downstream can parse
            return timestamp.replace("T", " ").replace(".000+0000", " UTC")[:25]
        except Exception:
            return timestamp[:25] if len(timestamp) > 25 else timestamp

    async def _iter_record_access(
        self,
        user_id: str,
        user_email: str,
        record_ids: List[str],
        api_version: str,
    ) -> AsyncGenerator[Tuple[str, str, str], None]:
        """
        Yields (record_id, user_email, access_level) tuples for a given user,
        streaming results as each composite batch resolves.
        """
        unique_ids = list(set(rid for rid in record_ids if rid))
        if not unique_ids or not user_id:
            return

        SOQL_BATCH_SIZE = 200
        COMPOSITE_BATCH_SIZE = 25

        base_query_url = f"/services/data/v{api_version}/query/"

        sub_requests = []
        for i in range(0, len(unique_ids), SOQL_BATCH_SIZE):
            batch_ids = unique_ids[i : i + SOQL_BATCH_SIZE]
            ids_str = "','".join(batch_ids)
            query = (
                f"SELECT RecordId, MaxAccessLevel FROM UserRecordAccess "
                f"WHERE UserId='{user_id}' "
                f"AND RecordId IN ('{ids_str}')"
            )
            sub_requests.append({
                "method": "GET",
                "url": f"{base_query_url}?q={quote(query)}",
                "referenceId": f"access_batch_{i}"
            })

        for i in range(0, len(sub_requests), COMPOSITE_BATCH_SIZE):
            batch = sub_requests[i : i + COMPOSITE_BATCH_SIZE]
            try:
                response = await self.data_source.composite(
                    version=api_version,
                    data={"compositeRequest": batch}
                )
                if not response.success or not response.data:
                    self.logger.error(f"Composite access check failed: {response.error}")
                    continue

                for item in response.data.get("compositeResponse") or []:
                    if item.get("httpStatusCode") != 200:
                        continue

                    for row in (item.get("body") or {}).get("records") or []:
                        rec_id = row.get("RecordId")
                        max_access = row.get("MaxAccessLevel")

                        if max_access in ("All", "Transfer"):
                            access_level = "OWNER"
                        elif max_access in ("Edit", "Delete"):
                            access_level = "WRITER"
                        elif max_access == "Read":
                            access_level = "READER"
                        else:
                            continue  # Skip OTHERS entirely — no yield, no DB write

                        yield rec_id, user_email, access_level

            except Exception as e:
                self.logger.error(
                    f"Error processing access batch {i} for user {user_email}: {e}",
                    exc_info=True
                )

    async def run_sync(self) -> None:
            """
            Runs a full synchronization from the Salesforce instance.
            Syncs roles first (with hierarchy), then users with role permissions.
            """
            self.logger.info("Reinitializing Salesforce token if needed...")
            await self._reinitialize_token_if_needed()
            if not self.data_source:
                self.logger.error("Salesforce data source not initialized")
                return

            try:
                self.logger.info("Starting Salesforce full sync.")

                self.sync_filters, self.indexing_filters = await load_connector_filters(
                    self.config_service, "salesforce", self.connector_id, self.logger
                )
                api_version = await self._get_api_version()

                # Step 1: Incremental sync for users
                self.logger.info("Syncing users (incremental)...")
                user_sync_point = await self.user_sync_point.read_sync_point(USERS_SYNC_POINT_KEY)
                last_sync_ts_ms = user_sync_point.get("lastSyncTimestamp")
                base_soql = (
                    "SELECT Id, FirstName, LastName, Email, Phone, MobilePhone, Title, CreatedDate, LastModifiedDate, UserRoleId FROM User"
                )
                if last_sync_ts_ms:
                    soql_datetime = _epoch_ms_to_soql_datetime(last_sync_ts_ms)
                    soql_query = f"{base_soql} WHERE LastModifiedDate >= {soql_datetime} ORDER BY LastModifiedDate ASC"
                    self.logger.info("Incremental user sync: fetching users modified since %s", soql_datetime)
                else:
                    soql_query = f"{base_soql} ORDER BY LastModifiedDate ASC"
                    self.logger.info("Full user sync: no previous sync point, fetching all users")
                response = await self._soql_query_paginated(api_version=api_version, q=soql_query)
                self.logger.info("Fetched %s users from Salesforce", len(response.data.get("records", [])))
                await self._sync_users(response.data.get("records", []))
                await self.user_sync_point.update_sync_point(
                    USERS_SYNC_POINT_KEY,
                    {"lastSyncTimestamp": get_epoch_timestamp_in_ms()},
                )

                # Step 2: Incremental sync for roles
                self.logger.info("Syncing roles and role permissions (incremental)...")
                roles_sync_point = await self.records_sync_point.read_sync_point(ROLES_SYNC_POINT_KEY)
                roles_last_ts_ms = roles_sync_point.get("lastSyncTimestamp")
                base_roles_soql = "SELECT Id, Name, ParentRoleId, DeveloperName, SystemModstamp FROM UserRole"
                base_user_to_role_soql = "SELECT Id, FirstName, LastName, Email, Phone, MobilePhone, Title, CreatedDate, LastModifiedDate, UserRoleId FROM User WHERE UserRoleId != null"
                if roles_last_ts_ms:
                    soql_datetime = _epoch_ms_to_soql_datetime(roles_last_ts_ms)
                    soql_query = f"{base_roles_soql} WHERE SystemModstamp >= {soql_datetime} ORDER BY SystemModstamp ASC"
                    soql_user_to_role_query = f"{base_user_to_role_soql} AND LastModifiedDate >= {soql_datetime} ORDER BY LastModifiedDate ASC"
                    self.logger.info("Incremental roles sync: fetching since %s", soql_datetime)
                else:
                    soql_query = f"{base_roles_soql} ORDER BY SystemModstamp ASC"
                    soql_user_to_role_query = f"{base_user_to_role_soql} ORDER BY LastModifiedDate ASC"
                    self.logger.info("Full roles sync: no previous sync point")
                response = await self._soql_query_paginated(api_version=api_version, q=soql_query)
                response_to_role = await self._soql_query_paginated(api_version=api_version, q=soql_user_to_role_query)
                await self._sync_roles(response.data.get("records", []), response_to_role.data.get("records", []))
                await self.records_sync_point.update_sync_point(
                    ROLES_SYNC_POINT_KEY,
                    {"lastSyncTimestamp": get_epoch_timestamp_in_ms()},
                )

                # Step 3: Full sync for Public Groups and Queues
                self.logger.info("Syncing Public Groups and Queues (full)...")
                base_groups_soql = "SELECT Id, Name, DeveloperName, Type, CreatedDate, LastModifiedDate FROM Group WHERE Type IN ('Regular', 'Queue')"
                response = await self._soql_query_paginated(api_version=api_version, q=base_groups_soql)
                group_records = response.data.get("records", [])
                await self._sync_user_groups(api_version=api_version, group_records=group_records)

                # Step 4: Incremental sync for Accounts
                self.logger.info("Syncing Accounts and sales prospect edges (incremental)...")
                accounts_sync_point = await self.records_sync_point.read_sync_point(ACCOUNTS_SYNC_POINT_KEY)
                accounts_last_ts_ms = accounts_sync_point.get("lastSyncTimestamp")
                soql_accounts_query = (
                    "SELECT Name, Website, Industry, Ownership, Phone, DunsNumber, Owner.Name, Type, Rating, "
                    "Id, CreatedDate, LastModifiedDate, SystemModstamp, "
                    "(SELECT Id, CloseDate, IsWon, IsClosed FROM Opportunities ORDER BY CloseDate ASC) "
                    "FROM Account"
                )
                if accounts_last_ts_ms:
                    soql_datetime = _epoch_ms_to_soql_datetime(accounts_last_ts_ms)
                    self.logger.info("Incremental account sync: fetching accounts changed since %s", soql_datetime)
                    account_records = await self._get_updated_account(api_version=api_version, soql_datetime=soql_datetime, soql_accounts_query=soql_accounts_query)
                else:
                    self.logger.info("Full account sync: no previous sync point")
                    response = await self._soql_query_paginated(api_version=api_version, q=soql_accounts_query)
                    account_records = response.data.get("records", [])
                await self._sync_accounts(account_records)
                await self.records_sync_point.update_sync_point(
                    ACCOUNTS_SYNC_POINT_KEY,
                    {"lastSyncTimestamp": get_epoch_timestamp_in_ms()},
                )

                # Step 5: Incremental sync for Contacts
                self.logger.info("Syncing Contacts and sales contact edges (incremental)...")
                contacts_sync_point = await self.records_sync_point.read_sync_point(CONTACTS_SYNC_POINT_KEY)
                contacts_last_ts_ms = contacts_sync_point.get("lastSyncTimestamp")
                base_contacts_soql = (
                    "SELECT Id, FirstName, LastName, Email, Phone, AccountId, Account.Name, Title, Department, "
                    "Description, LeadSource, CreatedDate, LastModifiedDate FROM Contact"
                )
                if contacts_last_ts_ms:
                    soql_datetime = _epoch_ms_to_soql_datetime(contacts_last_ts_ms)
                    soql_contacts_query = f"{base_contacts_soql} WHERE LastModifiedDate >= {soql_datetime} ORDER BY LastModifiedDate ASC"
                    self.logger.info("Incremental contacts sync: fetching since %s", soql_datetime)
                else:
                    soql_contacts_query = f"{base_contacts_soql} ORDER BY LastModifiedDate ASC"
                    self.logger.info("Full contacts sync: no previous sync point")
                response = await self._soql_query_paginated(api_version=api_version, q=soql_contacts_query)
                await self._sync_contacts(response.data.get("records", []))
                await self.records_sync_point.update_sync_point(
                    CONTACTS_SYNC_POINT_KEY,
                    {"lastSyncTimestamp": get_epoch_timestamp_in_ms()},
                )

                # Step 6: Incremental sync for Leads
                self.logger.info("Syncing Leads and sales lead edges (incremental)...")
                leads_sync_point = await self.records_sync_point.read_sync_point(LEADS_SYNC_POINT_KEY)
                leads_last_ts_ms = leads_sync_point.get("lastSyncTimestamp")
                base_leads_soql = (
                    "SELECT Id, FirstName, LastName, Email, Phone, Company, Title, Status, "
                    "Rating, Industry, LeadSource, AnnualRevenue, CreatedDate, LastModifiedDate, ConvertedDate, "
                    "ConvertedContactId FROM Lead"
                )
                if leads_last_ts_ms:
                    soql_datetime = _epoch_ms_to_soql_datetime(leads_last_ts_ms)
                    soql_leads_query = f"{base_leads_soql} WHERE LastModifiedDate >= {soql_datetime} ORDER BY LastModifiedDate ASC"
                    self.logger.info("Incremental leads sync: fetching since %s", soql_datetime)
                else:
                    soql_leads_query = f"{base_leads_soql} ORDER BY LastModifiedDate ASC"
                    self.logger.info("Full leads sync: no previous sync point")
                response = await self._soql_query_paginated(api_version=api_version, q=soql_leads_query)
                await self._sync_leads(response.data.get("records", []))
                await self.records_sync_point.update_sync_point(
                    LEADS_SYNC_POINT_KEY,
                    {"lastSyncTimestamp": get_epoch_timestamp_in_ms()},
                )

                # Step 7: Incremental sync for Products
                self.logger.info("Syncing Products (incremental)...")
                products_sync_point = await self.records_sync_point.read_sync_point(PRODUCTS_SYNC_POINT_KEY)
                products_last_ts_ms = products_sync_point.get("lastSyncTimestamp")
                base_products_soql = (
                    "SELECT Id, Name, ProductCode, Family, Description, CreatedDate, LastModifiedDate, SystemModstamp FROM Product2"
                )
                product_records = await self._get_updated_product(api_version, products_last_ts_ms, base_products_soql)
                await self._sync_products(product_records)
                await self.records_sync_point.update_sync_point(
                    PRODUCTS_SYNC_POINT_KEY,
                    {"lastSyncTimestamp": get_epoch_timestamp_in_ms()},
                )

                # Step 8: Incremental sync for Opportunities as deals, record groups per account, dealof/salesdeal/permission edges
                self.logger.info("Syncing Opportunities (deals) (incremental)...")
                opportunities_sync_point = await self.records_sync_point.read_sync_point(DEALS_SYNC_POINT_KEY)
                opportunities_last_ts_ms = opportunities_sync_point.get("lastSyncTimestamp")
                base_opportunities_soql = (
                    "SELECT Id, Name, AccountId, Account.Name, StageName, Amount, ExpectedRevenue, CloseDate, "
                    "Probability, Type, Description, OwnerId, Owner.Name, IsWon, IsClosed, "
                    "CreatedDate, LastModifiedDate FROM Opportunity"
                )
                opp_records = await self._get_updated_deal(api_version, opportunities_last_ts_ms, base_opportunities_soql)
                self.logger.info("Fetched %s opportunities from Salesforce", len(opp_records))
                await self._sync_opportunities(opp_records)
                await self.records_sync_point.update_sync_point(
                    DEALS_SYNC_POINT_KEY,
                    {"lastSyncTimestamp": get_epoch_timestamp_in_ms()},
                )

                # Step 8.5: Sync soldIn edges (Product -> Deal) from OpportunityLineItems
                self.logger.info("Syncing soldIn edges (Product -> Deal) from OpportunityLineItems...")
                products_sync_point = await self.records_sync_point.read_sync_point(SOLD_IN_SYNC_POINT_KEY)
                products_last_ts_ms = products_sync_point.get("lastSyncTimestamp")
                base_sold_in_soql = (
                    "SELECT Id, OpportunityId, Product2.Id, Product2.Name, UnitPrice, Quantity, "
                    "CreatedDate, LastModifiedDate, IsDeleted FROM OpportunityLineItem"
                )
                if products_last_ts_ms:
                    soql_datetime = _epoch_ms_to_soql_datetime(products_last_ts_ms)
                    soql_sold_in_query = f"{base_sold_in_soql} WHERE LastModifiedDate > {soql_datetime} ORDER BY LastModifiedDate ASC"
                    self.logger.info("Incremental OpportunityLineItem sync: fetching since %s", soql_datetime)
                else:
                    soql_sold_in_query = f"{base_sold_in_soql} ORDER BY LastModifiedDate ASC"
                    self.logger.info("Full OpportunityLineItem sync: no product sync point filter")
                sold_in_response = await self._soql_query_paginated(api_version=api_version, q=soql_sold_in_query, queryAll=True)
                sold_in_records = (sold_in_response.data or {}).get("records", [])
                self.logger.info("Fetched %s OpportunityLineItem records for soldIn edges", len(sold_in_records))
                await self._sync_sold_in_edges(sold_in_records, api_version)
                await self.records_sync_point.update_sync_point(
                    SOLD_IN_SYNC_POINT_KEY,
                    {"lastSyncTimestamp": get_epoch_timestamp_in_ms()},
                )

                # Step 9: Incremental sync for Cases
                self.logger.info("Syncing Cases (incremental)...")
                cases_sync_point = await self.records_sync_point.read_sync_point(CASES_SYNC_POINT_KEY)
                cases_last_ts_ms = cases_sync_point.get("lastSyncTimestamp")
                base_cases_soql = (
                    "SELECT Id, CaseNumber, Subject, Status, Priority, Type, OwnerId, Owner.Email, "
                    "Owner.Name, AccountId, Contact.Email, Contact.Name, CreatedBy.Email, "
                    "CreatedBy.Name, CreatedDate, LastModifiedDate, SystemModstamp FROM Case"
                )
                case_records = await self._get_updated_case(api_version, cases_last_ts_ms, base_cases_soql)
                await self._sync_cases(case_records)
                await self.records_sync_point.update_sync_point(
                    CASES_SYNC_POINT_KEY,
                    {"lastSyncTimestamp": get_epoch_timestamp_in_ms()},
                )

                # Step 10: Incremental sync for Tasks
                self.logger.info("Syncing Tasks (incremental)...")
                tasks_sync_point = await self.records_sync_point.read_sync_point(TASKS_SYNC_POINT_KEY)
                tasks_last_ts_ms = tasks_sync_point.get("lastSyncTimestamp")
                base_tasks_soql = (
                    "SELECT Id, Subject, Status, Priority, ActivityDate, Description, WhoId, Who.Email, WhatId, What.Name, What.Type, OwnerId, TaskSubtype, "
                    "Owner.Name, Owner.Email, CreatedBy.Name, CreatedBy.Email, CreatedDate, LastModifiedDate, SystemModstamp FROM Task"
                )
                task_records = await self._get_updated_task(api_version, tasks_last_ts_ms, base_tasks_soql)
                await self._sync_tasks(task_records)
                await self.records_sync_point.update_sync_point(
                    TASKS_SYNC_POINT_KEY,
                    {"lastSyncTimestamp": get_epoch_timestamp_in_ms()},
                )

                # Step 11: Sync Files
                self.logger.info("Syncing Files...")
                files_sync_point = await self.records_sync_point.read_sync_point(FILES_SYNC_POINT_KEY)
                files_last_ts_ms = files_sync_point.get("lastSyncTimestamp")
                file_data = await self._get_updated_file(api_version, files_last_ts_ms)
                await self._sync_files(api_version=api_version, file_records=file_data)

                await self.records_sync_point.update_sync_point(
                    FILES_SYNC_POINT_KEY,
                    {"lastSyncTimestamp": get_epoch_timestamp_in_ms()},
                )

                # Step 12: Sync permissions edges
                self.logger.info("Syncing permissions edges...")
                await self._sync_permissions_edges(api_version=api_version)

                self.logger.info("Salesforce full sync completed.")

            except Exception as ex:
                self.logger.error(f"Error in Salesforce connector run: {ex}", exc_info=True)
                raise

    async def get_updated_record_ids(
            self,
            since_timestamp_ms: int,
            record_types: Optional[List[str]] = None,
        ) -> Set[str]:
            """
            Get record IDs that have updated discussions, comments, or call logs since a timestamp.
            This queries:
            1. FeedItems (Chatter posts) created/modified since the cutoff
            2. FeedComments (replies to Chatter) created since the cutoff
            3. Tasks (Call logs) created/modified since the cutoff
            """
            if not self.data_source:
                self.logger.error("get_updated_record_ids: data source not initialized")
                return set()

            api_version = await self._get_api_version()
            cutoff_datetime = _epoch_ms_to_soql_datetime(since_timestamp_ms)
            updated_record_ids: Set[str] = set()

            # ── Query 1: FeedItems created or modified since cutoff ──────────────
            # FeedItems track Chatter posts, tracked field changes, content posts, etc.
            # ParentId points to the parent record (Opportunity, Account, Case, etc.)
            feeditem_soql = (
                "SELECT ParentId, Parent.Type "
                "FROM FeedItem "
                f"WHERE (CreatedDate >= {cutoff_datetime} OR LastModifiedDate >= {cutoff_datetime})"
            )

            if record_types:
                # Filter by parent object type (e.g. only Opportunities and Cases)
                type_list = ", ".join(f"'{t}'" for t in record_types)
                feeditem_soql += f" AND Parent.Type IN ({type_list})"

            try:
                resp = await self._soql_query_paginated(api_version=api_version, q=feeditem_soql)
                if resp.success and resp.data:
                    for row in resp.data.get("records") or []:
                        parent_id = row.get("ParentId")
                        if parent_id:
                            updated_record_ids.add(parent_id)
                self.logger.info(
                    f"Found {len(updated_record_ids)} records with updated FeedItems since {cutoff_datetime}"
                )
            except Exception as e:
                self.logger.error(f"Error querying FeedItems: {e}", exc_info=True)

            # ── Query 2: FeedComments created since cutoff ────────────────────────
            # FeedComments are replies to FeedItems. We need to join back to FeedItem
            # to get the ParentId (the actual record ID).
            # Note: FeedComment.FeedItemId -> FeedItem.Id -> FeedItem.ParentId
            feedcomment_soql = (
                "SELECT FeedItemId "
                "FROM FeedComment "
                f"WHERE CreatedDate >= {cutoff_datetime}"
            )

            try:
                resp = await self._soql_query_paginated(api_version=api_version, q=feedcomment_soql)
                if resp.success and resp.data:
                    feed_item_ids = [
                        row.get("FeedItemId")
                        for row in resp.data.get("records") or []
                        if row.get("FeedItemId")
                    ]

                    if feed_item_ids:
                        # Batch lookup of FeedItem.ParentId for all these FeedItems
                        # Salesforce allows up to ~4000 items in an IN clause, but we batch
                        # at 500 for safety (same as your existing pattern)
                        BATCH_SIZE = 500
                        for i in range(0, len(feed_item_ids), BATCH_SIZE):
                            batch = feed_item_ids[i : i + BATCH_SIZE]
                            in_clause = ", ".join(f"'{fid}'" for fid in batch)

                            lookup_soql = (
                                f"SELECT ParentId, Parent.Type FROM FeedItem WHERE Id IN ({in_clause})"
                            )
                            if record_types:
                                type_list = ", ".join(f"'{t}'" for t in record_types)
                                lookup_soql += f" AND Parent.Type IN ({type_list})"

                            lookup_resp = await self._soql_query_paginated(
                                api_version=api_version, q=lookup_soql
                            )
                            if lookup_resp.success and lookup_resp.data:
                                for row in lookup_resp.data.get("records") or []:
                                    parent_id = row.get("ParentId")
                                    if parent_id:
                                        updated_record_ids.add(parent_id)

                self.logger.info(
                    f"Found {len(updated_record_ids)} total records after including FeedComment updates"
                )
            except Exception as e:
                self.logger.error(f"Error querying FeedComments: {e}", exc_info=True)

            # ── Query 3: Tasks (Call logs) created or modified since cutoff ──────
            # TaskSubtype = 'Call' identifies call logs
            # WhatId points to the parent record (Opportunity, Case, Account, etc.)
            task_soql = (
                "SELECT WhatId, What.Type "
                "FROM Task "
                f"WHERE TaskSubtype = 'Call' "
                f"AND (CreatedDate >= {cutoff_datetime} OR LastModifiedDate >= {cutoff_datetime})"
            )

            if record_types:
                type_list = ", ".join(f"'{t}'" for t in record_types)
                task_soql += f" AND What.Type IN ({type_list})"

            try:
                resp = await self._soql_query_paginated(api_version=api_version, q=task_soql)
                if resp.success and resp.data:
                    for row in resp.data.get("records") or []:
                        what_id = row.get("WhatId")
                        if what_id:
                            updated_record_ids.add(what_id)
                self.logger.info(
                    f"Found {len(updated_record_ids)} total records after including Call logs"
                )
            except Exception as e:
                self.logger.error(f"Error querying Tasks: {e}", exc_info=True)

            # ── Query 4: Comments on Call logs ────────────────────────────────────
            # Call logs can have FeedComments too — we need to find Tasks that have
            # new comments, then add their WhatId to the set.
            # Strategy: Query FeedComments where ParentId (the Task.Id) is a Call Task,
            # then look up the Task to get WhatId.
            call_comment_soql = (
                "SELECT FeedItemId "
                "FROM FeedComment "
                f"WHERE CreatedDate >= {cutoff_datetime}"
            )

            try:
                resp = await self._soql_query_paginated(api_version=api_version, q=call_comment_soql)
                if resp.success and resp.data:
                    feed_item_ids_for_calls = [
                        row.get("FeedItemId")
                        for row in resp.data.get("records") or []
                        if row.get("FeedItemId")
                    ]

                    if feed_item_ids_for_calls:
                        BATCH_SIZE = 500
                        for i in range(0, len(feed_item_ids_for_calls), BATCH_SIZE):
                            batch = feed_item_ids_for_calls[i : i + BATCH_SIZE]
                            in_clause = ", ".join(f"'{fid}'" for fid in batch)

                            # Find FeedItems whose Parent is a Task with TaskSubtype = 'Call'
                            lookup_soql = (
                                f"SELECT ParentId, Parent.Type FROM FeedItem WHERE Id IN ({in_clause}) "
                                f"AND Parent.Type = 'Task'"
                            )

                            lookup_resp = await self._soql_query_paginated(
                                api_version=api_version, q=lookup_soql
                            )
                            if lookup_resp.success and lookup_resp.data:
                                task_ids = [
                                    row.get("ParentId")
                                    for row in lookup_resp.data.get("records") or []
                                    if row.get("ParentId")
                                ]

                                if task_ids:
                                    # Now fetch the WhatId for these Tasks (filtering TaskSubtype = Call)
                                    task_in_clause = ", ".join(f"'{tid}'" for tid in task_ids)
                                    task_lookup_soql = (
                                        f"SELECT WhatId, What.Type FROM Task "
                                        f"WHERE Id IN ({task_in_clause}) AND TaskSubtype = 'Call'"
                                    )

                                    if record_types:
                                        type_list = ", ".join(f"'{t}'" for t in record_types)
                                        task_lookup_soql += f" AND What.Type IN ({type_list})"

                                    task_resp = await self._soql_query_paginated(
                                        api_version=api_version, q=task_lookup_soql
                                    )
                                    if task_resp.success and task_resp.data:
                                        for row in task_resp.data.get("records") or []:
                                            what_id = row.get("WhatId")
                                            if what_id:
                                                updated_record_ids.add(what_id)

                self.logger.info(
                    f"Found {len(updated_record_ids)} total records after including Call log comments"
                )
            except Exception as e:
                self.logger.error(f"Error querying Call log comments: {e}", exc_info=True)

            self.logger.info(
                f"get_updated_record_ids complete: {len(updated_record_ids)} unique record IDs "
                f"with updated activity since {cutoff_datetime}"
            )
            return updated_record_ids

    def user_to_app_user(self, user: Dict[str, Any]) -> AppUser:
        return AppUser(
            app_name=self.connector_name,
            connector_id=self.connector_id,
            source_user_id=user.get("Id"),
            email=user.get("Email") or "",
            first_name=user.get("FirstName") or "",
            last_name=user.get("LastName") or "",
            full_name=f"{user.get('FirstName') or ''} {user.get('LastName') or ''}".strip(),
            org_id=self.data_entities_processor.org_id,
            title=user.get("Title"),
            source_created_at=_parse_salesforce_timestamp(user.get("CreatedDate")) or None,
            source_updated_at=_parse_salesforce_timestamp(user.get("LastModifiedDate")) or None
        )

    def role_to_app_role(self, role: Dict[str, Any]) -> AppRole:
        return AppRole(
            app_name=self.connector_name,
            connector_id=self.connector_id,
            source_role_id=role.get("Id"),  # becomes externalRoleId in ArangoDB
            name=role.get("Name", ""),
            org_id=self.data_entities_processor.org_id,
            parent_role_id=role.get("ParentRoleId", None),
            source_updated_at=_parse_salesforce_timestamp(role.get("SystemModstamp"))
        )

    async def _sync_users(self, user_records: List[Dict[str, Any]]) -> None:
        """
        Sync all users from Salesforce (user documents only).
        Role permission edges are created later in _sync_roles.
        
        Fetches from Salesforce:
        - FirstName, LastName -> combined into full_name
        - Email, Phone (or MobilePhone), Title, CreatedDate, LastModifiedDate
        """
        try:
            if not user_records:
                self.logger.info("No users found in Salesforce")
                return

            all_users = [self.user_to_app_user(user) for user in user_records]

            await self.data_entities_processor.on_new_app_users(all_users)
            self.logger.info("Finished syncing Salesforce users")

        except Exception as e:
            self.logger.error(f"Error syncing users: {e}", exc_info=True)
            raise

    async def _sync_roles(self, role_records: List[Dict[str, Any]], user_role_records: List[Dict[str, Any]]) -> None:
        """
        Sync all roles from Salesforce with parent hierarchy.        
        Creates all roles with externalRoleId (Salesforce role ID)
        """
        try:
            self.logger.info("Starting Salesforce role sync...")

            if not self.data_source:
                self.logger.error("Salesforce data source not initialized")
                return

            # Map Salesforce role ID -> list of AppUser for permission edges
            role_to_app_users: Dict[str, List[AppUser]] = {}
            for user in user_role_records:
                user_role_id = user.get("UserRoleId")
                if user_role_id not in role_to_app_users:
                    role_to_app_users[user_role_id] = []
                role_to_app_users[user_role_id].append(self.user_to_app_user(user))

            if not role_records:
                self.logger.info("No roles found in Salesforce")
                return

            roles_first_pass: List[Tuple[AppRole, List[AppUser]]] = []

            for role in role_records:
                sf_role_id = role.get("Id")
                app_role = self.role_to_app_role(role)
                members = role_to_app_users.get(sf_role_id, [])
                roles_first_pass.append((app_role, members))

            if roles_first_pass:
                self.logger.info(f"Creating {len(roles_first_pass)} roles (first pass)")
                await self.data_entities_processor.on_new_app_roles(roles_first_pass)

            self.logger.info("Finished syncing Salesforce roles")

        except Exception as e:
            self.logger.error(f"Error syncing roles: {e}", exc_info=True)
            raise

    async def _sync_user_groups(self, api_version: str, group_records: List[Dict[str, Any]]) -> None:
        """
        Sync Public Groups and Queues from Salesforce with flattened membership hierarchy.
        
        Steps:
        1. Call _flatten_group_members to get map of group ID -> set of {user_id, email}
        2. Loop through provided group_records
        3. For each group, get flattened users from the map
        4. Create UserGroup nodes and permission edges (READ and WRITE) from users to groups
        """
        try:
            self.logger.info("Starting Salesforce Public Groups and Queues sync...")

            if not group_records:
                self.logger.info("No groups found in Salesforce")
                return

            # Step 1: Get flattened group membership map
            flattened_user_memberships = await self._flatten_group_members(api_version)
            
            if not flattened_user_memberships:
                self.logger.warning("No flattened group memberships found")
                return

            # Step 2: Create AppUserGroup objects and AppUser objects for each group
            user_groups_batch: List[Tuple[AppUserGroup, List[AppUser]]] = []

            for group in group_records:
                group_id = group.get("Id")
                if not group_id:
                    continue
                
                group_name = group.get("Name", "")
                group_type = group.get("Type", "")
                
                # Get flattened users for this group from the map
                user_data_set = flattened_user_memberships.get(group_id, set())
                
                if not user_data_set:
                    self.logger.info(f"Group {group_name} ({group_id}) has no users")
                    continue

                # Create AppUserGroup
                app_user_group = AppUserGroup(
                    app_name=self.connector_name,
                    connector_id=self.connector_id,
                    source_user_group_id=group_id,
                    name=group_name,
                    org_id=self.data_entities_processor.org_id,
                    description=f"Salesforce {group_type}",
                    source_created_at=_parse_salesforce_timestamp(group.get("CreatedDate")),
                    source_updated_at=_parse_salesforce_timestamp(group.get("LastModifiedDate"))
                )

                # Create AppUser objects for all flattened users in this group
                app_users = []
                for user_id, email in user_data_set:
                    app_user = AppUser(
                        app_name=self.connector_name,
                        connector_id=self.connector_id,
                        source_user_id=user_id,
                        email=email,
                        full_name="",
                        org_id=self.data_entities_processor.org_id,
                    )
                    app_users.append(app_user)

                if app_users:
                    user_groups_batch.append((app_user_group, app_users))

            # Step 3: Create UserGroups and READ permission edges
            if user_groups_batch:
                self.logger.info(f"Creating {len(user_groups_batch)} user groups")
                await self.data_entities_processor.on_new_user_groups(user_groups_batch)

            self.logger.info("Finished syncing Salesforce Public Groups and Queues")

        except Exception as e:
            self.logger.error(f"Error syncing user groups: {e}", exc_info=True)
            raise

    def _parse_opportunities(
        self, acc: Dict
    ) -> Tuple[Optional[int], bool]:
        """
        Parse Opportunities subquery (already sorted by CloseDate ASC).
        Returns (end_time_ms, active_customer):
        - end_time_ms: first won opportunity CloseDate (epoch ms), or None.
        - active_customer: True if any opportunity has IsClosed == False.
        """
        opportunities = acc.get("Opportunities") or {}
        records = opportunities.get("records") or []
        end_time_ms: Optional[int] = None
        active_customer = False
        for opp in records:
            if opp.get("IsClosed") is False:
                active_customer = True
            if end_time_ms is None and opp.get("IsWon") is True:
                close_date = opp.get("CloseDate")
                parsed = _parse_salesforce_timestamp(close_date)
                if parsed is not None:
                    end_time_ms = parsed
        return (end_time_ms, active_customer)

    def _build_product_record(self, product: Dict[str, Any]) -> ProductRecord:
        """
        Build a ProductRecord from a raw Salesforce Product2 API row.

        Expected keys: Id, Name, ProductCode, Family, SystemModstamp, CreatedDate, LastModifiedDate.
        """
        product_id = product["Id"]
        weburl = HttpUrl(f"{self.salesforce_instance_url}/{product_id}")
        product_record = ProductRecord(
            record_name=product.get("Name") or "",
            record_type=RecordType.PRODUCT,
            record_group_type=RecordGroupType.PRODUCT,
            external_record_id=product_id,
            external_revision_id=product.get("SystemModstamp"),
            external_record_group_id=self.data_entities_processor.org_id + "-product",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            mime_type=MimeTypes.BLOCKS.value,
            product_code=product.get("ProductCode"),
            product_family=product.get("Family"),
            source_created_at=_parse_salesforce_timestamp(product.get("CreatedDate")),
            source_updated_at=_parse_salesforce_timestamp(product.get("LastModifiedDate")),
            preview_renderable=False,
            weburl=str(weburl) if weburl is not None else None,
        )
        return product_record

    def _build_deal_record(self, opp: Dict[str, Any]) -> DealRecord:
        """
        Build a DealRecord from a raw Salesforce Opportunity API row.

        Expected keys: Id, Name, AccountId, Account (nested), StageName, Amount, ExpectedRevenue,
        CloseDate, Probability, Type, OwnerId, IsWon, IsClosed, CreatedDate, LastModifiedDate.
        """
        opp_id = opp["Id"]
        account_id = "UNASSIGNED-DEAL"
        if opp.get("AccountId") and opp.get("Account", {}).get("Name"):
            account_id = opp["AccountId"]
        last_modified_epoch = _parse_salesforce_timestamp(opp.get("LastModifiedDate"))
        latest_comment_epoch: Optional[int] = opp.get("_latest_comment_epoch")
        effective_revision_epoch: Optional[int] = max(
            filter(lambda x: x is not None, [last_modified_epoch, latest_comment_epoch]),
            default=None,
        )
        weburl = (
            str(HttpUrl(f"{self.salesforce_instance_url}/{opp_id}"))
            if self.salesforce_instance_url and opp_id
            else None
        )
        deal_record = DealRecord(
            record_name=opp.get("Name") or "",
            record_type=RecordType.DEAL,
            record_group_type=RecordGroupType.DEALS,
            external_record_id=opp_id,
            external_record_group_id=account_id,
            version=1,
            external_revision_id=str(effective_revision_epoch) if effective_revision_epoch is not None else None,
            origin=OriginTypes.CONNECTOR,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            mime_type=MimeTypes.BLOCKS.value,
            org_id=self.data_entities_processor.org_id,
            name=opp.get("Name") or "",
            amount=float(opp["Amount"]) if opp.get("Amount") is not None else None,
            expected_revenue=float(opp["ExpectedRevenue"]) if opp.get("ExpectedRevenue") is not None else None,
            expected_close_date=str(opp["CloseDate"]) if opp.get("CloseDate") else None,
            conversion_probability=float(opp["Probability"]) if opp.get("Probability") is not None else None,
            type=opp.get("Type"),
            owner_id=opp.get("OwnerId"),
            is_won=opp.get("IsWon"),
            is_closed=opp.get("IsClosed"),
            created_date=str(opp["CreatedDate"]) if opp.get("CreatedDate") else None,
            close_date=str(opp["CloseDate"]) if opp.get("CloseDate") else None,
            source_created_at=_parse_salesforce_timestamp(opp.get("CreatedDate")),
            source_updated_at=last_modified_epoch,
            inherit_permissions=False,
            preview_renderable=False,
            weburl=weburl,
        )
        return deal_record

    def _build_case_record(self, case_row: Dict[str, Any]) -> TicketRecord:
        """
        Build a TicketRecord (CASE) from a raw Salesforce Case API row.

        Expected keys: Id, Status, Priority, Type, Owner (nested), Contact (nested),
        CreatedBy (nested), AccountId, SystemModstamp, CreatedDate, LastModifiedDate.
        """
        case_id = case_row["Id"]

        case_number = case_row.get("CaseNumber")
        case_name = case_row.get("Subject") or f"Case {case_number}"
        account = case_row.get("AccountId") or None
        external_record_group_id = account if account else "UNASSIGNED-CASE"
        weburl = None
        if self.salesforce_instance_url and case_id:
            try:
                weburl = HttpUrl(f"{self.salesforce_instance_url}/{case_id}")
            except Exception:
                pass
        owner = case_row.get("Owner") or {}
        reporter = case_row.get("Contact") or {}
        created_by = case_row.get("CreatedBy") or {}
        system_modstamp_epoch = _parse_salesforce_timestamp(case_row.get("SystemModstamp"))
        latest_feeditem_epoch: Optional[int] = case_row.get("_latest_feeditem_epoch")
        effective_revision_epoch: Optional[int] = max(
            filter(lambda x: x is not None, [system_modstamp_epoch, latest_feeditem_epoch]),
            default=None,
        )
        case_record = TicketRecord(
            record_name=case_name,
            record_type=RecordType.CASE,
            record_group_type=RecordGroupType.CASE,
            external_record_id=case_id,
            external_record_group_id=external_record_group_id,
            external_revision_id=str(effective_revision_epoch) if effective_revision_epoch is not None else None,
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            mime_type=MimeTypes.BLOCKS.value,
            status=case_row.get("Status"),
            priority=case_row.get("Priority"),
            type=case_row.get("Type"),
            assignee=owner.get("Name"),
            assignee_email=owner.get("Email"),
            reporter_name=reporter.get("Name"),
            reporter_email=reporter.get("Email"),
            creator_name=created_by.get("Name"),
            creator_email=created_by.get("Email"),
            source_created_at=_parse_salesforce_timestamp(case_row.get("CreatedDate")),
            source_updated_at=_parse_salesforce_timestamp(case_row.get("LastModifiedDate")),
            inherit_permissions=False,
            preview_renderable=False,
            weburl=str(weburl) if weburl is not None else None,
        )
        return case_record

    def _build_task_record(self, task_row: Dict[str, Any]) -> TicketRecord:
        """
        Build a TicketRecord (TASK) from a raw Salesforce Task API row.

        Expected keys: Id, Subject, Status, Priority, TaskSubtype, WhatId, What (nested),
        Owner (nested), CreatedBy (nested), SystemModstamp, ActivityDate, CreatedDate,
        LastModifiedDate.
        """
        task_id = task_row["Id"]
        what_id = task_row.get("WhatId") or None
        what_obj = task_row.get("What")
        what_type = (what_obj.get("Type") if isinstance(what_obj, dict) else None) or None
        parent_id = None
        if what_type in ("Opportunity", "Case", "Product2"):
            parent_id = what_id
            external_record_group_id = "UNASSIGNED-TASK"
        elif what_type == "Account":
            external_record_group_id = what_id
        else:
            external_record_group_id = "UNASSIGNED-TASK"
        weburl = None
        if self.salesforce_instance_url and task_id:
            try:
                weburl = HttpUrl(f"{self.salesforce_instance_url}/{task_id}")
            except Exception:
                pass
        subject = task_row.get("Subject") or ""
        record_name = subject if subject else f"Task {task_id}"
        owner = task_row.get("Owner") or {}
        created_by = task_row.get("CreatedBy") or {}
        task_record = TicketRecord(
            record_name=record_name,
            record_type=RecordType.TASK,
            record_group_type=RecordGroupType.TASK,
            external_record_id=task_id,
            external_record_group_id=external_record_group_id,
            parent_external_record_id=parent_id,
            external_revision_id=task_row.get("SystemModstamp") or None,
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            mime_type=MimeTypes.BLOCKS.value,
            status=task_row.get("Status"),
            priority=task_row.get("Priority"),
            type=task_row.get("TaskSubtype"),
            assignee=owner.get("Name"),
            assignee_email=owner.get("Email"),
            creator_name=created_by.get("Name"),
            creator_email=created_by.get("Email"),
            due_date_timestamp=_parse_salesforce_timestamp(task_row.get("ActivityDate")),
            source_created_at=_parse_salesforce_timestamp(task_row.get("CreatedDate")),
            source_updated_at=_parse_salesforce_timestamp(task_row.get("LastModifiedDate")),
            preview_renderable=False,
            inherit_permissions=False,
            weburl=str(weburl) if weburl is not None else None,
        )
        return task_record

    def _build_file_record(
        self,
        meta: Dict[str, Any],
        ext_id: str,
        parent_id: Optional[str] = None,
        external_record_group_id: Optional[str] = None,
    ) -> Optional[FileRecord]:
        """
        Build a FileRecord from a Salesforce ContentVersion metadata dict.

        Args:
            meta: ContentVersion row (keys: Id, ContentDocumentId, PathOnClient, Title,
                  ContentSize, FileExtension, Checksum, LastModifiedDate, CreatedDate).
            ext_id: The ContentVersionId used as external_record_id.
            parent_id: Optional external record ID of the linked parent (Opportunity/Case/Task).
            external_record_group_id: Optional external group ID for the record group.

        Returns FileRecord, or None if meta is missing required fields.
        """
        if not meta or not meta.get("Id"):
            return None
        doc_id = meta.get("ContentDocumentId") or ext_id
        mime_type = mimetypes.guess_type(meta.get("PathOnClient") or "")[0] or MimeTypes.UNKNOWN.value
        cv_id = meta["Id"]
        weburl = HttpUrl(f"{self.salesforce_instance_url}/{doc_id}")
        file_record = FileRecord(
            id=str(uuid4()),
            record_name=meta.get("PathOnClient") or meta.get("Title") or "Unknown",
            record_type=RecordType.FILE,
            record_group_type=RecordGroupType.SALESFORCE_FILE.value,
            external_record_id=ext_id,
            external_revision_id=cv_id,
            version=1,
            origin=OriginTypes.CONNECTOR.value,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            weburl=str(weburl) if weburl is not None else None,
            size_in_bytes=meta.get("ContentSize"),
            extension=meta.get("FileExtension"),
            md5_hash=meta.get("Checksum"),
            mime_type=mime_type,
            is_file=True,
            source_updated_at=_parse_salesforce_timestamp(meta.get("LastModifiedDate")),
            source_created_at=_parse_salesforce_timestamp(meta.get("CreatedDate")),
            external_record_group_id=external_record_group_id,
            parent_external_record_id=parent_id,
        )
        return file_record

    async def _fetch_salesforce_record_if_updated(
        self,
        external_id: str,
        record_type: str,
        since_timestamp_ms: int,
        api_version: str,
    ) -> Optional[Record]:
        """
        Query Salesforce for a single record by external ID and return a fully-built
        Record if it was modified after since_timestamp_ms, or None otherwise.

        For each supported type the method:
        1. Converts since_timestamp_ms to a SOQL datetime string.
        2. Runs a typed SOQL query filtered by Id (or ContentDocumentId for FILE)
           and LastModifiedDate >= soql_datetime.
        3. If the result has no rows returns None.
        4. Builds and returns the typed record using the appropriate _build_* method.
        """
        soql_datetime = _epoch_ms_to_soql_datetime(since_timestamp_ms)

        try:
            if record_type == RecordType.DEAL:
                soql = (
                    "SELECT Id, Name, AccountId, Account.Name, StageName, Amount, ExpectedRevenue, CloseDate, "
                    "Probability, Type, Description, OwnerId, Owner.Name, IsWon, IsClosed, "
                    f"CreatedDate, LastModifiedDate FROM Opportunity "
                    f"WHERE Id = '{external_id}' AND LastModifiedDate >= {soql_datetime}"
                )
                resp = await self._soql_query_paginated(api_version=api_version, q=soql)
                records = (resp.data or {}).get("records", [])
                if not records:
                    return None
                return self._build_deal_record(records[0])

            elif record_type == RecordType.CASE:
                soql = (
                    "SELECT Id, CaseNumber, Subject, Status, Priority, Type, OwnerId, Owner.Email, "
                    "Owner.Name, AccountId, Contact.Email, Contact.Name, CreatedBy.Email, "
                    "CreatedBy.Name, CreatedDate, LastModifiedDate, SystemModstamp FROM Case "
                    f"WHERE Id = '{external_id}' AND LastModifiedDate >= {soql_datetime}"
                )
                resp = await self._soql_query_paginated(api_version=api_version, q=soql)
                records = (resp.data or {}).get("records", [])
                if not records:
                    return None
                return self._build_case_record(records[0])

            elif record_type == RecordType.TASK:
                soql = (
                    "SELECT Id, Subject, Status, Priority, ActivityDate, Description, WhoId, Who.Email, "
                    "WhatId, What.Name, What.Type, OwnerId, TaskSubtype, "
                    "Owner.Name, Owner.Email, CreatedBy.Name, CreatedBy.Email, "
                    f"CreatedDate, LastModifiedDate, SystemModstamp FROM Task "
                    f"WHERE Id = '{external_id}' AND LastModifiedDate >= {soql_datetime}"
                )
                resp = await self._soql_query_paginated(api_version=api_version, q=soql)
                records = (resp.data or {}).get("records", [])
                if not records:
                    return None
                return self._build_task_record(records[0])

            elif record_type == RecordType.FILE:
                # external_record_id is "{doc_id}-{linked_entity_id}" (linked) or just doc_id (unlinked)
                doc_id = external_id.split("-")[0]
                soql = (
                    "SELECT Id, ContentDocumentId, Title, PathOnClient, ContentSize, "
                    "FileExtension, FileType, LastModifiedDate, CreatedDate, Checksum "
                    f"FROM ContentVersion WHERE IsLatest = true "
                    f"AND ContentDocumentId = '{doc_id}' AND LastModifiedDate >= {soql_datetime}"
                )
                resp = await self._soql_query_paginated(api_version=api_version, q=soql)
                records = (resp.data or {}).get("records", [])
                if not records:
                    return None
                # Pass original external_id to preserve composite identity
                return self._build_file_record(records[0], ext_id=external_id)

            elif record_type == RecordType.PRODUCT:
                soql = (
                    "SELECT Id, Name, ProductCode, Family, CreatedDate, LastModifiedDate FROM Product2 "
                    f"WHERE Id = '{external_id}' AND LastModifiedDate >= {soql_datetime}"
                )
                resp = await self._soql_query_paginated(api_version=api_version, q=soql)
                records = (resp.data or {}).get("records", [])
                if not records:
                    return None
                return self._build_product_record(records[0])
            else:
                self.logger.warning(
                    "Unsupported record type for staleness check: %s (external_id=%s)",
                    record_type,
                    external_id,
                )
                return None

        except Exception as e:
            self.logger.error(
                "Error checking if record %s (%s) is updated: %s",
                external_id,
                record_type,
                e,
                exc_info=True,
            )
            return None

    async def _get_updated_deal(
        self,
        api_version: str,
        opportunities_last_ts_ms: Optional[int],
        base_opportunities_soql: str,
    ) -> List[Dict[str, Any]]:
        """
        Fetch Opportunity records for incremental sync.

        When a sync point timestamp is available, returns the union of:
        - Opportunities whose LastModifiedDate >= cutoff (direct edits)
        - Opportunities that have new Chatter posts (FeedItem) since the cutoff
        - Opportunities that have new Chatter replies (FeedComment) since the cutoff
        """
        if opportunities_last_ts_ms:
            soql_datetime = _epoch_ms_to_soql_datetime(opportunities_last_ts_ms)

            # 1. Opportunities modified directly
            soql_modified = (
                f"{base_opportunities_soql} WHERE LastModifiedDate >= {soql_datetime} "
                f"ORDER BY LastModifiedDate ASC"
            )

            # 2. Opportunities with new Chatter POSTS (FeedItem)
            soql_feeditems = (
                f"{base_opportunities_soql} WHERE Id IN ("
                f"SELECT ParentId FROM FeedItem WHERE CreatedDate >= {soql_datetime}"
                f") AND IsDeleted = false"
            )

            # 3. Opportunities with new Chatter REPLIES (FeedComment)
            soql_feedcomments = (
                f"{base_opportunities_soql} WHERE Id IN ("
                f"SELECT ParentId FROM FeedComment WHERE CreatedDate >= {soql_datetime}"
                f") AND IsDeleted = false"
            )

            # 4. Fetch the latest timestamps for BOTH to fold into external_revision_id
            soql_item_dates = (
                f"SELECT ParentId, CreatedDate FROM FeedItem "
                f"WHERE CreatedDate >= {soql_datetime} ORDER BY CreatedDate DESC"
            )
            soql_comment_dates = (
                f"SELECT ParentId, CreatedDate FROM FeedComment "
                f"WHERE CreatedDate >= {soql_datetime} ORDER BY CreatedDate DESC"
            )

            # Execute the queries
            resp_modified = await self._soql_query_paginated(api_version=api_version, q=soql_modified)
            resp_feeditems = await self._soql_query_paginated(api_version=api_version, q=soql_feeditems)
            resp_feedcomments = await self._soql_query_paginated(api_version=api_version, q=soql_feedcomments)
            
            resp_item_dates = await self._soql_query_paginated(api_version=api_version, q=soql_item_dates)
            resp_comment_dates = await self._soql_query_paginated(api_version=api_version, q=soql_comment_dates)

            self.logger.info(
                "Incremental opportunities: %s modified, %s from FeedItem, %s from FeedComment",
                len(resp_modified.data.get("records", [])),
                len(resp_feeditems.data.get("records", [])),
                len(resp_feedcomments.data.get("records", [])),
            )

            # Build a map: opportunity_id -> latest Feed epoch ms (checking both items and comments)
            latest_feed_epoch: Dict[str, int] = {}
            
            # Helper to process dates
            def _process_feed_dates(feed_records):
                for f in feed_records:
                    parent_id = f.get("ParentId")
                    created_date = f.get("CreatedDate")
                    if not parent_id or not created_date:
                        continue
                    epoch = _parse_salesforce_timestamp(created_date)
                    if epoch is not None:
                        if parent_id not in latest_feed_epoch or epoch > latest_feed_epoch[parent_id]:
                            latest_feed_epoch[parent_id] = epoch

            _process_feed_dates((resp_item_dates.data or {}).get("records", []))
            _process_feed_dates((resp_comment_dates.data or {}).get("records", []))

            # Deduplicate across all three sources
            seen: Set[str] = set()
            all_opp_records: List[Dict[str, Any]] = []
            
            combined_records = (
                resp_modified.data.get("records", [])
                + resp_feeditems.data.get("records", [])
                + resp_feedcomments.data.get("records", [])
            )

            for row in combined_records:
                opp_id = row.get("Id")
                if opp_id and opp_id not in seen:
                    seen.add(opp_id)
                    # I kept your original dictionary key '_latest_comment_epoch' so it doesn't break 
                    # your downstream `_build_deal_record` function, even though it now includes FeedItems too.
                    row["_latest_comment_epoch"] = latest_feed_epoch.get(opp_id)
                    all_opp_records.append(row)

            self.logger.info("Incremental opportunities after dedupe: %s unique", len(all_opp_records))
            return all_opp_records

        soql_full = f"{base_opportunities_soql} ORDER BY LastModifiedDate ASC"
        self.logger.info("Full deals sync: no previous sync point")
        response = await self._soql_query_paginated(api_version=api_version, q=soql_full)
        return response.data.get("records", [])

    async def _get_updated_product(
        self,
        api_version: str,
        products_last_ts_ms: Optional[int],
        base_products_soql: str,
    ) -> List[Dict[str, Any]]:
        """
        Fetch Product2 records for incremental sync.

        Returns products modified since the last sync point, or all products
        when no sync point is available.
        """
        if products_last_ts_ms:
            soql_datetime = _epoch_ms_to_soql_datetime(products_last_ts_ms)
            soql = f"{base_products_soql} WHERE LastModifiedDate >= {soql_datetime} ORDER BY LastModifiedDate ASC"
            self.logger.info("Incremental products sync: fetching since %s", soql_datetime)
        else:
            soql = f"{base_products_soql} ORDER BY LastModifiedDate ASC"
            self.logger.info("Full products sync: no previous sync point")

        response = await self._soql_query_paginated(api_version=api_version, q=soql)
        return response.data.get("records", [])

    async def _get_updated_case(
        self,
        api_version: str,
        cases_last_ts_ms: Optional[int],
        base_cases_soql: str,
    ) -> List[Dict[str, Any]]:
        """
        Fetch Case records for incremental sync.
        """
        if cases_last_ts_ms:
            soql_datetime = _epoch_ms_to_soql_datetime(cases_last_ts_ms)

            # 1. Cases modified directly
            soql_modified = (
                f"{base_cases_soql} WHERE LastModifiedDate >= {soql_datetime} "
                f"ORDER BY LastModifiedDate ASC"
            )

            # 2. Cases with new Chatter POSTS (FeedItem)
            soql_feeditems = (
                f"{base_cases_soql} WHERE Id IN ("
                f"SELECT ParentId FROM FeedItem WHERE CreatedDate >= {soql_datetime}"
                f") AND IsDeleted = false"
            )

            # 3. Cases with new Chatter REPLIES (FeedComment)
            soql_feedcomments = (
                f"{base_cases_soql} WHERE Id IN ("
                f"SELECT ParentId FROM FeedComment WHERE CreatedDate >= {soql_datetime}"
                f") AND IsDeleted = false"
            )

            # 4. Fetch the latest timestamps for BOTH
            soql_item_dates = (
                f"SELECT ParentId, CreatedDate FROM FeedItem "
                f"WHERE CreatedDate >= {soql_datetime} ORDER BY CreatedDate DESC"
            )
            soql_comment_dates = (
                f"SELECT ParentId, CreatedDate FROM FeedComment "
                f"WHERE CreatedDate >= {soql_datetime} ORDER BY CreatedDate DESC"
            )

            # Execute the queries
            resp_modified = await self._soql_query_paginated(api_version=api_version, q=soql_modified)
            resp_feeditems = await self._soql_query_paginated(api_version=api_version, q=soql_feeditems)
            resp_feedcomments = await self._soql_query_paginated(api_version=api_version, q=soql_feedcomments)
            
            resp_item_dates = await self._soql_query_paginated(api_version=api_version, q=soql_item_dates)
            resp_comment_dates = await self._soql_query_paginated(api_version=api_version, q=soql_comment_dates)

            self.logger.info(
                "Incremental cases: %s modified, %s from FeedItem, %s from FeedComment",
                len(resp_modified.data.get("records", [])),
                len(resp_feeditems.data.get("records", [])),
                len(resp_feedcomments.data.get("records", [])),
            )

            # Build a map: case_id -> latest Feed epoch ms
            latest_feed_epoch: Dict[str, int] = {}
            
            def _process_feed_dates(feed_records):
                for f in feed_records:
                    parent_id = f.get("ParentId")
                    created_date = f.get("CreatedDate")
                    if not parent_id or not created_date:
                        continue
                    epoch = _parse_salesforce_timestamp(created_date)
                    if epoch is not None:
                        if parent_id not in latest_feed_epoch or epoch > latest_feed_epoch[parent_id]:
                            latest_feed_epoch[parent_id] = epoch

            _process_feed_dates((resp_item_dates.data or {}).get("records", []))
            _process_feed_dates((resp_comment_dates.data or {}).get("records", []))

            # Deduplicate across all three sources
            seen: Set[str] = set()
            all_case_records: List[Dict[str, Any]] = []
            
            combined_records = (
                resp_modified.data.get("records", [])
                + resp_feeditems.data.get("records", [])
                + resp_feedcomments.data.get("records", [])
            )

            for row in combined_records:
                case_id = row.get("Id")
                if case_id and case_id not in seen:
                    seen.add(case_id)
                    row["_latest_comment_epoch"] = latest_feed_epoch.get(case_id)
                    all_case_records.append(row)

            self.logger.info("Incremental cases after dedupe: %s unique", len(all_case_records))
            return all_case_records

        soql_full = f"{base_cases_soql} ORDER BY LastModifiedDate ASC"
        self.logger.info("Full cases sync: no previous sync point")
        response = await self._soql_query_paginated(api_version=api_version, q=soql_full)
        return response.data.get("records", [])

    async def _get_updated_task(
        self,
        api_version: str,
        tasks_last_ts_ms: Optional[int],
        base_tasks_soql: str,
    ) -> List[Dict[str, Any]]:
        """
        Fetch Task records for incremental sync.

        Returns tasks modified since the last sync point, or all tasks
        when no sync point is available.
        """
        if tasks_last_ts_ms:
            soql_datetime = _epoch_ms_to_soql_datetime(tasks_last_ts_ms)    
            soql = f"{base_tasks_soql} WHERE LastModifiedDate >= {soql_datetime} ORDER BY LastModifiedDate ASC"
            self.logger.info("Incremental tasks sync: fetching since %s", soql_datetime)
        else:
            soql = f"{base_tasks_soql} ORDER BY LastModifiedDate ASC"
            self.logger.info("Full tasks sync: no previous sync point")

        response = await self._soql_query_paginated(api_version=api_version, q=soql)
        return response.data.get("records", [])

    async def _get_updated_file(
        self,
        api_version: str,
        files_last_ts_ms: Optional[int],
    ) -> List[Dict[str, Any]]:
        """
        Fetch ContentVersion records for incremental sync.

        Returns the latest version of files modified since the last sync point,
        or all latest-version files when no sync point is available.
        """
        base_files_soql = (
            "SELECT Id, ContentDocumentId, Title, PathOnClient, ContentSize, "
            "FileExtension, FileType, LastModifiedDate, CreatedDate, Checksum "
            "FROM ContentVersion WHERE IsLatest = true"
        )

        if files_last_ts_ms:
            soql_datetime = _epoch_ms_to_soql_datetime(files_last_ts_ms)
            soql = f"{base_files_soql} AND LastModifiedDate >= {soql_datetime} ORDER BY LastModifiedDate ASC"
            self.logger.info("Incremental files sync: fetching since %s", soql_datetime)
        else:
            soql = f"{base_files_soql} ORDER BY LastModifiedDate ASC"
            self.logger.info("Full files sync: no previous sync point")

        response = await self._soql_query_paginated(api_version=api_version, q=soql)
        return (response.data or {}).get("records", [])

    async def _get_updated_account(
        self, api_version: str, soql_datetime: str, soql_accounts_query: str
    ) -> List[Dict[str, Any]]:
    
        # Accounts changed directly
        query_a = f"SELECT Id FROM Account WHERE SystemModstamp >= {soql_datetime}"
        resp_a = await self._soql_query_paginated(api_version=api_version, q=query_a)
        ids_from_accounts: Set[str] = set()
        if resp_a.success and resp_a.data:
            for rec in resp_a.data.get("records") or []:
                if rec.get("Id"):
                    ids_from_accounts.add(rec["Id"])

        # Query B: Accounts with changed Opportunities
        query_b = f"SELECT AccountId FROM Opportunity WHERE SystemModstamp >= {soql_datetime}"
        resp_b = await self._soql_query_paginated(api_version=api_version, q=query_b)
        if resp_b.success and resp_b.data:
            for rec in resp_b.data.get("records") or []:
                aid = rec.get("AccountId")
                if aid:
                    ids_from_accounts.add(aid)

        if not ids_from_accounts:
            self.logger.info("Incremental account sync: no changed account IDs")
            return []

        # Step 2: Combine and fetch full records (batch IN clause; Salesforce limit 500)
        main_select = soql_accounts_query
        id_list = list(ids_from_accounts)
        all_records: List[Dict[str, Any]] = []
        batch_size = 500
        for i in range(0, len(id_list), batch_size):
            batch_ids = id_list[i : i + batch_size]
            in_clause = "','".join(batch_ids)
            main_query = f"{main_select} WHERE Id IN ('{in_clause}')"
            response = await self._soql_query_paginated(api_version=api_version, q=main_query)
            if response.success and response.data:
                all_records.extend(response.data.get("records") or [])
        self.logger.info(
            "Incremental account sync: fetched %s accounts for %s changed IDs",
            len(all_records),
            len(ids_from_accounts),
        )
        return all_records

    async def _sync_accounts(self, account_records: List[Dict[str, Any]]) -> None:
        """
        Fetch all Salesforce accounts, create org nodes (accountType=enterprise, isExternal=true) for each,
        create one record group per account (SALESFORCE_ORG), create salesProspect edges (registered org -> account org),
        and salesCustomer edges only when there is an end time (first won opportunity).

        Parsing (from Opportunities subquery, sorted by CloseDate ASC):
        - sales_prospect End Time: first record where IsWon == true.
        - salesCustomer activeCustomer: true if any record has IsClosed == false.
        - salesCustomer edge: only when sales_prospect has end time.
        """

        try:
            self.logger.info(f"Fetched {len(account_records)} accounts from Salesforce")

            orgs_with_edges: List[Tuple[Org, RecordGroup, Dict, Dict]] = []
            record_groups_with_perms: List[Tuple[RecordGroup, List[Permission]]] = []
            org_permission = Permission(entity_type=EntityType.ORG, type=PermissionType.READ)

            for acc in account_records:
                try:
                    account_id = acc.get("Id")
                    if not account_id:
                        continue

                    acc_type = acc.get("Type")
                    rating = acc.get("Rating")
                    created_date = acc.get("CreatedDate")
                    account_name = acc.get("Name") or ""

                    start_time_ms = _parse_salesforce_timestamp(created_date)
                    end_time_ms, active_customer = self._parse_opportunities(acc)

                    org_node = Org(
                        name=account_name,
                        account_type="enterprise",
                        is_external=True,
                        is_active=True,
                        website=acc.get("Website"),
                        industry=acc.get("Industry"),
                        ownership_type=acc.get("Ownership"),
                        phone=acc.get("Phone"),
                        duns_id=acc.get("DunsNumber"),
                        source_created_at=_parse_salesforce_timestamp(created_date),
                        source_updated_at=_parse_salesforce_timestamp(acc.get("LastModifiedDate"))
                    )

                    record_group = RecordGroup(
                        name=account_name or account_id,
                        external_group_id=account_id,
                        group_type=RecordGroupType.SALESFORCE_ORG,
                        connector_name=self.connector_name,
                        connector_id=self.connector_id,
                        source_created_at=_parse_salesforce_timestamp(created_date),
                        source_updated_at=_parse_salesforce_timestamp(acc.get("LastModifiedDate")),
                    )
                    record_groups_with_perms.append((record_group, []))

                    updated_time_ms = _parse_salesforce_timestamp(acc.get("LastModifiedDate"))
                    prospect_edge = {
                        "to_id": org_node.id,
                        "rating": rating,
                        "type": acc_type,
                        "externalId": account_id,
                        "startTime": start_time_ms,
                        "endTime": end_time_ms,
                        "createdAtTimestamp": start_time_ms,
                        "updatedAtTimestamp": updated_time_ms,
                    }
                    customer_edge: Optional[Dict] = None
                    if end_time_ms is not None:
                        customer_edge = {
                            "to_id": org_node.id,
                            "rating": rating,
                            "type": acc_type,
                            "activeCustomer": active_customer,
                            "externalId": account_id,
                            "since": end_time_ms,
                            "createdAtTimestamp": start_time_ms,
                            "updatedAtTimestamp": updated_time_ms,
                        }
                    
                    orgs_with_edges.append((org_node, record_group, prospect_edge, customer_edge))

                except Exception as e:
                    self.logger.warning(f"Failed to process account {acc.get('Id')}: {e}")
                    continue

            if record_groups_with_perms:
                await self.data_entities_processor.on_new_record_groups(record_groups_with_perms)
                self.logger.info(f"Created/updated {len(record_groups_with_perms)} account record groups")

            if orgs_with_edges:
                org_id = self.data_entities_processor.org_id
                async with self.data_entities_processor.data_store_provider.transaction() as tx_store:
                    all_orgs = await tx_store.get_all_orgs()
                    external_org_key_by_name = {
                        o["name"]: o["_key"]
                        for o in all_orgs
                        if o.get("isExternal") is True
                    }

                    delete_tasks = []
                    for org, rg, _, _ in orgs_with_edges:
                        existing_key = external_org_key_by_name.get(org.name)
                        if existing_key is not None:
                            org.id = existing_key
                            delete_tasks.append(tx_store.delete_edges_to(
                                to_id=existing_key,
                                to_collection=CollectionNames.ORGS.value,
                                collection=CollectionNames.SALES_PROSPECT.value,
                            ))
                            delete_tasks.append(tx_store.delete_edges_to(
                                to_id=existing_key,
                                to_collection=CollectionNames.ORGS.value,
                                collection=CollectionNames.SALES_CUSTOMER.value,
                            ))
                            delete_tasks.append(tx_store.delete_edges_from(
                                rg.id,
                                CollectionNames.RECORD_GROUPS.value,
                                CollectionNames.DEAL_OF.value,
                            ))
                    if delete_tasks:
                        await asyncio.gather(*delete_tasks)

                    await tx_store.batch_upsert_orgs(
                        [org.to_arango_org() for org, _, _, _ in orgs_with_edges]
                    )

                    prospect_edges = []
                    customer_edges = []
                    dealof_edges = []
                    for org, rg, prospect_edge_attrs, customer_edge_attrs in orgs_with_edges:
                        prospect_edges.append({
                            **prospect_edge_attrs,
                            "to_id": org.id,
                            "to_collection": CollectionNames.ORGS.value,
                            "from_id": org_id,
                            "from_collection": CollectionNames.ORGS.value,
                        })
                        if customer_edge_attrs:
                            customer_edges.append({
                                **customer_edge_attrs,
                                "to_id": org.id,
                                "to_collection": CollectionNames.ORGS.value,
                                "from_id": org_id,
                                "from_collection": CollectionNames.ORGS.value,
                            })
                        dealof_edges.append({
                            "from_id": rg.id,
                            "from_collection": CollectionNames.RECORD_GROUPS.value,
                            "to_id": org.id,
                            "to_collection": CollectionNames.ORGS.value,
                            "createdAtTimestamp": prospect_edge_attrs.get("createdAtTimestamp"),
                            "updatedAtTimestamp": prospect_edge_attrs.get("updatedAtTimestamp"),
                        })

                    await tx_store.batch_create_edges(
                        prospect_edges,
                        collection=CollectionNames.SALES_PROSPECT.value,
                    )
                    if customer_edges:
                        await tx_store.batch_create_edges(
                            customer_edges,
                            collection=CollectionNames.SALES_CUSTOMER.value,
                        )
                    if dealof_edges:
                        await tx_store.batch_create_edges(
                            dealof_edges,
                            collection=CollectionNames.DEAL_OF.value,
                        )

                self.logger.info(
                    f"Synced {len(orgs_with_edges)} account orgs with salesProspect/salesCustomer/dealOf edges"
                )
            else:
                self.logger.info("No account orgs or edges to sync")

        except Exception as e:
            self.logger.error(f"Error syncing accounts and sales prospect/customer: {e}", exc_info=True)
            raise

    async def _sync_contacts(self, contact_records: List[Dict[str, Any]]) -> None:
        """
        Fetch all Salesforce Contacts, create Person nodes (firstName, lastName, email, phone),
        create salesContact edges (registered org -> person), and memberOf edges (person -> account org).
        """
        try:
            if not contact_records:
                self.logger.info("No contacts found in Salesforce")
                return

            contact_with_edges: List[Tuple[Dict, Dict, Dict]] = [] #[(people, sales_contact_edges, member_of_edges)]

            for contact in contact_records:
                try:
                    contact_id = contact.get("Id")
                    if not contact_id:
                        continue

                    # Person node fields (from Contact)
                    email = contact.get("Email")
                    if not email:
                        self.logger.warning(
                            "Skipping contact %s: email not available (required for Person node)",
                            contact_id,
                        )
                        continue

                    person = Person(
                        email=email,
                        first_name=contact.get("FirstName"),
                        last_name=contact.get("LastName"),
                        phone=contact.get("Phone"),
                        created_at=_parse_salesforce_timestamp(contact.get("CreatedDate")),
                        updated_at=_parse_salesforce_timestamp(contact.get("LastModifiedDate")),
                    )

                    # salesContact edge: since = when contact was made (CreatedDate)
                    since_ms = _parse_salesforce_timestamp(contact.get("CreatedDate"))
                    contact_updated_ms = _parse_salesforce_timestamp(contact.get("LastModifiedDate"))

                    sales_contact_edge = {
                        "to_id": person.id,
                        "description": contact.get("Description"),
                        "leadSource": contact.get("LeadSource"),
                        "since": since_ms,
                        "externalId": contact_id,
                        "createdAtTimestamp": since_ms,
                        "updatedAtTimestamp": contact_updated_ms,
                    }

                    member_of_edge: Optional[Dict] = None

                    # memberOf edge: person -> account org (Title, Department)
                    account_name = contact.get("Account").get("Name")
                    if account_name:
                        member_of_edge = {
                            "from_id": person.id,
                            "accountName": account_name,
                            "title": contact.get("Title"),
                            "department": contact.get("Department"),
                            "createdAtTimestamp": since_ms,
                            "updatedAtTimestamp": contact_updated_ms,
                        }
                            
                    contact_with_edges.append((person, sales_contact_edge, member_of_edge))

                except Exception as e:
                    self.logger.warning(f"Failed to process contact {contact.get('Id')}: {e}")
                    continue

            if contact_with_edges:
                org_id = self.data_entities_processor.org_id
                async with self.data_entities_processor.data_store_provider.transaction() as tx_store:
                    all_emails = [p.email for p, _, _ in contact_with_edges]
                    all_account_names = list({
                        moe.get("accountName")
                        for _, sce, moe in contact_with_edges
                        if sce and moe and moe.get("accountName")
                    })

                    people_coro = tx_store.get_nodes_by_field_in(
                        collection=CollectionNames.PEOPLE.value,
                        field="email",
                        values=all_emails,
                    )
                    orgs_coro = (
                        tx_store.get_nodes_by_field_in(
                            collection=CollectionNames.ORGS.value,
                            field="name",
                            values=all_account_names,
                        )
                        if all_account_names
                        else asyncio.gather()  # resolves to () immediately
                    )
                    existing_people_result, existing_orgs_result = await asyncio.gather(
                        people_coro, orgs_coro
                    )
                    existing_people = existing_people_result or []
                    existing_orgs = existing_orgs_result or []
                    email_map = {node.get("email"): node for node in existing_people}
                    org_name_map = {node.get("name"): node for node in (existing_orgs or [])}

                    delete_tasks = []
                    for person, _, _ in contact_with_edges:
                        node = email_map.get(person.email)
                        if node:
                            person.id = node.get("id") or node.get("_key")
                            delete_tasks.append(tx_store.delete_edges_to(
                                to_id=person.id,
                                to_collection=CollectionNames.PEOPLE.value,
                                collection=CollectionNames.SALES_CONTACT.value,
                            ))
                            delete_tasks.append(tx_store.delete_edges_from(
                                from_id=person.id,
                                from_collection=CollectionNames.PEOPLE.value,
                                collection=CollectionNames.MEMBER_OF.value,
                            ))
                    if delete_tasks:
                        await asyncio.gather(*delete_tasks)

                    await tx_store.batch_upsert_people([p for p, _, _ in contact_with_edges])

                    sales_contact_edges = []
                    member_of_edges = []
                    for person, sales_contact_edge, member_of_edge in contact_with_edges:
                        if sales_contact_edge:
                            sales_contact_edge["to_id"] = person.id
                            sales_contact_edges.append({
                                "from_id": org_id,
                                "from_collection": CollectionNames.ORGS.value,
                                "to_collection": CollectionNames.PEOPLE.value,
                                **sales_contact_edge,
                            })
                        if member_of_edge and sales_contact_edge:
                            account_name = member_of_edge.pop("accountName", None)
                            if account_name:
                                org_node = org_name_map.get(account_name)
                                if org_node:
                                    member_of_edges.append({
                                        "from_id": person.id,
                                        "from_collection": CollectionNames.PEOPLE.value,
                                        "to_id": org_node.get("id") or org_node.get("_key"),
                                        "to_collection": CollectionNames.ORGS.value,
                                        **{k: v for k, v in member_of_edge.items()},
                                    })

                    if sales_contact_edges:
                        await tx_store.batch_create_edges(
                            sales_contact_edges,
                            collection=CollectionNames.SALES_CONTACT.value,
                        )
                    if member_of_edges:
                        await tx_store.batch_create_edges(
                            member_of_edges,
                            collection=CollectionNames.MEMBER_OF.value,
                        )

                self.logger.info(
                    f"Synced {len(contact_with_edges)} contact person nodes and salesContact edges"
                )

        except Exception as e:
            self.logger.error(f"Error syncing contacts and sales contact edges: {e}", exc_info=True)
            raise

    async def _sync_leads(self, lead_records: List[Dict[str, Any]]) -> None:
        """
        Fetch all Salesforce Leads and create salesLead edges (registered org -> person).

        - Builds a map from salesContact edges: external id (Salesforce Contact Id) -> person node id.
        - If lead is converted: create sales_lead edge from org to the existing people node
          (using ConvertedContactId to find the person via the map).
        - If lead is not converted: create a new Person node and sales_lead edge from org to that person;
          skip leads with no email.

        salesLead edge fields: Company, Title, Status, Rating, Industry, LeadSource,
        AnnualRevenue, ExternalId, startTime (CreatedDate), endTime (ConvertedDate).
        """
        
        try:
            if not lead_records:
                self.logger.info("No leads found in Salesforce")
                return

            lead_with_edges: List[Tuple[Person, Dict]] = [] #[(person, sales_lead_edge)]

            for lead in lead_records:
                try:
                    lead_id = lead.get("Id")
                    if not lead_id:
                        continue

                    converted_contact_id = lead.get("ConvertedContactId")
                    is_converted = bool(converted_contact_id)

                    # salesLead edge: startTime = CreatedDate, endTime = ConvertedDate if converted, otherwise None
                    start_time_ms = _parse_salesforce_timestamp(lead.get("CreatedDate"))
                    end_time_ms = None
                    if is_converted:
                        end_time_ms = _parse_salesforce_timestamp(lead.get("ConvertedDate"))

                    annual_revenue = lead.get("AnnualRevenue")
                    if annual_revenue is not None:
                        try:
                            annual_revenue = float(annual_revenue)
                        except (TypeError, ValueError):
                            annual_revenue = None

                    edge_attrs = {
                        "company": lead.get("Company"),
                        "title": lead.get("Title"),
                        "status": lead.get("Status"),
                        "rating": lead.get("Rating"),
                        "industry": lead.get("Industry"),
                        "leadSource": lead.get("LeadSource"),
                        "annualRevenue": annual_revenue,
                        "externalId": lead_id,
                        "startTime": start_time_ms,
                        "endTime": end_time_ms,
                        "createdAtTimestamp": start_time_ms,
                        "updatedAtTimestamp": _parse_salesforce_timestamp(lead.get("LastModifiedDate")),
                    }

                    lead_email = lead.get("Email")
                    if not lead_email:
                        self.logger.warning(
                            f"Skipping lead {lead_id}: missing email required for Person node"
                        )
                        continue

                    person = Person(
                            email=lead_email,
                            first_name=lead.get("FirstName"),
                            last_name=lead.get("LastName"),
                            phone=lead.get("Phone"),
                            created_at=_parse_salesforce_timestamp(lead.get("CreatedDate")),
                            updated_at=_parse_salesforce_timestamp(lead.get("LastModifiedDate")),
                        )

                    lead_with_edges.append((person, edge_attrs))

                except Exception as e:
                    self.logger.warning(f"Failed to process lead {lead.get('Id')}: {e}")
                    continue

            if lead_with_edges:
                org_id = self.data_entities_processor.org_id
                async with self.data_entities_processor.data_store_provider.transaction() as tx_store:
                    all_emails = [p.email for p, _ in lead_with_edges]
                    existing_people = await tx_store.get_nodes_by_field_in(
                        collection=CollectionNames.PEOPLE.value,
                        field="email",
                        values=all_emails,
                    )
                    email_map = {node.get("email"): node for node in (existing_people or [])}

                    ids_to_delete = []
                    for person, _ in lead_with_edges:
                        node = email_map.get(person.email)
                        if node:
                            person.id = node.get("id") or node.get("_key")
                            ids_to_delete.append(person.id)

                    if ids_to_delete:
                        await asyncio.gather(*[
                            tx_store.delete_edges_to(
                                to_id=pid,
                                to_collection=CollectionNames.PEOPLE.value,
                                collection=CollectionNames.SALES_LEAD.value,
                            )
                            for pid in ids_to_delete
                        ])

                    await tx_store.batch_upsert_people([p for p, _ in lead_with_edges])

                    edges = [
                        {
                            "from_id": org_id,
                            "from_collection": CollectionNames.ORGS.value,
                            "to_id": p.id,
                            "to_collection": CollectionNames.PEOPLE.value,
                            **e,
                        }
                        for p, e in lead_with_edges
                    ]
                    await tx_store.batch_create_edges(edges, collection=CollectionNames.SALES_LEAD.value)

                self.logger.info(
                    f"Synced {len(lead_with_edges)} lead person nodes and salesLead edges"
                )

        except Exception as e:
            self.logger.error(f"Error syncing leads and sales lead edges: {e}", exc_info=True)
            raise

    async def _sync_products(self, product_records: List[Dict[str, Any]]) -> None:
        """
        Sync Salesforce Product2 as a record group and product records.
        """
        try:
            if not self.data_source:
                self.logger.error("Salesforce data source not initialized")
                return

            # 1. Ensure product record group exists (org -> record group BELONGS_TO + org -> record group PERMISSION)
            product_record_group = RecordGroup(
                name="Products",
                external_group_id= self.data_entities_processor.org_id + "-product",
                group_type=RecordGroupType.PRODUCT,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
            )
            # Platform permission: org can read the product record group (enables org-level access to product records)
            org_permission = Permission(
                entity_type=EntityType.ORG,
                type=PermissionType.READ,
            )
            await self.data_entities_processor.on_new_record_groups([(product_record_group, [org_permission])])
            self.logger.info("Product record group ensured.")

            if not product_records:
                self.logger.info("No products found in Salesforce")
                return

            # 3. Build ProductRecord list with external_record_id = Salesforce product Id
            records: List[Record] = []
            for product in product_records:
                try:
                    if not product.get("Id"):
                        continue
                    record = self._build_product_record(product)
                    if self.indexing_filters and self.indexing_filters.is_enabled(IndexingFilterKey.ENABLE_MANUAL_SYNC):
                        record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
                    records.append(record)
                except Exception as e:
                    self.logger.warning(f"Failed to process product {product.get('Id')}: {e}")
                    continue

            if not records:
                self.logger.warning("No product records to sync")
                return

            # 4. Sync records via on_new_records (creates record, record->record group BELONGS_TO, record->product IS_OF_TYPE)
            for record in records:
                existing_record = await self.data_entities_processor.get_record_by_external_id(self.connector_id, record.external_record_id)
                if existing_record is not None:
                    await self.data_entities_processor.on_record_content_update(record)
                else:
                    await self.data_entities_processor.on_new_records([(record, [])])

            self.logger.info(
                f"Synced {len(records)} product records."
            )

        except Exception as e:
            self.logger.error(f"Error syncing products: {e}", exc_info=True)
            raise

    async def _sync_opportunities(self, opportunity_records: List[Dict[str, Any]]) -> None:
        """
        Fetch all Salesforce Opportunities, create deal records (skip if same external id exists),
        create salesdeal edge (internal org -> deal record, stage),
        and permission edge (internal org -> record group).
        """
        
        try:
            if not opportunity_records:
                self.logger.info("No opportunities found in Salesforce")
                return

            opportunity_with_edges: List[Tuple[Record, Dict]] = [] #[(record, salesdeal_edge)]
            records: List[Record] = []

            org_permission = Permission(
                entity_type=EntityType.ORG,
                type=PermissionType.READ,
            )
            record_group = RecordGroup(
                name="Unassigned Deals",
                external_group_id="UNASSIGNED-DEAL",
                group_type=RecordGroupType.DEALS,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
            )
            await self.data_entities_processor.on_new_record_groups([(record_group, [org_permission])])
            self.logger.info(f"Ensured unassigned deal record group.")

            for opp in opportunity_records:
                if not opp.get("Id"):
                    continue

                record = self._build_deal_record(opp)
                if self.indexing_filters and self.indexing_filters.is_enabled(IndexingFilterKey.ENABLE_MANUAL_SYNC):
                    record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
                records.append(record)
                salesdeal_edge = {
                    "stage": opp.get("StageName") or "",
                    "createdAtTimestamp": _parse_salesforce_timestamp(opp.get("CreatedDate")),
                    "updatedAtTimestamp": _parse_salesforce_timestamp(opp.get("LastModifiedDate")),
                }
                opportunity_with_edges.append((record, salesdeal_edge))

            for record in records:
                existing_record = await self.data_entities_processor.get_record_by_external_id(self.connector_id, record.external_record_id)
                if existing_record is not None:
                    await self.data_entities_processor.on_record_content_update(record)
                else:
                    await self.data_entities_processor.on_new_records([(record, [])])
            self.logger.info(f"Synced {len(records)} deal records")

            if opportunity_with_edges:
                org_id = self.data_entities_processor.org_id
                async with self.data_entities_processor.data_store_provider.transaction() as tx_store:
                    # Fetch all existing records concurrently
                    existing_records_list = await asyncio.gather(*[
                        tx_store.get_record_by_external_id(
                            connector_id=record.connector_id,
                            external_id=record.external_record_id,
                        )
                        for record, _ in opportunity_with_edges
                    ])
                    ext_id_to_existing = {
                        existing.external_record_id: existing
                        for existing in existing_records_list
                        if existing is not None
                    }

                    # Update record IDs and delete stale SALES_DEAL edges concurrently
                    delete_tasks = []
                    for record, _ in opportunity_with_edges:
                        existing = ext_id_to_existing.get(record.external_record_id)
                        if existing is not None:
                            record.id = existing.id
                            delete_tasks.append(tx_store.delete_edges_to(
                                to_id=record.id,
                                to_collection=CollectionNames.RECORDS.value,
                                collection=CollectionNames.SALES_DEAL.value,
                            ))
                    if delete_tasks:
                        await asyncio.gather(*delete_tasks)

                    salesdeal_edges = []
                    for record, salesdeal_edge in opportunity_with_edges:
                        existing = ext_id_to_existing.get(record.external_record_id)
                        if existing is None:
                            continue
                        if salesdeal_edge:
                            salesdeal_edges.append({
                                "from_id": org_id,
                                "from_collection": CollectionNames.ORGS.value,
                                "to_id": record.id,
                                "to_collection": CollectionNames.RECORDS.value,
                                **salesdeal_edge,
                            })

                    if salesdeal_edges:
                        await tx_store.batch_create_edges(
                            salesdeal_edges,
                            collection=CollectionNames.SALES_DEAL.value,
                        )

        except Exception as e:
            self.logger.error(f"Error syncing opportunities: {e}", exc_info=True)
            raise

    async def _sync_sold_in_edges(self, line_item_records: List[Dict[str, Any]], api_version: str) -> None:
        """
        Sync soldIn edges (Product -> Deal) from OpportunityLineItem records.
        For each unique (OpportunityId, Product2Id) pair found in line_item_records,
        queries Salesforce (including deleted records) to get all matching line items
        and builds one edge per pair with lineItems [{id, quantity, unitPrice, totalPrice, isDeleted}, ...].
        """
        try:
            if not line_item_records:
                self.logger.info("No OpportunityLineItem records to sync for soldIn edges")
                return

            # Collect unique (OpportunityId, Product2Id) pairs from the incoming batch
            unique_pairs: Set[Tuple[str, str]] = set()
            for sold_in in line_item_records:
                product2 = sold_in.get("Product2") or {}
                product2_id = product2.get("Id")
                opp_id = sold_in.get("OpportunityId")
                if product2_id and opp_id:
                    unique_pairs.add((opp_id, product2_id))

            if not unique_pairs:
                self.logger.info("No valid (OpportunityId, Product2Id) pairs found in line item records")
                return

            # {deal_external_id: (deal_record, {product_internal_id: [raw line rows]})}
            deal_groups: Dict[str, Tuple[Record, DefaultDict[str, List[Dict[str, Any]]]]] = {}

            for opp_id, product2_id in unique_pairs:
                soql = (
                    f"SELECT Id, Quantity, UnitPrice, TotalPrice, IsDeleted, CreatedDate, LastModifiedDate "
                    f"FROM OpportunityLineItem "
                    f"WHERE OpportunityId = '{opp_id}' AND Product2Id = '{product2_id}'"
                )
                response = await self._soql_query_paginated(api_version=api_version, q=soql, queryAll=True)
                fetched_items = (response.data or {}).get("records", [])

                if not fetched_items:
                    continue

                product_record = await self.data_entities_processor.get_record_by_external_id(self.connector_id, product2_id)
                if product_record is None:
                    continue

                if opp_id not in deal_groups:
                    deal_record = await self.data_entities_processor.get_record_by_external_id(self.connector_id, opp_id)
                    if deal_record is None:
                        continue
                    deal_groups[opp_id] = (deal_record, defaultdict(list))

                _, by_product = deal_groups[opp_id]

                for item in fetched_items:
                    by_product[product_record.id].append(
                        {
                            "quantity": float(item["Quantity"]) if item.get("Quantity") is not None else None,
                            "unitPrice": float(item["UnitPrice"]) if item.get("UnitPrice") is not None else None,
                            "totalPrice": float(item["TotalPrice"]) if item.get("TotalPrice") is not None else None,
                            "isDeleted": item.get("IsDeleted", False),
                            "sourceCreatedAtTimestamp": _parse_salesforce_timestamp(item.get("CreatedDate")),
                            "sourceUpdatedAtTimestamp": _parse_salesforce_timestamp(item.get("LastModifiedDate")),
                        }
                    )

            final_sold_in_items: List[Tuple[Record, List[Dict[str, Any]]]] = []
            for deal_record, by_product in deal_groups.values():
                if not by_product:
                    continue
                edges_list: List[Dict[str, Any]] = []
                for product_id, raw_lines in by_product.items():
                    created_timestamps = [
                        row["sourceCreatedAtTimestamp"]
                        for row in raw_lines
                        if row.get("sourceCreatedAtTimestamp") is not None
                    ]
                    updated_timestamps = [
                        row["sourceUpdatedAtTimestamp"]
                        for row in raw_lines
                        if row.get("sourceUpdatedAtTimestamp") is not None
                    ]
                    min_created_ts = min(created_timestamps) if created_timestamps else None
                    max_updated_ts = max(updated_timestamps) if updated_timestamps else None
                    edge_data: Dict[str, Any] = {
                        "from_id": product_id,
                        "from_collection": CollectionNames.RECORDS.value,
                        "to_id": deal_record.id,
                        "to_collection": CollectionNames.RECORDS.value,
                        "quantities": [row["quantity"] for row in raw_lines],
                        "unitPrices": [row["unitPrice"] for row in raw_lines],
                        "totalPrices": [row["totalPrice"] for row in raw_lines],
                        "isDeletedFlags": [bool(row["isDeleted"]) for row in raw_lines],
                    }
                    if min_created_ts is not None:
                        edge_data["createdAtTimestamp"] = min_created_ts
                    if max_updated_ts is not None:
                        edge_data["updatedAtTimestamp"] = max_updated_ts
                        edge_data["sourceUpdatedAtTimestamp"] = max_updated_ts
                    edges_list.append(edge_data)
                final_sold_in_items.append((deal_record, edges_list))

            if final_sold_in_items:
                async with self.data_entities_processor.data_store_provider.transaction() as tx_store:
                    delete_tasks = [
                        tx_store.delete_edge(
                            from_id=edge.get("from_id"),
                            from_collection=edge.get("from_collection"),
                            to_id=deal_record.id,
                            to_collection=CollectionNames.RECORDS.value,
                            collection=CollectionNames.SOLD_IN.value,
                        )
                        for deal_record, edges_list in final_sold_in_items
                        for edge in edges_list
                    ]
                    if delete_tasks:
                        await asyncio.gather(*delete_tasks)

                    all_sold_in_edges = [
                        edge
                        for _, edges_list in final_sold_in_items
                        for edge in edges_list
                    ]
                    if all_sold_in_edges:
                        await tx_store.batch_create_edges(
                            all_sold_in_edges,
                            collection=CollectionNames.SOLD_IN.value,
                        )

            self.logger.info(
                "Synced soldIn edges for %s unique deals (total %s edges)",
                len(final_sold_in_items),
                sum(len(edges) for _, edges in final_sold_in_items),
            )
        except Exception as e:
            self.logger.error(f"Error syncing soldIn edges: {e}", exc_info=True)
            raise

    async def _sync_cases(self, case_records: List[Dict[str, Any]]) -> None:
        """
        Sync Salesforce Case as ticket records (CASE -> TICKETS collection).
        Creates one record group per assigned user with external_group_id = assignee email.
        Uses on_new_records so _process_record forms edges (ASSIGNED_TO, CREATED_BY, REPORTED_BY).
        Mapping: external id = Id; Status->status; Priority->priority; Type->type;
        Owner.Name->assignee; Owner.Email->assigneeEmail; Contact.Name->reporterName;
        CreatedBy.Name->creatorName; CreatedBy.Email->creatorEmail.
        """

        try:
            if not case_records:
                self.logger.error("No cases found in Salesforce")
                return

            #make a record group for cases with no account id
            org_permission = Permission(
                entity_type=EntityType.ORG,
                type=PermissionType.READ,
            )
            record_group = RecordGroup(
                name="Cases - Unassigned",
                external_group_id="UNASSIGNED-CASE",
                group_type=RecordGroupType.CASE,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
            )
            await self.data_entities_processor.on_new_record_groups([(record_group, [org_permission])])
            self.logger.info(f"Ensured case record group for cases with no account id.")
            records: List[Record] = []
            for case_row in case_records:
                try:
                    if not case_row.get("Id"):
                        continue
                    record = self._build_case_record(case_row)
                    if self.indexing_filters and self.indexing_filters.is_enabled(IndexingFilterKey.ENABLE_MANUAL_SYNC):
                        record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
                    records.append(record)
                except Exception as e:
                    self.logger.warning(f"Failed to process case {case_row.get('Id')}: {e}")
                    continue

            if not records:
                self.logger.warning("No case records to sync")
                return

            # 4. Sync via on_new_records (creates record, isOfType->tickets, ASSIGNED_TO/CREATED_BY/REPORTED_BY edges via _process_record)
            for record in records:
                existing_record = await self.data_entities_processor.get_record_by_external_id(self.connector_id, record.external_record_id)
                if existing_record is not None:
                    await self.data_entities_processor.on_record_content_update(record)
                else:
                    await self.data_entities_processor.on_new_records([(record, [])])

            self.logger.info(f"Synced {len(records)} case records.")

        except Exception as e:
            self.logger.error(f"Error syncing cases: {e}", exc_info=True)
            raise

    async def _sync_tasks(self, task_records: List[Dict[str, Any]]) -> None:
        """
        Sync Salesforce Task as ticket records (TASK -> TICKETS collection).
        Creates one record group per assigned user with external_group_id = assignee email.
        Uses on_new_records so _process_record forms edges (ASSIGNED_TO, CREATED_BY).
        Mapping: external id = Id; status = Status; priority = Priority; type = TaskSubtype;
        deliveryStatus = null; assignee = Owner.Name; assigneeEmail = Owner.Email;
        creatorName = CreatedBy.Name; creatorEmail = CreatedBy.Email; dueDateTimestamp = ActivityDate.
        """

        try:
            if not task_records:
                self.logger.error("No tasks found in Salesforce")
                return

            org_permission = Permission(
                entity_type=EntityType.ORG,
                type=PermissionType.READ,
            )
            record_group = RecordGroup(
                name="Unassigned Tasks",
                external_group_id="UNASSIGNED-TASK",
                group_type=RecordGroupType.TASK,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
            )
            await self.data_entities_processor.on_new_record_groups([(record_group, [org_permission])])
            self.logger.info(f"Ensured unassigned task record group.")

            records: List[Record] = []
            for task_row in task_records:
                try:
                    if not task_row.get("Id"):
                        self.logger.debug("Skipping task row with missing Id")
                        continue
                    record = self._build_task_record(task_row)
                    if self.indexing_filters and self.indexing_filters.is_enabled(IndexingFilterKey.ENABLE_MANUAL_SYNC):
                        record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
                    records.append(record)
                except Exception as e:
                    self.logger.warning(f"Failed to process task {task_row.get('Id')}: {e}")
                    continue

            if not records:
                self.logger.warning("No task records to sync")
                return

            for record in records:
                existing_record = await self.data_entities_processor.get_record_by_external_id(self.connector_id, record.external_record_id)
                if existing_record is not None:
                    record.id = existing_record.id
                    if existing_record.external_record_group_id != record.external_record_group_id:
                        # Delete the BELONGS_TO edges to the existing record group
                        async with self.data_store_provider.transaction() as tx_store:
                            await tx_store.delete_edges_from(existing_record.id, CollectionNames.RECORDS.value, CollectionNames.BELONGS_TO.value)
                    await self.data_entities_processor.on_record_content_update(record)
                else:
                    await self.data_entities_processor.on_new_records([(record, [])])

            self.logger.info(f"Synced {len(records)} task records.")

        except Exception as e:
            self.logger.error(f"Error syncing tasks: {e}", exc_info=True)
            raise

    async def _handle_record_updates(self, record_update: RecordUpdate) -> None:
        """
        Handle different types of record updates (content changed, metadata changed).
        """
        try:
            if record_update.is_deleted and record_update.external_record_id:
                await self.data_entities_processor.on_record_deleted(record_id=record_update.external_record_id)
            elif record_update.is_updated and record_update.record:
                if record_update.content_changed:
                    self.logger.info(f"Content changed for record: {record_update.record.record_name}")
                    await self.data_entities_processor.on_record_content_update(record_update.record)
                if record_update.metadata_changed:
                    self.logger.info(f"Metadata changed for record: {record_update.record.record_name}")
                    await self.data_entities_processor.on_record_metadata_update(record_update.record)
        except Exception as e:
            self.logger.error(f"Error handling record updates: {e}", exc_info=True)

    async def _sync_files(self, api_version: str, file_records: List[Dict[str, Any]]) -> None:
        """
        Optimized sync of Salesforce Files.
        Reduces memory overhead by using generators and optimized lookups.
        """
        if not file_records:
            self.logger.info("No files found in Salesforce to sync.")
            return

        LINK_SOQL_TEMPLATE = (
            "SELECT ContentDocumentId, LinkedEntityId, LinkedEntity.Type "
            "FROM ContentDocumentLink WHERE ContentDocumentId IN ({})"
        )
        IN_CLAUSE_BATCH_SIZE = 500
        PARENT_TYPES = {"Opportunity", "Task", "Case", "Account"}

        try:
            # 1. Ensure Record Group exists
            files_record_group = RecordGroup(
                name="Salesforce Files",
                external_group_id=f"{self.data_entities_processor.org_id}-files",
                group_type=RecordGroupType.SALESFORCE_FILE.value,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                is_internal=True,
            )
            await self.data_entities_processor.on_new_record_groups([
                (files_record_group, [Permission(entity_type=EntityType.ORG, type=PermissionType.READ)])
            ])

            # 2. Map Metadata and extract IDs efficiently
            files_by_doc_id = {
                row["ContentDocumentId"]: row
                for row in file_records if row.get("ContentDocumentId")
            }
            unique_doc_ids = list(files_by_doc_id.keys())

            # 3. Batch Query Links — collect ALL links regardless of entity type
            links_map = defaultdict(list)
            docs_with_any_links = set()

            for i in range(0, len(unique_doc_ids), IN_CLAUSE_BATCH_SIZE):
                batch = unique_doc_ids[i : i + IN_CLAUSE_BATCH_SIZE]
                in_list = ", ".join(f"'{d_id}'" for d_id in batch)

                resp = await self._soql_query_paginated(api_version=api_version, q=LINK_SOQL_TEMPLATE.format(in_list))

                if resp.success and resp.data:
                    for row in resp.data.get("records", []):
                        doc_id = row.get("ContentDocumentId")
                        if not doc_id:
                            continue
                        links_map[doc_id].append(row)
                        docs_with_any_links.add(doc_id)

            # 5. Build all file records
            all_records: List[Tuple[FileRecord, str]] = []  # (rec, ext_id)

            # Process all linked files — ext_id is always {doc_id}-{l_id}
            # parent_id is set only for Opportunity, Task, Case
            for doc_id in docs_with_any_links:
                for link in links_map[doc_id]:
                    linked_entity_id = link.get("LinkedEntityId")
                    linked_entity_type = (link.get("LinkedEntity") or {}).get("Type")
                    self.logger.info(f"linked_entity_type: {linked_entity_type} for file name: {doc_id}")
                    if linked_entity_type not in PARENT_TYPES:
                        continue

                    if linked_entity_type == "Account":
                        parent_id = None
                        external_record_group_id = linked_entity_id
                    else:
                        parent_id = linked_entity_id
                        external_record_group_id = f"{self.data_entities_processor.org_id}-files"

                    external_record_id = f"{doc_id}-{linked_entity_id}"
                    meta = files_by_doc_id.get(doc_id)
                    rec = self._build_file_record(meta, external_record_id, parent_id, external_record_group_id)
                    if rec:
                        all_records.append((rec, external_record_id))

            # Process truly unlinked files (no links at all)
            for doc_id in (set(files_by_doc_id.keys()) - docs_with_any_links):
                meta = files_by_doc_id.get(doc_id)
                rec = self._build_file_record(
                    meta,
                    doc_id,
                    external_record_group_id=f"{self.data_entities_processor.org_id}-files",
                )
                if self.indexing_filters and self.indexing_filters.is_enabled(IndexingFilterKey.ENABLE_MANUAL_SYNC):
                    rec.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
                if rec:
                    all_records.append((rec, doc_id))

            # 6. For each record: check if exists; if new -> on_new_records, if exists -> compare and update
            new_records_batch: List[Tuple[FileRecord, List[Permission]]] = []

            for rec, ext_id in all_records:
                if not rec:
                    continue
                existing = await self.data_entities_processor.get_record_by_external_id(
                    self.connector_id,
                    ext_id,
                )
                if existing is None:
                    new_records_batch.append((rec, []))
                    continue

                # Record exists: detect content and metadata changes
                content_changed = (getattr(existing, "md5_hash", None) != rec.md5_hash)
                metadata_changed = (
                    getattr(existing, "record_name", None) != rec.record_name
                    or getattr(existing, "external_revision_id", None) != rec.external_revision_id
                    or getattr(existing, "source_updated_at", None) != rec.source_updated_at
                    or getattr(existing, "size_in_bytes", None) != rec.size_in_bytes
                    or getattr(existing, "extension", None) != rec.extension
                    or getattr(existing, "mime_type", None) != rec.mime_type
                    or getattr(existing, "weburl", None) != rec.weburl
                )
                if content_changed or metadata_changed:
                    rec.id = existing.id
                    record_update = RecordUpdate(
                        record=rec,
                        is_new=False,
                        is_updated=True,
                        is_deleted=False,
                        metadata_changed=metadata_changed,
                        content_changed=content_changed,
                        permissions_changed=False,
                        external_record_id=ext_id,
                    )
                    await self._handle_record_updates(record_update)

            # 7. Persist new records in chunks of 100
            for i in range(0, len(new_records_batch), 100):
                await self.data_entities_processor.on_new_records(new_records_batch[i : i + 100])

        except Exception as e:
            self.logger.error(f"Failed to sync Salesforce files: {str(e)}", exc_info=True)
            raise
    
    async def _sync_permissions_edges(self, api_version: str) -> None:
        """
        Sync permissions edges for Salesforce Deals.
        Streams access results per user and writes edges immediately — no intermediate map.
        """
        # 1. Fetch all active users
        soql_query = "SELECT Id, Email FROM User WHERE IsActive = true"
        response = await self._soql_query_paginated(api_version=api_version, q=soql_query)
        users = response.data.get("records", [])

        # 2. Fetch all Deal/Task nodes from ArangoDB
        async with self.data_store_provider.transaction() as tx_store:
            salesforce_records = await tx_store.get_nodes_by_field_in(
                collection=CollectionNames.RECORDS.value,
                field="recordType",
                values=[RecordType.DEAL.value, RecordType.TASK.value , RecordType.CASE.value , RecordType.PRODUCT.value]
            )
            salesforce_records = [r for r in salesforce_records if r.get("connectorId") == self.connector_id]

            salesforce_record_groups = await tx_store.get_nodes_by_field_in(
                collection=CollectionNames.RECORD_GROUPS.value,
                field="groupType",
                values=[RecordGroupType.SALESFORCE_ORG.value]
            )
            salesforce_record_groups = [r for r in salesforce_record_groups if r.get("connectorId") == self.connector_id]

        salesforce_external_ids = [
            r.get("externalRecordId") for r in salesforce_records if r.get("externalRecordId")
        ]

        salesforce_record_group_external_ids = [
            r.get("externalGroupId") for r in salesforce_record_groups if r.get("externalGroupId")
        ]

        if not salesforce_external_ids:
            self.logger.info("No Salesforce records found to sync permissions for.")
            return

        self.logger.info(f"Syncing permissions for {len(users)} users across {len(salesforce_external_ids)} records.")

        # 3. Process users concurrently with a semaphore to avoid overwhelming the API
        MAX_CONCURRENT_USERS = 5
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_USERS)

        tasks = [
            self._sync_permissions_for_user(
                user=user,
                salesforce_external_ids=salesforce_external_ids,
                salesforce_record_group_external_ids=salesforce_record_group_external_ids,
                api_version=api_version,
                semaphore=semaphore,
            )
            for user in users
        ]

        total = len(tasks)
        for index, coro in enumerate(asyncio.as_completed(tasks)):
            await coro
            if (index + 1) % 10 == 0:
                self.logger.info(f"Completed permissions for {index + 1}/{total} users")

    async def _sync_permissions_for_user(
        self,
        user: Dict[str, str],
        salesforce_external_ids: List[str],
        salesforce_record_group_external_ids: List[str],
        api_version: str,
        semaphore: asyncio.Semaphore,
    ) -> None:
        """Process all permission edges for a single user, streaming and writing immediately."""
        user_id = user.get("Id")
        user_email = user.get("Email")

        if not user_id or not user_email:
            return

        async with semaphore:
            async for rec_id, email, access_level in self._iter_record_access(
                user_id=user_id,
                user_email=user_email,
                record_ids=salesforce_external_ids,
                api_version=api_version,
            ):
                try:
                    await self.salesforce_permissions_sync(
                        connector_id=self.connector_id,
                        record_external_id=rec_id,
                        users_email=email,
                        access_level=PermissionType(access_level),
                    )
                except Exception as e:
                    self.logger.error(
                        f"Failed to sync permission for user {email} on record {rec_id}: {e}",
                        exc_info=True,
                    )
            
            async for record_group_id, email, access_level in self._iter_record_access(
                user_id=user_id,
                user_email=user_email,
                record_ids=salesforce_record_group_external_ids,
                api_version=api_version,
            ):
                try:
                    await self.salesforce_record_group_permissions_sync(
                        connector_id=self.connector_id,
                        record_group_external_id=record_group_id,
                        users_email=email,
                        access_level=PermissionType(access_level),
                    )
                except Exception as e:
                    self.logger.error(
                        f"Failed to sync record group permission for user {email} on record group {rec_id}: {e}",
                        exc_info=True,
                    )

    async def salesforce_record_group_permissions_sync(
        self,
        connector_id: str,
        record_group_external_id: str,
        users_email: str,
        access_level: PermissionType,
    ) -> None:
        """
        Ensure a user has at least the given permission level on a Salesforce org record group.
        """
        try:
            async with self.data_store_provider.transaction() as tx_store:
                record_group = await tx_store.get_record_group_by_external_id(
                    connector_id=connector_id,
                    external_id=record_group_external_id,
                )
                if not record_group:
                    self.logger.warning(
                        f"Record group with external id {record_group_external_id} not found in database"
                    )
                    return

                if record_group.group_type != RecordGroupType.SALESFORCE_ORG:
                    return

                user = await tx_store.get_user_by_email(users_email)
                if not user:
                    self.logger.warning(f"User with email {users_email} not found in database")
                    return

                existing_edge = await tx_store.get_edge(
                    from_id=user.id,
                    from_collection=CollectionNames.USERS.value,
                    to_id=record_group.id,
                    to_collection=CollectionNames.RECORD_GROUPS.value,
                    collection=CollectionNames.PERMISSION.value,
                )

                required_level = PERMISSION_HIERARCHY.get(access_level.value, 0)
                if existing_edge:
                    existing_role = existing_edge.get("role", "READER")
                    existing_level = PERMISSION_HIERARCHY.get(existing_role, 0)
                    if existing_level == required_level:
                        return
                    await tx_store.delete_edge(
                        from_id=user.id,
                        from_collection=CollectionNames.USERS.value,
                        to_id=record_group.id,
                        to_collection=CollectionNames.RECORD_GROUPS.value,
                        collection=CollectionNames.PERMISSION.value,
                    )

                permission = Permission(
                    email=users_email,
                    type=access_level,
                    entity_type=EntityType.USER,
                )
                edge_data = permission.to_arango_permission(
                    from_id=user.id,
                    from_collection=CollectionNames.USERS.value,
                    to_id=record_group.id,
                    to_collection=CollectionNames.RECORD_GROUPS.value,
                )
                await tx_store.batch_create_edges([edge_data], collection=CollectionNames.PERMISSION.value)
        except Exception as e:
            self.logger.error(f"Failed to sync record group permissions: {str(e)}", exc_info=True)
            raise

    async def salesforce_permissions_sync(
        self,
        connector_id: str,
        record_external_id: str,
        users_email: str,
        access_level: PermissionType,
    ) -> None:
        """
        Ensure a user has at least the given permission level on a record.
        If they already have that level or higher, return. Otherwise create or upgrade the permission edge.
        """
        try:
            async with self.data_store_provider.transaction() as tx_store:
                record = await tx_store.get_record_by_external_id(
                    connector_id=connector_id,
                    external_id=record_external_id,
                )
                if not record:
                    self.logger.warning(f"Record with external id {record_external_id} not found in database")
                    return

                user = await tx_store.get_user_by_email(users_email)
                if not user:
                    self.logger.warning(f"User with email {users_email} not found in database")
                    return

                existing_edge = await tx_store.get_edge(
                    from_id=user.id,
                    from_collection=CollectionNames.USERS.value,
                    to_id=record.id,
                    to_collection=CollectionNames.RECORDS.value,
                    collection=CollectionNames.PERMISSION.value,
                )

                required_level = PERMISSION_HIERARCHY.get(access_level.value, 0)
                if existing_edge:
                    existing_role = existing_edge.get("role", "READER")
                    existing_level = PERMISSION_HIERARCHY.get(existing_role, 0)
                    if existing_level == required_level:
                        self.logger.debug(
                            f"User {users_email} already has sufficient permission on record {record_external_id} "
                            f"(existing: {existing_role}, required: {access_level.value})"
                        )
                        return
                    await tx_store.delete_edge(
                        from_id=user.id,
                        from_collection=CollectionNames.USERS.value,
                        to_id=record.id,
                        to_collection=CollectionNames.RECORDS.value,
                        collection=CollectionNames.PERMISSION.value,
                    )

                permission = Permission(
                    email=users_email,
                    type=access_level,
                    entity_type=EntityType.USER,
                )
                edge_data = permission.to_arango_permission(
                    from_id=user.id,
                    from_collection=CollectionNames.USERS.value,
                    to_id=record.id,
                    to_collection=CollectionNames.RECORDS.value,
                )
                await tx_store.batch_create_edges([edge_data], collection=CollectionNames.PERMISSION.value)
                self.logger.info(
                    f"Created/updated permission for {users_email} on record {record_external_id} to {access_level.value}"
                )
        except Exception as e:
            self.logger.error(f"Failed to sync permissions: {str(e)}", exc_info=True)
            raise

    def _flatten_single_group_members(
        self,
        group_id: str,
        group_to_members: Dict[str, List[str]],
        visited: set,
        current_path: set,
    ) -> set:
        """
        Recursively flatten a single group's members to find all actual User IDs.
        Returns:
            Set of User IDs that belong to this group (directly or indirectly)
        """
        if group_id in current_path:
            self.logger.warning(f"Circular reference detected for group {group_id}")
            return set()
        if group_id in visited:
            return set()

        visited.add(group_id)
        current_path.add(group_id)

        user_ids = set()
        immediate_members = group_to_members.get(group_id, [])

        for member_id in immediate_members:
            if member_id in group_to_members:
                nested_user_ids = self._flatten_single_group_members(
                    member_id, group_to_members, visited, current_path.copy()
                )
                user_ids.update(nested_user_ids)
            else:
                user_ids.add(member_id)

        current_path.remove(group_id)
        return user_ids

    async def _flatten_group_members(self, api_version: str) -> Dict[str, Set[tuple]]:
        """
        Fetch all groups and their members, then flatten nested groups to actual User IDs with emails.
        Returns:
            Map of group ID -> set of dicts with {'user_id': str, 'email': str}
        """
        try:
            # Fetch all groups and their immediate members
            soql_query = """
                SELECT Id, Name, 
                    (SELECT Id, UserOrGroupId, UserOrGroup.Email 
                    FROM GroupMembers) 
                FROM Group 
                WHERE Type IN ('Regular', 'Queue')
            """
            response = await self._soql_query_paginated(api_version=api_version, q=soql_query)
            
            if not response.success or not response.data:
                self.logger.warning("No groups found or query failed")
                return {}
            
            all_groups = response.data.get("records", [])
            
            # Build map of Group ID -> List of immediate member IDs (UserOrGroupId)
            group_to_members: Dict[str, List[str]] = {}
            user_id_to_email: Dict[str, str] = {}  # Store user emails
            
            for group in all_groups:
                group_id = group.get("Id")
                if not group_id:
                    continue
                
                # Extract immediate members
                records = group.get("GroupMembers", {}).get("records", [])
                member_ids = []
                
                for member in records:
                    user_or_group_id = member.get("UserOrGroupId")
                    if not user_or_group_id:
                        continue
                        
                    member_ids.append(user_or_group_id)
                    
                    # Store email if this is a user (has UserOrGroup.Email)
                    user_or_group = member.get("UserOrGroup")
                    if user_or_group and user_or_group.get("Email"):
                        user_id_to_email[user_or_group_id] = user_or_group.get("Email")
                
                group_to_members[group_id] = member_ids
            
            # Flatten all groups to get actual user IDs
            flattened_result: Dict[str, Set[tuple]] = {}
            
            for group_id in group_to_members:
                visited: set = set()
                current_path: set = set()
                user_ids = self._flatten_single_group_members(
                    group_id, group_to_members, visited, current_path
                )
                
                # Convert user IDs to set of (user_id, email) tuples
                user_set = set()
                for user_id in user_ids:
                    email = user_id_to_email.get(user_id, "")
                    if email:  # Only include users with emails
                        user_set.add((user_id, email))
                
                flattened_result[group_id] = user_set
            
            return flattened_result
            
        except Exception as e:
            self.logger.error(f"Error flattening group members: {e}", exc_info=True)
            return {}

    async def run_incremental_sync(self) -> None:
        """
        Runs an incremental sync based on last sync timestamp.
        """
        try:
            self.logger.info("Starting Salesforce incremental sync.")
            # For now, run full sync
            await self.run_sync()
            self.logger.info("Salesforce incremental sync completed.")

        except Exception as ex:
            self.logger.error(f"Error in Salesforce incremental sync: {ex}", exc_info=True)
            raise

    def handle_webhook_notification(self, notification: Dict) -> None:
        """
        Handles webhook notifications from Salesforce.

        Args:
            notification: The webhook notification payload
        """
        self.logger.info("Salesforce webhook received.")
        asyncio.create_task(self.run_incremental_sync())

    async def cleanup(self) -> None:
        """
        Cleanup resources used by the connector.
        """
        self.logger.info("Cleaning up Salesforce connector resources.")
        self.data_source = None

    async def reindex_records(self, records: List[Record]) -> None:
        """
        Reindex records from Salesforce.

        For each record, checks whether Salesforce has a newer version by comparing
        the stored external_revision_id (epoch ms string for DEAL/CASE) against the
        record's current LastModifiedDate. Records that have been updated are rebuilt
        and sent to on_record_content_update; unchanged records are batched and sent
        to reindex_existing_records.
        """
        try:
            if not records:
                self.logger.info("No records to reindex")
                return

            self.logger.info(f"Starting reindex for {len(records)} Salesforce records")

            if not self.data_source:
                raise Exception("Salesforce client not initialized. Call init() first.")

            api_version = await self._get_api_version()
            to_reindex: List[Record] = []

            for record in records:
                # external_revision_id is stored as str(epoch_ms) for DEAL and CASE.
                # For TASK/PRODUCT it is a SystemModstamp string; for FILE a ContentVersionId.
                # Non-numeric values fall back to since_ms=0 → always re-fetch and rebuild.
                since_ms: Optional[int] = None
                if record.external_revision_id:
                    try:
                        since_ms = int(record.external_revision_id)
                    except (ValueError, TypeError):
                        pass

                updated_record = await self._fetch_salesforce_record_if_updated(
                    record.external_record_id, record.record_type, since_ms or 0, api_version
                )

                if updated_record:
                    await self.data_entities_processor.on_record_content_update(updated_record)
                else:
                    to_reindex.append(record)

            if to_reindex:
                await self.data_entities_processor.reindex_existing_records(to_reindex)

            self.logger.info(
                "Reindex complete: %s updated, %s reindexed as-is",
                len(records) - len(to_reindex),
                len(to_reindex),
            )

        except Exception as e:
            self.logger.error(f"Error during Salesforce reindex: {e}", exc_info=True)
            raise

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None
    ) -> None:
        """Salesforce connector does not support dynamic filter options yet."""
        raise NotImplementedError("Salesforce connector does not support dynamic filter options")

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str
    ) -> "BaseConnector":
        """
        Factory method to create a Salesforce connector instance.

        Args:
            logger: Logger instance
            data_store_provider: Data store provider for database operations
            config_service: Configuration service for accessing credentials
            connector_id: The connector instance ID

        Returns:
            Initialized SalesforceConnector instance
        """
        data_entities_processor = DataSourceEntitiesProcessor(
            logger, data_store_provider, config_service
        )
        await data_entities_processor.initialize()

        return SalesforceConnector(
            logger, data_entities_processor, data_store_provider, config_service, connector_id
        )