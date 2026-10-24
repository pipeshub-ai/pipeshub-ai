"""RSS/Atom Feed Connector Implementation"""

import asyncio
import hashlib
import uuid
from io import BytesIO
from logging import Logger
from typing import Dict, List, Optional, Set, Tuple, Union
from urllib.parse import urljoin, urlparse

import aiohttp
import feedparser
import trafilatura
from bs4 import BeautifulSoup
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    PermissionModel,
    AppGroups,
    CollectionNames,
    Connectors,
    ExtensionTypes,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
)
from app.connectors.core.constants import IconPaths
from app.connectors.sources.web.address_guard import create_guarded_session
from app.connectors.sources.web.fetch_strategy import (
    FetchResponse,
    fetch_url_with_fallback,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.base.connector.connector_service import (
    BaseConnector,
    ConnectorInitError,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.error.stream_errors import (
    connector_not_ready,
    map_source_status,
    not_found_at_source,
    to_stream_error,
)
from app.connectors.core.interfaces.connector.apps import App
from app.connectors.core.registry.connector_builder import (
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
    CustomField,
    DocumentationLink,
)
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOptionsResponse,
    IndexingFilterKey,
    load_connector_filters,
)
from app.connectors.core.sync.full_sync_coverage import FullSyncCoverage
from app.models.entities import (
    AppUser,
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    User,
)
from app.models.permission import Permission
from app.services.notification.types import NotificationSeverity, NotificationType
from app.utils.streaming import create_stream_record_response
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from app.utils.url_redaction import redact_url, redact_urls_in_text

MAX_ARTICLES_PER_FEED_LIMIT = 500
DEFAULT_MAX_ARTICLES_PER_FEED = 50
# A feed or an article page past this is not read: the whole body is held in memory.
MAX_FETCH_SIZE_MB = 10
# Feeds named in a notification; the rest are counted.
MAX_FEEDS_NAMED = 5
# Pages of a paged feed read per sync, the first included.
MAX_FEED_PAGES = 10


class FeedUnreadableError(Exception):
    """A configured feed could not be fetched or parsed; the message says why."""


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
    .with_permission_model(PermissionModel.APP_LEVEL)
    .configure(
        lambda builder: builder.with_icon(IconPaths.connector_icon(Connectors.RSS.value))
        .with_realtime_support(False)
        .add_documentation_link(
            DocumentationLink(
                "RSS Connector Guide",
                "https://docs.pipeshub.com/connectors/rss/rss",
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
                default_value=str(DEFAULT_MAX_ARTICLES_PER_FEED),
                description="Maximum number of articles to process per feed (1-500)",
                min_value=1,
                max_value=MAX_ARTICLES_PER_FEED_LIMIT,
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
        .add_filter_field(CommonFields.enable_manual_sync_filter())
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
        scope: str,
        created_by: str,
    ) -> None:
        super().__init__(
            RSSApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
        self.connector_name = Connectors.RSS
        self.connector_id = connector_id

        # Configuration (loaded during init)
        self.feed_urls: List[str] = []
        self.max_articles_per_feed: int = 50
        self.fetch_full_content: bool = True
        self.indexing_filters: FilterCollection = FilterCollection()

        # HTTP session
        self.session: Optional[aiohttp.ClientSession] = None

        # Tracking state
        self.processed_urls: Set[str] = set()
        self.batch_size: int = 50

    async def init(self) -> bool:
        """Initialize the RSS connector with configuration from etcd."""
        try:
            await self._load_config()

            # Load creator email if needed (for personal scope permission creation)
            await self._load_creator_email()

            # Initialize aiohttp session with realistic browser headers
            timeout = aiohttp.ClientTimeout(total=30)
            self.session = create_guarded_session(
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

        except ConnectorInitError as e:
            self.logger.error(f"❌ RSS connector settings are not usable: {e}")
            raise
        except Exception as e:
            self.logger.error(
                f"❌ Failed to initialize RSS connector: {e}", exc_info=True
            )
            return False

    async def _load_config(self, *, use_cache: bool = True) -> None:
        """Read the feed settings. Unusable settings raise ConnectorInitError, whose
        message is shown to the user as is."""
        config = await self.config_service.get_config(
            f"/services/connectors/{self.connector_id}/config", use_cache=use_cache
        )
        sync_config = (config or {}).get("sync") or {}
        self.feed_urls = self._parse_feed_urls(sync_config.get("feed_urls") or "")
        if not self.feed_urls:
            raise ConnectorInitError(
                "No feed URLs are configured. Add at least one RSS or Atom feed URL "
                "that starts with http:// or https://."
            )

        raw_limit = sync_config.get("max_articles_per_feed")
        try:
            limit = int(raw_limit) if raw_limit not in (None, "") else DEFAULT_MAX_ARTICLES_PER_FEED
        except (TypeError, ValueError):
            raise ConnectorInitError(
                f"Max Articles Per Feed must be a whole number from 1 to {MAX_ARTICLES_PER_FEED_LIMIT}."
            ) from None
        # Settings saved before the field had a range can hold any number.
        self.max_articles_per_feed = min(max(limit, 1), MAX_ARTICLES_PER_FEED_LIMIT)

        self.fetch_full_content = sync_config.get("fetch_full_content", True)
        if isinstance(self.fetch_full_content, str):
            self.fetch_full_content = self.fetch_full_content.lower() == "true"

        _, self.indexing_filters = await load_connector_filters(
            self.config_service, "rss", self.connector_id, self.logger
        )

    def _parse_feed_urls(self, raw_urls: str | list[str]) -> list[str]:
        """Parse comma or newline separated feed URLs into a clean list."""
        if isinstance(raw_urls, list):
            urls = [str(url) for url in raw_urls]
        else:
            urls = str(raw_urls).replace(",", "\n").split("\n")

        clean_urls = []
        for url in urls:
            url = url.strip()
            if url and url.startswith(("http://", "https://")):
                clean_urls.append(url)

        return list(dict.fromkeys(clean_urls))  # Deduplicate while preserving order

    def _create_rss_permissions(self) -> list[Permission]:
        """Articles inherit the feed, and the feed inherits the app. No permission edges."""
        return []

    async def test_connection_and_access(self) -> bool:
        """Check that every configured URL answers with an RSS or Atom feed.

        Raises ConnectorInitError naming each feed that does not, so the user
        knows which URL to fix instead of a generic connection failure.
        """
        if not self.feed_urls or not self.session:
            return False

        unreadable: list[str] = []
        for feed_url in self.feed_urls:
            problems: list[str] = []
            if await self._fetch_and_parse_feed(feed_url, problems=problems, read_pages=False) is None:
                unreadable.append(f"{redact_url(feed_url)} ({problems[0] if problems else 'no answer'})")
        if unreadable:
            raise ConnectorInitError(
                "These feed URLs could not be read as RSS or Atom feeds: "
                + "; ".join(unreadable)
                + ". Check that each URL opens a feed, or remove it."
            )
        self.logger.info(f"✅ All {len(self.feed_urls)} feed(s) are readable")
        return True

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

    async def create_record_group(self, feed_url: str, feed_title: str | None = None) -> None:
        """
        Create a record group for a specific feed URL.

        Args:
            feed_url: The RSS/Atom feed URL
            feed_title: The feed's own title; two feeds of one site would
                otherwise share the host name as their name.
        """
        try:
            parsed_url = urlparse(feed_url)
            record_group_name = (feed_title or "").strip() or parsed_url.netloc or feed_url

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
                inherit_permissions=True,
            )

            permissions = self._create_rss_permissions()

            await self.data_entities_processor.on_new_record_groups(
                [(record_group, permissions)]
            )
            self.logger.info(
                f"✅ Created record group '{record_group_name}' for feed"
            )

        except Exception as e:
            self.logger.error(
                f"❌ Failed to create record group for {redact_url(feed_url)}: {e}", exc_info=True
            )
            raise

    def full_sync_coverage(self) -> FullSyncCoverage:
        """A feed lists only its latest entries, so an entry a sync did not see has
        rolled off the feed, not been deleted, and keeps its record. A feed that
        could not be read keeps its records too. Feeds taken out of the settings
        are removed by the sync itself (_remove_unconfigured_feeds)."""
        return FullSyncCoverage(complete=False)

    async def run_sync(self) -> None:
        """Main sync method: parse feeds, crawl articles, and index content."""
        try:
            # Scheduled syncs reuse this instance, and feeds no longer in the
            # settings are deleted below, so the list must be the saved one.
            await self._load_config(use_cache=False)
            self.logger.info(f"🚀 Starting RSS sync for {len(self.feed_urls)} feed(s)")
            # The instance outlives a sync (scheduled syncs reuse it), so a set left
            # from the last run would skip every entry and no change would be indexed.
            self.processed_urls.clear()

            if self.scope == ConnectorScope.TEAM.value:
                await self.data_entities_processor.ensure_team_app_edge(
                    self.connector_id
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
            failed_feeds: dict[str, str] = {}
            for feed_url in self.feed_urls:
                try:
                    count = await self._process_feed(feed_url, app_users)
                    total_articles += count
                except FeedUnreadableError as e:
                    self.logger.warning(f"⚠️ Skipping feed {redact_url(feed_url)}: {e}")
                    failed_feeds[feed_url] = str(e)
                except Exception as e:
                    self.logger.error(
                        f"❌ Error processing feed {redact_url(feed_url)}: {e}", exc_info=True
                    )
                    failed_feeds[feed_url] = "it could not be saved"

            await self._remove_unconfigured_feeds()

            if failed_feeds:
                await self._notify_failed_feeds(failed_feeds)
            if failed_feeds and len(failed_feeds) == len(self.feed_urls):
                raise RuntimeError(f"None of the {len(self.feed_urls)} feed(s) could be read")

            self.logger.info(
                f"✅ RSS sync completed: {total_articles} articles processed across "
                f"{len(self.feed_urls) - len(failed_feeds)} of {len(self.feed_urls)} feed(s)"
            )

        except Exception as e:
            self.logger.error(f"❌ Error during RSS sync: {e}", exc_info=True)
            raise

    async def _remove_unconfigured_feeds(self) -> None:
        """Delete the feeds, with their articles, taken out of the Feed URLs setting.

        Saving that setting does not queue a full sync, so nothing else removes
        them. A failed read deletes nothing; the next sync tries again.
        """
        try:
            groups = await self.data_entities_processor.get_nodes_by_filters(
                CollectionNames.RECORD_GROUPS.value,
                {"connectorId": self.connector_id, "groupType": RecordGroupType.RSS_FEED.value},
                return_fields=["externalGroupId"],
                raise_on_error=True,
            )
        except Exception as e:
            self.logger.warning(f"⚠️ Not removing feeds taken out of the settings: they could not be read: {e}")
            return
        stored = {group.get("externalGroupId") for group in groups} - {None, ""}
        for feed_url in sorted(stored - set(self.feed_urls)):
            self.logger.info(f"🗑️ Removing feed {redact_url(feed_url)}, which is no longer in the settings")
            try:
                if not await self.data_entities_processor.on_record_group_deleted(feed_url, self.connector_id):
                    self.logger.warning(f"⚠️ Could not remove feed {redact_url(feed_url)}; retrying next sync")
            except Exception as e:
                self.logger.warning(
                    f"⚠️ Could not remove feed {redact_url(feed_url)}; retrying next sync: {e}", exc_info=True
                )

    async def _notify_failed_feeds(self, failed_feeds: dict[str, str]) -> None:
        """Tell the connector's owner which feeds were skipped; never fails the sync."""
        named = [f"{redact_url(url)} ({reason})" for url, reason in list(failed_feeds.items())[:MAX_FEEDS_NAMED]]
        more = len(failed_feeds) - len(named)
        every_feed = len(failed_feeds) == len(self.feed_urls)
        message = (
            f"{self.display_name}: {len(failed_feeds)} of {len(self.feed_urls)} feed(s) could not be read: "
            + "; ".join(named)
            + (f"; and {more} more" if more > 0 else "")
            + ". Articles already synced from them are kept."
        )
        try:
            await self.notify(
                type=NotificationType.CONNECTOR_SYNC_ERROR if every_feed
                else NotificationType.CONNECTOR_RECORD_GROUP_SYNC_ERROR,
                severity=NotificationSeverity.ERROR if every_feed else NotificationSeverity.WARNING,
                title=f"{self.display_name}: " + ("sync failed" if every_feed else "some feeds were skipped"),
                message=message,
            )
        except Exception as e:
            self.logger.warning(f"⚠️ Could not send the failed-feeds notification: {e}")

    async def _process_feed(self, feed_url: str, app_users: List[AppUser]) -> int:
        """
        Process a single RSS/Atom feed: parse entries, crawl articles, create records.

        Args:
            feed_url: The feed URL to process
            app_users: List of AppUser for permissions

        Returns:
            Number of articles processed

        Raises:
            FeedUnreadableError: the feed could not be fetched or parsed. Its
                stored records are left as they are.
        """
        self.logger.info(f"📡 Processing feed: {redact_url(feed_url)}")

        problems: list[str] = []
        feed = await self._fetch_and_parse_feed(feed_url, problems=problems)
        if feed is None:
            raise FeedUnreadableError(problems[0] if problems else "no answer")

        feed_title = feed.feed.get("title") or urlparse(feed_url).netloc
        await self.create_record_group(feed_url, feed_title)

        if not feed.entries:
            self.logger.info(f"📭 Feed '{feed_title}' has no entries")
            return 0

        self.logger.info(f"📰 Feed '{feed_title}' has {len(feed.entries)} entries")

        # Process entries in batches
        records_batch: List[Tuple[FileRecord, List[Permission]]] = []
        retitled: list[FileRecord] = []
        processed_count = 0

        for entry in feed.entries[: self.max_articles_per_feed]:
            try:
                result = await self._process_entry(entry, feed_url)
                if result:
                    records_batch.append(result)
                    processed_count += 1
                    if await self._only_metadata_changed(result[0]):
                        retitled.append(result[0])
            except Exception as e:
                entry_title = entry.get("title", "Unknown")
                self.logger.error(
                    f"❌ Error processing entry '{entry_title}': {e}", exc_info=True
                )
                continue

            # Outside the per-entry try: a failed write fails the feed instead of
            # being retried, with a growing batch, by every later entry.
            if len(records_batch) >= self.batch_size:
                await self.data_entities_processor.on_new_records(records_batch)
                self.logger.info(f"  📦 Flushed batch of {len(records_batch)} records")
                records_batch = []

        # Flush remaining records
        if records_batch:
            await self.data_entities_processor.on_new_records(records_batch)
            self.logger.info(
                f"  📦 Flushed final batch of {len(records_batch)} records"
            )

        # on_new_records rewrites an existing record only when its revision (the
        # text's hash) changes, so a new title or link on unchanged text is saved here.
        for record in retitled:
            try:
                await self.data_entities_processor.on_record_metadata_update(record)
            except Exception as e:
                self.logger.error(
                    f"❌ Error updating the title of '{record.record_name}': {e}", exc_info=True
                )

        self.logger.info(f"✅ Processed {processed_count} articles from '{feed_title}'")
        return processed_count

    async def _only_metadata_changed(self, record: FileRecord) -> bool:
        """True when the entry is already synced with the same text but a new title or link."""
        try:
            existing = await self.data_entities_processor.get_record_by_external_id(
                self.connector_id, record.external_record_id
            )
        except Exception as e:
            # The entry still goes to on_new_records; only its rename waits for the next sync.
            self.logger.warning(f"⚠️ Could not read the stored record for '{record.record_name}': {e}")
            return False
        if existing is None or existing.external_revision_id != record.external_revision_id:
            return False
        return existing.record_name != record.record_name or existing.weburl != record.weburl

    def _source_fetch_error(self, result: Optional[FetchResponse]) -> HTTPException:
        """Map a failed `fetch_url_with_fallback` result onto our HTTP status."""
        retry_after = getattr(result, "retry_after", None)
        return map_source_status(
            result.status_code if result else HttpStatusCode.BAD_GATEWAY.value,
            connector=self.display_name,
            retry_after=str(int(retry_after)) if retry_after else None,
        )

    async def _fetch_and_parse_feed(
        self,
        feed_url: str,
        *,
        raise_on_error: bool = False,
        problems: list[str] | None = None,
        read_pages: bool = True,
    ) -> feedparser.FeedParserDict | None:
        """Fetch and parse an RSS/Atom feed URL.

        Sync walks many feeds and must survive one bad feed, so failures return
        None unless *raise_on_error* — the streaming path needs the real status.
        A failure's reason is appended to *problems*, so a caller can tell the
        user why the feed was skipped. *read_pages* False reads only the first page
        of a paged feed.
        """
        def note(reason: str) -> None:
            if problems is not None:
                problems.append(reason)

        try:
            result = await fetch_url_with_fallback(
                url=feed_url, session=self.session, logger=self.logger, max_size_mb=MAX_FETCH_SIZE_MB
            )
            if result is None or result.status_code >= HttpStatusCode.BAD_REQUEST.value:
                status = result.status_code if result else "no response"
                self.logger.warning(f"⚠️ HTTP {status} fetching feed: {redact_url(feed_url)}")
                note(f"HTTP {status}" if result else "no response")
                if raise_on_error:
                    raise self._source_fetch_error(result)
                return None

            # Given bytes, feedparser first tries them as a file name on this host.
            feed = await asyncio.to_thread(feedparser.parse, BytesIO(result.content_bytes))

            if not feed.entries and (feed.bozo or not feed.version):
                reason = (
                    redact_urls_in_text(str(feed.bozo_exception))[:200]
                    if feed.bozo else "no RSS or Atom root element"
                )
                self.logger.warning(f"⚠️ Feed parse warning for {redact_url(feed_url)}: {reason}")
                note(f"not an RSS or Atom feed: {reason}")
                return None

            if read_pages:
                await self._read_next_pages(feed, feed_url)
            return feed

        except HTTPException:
            raise
        except asyncio.TimeoutError as e:
            self.logger.warning(f"⚠️ Timeout fetching feed: {redact_url(feed_url)}")
            note("timed out")
            if raise_on_error:
                raise to_stream_error(e, connector=self.display_name) from e
            return None
        except Exception as e:
            reason = redact_urls_in_text(str(e))[:200]
            self.logger.error(f"❌ Error fetching feed {redact_url(feed_url)}: {reason}", exc_info=True)
            note(f"request failed: {reason}")
            if raise_on_error:
                raise to_stream_error(e, connector=self.display_name) from e
            return None

    async def _read_next_pages(self, feed: feedparser.FeedParserDict, page_url: str) -> bool:
        """A paged feed (RFC 5005) links its older entries with rel="next"; add them to
        *feed* up to the article limit. A page that fails ends the walk, keeping what was read,
        and returns False."""
        seen = {page_url}
        page = feed
        for _ in range(MAX_FEED_PAGES - 1):
            links = page.feed.get("links")
            if len(feed.entries) >= self.max_articles_per_feed or not isinstance(links, list):
                return True
            href = next((link.get("href") for link in links if link.get("rel") == "next"), None)
            if not href:
                return True
            page_url = urljoin(page_url, href)
            if page_url in seen:
                return True
            seen.add(page_url)
            try:
                result = await fetch_url_with_fallback(
                    url=page_url, session=self.session, logger=self.logger, max_size_mb=MAX_FETCH_SIZE_MB
                )
                if result is None or result.status_code >= HttpStatusCode.BAD_REQUEST.value:
                    status = result.status_code if result else "no response"
                    self.logger.warning(f"⚠️ HTTP {status} reading the next page of a feed: {redact_url(page_url)}")
                    return False
                page = await asyncio.to_thread(feedparser.parse, BytesIO(result.content_bytes))
            except Exception as e:
                self.logger.warning(
                    f"⚠️ Could not read the next page of a feed {redact_url(page_url)}: "
                    f"{redact_urls_in_text(str(e))[:200]}"
                )
                return False
            # An empty page can still link to a next one; the loop's own checks end the walk.
            feed.entries.extend(page.entries)
        return True

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
        if not self._entry_guid(entry):
            return None
        # Many entries can share one link (a podcast's show page), so the id is the key.
        entry_key = self._entry_external_id(entry, feed_url)
        if entry_key in self.processed_urls:
            return None
        self.processed_urls.add(entry_key)
        article_url = self._article_link(entry)

        # Extract metadata from feed entry
        title = entry.get("title") or self._extract_title_from_url(article_url)
        guid, stored = await self._stored_entry(entry, feed_url)

        # Parse timestamps
        source_created_at = self._parse_feed_timestamp(
            entry.get("published_parsed") or entry.get("updated_parsed")
        )
        source_updated_at = self._parse_feed_timestamp(
            entry.get("updated_parsed") or entry.get("published_parsed")
        )
        timestamp = get_epoch_timestamp_in_ms()

        try:
            # A synced article whose page does not load this time keeps its stored
            # text; only a new one falls back to the feed's summary.
            content_text = await self._resolve_entry_text(
                entry, article_url, raise_on_error=stored is not None
            )
        except HTTPException as e:
            self.logger.warning(
                f"⚠️ Keeping the stored copy of '{title}': its article {redact_url(article_url)} "
                f"did not load ({e.status_code})"
            )
            return None

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
            source_updated_at=source_updated_at or timestamp,
            weburl=article_url or feed_url,
            size_in_bytes=len(content_bytes),
            is_file=True,
            extension=ExtensionTypes.TXT.value,
            path=urlparse(article_url).path or "/" if article_url else "/",
            mime_type=MimeTypes.PLAIN_TEXT.value,
            md5_hash=content_md5_hash,
            preview_renderable=True,
        )
        if self.indexing_filters.is_enabled(IndexingFilterKey.ENABLE_MANUAL_SYNC, default=False):
            file_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

        permissions = self._create_rss_permissions()

        return file_record, permissions

    @staticmethod
    def _article_link(entry: dict) -> str:
        """The entry's web link; an Atom entry without one gets its id (a URN) as its link."""
        link = entry.get("link") or ""
        return link if link.startswith(("http://", "https://")) else ""

    @staticmethod
    def _entry_guid(entry: dict) -> str:
        return entry.get("id") or entry.get("link") or ""

    def _entry_external_id(self, entry: dict, feed_url: str) -> str:
        """The entry's record id. An RSS guid need only be unique within its feed
        ("1", "post-42"), so one that is not a URL or URN is prefixed with the feed
        URL; otherwise two feeds' entries would share, and overwrite, one record."""
        guid = self._entry_guid(entry)
        if "://" in guid or guid.lower().startswith(("urn:", "tag:")):
            return guid
        return f"{feed_url}#{guid}"

    async def _stored_entry(self, entry: dict, feed_url: str) -> tuple[str, Record | None]:
        """The entry's record id and its stored record, if any.

        Records synced before ids were prefixed carry the bare guid; one of this
        feed keeps that id, so the entry is not synced a second time.
        """
        external_id = self._entry_external_id(entry, feed_url)
        stored = await self.data_entities_processor.get_record_by_external_id(
            self.connector_id, external_id
        )
        guid = self._entry_guid(entry)
        if stored is None and external_id != guid:
            legacy = await self.data_entities_processor.get_record_by_external_id(
                self.connector_id, guid
            )
            if legacy is not None and legacy.external_record_group_id == feed_url:
                return guid, legacy
        return external_id, stored

    async def _fetch_article_content(self, url: str, *, raise_on_error: bool = False) -> str:
        """
        Fetch the full article page and extract clean text content.

        Args:
            url: Article URL to crawl
            raise_on_error: Surface the source's status instead of returning "".
                Sync treats a failed crawl as "no extra content"; streaming must
                not report a blocked page as an empty document.

        Returns:
            Cleaned text content or empty string on failure
        """
        try:
            result = await fetch_url_with_fallback(
                url=url, session=self.session, logger=self.logger, max_size_mb=MAX_FETCH_SIZE_MB
            )
            if result is None or result.status_code >= HttpStatusCode.BAD_REQUEST.value:
                status = result.status_code if result else "no response"
                self.logger.debug(f"⚠️ HTTP {status} for article: {redact_url(url)}")
                if raise_on_error:
                    raise self._source_fetch_error(result)
                return ""

            # Header casing varies across fetch strategies (aiohttp preserves case,
            # curl_cffi/cloudscraper do dict(resp.headers)) — look up case-insensitively.
            content_type = ""
            for key, value in result.headers.items():
                if key.lower() == "content-type":
                    content_type = value.lower()
                    break
            if "html" not in content_type and "xml" not in content_type:
                self.logger.debug(
                    f"⚠️ Non-HTML content type for {redact_url(url)}: {content_type}"
                )
                if raise_on_error:
                    # Same reason as an empty extraction below: no summary under the article's revision.
                    raise self._source_fetch_error(None)
                return ""

            # Extraction is CPU-bound and slow on a large page; it would stall every other sync.
            text = await asyncio.to_thread(self._extract_text_content, result.content_bytes)
            if not text and raise_on_error:
                # Falling back to the summary here would re-index a synced article as its
                # summary, and back again once the page reads normally. Retryable, as
                # a page that renders by script or answers oddly may read next time.
                raise self._source_fetch_error(None)
            return text

        except HTTPException:
            raise
        except asyncio.TimeoutError as e:
            self.logger.debug(f"⚠️ Timeout fetching article: {redact_url(url)}")
            if raise_on_error:
                raise to_stream_error(e, connector=self.display_name) from e
            return ""
        except Exception as e:
            self.logger.debug(f"⚠️ Error fetching article {redact_url(url)}: {redact_urls_in_text(str(e))[:200]}")
            if raise_on_error:
                raise to_stream_error(e, connector=self.display_name) from e
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

    def _html_to_text(self, html: Union[str, bytes]) -> str:
        """Convert author-provided feed HTML to plain text with a light tag-strip.
        Feed content is already clean, so it does not need trafilatura's page
        boilerplate extraction — only tag removal."""
        if not html:
            return ""
        try:
            if isinstance(html, bytes):
                html = html.decode("utf-8", errors="replace")
            return BeautifulSoup(html, "html.parser").get_text(separator="\n").strip()
        except Exception:
            return ""

    async def _resolve_entry_text(
        self, entry: dict, article_url: str, *, raise_on_error: bool = False, crawl: bool = True
    ) -> str:
        """Resolve a feed entry's content as plain text.

        Shared by sync (_process_entry) and index (stream_record) so both produce
        identical content. Crawls the article page only when fetch_full_content is
        enabled, or as a last resort when the feed carries no usable content.
        With *raise_on_error* a crawl that fails raises instead of falling back
        to the feed's text; *crawl* False never opens the article page.
        """
        content_text = ""

        # 1. Full-content crawl only when explicitly enabled
        if self.fetch_full_content and crawl and article_url:
            content_text = await self._fetch_article_content(article_url, raise_on_error=raise_on_error)

        # 2. Feed-provided content (no crawl): entry.content, then summary
        if not content_text:
            entry_content = entry.get("content")
            if entry_content and isinstance(entry_content, list) and len(entry_content) > 0:
                value = entry_content[0].get("value", "")
                if value:
                    content_text = await asyncio.to_thread(self._html_to_text, value)

        if not content_text:
            summary = entry.get("summary", "")
            if summary:
                summary_type = entry.get("summary_detail", {}).get("type", "")
                if summary_type in ("text/html", "application/xhtml+xml"):
                    content_text = await asyncio.to_thread(self._html_to_text, summary)
                else:
                    content_text = summary

        # 3. Last-resort crawl only if the feed carried nothing and we didn't already
        if not content_text and not self.fetch_full_content and article_url and crawl:
            content_text = await self._fetch_article_content(article_url, raise_on_error=raise_on_error)

        # 4. Final fallback → title
        if not content_text:
            content_text = entry.get("title") or self._extract_title_from_url(article_url)

        return content_text

    async def stream_record(self, record: Record) -> Optional[StreamingResponse]:
        """Serve the record content as plain text.

        Reproduces the sync-time resolution: re-fetch the feed, locate this entry by
        guid, and run the shared resolver. Falls back to a direct page crawl only if
        the entry has aged out of the feed. No article-page crawl is needed for the
        default (feed-content) path.
        """
        if not self.session:
            raise connector_not_ready(self.display_name)

        article_url = record.weburl
        feed_url = record.external_record_group_id
        guid = record.external_record_id

        try:
            content_text = ""
            # The feed and the article page are two chances at the same content,
            # so a failure on either is only fatal if the other yields nothing.
            # Held here so the user gets that status instead of a bare 404.
            source_error: Optional[HTTPException] = None

            # Preferred path: reproduce sync from the feed entry
            if feed_url:
                feed = None
                try:
                    feed = await self._fetch_and_parse_feed(feed_url, raise_on_error=True, read_pages=False)
                except HTTPException as exc:
                    source_error = exc

                def entry_by_id() -> dict | None:
                    for candidate in feed.entries:
                        # The bare guid matches records synced before ids were prefixed.
                        if guid and guid in (
                            self._entry_external_id(candidate, feed_url),
                            self._entry_guid(candidate),
                        ):
                            return candidate
                    return None

                if feed:
                    entry = entry_by_id()
                    if entry is None:
                        # An empty first page can still link to the page sync took this entry from.
                        pages_read = await self._read_next_pages(feed, feed_url)
                        entry = entry_by_id()
                        if entry is None and not pages_read:
                            # The entry may be on the page that failed, and a crawl of its link
                            # could serve other text under its revision; let indexing retry.
                            raise self._source_fetch_error(None)
                    if entry is not None:
                        entry_url = self._article_link(entry)
                        try:
                            content_text = await self._resolve_entry_text(
                                entry, entry_url, raise_on_error=True
                            )
                        except HTTPException:
                            # The feed's own text stands in only if the sync stored that
                            # too; otherwise indexing would keep the summary under the
                            # article's revision and never fetch the article again.
                            fallback = await self._resolve_entry_text(entry, entry_url, crawl=False)
                            if hashlib.md5(fallback.encode("utf-8")).hexdigest() != record.external_revision_id:
                                raise
                            content_text = fallback

            # Fallback: entry aged out of the feed → crawl the article page directly.
            # An entry without a web link was stored with the feed's URL instead, and
            # a link other entries still have (a podcast's show page) is not this entry's page.
            shared_link = feed_url and feed and any(
                self._article_link(c) == article_url for c in feed.entries
            )
            if not content_text and article_url and article_url != feed_url and not shared_link:
                try:
                    content_text = await self._fetch_article_content(
                        article_url, raise_on_error=True
                    )
                except HTTPException as exc:
                    source_error = exc

            # Every feed entry carries at least a title, so empty content here means
            # both the feed fetch and the article crawl failed. Fail loud so indexing
            # retries (FAILED) instead of marking the record terminally EMPTY.
            if not content_text:
                raise source_error or not_found_at_source(self.display_name)

            content_bytes = content_text.encode("utf-8")
            return create_stream_record_response(
                BytesIO(content_bytes),
                filename=record.record_name,
                mime_type=record.mime_type or MimeTypes.PLAIN_TEXT.value,
                fallback_filename=f"record_{record.id}",
            )

        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(f"❌ Error streaming record {record.id}: {e}", exc_info=True)
            raise

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
        scope: str,
        created_by: str,
        data_entities_processor,
        **kwargs,
    ) -> BaseConnector:
        """Factory method to create an RSSConnector instance."""
        return RSSConnector(
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )

    async def cleanup(self) -> None:
        """Cleanup resources."""
        if self.session:
            await self.session.close()
            self.session = None
        self.processed_urls.clear()
        self.logger.info("✅ RSS connector cleanup completed")

    async def reindex_records(self, record_results: list[Record]) -> None:
        """Re-queue the records for indexing, unchanged ones included; indexing
        streams each one again, so it reads the feed and article as they are now."""
        if not record_results:
            return
        await self.data_entities_processor.reindex_existing_records(record_results)

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

    async def get_signed_url(self, record: Record) -> str | None:
        """No signed URL: content is streamed from the feed and article page."""
        return None
