"""RSS/Atom Feed Connector Implementation"""

import asyncio
import hashlib
import uuid
from io import BytesIO
from logging import Logger
from typing import Dict, List, Optional, Set, Tuple, Union
from urllib.parse import urlparse

import aiohttp
import feedparser
import trafilatura
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import AppGroups, Connectors, MimeTypes, OriginTypes
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.interfaces.connector.apps import App
from app.connectors.core.registry.connector_builder import (
    ConnectorBuilder,
    ConnectorScope,
    CustomField,
    DocumentationLink,
)
from app.connectors.core.registry.filters import FilterOptionsResponse
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
from app.utils.streaming import create_stream_record_response
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class RSSApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.RSS, AppGroups.RSS, connector_id)


@(
    ConnectorBuilder("RSS")
    .in_group("RSS")
    .with_supported_auth_types("NONE")
    .with_description("Subscribe to and sync content from RSS and Atom feeds")
    .with_categories(["Web", "Content"])
    .with_scopes([ConnectorScope.PERSONAL.value, ConnectorScope.TEAM.value])
    .configure(
        lambda builder: builder.with_icon("/assets/icons/connectors/rss.svg")
        .with_realtime_support(False)
        .add_documentation_link(
            DocumentationLink(
                "RSS Connector Guide",
                "https://docs.pipeshub.ai/connectors/rss",
                "setup",
            )
        )
        .with_scheduled_config(True, 60)  # Hourly sync (RSS feeds update frequently)
        .add_sync_custom_field(
            CustomField(
                name="feed_urls",
                display_name="Feed URLs",
                field_type="TEXT",
                required=True,
                description="RSS or Atom feed URLs, separated by commas or newlines (e.g., https://blog.example.com/rss, https://news.ycombinator.com/rss)",
            )
        )
        .add_sync_custom_field(
            CustomField(
                name="max_articles_per_feed",
                display_name="Max Articles Per Feed",
                field_type="NUMBER",
                required=False,
                default_value="50",
                description="Maximum number of articles to process per feed (1-500)",
            )
        )
        .add_sync_custom_field(
            CustomField(
                name="fetch_full_content",
                display_name="Fetch Full Content",
                field_type="BOOLEAN",
                required=False,
                default_value="true",
                description="Crawl each article URL for full content. If disabled, only the feed summary/description is indexed.",
            )
        )
        .with_sync_support(True)
        .with_agent_support(False)
    )
    .build_decorator()
)
class RSSConnector(BaseConnector):
    """
    RSS/Atom feed connector for subscribing to and indexing feed content.

    Features:
    - Supports both RSS 2.0 and Atom feed formats
    - Multi-feed support (comma or newline separated URLs)
    - Optional full-content crawling of article pages
    - Fallback to feed summary/description when full crawl disabled
    - Deduplication via article link/GUID
    - Org-level read permissions (public content)
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
            RSSApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
        )
        self.connector_name = Connectors.RSS
        self.connector_id = connector_id

        # Configuration (loaded during init)
        self.feed_urls: List[str] = []
        self.max_articles_per_feed: int = 50
        self.fetch_full_content: bool = True

        # HTTP session
        self.session: Optional[aiohttp.ClientSession] = None

        # Tracking state
        self.processed_urls: Set[str] = set()
        self.batch_size: int = 50

        # Scope and creator (set from config in init())
        self.connector_scope: Optional[str] = None
        self.created_by: Optional[str] = None

    async def init(self) -> bool:
        """Initialize the RSS connector with configuration from etcd."""
        try:
            config = await self.config_service.get_config(
                f"/services/connectors/{self.connector_id}/config"
            )

            if not config:
                self.logger.error("❌ RSS connector config not found")
                raise ValueError("RSS connector configuration not found")

            sync_config = config.get("sync", {})
            if not sync_config:
                self.logger.error("❌ RSS sync config not found")
                raise ValueError("RSS sync config not found")

            # Parse feed URLs (comma or newline separated)
            raw_urls = sync_config.get("feed_urls", "")
            if not raw_urls:
                self.logger.error("❌ No feed URLs configured")
                raise ValueError("No feed URLs configured")

            self.feed_urls = self._parse_feed_urls(raw_urls)
            if not self.feed_urls:
                self.logger.error("❌ No valid feed URLs found after parsing")
                raise ValueError("No valid feed URLs found")

            self.max_articles_per_feed = int(
                sync_config.get("max_articles_per_feed", 50)
            )
            self.fetch_full_content = sync_config.get("fetch_full_content", True)
            # Handle string "true"/"false" from config
            if isinstance(self.fetch_full_content, str):
                self.fetch_full_content = self.fetch_full_content.lower() == "true"

            scope_from_config = config.get("scope")
            self.connector_scope = scope_from_config if scope_from_config else ConnectorScope.PERSONAL.value
            self.created_by = config.get("createdBy") or config.get("created_by")

            # Initialize aiohttp session with realistic browser headers
            timeout = aiohttp.ClientTimeout(total=30)
            self.session = aiohttp.ClientSession(
                timeout=timeout,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate, br",
                    "DNT": "1",
                    "Connection": "keep-alive",
                },
            )

            self.logger.info(
                f"✅ RSS connector initialized: {len(self.feed_urls)} feed(s), "
                f"max_articles={self.max_articles_per_feed}, fetch_full={self.fetch_full_content}"
            )
            return True

        except Exception as e:
            self.logger.error(
                f"❌ Failed to initialize RSS connector: {e}", exc_info=True
            )
            return False

    def _parse_feed_urls(self, raw_urls: str) -> List[str]:
        """Parse comma or newline separated feed URLs into a clean list."""
        if isinstance(raw_urls, str):
            # Split by newlines and commas
            urls = raw_urls.replace(",", "\n").split("\n")

        clean_urls = []
        for url in urls:
            url = url.strip()
            if url and url.startswith(("http://", "https://")):
                clean_urls.append(url)

        return list(dict.fromkeys(clean_urls))  # Deduplicate while preserving order

    async def test_connection_and_access(self) -> bool:
        """Test if the configured feed URLs are accessible."""
        if not self.feed_urls or not self.session:
            return False

        try:
            # Test the first feed URL
            async with self.session.get(
                self.feed_urls[0], allow_redirects=True
            ) as response:
                if response.status < HttpStatusCode.BAD_REQUEST.value:
                    self.logger.info(
                        f"✅ Feed accessible: {self.feed_urls[0]} (status: {response.status})"
                    )
                    return True
                else:
                    self.logger.warning(
                        f"⚠️ Feed returned status {response.status}: {self.feed_urls[0]}"
                    )
                    return False
        except Exception as e:
            self.logger.error(f"❌ Failed to access feed: {e}")
            return False

    def get_app_users(self, users: List[User]) -> List[AppUser]:
        """Convert User objects to AppUser objects."""
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

    async def create_record_group(self, feed_url: str) -> None:
        """
        Create a record group for a specific feed URL.

        Args:
            feed_url: The RSS/Atom feed URL
        """
        try:
            parsed_url = urlparse(feed_url)
            record_group_name = parsed_url.netloc or feed_url

            record_group = RecordGroup(
                org_id=self.data_entities_processor.org_id,
                name=record_group_name,
                external_group_id=feed_url,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                group_type=RecordGroupType.RSS_FEED,
                web_url=feed_url,
                created_at=get_epoch_timestamp_in_ms(),
                updated_at=get_epoch_timestamp_in_ms(),
            )

            # Org-level READ permission (RSS content is public within the org)
            permissions = [
                Permission(
                    external_id=self.data_entities_processor.org_id,
                    type=PermissionType.READ,
                    entity_type=EntityType.ORG,
                )
            ]

            await self.data_entities_processor.on_new_record_groups(
                [(record_group, permissions)]
            )
            self.logger.info(
                f"✅ Created record group '{record_group_name}' for feed with org-level permissions"
            )

        except Exception as e:
            self.logger.error(
                f"❌ Failed to create record group for {feed_url}: {e}", exc_info=True
            )
            raise

    async def run_sync(self) -> None:
        """Main sync method: parse feeds, crawl articles, and index content."""
        try:
            self.logger.info(f"🚀 Starting RSS sync for {len(self.feed_urls)} feed(s)")

            if self.connector_scope == ConnectorScope.TEAM.value:
                async with self.data_store_provider.transaction() as tx_store:
                    await tx_store.ensure_team_app_edge(
                        self.connector_id,
                        self.data_entities_processor.org_id,
                    )
                app_users = []
            else:
                # Personal: create user-app edge only for the creator
                if self.created_by:
                    creator_user = await self.data_entities_processor.get_user_by_user_id(self.created_by)
                    if creator_user and getattr(creator_user, "email", None):
                        app_users = self.get_app_users([creator_user])
                        await self.data_entities_processor.on_new_app_users(app_users)
                    else:
                        self.logger.warning(
                            "Creator user not found or has no email for created_by %s; skipping user-app edges.",
                            self.created_by,
                        )
                        app_users = []
                else:
                    self.logger.warning(
                        "Personal connector has no created_by; skipping user-app edges."
                    )
                    app_users = []

            # Step 2: Process each feed
            total_articles = 0
            for feed_url in self.feed_urls:
                try:
                    count = await self._process_feed(feed_url, app_users)
                    total_articles += count
                except Exception as e:
                    self.logger.error(
                        f"❌ Error processing feed {feed_url}: {e}", exc_info=True
                    )
                    continue

            self.logger.info(
                f"✅ RSS sync completed: {total_articles} articles processed across {len(self.feed_urls)} feed(s)"
            )

        except Exception as e:
            self.logger.error(f"❌ Error during RSS sync: {e}", exc_info=True)
            raise

    async def _process_feed(self, feed_url: str, app_users: List[AppUser]) -> int:
        """
        Process a single RSS/Atom feed: parse entries, crawl articles, create records.

        Args:
            feed_url: The feed URL to process
            app_users: List of AppUser for permissions

        Returns:
            Number of articles processed
        """
        self.logger.info(f"📡 Processing feed: {feed_url}")

        # Create record group for this feed
        await self.create_record_group(feed_url)

        # Fetch and parse the feed
        feed = await self._fetch_and_parse_feed(feed_url)
        if not feed or not feed.entries:
            self.logger.warning(f"⚠️ No entries found in feed: {feed_url}")
            return 0

        feed_title = feed.feed.get("title", urlparse(feed_url).netloc)
        self.logger.info(f"📰 Feed '{feed_title}' has {len(feed.entries)} entries")

        # Process entries in batches
        records_batch: List[Tuple[FileRecord, List[Permission]]] = []
        processed_count = 0

        for entry in feed.entries[: self.max_articles_per_feed]:
            try:
                result = await self._process_entry(entry, feed_url)
                if result:
                    records_batch.append(result)
                    processed_count += 1

                    # Flush batch when it reaches batch_size
                    if len(records_batch) >= self.batch_size:
                        await self.data_entities_processor.on_new_records(records_batch)
                        self.logger.info(
                            f"  📦 Flushed batch of {len(records_batch)} records"
                        )
                        records_batch = []

            except Exception as e:
                entry_title = entry.get("title", "Unknown")
                self.logger.error(
                    f"❌ Error processing entry '{entry_title}': {e}", exc_info=True
                )
                continue

        # Flush remaining records
        if records_batch:
            await self.data_entities_processor.on_new_records(records_batch)
            self.logger.info(
                f"  📦 Flushed final batch of {len(records_batch)} records"
            )

        self.logger.info(f"✅ Processed {processed_count} articles from '{feed_title}'")
        return processed_count

    async def _fetch_and_parse_feed(
        self, feed_url: str
    ) -> Optional[feedparser.FeedParserDict]:
        """Fetch and parse an RSS/Atom feed URL."""
        try:
            async with self.session.get(feed_url, allow_redirects=True) as response:
                if response.status >= HttpStatusCode.BAD_REQUEST.value:
                    self.logger.warning(
                        f"⚠️ HTTP {response.status} fetching feed: {feed_url}"
                    )
                    return None

                content = await response.read()
                feed = feedparser.parse(content)

                if feed.bozo and not feed.entries:
                    self.logger.warning(
                        f"⚠️ Feed parse warning for {feed_url}: {feed.bozo_exception}"
                    )
                    return None

                return feed

        except asyncio.TimeoutError:
            self.logger.warning(f"⚠️ Timeout fetching feed: {feed_url}")
            return None
        except Exception as e:
            self.logger.error(f"❌ Error fetching feed {feed_url}: {e}", exc_info=True)
            return None

    async def _process_entry(
        self, entry: Dict, feed_url: str
    ) -> Optional[Tuple[FileRecord, List[Permission]]]:
        """
        Process a single feed entry: extract metadata, optionally crawl full content,
        and create a FileRecord.

        Args:
            entry: A parsed feed entry from feedparser
            feed_url: The parent feed URL (used as external_record_group_id)

        Returns:
            Tuple of (FileRecord, permissions) or None on failure
        """
        # Extract article URL (the link to the full article)
        article_url = entry.get("link", "")
        if not article_url:
            return None

        # Skip if already processed in this sync run
        if article_url in self.processed_urls:
            return None
        self.processed_urls.add(article_url)

        # Extract metadata from feed entry
        title = entry.get("title", self._extract_title_from_url(article_url))
        published = entry.get("published_parsed") or entry.get("updated_parsed")
        guid = entry.get("id", article_url)  # Unique ID from feed, fallback to URL

        # Parse timestamps
        source_created_at = self._parse_feed_timestamp(published)
        timestamp = get_epoch_timestamp_in_ms()

        # Resolve content using a priority chain:
        content_text = ""

        # 1. entry.content[0].value contains structured semantic HTML from the feed
        #    author (headings, lists, emphasis, code blocks, etc.). Running it through
        #    trafilatura would strip this meaningful markup. We preserve it as-is so
        #    downstream indexing/rendering retains the author's intended structure.
        entry_content = entry.get("content")
        if entry_content and isinstance(entry_content, list) and len(entry_content) > 0:
            value = entry_content[0].get("value", "")
            if value:
                content_text = value

        # 2. If entry.content.value is NOT present, try other sources
        if not content_text:
            # 2a. If fetch_full_content is enabled, crawl the full article page
            if self.fetch_full_content:
                content_text = await self._fetch_article_content(article_url)

            # 2b. Try entry summary (feedparser normalizes <description> into summary)
            if not content_text:
                summary = entry.get("summary", "")
                if summary:
                    summary_type = entry.get("summary_detail", {}).get("type", "")
                    if summary_type in ("text/html", "application/xhtml+xml"):
                        content_text = self._extract_text_content(summary)
                    else:
                        content_text = summary

        # 3. Final fallback → use title
        if not content_text:
            content_text = title

        content_bytes = content_text.encode("utf-8")
        content_md5_hash = hashlib.md5(content_bytes).hexdigest()

        # Create FileRecord
        record_id = str(uuid.uuid4())
        file_record = FileRecord(
            id=record_id,
            record_name=title,
            record_type=RecordType.FILE,
            record_group_type=RecordGroupType.RSS_FEED,
            external_record_id=guid,
            external_revision_id=content_md5_hash,
            external_record_group_id=feed_url,
            version=0,
            origin=OriginTypes.CONNECTOR.value,
            connector_name=self.connector_name,
            connector_id=self.connector_id,
            created_at=timestamp,
            updated_at=timestamp,
            source_created_at=source_created_at or timestamp,
            source_updated_at=source_created_at or timestamp,
            weburl=article_url,
            size_in_bytes=len(content_bytes),
            is_file=True,
            extension="html",
            path=urlparse(article_url).path or "/",
            mime_type=MimeTypes.HTML.value,
            md5_hash=content_md5_hash,
            preview_renderable=False,
        )

        # Org-level READ permissions (public content)
        permissions = [
            Permission(
                external_id=self.data_entities_processor.org_id,
                type=PermissionType.READ,
                entity_type=EntityType.ORG,
            )
        ]

        return file_record, permissions

    async def _fetch_article_content(self, url: str) -> str:
        """
        Fetch the full article page and extract clean text content.

        Args:
            url: Article URL to crawl

        Returns:
            Cleaned text content or empty string on failure
        """
        try:
            async with self.session.get(url, allow_redirects=True) as response:
                if response.status >= HttpStatusCode.BAD_REQUEST.value:
                    self.logger.debug(f"⚠️ HTTP {response.status} for article: {url}")
                    return ""

                content_type = response.headers.get("Content-Type", "").lower()
                if "html" not in content_type and "xml" not in content_type:
                    self.logger.debug(
                        f"⚠️ Non-HTML content type for {url}: {content_type}"
                    )
                    return ""

                content_bytes = await response.read()
                return self._extract_text_content(content_bytes)

        except asyncio.TimeoutError:
            self.logger.debug(f"⚠️ Timeout fetching article: {url}")
            return ""
        except Exception as e:
            self.logger.debug(f"⚠️ Error fetching article {url}: {e}")
            return ""

    def _extract_title_from_url(self, url: str) -> str:
        """Derive a readable title from an article URL when the feed has no title."""
        if not url:
            return "Untitled"
        try:
            parsed = urlparse(url)
            path = (parsed.path or "").strip("/")
            if path:
                # Use last path segment, replace hyphens/underscores with spaces
                segment = path.split("/")[-1]
                if segment:
                    return (
                        segment.replace("-", " ").replace("_", " ").strip()
                        or parsed.netloc
                        or url
                    )
            return parsed.netloc or url
        except Exception:
            return url

    def _parse_feed_timestamp(self, time_struct) -> Optional[int]:
        """Convert a feedparser time.struct_time to epoch timestamp in milliseconds."""
        if not time_struct:
            return None
        try:
            from calendar import timegm

            epoch_seconds = timegm(time_struct)
            return epoch_seconds * 1000
        except Exception:
            return None

    def _extract_text_content(self, html: Union[str, bytes]) -> str:
        """Extract meaningful text from HTML using trafilatura."""
        if not html:
            return ""
        try:
            if isinstance(html, bytes):
                html = html.decode("utf-8", errors="replace")
            extracted = trafilatura.extract(html)
            return extracted or ""
        except Exception:
            return ""

    async def stream_record(self, record: Record) -> Optional[StreamingResponse]:
        """
        Stream the article content. Fetches the article page and returns
        cleaned HTML content as a streaming response.

        Args:
            record: Record object containing the article URL

        Returns:
            StreamingResponse with the processed content, or None on failure
        """
        if not record.weburl:
            return None

        try:
            async with self.session.get(
                record.weburl, allow_redirects=True
            ) as response:
                if response.status >= HttpStatusCode.BAD_REQUEST.value:
                    return None

                content_bytes = await response.read()
                mime_type = record.mime_type or "text/html"

                # Clean HTML content
                if "html" in mime_type.lower():
                    cleaned_text = self._extract_text_content(content_bytes)
                    if cleaned_text:
                        content_bytes = cleaned_text.encode("utf-8")

                return create_stream_record_response(
                    BytesIO(content_bytes),
                    filename=record.record_name,
                    mime_type=mime_type,
                    fallback_filename=f"record_{record.id}",
                )

        except Exception as e:
            self.logger.error(f"❌ Error streaming record {record.id}: {e}")
            return None

    async def run_incremental_sync(self) -> None:
        """Run incremental sync (same as full sync for RSS feeds)."""
        await self.run_sync()

    @classmethod
    async def create_connector(
        cls,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
    ) -> BaseConnector:
        """Factory method to create an RSSConnector instance."""
        data_entities_processor = DataSourceEntitiesProcessor(
            logger, data_store_provider, config_service
        )
        await data_entities_processor.initialize()
        return RSSConnector(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
        )

    async def cleanup(self) -> None:
        """Cleanup resources."""
        if self.session:
            await self.session.close()
            self.session = None
        self.processed_urls.clear()
        self.logger.info("✅ RSS connector cleanup completed")

    async def reindex_records(self, record_results: List[Record]) -> None:
        """Reindex records — not implemented for RSS connector."""
        raise NotImplementedError("RSS connector does not support reindexing")
    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None,
    ) -> FilterOptionsResponse:
        """RSS connector does not support dynamic filter options."""
        raise NotImplementedError("RSS connector does not support dynamic filter options")

    async def handle_webhook_notification(self, notification: Dict) -> None:
        """RSS connector doesn't support webhooks."""
        raise NotImplementedError("RSS connector does not support webhooks")

    async def get_signed_url(self, record: Record) -> Optional[str]:
        """RSS connector does not support signed URLs."""
        raise NotImplementedError("RSS connector does not support signed URLs")
