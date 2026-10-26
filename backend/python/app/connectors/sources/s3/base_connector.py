"""
Base connector for S3-compatible storage systems (AWS S3, MinIO, etc.)

This module provides shared functionality for connectors that work with S3-compatible
object storage systems. The base classes and utilities here are used by both
S3Connector and MinIOConnector to avoid code duplication.
"""

import asyncio
import mimetypes
import uuid
from abc import abstractmethod
from collections.abc import Callable, Container, Iterable
from contextlib import AbstractAsyncContextManager, nullcontext
from datetime import datetime, timezone
from logging import Logger
from typing import Any
from urllib.parse import quote

from aiobotocore.config import AioConfig
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    CollectionNames,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
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
    not_downloadable,
    not_found_at_source,
    to_stream_error,
)
from app.connectors.core.base.sync_point.sync_point import (
    FailedItems,
    SyncDataPointType,
    SyncPoint,
    generate_record_sync_point_key,
    listing_checkpoint,
)
from app.connectors.core.interfaces.connector.apps import App
from app.connectors.core.registry.connector_builder import ConnectorScope
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOption,
    FilterOptionsResponse,
    IndexingFilterKey,
    SyncFilterKey,
    extension_passes_filter,
    included_names,
    load_connector_filters,
    name_passes_filter,
)
from app.connectors.core.registry.folder_scope import (
    FolderScope,
    clean_up_scope,
    listed_record_ids,
    path_in_container,
    recorded_ids,
    remove_container,
    remove_deselected_containers,
    remove_records_not_listed,
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
from app.services.notification.types import (
    NotificationSeverity,
    NotificationType,
)
from app.sources.client.s3.s3 import S3Response
from app.utils.streaming import create_stream_record_response, stream_content
from app.utils.time_conversion import datetime_to_epoch_ms, get_epoch_timestamp_in_ms


def _bucket_creation_dates_from_list_buckets(
    api_buckets: list[dict] | None, target_names: set[str]
) -> dict[str, int]:
    """Map bucket name -> creation time (epoch ms) from ListBuckets response.

    AWS S3 and S3-compatible APIs include CreationDate per bucket. Names not in
    target_names or without a parseable date are omitted.
    """
    out: dict[str, int] = {}
    if not api_buckets or not target_names:
        return out
    for b in api_buckets:
        name = b.get("Name")
        if not name or name not in target_names:
            continue
        cd = b.get("CreationDate")
        ts = datetime_to_epoch_ms(cd) if cd else None
        if ts is not None:
            out[name] = ts
    return out

# Default connector endpoint for signed URL generation
DEFAULT_CONNECTOR_ENDPOINT = "http://localhost:8000"
# The only caller fetches the URL at once on the server; S3 checks expiry when the GET starts.
PRESIGNED_URL_EXPIRY_SECONDS = 300


def _s3_last_modified_to_epoch_ms(last_modified: Any) -> int:
    """Map S3 LastModified (datetime or ISO string from some S3-compatible APIs) to epoch ms."""
    parsed = datetime_to_epoch_ms(last_modified)
    if parsed is not None:
        return parsed
    return get_epoch_timestamp_in_ms()


def get_file_extension(key: str) -> str | None:
    """Extracts the extension from an S3 key."""
    if "." in key:
        parts = key.split(".")
        if len(parts) > 1:
            return parts[-1].lower()
    return None


def get_parent_path_from_key(key: str) -> str | None:
    """Extracts the parent path from an S3 key (without leading slash).

    For a key like 'a/b/c/file.txt', returns 'a/b/c'
    For a key like 'a/b/c/', returns 'a/b'
    """
    if not key:
        return None
    # Remove leading slash and trailing slash (if present)
    normalized_key = key.lstrip("/").rstrip("/")
    if not normalized_key or "/" not in normalized_key:
        return None
    parent_path = "/".join(normalized_key.split("/")[:-1])
    return parent_path if parent_path else None


def get_folder_path_segments_from_key(key: str) -> list[str]:
    """Derives folder path segments from an S3 key for hierarchy creation.

    S3 list_objects only returns object keys (files); there are no separate folder objects.
    For each file key (e.g. a/b/c/file.txt), returns the folder path segments that must exist:
    e.g. ['a', 'a/b', 'a/b/c']. For a root-level key (e.g. file.txt) returns [].

    Args:
        key: S3 object key (may have leading/trailing slashes)

    Returns:
        List of cumulative path segments (folders only), root to leaf, no duplicates.
    """
    if not key:
        return []
    normalized = key.lstrip("/").rstrip("/")
    if not normalized or "/" not in normalized:
        return []
    parts = normalized.split("/")
    # Last part is the file (or folder key); segments are the folder path prefix
    return ["/".join(parts[:i]) for i in range(1, len(parts))]


def get_parent_weburl_for_s3(parent_external_id: str, base_console_url: str = "https://s3.console.aws.amazon.com") -> str:
    """Generate webUrl for an S3/MinIO directory based on parent external_id.

    Args:
        parent_external_id: External ID in format "bucket_name/path" or just "bucket_name"
        base_console_url: Base URL for the console (different for S3 vs MinIO)

    Returns:
        Console URL for the directory
    """
    if "/" in parent_external_id:
        parts = parent_external_id.split("/", 1)
        bucket_name = parts[0]
        path = parts[1]
        path = path.lstrip("/")
        if path and not path.endswith("/"):
            path = path + "/"
        return f"{base_console_url}/s3/object/{bucket_name}?prefix={quote(path, safe='/')}"
    else:
        bucket_name = parent_external_id
        return f"{base_console_url}/s3/buckets/{bucket_name}"


def get_parent_path_for_s3(parent_external_id: str) -> str | None:
    """Extract directory path from S3 parent external_id.

    Args:
        parent_external_id: External ID in format "bucket_name/path" or just "bucket_name"

    Returns:
        Directory path without bucket name prefix, or None for root directories
    """
    if "/" in parent_external_id:
        parts = parent_external_id.split("/", 1)
        directory_path = parts[1]
        if directory_path and not directory_path.endswith("/"):
            directory_path = directory_path + "/"
        return directory_path
    else:
        return None


def parse_parent_external_id(parent_external_id: str) -> tuple[str, str | None]:
    """Parse parent_external_id to extract bucket_name and normalized path.

    This helper method extracts the common parsing logic for parent_external_id
    used by both S3 and MinIO connectors to generate parent web URLs.

    Args:
        parent_external_id: External ID in format "bucket_name/path" or just "bucket_name"

    Returns:
        A tuple of (bucket_name, normalized_path) where normalized_path is None
        if parent_external_id contains only a bucket name, otherwise it's the
        path normalized (leading slashes removed, trailing slash added if not present).
    """
    if "/" in parent_external_id:
        parts = parent_external_id.split("/", 1)
        bucket_name = parts[0]
        path = parts[1]
        path = path.lstrip("/")
        if path and not path.endswith("/"):
            path = path + "/"
        return bucket_name, path
    else:
        bucket_name = parent_external_id
        return bucket_name, None


def make_s3_composite_revision(bucket_name: str, normalized_key: str, raw_etag: str | None) -> str:
    """Build external_revision_id for S3. Uses bucket/etag so move/rename can be detected
    (same etag in same bucket). When etag is missing, falls back to bucket/key for uniqueness."""
    if raw_etag:
        return f"{bucket_name}/{raw_etag}"
    return f"{bucket_name}/{normalized_key}|"


def get_mimetype_for_s3(key: str, *, is_folder: bool = False) -> str:
    """Determines the correct MimeTypes string value for an S3 object."""
    if is_folder:
        return MimeTypes.FOLDER.value

    mime_type_str, _ = mimetypes.guess_type(key)
    if mime_type_str:
        try:
            return MimeTypes(mime_type_str).value
        except ValueError:
            return MimeTypes.BIN.value
    return MimeTypes.BIN.value


class _BucketGone(Exception):
    """The store answered NoSuchBucket: the bucket was deleted at the source."""


class _ObjectGone(Exception):
    """The store answered 404 for the object: it is no longer at its key."""


class S3CompatibleDataSourceEntitiesProcessor(DataSourceEntitiesProcessor):
    """S3-compatible processor that extends the base processor with S3-specific placeholder record logic."""

    def __init__(
        self,
        logger: Logger,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        base_console_url: str = "https://s3.console.aws.amazon.com",
        parent_url_generator: Callable[[str], str] | None = None,
    ) -> None:
        super().__init__(logger, data_store_provider, config_service)
        self.base_console_url = base_console_url
        # Default to S3 format if no generator provided (for backward compatibility)
        self.parent_url_generator = parent_url_generator or (
            lambda parent_external_id: get_parent_weburl_for_s3(parent_external_id, base_console_url)
        )


class S3CompatibleBaseConnector(BaseConnector):
    """
    Base connector for S3-compatible storage systems.

    This abstract base class provides common functionality for syncing data from
    S3-compatible object storage (AWS S3, MinIO, etc.). Subclasses must implement
    the abstract methods to provide storage-specific behavior.
    """

    # Keys per ListObjectsV2 page, the most S3 returns; batch_size stays the graph write size.
    list_page_size = 1000
    # Buckets synced at once; botocore's default pool of 10 connections covers them.
    bucket_concurrency = 4

    def __init__(
        self,
        app: App,
        logger: Logger,
        data_entities_processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        config_service: ConfigurationService,
        connector_id: str,
        scope: str,
        created_by: str,
        connector_name: str,
        filter_key: str,
        base_console_url: str = "https://s3.console.aws.amazon.com",
    ) -> None:
        super().__init__(
            app,
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by
        )

        self.connector_name = connector_name
        self.connector_id = connector_id
        self.filter_key = filter_key
        self.base_console_url = base_console_url

        # Initialize sync point for tracking record changes
        def _create_sync_point(sync_data_point_type: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_id=self.connector_id,
                org_id=self.data_entities_processor.org_id,
                sync_data_point_type=sync_data_point_type,
                data_store_provider=self.data_store_provider,
            )

        self.record_sync_point = _create_sync_point(SyncDataPointType.RECORDS)

        self.data_source: Any | None = None  # Will be S3DataSource or MinIODataSource
        self.batch_size = 100
        # Per bucket, records moved during this listing, by id, to the external id they moved from:
        # two new copies of a deleted object must not both take its record before the
        # batch naming it is saved, and the batch saves each move in place.
        self._moved_record_ids: dict[str, dict[str, str]] = {}
        self.bucket_name: str | None = None
        self.region: str | None = None
        self.bucket_regions: dict[str, str] = {}  # Cache for bucket-to-region mapping
        # Folder records already upserted during the current sync run, keyed by
        # "{bucket}/{segment}". Cleared at the start of every run_sync so a
        # folder removed between runs is still re-created.
        self._ensured_folders: set[str] = set()
        # Personal scope's creator, looked up once per run; a failed lookup is never kept.
        self._creator: User | None = None
        # Set when a bucket of this full sync was not listed to the end; asyncio runs the buckets on one thread.
        self._skipped_part_of_a_bucket = False
        # Per run, for one notification at its end: buckets the key may not list, buckets
        # whose listing failed at the source, and buckets whose sync failed here, with why.
        self._unreadable_buckets: set[str] = set()
        self._unlisted_buckets: dict[str, str] = {}
        self._failed_buckets: dict[str, str] = {}
        # Bucket groups written in this full sync, after the bucket's first listed page, so a
        # bucket the key cannot read gets no empty group; None in an incremental run, which
        # leaves groups as they are.
        self._bucket_groups_written: set[str] | None = None
        self._bucket_creation_ms: dict[str, int] = {}

        # Initialize filter collections
        self.sync_filters: FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()

    def get_app_users(self, users: list[User]) -> list[AppUser]:
        """Convert User objects to AppUser objects for S3-compatible connectors."""
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

    @abstractmethod
    async def init(self) -> bool:
        """Initialize the connector. Must be implemented by subclasses."""
        pass

    @abstractmethod
    async def _build_data_source(self) -> object:
        """Build the data source (S3DataSource or MinIODataSource). Must be implemented by subclasses."""
        pass

    @abstractmethod
    def _generate_web_url(self, bucket_name: str, normalized_key: str) -> str:
        """Generate the web URL for an object. Must be implemented by subclasses."""
        pass

    @abstractmethod
    def _generate_parent_web_url(self, parent_external_id: str) -> str:
        """Generate the web URL for a parent folder/directory. Must be implemented by subclasses."""
        pass

    async def run_sync(self) -> None:
        """Runs a full synchronization from buckets."""
        async with self._shared_client():
            await self._run_full_sync()

    async def _run_full_sync(self) -> None:
        try:
            self.logger.info(f"Starting {self.connector_name} full sync.")

            if not self.data_source:
                raise ConnectionError(f"{self.connector_name} connector is not initialized.")

            self._start_run(full=True)

            # Reload sync and indexing filters to pick up configuration changes
            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, self.filter_key, self.connector_id, self.logger
            )

            if self.scope == ConnectorScope.TEAM.value:
                await self.data_entities_processor.ensure_team_app_edge(
                    self.connector_id
                )
            else:
                creator = await self._creator_or_raise()
                await self.data_entities_processor.on_new_app_users(self.get_app_users([creator]))

            # Get sync filters
            sync_filters = self.sync_filters if hasattr(self, 'sync_filters') and self.sync_filters else FilterCollection()

            # Get bucket filter if specified
            selected_buckets = included_names(sync_filters, "buckets")
            if not self.bucket_name:
                await remove_deselected_containers(
                    self.data_entities_processor, self.config_service, self.connector_id, "buckets", sync_filters, self.logger
                )

            # List all buckets or use configured bucket
            buckets_to_sync: list[str] = []
            list_buckets_payload: list[dict] | None = None
            if self.bucket_name:
                buckets_to_sync = [self.bucket_name]
                self.logger.info(f"Using configured bucket: {self.bucket_name}")
            elif selected_buckets:
                buckets_to_sync = selected_buckets
                self.logger.info(f"Using filtered buckets: {buckets_to_sync}")
            else:
                self.logger.info("Listing all buckets...")
                list_buckets_payload = await self._list_buckets_or_raise()
                buckets_to_sync = await self._unfiltered_bucket_names(list_buckets_payload, sync_filters)
                self.logger.info(f"Found {len(buckets_to_sync)} bucket(s) to sync")

            target_names = {n for n in buckets_to_sync if n}
            bucket_creation_ms = _bucket_creation_dates_from_list_buckets(
                list_buckets_payload, target_names
            )
            # Single-bucket or filter mode: reuse ListBuckets to resolve CreationDate
            if not list_buckets_payload and target_names:
                try:
                    extra = await self._list_all_buckets()
                    if extra.success:
                        bucket_creation_ms = _bucket_creation_dates_from_list_buckets(
                            extra.data["Buckets"], target_names
                        )
                except Exception as e:
                    self.logger.warning(
                        f"Failed to fetch bucket creation dates via list_buckets: {e}"
                    )

            # Fetch and cache regions for all buckets
            self.logger.info(f"Fetching regions for {len(buckets_to_sync)} bucket(s)...")
            for bucket_name in buckets_to_sync:
                if bucket_name:
                    await self._get_bucket_region(bucket_name)

            self._bucket_creation_ms = bucket_creation_ms
            await self._sync_buckets(buckets_to_sync)
            await self._report_bucket_problems(chosen=bool(self.bucket_name or selected_buckets))

            self.logger.info(f"{self.connector_name} full sync completed.")
        except Exception as ex:
            self.logger.error(f"❌ Error in {self.connector_name} connector run: {ex}", exc_info=True)
            raise

    def _start_run(self, *, full: bool) -> None:
        """Reset what one run collects."""
        # Per run, so folder edges are still rebuilt on every full sync; the memo only removes repeats within one run.
        self._ensured_folders.clear()
        self._creator = None
        self._skipped_part_of_a_bucket = False
        self._unreadable_buckets = set()
        self._unlisted_buckets = {}
        self._failed_buckets = {}
        self._bucket_groups_written = set() if full else None
        self._bucket_creation_ms = {}

    async def _ensure_bucket_group(self, bucket_name: str) -> None:
        """Write the bucket's group once per full sync, so its edges are re-created."""
        if self._bucket_groups_written is None or bucket_name in self._bucket_groups_written:
            return
        await self._create_record_groups_for_buckets([bucket_name], self._bucket_creation_ms)
        self._bucket_groups_written.add(bucket_name)

    async def _report_bucket_problems(self, *, chosen: bool) -> None:
        """One notification per kind of bucket problem this run had, then fail the run if a sync failed here.

        A bucket the source refused or failed to list is skipped and the run goes on: its
        records are kept. ``chosen`` says the user picked the buckets, so an unreadable one
        is an error; otherwise it is a bucket the key can list but was never given.
        """
        def names(buckets: Iterable[str]) -> str:
            listed = sorted(buckets)
            shown = ", ".join(listed[:10])
            return f"{shown} and {len(listed) - 10} more" if len(listed) > 10 else shown

        if self._unreadable_buckets:
            hint = "" if chosen else ", or choose the buckets to sync in the Bucket Names filter"
            await self.notify(
                type=NotificationType.CONNECTOR_AUTH_ERROR,
                severity=NotificationSeverity.ERROR if chosen else NotificationSeverity.WARNING,
                title=self._notification_title("Buckets skipped: access denied"),
                message=f"{len(self._unreadable_buckets)} bucket(s) could not be read and were skipped: "
                f"{names(self._unreadable_buckets)}. Give the key s3:ListBucket and s3:GetObject on them{hint}.",
            )
        if self._unlisted_buckets:
            first = sorted(self._unlisted_buckets)[0]
            await self.notify(
                type=NotificationType.CONNECTOR_SYNC_ERROR,
                severity=NotificationSeverity.ERROR,
                title=self._notification_title("Some buckets could not be listed"),
                message=f"Could not list {len(self._unlisted_buckets)} bucket(s): {names(self._unlisted_buckets)} "
                f"({first}: {self._unlisted_buckets[first]}). Their files are kept and the next sync retries them.",
            )
        if self._failed_buckets:
            await self.notify(
                type=NotificationType.CONNECTOR_SYNC_ERROR,
                severity=NotificationSeverity.ERROR,
                title=self._notification_title("Some buckets failed to sync"),
                message=f"{len(self._failed_buckets)} bucket(s) failed to sync: {names(self._failed_buckets)}. "
                "Their files are kept and the next sync retries them.",
            )
            # Records may not have been saved, so the run must not show as a success.
            raise RuntimeError(f"{self.connector_name.value} sync failed for bucket(s): {names(self._failed_buckets)}")

    def _paced(self) -> AbstractAsyncContextManager[None]:
        """One request's share of the declared rate limit, shared by every bucket of the connector."""
        return self.resilience.guard() if self.resilience else nullcontext()

    def _with_retry_budget(self, client: Any) -> Any:  # noqa: ANN401 - S3Client or MinIOClient
        """Give botocore the declared retry budget: it retries throttling (503 SlowDown) and 5xx itself."""
        if self.resilience:
            client.get_session().set_default_client_config(
                AioConfig(retries={"mode": "standard", "total_max_attempts": self.resilience.max_retries + 1})
            )
        return client

    def _shared_client(self) -> AbstractAsyncContextManager[object]:
        """One data-source client for the whole run, where the data source offers it."""
        shared_client = getattr(self.data_source, "shared_client", None)
        return shared_client() if shared_client else nullcontext()

    async def _sync_buckets(self, bucket_names: list[str]) -> None:
        """Sync the buckets a few at a time; one failed bucket does not stop the others."""
        semaphore = asyncio.Semaphore(self.bucket_concurrency)

        async def sync_one(bucket_name: str) -> None:
            async with semaphore:
                try:
                    self.logger.info(f"Syncing bucket: {bucket_name}")
                    await self._sync_bucket(bucket_name)
                except Exception as e:
                    self._skipped_part_of_a_bucket = True
                    self._failed_buckets[bucket_name] = str(e)
                    self.logger.error(f"Error syncing bucket {bucket_name}: {e}", exc_info=True)

        async with asyncio.TaskGroup() as tasks:
            for bucket_name in bucket_names:
                if bucket_name:
                    tasks.create_task(sync_one(bucket_name))

    async def _create_record_groups_for_buckets(
        self,
        bucket_names: list[str],
        bucket_creation_epoch_ms: dict[str, int] | None = None,
    ) -> None:
        """Create or upsert record groups for buckets with appropriate permissions.
        Always processes all buckets so that edges (e.g. recordGroup->app) are
        re-created after full sync when only edges were deleted.

        Args:
            bucket_names: Bucket names to sync.
            bucket_creation_epoch_ms: Optional map of bucket name -> AWS CreationDate
                as epoch ms (from ListBuckets). Used for source_created_at / source_updated_at
                so the knowledge hub shows real dates. S3 does not expose bucket last-modified
                in ListBuckets; we use creation time for both.
        """
        if not bucket_names:
            return

        creation_map = bucket_creation_epoch_ms or {}
        record_groups = []
        for bucket_name in bucket_names:
            if not bucket_name:
                continue
            creation_ms = creation_map.get(bucket_name)
            record_group = RecordGroup(
                name=bucket_name,
                external_group_id=bucket_name,
                group_type=RecordGroupType.BUCKET,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                description=f"{self.connector_name.value} Bucket: {bucket_name}",
                web_url=self._generate_parent_web_url(bucket_name),
                source_created_at=creation_ms,
                source_updated_at=creation_ms,
                inherit_permissions=True,
            )
            record_groups.append((record_group, []))

        if record_groups:
            await self.data_entities_processor.on_new_record_groups(record_groups)
            self.logger.info(f"Created {len(record_groups)} record group(s) for buckets")

    def _get_date_filters(self) -> tuple[int | None, int | None]:
        """The modified date bounds from sync_filters; S3 lists no creation time, so there is no created filter."""
        modified_after_ms: int | None = None
        modified_before_ms: int | None = None

        modified_date_filter = self.sync_filters.get(SyncFilterKey.MODIFIED)
        if modified_date_filter and not modified_date_filter.is_empty():
            after_iso, before_iso = modified_date_filter.get_datetime_iso()
            if after_iso:
                after_dt = datetime.fromisoformat(after_iso).replace(tzinfo=timezone.utc)
                modified_after_ms = int(after_dt.timestamp() * 1000)
                self.logger.info(f"Applying modified date filter: after {after_dt}")
            if before_iso:
                before_dt = datetime.fromisoformat(before_iso).replace(tzinfo=timezone.utc)
                modified_before_ms = int(before_dt.timestamp() * 1000)
                self.logger.info(f"Applying modified date filter: before {before_dt}")

        return modified_after_ms, modified_before_ms

    def _pass_date_filters(
        self,
        obj: dict,
        modified_after_ms: int | None = None,
        modified_before_ms: int | None = None,
    ) -> bool:
        """Returns True if S3 object PASSES date filters (should be kept)."""
        key = obj.get("Key", "")
        is_folder = key.endswith("/")
        if is_folder:
            return True

        if not any([modified_after_ms, modified_before_ms]):
            return True

        last_modified = obj.get("LastModified")
        if not last_modified:
            return True

        obj_timestamp_ms = datetime_to_epoch_ms(last_modified)
        if obj_timestamp_ms is None:
            return True

        if modified_after_ms and obj_timestamp_ms < modified_after_ms:
            self.logger.debug(f"Skipping {key}: modified {obj_timestamp_ms} before cutoff {modified_after_ms}")
            return False
        if modified_before_ms and obj_timestamp_ms > modified_before_ms:
            self.logger.debug(f"Skipping {key}: modified {obj_timestamp_ms} after cutoff {modified_before_ms}")
            return False

        return True

    async def _list_all_buckets(self) -> S3Response:
        """Every bucket the key can list, a page at a time, keeping the region each page reports.

        AWS refuses an unpaged ListBuckets for accounts allowed more than 10,000 buckets.
        A server that ignores the paging arguments answers with the full list and no token.
        """
        buckets: list[dict] = []
        token = None
        while True:
            async with self._paced():
                response = await self.data_source.list_buckets(MaxBuckets=1000, ContinuationToken=token)
            if not response.success:
                return response
            # An account without buckets answers {"Buckets": []}; no "Buckets" key is a broken answer.
            if not isinstance(response.data, dict) or "Buckets" not in response.data:
                return S3Response(success=False, error="the response has no bucket list")
            page = response.data["Buckets"] or []
            for bucket in page:
                if bucket.get("Name") and bucket.get("BucketRegion"):
                    self.bucket_regions[bucket["Name"]] = bucket["BucketRegion"]
            buckets.extend(page)
            token = (response.data or {}).get("ContinuationToken")
            if not token:
                return S3Response(success=True, data={"Buckets": buckets})

    async def _list_buckets_or_raise(self) -> list[dict]:
        """Every bucket, or a notification and ConnectionError: an empty run must not pass for a sync."""
        response = await self._list_all_buckets()
        if not response.success:
            err = response.error or "unknown error"
            self.logger.error(f"Failed to list buckets: {err}")
            await self.notify(
                type=NotificationType.CONNECTOR_SYNC_ERROR,
                title=self._notification_title("Failed to list buckets"),
                message=f"Failed to list buckets: {err}. "
                "Check credentials and s3:ListAllMyBuckets / bucket access.",
                severity=NotificationSeverity.ERROR,
            )
            raise ConnectionError(f"Failed to list {self.connector_name} buckets: {err}")
        return response.data["Buckets"]

    async def _unfiltered_bucket_names(self, listed: list[dict], sync_filters: FilterCollection) -> list[str]:
        """Listed buckets plus the stored ones: a deleted bucket drops out of ListBuckets,
        and only syncing it again sees NoSuchBucket and removes its records."""
        names = [b.get("Name") for b in listed if b.get("Name")]
        try:
            groups = await self.data_entities_processor.get_nodes_by_filters(
                collection=CollectionNames.RECORD_GROUPS.value,
                filters={"connectorId": self.connector_id, "groupType": RecordGroupType.BUCKET.value},
            )
        except Exception as e:  # the listed buckets still sync; the stored ones are retried next run
            self.logger.warning(f"Could not read the stored buckets: {e}")
            groups = []
        stored = sorted({g.get("externalGroupId") for g in groups if isinstance(g, dict)} - {None, ""} - set(names))
        return [n for n in names + stored if name_passes_filter(sync_filters, "buckets", n)]

    async def _get_bucket_region(self, bucket_name: str) -> str:
        """Get the region for a bucket, using cache if available."""
        if bucket_name in self.bucket_regions:
            return self.bucket_regions[bucket_name]

        if not self.data_source:
            self.logger.warning(f"Cannot fetch region for bucket {bucket_name}: data_source not initialized")
            return self.region or "us-east-1"

        try:
            async with self._paced():
                response = await self.data_source.get_bucket_location(Bucket=bucket_name)
            if response.success and response.data:
                location = response.data.get("LocationConstraint")
                region = "us-east-1" if location is None or location == "" else location
                self.bucket_regions[bucket_name] = region
                self.logger.debug(f"Cached region for bucket {bucket_name}: {region}")
                return region
            else:
                self.logger.warning(
                    f"Failed to get region for bucket {bucket_name}: {response.error}. "
                    f"Using configured region {self.region or 'us-east-1'}"
                )
        except Exception as e:
            self.logger.warning(
                f"Error fetching region for bucket {bucket_name}: {e}. "
                f"Using configured region {self.region or 'us-east-1'}"
            )

        return self.region or "us-east-1"

    async def _sync_bucket(self, bucket_name: str) -> None:
        """Sync a bucket, or only the folders the folder filter names."""
        sync_filters = self.sync_filters if hasattr(self, 'sync_filters') and self.sync_filters else FilterCollection()
        scope = FolderScope.from_filters(sync_filters)
        if not scope.is_everything:
            self.logger.info(f"Folder filter for bucket {bucket_name}: {scope.describe()}")
        recorded = await recorded_ids(self.data_entities_processor, self.connector_id, bucket_name)
        listed: set[str] = set()
        unrecorded: list[dict] = []
        complete = True
        for prefix in scope.list_prefixes:
            try:
                prefix_listed, prefix_complete = await self._sync_bucket_prefix(
                    bucket_name, prefix, scope, recorded, unrecorded
                )
            except _BucketGone:
                await remove_container(
                    self.data_entities_processor, self.connector_id, bucket_name,
                    f"now that {bucket_name} no longer exists", self.logger,
                )
                return
            listed |= prefix_listed
            complete = complete and prefix_complete
        # One pass after every prefix: a rename into a folder listed later is
        # still stored under its old path until that folder is processed.
        if complete:
            await self._claim_records_of_gone_copies(bucket_name, unrecorded, listed)
            await remove_records_not_listed(
                self.data_entities_processor, self.connector_id, bucket_name, scope.list_prefixes, listed, self.logger
            )
        else:
            # Only a bucket with stored records or a stored group needs the full sync to keep its
            # hands off; one never read (the key can list it but not read it) must not stop every
            # full sync's cleanup. A group not written this run would otherwise lose its edges.
            if recorded or await self._unwritten_group_is_stored(bucket_name):
                self._skipped_part_of_a_bucket = True
            self.logger.info(f"Not removing records in bucket {bucket_name}: a listing did not cover every object")
        await clean_up_scope(
            self.data_entities_processor, self.record_sync_point, self.connector_id, bucket_name, scope, self.logger
        )

    async def _unwritten_group_is_stored(self, bucket_name: str) -> bool:
        """Whether the bucket has a stored group this full sync has not written. Unsure counts as yes."""
        if self._bucket_groups_written is not None and bucket_name in self._bucket_groups_written:
            return False
        try:
            groups = await self.data_entities_processor.get_nodes_by_filters(
                collection=CollectionNames.RECORD_GROUPS.value,
                filters={
                    "connectorId": self.connector_id,
                    "groupType": RecordGroupType.BUCKET.value,
                    "externalGroupId": bucket_name,
                },
            )
        except Exception as e:
            self.logger.warning(f"Could not check for a stored group of bucket {bucket_name}: {e}")
            return True
        return bool(groups)

    async def _claim_records_of_gone_copies(self, bucket_name: str, unrecorded: list[dict], listed: set[str]) -> None:
        """Process the unchanged objects that have no record of their own, when that is safe.

        The connectors take equal content at a new key for a move, so one record can
        stand for two keys. Before the removal pass deletes a record whose key is gone,
        a listed copy of its content takes it over. A copy whose content is held by a
        key that is still listed, or by another bucket, is left alone, so the record
        does not move back and forth between two live keys.
        """
        prefix = f"{bucket_name}/"
        for obj in unrecorded:
            key = obj.get("Key", "")
            holder = None
            try:
                revision = make_s3_composite_revision(bucket_name, key.lstrip("/"), obj.get("ETag", "").strip('"') or None)
                holder = (
                    await self.data_entities_processor.get_record_by_external_revision_id(self.connector_id, revision)
                    if revision else None
                )
                if holder is not None:
                    held_at = holder.external_record_id or ""
                    if not held_at.startswith(prefix) or held_at in listed:
                        continue
                segments = get_folder_path_segments_from_key(key)
                if segments:
                    await self._ensure_parent_folders_exist(bucket_name, segments)
                record, permissions = await self._process_s3_object(obj, bucket_name)
                if record:
                    await self._save_records(*self._split_moves([(record, permissions)], bucket_name))
                elif holder is not None:
                    listed.add(held_at)
            except Exception as e:
                self.logger.error(f"Error giving {key} the record of its content: {e}", exc_info=True)
                if holder is not None:
                    # Keep the record rather than delete content that is still listed.
                    listed.add(holder.external_record_id or "")

    async def _sync_bucket_prefix(
        self, bucket_name: str, prefix: str, scope: FolderScope, recorded: Container[str], unrecorded: list[dict],
    ) -> tuple[set[str], bool]:
        """Sync objects under one prefix of a bucket ("" for all of it), with pagination and incremental sync.

        Returns the record ids the listing keeps, and whether it covered every object.
        """
        if not self.data_source:
            raise ConnectionError(f"{self.connector_name} connector is not initialized.")

        sync_filters = self.sync_filters if hasattr(self, 'sync_filters') and self.sync_filters else FilterCollection()

        extensions_filter = sync_filters.get(SyncFilterKey.FILE_EXTENSIONS)
        if extensions_filter and not extensions_filter.is_empty():
            self.logger.info(
                f"File extensions filter active for bucket {bucket_name}: "
                f"operator={extensions_filter.operator_value}, extensions={extensions_filter.value}"
            )

        user_date_filters = self._get_date_filters()
        modified_after_ms, modified_before_ms = user_date_filters

        # Each listed prefix keeps its own continuation token and last sync time.
        sync_point_key = generate_record_sync_point_key(
            RecordType.FILE.value, "bucket", f"{bucket_name}/{prefix}" if prefix else bucket_name
        )
        sync_point = await self.record_sync_point.read_sync_point(sync_point_key)
        continuation_token = sync_point.get("continuation_token") if sync_point else None
        last_sync_time = sync_point.get("last_sync_time") if sync_point else None
        # A run resumed from a saved token never sees the pages before it.
        resumed = continuation_token is not None
        listing_started_at = sync_point.get("listing_started_at") if resumed else get_epoch_timestamp_in_ms()

        if last_sync_time:
            user_modified_after_ms = modified_after_ms
            if user_modified_after_ms:
                modified_after_ms = max(user_modified_after_ms, last_sync_time)
                self.logger.debug(
                    f"Merging modified_after filter for incremental sync: {modified_after_ms}"
                )
            else:
                modified_after_ms = last_sync_time
                self.logger.debug(f"Using last_sync_time for incremental sync: {modified_after_ms}")

        batch_records = []
        self._moved_record_ids[bucket_name] = {}
        has_more = True
        listing_failed = bucket_gone = False
        failed = FailedItems()
        max_timestamp = last_sync_time if last_sync_time else 0
        listed: set[str] = set()
        unjudged = False
        # A refused position is dropped once; refused again, the listing fails as any other.
        restarted = False

        while has_more:
            listing = False
            try:
                async with self._paced():
                    listing = True
                    response = await self.data_source.list_objects_v2(
                        Bucket=bucket_name,
                        MaxKeys=self.list_page_size,
                        ContinuationToken=continuation_token,
                        Prefix=prefix or None,
                    )
                    listing = False

                    if not response.success:
                        error_msg = response.error or "Unknown error"
                        status = getattr(response, "status_code", None)
                        if (
                            continuation_token
                            and not restarted
                            and status == HttpStatusCode.BAD_REQUEST.value
                            and error_msg.startswith("InvalidArgument")
                        ):
                            # A saved position the store no longer takes would fail every run; list from the start.
                            self.logger.warning(
                                f"Listing of bucket {bucket_name} refused its saved position ({error_msg}); "
                                "listing the bucket again from the start"
                            )
                            await self.notify(
                                type=NotificationType.CONNECTOR_SYNC_ERROR,
                                severity=NotificationSeverity.WARNING,
                                title=self._notification_title("Bucket listed again from the start"),
                                message=f"The saved listing position for bucket '{bucket_name}' was no longer "
                                "valid, so this sync lists the whole bucket again.",
                            )
                            await self.record_sync_point.update_sync_point(
                                sync_point_key, {"continuation_token": None, "listing_started_at": None}
                            )
                            continuation_token = None
                            resumed = False
                            restarted = True
                            listing_started_at = get_epoch_timestamp_in_ms()
                            continue
                        # Only this code proves the bucket is gone; access and network errors prove nothing.
                        bucket_gone = error_msg.startswith("NoSuchBucket")
                        if status == HttpStatusCode.FORBIDDEN.value or error_msg.startswith("AccessDenied"):
                            self.logger.warning(
                                f"Access denied when listing objects in bucket {bucket_name} (status {status}): "
                                f"{error_msg}. The key needs s3:ListBucket on arn:aws:s3:::{bucket_name}."
                            )
                            self._unreadable_buckets.add(bucket_name)
                        else:
                            self.logger.error(
                                f"Failed to list objects in bucket {bucket_name} (status {status}): {error_msg}"
                            )
                            self._unlisted_buckets[bucket_name] = error_msg
                        listing_failed = True
                        has_more = False
                        continue

                    await self._ensure_bucket_group(bucket_name)

                    objects_data = response.data or {}
                    # S3 leaves "Contents" out of an empty page, which can still be truncated.
                    objects = objects_data.get("Contents") or []
                    self.logger.info(
                        f"Processing {len(objects)} objects from bucket {bucket_name}"
                    )

                    for obj in objects:
                        obj_ts = cutoff_ts = None
                        judged = False
                        try:
                            key = obj.get("Key", "")

                            is_folder = key.endswith("/")

                            if not (scope.includes_folder(key) if is_folder else scope.includes_file(key)):
                                continue

                            if not is_folder and not extension_passes_filter(sync_filters, get_file_extension(key)):
                                self.logger.debug(f"Skipping {key}: excluded by the file extensions filter")
                                continue

                            if not self._pass_date_filters(obj, *user_date_filters):
                                continue

                            # Kept before processing: an object that fails to process still exists.
                            listed |= listed_record_ids(bucket_name, key)
                            judged = True

                            if last_sync_time and not self._pass_date_filters(obj, modified_after_ms, modified_before_ms):
                                if not is_folder and f"{bucket_name}/{key.lstrip('/')}" not in recorded:
                                    # Its content may be held by another key's record; decided once every prefix is listed.
                                    unrecorded.append(obj)
                                continue

                            last_modified = obj.get("LastModified")
                            if last_modified:
                                obj_ts = datetime_to_epoch_ms(last_modified)
                                if obj_ts is not None:
                                    max_timestamp = max(max_timestamp, obj_ts)

                            cutoff_ts = None if is_folder else obj_ts

                            # Ensure folder hierarchy exists from file path (S3 has no folder objects)
                            if not is_folder:
                                path_segments = get_folder_path_segments_from_key(key)
                                if path_segments:
                                    await self._ensure_parent_folders_exist(bucket_name, path_segments)

                            record, permissions = await self._process_s3_object(
                                obj, bucket_name
                            )
                            if record:
                                batch_records.append((record, permissions))

                                if len(batch_records) >= self.batch_size:
                                    await self._save_records(*self._split_moves(batch_records, bucket_name))
                                    batch_records = []
                            elif key.lstrip("/"):
                                # The processor returns no record for a real key only on an error.
                                failed.add(cutoff_ts)
                        except Exception as e:
                            self.logger.error(
                                f"Error processing object {obj.get('Key', 'unknown')}: {e}",
                                exc_info=True,
                            )
                            failed.add(cutoff_ts)
                            # An object that errored before it was judged may still be kept.
                            unjudged = unjudged or not judged
                            continue

                    has_more = objects_data.get("IsTruncated", False)
                    continuation_token = objects_data.get("NextContinuationToken")
                    if has_more and not continuation_token:
                        self.logger.error(f"Listing of bucket {bucket_name} is truncated but has no continuation token")
                        listing_failed = True
                        has_more = False
                        continue

                    # A saved token marks the pages before it done: their records are written first,
                    # and after a failed object the token stays before it.
                    if continuation_token and not failed.count:
                        if batch_records:
                            await self._save_records(*self._split_moves(batch_records, bucket_name))
                            batch_records = []
                        await self.record_sync_point.update_sync_point(
                            sync_point_key,
                            {
                                "continuation_token": continuation_token,
                                "listing_started_at": listing_started_at,
                                "no_such_bucket": None,
                            },
                        )

            except Exception as e:
                if not listing:
                    # Our own failure (a graph or sync point write), not the store's: the bucket
                    # fails, and the run fails once the other buckets are synced.
                    raise
                self.logger.error(
                    f"Error during bucket sync for {bucket_name}: {e}", exc_info=True
                )
                self._unlisted_buckets[bucket_name] = str(e)
                listing_failed = True
                has_more = False

        if batch_records:
            try:
                await self._save_records(*self._split_moves(batch_records, bucket_name))
            except Exception:
                # The unsaved records sit on pages a resume token would skip, so
                # clear it and let the error fail the sync; no checkpoint is written.
                try:
                    await self.record_sync_point.update_sync_point(sync_point_key, {"continuation_token": None})
                except Exception as clear_error:
                    self.logger.error(
                        f"Failed to clear the resume token for bucket {bucket_name}: {clear_error}"
                    )
                raise

        # Objects are listed by name, not time, so a checkpoint after a partial
        # listing would skip the older objects it never reached. The saved
        # continuation_token lets the next run resume.
        if failed.count:
            self.logger.warning(
                f"{failed.count} objects in bucket {bucket_name} failed to process; "
                "the next sync retries them"
            )
            # A saved resume token would skip the pages holding the failures.
            await self.record_sync_point.update_sync_point(sync_point_key, {"continuation_token": None})
        if bucket_gone:
            # One answer can come from a server restarted without its volume; remove only on a second run in a row.
            if sync_point and sync_point.get("no_such_bucket"):
                raise _BucketGone(bucket_name)
            await self.record_sync_point.update_sync_point(sync_point_key, {"no_such_bucket": True})
        if listing_failed:
            return listed, False
        checkpoint = listing_checkpoint(failed.checkpoint(max_timestamp), listing_started_at)
        # The listing reached its end, so the saved token is spent even when no time was seen.
        done: dict[str, Any] = {"continuation_token": None, "listing_started_at": None, "no_such_bucket": None}
        if checkpoint and checkpoint > 0:
            done["last_sync_time"] = checkpoint
        await self.record_sync_point.update_sync_point(sync_point_key, done)

        return listed, not (resumed or unjudged)

    def _split_moves(
        self, batch: list[tuple[FileRecord, list[Permission]]], bucket_name: str
    ) -> tuple[list[tuple[FileRecord, list[Permission]]], list[tuple[str, FileRecord, list[Permission]]]]:
        """Split a batch into new or changed records and moves (old external id, record, permissions)."""
        records, moves = [], []
        for record, permissions in batch:
            moved_from = self._moved_record_ids.get(bucket_name, {}).get(record.id)
            if moved_from and moved_from != record.external_record_id:
                moves.append((moved_from, record, permissions))
            else:
                records.append((record, permissions))
        return records, moves

    async def _save_records(
        self,
        records: list[tuple[FileRecord, list[Permission]]],
        moves: list[tuple[str, FileRecord, list[Permission]]],
    ) -> None:
        # A move is saved in place, keeping its indexed content; on_new_records would
        # take it for a new record and index it again. Moves go first so a new object
        # at a moved record's old key is not written onto that record.
        if moves:
            await self.data_entities_processor.on_records_moved(moves)
        if records:
            await self.data_entities_processor.on_new_records(records)

    async def _ensure_parent_folders_exist(
        self, bucket_name: str, path_segments: list[str]
    ) -> None:
        """Ensure folder records exist for each path segment (root to leaf).

        S3 list_objects only returns object keys; there are no separate folder objects.
        For each segment (e.g. 'a', 'a/b', 'a/b/c'), upsert a folder record and its edges.
        Every segment is processed once per sync run, so edges are still re-created
        on a full sync; segments already handled during *this* run are skipped.
        Without that memo the same handful of folders is re-upserted once per
        object per depth level — thousands of redundant graph round-trips on a
        bucket of any size, and profiling showed it dominating the sync.
        Process in order so parent exists before child. Aligns with Box _ensure_parent_folders_exist pattern.
        """
        if not path_segments:
            return
        timestamp_ms = get_epoch_timestamp_in_ms()
        for i, segment in enumerate(path_segments):
            external_id = f"{bucket_name}/{segment}"
            if external_id in self._ensured_folders:
                continue
            # Root folder: first segment has no parent. Others: parent is previous segment.
            parent_external_id = (
                f"{bucket_name}/{path_segments[i - 1]}" if i > 0 else None
            )
            parent_record_type = RecordType.FILE if parent_external_id else None
            record_name = segment.split("/")[-1] if segment else segment
            web_url = self._generate_web_url(bucket_name, segment + "/")
            folder_record = FileRecord(
                id=str(uuid.uuid4()),
                record_name=record_name,
                record_type=RecordType.FILE,
                record_group_type=RecordGroupType.BUCKET.value,
                external_record_group_id=bucket_name,
                external_record_id=external_id,
                external_revision_id=None,
                version=0,
                origin=OriginTypes.CONNECTOR.value,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                source_created_at=timestamp_ms,
                source_updated_at=timestamp_ms,
                weburl=web_url,
                signed_url=None,
                hide_weburl=True,
                is_internal=True,
                parent_external_record_id=parent_external_id,
                parent_record_type=parent_record_type,
                size_in_bytes=0,
                is_file=False,
                extension=None,
                path=segment,
                mime_type=MimeTypes.FOLDER.value,
                etag=None,
            )
            permissions = await self._create_s3_permissions(bucket_name, segment + "/")
            await self.data_entities_processor.on_new_records([(folder_record, permissions)])
            # Marked only after a successful upsert, so a failure part-way
            # through leaves the folder to be retried by the next object.
            self._ensured_folders.add(external_id)

    async def _process_s3_object(
        self, obj: dict, bucket_name: str
    ) -> tuple[FileRecord | None, list[Permission]]:
        """Process a single S3 object and convert it to a FileRecord.

        Logic:
        1. Extract path and etag from S3 object
        2. Try lookup by path (externalRecordId) - PRIMARY
           ├─ Found → Compare etags
           │   ├─ Different → Content change → Update record
           │   └─ Same → Skip (no changes)
           └─ Not Found → Try lookup by etag (externalRevisionId) - FALLBACK
               ├─ Found, and its key is gone from the bucket → Move/rename detected
               │   ├─ Reuse the record id; _moved_record_ids keeps the old external id
               │   └─ The batch saves it with on_records_moved (no re-index unless the type changed)
               └─ Not Found, or its key is still listed (a copy) → New file → Create new record
        """
        try:
            # 1. Extract path and etag from S3 object
            key = obj.get("Key", "")
            if not key:
                return None, []

            is_folder = key.endswith("/")
            is_file = not is_folder

            normalized_key = key.lstrip("/")
            if not normalized_key:
                return None, []

            last_modified = obj.get("LastModified")
            if last_modified:
                timestamp_ms = _s3_last_modified_to_epoch_ms(last_modified)
            else:
                timestamp_ms = get_epoch_timestamp_in_ms()

            external_record_id = f"{bucket_name}/{normalized_key}"
            current_etag = obj.get("ETag", "").strip('"')
            composite_revision = make_s3_composite_revision(bucket_name, normalized_key, current_etag or None)

            existing_record = await self.data_entities_processor.get_record_by_external_id(
                self.connector_id, external_record_id
            )

            if existing_record:
                # Found by path - check if revision (composite) changed (content change)
                stored_revision = existing_record.external_revision_id or ""

                # Content changed or missing - sync properly from S3
                if composite_revision and stored_revision and composite_revision != stored_revision:
                    self.logger.info(
                        f"Content change detected: {normalized_key} - externalRevisionId changed from {stored_revision} to {composite_revision}"
                    )
                elif not composite_revision or not stored_revision:
                    if not current_etag:
                        self.logger.warning(
                            f"Current ETag missing for {normalized_key}, processing record"
                        )
                    if not stored_revision:
                        self.logger.debug(
                            f"Stored revision missing for {normalized_key}, processing record"
                        )
            else:
                existing_record = await self.data_entities_processor.get_record_by_external_revision_id(
                    self.connector_id, composite_revision
                )
                if existing_record and (
                    existing_record.id in self._moved_record_ids.get(bucket_name, {}) or await self._still_in_bucket(existing_record)
                ):
                    self.logger.info(
                        f"New document: {normalized_key} is a copy of {existing_record.external_record_id}, not a move"
                    )
                    existing_record = None

                if existing_record:
                    # Same composite can only match same key; if path differs it's a move/rename (key changed, etag same)
                    if existing_record.external_record_id != external_record_id:
                        self._moved_record_ids.setdefault(bucket_name, {})[existing_record.id] = existing_record.external_record_id
                        self.logger.info(
                            f"Move/rename detected: {normalized_key} - file moved from {existing_record.external_record_id} to {external_record_id}"
                        )
                else:
                    self.logger.debug(f"New document: {normalized_key}")

            # Prepare record data: all items are RecordType.FILE; folders have is_file=False
            record_type = RecordType.FILE

            extension = get_file_extension(normalized_key) if is_file else None
            mime_type = get_mimetype_for_s3(normalized_key, is_folder=is_folder)

            parent_path = get_parent_path_from_key(normalized_key)
            parent_external_id = (f"{bucket_name}/{parent_path}" if parent_path else None)
            parent_record_type = RecordType.FILE if parent_path else None
            # Root-level items: parent must be null, not the bucket
            if parent_external_id == bucket_name:
                parent_external_id = None
                parent_record_type = None

            web_url = self._generate_web_url(bucket_name, normalized_key)

            record_id = existing_record.id if existing_record else str(uuid.uuid4())

            record_name = normalized_key.rstrip("/").split("/")[-1] or normalized_key.rstrip("/")

            file_record = FileRecord(
                id=record_id,
                record_name=record_name,
                record_type=record_type,
                record_group_type=RecordGroupType.BUCKET.value,
                external_record_group_id=bucket_name,
                external_record_id=external_record_id,
                external_revision_id=composite_revision,
                # The processor bumps the stored version only when the revision changed;
                # a version set here would rise on every re-sent unchanged object.
                version=0,
                origin=OriginTypes.CONNECTOR.value,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                source_created_at=existing_record.source_created_at if existing_record else timestamp_ms,
                source_updated_at=timestamp_ms,
                weburl=web_url,
                signed_url=None,
                hide_weburl=True,
                is_internal=True if is_folder else False,
                parent_external_record_id=parent_external_id,
                parent_record_type=parent_record_type,
                size_in_bytes=obj.get("Size", 0) if is_file else 0,
                is_file=is_file,
                extension=extension,
                path=normalized_key,
                mime_type=mime_type,
                etag=current_etag,
            )

            # Root-level items: do not link to the bucket as a parent
            if (
                file_record.parent_external_record_id
                and file_record.external_record_group_id
                and file_record.parent_external_record_id == file_record.external_record_group_id
            ):
                file_record.parent_external_record_id = None
                file_record.parent_record_type = None

            if (
                hasattr(self, 'indexing_filters')
                and self.indexing_filters
                and not self.indexing_filters.is_enabled(IndexingFilterKey.FILES, default=True)
            ):
                file_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

            permissions = await self._create_s3_permissions(bucket_name, key)

            return file_record, permissions

        except Exception as e:
            self.logger.error(f"Error processing S3 object: {e}", exc_info=True)
            return None, []

    async def _still_in_bucket(self, record: Record) -> bool:
        """Whether ``record``'s object is still listed. A check that fails counts as yes:
        taking a copy for a move would lose the original's record."""
        bucket_name = record.external_record_group_id
        key = path_in_container(bucket_name, record.external_record_id)
        if not bucket_name or not key:
            return True
        try:
            async with self._paced():
                response = await self.data_source.head_object(Bucket=bucket_name, Key=key)
        except Exception as e:
            self.logger.warning(f"Could not check whether {bucket_name}/{key} still exists: {e}")
            return True
        if response.success:
            return True
        if getattr(response, "status_code", None) == HttpStatusCode.NOT_FOUND.value:
            return False
        self.logger.warning(f"Could not check whether {bucket_name}/{key} still exists: {response.error}")
        return True

    async def _create_s3_permissions(
        self, bucket_name: str, key: str
    ) -> list[Permission]:
        """Objects inherit their parent folder or the bucket. No permission edges."""
        return []

    async def _load_creator(self) -> User:
        """The personal connector's creator. Raises if the lookup fails or finds no user with an email."""
        if self._creator is None:
            user = (
                await self.data_entities_processor.get_user_by_user_id(self.created_by, raise_on_error=True)
                if self.created_by else None
            )
            if not user or not getattr(user, "email", None):
                raise LookupError(f"Creator {self.created_by!r} of connector {self.connector_id} not found or has no email")
            self._creator = user
        return self._creator

    def full_sync_coverage(self) -> FullSyncCoverage:
        """A bucket not listed to the end was not seen in full, so nothing is unlinked after this sync."""
        return FullSyncCoverage(complete=not self._skipped_part_of_a_bucket)

    async def _creator_or_raise(self) -> User:
        """The creator, or a notification and the error: a run that ends normally would count as a full sync."""
        try:
            return await self._load_creator()
        except Exception as e:
            self.logger.error(f"Stopping {self.connector_name} sync: {e}")
            await self.notify(
                type=NotificationType.CONNECTOR_SYNC_ERROR,
                title=self._notification_title("Sync stopped"),
                message="The owner of this personal connector could not be looked up, so nothing was synced. "
                "Existing access is unchanged. The next sync tries again.",
                severity=NotificationSeverity.ERROR,
            )
            raise

    async def test_connection_and_access(self) -> bool:
        """Check that the key can list and read the buckets the sync uses."""
        if not self.data_source:
            return False
        try:
            async with self._shared_client():
                problem = await self._access_problem()
        except Exception as e:
            self.logger.error(f"{self.connector_name} connection test failed: {e}", exc_info=True)
            await self.notify(
                type=NotificationType.CONNECTOR_AUTH_ERROR,
                severity=NotificationSeverity.ERROR,
                title=self._notification_title("Connection test failed"),
                message=str(e),
            )
            # False would show only a generic "verify your credentials"; the reason is the useful part.
            raise ConnectorInitError(f"{self.display_name}: could not check access: {e}") from e
        if problem is None:
            self.logger.info(f"{self.connector_name} connection test successful.")
            return True
        self.logger.error(f"{self.connector_name} connection test failed: {problem}")
        await self.notify(
            type=NotificationType.CONNECTOR_AUTH_ERROR,
            severity=NotificationSeverity.ERROR,
            title=self._notification_title("Connection test failed"),
            message=problem,
        )
        raise ConnectorInitError(f"{self.display_name}: {problem}")

    async def _access_problem(self) -> str | None:
        """Why the key cannot sync, or None.

        At least one bucket the sync uses must be readable. An unreadable selected bucket
        next to readable ones only warns: this test also runs when the connector is rebuilt,
        and one bucket losing access must not stop the sync of the others. The same holds for
        the folders chosen in a bucket: a policy often grants s3:GetObject per folder.
        """
        selected = [self.bucket_name] if self.bucket_name else included_names(self.sync_filters, "buckets")
        # Listed as the sync lists: a policy may allow s3:ListBucket only under an s3:prefix condition.
        prefixes = FolderScope.from_filters(self.sync_filters).list_prefixes
        if selected:
            semaphore = asyncio.Semaphore(self.bucket_concurrency)

            async def probe(bucket_name: str) -> tuple[str | None, list[str]]:
                async with semaphore:
                    return await self._bucket_folders_problem(bucket_name, prefixes)

            # A key scoped to named buckets may lack s3:ListAllMyBuckets; sync does not need it then.
            results = await asyncio.gather(*(probe(b) for b in selected))
            problems = [problem for problem, _ in results if problem]
            if len(problems) == len(selected):
                return " ".join(problems)
            await self._warn_unreadable(problems, [p for _, partial in results for p in partial])
            return None
        response = await self._list_all_buckets()
        if not response.success:
            hint = (
                " The key needs s3:ListAllMyBuckets, or name the buckets in the Bucket Names filter."
                if getattr(response, "status_code", None) == HttpStatusCode.FORBIDDEN.value else ""
            )
            return f"Could not list buckets: {response.error}.{hint}"
        problems = []
        for bucket in response.data["Buckets"]:
            name = bucket.get("Name")
            if not name or not name_passes_filter(self.sync_filters, "buckets", name):
                continue
            problem, partial = await self._bucket_folders_problem(name, prefixes)
            if problem is None:
                await self._warn_unreadable([], partial)
                return None
            problems.append(problem)
        if not problems:
            return None
        return f"{problems[0]} None of the {len(problems)} listed bucket(s) could be read."

    async def _warn_unreadable(self, buckets: list[str], folders: list[str]) -> None:
        """One warning for the buckets and chosen folders the key cannot read, next to readable ones."""
        if not buckets and not folders:
            return
        what = "buckets" if buckets else "folders"
        self.logger.warning(f"{self.connector_name} cannot read some selected {what}: {' '.join(buckets + folders)}")
        await self.notify(
            type=NotificationType.CONNECTOR_AUTH_ERROR,
            severity=NotificationSeverity.WARNING,
            title=self._notification_title(f"Some {what} cannot be read"),
            message=" ".join(buckets + folders),
        )

    async def _bucket_folders_problem(self, bucket_name: str, prefixes: list[str]) -> tuple[str | None, list[str]]:
        """Why the key can read none of the chosen folders of the bucket (or None), and the ones it cannot read."""
        problems = [p for p in [await self._bucket_access_problem(bucket_name, prefix) for prefix in prefixes] if p]
        if len(problems) == len(prefixes):
            return " ".join(problems), []
        return None, problems

    async def _bucket_access_problem(self, bucket_name: str, prefix: str = "") -> str | None:
        """Why the key cannot list or read objects under ``prefix`` in the bucket, or None."""
        where = f"folder '{prefix}' of bucket '{bucket_name}'" if prefix else f"bucket '{bucket_name}'"
        async with self._paced():
            listing = await self.data_source.list_objects_v2(Bucket=bucket_name, MaxKeys=1, Prefix=prefix or None)
        if not listing.success:
            hint = (
                f" The key needs s3:ListBucket on arn:aws:s3:::{bucket_name}."
                if getattr(listing, "status_code", None) == HttpStatusCode.FORBIDDEN.value else ""
            )
            return f"Could not list objects in {where}: {listing.error}.{hint}"
        contents = (listing.data or {}).get("Contents") or []
        if not contents:
            return None
        async with self._paced():
            head = await self.data_source.head_object(Bucket=bucket_name, Key=contents[0].get("Key"))
        if not head.success:
            hint = ""
            if getattr(head, "status_code", None) == HttpStatusCode.FORBIDDEN.value:
                hint = f" The key needs s3:GetObject on arn:aws:s3:::{bucket_name}/{prefix}*."
                if not prefix:
                    hint += " If it may read only some folders, choose those in the Folders filter."
            return f"Could not read objects in {where}: {head.error}.{hint}"
        return None

    async def get_signed_url(self, record: Record) -> str | None:
        """Generate a presigned URL for an S3 object."""
        if not self.data_source:
            raise connector_not_ready(self.display_name)
        try:
            bucket_name = record.external_record_group_id
            if not bucket_name:
                self.logger.warning(f"No bucket name found for record: {record.id}")
                raise not_downloadable(
                    "This item is missing the bucket it belongs to and cannot be downloaded.",
                    connector=self.display_name,
                )

            external_record_id = record.external_record_id
            if not external_record_id:
                self.logger.warning(f"No external_record_id found for record: {record.id}")
                raise not_downloadable(
                    "This item is missing its object key and cannot be downloaded.",
                    connector=self.display_name,
                )

            if external_record_id.startswith(f"{bucket_name}/"):
                key = external_record_id[len(f"{bucket_name}/"):]
            else:
                key = external_record_id.lstrip("/")
            # Sync stores the key as listed (botocore decodes it), so decoding it again names another object.

            bucket_region = await self._get_bucket_region(bucket_name)

            self.logger.debug(
                f"Generating presigned URL - Bucket: {bucket_name}, "
                f"Region: {bucket_region}, Key: {key}, Record ID: {record.id}"
            )

            response = await self.data_source.generate_presigned_url(
                ClientMethod="get_object",
                Params={"Bucket": bucket_name, "Key": key},
                ExpiresIn=PRESIGNED_URL_EXPIRY_SECONDS,
                region_name=bucket_region
            )

            if response.success:
                return response.data

            error_msg = response.error or "Unknown error"
            # The status comes off the botocore error. The error text is built
            # from the source's own strings, so matching phrases in it would let
            # a bucket or key name pick the status the user sees.
            source_status = getattr(response, "status_code", None)
            self.logger.error(
                f"❌ FAILED: Failed to generate presigned URL (status {source_status}). "
                f"Error: {error_msg} | Bucket: {bucket_name} | Key: {key} | Record ID: {record.id}"
            )
            raise map_source_status(source_status, connector=self.display_name)
        except HTTPException:
            raise
        except Exception as e:
            self.logger.error(
                f"Error generating signed URL for record {record.id}: {e}", exc_info=True
            )
            raise to_stream_error(e, connector=self.display_name) from e

    async def stream_record(self, record: Record) -> StreamingResponse:
        """Stream S3 object content."""
        if isinstance(record, FileRecord) and not record.is_file:
            raise HTTPException(
                status_code=HttpStatusCode.BAD_REQUEST.value,
                detail="Cannot stream folder content",
            )

        signed_url = await self.get_signed_url(record)
        if not signed_url:
            raise not_found_at_source(self.display_name)

        return create_stream_record_response(
            stream_content(
                signed_url,
                record_id=record.id,
                file_name=record.record_name,
                connector=self.display_name,
            ),
            filename=record.record_name,
            mime_type=record.mime_type if record.mime_type else "application/octet-stream",
            fallback_filename=f"record_{record.id}",
        )


    async def cleanup(self) -> None:
        """Clean up resources used by the connector."""
        self.logger.info(f"Cleaning up {self.connector_name} connector resources.")
        self.data_source = None

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: str | None = None,
        cursor: str | None = None
    ) -> FilterOptionsResponse:
        """Get dynamic filter options for filters."""
        if filter_key == "buckets":
            return await self._get_bucket_options(page, limit, search, cursor)
        else:
            raise ValueError(f"Unsupported filter key: {filter_key}")

    async def _get_bucket_options(
        self,
        page: int,
        limit: int,
        search: str | None,
        cursor: str | None = None,
    ) -> FilterOptionsResponse:
        """One page of buckets, filtered by the store (name starts with ``search``) and paged with its token."""
        def failure(message: str) -> FilterOptionsResponse:
            return FilterOptionsResponse(
                success=False, options=[], page=page, limit=limit, has_more=False, message=message
            )

        try:
            if not self.data_source:
                return failure(f"{self.connector_name.value} connector is not initialized")
            async with self._paced():
                response = await self.data_source.list_buckets(
                    MaxBuckets=limit, ContinuationToken=cursor or None, Prefix=search or None
                )
            if not response.success:
                return failure(f"Failed to list buckets: {response.error}")
            data = response.data
            # An account without buckets answers {"Buckets": []}; no "Buckets" key is a broken answer.
            if not isinstance(data, dict) or "Buckets" not in data:
                return failure("Failed to list buckets: the response has no bucket list")
            names = [b["Name"] for b in data["Buckets"] or [] if b.get("Name")]
            token = data.get("ContinuationToken")
            if search:
                # A store that ignores Prefix (MinIO) answers with every bucket.
                names = [n for n in names if n.startswith(search)]
            if not token and len(names) > limit:
                # A store that ignores MaxBuckets (MinIO) answers with every bucket; page them here.
                start = (page - 1) * limit
                return FilterOptionsResponse(
                    success=True,
                    options=[FilterOption(id=n, label=n) for n in names[start:start + limit]],
                    page=page,
                    limit=limit,
                    has_more=start + limit < len(names),
                )
            return FilterOptionsResponse(
                success=True,
                options=[FilterOption(id=n, label=n) for n in names],
                page=page,
                limit=limit,
                has_more=bool(token),
                cursor=token,
            )
        except Exception as e:
            self.logger.error(f"Error getting bucket options: {e}", exc_info=True)
            return failure(f"Error: {str(e)}")

    def handle_webhook_notification(self, notification: dict) -> None:
        """Handle webhook notifications from the source."""
        raise NotImplementedError("This method is not supported")

    async def reindex_records(self, record_results: list[Record]) -> None:
        """Reindex records by checking for updates at source and publishing reindex events."""
        try:
            if not record_results:
                self.logger.info("No records to reindex")
                return

            self.logger.info(f"Starting reindex for {len(record_results)} {self.connector_name} records")

            if not self.data_source:
                self.logger.error(f"{self.connector_name} connector is not initialized.")
                raise Exception(f"{self.connector_name} connector is not initialized.")

            org_id = self.data_entities_processor.org_id
            updated_records = []
            non_updated_records = []

            for record in record_results:
                try:
                    updated_record_data = await self._check_and_fetch_updated_record(
                        org_id, record
                    )
                    if updated_record_data:
                        updated_record, permissions = updated_record_data
                        updated_records.append((updated_record, permissions))
                    else:
                        non_updated_records.append(record)
                except _ObjectGone as gone:
                    # Indexing could only fail to fetch it; the next sync's listing removes the record.
                    if record.mime_type != MimeTypes.FOLDER.value:
                        self.logger.info(f"Not reindexing record {record.id}: {gone} is no longer in the bucket")
                    continue
                except Exception as e:
                    self.logger.error(f"Error checking record {record.id} at source: {e}", exc_info=True)
                    continue

            if updated_records:
                await self.data_entities_processor.on_new_records(updated_records)
                self.logger.info(f"Updated {len(updated_records)} records in DB")

            if non_updated_records:
                await self.data_entities_processor.reindex_existing_records(non_updated_records)
                self.logger.info(f"Published reindex events for {len(non_updated_records)} records with unchanged external_revision_id")

        except Exception as e:
            self.logger.error(f"Error during {self.connector_name} reindex: {e}", exc_info=True)
            raise

    async def _check_and_fetch_updated_record(
        self, org_id: str, record: Record
    ) -> tuple[Record, list[Permission]] | None:
        """Check if record has been updated at source and fetch updated data."""
        try:
            bucket_name = record.external_record_group_id
            external_record_id = record.external_record_id

            if not bucket_name or not external_record_id:
                self.logger.warning(f"Missing bucket or external_record_id for record {record.id}")
                return None

            if external_record_id.startswith(f"{bucket_name}/"):
                normalized_key = external_record_id[len(f"{bucket_name}/"):]
            else:
                normalized_key = external_record_id.lstrip("/")

            if not normalized_key:
                self.logger.warning(f"Invalid key for record {record.id}")
                return None

            async with self._paced():
                response = await self.data_source.head_object(Bucket=bucket_name, Key=normalized_key)

            if not response.success:
                status = getattr(response, "status_code", None)
                if status == HttpStatusCode.NOT_FOUND.value:
                    raise _ObjectGone(f"{bucket_name}/{normalized_key}")
                # Not proof of anything: reindex what is stored, and indexing reports the fetch.
                self.logger.warning(
                    f"Could not check {bucket_name}/{normalized_key} for record {record.id} (status {status}): "
                    f"{response.error}"
                )
                return None

            obj_metadata = response.data
            if not obj_metadata:
                return None

            current_etag = obj_metadata.get("ETag", "").strip('"')
            composite_revision = make_s3_composite_revision(bucket_name, normalized_key, current_etag or None)
            stored_revision = record.external_revision_id

            if composite_revision and stored_revision and composite_revision == stored_revision:
                self.logger.debug(f"Record {record.id}: external_revision_id unchanged ({composite_revision})")
                return None

            self.logger.debug(f"Record {record.id}: external_revision_id changed from {stored_revision} to {composite_revision}")

            last_modified = obj_metadata.get("LastModified")
            if last_modified:
                timestamp_ms = _s3_last_modified_to_epoch_ms(last_modified)
            else:
                timestamp_ms = get_epoch_timestamp_in_ms()

            is_folder = normalized_key.endswith("/")
            is_file = not is_folder

            extension = get_file_extension(normalized_key) if is_file else None
            mime_type = get_mimetype_for_s3(normalized_key, is_folder=is_folder)

            parent_path = get_parent_path_from_key(normalized_key)
            parent_external_id = (f"{bucket_name}/{parent_path}" if parent_path else None)
            parent_record_type = RecordType.FILE if parent_path else None
            # Root-level items: parent must be null, not the bucket
            if parent_external_id == bucket_name:
                parent_external_id = None
                parent_record_type = None

            web_url = self._generate_web_url(bucket_name, normalized_key)

            record_name = normalized_key.rstrip("/").split("/")[-1] or normalized_key.rstrip("/")

            updated_external_record_id = f"{bucket_name}/{normalized_key}"

            # All items are RecordType.FILE; folders have is_file=False
            updated_record = FileRecord(
                id=record.id,
                record_name=record_name,
                record_type=RecordType.FILE,
                record_group_type=RecordGroupType.BUCKET.value,
                external_record_group_id=bucket_name,
                external_record_id=updated_external_record_id,
                external_revision_id=composite_revision,
                version=0,
                origin=OriginTypes.CONNECTOR.value,
                connector_name=self.connector_name,
                connector_id=self.connector_id,
                source_created_at=record.source_created_at,
                source_updated_at=timestamp_ms,
                weburl=web_url,
                signed_url=None,
                hide_weburl=True,
                is_internal=True if is_folder else False,
                parent_external_record_id=parent_external_id,
                parent_record_type=parent_record_type,
                size_in_bytes=obj_metadata.get("ContentLength", 0) if is_file else 0,
                is_file=is_file,
                extension=extension,
                path=normalized_key,
                mime_type=mime_type,
                etag=current_etag,
            )

            # Root-level items: do not link to the bucket as a parent
            if (
                updated_record.parent_external_record_id
                and updated_record.external_record_group_id
                and updated_record.parent_external_record_id == updated_record.external_record_group_id
            ):
                updated_record.parent_external_record_id = None
                updated_record.parent_record_type = None

            if (
                hasattr(self, 'indexing_filters')
                and self.indexing_filters
                and not self.indexing_filters.is_enabled(IndexingFilterKey.FILES, default=True)
            ):
                updated_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

            permissions = await self._create_s3_permissions(bucket_name, normalized_key)

            return updated_record, permissions

        except _ObjectGone:
            raise
        except Exception as e:
            self.logger.error(f"Error checking record {record.id} at source: {e}", exc_info=True)
            return None

    async def run_incremental_sync(self) -> None:
        """Run an incremental synchronization from buckets."""
        async with self._shared_client():
            await self._run_incremental_sync()

    async def _run_incremental_sync(self) -> None:
        try:
            self.logger.info(f"Starting {self.connector_name} incremental sync.")

            if not self.data_source:
                raise ConnectionError(f"{self.connector_name} connector is not initialized.")

            # Per run, as in run_sync: a folder removed between runs must be re-created.
            self._start_run(full=False)

            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, self.filter_key, self.connector_id, self.logger
            )

            sync_filters = self.sync_filters if hasattr(self, 'sync_filters') and self.sync_filters else FilterCollection()

            selected_buckets = included_names(sync_filters, "buckets")
            if not self.bucket_name:
                await remove_deselected_containers(
                    self.data_entities_processor, self.config_service, self.connector_id, "buckets", sync_filters, self.logger
                )

            buckets_to_sync = []
            if self.bucket_name:
                buckets_to_sync = [self.bucket_name]
                self.logger.info(f"Using configured bucket: {self.bucket_name}")
            elif selected_buckets:
                buckets_to_sync = selected_buckets
                self.logger.info(f"Using filtered buckets: {buckets_to_sync}")
            else:
                buckets_to_sync = await self._unfiltered_bucket_names(await self._list_buckets_or_raise(), sync_filters)

            if not buckets_to_sync:
                self.logger.warning("No buckets to sync")
                return

            self.logger.info(f"Fetching regions for {len(buckets_to_sync)} bucket(s)...")
            for bucket_name in buckets_to_sync:
                if bucket_name:
                    await self._get_bucket_region(bucket_name)

            await self._sync_buckets(buckets_to_sync)
            await self._report_bucket_problems(chosen=bool(self.bucket_name or selected_buckets))

            self.logger.info(f"{self.connector_name} incremental sync completed.")
        except Exception as ex:
            self.logger.error(f"❌ Error in {self.connector_name} incremental sync: {ex}", exc_info=True)
            raise
