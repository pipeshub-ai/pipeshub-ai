"""
Google Sites Connector

Syncs a published Google Site by URL: user provides the published site URL (mandatory),
connector crawls it over HTTP to discover all pages, creates one RecordGroup and
FileRecords per page, and streams content for indexing via HTTP fetch.
"""

import os
import uuid
from logging import Logger
from typing import AsyncIterator, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    AppGroups,
    Connectors,
    MimeTypes,
    OriginTypes,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import config_node_constants
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.registry.auth_builder import AuthBuilder, AuthType
from app.connectors.core.registry.connector_builder import (
    AuthField,
    ConnectorBuilder,
    ConnectorScope,
    DocumentationLink,
)
from app.connectors.core.registry.filters import (
    FilterCategory,
    FilterCollection,
    FilterField,
    FilterOptionsResponse,
    FilterType,
    load_connector_filters,
)
from app.connectors.sources.google.common.apps import GoogleSitesApp
from app.models.entities import (
    AppUser,
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    User,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.google.google import GoogleClient
from app.sources.client.google.sites import GoogleSitesRESTClient
from app.sources.external.google.sites.sites import (
    LOG_URL_PREVIEW_LEN,
    LOG_URL_SHORT_PREVIEW_LEN,
    GoogleSitesDataSource,
    GoogleSitesPage,
    normalize_published_site_url,
)
from app.utils.streaming import create_stream_record_response
from app.utils.time_conversion import get_epoch_timestamp_in_ms

DEFAULT_CONNECTOR_ENDPOINT = os.getenv("CONNECTOR_ENDPOINT", "http://localhost:8000")


@ConnectorBuilder("Google Sites")\
    .in_group(AppGroups.GOOGLE_WORKSPACE.value)\
    .with_description("Sync a published Google Site by URL via HTTP crawl")\
    .with_categories(["Storage", "Documentation"])\
    .with_scopes([ConnectorScope.PERSONAL.value, ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.ACCESS_KEY).fields([
            AuthField(
                name="serviceAccountJson",
                display_name="Service Account JSON",
                placeholder="Click to upload service account JSON file",
                description="Upload your Service Account JSON key. This is optional for URL-based crawling of public sites.",
                field_type="FILE",
                is_secret=True
            ),
        ])
    ])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/google_sites.svg")
        .add_documentation_link(DocumentationLink(
            "Google Sites API – Service account",
            "https://developers.google.com/sites/api/v1/overview",
            "setup"
        ))
        .add_filter_field(FilterField(
            name="published_site_url",
            display_name="Published site URL",
            filter_type=FilterType.STRING,
            category=FilterCategory.SYNC,
            description="Full published Google Site URL (e.g. https://sites.google.com/view/your-site). This URL will be crawled over HTTP to discover and index all pages under the same site.",
            required=True,
        ))
        .with_sync_strategies(["MANUAL"])
        .with_scheduled_config(False, 60)
        .with_sync_support(True)
        .with_agent_support(True)
    )\
    .build_decorator()
class GoogleSitesConnector(BaseConnector):
    """
    Connector for Google Sites (new). Uses Drive API to list sites,
    creates RecordGroups for sites and FileRecords for pages within each site.
    Uses service account token authentication for fetching content.
    """

    def __init__(
        self,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
    ) -> None:
        super().__init__(
            app=GoogleSitesApp(connector_id),
            logger=logger,
            data_entities_processor=data_entities_processor,
            data_store_provider=data_store_provider,
            config_service=config_service,
            connector_id=connector_id,
        )
        self.connector_name = Connectors.GOOGLE_SITES
        self.connector_id = connector_id
        self.filter_key = "googlesites"
        self.batch_size = 100
        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()
        # Use GoogleClient like other Google connectors (Drive, Gmail, etc.).
        # Underlying HTTP for published-site crawl is wrapped via build_with_client.
        self.google_client = GoogleClient.build_with_client(GoogleSitesRESTClient())
        self.sites_data_source = GoogleSitesDataSource(client=self.google_client, logger=logger)

    def get_app_users(self, users: List[User]) -> List[AppUser]:
        return [
            AppUser(
                app_name=self.connector_name,
                connector_id=self.connector_id,
                source_user_id=user.source_user_id or user.id or user.email,
                org_id=user.org_id or self.data_entities_processor.org_id,
                email=user.email,
                full_name=user.full_name or user.email,
                is_active=user.is_active if user.is_active is not None else True,
                title=user.title,
            )
            for user in users
            if user.email
        ]

    async def init(self) -> bool:
        self.logger.info("")
        self.logger.info("=" * 60)
        self.logger.info("[Google Sites] INIT: Starting connector initialization")
        self.logger.info("[Google Sites] INIT: connector_id=%s", self.connector_id)
        self.logger.info("")

        try:
            self.logger.info("[Google Sites] INIT: Step 1 — Load sync and indexing filters")
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, self.filter_key, self.connector_id, self.logger
            )
            self.logger.info("[Google Sites] INIT:   ✓ Filters loaded")
            self.logger.info("")
            self.logger.info("=" * 60)
            self.logger.info("[Google Sites] INIT: ✓ Initialization complete")
            self.logger.info("")
            return True
        except Exception as e:
            self.logger.error("[Google Sites] INIT:   ❌ Failed: %s", e, exc_info=True)
            return False

    def _build_permissions(self) -> List[Permission]:
        """Build permissions for synced content (org read)."""
        return [
            Permission(
                type=PermissionType.READ,
                entity_type=EntityType.ORG,
                external_id=self.data_entities_processor.org_id,
            )
        ]

    async def run_sync(self) -> None:
        """Main sync: crawl published site URL over HTTP, discover pages, create RecordGroup + FileRecords."""
        try:
            self.logger.info("")
            self.logger.info("=" * 80)
            self.logger.info("[Google Sites] SYNC: ========== STARTING FULL SYNC ==========")
            self.logger.info("=" * 80)
            self.logger.info("")

            # Step 1: Reload filters
            self.logger.info("[Google Sites] SYNC: Step 1 — Reload filters")
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, self.filter_key, self.connector_id, self.logger
            )
            self.logger.info("[Google Sites] SYNC:   ✓ Filters loaded")
            self.logger.info("")

            # Step 2: Validate mandatory published site URL
            published_url_filter = self.sync_filters.get("published_site_url") if self.sync_filters else None
            raw_url = (published_url_filter.get_value() or "") if published_url_filter else ""
            if not raw_url or (isinstance(raw_url, str) and not raw_url.strip()):
                self.logger.error("[Google Sites] SYNC:   ❌ Published site URL is required")
                raise ValueError("Published site URL is required. Set the 'Published site URL' filter and try again.")
            try:
                start_url = normalize_published_site_url(
                    raw_url.strip() if isinstance(raw_url, str) else str(raw_url)
                )
            except ValueError as e:
                self.logger.error("[Google Sites] SYNC:   ❌ Invalid URL: %s", e)
                raise
            self.logger.info(
                "[Google Sites] SYNC:   Published site URL: %s",
                start_url[:LOG_URL_PREVIEW_LEN]
                + ("..." if len(start_url) > LOG_URL_PREVIEW_LEN else ""),
            )
            self.logger.info("")

            # Step 3: Sync app users
            self.logger.info("[Google Sites] SYNC: Step 3 — Sync app users")
            all_active_users = await self.data_entities_processor.get_all_active_users()
            app_users = self.get_app_users(all_active_users)
            await self.data_entities_processor.on_new_app_users(app_users)
            self.logger.info("[Google Sites] SYNC:   ✓ %s app users", len(app_users))
            self.logger.info("")

            base_parsed = urlparse(start_url)
            base_origin = f"{base_parsed.scheme}://{base_parsed.netloc}"

            # Step 4: Crawl published site using datasource
            self.logger.info(
                "[Google Sites] SYNC: Step 4 — Crawl published site"
            )
            pages: List[GoogleSitesPage] = await self.sites_data_source.crawl_site(start_url)

            if not pages:
                self.logger.warning("[Google Sites] SYNC:   ⚠ No pages discovered")
                self.logger.info("=" * 80)
                self.logger.info("")
                return

            # Step 5: Create RecordGroup (one per site)
            first_page = pages[0]
            site_name = first_page.title if first_page.title != "Page" else base_parsed.netloc or "Google Site"
            record_group = RecordGroup(
                name=site_name,
                external_group_id=base_origin,
                group_type=RecordGroupType.SITE,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                description=f"Google Site: {site_name}",
                web_url=start_url,
            )
            permissions = self._build_permissions()
            await self.data_entities_processor.on_new_record_groups([(record_group, permissions)])
            self.logger.info("[Google Sites] SYNC:   ✓ RecordGroup: %s", site_name)
            self.logger.info("")

            # Step 6: Create FileRecords for each page
            timestamp_ms = get_epoch_timestamp_in_ms()
            batch_records: List[Tuple[FileRecord, List[Permission]]] = []
            for page in pages:
                page_url = page.url
                page_title = page.title
                page_revision_id = f"{page_url}/{timestamp_ms}"
                file_record = FileRecord(
                    id=str(uuid.uuid4()),
                    record_name=page_title or "Page",
                    record_type=RecordType.FILE,
                    record_group_type=RecordGroupType.SITE.value,
                    external_record_group_id=base_origin,
                    external_record_id=page_url,
                    external_revision_id=page_revision_id,
                    version=0,
                    origin=OriginTypes.CONNECTOR.value,
                    connector_name=self.connector_name,
                    connector_id=self.connector_id,
                    source_created_at=timestamp_ms,
                    source_updated_at=timestamp_ms,
                    weburl=page_url,
                    signed_url=None,
                    fetch_signed_url=None,
                    mime_type=MimeTypes.HTML.value,
                    is_file=True,
                    extension="html",
                    path=urlparse(page_url).path or "/",
                    parent_external_record_id=None,
                    parent_record_type=None,
                    etag=None,
                    ctag=None,
                    quick_xor_hash=None,
                    crc32_hash=None,
                    sha1_hash=None,
                    sha256_hash=None,
                )
                batch_records.append((file_record, permissions))
            if batch_records:
                await self.data_entities_processor.on_new_records(batch_records)

            self.logger.info("")
            self.logger.info("=" * 80)
            self.logger.info("[Google Sites] SYNC: ========== SYNC COMPLETED ==========")
            self.logger.info("[Google Sites] SYNC: Pages discovered and indexed: %s", len(pages))
            self.logger.info("=" * 80)
            self.logger.info("")

        except Exception as ex:
            self.logger.error("[Google Sites] SYNC: FAILED: %s", ex, exc_info=True)
            raise

    async def get_signed_url(self, record: Record) -> Optional[str]:
        endpoints = await self.config_service.get_config(config_node_constants.ENDPOINTS.value)
        connector_endpoint = endpoints.get("connectors", {}).get("endpoint", DEFAULT_CONNECTOR_ENDPOINT)
        return f"{connector_endpoint}/api/v1/internal/stream/record/{record.id}"

    async def test_connection_and_access(self) -> bool:
        """Test access by validating the Published site URL over HTTP.

        If the mandatory Published site URL filter is set, we normalize it and perform an
        HTTP HEAD request to confirm the site is reachable. Returns True on 2xx/3xx, and
        False on 4xx/5xx or other failures.

        If the Published site URL filter is not set, a ValueError is raised.
        """
        self.logger.info("")
        self.logger.info("[Google Sites] TEST: Testing connection")
        try:
            self.sync_filters, _ = await load_connector_filters(
                self.config_service, self.filter_key, self.connector_id, self.logger
            )
            published_url_filter = self.sync_filters.get("published_site_url") if self.sync_filters else None
            raw_url = (published_url_filter.get_value() or "") if published_url_filter else ""
            url_str = (raw_url.strip() if isinstance(raw_url, str) else str(raw_url or "")).strip()
            if not url_str:
                self.logger.error(
                    "[Google Sites] TEST:   ❌ Published site URL filter is required for connection testing"
                )
                raise ValueError(
                    "Google Sites connector requires the 'Published site URL' sync filter to be set "
                    "before testing the connection."
                )

            try:
                start_url = normalize_published_site_url(url_str)
            except ValueError as e:
                self.logger.error("[Google Sites] TEST:   ❌ Invalid published site URL: %s", e)
                return False

            reachable = await self.sites_data_source.check_url_reachable(start_url)
            if reachable:
                self.logger.info(
                    "[Google Sites] TEST:   ✓ Published site URL reachable"
                )
                self.logger.info("")
                return True
            self.logger.warning(
                "[Google Sites] TEST:   Published URL is not reachable or returned non-success status"
            )
            return False
        except Exception as e:
            self.logger.error("[Google Sites] TEST:   ❌ %s", e)
            return False

    def _is_url_based_record(self, record: Record) -> bool:
        """True if record was created from URL crawl (external_record_group_id is a URL)."""
        group_id = getattr(record, "external_record_group_id", None) or ""
        return isinstance(group_id, str) and (
            group_id.startswith("http://") or group_id.startswith("https://")
        )

    async def stream_record(self, record: Record, user_id: Optional[str] = None, convertTo: Optional[str] = None) -> StreamingResponse:
        """Stream record content for indexing.

        URL-based records: fetch record.weburl over HTTP and return cleaned text.
        """
        if isinstance(record, FileRecord) and not record.is_file:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Cannot stream folder content",
            )

        self.logger.info("")
        self.logger.info(
            "[Google Sites] STREAM: record_id=%s name=%s",
            record.id,
            getattr(record, "record_name", "") or "(unnamed)",
        )
        self.logger.info(
            "[Google Sites] STREAM:   URL: %s",
            (record.weburl or "").strip()[:LOG_URL_SHORT_PREVIEW_LEN] + "..."
            if len((record.weburl or "")) > LOG_URL_SHORT_PREVIEW_LEN
            else (record.weburl or "(none)"),
        )

        if self._is_url_based_record(record):
            page_url = (record.weburl or "").strip()
            if not page_url:
                raise HTTPException(
                    status_code=HttpStatusCode.NOT_FOUND.value,
                    detail="Record has no web URL",
                )
            self.logger.info("[Google Sites] STREAM:   Fetching page over HTTP")
            try:
                cleaned_text = await self.sites_data_source.fetch_and_clean_page(page_url)
            except RuntimeError as e:
                self.logger.error("[Google Sites] STREAM:   ❌ HTTP error: %s", e)
                raise HTTPException(
                    status_code=HttpStatusCode.NOT_FOUND.value,
                    detail=str(e),
                ) from e
            except Exception as e:
                self.logger.error("[Google Sites] STREAM:   ❌ Failed to fetch page: %s", e)
                raise HTTPException(
                    status_code=HttpStatusCode.BAD_GATEWAY.value,
                    detail=f"Failed to fetch page: {e}",
                ) from e
            content_bytes = cleaned_text.encode("utf-8")
            self.logger.info("[Google Sites] STREAM:   ✓ Returning %s bytes", len(content_bytes))
            self.logger.info("")

            async def content_gen() -> AsyncIterator[bytes]:
                yield content_bytes

            return create_stream_record_response(
                content_gen(),
                filename=record.record_name or "page.html",
                mime_type="text/html",
                fallback_filename=f"record_{record.id}",
            )
        self.logger.error(
            "[Google Sites] STREAM:   ❌ Unsupported record type; only URL-based Google Sites records are supported"
        )
        raise HTTPException(
            status_code=HttpStatusCode.NOT_FOUND.value,
            detail="Only URL-based Google Sites records are supported by the Google Sites connector.",
        )

    async def run_incremental_sync(self) -> None:
        await self.run_sync()

    def handle_webhook_notification(self, notification: Dict) -> None:
        pass

    async def cleanup(self) -> None:
        self.logger.info("")
        self.logger.info(
            "[Google Sites] CLEANUP: URL-based Google Sites connector has no external resources to release"
        )
        self.logger.info("")

    async def reindex_records(self, record_results: List[Record]) -> None:
        if not record_results:
            return
        for record in record_results:
            await self.data_entities_processor.on_record_content_update(record)

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
    ) -> BaseConnector:
        data_entities_processor = DataSourceEntitiesProcessor(
            logger,
            data_store_provider,
            config_service,
        )
        await data_entities_processor.initialize()
        return GoogleSitesConnector(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
        )

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None,
    ) -> FilterOptionsResponse:
        """Google Sites connector uses only the mandatory Published site URL filter; no dynamic options."""
        raise ValueError(
            "Google Sites connector has no dynamic filter options. "
            "Only the 'Published site URL' string sync filter is supported."
        )
