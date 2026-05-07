"""
Slack Connector — Production-ready implementation

Sync pipeline (in order):
  1. Users       → AppUser nodes + user_id_to_email_cache + user_id_to_internal_id_cache
  2. UserGroups  → AppUserGroup nodes with resolved AppUser members
  3. Channels    → RecordGroup nodes (public/private/DM/MPIM) + HAS_PERMISSION edges
  4. Messages    → MessageRecord / FileRecord / LinkRecord per channel (incremental,
                   per-channel checkpoint via messages_sync_point)
  5. Thread-growth → detects threads with new replies since last run.
                   Uses per-channel checkpoints (audit_log_sync_point keyed by channel_id)
                   so each channel's window is exactly "since we last checked it".
                   First run falls back to the sync-window filter (same range as messages).
                   Full cursor-based pagination prevents silent truncation on busy channels.

Graph edges produced:
  HAS_PERMISSION   AppUser        → RecordGroup
  BELONGS_TO       Any Record     → RecordGroup  (via record_group_id on every record)
  MENTIONED_IN     AppUser        → MessageRecord  (from <@U…> in text)
  MENTIONED_IN     RecordGroup    → MessageRecord  (from <#C…> in text)
  ATTACHMENT       FileRecord     → MessageRecord  (via RecordRelations.ATTACHMENT)
  parent_node_id   child record   → parent record (files, links, thread replies, burst children)

Change-detection limitations (inherent to polling conversations.history):
  - conversations.history orders messages by creation time, not modification time.
    Edits on messages whose ts predates the current window cannot be detected.
  - For complete real-time coverage use Slack's Events API (push webhooks).
"""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional

import httpx
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.sync_point.sync_point import (
    SyncDataPointType,
    SyncPoint,
    generate_record_sync_point_key,
)
from app.connectors.core.registry.auth_builder import (
    AuthBuilder,
    AuthType,
)
from app.connectors.core.registry.connector_builder import (
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
    DocumentationLink,
    SyncStrategy,
)
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
from app.connectors.sources.slack.common.apps import SlackWorkspaceApp
from app.models.blocks import (
    Block,
    BlockGroup,
    BlockGroupChildren,
    BlocksContainer,
    BlockSubType,
    BlockType,
    ChildRecord,
    ChildType,
    DataFormat,
    GroupSubType,
    GroupType,
    IndexRange,
)
from app.models.entities import (
    AppRole,
    AppUser,
    AppUserGroup,
    FileRecord,
    LinkPublicStatus,
    LinkRecord,
    MessageRecord,
    ProgressStatus,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordRelations,
    RecordType,
    RelatedExternalRecord,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.slack.slack import SlackClient
from app.sources.external.slack.slack import SlackDataSource
from app.utils.streaming import create_stream_record_response
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from logging import Logger

    from app.config.configuration_service import ConfigurationService
    from app.connectors.core.base.data_store.data_store import DataStoreProvider

# ── Constants ──────────────────────────────────────────────────────────────────
BURST_WINDOW_SECONDS        = 300           # 5-minute conversational burst window

PAGE_SIZE_MESSAGES          = 999
PAGE_SIZE_THREADS           = 999
PAGE_SIZE_USERS             = 999
PAGE_SIZE_CHANNELS          = 999
PAGE_SIZE_MEMBERS           = 999
RATE_LIMIT_CALLS_PER_MINUTE = 50


# ── Internal dataclasses ───────────────────────────────────────────────────────

@dataclass
class ConversationalBurst:
    """All messages within BURST_WINDOW_SECONDS regardless of author."""
    messages:  list[dict[str, Any]]
    start_ts:  float
    end_ts:    float

    @property
    def message_count(self) -> int:
        return len(self.messages)

    @property
    def aggregated_text(self) -> str:
        return "\n\n".join(
            m.get("text", "") for m in self.messages if m.get("text")
        )


@dataclass
class ProcessingContext:
    """Immutable snapshot of workspace state passed through the message pipeline."""
    channel_id:         str
    channel_groups_map: dict[str, str]   # external channel_id → internal RecordGroup.id
    user_id_to_email:   dict[str, str]   # Slack user_id → email  (snapshot)
    user_id_to_name:    dict[str, str]   # Slack user_id → name  (snapshot)
    channel_id_to_name: dict[str, str]   # Slack channel_id → name  (snapshot)
    rate_limiter:       "RateLimiter"


@dataclass
class DeferredThread:
    """Thread parent message whose processing is deferred until after channel records are published."""
    msg: dict[str, Any]
    ctx: ProcessingContext
    rg_id: Optional[str]


class RateLimiter:
    """Sliding-window rate limiter shared across the entire connector instance."""

    def __init__(self, calls_per_minute: int = RATE_LIMIT_CALLS_PER_MINUTE,
                 logger: Optional[Logger] = None) -> None:
        self.calls_per_minute = calls_per_minute
        self._call_times: list[datetime] = []
        self._logger = logger

    async def acquire(self) -> None:
        now = datetime.now()
        # Evict stale entries
        self._call_times = [t for t in self._call_times if now - t < timedelta(minutes=1)]

        if len(self._call_times) >= self.calls_per_minute:
            wait = (self._call_times[0] + timedelta(minutes=1) - now).total_seconds()
            if wait > 0:
                if self._logger:
                    self._logger.debug(f"⏱️  Rate limit — waiting {wait:.2f}s")
                await asyncio.sleep(wait)
                return await self.acquire()

        self._call_times.append(now)
        return None


# ── Connector declaration ──────────────────────────────────────────────────────

@ConnectorBuilder("Slack Workspace")\
    .in_group("Slack")\
    .with_description("Sync messages and channels from Slack")\
    .with_categories(["Messaging"])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .with_auth([
        # AuthBuilder.type(AuthType.OAUTH).oauth(
        #     connector_name="Slack Workspace",
        #     authorize_url="https://slack.com/oauth/v2/authorize",
        #     token_url="https://slack.com/api/oauth.v2.access",
        #     redirect_uri="connectors/oauth/callback/Slack%20Workspace",
        #     scopes=OAuthScopeConfig(
        #         personal_sync=[],
        #         team_sync=[
        #             "channels:read",      "channels:history",
        #             "channels:join",
        #             "groups:read",        "groups:history",
        #             "mpim:read",          "mpim:history",
        #             "im:read",            "im:history",
        #             "users:read",         "users:read.email",
        #             "users.profile:read",
        #             "files:read",
        #             "usergroups:read",    "usergroups.users:read",
        #         ],
        #         agent=[]
        #     ),
        #     fields=[
        #         CommonFields.client_id("Slack App Console"),
        #         CommonFields.client_secret("Slack App Console"),
        #     ],
        #     icon_path="/assets/icons/connectors/slack.svg",
        #     app_group="Communication",
        #     app_description="OAuth application for accessing Slack workspace data",
        #     app_categories=["Messaging"],
        #     scope_parameter_name="user_scope",
        #     token_response_path="authed_user",
        # ),
        AuthBuilder.type(AuthType.API_TOKEN).fields([
            CommonFields.api_token("Bot Access Token")
        ]),
    ])\
    .configure(lambda b: b
        .with_icon("/assets/icons/connectors/slackworkspace.svg")
        .with_realtime_support(False)
        .add_documentation_link(DocumentationLink(
            "Slack API Setup", "https://api.slack.com/authentication/basics", "setup"))
        .add_documentation_link(DocumentationLink(
            "Pipeshub Docs", "https://docs.pipeshub.com/connectors/slack/slack", "pipeshub"))
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_agent_support(True)
        # Sync filters
        .add_filter_field(FilterField(
            name="channel_ids", display_name="Channel",
            description="Include or exclude specific channels",
            filter_type=FilterType.LIST, category=FilterCategory.SYNC,
            option_source_type=OptionSourceType.DYNAMIC))
        .add_filter_field(FilterField(
            name="channel_types", display_name="Channel Types",
            description="Filter by channel type (public, private)",
            filter_type=FilterType.MULTISELECT, category=FilterCategory.SYNC,
            options=["public_channel", "private_channel"],
            option_source_type=OptionSourceType.STATIC,
            default_value=["public_channel", "private_channel"]))
        .add_filter_field(FilterField(
            name="sync_window",
            display_name="Sync Messages From",
            description="How far back to sync messages on initial sync",
            filter_type=FilterType.DATETIME,
            category=FilterCategory.SYNC,
            default_operator=FilterOperator.LAST_30_DAYS))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        # Indexing filters
        .add_filter_field(FilterField(
            name="messages", display_name="Index Messages",
            filter_type=FilterType.BOOLEAN, category=FilterCategory.INDEXING,
            description="Enable indexing of messages", default_value=True))
        .add_filter_field(FilterField(
            name="threads", display_name="Index Threads",
            filter_type=FilterType.BOOLEAN, category=FilterCategory.INDEXING,
            description="Enable indexing of thread replies", default_value=True))
        .add_filter_field(FilterField(
            name="files", display_name="Index Files",
            filter_type=FilterType.BOOLEAN, category=FilterCategory.INDEXING,
            description="Enable indexing of files shared in messages", default_value=True))
        .add_filter_field(FilterField(
            name="links", display_name="Index Links",
            filter_type=FilterType.BOOLEAN, category=FilterCategory.INDEXING,
            description="Enable indexing of links shared in messages", default_value=True))
    )\
    .build_decorator()
class SlackConnector(BaseConnector):
    """
    Slack Connector — full graph-aware implementation.

    Three categories of caches live on the instance:
      user_id_to_email_cache      Slack user_id  → email
      user_id_to_internal_id_cache Slack user_id → internal AppUser._key
      channel_groups_cache         Slack channel_id → internal RecordGroup._key

    All caches are re-populated on every run_sync() so stale data never persists
    across runs.  Every downstream method receives a snapshot of the warm caches
    via ProcessingContext — mid-batch updates to the instance caches cannot affect
    in-flight processing.
    """

    # ── Constructor ────────────────────────────────────────────────────────────

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
            SlackWorkspaceApp(connector_id), logger,
            data_entities_processor, data_store_provider,
            config_service, connector_id,
            scope, created_by,
        )

        self.connector_id = connector_id
        self.external_client: Optional[SlackClient] = None
        self.data_source:     Optional[SlackDataSource] = None

        # Workspace identity (set in init())
        self.workspace_domain: Optional[str] = None
        self.team_id:          Optional[str] = None

        # ── Sync points ──────────────────────────────────────────────────────
        def _sp(t: SyncDataPointType) -> SyncPoint:
            return SyncPoint(
                connector_id=self.connector_id,
                org_id=self.data_entities_processor.org_id,
                sync_data_point_type=t,
                data_store_provider=self.data_store_provider,
            )

        # messages_sync_point  → RECORDS   : per-channel incremental cursor
        # audit_log_sync_point → RECORDS   : global change-detection watermark
        self.messages_sync_point  = _sp(SyncDataPointType.RECORDS)
        self.audit_log_sync_point = _sp(SyncDataPointType.RECORDS)

        # ── Rate limiter ─────────────────────────────────────────────────────
        self.rate_limiter = RateLimiter(logger=logger)

        # ── Workspace-wide caches ────────────────────────────────────────────
        # Populated during _sync_users(); re-populated on every run_sync().
        self.user_id_to_email_cache:       dict[str, str] = {}
        # Populated in _build_user_id_caches() after _sync_users().
        # Key: Slack user_id  Value: internal ArangoDB AppUser._key
        # Used to create MENTIONED_IN edges without per-message DB lookups.
        self.user_id_to_internal_id_cache: dict[str, str] = {}
        # Populated in _build_user_id_caches() after _sync_users().
        # Key: Slack user_id  Value: user full_name
        # Used to replace mentions in message content.
        self.user_id_to_name_cache:        dict[str, str] = {}
        # Populated during _sync_channels(); extended lazily.
        self.channel_groups_cache:         dict[str, str] = {}
        # Populated during _sync_channels().
        # Key: Slack channel_id  Value: channel name
        # Used to replace channel mentions in message content.
        self.channel_id_to_name_cache:     dict[str, str] = {}
        # Populated by channel cache refresh methods and used by get_filter_options().
        # Key: Slack channel_id  Value: {"id": str, "label": str}
        self.channel_filter_cache:         dict[str, dict[str, str]] = {}
        self._cache_rebuild_event:         Optional[asyncio.Event]   = None

        # ── Filter state ─────────────────────────────────────────────────────
        self.sync_filters:     FilterCollection = FilterCollection()
        self.indexing_filters: FilterCollection = FilterCollection()

    # =========================================================================
    # 0.  Helpers
    # =========================================================================

    _OPERATOR_SECONDS: dict[str, int] = {
        FilterOperator.LAST_7_DAYS:   7   * 86400,
        FilterOperator.LAST_14_DAYS:  14  * 86400,
        FilterOperator.LAST_30_DAYS:  30  * 86400,
        FilterOperator.LAST_90_DAYS:  90  * 86400,
        FilterOperator.LAST_180_DAYS: 180 * 86400,
        FilterOperator.LAST_365_DAYS: 365 * 86400,
    }

    def _compute_sync_window_oldest(self) -> Optional[str]:
        """
        Compute the Slack ``oldest`` ts from the sync_window DATETIME filter.

        The filter operator determines the time range:
          - LAST_7_DAYS … LAST_365_DAYS  → relative window
          - IS_AFTER                     → absolute custom start date

        Falls back to 30 days when no filter is configured.
        """
        sw = self.sync_filters.get("sync_window")
        if not sw:
            return f"{time.time() - 30 * 86400:.6f}"

        op = sw.operator_value
        secs = self._OPERATOR_SECONDS.get(op)
        # Guard against a 0 / negative entry slipping into _OPERATOR_SECONDS:
        # `time.time() - 0` would send "now" as Slack's `oldest` and silently
        # return zero messages.
        if secs and secs > 0:
            self.logger.info(f"Computing sync window oldest from seconds: {secs}")
            return f"{time.time() - secs:.6f}"

        if op == FilterOperator.IS_AFTER:
            start_epoch = sw.get_datetime_start()
            if start_epoch:
                return f"{start_epoch / 1000.0:.6f}"

        return f"{time.time() - 30 * 86400:.6f}"

    # =========================================================================
    # 0b. Initialisation
    # =========================================================================

    async def init(self) -> bool:
        """Build the Slack client and fetch workspace identity."""
        try:
            self.logger.info("🔧 Initialising Slack connector…")
            self.external_client = await SlackClient.build_from_services(
                logger=self.logger,
                config_service=self.config_service,
                connector_instance_id=self.connector_id,
            )
            self.data_source = SlackDataSource(self.external_client)

            ds = await self._fresh_datasource()
            resp = await ds.team_info()
            if resp and resp.success:
                team = resp.data.get("team", {})
                self.workspace_domain = team.get("domain")
                self.team_id = team.get("id")
                self.logger.info(f"Workspace: {self.workspace_domain} ({self.team_id})")

            self.logger.info("✅ Slack connector initialised")
            return True

        except Exception as exc:
            self.logger.error(f"❌ Init failed: {exc}", exc_info=True)
            return False

    async def _fresh_datasource(self) -> SlackDataSource:
        """Return a SlackDataSource backed by the always-current OAuth token."""
        if not self.external_client:
            raise RuntimeError("Call init() first.")

        cfg = await self.config_service.get_config(
            f"/services/connectors/{self.connector_id}/config"
        )
        if not cfg:
            raise RuntimeError("Connector config not found.")

        auth = cfg.get("auth", {}) or {}
        auth_type = auth.get("authType", "API_TOKEN")
        if auth_type == "OAUTH":
            credentials = cfg.get("credentials", {}) or {}
            token = credentials.get("access_token", "")
        else:
            token = auth.get("apiToken", "")
        if not token:
            raise RuntimeError("No access token in config.")

        client = self.external_client.get_client()
        if getattr(client, "get_token", lambda: None)() != token:
            if hasattr(client, "set_token"):
                client.set_token(token)

        return SlackDataSource(self.external_client)

    # =========================================================================
    # 1.  Main orchestration
    # =========================================================================

    async def run_sync(self) -> None:
        """
        Full workspace synchronisation.

        Phase 1 — Users       (_sync_users)
        Phase 2 — UserGroups  (_sync_user_groups)
        Phase 3 — Channels    (_sync_channels)          → RecordGroups + HAS_PERMISSION edges
        Phase 4 — Messages    (sync_channel_messages)   → per-RecordGroup checkpoint
        Phase 5 — Changes     (_sync_message_changes)   → AUDIT_LOG checkpoint
        """
        try:
            self.logger.info(f"🚀 Slack sync — org {self.data_entities_processor.org_id}")

            if not self.external_client:
                raise RuntimeError("Call init() first.")

            self.sync_filters, self.indexing_filters = await load_connector_filters(
                self.config_service, "slack_workspace", self.connector_id, self.logger
            )

            # Phase 1 — Users (warms all user caches as a side-effect)
            await self._sync_users()

            # Phase 1b — Workspace roles (AppRole for permission resolution)
            await self._sync_workspace_roles()

            # Phase 2 — User groups
            await self._sync_user_groups()

            # Phase 3 — Channels → RecordGroups + HAS_PERMISSION
            channels = await self._sync_channels()

            # Phase 4 — Messages per channel (per-RecordGroup sync point)
            for rg in channels:
                if rg.external_group_id:
                    await self.sync_channel_messages(rg.external_group_id)

            # Phase 5 — Thread-growth detection (edit detection bypassed for now)
            await self._sync_thread_growth()

            self.logger.info("✅ Slack sync complete")

        except Exception as exc:
            self.logger.error(f"❌ Sync failed: {exc}", exc_info=True)
            raise

    # =========================================================================
    # 2.  Phase 1 — User sync
    # =========================================================================

    async def _sync_users(self) -> None:
        """
        Sync all human workspace members.

        Side-effects (both caches re-built from scratch every run):
          • user_id_to_email_cache        populated inline
          • user_id_to_internal_id_cache  populated by _build_user_id_caches()
                                          called once after all pages are done
          • _non_guest_app_users          populated for workspace role sync
        """
        self.logger.info("👥 Syncing users…")

        # Clear caches so stale data from a previous run never persists
        self.user_id_to_email_cache.clear()
        self.user_id_to_internal_id_cache.clear()
        self.user_id_to_name_cache.clear()
        self._non_guest_app_users: list[AppUser] = []

        cursor: Optional[str] = None
        synced = skipped = 0

        while True:
            await self.rate_limiter.acquire()
            ds = await self._fresh_datasource()
            resp = await ds.users_list(limit=PAGE_SIZE_USERS, cursor=cursor)

            if not resp or not resp.success:
                self.logger.error(f"❌ users.list failed: {getattr(resp, 'error', 'no response')}")
                break

            members = resp.data.get("members", [])
            if not members:
                break

            batch: list[AppUser] = []
            for m in members:
                # Skip bots, deleted users, Slack internals
                if m.get("is_bot") or m.get("deleted") or m.get("id") == "USLACKBOT":
                    skipped += 1
                    continue

                profile = m.get("profile", {})
                email = (profile.get("email") or "").strip()
                if not email:
                    self.logger.debug(f"Skipping {m.get('name')}: no email")
                    skipped += 1
                    continue

                app_user = self._to_app_user(m)
                if app_user:
                    batch.append(app_user)
                    self.user_id_to_email_cache[m["id"]] = email
                    if app_user.full_name:
                        self.user_id_to_name_cache[m["id"]] = app_user.full_name

                    # Track non-guest users for workspace role
                    is_guest = m.get("is_restricted", False) or m.get("is_ultra_restricted", False)
                    if not is_guest:
                        self._non_guest_app_users.append(app_user)

            if batch:
                await self.data_entities_processor.on_new_app_users(batch)
                synced += len(batch)

            cursor = resp.data.get("response_metadata", {}).get("next_cursor", "")
            if not cursor:
                break

        # After all pages, build the internal-ID cache for fast mention resolution
        await self._build_user_id_caches()

        self.logger.info(f"✅ Users — synced: {synced}, skipped: {skipped}")

    def _to_app_user(self, m: dict[str, Any]) -> Optional[AppUser]:
        uid    = m.get("id")
        profile = m.get("profile", {})
        email  = (profile.get("email") or "").strip()
        updated_at = m.get("updated", get_epoch_timestamp_in_ms())
        if not uid or not email:
            return None

        name = (
            profile.get("real_name_normalized")
            or profile.get("real_name")
            or profile.get("display_name_normalized")
            or profile.get("display_name")
            or email
        )
        try:
            return AppUser(
                app_name=Connectors.SLACK_WORKSPACE,
                connector_id=self.connector_id,
                source_user_id=uid,
                org_id=self.data_entities_processor.org_id,
                email=email,
                full_name=name,
                is_active=not m.get("deleted", False),
                created_at=updated_at,
                updated_at=updated_at,
            )
        except Exception as exc:
            self.logger.error(f"_to_app_user failed for {uid}: {exc}")
            return None

    async def _build_user_id_caches(self) -> None:
        """
        Query the DB for all synced AppUsers and build user_id_to_internal_id_cache.

        Called once after all user pages are processed.  This is a single DB
        call — cheap compared to per-message lookups during mention resolution.
        """
        try:
            all_users = await self.data_entities_processor.get_all_app_users(
                connector_id=self.connector_id
            )
            self.user_id_to_internal_id_cache.clear()
            for u in all_users:
                if u.source_user_id and u.id:
                    self.user_id_to_internal_id_cache[u.source_user_id] = u.id
                    if u.source_user_id not in self.user_id_to_email_cache and u.email:
                        self.user_id_to_email_cache[u.source_user_id] = u.email
                    # Populate name cache if not already set
                    if u.source_user_id not in self.user_id_to_name_cache and u.full_name:
                        self.user_id_to_name_cache[u.source_user_id] = u.full_name
            self.logger.debug(
                f"user_id_to_internal_id_cache warmed: {len(self.user_id_to_internal_id_cache)} entries"
            )
        except Exception as exc:
            self.logger.error(f"_build_user_id_caches failed: {exc}", exc_info=True)

    # =========================================================================
    # 2b. Workspace roles (AppRole)
    # =========================================================================

    async def _sync_workspace_roles(self) -> None:
        """
        Create/update a ``workspace_member`` AppRole containing all non-guest
        users (owners, admins, regular members).  This role is referenced in
        channel permissions so that public channels don't need per-user edges.
        """
        non_guests = getattr(self, "_non_guest_app_users", [])
        if not non_guests:
            self.logger.info("ℹ️ No non-guest users to sync workspace role for")
            return

        role = AppRole(
            app_name=Connectors.SLACK_WORKSPACE,
            connector_id=self.connector_id,
            source_role_id="workspace_member",
            name="Workspace Member",
            org_id=self.data_entities_processor.org_id,
        )
        await self.data_entities_processor.on_new_app_roles([(role, non_guests)])
        self.logger.info(
            f"✅ Workspace role synced — {len(non_guests)} non-guest members"
        )

    # =========================================================================
    # 3.  Phase 2 — User group sync
    # =========================================================================

    async def _sync_user_groups(self) -> None:
        """Sync Slack usergroups and their members."""
        self.logger.info("👥 Syncing user groups…")
        try:
            await self.rate_limiter.acquire()
            ds = await self._fresh_datasource()
            resp = await ds.usergroups_list(include_users=True)

            if not resp or not resp.success:
                self.logger.warning(
                    f"⚠️  Could not fetch usergroups (scope missing?): "
                    f"{getattr(resp, 'error', 'no response')}"
                )
                return

            groups = resp.data.get("usergroups", [])
            if not groups:
                self.logger.info("No usergroups found.")
                return

            # Build quick source_id → AppUser lookup from the internal-ID cache
            # (avoids a second get_all_app_users call — we already built the cache)
            source_id_to_user: dict[str, AppUser] = {}
            try:
                all_users = await self.data_entities_processor.get_all_app_users(
                    connector_id=self.connector_id
                )
                source_id_to_user = {
                    u.source_user_id: u for u in all_users if u.source_user_id
                }
            except Exception as exc:
                self.logger.error(f"Could not load users for group sync: {exc}")

            batch: list[tuple[AppUserGroup, list[AppUser]]] = []
            g_synced = m_synced = 0

            for gd in groups:
                ug = self._to_user_group(gd)
                if not ug:
                    continue

                member_ids: list[str] = gd.get("users", [])
                if not member_ids:
                    member_ids = await self._fetch_usergroup_members(gd.get("id", ""))

                members: list[AppUser] = []
                for uid in member_ids:
                    user = source_id_to_user.get(uid)
                    if user:
                        members.append(user)
                    else:
                        self.logger.debug(
                            f"User {uid} in group {gd.get('handle')} not in synced users"
                        )

                batch.append((ug, members))
                g_synced += 1
                m_synced += len(members)

            if batch:
                await self.data_entities_processor.on_new_user_groups(batch)

            self.logger.info(
                f"✅ User groups — groups: {g_synced}, memberships: {m_synced}"
            )

        except Exception as exc:
            self.logger.error(f"❌ User group sync failed: {exc}", exc_info=True)
            raise

    def _to_user_group(self, gd: dict[str, Any]) -> Optional[AppUserGroup]:
        gid  = gd.get("id")
        name = gd.get("name") or gd.get("handle")
        date_create = gd.get("date_create")
        date_update = gd.get("date_update")

        if date_create:
            date_create = int(float(date_create) * 1000)
        if date_update:
            date_update = int(float(date_update) * 1000)

        if not gid or not name:
            return None
        try:
            return AppUserGroup(
                app_name=Connectors.SLACK_WORKSPACE,
                connector_id=self.connector_id,
                source_user_group_id=gid,
                name=name,
                org_id=self.data_entities_processor.org_id,
                description=gd.get("description", ""),
                source_created_at=date_create,
                source_updated_at=date_update,
            )
        except Exception as exc:
            self.logger.error(f"_to_user_group failed for {gid}: {exc}")
            return None

    async def _fetch_usergroup_members(self, group_id: str) -> list[str]:
        try:
            await self.rate_limiter.acquire()
            ds = await self._fresh_datasource()
            r  = await ds.usergroups_users_list(usergroup=group_id)
            return r.data.get("users", []) if r and r.success else []
        except Exception as exc:
            self.logger.error(f"_fetch_usergroup_members({group_id}): {exc}")
            return []

    # =========================================================================
    # 4.  Phase 3 — Channel sync → RecordGroups + HAS_PERMISSION edges
    # =========================================================================

    async def _sync_channels(self) -> list[RecordGroup]:
        """
        Sync every channel type → RecordGroup.
        Builds HAS_PERMISSION edges based on channel accessibility:

        Public channel  → workspace_member AppRole gets READ permission
        Private channel → members from conversations.members get READ permission
        IM (DM)         → the other user (channel_data['user']) gets READ permission
        MPIM (group DM) → members from conversations.members get READ permission
        """
        self.logger.info("📢 Syncing channels…")

        # Apply channel_ids filter
        ch_filter = self.sync_filters.get(SyncFilterKey.CHANNEL_IDS)
        include_ids: Optional[set[str]] = None
        exclude_ids: Optional[set[str]] = None
        if ch_filter is not None:
            op = ch_filter.get_operator()
            if op == FilterOperator.IN:
                include_ids = set(ch_filter.get_value() or [])
            elif op == FilterOperator.NOT_IN:
                exclude_ids = set(ch_filter.get_value() or [])

        # Apply channel_types filter (DMs not supported — only public, private channels)
        ch_types_filter = self.sync_filters.get(SyncFilterKey.CHANNEL_TYPES)
        allowed_types: Optional[set[str]] = None
        if ch_types_filter is not None:
            allowed_types = set(ch_types_filter.get_value() or [])
        if allowed_types:
            allowed_types = allowed_types - {"im", "mpim"}
        types_param = ",".join(allowed_types) if allowed_types else "public_channel,private_channel"

        # Clear channel caches so stale entries from a previous sync (with different
        # channel filters) never bleed into Phase 5 (thread-growth / change detection)
        self.channel_groups_cache.clear()
        self.channel_id_to_name_cache.clear()

        cursor: Optional[str] = None
        record_groups: list[RecordGroup] = []
        total = 0

        while True:
            await self.rate_limiter.acquire()
            ds   = await self._fresh_datasource()
            resp = await ds.conversations_list(
                exclude_archived=True,
                types=types_param,
                limit=PAGE_SIZE_CHANNELS,
                cursor=cursor,
            )

            if not resp or not resp.success:
                self.logger.error(
                    f"❌ conversations.list failed: {getattr(resp, 'error', 'no response')}"
                )
                break

            data = resp.data
            channels: list[dict[str, Any]] = data.get("channels", [])
            if not channels:
                break

            # Apply channel_ids filter
            if exclude_ids:
                channels = [c for c in channels if c.get("id") not in exclude_ids]

            batch: list[tuple[RecordGroup, list[Permission]]] = []

            for cd in channels:
                cid = cd.get("id")
                if not cid:
                    continue
                if include_ids and cid not in include_ids:
                    continue

                try:
                    rg = self._to_channel_record_group(cd)
                    if not rg:
                        continue

                    if rg.name:
                        self.channel_id_to_name_cache[cid] = rg.name

                    permissions = await self._channel_permissions(cd)

                    batch.append((rg, permissions))
                    record_groups.append(rg)
                    total += 1

                except Exception as exc:
                    self.logger.error(
                        f"❌ Failed to process channel {cd.get('name')}: {exc}"
                    )

            if batch:
                await self.data_entities_processor.on_new_record_groups(batch)
                for rg_item, _ in batch:
                    ext_id = rg_item.external_group_id
                    if ext_id and rg_item.id:
                        self.channel_groups_cache[ext_id] = rg_item.id
                self.logger.info(f"Synced batch of {len(batch)} channels")

            cursor = data.get("response_metadata", {}).get("next_cursor", "")
            if not cursor:
                break

        self.logger.info(f"✅ Channels — {total} synced")
        return record_groups

    # ── Permission helpers ─────────────────────────────────────────────────────

    async def _channel_permissions(
        self,
        channel_data: dict[str, Any],
    ) -> list[Permission]:
        """
        Return the correct HAS_PERMISSION list for a single channel.

        Public channel  → workspace_member AppRole (efficient single edge)
        Private channel → explicit member list from conversations.members
        IM              → the remote user (channel_data['user'])
        MPIM            → explicit member list from conversations.members
        """
        cid = channel_data.get("id", "")

        # Direct message — only the other user needs permission
        if channel_data.get("is_im"):
            remote_uid = channel_data.get("user", "")
            email = self.user_id_to_email_cache.get(remote_uid)
            if email:
                return [Permission(
                    email=email,
                    entity_type=EntityType.USER,
                    type=PermissionType.READ,
                )]
            return []

        # Public channel — use workspace_member AppRole
        if not channel_data.get("is_private") and not channel_data.get("is_mpim"):
            return [Permission(
                entity_type=EntityType.ROLE,
                external_id="workspace_member",
                type=PermissionType.READ,
            )]

        # Private channel or group DM — resolve explicit member list
        member_ids = await self._fetch_channel_members(cid)
        perms: list[Permission] = []
        for uid in member_ids:
            email = self.user_id_to_email_cache.get(uid)
            if email:
                perms.append(Permission(
                    email=email,
                    entity_type=EntityType.USER,
                    type=PermissionType.READ,
                ))
        return perms

    async def _fetch_channel_members(self, channel_id: str) -> list[str]:
        """
        Return all member Slack user IDs for a channel via conversations.members
        with full cursor-based pagination.
        """
        members: list[str] = []
        cursor: Optional[str] = None

        while True:
            try:
                await self.rate_limiter.acquire()
                ds   = await self._fresh_datasource()
                resp = await ds.conversations_members(
                    channel=channel_id,
                    limit=PAGE_SIZE_MEMBERS,
                    cursor=cursor,
                )

                if not resp or not resp.success:
                    # Connector may lack membership scope — log and bail
                    error = getattr(resp, "error", "no response")
                    if error in ("channel_not_found", "not_in_channel", "missing_scope"):
                        self.logger.debug(
                            f"Cannot fetch members for {channel_id}: {error}"
                        )
                    else:
                        self.logger.warning(
                            f"conversations.members({channel_id}) failed: {error}"
                        )
                    break

                members.extend(resp.data.get("members", []))
                cursor = resp.data.get("response_metadata", {}).get("next_cursor", "")
                if not cursor:
                    break

            except Exception as exc:
                self.logger.error(
                    f"_fetch_channel_members({channel_id}): {exc}", exc_info=True
                )
                break

        return members

    def _to_channel_record_group(
        self, cd: dict[str, Any]
    ) -> Optional[RecordGroup]:
        cid = cd.get("id")
        if not cid:
            return None

        name = cd.get("name", "")

        if cd.get("is_im"):
            uid   = cd.get("user", "")
            if uid == "USLACKBOT":
                display = "Slackbot"
            else:
                display = (
                    self.user_id_to_name_cache.get(uid)
                    or self.user_id_to_email_cache.get(uid)
                    or uid
                    or cid
                )
            name  = f"DM: {display}"
        elif cd.get("is_mpim"):
            name = name or f"Group DM: {cid}"

        web_url = (
            f"https://{self.workspace_domain}.slack.com/archives/{cid}"
            if self.workspace_domain else None
        )
        created_ts = cd.get("created")
        src_ts = int(float(created_ts) * 1000) if created_ts else None
        updated_ts = cd.get("updated")
        upd_ts = int(float(updated_ts) * 1000) if updated_ts else None
        current_ts = get_epoch_timestamp_in_ms()

        try:
            return RecordGroup(
                org_id=self.data_entities_processor.org_id,
                name=name or f"Channel {cid}",
                short_name=name or cid,
                description=(
                    cd.get("topic", {}).get("value", "")
                    if not cd.get("is_im") else ""
                ),
                external_group_id=cid,
                connector_name=Connectors.SLACK_WORKSPACE,
                connector_id=self.connector_id,
                group_type=RecordGroupType.SLACK_CHANNEL,
                web_url=web_url,
                created_at=current_ts,
                updated_at=current_ts,
                source_created_at=src_ts,
                source_updated_at=upd_ts,
                inherit_permissions=True,
            )
        except Exception as exc:
            self.logger.error(f"_to_channel_record_group({cid}): {exc}")
            return None

    # =========================================================================
    # 5.  Phase 4 — Messages (per-RecordGroup / per-channel sync point)
    # =========================================================================

    async def sync_channel_messages(self, channel_id: str) -> None:
        """
        Incremental message sync for one channel.

        Checkpoint key:  generate_record_sync_point_key(MESSAGE, "slack_messages", channel_id)
        Checkpoint value: {"last_sync_time": "<slack ts of newest message>"}

        The checkpoint is written ONLY after ALL pages succeed, guaranteeing
        at-least-once delivery even if the process crashes mid-channel.
        """
        self.logger.info(f"📨 Syncing messages — channel: {channel_id}")

        # Per-channel (per-RecordGroup) sync point
        sync_key      = generate_record_sync_point_key(
            RecordType.MESSAGE.value, "slack_messages", channel_id
        )
        last_data     = await self.messages_sync_point.read_sync_point(sync_key)
        last_ts: Optional[str] = last_data.get("last_sync_time") if last_data else None

        # On first sync (no checkpoint), apply the sync_window filter
        if not last_ts:
            last_ts = self._compute_sync_window_oldest()

        self.logger.info(
            f"  {'Incremental from ts=' + last_ts if last_ts else 'Full sync (no window limit)'}"
        )

        ctx = ProcessingContext(
            channel_id=channel_id,
            channel_groups_map=await self._channel_group_map([channel_id]),
            user_id_to_email=dict(self.user_id_to_email_cache),   # snapshot
            user_id_to_name=dict(self.user_id_to_name_cache),     # snapshot
            channel_id_to_name=dict(self.channel_id_to_name_cache), # snapshot
            rate_limiter=self.rate_limiter,
        )

        msg_enabled  = self.indexing_filters.is_enabled(IndexingFilterKey.MESSAGES)
        file_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.FILES)
        link_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.LINKS)

        cursor:     Optional[str] = None
        newest_ts:  Optional[str] = None   # newest across ALL pages (Slack newest-first)
        total_msgs = 0
        all_deferred_threads: list[DeferredThread] = []
        join_attempted = False

        while True:
            await self.rate_limiter.acquire()
            ds   = await self._fresh_datasource()
            resp = await ds.conversations_history(
                channel=channel_id,
                oldest=last_ts,          # exclusive lower bound → only new messages
                limit=PAGE_SIZE_MESSAGES,
                cursor=cursor,
            )

            if not resp or not resp.success:
                error = getattr(resp, "error", "no response")
                if error == "not_in_channel" and not join_attempted:
                    # Bot is not a member — try joining (works for public channels)
                    self.logger.info(
                        f"Bot not in channel {channel_id}, attempting to join…"
                    )
                    join_ds = await self._fresh_datasource()
                    join_resp = await join_ds.conversations_join(channel=channel_id)
                    if join_resp and join_resp.success:
                        self.logger.info(
                            f"✅ Joined channel {channel_id}, retrying sync…"
                        )
                        join_attempted = True
                        continue
                    else:
                        join_err = getattr(join_resp, "error", "unknown")
                        self.logger.warning(
                            f"Could not join channel {channel_id} ({join_err}), skipping."
                        )
                        break
                elif error == "channel_not_found":
                    self.logger.warning(
                        f"Skipping channel {channel_id}: {error}"
                    )
                    break
                elif error == "not_in_channel":
                    # Already attempted join but still failing
                    self.logger.warning(
                        f"Skipping channel {channel_id}: still not_in_channel after join attempt"
                    )
                    break
                else:
                    self.logger.error(
                        f"❌ conversations.history({channel_id}) failed: {error}"
                    )
                    break

            msgs: list[dict[str, Any]] = resp.data.get("messages", [])
            if not msgs:
                break

            # Slack returns newest-first; first message of the first page is the global newest
            if newest_ts is None:
                newest_ts = msgs[0].get("ts")

            records, deferred_threads = await self._process_message_batch(msgs, ctx)

            # Apply indexing filters
            for rec, _ in records:
                if rec.record_type == RecordType.MESSAGE and not msg_enabled:
                    rec.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
                elif rec.record_type == RecordType.FILE and not file_enabled:
                    rec.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
                elif rec.record_type == RecordType.LINK and not link_enabled:
                    rec.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

            await self._enrich_link_records_linked_id(records)
            if records:
                await self.data_entities_processor.on_new_records(records)

                total_msgs += sum(
                    1 for r, _ in records if r.record_type == RecordType.MESSAGE
                )

            all_deferred_threads.extend(deferred_threads)

            cursor = resp.data.get("response_metadata", {}).get("next_cursor", "")
            if not cursor:
                break

        # Process deferred threads after all channel records have been published.
        # Full vs. incremental is gated on the per-thread sync_point, not on RG-node
        # existence: a full sync deletes sync_points and sync edges but keeps RG
        # nodes, so gating on RG existence would silently take the incremental
        # path on full sync and leave the thread RG's BELONGS_TO /
        # INHERIT_PERMISSIONS / PERMISSION edges unrecreated.
        # Sync_point absent ⇒ _process_thread (re-emits the RG and refetches all
        # replies, recreating every edge through on_new_record_groups /
        # on_new_records).
        if all_deferred_threads:
            self.logger.info(
                f"📨 Processing {len(all_deferred_threads)} deferred threads for channel {channel_id}"
            )
            for dt in all_deferred_threads:
                ts = dt.msg.get("ts", "")
                thread_sp_key = generate_record_sync_point_key(
                    RecordType.MESSAGE.value, "slack_thread",
                    f"{dt.ctx.channel_id}_{ts}",
                )
                try:
                    sp_data = await self.audit_log_sync_point.read_sync_point(
                        thread_sp_key
                    )
                    last_reply_ts = (sp_data or {}).get("last_reply_ts")

                    existing_rg_id = ""
                    if last_reply_ts:
                        try:
                            async with self.data_store_provider.transaction() as tx:
                                existing_rg = await tx.get_record_group_by_external_id(
                                    external_id=f"thread_{dt.ctx.channel_id}_{ts}",
                                    connector_id=self.connector_id,
                                )
                                if existing_rg:
                                    existing_rg_id = existing_rg.id or ""
                        except Exception as exc:
                            self.logger.debug(
                                f"[DeferredThread] RG lookup for thread {ts}: {exc}"
                            )

                    # No checkpoint, or checkpoint exists but the RG node was
                    # removed (orphan sync_point) ⇒ full path so the RG is
                    # rebuilt and edges reasserted.
                    if not last_reply_ts or not existing_rg_id:
                        self.logger.info(
                            f"[DeferredThread] Processing thread parent ts={ts}, "
                            f"reply_count={dt.msg.get('reply_count')}, channel={dt.ctx.channel_id} (full)"
                        )
                        await self._process_thread(dt.msg, dt.ctx, dt.rg_id)
                    else:
                        self.logger.info(
                            f"[DeferredThread] Processing thread parent ts={ts}, "
                            f"reply_count={dt.msg.get('reply_count')}, channel={dt.ctx.channel_id} (incremental)"
                        )
                        await self._process_thread_incremental(
                            dt.msg, dt.ctx, existing_rg_id, last_reply_ts
                        )

                    latest_reply = dt.msg.get("latest_reply") or ts
                    await self.audit_log_sync_point.update_sync_point(
                        thread_sp_key, {"last_reply_ts": latest_reply}
                    )
                except Exception as exc:
                    self.logger.error(
                        f"[DeferredThread] Thread processing FAILED for ts={ts}, "
                        f"channel={dt.ctx.channel_id}: {exc}",
                        exc_info=True,
                    )

        # Persist checkpoint ONLY after all pages succeed
        if newest_ts:
            await self.messages_sync_point.update_sync_point(
                sync_key, {"last_sync_time": newest_ts}
            )
            self.logger.info(f"📍 Checkpoint updated for {channel_id}: ts={newest_ts}")

        self.logger.info(f"✅ {total_msgs} messages synced for {channel_id}")

    # ── Message batch processing ───────────────────────────────────────────────

    async def _process_message_batch(
        self,
        msgs: list[dict[str, Any]],
        ctx: ProcessingContext,
    ) -> tuple[list[tuple[Record, list[Permission]]], list[DeferredThread]]:
        out: list[tuple[Record, list[Permission]]] = []
        deferred_threads: list[DeferredThread] = []
        for burst in self._detect_bursts(msgs):
            if burst.message_count > 1:
                recs, threads = await self._process_burst(burst, ctx)
                out.extend(recs)
                deferred_threads.extend(threads)
            else:
                recs, threads = await self._process_single(burst.messages[0], ctx)
                out.extend(recs)
                deferred_threads.extend(threads)
        return out, deferred_threads

    # =========================================================================
    # 5b.  Block-building helpers
    # =========================================================================

    def _build_message_block(
        self,
        msg: dict[str, Any],
        block_index: int,
        parent_bg_index: int,
        ctx: ProcessingContext,
        children_records: Optional[list[ChildRecord]] = None,
    ) -> Block:
        """Build a single TEXT Block from a Slack message dict.

        The block ``data`` field is prefixed with the author's display name
        so that each message within a multi-user burst is attributable.
        """
        ts   = msg.get("ts", "")
        text = self._replace_mentions_in_text(
            msg.get("text", ""),
            ctx.user_id_to_name,
            ctx.channel_id_to_name,
        )

        # Prefix the message content with the author name
        user_id = msg.get("user", "")
        author_name = ctx.user_id_to_name.get(user_id) or ctx.user_id_to_email.get(user_id) or user_id
        if author_name and text:
            block_data = f"**{author_name}**: {text}"
        elif author_name:
            block_data = f"**{author_name}**:"
        else:
            block_data = text or ""

        src_ts_ms = int(float(ts) * 1000) if ts else None
        is_edited = "edited" in msg
        upd_ts_ms = src_ts_ms
        if is_edited:
            raw_ets = msg.get("edited", {}).get("ts")
            if raw_ets:
                upd_ts_ms = int(float(raw_ets) * 1000)

        from datetime import timezone as _tz
        src_dt = (
            datetime.fromtimestamp(src_ts_ms / 1000, tz=_tz.utc)
            if src_ts_ms else None
        )
        upd_dt = (
            datetime.fromtimestamp(upd_ts_ms / 1000, tz=_tz.utc)
            if upd_ts_ms else None
        )

        try:
            weburl_str = self._message_url(ctx.channel_id, ts)
        except Exception:
            weburl_str = None

        # Append attachment and link metadata to block data so they are stored in blob
        # and included in indexing/retrieval (vectorstore uses block.data only).
        if children_records:
            att_parts = [
                f"{cr.child_name or 'attachment'} (record_id: {cr.child_id})"
                for cr in children_records
            ]
            block_data = f"{block_data}\n\nAttachments: {', '.join(att_parts)}"
        urls = self._extract_urls(msg.get("text", ""))
        if urls:
            block_data = f"{block_data}\n\nLinks: {', '.join(urls)}"

        return Block(
            index=block_index,
            parent_index=parent_bg_index,
            type=BlockType.TEXT,
            sub_type=BlockSubType.MESSAGE,
            format=DataFormat.MARKDOWN,
            data=block_data,
            source_id=ts,
            source_name=author_name or None,
            source_creation_date=src_dt,
            source_update_date=upd_dt,
            weburl=weburl_str,
            children_records=children_records,
        )

    def _build_burst_block_containers(
        self,
        messages: list[dict[str, Any]],
        burst_id: str,
        ctx: ProcessingContext,
        file_child_records: Optional[dict[str, list[ChildRecord]]] = None,
        thread_ts: Optional[str] = None,
    ) -> BlocksContainer:
        """
        Build a BlocksContainer for a burst record.

        Structure:
          block_groups[0]: CONVERSATION / BURST  (covers all blocks via IndexRange)
          blocks[i]:       one TEXT block per message

        ``thread_ts`` should be set when the burst lives inside a thread, so the
        BlockGroup ``weburl`` opens the thread side-panel on the first reply.
        """
        first_ts = messages[0].get("ts", "") if messages else ""
        try:
            first_url = self._message_url(ctx.channel_id, first_ts, thread_ts=thread_ts)
        except Exception:
            first_url = None

        blocks: list[Block] = []
        for i, msg in enumerate(messages):
            ts = msg.get("ts", "")
            cr = (file_child_records or {}).get(ts)
            block = self._build_message_block(msg, i, 0, ctx, children_records=cr)
            blocks.append(block)

        n = len(blocks)
        bg = BlockGroup(
            index=0,
            type=GroupType.TEXT_SECTION,
            sub_type=GroupSubType.BURST,
            source_group_id=burst_id,
            weburl=first_url,
            children=BlockGroupChildren(
                block_ranges=[IndexRange(start=0, end=n - 1)] if n > 0 else []
            ),
        )

        return BlocksContainer(block_groups=[bg], blocks=blocks)

    def _build_single_block_containers(
        self,
        msg: dict[str, Any],
        ctx: ProcessingContext,
        children_records: Optional[list[ChildRecord]] = None,
    ) -> BlocksContainer:
        """
        Build a BlocksContainer for a single message record.

        Structure:
          block_groups[0]: CONVERSATION / SINGLE_MESSAGE
          blocks[0]:       one TEXT block
        """
        ts = msg.get("ts", "")
        try:
            url = self._message_url(ctx.channel_id, ts)
        except Exception:
            url = None

        block = self._build_message_block(msg, 0, 0, ctx, children_records=children_records)

        bg = BlockGroup(
            index=0,
            type=GroupType.CONVERSATION,
            sub_type=GroupSubType.SINGLE_MESSAGE,
            source_group_id=ts,
            weburl=url,
            children=BlockGroupChildren(
                block_ranges=[IndexRange(start=0, end=0)]
            ),
        )
        return BlocksContainer(block_groups=[bg], blocks=[block])

    def _build_thread_block_containers(
        self,
        parent_msg: dict[str, Any],
        replies: list[dict[str, Any]],
        ctx: ProcessingContext,
        file_child_records: Optional[dict[str, list[ChildRecord]]] = None,
    ) -> BlocksContainer:
        """
        Build a BlocksContainer for a thread-burst record (parent + replies).

        Structure:
          block_groups[0]: CONVERSATION / THREAD  (covers all blocks)
          blocks[0]:       parent message
          blocks[1..N]:    reply messages
        """
        thread_ts = parent_msg.get("ts", "")
        try:
            thread_url = self._message_url(ctx.channel_id, thread_ts)
        except Exception:
            thread_url = None

        all_msgs  = [parent_msg] + replies
        blocks: list[Block] = []
        for i, msg in enumerate(all_msgs):
            ts = msg.get("ts", "")
            cr = (file_child_records or {}).get(ts)
            block = self._build_message_block(msg, i, 0, ctx, children_records=cr)
            blocks.append(block)

        n = len(blocks)
        bg = BlockGroup(
            index=0,
            type=GroupType.CONVERSATION,
            sub_type=GroupSubType.THREAD,
            source_group_id=thread_ts,
            weburl=thread_url,
            children=BlockGroupChildren(
                block_ranges=[IndexRange(start=0, end=n - 1)] if n > 0 else []
            ),
        )
        return BlocksContainer(block_groups=[bg], blocks=blocks)

    # =========================================================================
    # 5c.  Thread record-group / record builder
    # =========================================================================

    def _create_thread_record_group(
        self,
        parent_msg: dict[str, Any],
        ctx: ProcessingContext,
        parent_rg_id: Optional[str],
    ) -> Optional[RecordGroup]:
        """Create a SLACK_THREAD RecordGroup for a thread-parent message."""
        thread_ts = parent_msg.get("ts", "")
        if not thread_ts:
            self.logger.warning(
                f"[ThreadRG] Skipping — parent_msg has no ts, channel={ctx.channel_id}"
            )
            return None

        channel_name = ctx.channel_id_to_name.get(ctx.channel_id, ctx.channel_id)
        name = f"{channel_name}_{self._slack_ts_to_utc_label(thread_ts)}"

        try:
            url = self._message_url(ctx.channel_id, thread_ts)
        except Exception:
            url = None

        src_ts_ms = int(float(thread_ts) * 1000)
        now = get_epoch_timestamp_in_ms()
        ext_group_id = f"thread_{ctx.channel_id}_{thread_ts}"

        try:
            rg = RecordGroup(
                org_id=self.data_entities_processor.org_id,
                name=name,
                external_group_id=ext_group_id,
                connector_name=Connectors.SLACK_WORKSPACE,
                connector_id=self.connector_id,
                group_type=RecordGroupType.SLACK_THREAD,
                web_url=url,
                created_at=now,
                updated_at=now,
                source_created_at=src_ts_ms,
                source_updated_at=src_ts_ms,
                parent_external_group_id=ctx.channel_id,
                inherit_permissions=True,
            )
            self.logger.info(
                f"[ThreadRG] Built SLACK_THREAD RecordGroup: "
                f"ext_id={ext_group_id}, parent_ext_id={ctx.channel_id}, "
                f"name={name[:60]}, auto_id={rg.id}"
            )
            return rg
        except Exception as exc:
            self.logger.error(
                f"[ThreadRG] Failed to build RecordGroup for thread_ts={thread_ts}, "
                f"channel={ctx.channel_id}: {exc}",
                exc_info=True,
            )
            return None

    async def _fetch_thread_replies_raw(
        self,
        channel_id: str,
        thread_ts: str,
        ctx: ProcessingContext,
        oldest: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Fetch raw thread replies (excluding the parent) via conversations.replies.

        Args:
            oldest: If set, only replies with ts > oldest are returned
                    (Slack ``oldest`` is exclusive lower bound).
        """
        replies: list[dict[str, Any]] = []
        cursor:  Optional[str] = None
        seen:    set[str] = set()

        while True:
            await ctx.rate_limiter.acquire()
            ds   = await self._fresh_datasource()
            kwargs: dict[str, Any] = dict(
                channel=channel_id, ts=thread_ts,
                limit=PAGE_SIZE_THREADS, cursor=cursor,
            )
            if oldest:
                kwargs["oldest"] = oldest
            resp = await ds.conversations_replies(**kwargs)
            if not resp or not resp.success:
                break

            for md in resp.data.get("messages", []):
                ts = md.get("ts")
                if not ts or ts == thread_ts or ts in seen:
                    continue
                seen.add(ts)
                replies.append(md)

            cursor = resp.data.get("response_metadata", {}).get("next_cursor", "")
            if not cursor:
                break

        return replies

    async def _build_thread_burst_record(
        self,
        burst: ConversationalBurst,
        thread_ts: str,
        thread_rg_id: str,
        ctx: ProcessingContext,
        file_child_records: Optional[dict[str, list[ChildRecord]]] = None,
    ) -> Optional[MessageRecord]:
        """
        Build a thread-burst MessageRecord from a 5-min burst window of thread
        messages.  These records are indexed (blob storage + vectorization).
        """
        if not burst.messages:
            return None

        first_ts = burst.messages[0].get("ts", "")
        last_ts  = burst.messages[-1].get("ts", "")
        if not first_ts:
            return None

        mentioned_user_ids:  set[str] = set()
        mentioned_group_ids: set[str] = set()
        extracted_urls:      set[str] = set()
        all_reactions: list[dict[str, Any]] = []
        for msg in burst.messages:
            t = msg.get("text", "")
            mentioned_user_ids.update(self._extract_user_mentions(t))
            mentioned_group_ids.update(self._extract_channel_mentions(t))
            extracted_urls.update(self._extract_urls(t))
            all_reactions.extend(msg.get("reactions", []))

        src_ts_ms  = int(float(first_ts) * 1000)
        last_ts_ms = int(float(last_ts) * 1000) if last_ts else src_ts_ms

        all_ts_str = "|".join(sorted(m.get("ts", "") for m in burst.messages))
        burst_hash = hashlib.md5(all_ts_str.encode()).hexdigest()[:8]
        burst_id   = f"thread_burst_{ctx.channel_id}_{thread_ts}_{burst_hash}"

        bc = self._build_burst_block_containers(
            burst.messages, burst_id, ctx,
            file_child_records=file_child_records,
            thread_ts=thread_ts,
        )

        aggregated_text = self._replace_mentions_in_text(
            burst.aggregated_text, ctx.user_id_to_name, ctx.channel_id_to_name
        )

        try:
            url = self._message_url(ctx.channel_id, first_ts, thread_ts=thread_ts)
        except Exception:
            url = None

        burst_authors = list(dict.fromkeys(
            m.get("user", "") for m in burst.messages if m.get("user")
        ))

        attachments_meta = []
        if file_child_records:
            for crs in file_child_records.values():
                for cr in crs:
                    attachments_meta.append({
                        "record_id": cr.child_id,
                        "name": cr.child_name,
                    })

        channel_name = ctx.channel_id_to_name.get(ctx.channel_id, ctx.channel_id)
        try:
            return MessageRecord(
                org_id=self.data_entities_processor.org_id,
                record_name=f"{channel_name}_{self._slack_ts_to_utc_label(first_ts)}",
                record_type=RecordType.MESSAGE,
                record_group_type=RecordGroupType.SLACK_THREAD,
                external_record_id=burst_id,
                external_record_group_id=f"thread_{ctx.channel_id}_{thread_ts}",
                record_group_id=thread_rg_id,
                version=1,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.SLACK_WORKSPACE,
                connector_id=self.connector_id,
                mime_type=MimeTypes.BLOCKS.value,
                weburl=url,
                source_created_at=src_ts_ms,
                source_updated_at=last_ts_ms,
                content=aggregated_text,
                thread_id=thread_ts,
                is_thread_parent=False,
                reply_count=len(burst.messages),
                mentioned_user_ids=list(mentioned_user_ids),
                mentioned_group_ids=list(mentioned_group_ids),
                extracted_urls=list(extracted_urls),
                reactions=all_reactions,
                author_id=burst_authors[0] if burst_authors else "",
                slack_user_id=burst_authors[0] if burst_authors else "",
                slack_subtype="thread_burst",
                thread_ts=thread_ts,
                start_ts=first_ts,
                end_ts=last_ts,
                inherit_permissions=True,
                preview_renderable=False,
                block_containers=bc,
                attachments_metadata=attachments_meta,
                involved_user_source_ids=burst_authors,
            )
        except Exception as exc:
            self.logger.error(f"_build_thread_burst_record({burst_id}): {exc}")
            return None

    # =========================================================================
    # 5d.  Shared thread processing
    # =========================================================================

    async def _process_thread(
        self,
        parent_msg: dict[str, Any],
        ctx: ProcessingContext,
        rg_id: Optional[str],
    ) -> None:
        """
        Full thread processing for a thread-parent message.

        Creates:
          1. SLACK_THREAD RecordGroup
          2. Thread-burst/single MessageRecords (5-min windows, indexed)
          3. FileRecords attached to burst records
          4. MENTIONED_IN / INVOLVED_IN edges for burst records
        """
        ts = parent_msg.get("ts", "")
        reply_count = parent_msg.get("reply_count", 0)
        self.logger.info(
            f"[ThreadProcess] Starting full thread processing: "
            f"ts={ts}, channel={ctx.channel_id}, reply_count={reply_count}, "
            f"channel_rg_id={rg_id}"
        )

        thread_rg = self._create_thread_record_group(parent_msg, ctx, rg_id)
        if not thread_rg:
            self.logger.warning(
                f"[ThreadProcess] Could not build thread RecordGroup for ts={ts} "
                f"in {ctx.channel_id} — skipping thread"
            )
            return

        try:
            await self.data_entities_processor.on_new_record_groups([(thread_rg, [])])
        except Exception as exc:
            self.logger.error(
                f"[ThreadProcess] on_new_record_groups FAILED for thread ts={ts}, "
                f"channel={ctx.channel_id}, ext_id={thread_rg.external_group_id}: {exc}",
                exc_info=True,
            )
            raise

        thread_rg_id = thread_rg.id or ""
        self.logger.info(
            f"[ThreadProcess] Thread RecordGroup persisted: "
            f"id={thread_rg_id}, ext_id={thread_rg.external_group_id}, "
            f"group_type={thread_rg.group_type}"
        )

        replies = await self._fetch_thread_replies_raw(ctx.channel_id, ts, ctx)
        all_msgs = [parent_msg] + replies
        self.logger.info(
            f"[ThreadProcess] Fetched {len(replies)} replies for thread ts={ts}"
        )

        # -- Collect file records keyed by message ts --------------------------
        thread_ext_group_id = f"thread_{ctx.channel_id}_{ts}"
        file_children_by_ts: dict[str, list[ChildRecord]] = {}
        file_recs_by_ts: dict[str, list[FileRecord]] = {}
        for msg in all_msgs:
            mts = msg.get("ts", "")
            for fd in msg.get("files", []):
                fr = await self._process_file_raw(fd, ctx)
                if fr:
                    fr.record_group_id = thread_rg_id
                    fr.external_record_group_id = thread_ext_group_id
                    fr.record_group_type = RecordGroupType.SLACK_THREAD
                    file_recs_by_ts.setdefault(mts, []).append(fr)
                    cr = ChildRecord(
                        child_type=ChildType.RECORD,
                        child_id=fr.id,
                        child_name=fr.record_name,
                    )
                    file_children_by_ts.setdefault(mts, []).append(cr)

        # -- Thread-burst MessageRecords (5-min windows, indexed) ---------------
        bursts = self._detect_bursts(all_msgs)
        burst_recs_batch: list[tuple[Record, list[Permission]]] = []

        for burst in bursts:
            burst_file_children: dict[str, list[ChildRecord]] = {}
            burst_file_recs: list[FileRecord] = []
            for msg in burst.messages:
                mts = msg.get("ts", "")
                if mts in file_children_by_ts:
                    burst_file_children[mts] = file_children_by_ts[mts]
                if mts in file_recs_by_ts:
                    burst_file_recs.extend(file_recs_by_ts[mts])

            burst_rec = await self._build_thread_burst_record(
                burst, ts, thread_rg_id, ctx,
                file_child_records=burst_file_children,
            )
            if not burst_rec:
                continue

            burst_recs_batch.append((burst_rec, []))

            for fr in burst_file_recs:
                fr.parent_node_id            = burst_rec.id
                fr.is_dependent_node         = True
                fr.inherit_permissions       = True
                fr.parent_external_record_id = burst_rec.external_record_id
                fr.parent_record_type        = RecordType.MESSAGE
                self.logger.debug(
                    f"[ThreadFile] File '{fr.record_name}' linked to thread burst "
                    f"parent_node_id={burst_rec.id}, rg={fr.external_record_group_id}"
                )
                burst_recs_batch.append((fr, []))

            for msg in burst.messages:
                for url_str in self._extract_urls(msg.get("text", "")):
                    lr = await self._process_link(url_str, burst_rec, ctx, msg)
                    if lr:
                        burst_recs_batch.append((lr, []))

            burst_authors = list(dict.fromkeys(
                m.get("user", "") for m in burst.messages if m.get("user")
            ))
            burst_rec.involved_user_source_ids = burst_authors

        if burst_recs_batch:
            thread_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.THREADS)
            file_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.FILES)
            link_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.LINKS)
            for rec, _ in burst_recs_batch:
                if rec.record_type == RecordType.MESSAGE and not thread_enabled:
                    rec.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
                elif rec.record_type == RecordType.FILE and not file_enabled:
                    rec.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
                elif rec.record_type == RecordType.LINK and not link_enabled:
                    rec.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

            await self._enrich_link_records_linked_id(burst_recs_batch)
            burst_msg_count = sum(
                1 for r, _ in burst_recs_batch if getattr(r, 'record_type', None) == RecordType.MESSAGE
            )
            burst_file_count = sum(
                1 for r, _ in burst_recs_batch if getattr(r, 'record_type', None) == RecordType.FILE
            )
            self.logger.info(
                f"[ThreadProcess] Creating {burst_msg_count} burst records and "
                f"{burst_file_count} file records for thread ts={ts}"
            )
            await self.data_entities_processor.on_new_records(burst_recs_batch)

        self.logger.info(
            f"[ThreadProcess] Completed thread processing for ts={ts}, "
            f"channel={ctx.channel_id}: thread_rg_id={thread_rg_id}, "
            f"{len(burst_recs_batch)} burst/file records"
        )

    async def _process_thread_incremental(
        self,
        parent_msg: dict[str, Any],
        ctx: ProcessingContext,
        thread_rg_id: str,
        last_reply_ts: str,
    ) -> None:
        """
        Incremental thread processing: only fetch and process replies newer
        than ``last_reply_ts``.

        Creates thread-burst/single records for the new replies only.
        """
        thread_ts = parent_msg.get("ts", "")
        new_replies = await self._fetch_thread_replies_raw(
            ctx.channel_id, thread_ts, ctx, oldest=last_reply_ts,
        )
        if not new_replies:
            return

        self.logger.info(
            f"  Thread {thread_ts}: {len(new_replies)} new replies "
            f"(after ts={last_reply_ts})"
        )

        # -- Collect file records for new replies -----------------------------
        thread_ext_group_id = f"thread_{ctx.channel_id}_{thread_ts}"
        file_children_by_ts: dict[str, list[ChildRecord]] = {}
        file_recs_by_ts: dict[str, list[FileRecord]] = {}
        for msg in new_replies:
            mts = msg.get("ts", "")
            for fd in msg.get("files", []):
                fr = await self._process_file_raw(fd, ctx)
                if fr:
                    fr.record_group_id = thread_rg_id
                    fr.external_record_group_id = thread_ext_group_id
                    fr.record_group_type = RecordGroupType.SLACK_THREAD
                    file_recs_by_ts.setdefault(mts, []).append(fr)
                    cr = ChildRecord(
                        child_type=ChildType.RECORD,
                        child_id=fr.id,
                        child_name=fr.record_name,
                    )
                    file_children_by_ts.setdefault(mts, []).append(cr)

        # -- Thread-burst MessageRecords for new replies -----------------------
        bursts = self._detect_bursts(new_replies)
        burst_recs_batch: list[tuple[Record, list[Permission]]] = []

        for burst in bursts:
            burst_file_children: dict[str, list[ChildRecord]] = {}
            burst_file_recs: list[FileRecord] = []
            for msg in burst.messages:
                mts = msg.get("ts", "")
                if mts in file_children_by_ts:
                    burst_file_children[mts] = file_children_by_ts[mts]
                if mts in file_recs_by_ts:
                    burst_file_recs.extend(file_recs_by_ts[mts])

            burst_rec = await self._build_thread_burst_record(
                burst, thread_ts, thread_rg_id, ctx,
                file_child_records=burst_file_children,
            )
            if not burst_rec:
                continue

            burst_recs_batch.append((burst_rec, []))

            for fr in burst_file_recs:
                fr.parent_node_id            = burst_rec.id
                fr.is_dependent_node         = True
                fr.inherit_permissions       = True
                fr.parent_external_record_id = burst_rec.external_record_id
                fr.parent_record_type        = RecordType.MESSAGE
                burst_recs_batch.append((fr, []))

            for msg in burst.messages:
                for url_str in self._extract_urls(msg.get("text", "")):
                    lr = await self._process_link(url_str, burst_rec, ctx, msg)
                    if lr:
                        burst_recs_batch.append((lr, []))

            burst_authors = list(dict.fromkeys(
                m.get("user", "") for m in burst.messages if m.get("user")
            ))
            burst_rec.involved_user_source_ids = burst_authors

        if burst_recs_batch:
            thread_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.THREADS)
            file_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.FILES)
            link_enabled = self.indexing_filters.is_enabled(IndexingFilterKey.LINKS)
            for rec, _ in burst_recs_batch:
                if rec.record_type == RecordType.MESSAGE and not thread_enabled:
                    rec.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
                elif rec.record_type == RecordType.FILE and not file_enabled:
                    rec.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
                elif rec.record_type == RecordType.LINK and not link_enabled:
                    rec.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

            await self._enrich_link_records_linked_id(burst_recs_batch)
            await self.data_entities_processor.on_new_records(burst_recs_batch)

    # =========================================================================
    # 5e.  Burst / single processing
    # =========================================================================

    async def _process_burst(
        self,
        burst: ConversationalBurst,
        ctx: ProcessingContext,
    ) -> tuple[list[tuple[Record, list[Permission]]], list[DeferredThread]]:
        """
        Multi-message burst → ONE burst MessageRecord with block_containers.
        Thread-parent messages are collected for deferred processing.
        """
        recs: list[tuple[Record, list[Permission]]] = []
        deferred_threads: list[DeferredThread] = []

        first_ts = burst.messages[0].get("ts", "")
        last_ts  = burst.messages[-1].get("ts", "")
        if not first_ts:
            return recs, deferred_threads

        # ── Aggregate burst-level metadata ────────────────────────────────────
        mentioned_user_ids:  set[str] = set()
        mentioned_group_ids: set[str] = set()
        extracted_urls:      set[str] = set()
        all_reactions: list[dict[str, Any]] = []
        reply_count = 0
        latest_reply_timestamp: Optional[int] = None

        for msg in burst.messages:
            t = msg.get("text", "")
            mentioned_user_ids.update(self._extract_user_mentions(t))
            mentioned_group_ids.update(self._extract_channel_mentions(t))
            extracted_urls.update(self._extract_urls(t))
            all_reactions.extend(msg.get("reactions", []))
            rc = msg.get("reply_count", 0)
            if rc > reply_count:
                reply_count = rc
            lr = msg.get("latest_reply")
            if lr:
                lr_ms = int(float(lr) * 1000)
                if not latest_reply_timestamp or lr_ms > latest_reply_timestamp:
                    latest_reply_timestamp = lr_ms

        # ── Build file FileRecords first so we can attach ChildRecords ────────
        file_children_by_ts: dict[str, list[ChildRecord]] = {}
        file_recs: list[FileRecord] = []

        for msg in burst.messages:
            ts = msg.get("ts", "")
            for fd in msg.get("files", []):
                fr = await self._process_file_raw(fd, ctx)
                if fr:
                    # fr.record_group_type = RecordGroupType.SLACK_CHANNEL
                    file_recs.append(fr)
                    cr = ChildRecord(
                        child_type=ChildType.RECORD,
                        child_id=fr.id,
                        child_name=fr.record_name,
                    )
                    file_children_by_ts.setdefault(ts, []).append(cr)

        # ── Generate burst id ─────────────────────────────────────────────────
        all_ts_str = "|".join(sorted([m.get("ts", "") for m in burst.messages]))
        ts_hash = hashlib.md5(all_ts_str.encode()).hexdigest()[:8]
        burst_id = f"burst_{ctx.channel_id}_{ts_hash}"

        # ── Build block_containers ────────────────────────────────────────────
        bc = self._build_burst_block_containers(
            burst.messages, burst_id, ctx,
            file_child_records=file_children_by_ts,
        )

        # ── Aggregated display name ───────────────────────────────────────────
        aggregated_text = self._replace_mentions_in_text(
            burst.aggregated_text, ctx.user_id_to_name, ctx.channel_id_to_name
        )
        rg_id     = ctx.channel_groups_map.get(ctx.channel_id)
        src_ts_ms = int(float(first_ts) * 1000)
        last_ts_ms = int(float(last_ts) * 1000) if last_ts else src_ts_ms

        # Collect unique authors in this burst
        burst_authors = list(dict.fromkeys(
            m.get("user", "") for m in burst.messages if m.get("user")
        ))

        attachments_meta = [
            {"record_id": fr.id, "name": fr.record_name}
            for fr in file_recs
        ]

        channel_name = ctx.channel_id_to_name.get(ctx.channel_id, ctx.channel_id)
        try:
            burst_rec = MessageRecord(
                org_id=self.data_entities_processor.org_id,
                record_name=f"{channel_name}_{self._slack_ts_to_utc_label(first_ts)}",
                record_type=RecordType.MESSAGE,
                record_group_type=RecordGroupType.SLACK_CHANNEL,
                external_record_id=burst_id,
                external_record_group_id=ctx.channel_id,
                record_group_id=rg_id,
                version=1,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.SLACK_WORKSPACE,
                connector_id=self.connector_id,
                mime_type=MimeTypes.BLOCKS.value,
                weburl=self._message_url(ctx.channel_id, first_ts),
                source_created_at=src_ts_ms,
                source_updated_at=last_ts_ms,
                content=aggregated_text,
                reply_count=reply_count,
                latest_reply_timestamp=latest_reply_timestamp,
                mentioned_user_ids=list(mentioned_user_ids),
                mentioned_group_ids=list(mentioned_group_ids),
                extracted_urls=list(extracted_urls),
                reactions=all_reactions,
                author_id=burst_authors[0] if burst_authors else "",
                slack_user_id=burst_authors[0] if burst_authors else "",
                slack_subtype="burst",
                start_ts=first_ts,
                end_ts=last_ts,
                inherit_permissions=True,
                preview_renderable=False,
                block_containers=bc,
                attachments_metadata=attachments_meta,
                involved_user_source_ids=burst_authors,
            )
        except Exception as exc:
            self.logger.error(f"_process_burst create burst_rec ({burst_id}): {exc}")
            return recs, deferred_threads

        recs.append((burst_rec, []))

        # ── File dependencies ─────────────────────────────────────────────────
        for fr in file_recs:
            fr.parent_node_id = burst_rec.id
            fr.is_dependent_node = True
            fr.inherit_permissions = True
            fr.parent_external_record_id = burst_id
            fr.parent_record_type = RecordType.MESSAGE
            self.logger.debug(
                f"[BurstFile] File '{fr.record_name}' linked to burst "
                f"parent_node_id={burst_rec.id}, parent_ext_id={burst_id}"
            )
            recs.append((fr, []))

        # ── Links at burst level ──────────────────────────────────────────────
        for msg in burst.messages:
            for url in self._extract_urls(msg.get("text", "")):
                lr = await self._process_link(url, burst_rec, ctx, msg)
                if lr:
                    recs.append((lr, []))

        # ── Collect thread parents for deferred processing ──────────────────
        for msg in burst.messages:
            ts = msg.get("ts", "")
            if msg.get("reply_count", 0) > 0 and msg.get("thread_ts") == ts:
                deferred_threads.append(DeferredThread(msg=msg, ctx=ctx, rg_id=rg_id))

        if deferred_threads:
            self.logger.info(
                f"[BurstThread] Found {len(deferred_threads)} thread parent(s) "
                f"in burst, channel={ctx.channel_id} (deferred)"
            )

        return recs, deferred_threads

    async def _process_single(
        self,
        msg: dict[str, Any],
        ctx: ProcessingContext,
    ) -> tuple[list[tuple[Record, list[Permission]]], list[DeferredThread]]:
        """
        Single-message burst → ONE MessageRecord with block_containers.
        Thread-parent messages are collected for deferred processing.
        """
        recs: list[tuple[Record, list[Permission]]] = []
        deferred_threads: list[DeferredThread] = []
        ts   = msg.get("ts", "")
        if not ts:
            return recs, deferred_threads

        rg_id = ctx.channel_groups_map.get(ctx.channel_id)

        # ── Build file ChildRecords ───────────────────────────────────────────
        file_children: list[ChildRecord] = []
        file_recs:     list[FileRecord]  = []
        for fd in msg.get("files", []):
            fr = await self._process_file_raw(fd, ctx)
            if fr:
                file_recs.append(fr)
                # fr.record_group_type = RecordGroupType.SLACK_CHANNEL
                file_children.append(ChildRecord(
                    child_type=ChildType.RECORD,
                    child_id=fr.id,
                    child_name=fr.record_name,
                ))

        # ── Build block_containers ────────────────────────────────────────────
        bc = self._build_single_block_containers(
            msg, ctx, children_records=file_children or None
        )

        # ── Build message record ──────────────────────────────────────────────
        text = self._replace_mentions_in_text(
            msg.get("text", ""), ctx.user_id_to_name, ctx.channel_id_to_name
        )
        src_ts_ms = int(float(ts) * 1000)
        is_edited = "edited" in msg
        upd_ts_ms = src_ts_ms
        if is_edited:
            raw_ets = msg.get("edited", {}).get("ts")
            if raw_ets:
                upd_ts_ms = int(float(raw_ets) * 1000)

        thread_ts = msg.get("thread_ts")
        is_thread_parent = thread_ts == ts if thread_ts else False

        try:
            url = self._message_url(ctx.channel_id, ts)
        except Exception:
            url = None

        mentioned_user_ids  = self._extract_user_mentions(msg.get("text", ""))
        mentioned_group_ids = self._extract_channel_mentions(msg.get("text", ""))

        attachments_meta = [
            {"record_id": fr.id, "name": fr.record_name}
            for fr in file_recs
        ]

        channel_name = ctx.channel_id_to_name.get(ctx.channel_id, ctx.channel_id)
        try:
            rec = MessageRecord(
                org_id=self.data_entities_processor.org_id,
                record_name=f"{channel_name}_{self._slack_ts_to_utc_label(ts)}",
                record_type=RecordType.MESSAGE,
                record_group_type=RecordGroupType.SLACK_CHANNEL,
                external_record_id=ts,
                external_record_group_id=ctx.channel_id,
                record_group_id=rg_id,
                version=1,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.SLACK_WORKSPACE,
                connector_id=self.connector_id,
                mime_type=MimeTypes.BLOCKS.value,
                weburl=url,
                source_created_at=src_ts_ms,
                source_updated_at=upd_ts_ms,
                content=text,
                thread_id=thread_ts,
                is_thread_parent=is_thread_parent,
                reply_count=msg.get("reply_count", 0),
                reply_user_ids=msg.get("reply_users", []),
                latest_reply_timestamp=(
                    int(float(msg.get("latest_reply")) * 1000)
                    if msg.get("latest_reply") else None
                ),
                reactions=msg.get("reactions", []),
                mentioned_user_ids=mentioned_user_ids,
                mentioned_group_ids=mentioned_group_ids,
                extracted_urls=self._extract_urls(msg.get("text", "")),
                is_edited=is_edited,
                edited_timestamp=upd_ts_ms if is_edited else None,
                author_id=msg.get("user", ""),
                author_name=ctx.user_id_to_name.get(msg.get("user", "")),
                author_email=ctx.user_id_to_email.get(msg.get("user", "")),
                bot_id=msg.get("bot_id"),
                app_id=msg.get("app_id"),
                attachments=msg.get("attachments", []),
                rich_content={"blocks": msg["blocks"]} if msg.get("blocks") else None,
                thread_ts=thread_ts,
                slack_subtype=msg.get("subtype"),
                slack_user_id=msg.get("user", ""),
                slack_blocks=msg.get("blocks", []),
                start_ts=ts,
                end_ts=ts,
                inherit_permissions=True,
                preview_renderable=False,
                block_containers=bc,
                attachments_metadata=attachments_meta,
                involved_user_source_ids=[msg.get("user", "")] if msg.get("user") else [],
            )
        except Exception as exc:
            self.logger.error(f"_process_single({ts}): {exc}")
            return recs, deferred_threads

        recs.append((rec, []))

        # ── File dependencies ─────────────────────────────────────────────────
        for fr in file_recs:
            fr.parent_node_id = rec.id
            fr.is_dependent_node = True
            fr.inherit_permissions = True
            fr.parent_external_record_id = ts
            fr.parent_record_type = RecordType.MESSAGE
            self.logger.debug(
                f"[ChannelFile] File '{fr.record_name}' linked to msg "
                f"parent_node_id={rec.id}, parent_ext_id={ts}, rg={fr.external_record_group_id}"
            )
            recs.append((fr, []))

        # ── Link dependencies ─────────────────────────────────────────────────
        for url_str in self._extract_urls(msg.get("text", "")):
            lr = await self._process_link(url_str, rec, ctx, msg)
            if lr:
                recs.append((lr, []))

        # ── Collect thread parent for deferred processing ──────────────────
        if is_thread_parent and msg.get("reply_count", 0) > 0:
            deferred_threads.append(DeferredThread(msg=msg, ctx=ctx, rg_id=rg_id))

        return recs, deferred_threads

    # ── File record helpers ────────────────────────────────────────────────────

    @staticmethod
    def _get_file_extension(filename: str) -> Optional[str]:
        """Extract the file extension from a filename, lowercased and without dot."""
        if filename and "." in filename:
            parts = filename.rsplit(".", 1)
            if len(parts) > 1 and parts[1]:
                return parts[1].lower()
        return None

    @staticmethod
    def _resolve_file_mime_type(filename: str, slack_mimetype: Optional[str]) -> str:
        """
        Determine the best MIME type for a file.

        Slack often returns 'application/octet-stream' (binary) for files it
        doesn't recognise (e.g. .md, .yaml).  When that happens we fall back
        to Python's mimetypes.guess_type which uses the filename extension.
        """
        UNRELIABLE_MIME_TYPES = {
            None,
            "",
            "application/octet-stream",
            "binary/octet-stream",
        }
        # If Slack gave a useful MIME type, validate it against our enum
        if slack_mimetype and slack_mimetype not in UNRELIABLE_MIME_TYPES:
            return slack_mimetype

        # Fall back to guessing from filename
        if filename:
            guessed, _ = mimetypes.guess_type(filename, strict=False)
            if guessed:
                return guessed

        return MimeTypes.UNKNOWN.value

    @staticmethod
    def _resolve_file_extension(
        filename: str, slack_filetype: Optional[str]
    ) -> Optional[str]:
        """
        Determine the best extension for a Slack file.

        Slack's ``filetype`` field sometimes returns ``'binary'`` for files it
        doesn't recognise (e.g. .md, .yaml).  When that happens we derive the
        extension from the filename instead.
        """
        UNRELIABLE_FILETYPES = {None, "", "binary", "undefined"}

        # Always prefer filename-derived extension when Slack's is unreliable
        fname_ext = None
        if filename and "." in filename:
            parts = filename.rsplit(".", 1)
            if len(parts) > 1 and parts[1]:
                fname_ext = parts[1].lower()

        if slack_filetype and slack_filetype not in UNRELIABLE_FILETYPES:
            return slack_filetype.lower()

        return fname_ext

    async def _process_file_raw(
        self,
        fd: dict[str, Any],
        ctx: ProcessingContext,
    ) -> Optional[FileRecord]:
        """
        Create a FileRecord for a Slack file attachment.
        parent_node_id is intentionally NOT set here; the caller sets it after
        the parent record has been created.
        """
        fid = fd.get("id")
        if not fid:
            return None

        file_hash: Optional[str] = None
        url_dl = fd.get("url_private_download") or fd.get("url_private")
        if url_dl:
            try:
                await ctx.rate_limiter.acquire()
                token = getattr(
                    self.external_client.get_client(), "get_token", lambda: None
                )()
                async with httpx.AsyncClient(timeout=30.0) as http:
                    headers = {"Authorization": f"Bearer {token}"} if token else {}
                    r = await http.get(url_dl, headers=headers)
                    if r.status_code == 200:
                        file_hash = hashlib.sha256(r.content).hexdigest()
            except Exception as exc:
                self.logger.warning(f"File download failed {fid}: {exc}")

        rg_id = ctx.channel_groups_map.get(ctx.channel_id)
        filename = fd.get("name", f"file_{fid}")
        extension = self._resolve_file_extension(filename, fd.get("filetype"))
        mime_type = self._resolve_file_mime_type(filename, fd.get("mimetype"))

        try:
            return FileRecord(
                org_id=self.data_entities_processor.org_id,
                record_name=filename,
                record_type=RecordType.FILE,
                external_record_id=fid,
                external_record_group_id=ctx.channel_id,
                record_group_id=rg_id,
                version=1,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.SLACK_WORKSPACE,
                connector_id=self.connector_id,
                mime_type=mime_type,
                weburl=fd.get("permalink"),
                source_created_at=int(fd.get("created", 0)) * 1000,
                source_updated_at=int(fd.get("created", 0)) * 1000,
                is_file=True,
                extension=extension,
                size_in_bytes=fd.get("size"),
                sha256_hash=file_hash,
                is_dependent_node=True,
                inherit_permissions=True,
            )
        except Exception as exc:
            self.logger.error(f"_process_file_raw({fid}): {exc}")
            return None

    async def _process_file(
        self,
        fd: dict[str, Any],
        parent: MessageRecord,
        ctx: ProcessingContext,
    ) -> Optional[FileRecord]:
        """Compatibility wrapper — creates a file record linked to parent."""
        fr = await self._process_file_raw(fd, ctx)
        if fr:
            fr.parent_node_id              = parent.id
            fr.parent_external_record_id   = parent.external_record_id
            fr.parent_record_type          = RecordType.MESSAGE
        return fr


    # ── Link record ────────────────────────────────────────────────────────────

    async def _process_link(
        self,
        url: str,
        parent: MessageRecord,
        ctx: ProcessingContext,
        msg: dict[str, Any],
    ) -> Optional[LinkRecord]:
        try:
            itype, imeta = self._classify_url(url)
            unfurl       = self._unfurl_data(url, msg)
            rg_id        = ctx.channel_groups_map.get(ctx.channel_id)

            related: list[RelatedExternalRecord] = []
            if itype == "jira" and imeta.get("ticket_key"):
                related.append(RelatedExternalRecord(
                    external_record_id=imeta["ticket_key"],
                    record_type=RecordType.TICKET,
                    relation_type=RecordRelations.LINKED_TO,
                ))

            return LinkRecord(
                org_id=self.data_entities_processor.org_id,
                record_name=unfurl.get("title") or url[:100],
                record_type=RecordType.LINK,
                external_record_id=hashlib.md5(url.encode()).hexdigest(),
                external_record_group_id=ctx.channel_id,
                record_group_id=rg_id,
                parent_external_record_id=parent.external_record_id,
                parent_record_type=RecordType.MESSAGE,
                version=1,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.SLACK_WORKSPACE,
                connector_id=self.connector_id,
                mime_type=MimeTypes.MARKDOWN.value,
                weburl=url,
                url=url,
                title=unfurl.get("title"),
                is_public=LinkPublicStatus.UNKNOWN,
                linked_record_id=None,
                is_dependent_node=True,
                parent_node_id=parent.id,
                inherit_permissions=True,
                related_external_records=related,
            )
        except Exception as exc:
            self.logger.error(f"_process_link({url[:60]}): {exc}")
            return None

    async def _enrich_link_records_linked_id(
        self,
        batch: list[tuple[Record, list[Permission]]],
    ) -> None:
        """Look up related record by weburl for LinkRecords (same as Linear)."""
        link_records = [
            rec for rec, _ in batch
            if rec.record_type == RecordType.LINK and getattr(rec, "weburl", None)
        ]
        if not link_records:
            return
        try:
            async with self.data_store_provider.transaction() as tx_store:
                for rec in link_records:
                    try:
                        related_record = await tx_store.get_record_by_weburl(
                            rec.weburl,
                            org_id=self.data_entities_processor.org_id,
                        )
                        if related_record:
                            rec.linked_record_id = related_record.id
                            self.logger.debug(
                                f"🔗 Found related record {related_record.id} for link URL: {rec.weburl}"
                            )
                    except Exception as e:
                        self.logger.debug(
                            f"⚠️ Could not fetch related record for URL {rec.weburl}: {e}"
                        )
        except Exception as e:
            self.logger.debug(f"⚠️ Could not open transaction for link record enrichment: {e}")

    # ── Message record transformation ──────────────────────────────────────────

    async def _to_message_record(
        self,
        msg: dict[str, Any],
        ctx: ProcessingContext,
        parent_external_record_id: Optional[str] = None,
    ) -> Optional[MessageRecord]:
        ts = msg.get("ts")
        if not ts:
            return None

        text       = msg.get("text", "")
        user_id    = msg.get("user", "")
        thread_ts  = msg.get("thread_ts")
        src_ts     = int(float(ts) * 1000)
        upd_ts     = src_ts
        is_edited  = "edited" in msg
        edited_ts: Optional[int] = None
        original_text: Optional[str] = None

        if is_edited:
            raw_ets = msg.get("edited", {}).get("ts")
            if raw_ets:
                edited_ts = int(float(raw_ets) * 1000)
                upd_ts    = edited_ts
            # Get original text if available
            original_text = msg.get("message", {}).get("text", "")

        # Replace mentions in content
        content_with_mentions = self._replace_mentions_in_text(
            text, ctx.user_id_to_name, ctx.channel_id_to_name
        )

        # Replace mentions in original_text if edited
        if original_text:
            original_text = self._replace_mentions_in_text(
                original_text, ctx.user_id_to_name, ctx.channel_id_to_name
            )

        latest_reply_ts = msg.get("latest_reply")
        rg_id           = ctx.channel_groups_map.get(ctx.channel_id)

        channel_name = ctx.channel_id_to_name.get(ctx.channel_id, ctx.channel_id)
        try:
            return MessageRecord(
                org_id=self.data_entities_processor.org_id,
                record_name=f"{channel_name}_{self._slack_ts_to_utc_label(ts)}",
                record_type=RecordType.MESSAGE,
                external_record_id=ts,
                external_record_group_id=ctx.channel_id,
                record_group_id=rg_id,
                parent_external_record_id=parent_external_record_id,
                parent_record_type=(
                    RecordType.MESSAGE if parent_external_record_id else None
                ),
                version=1,
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.SLACK_WORKSPACE,
                connector_id=self.connector_id,
                mime_type=MimeTypes.TEXT.value,
                weburl=self._message_url(ctx.channel_id, ts),
                source_created_at=src_ts,
                source_updated_at=upd_ts,
                # Content (with mentions replaced)
                content=content_with_mentions,
                thread_id=thread_ts,
                is_thread_parent=(thread_ts == ts) if thread_ts else False,
                reply_count=msg.get("reply_count", 0),
                reply_user_ids=msg.get("reply_users", []),
                latest_reply_timestamp=(
                    int(float(latest_reply_ts) * 1000) if latest_reply_ts else None
                ),
                reactions=msg.get("reactions", []),
                mentioned_user_ids=self._extract_user_mentions(text),  # Keep original IDs for edges
                mentioned_group_ids=self._extract_channel_mentions(text),  # Keep original IDs for edges
                extracted_urls=self._extract_urls(text),
                is_edited=is_edited,
                edited_timestamp=edited_ts,
                original_text=original_text,
                author_id=user_id,
                bot_id=msg.get("bot_id"),
                app_id=msg.get("app_id"),
                attachments=msg.get("attachments", []),
                rich_content={"blocks": msg["blocks"]} if msg.get("blocks") else None,
                # Slack-specific
                thread_ts=thread_ts,
                slack_subtype=msg.get("subtype"),
                slack_user_id=user_id,
                slack_blocks=msg.get("blocks", []),
                # Hierarchy
                inherit_permissions=True,
                preview_renderable=False,
            )
        except Exception as exc:
            self.logger.error(f"_to_message_record({ts}): {exc}")
            return None

    @staticmethod
    def _slack_ts_to_utc_label(ts: str) -> str:
        """Format Slack ts as UTC human-readable label (e.g. 2025-02-28_14-30-45)."""
        try:
            t = float(ts)
            dt = datetime.fromtimestamp(t, tz=timezone.utc)
            return dt.strftime("%Y-%m-%d_%H-%M-%S")
        except (ValueError, OSError):
            return ts.replace(".", "-")

    def _message_url(
        self,
        channel_id: str,
        ts: str,
        thread_ts: Optional[str] = None,
    ) -> str:
        clean = ts.replace(".", "")
        if self.workspace_domain:
            url = (
                f"https://{self.workspace_domain}.slack.com"
                f"/archives/{channel_id}/p{clean}"
            )
        else:
            url = f"https://app.slack.com/client/{self.team_id or ''}/{channel_id}/p{clean}"
        # Slack opens the thread side-panel and highlights the reply only when
        # both ?thread_ts=<parent> and &cid=<channel> are present on the URL.
        if thread_ts and thread_ts != ts:
            url += f"?thread_ts={thread_ts}&cid={channel_id}"
        return url

    # ── Burst detection ────────────────────────────────────────────────────────

    def _detect_bursts(self, msgs: list[dict[str, Any]]) -> list[ConversationalBurst]:
        """
        Group messages into conversational bursts (oldest-first processing).

        A burst contains ALL messages from ALL users that fall within a
        BURST_WINDOW_SECONDS gap between consecutive messages.  A gap larger
        than the window, or a bot / system message, starts a new burst.
        """
        if not msgs:
            return []

        SYSTEM = {
            "channel_join", "channel_leave", "channel_topic",
            "channel_purpose", "channel_name", "channel_archive",
        }

        # Slack returns newest-first; reverse to process chronologically
        sorted_msgs = sorted(msgs, key=lambda m: float(m.get("ts", "0")))
        bursts: list[ConversationalBurst] = []
        cur:    list[dict[str, Any]]      = [sorted_msgs[0]]

        for i in range(1, len(sorted_msgs)):
            curr = sorted_msgs[i]
            prev = sorted_msgs[i - 1]

            # Hard break: bot or system message
            if curr.get("bot_id") or curr.get("subtype") in SYSTEM:
                bursts.append(ConversationalBurst(
                    messages=cur,
                    start_ts=float(cur[0].get("ts", "0")),
                    end_ts=float(cur[-1].get("ts", "0")),
                ))
                cur = [curr]
                continue

            within_time = (
                float(curr.get("ts", "0")) - float(prev.get("ts", "0"))
                <= BURST_WINDOW_SECONDS
            )

            if within_time:
                cur.append(curr)
            else:
                bursts.append(ConversationalBurst(
                    messages=cur,
                    start_ts=float(cur[0].get("ts", "0")),
                    end_ts=float(cur[-1].get("ts", "0")),
                ))
                cur = [curr]

        # Flush final burst
        bursts.append(ConversationalBurst(
            messages=cur,
            start_ts=float(cur[0].get("ts", "0")),
            end_ts=float(cur[-1].get("ts", "0")),
        ))
        return bursts

    # =========================================================================
    # 7.  Phase 5 — Thread-growth detection (lightweight incremental)
    # =========================================================================

    async def _sync_thread_growth(self) -> None:
        """
        Per-channel scan that detects threads with new replies and
        incrementally creates records + burst records for those new replies.

        Edit detection is bypassed — only thread growth (reply_count > 0 on a
        parent message whose thread RecordGroup may already exist) is handled.

        Checkpoint logic mirrors ``sync_channel_messages``:
          - First run (no checkpoint) → use the sync-window filter so the scan
            covers the same range as the initial message sync.
          - Subsequent runs → use the stored ``last_check_time``.
        """
        self.logger.info("🔍 Thread-growth detection (per-channel)…")
        try:
            now_ms = int(time.time() * 1000)
            await self._refresh_user_caches()

            sync_window_ts = self._compute_sync_window_oldest()

            channel_ids = list(self.channel_groups_cache.keys())
            self.logger.info(f"  Scanning {len(channel_ids)} channels for thread growth…")

            for cid in channel_ids:
                if not cid:
                    continue

                chan_key = generate_record_sync_point_key(
                    RecordType.MESSAGE.value, "thread_growth", cid
                )
                last = await self.audit_log_sync_point.read_sync_point(chan_key)

                oldest_ts: Optional[str] = None
                if last and last.get("last_check_time"):
                    # Slack ts is canonical "<seconds>.<6-digit microseconds>";
                    # str(float) can produce 7+ decimals (e.g.
                    # "1777530605.9567418") which Slack's `oldest`/`latest`
                    # parser silently rejects (success=True, msg_count=0).
                    oldest_ts = f"{last['last_check_time'] / 1000.0:.6f}"
                else:
                    oldest_ts = sync_window_ts

                try:
                    await self._scan_channel_thread_growth(cid, oldest_ts)
                    await self.audit_log_sync_point.update_sync_point(
                        chan_key, {"last_check_time": now_ms}
                    )
                except Exception as exc:
                    self.logger.error(
                        f"Thread-growth detection failed for {cid}: {exc}",
                        exc_info=True,
                    )

            self.logger.info("✅ Thread-growth detection complete")

        except Exception as exc:
            self.logger.error(f"❌ Thread-growth detection failed: {exc}", exc_info=True)

    async def _scan_channel_thread_growth(
        self, channel_id: str, oldest_ts: Optional[str]
    ) -> None:
        """
        Paginate through channel history within [oldest_ts, now] and call
        ``_handle_new_thread()`` for every thread-root message (reply_count > 0).
        """
        SKIP_SUBTYPES: frozenset = frozenset({
            "bot_message",
            "channel_join",        "channel_leave",
            "channel_archive",     "channel_unarchive",
            "channel_name",        "channel_purpose",      "channel_topic",
            "group_join",          "group_leave",
            "group_archive",       "group_unarchive",
            "group_name",          "group_purpose",        "group_topic",
            "file_comment",        "pinned_item",          "unpinned_item",
            "reminder_add",        "reminder_delete",
            "slackbot_response",   "thread_broadcast",
        })

        cursor: Optional[str] = None
        # Slack expects canonical "<sec>.<6-digit μs>"; `str(time.time())` can
        # emit 7 decimals, which the API silently rejects.
        now_ts = f"{time.time():.6f}"

        while True:
            await self.rate_limiter.acquire()
            ds = await self._fresh_datasource()

            try:
                kwargs: dict[str, Any] = dict(
                    channel=channel_id,
                    latest=now_ts,
                    limit=200,
                    cursor=cursor,
                )
                if oldest_ts:
                    kwargs["oldest"] = oldest_ts
                resp = await ds.conversations_history(**kwargs)
            except Exception as exc:
                self.logger.error(
                    f"conversations.history for thread-growth in {channel_id}: {exc}",
                    exc_info=True,
                )
                break

            if not resp or not resp.success:
                break

            msgs: list[dict[str, Any]] = resp.data.get("messages", [])
            if not msgs:
                break

            for md in msgs:
                ts = md.get("ts")
                if not ts:
                    continue

                subtype: str = md.get("subtype", "")
                if subtype == "message_changed":
                    inner = md.get("message") or {}
                    if inner:
                        md      = inner
                        ts      = md.get("ts", ts)
                        subtype = md.get("subtype", "")

                if subtype in SKIP_SUBTYPES:
                    continue
                if md.get("bot_id") and not md.get("user"):
                    continue
                if md.get("thread_ts") and md.get("thread_ts") != ts:
                    continue

                if md.get("reply_count", 0) > 0 and md.get("thread_ts") == ts:
                    try:
                        await self._handle_new_thread(md, channel_id, ts)
                    except Exception as exc:
                        self.logger.error(
                            f"Thread-growth for ts={ts} in {channel_id}: {exc}",
                            exc_info=True,
                        )

            cursor = (
                resp.data.get("response_metadata", {}).get("next_cursor") or ""
            )
            if not cursor:
                break

    # =========================================================================
    # 7b. Phase 5 — Full change detection (bypassed for now)
    # =========================================================================

    async def _sync_message_changes(self) -> None:
        """
        Per-channel incremental change detection.

        Each channel maintains its own change-detection checkpoint
        (``audit_log_sync_point`` keyed per channel_id).  The change window for
        a given channel is:

          - First run (no checkpoint) → sync-window filter (same range as
            the initial message sync).
          - Subsequent runs → stored ``last_check_time``.

        Benefits over a single global checkpoint:
        - A channel checked 5 min ago only scans 5 min of history, not 24 h.
        - A newly-added channel falls back to the sync-window filter.
        - A failed channel does NOT update its checkpoint, so the next run
          automatically retries from the last successful check time.

        Limitation (inherent to polling conversations.history):
        - ``conversations.history`` returns messages ordered by creation time,
          not modification time.  Edits / new thread replies on messages whose
          ``ts`` falls outside the current window cannot be detected here.
          For complete real-time coverage, integrate with Slack's Events API.
        """
        self.logger.info("🔍 Change detection (per-channel)…")
        try:
            now_ms = int(time.time() * 1000)

            sync_window_ts = self._compute_sync_window_oldest()

            # Refresh user cache before the per-channel loop
            await self._refresh_user_caches()

            channel_ids = list(self.channel_groups_cache.keys())
            self.logger.info(f"  Scanning {len(channel_ids)} channels for changes…")

            for cid in channel_ids:
                if not cid:
                    continue

                # ── Per-channel change window ────────────────────────────────
                chan_key = generate_record_sync_point_key(
                    RecordType.MESSAGE.value, "changes", cid
                )
                last = await self.audit_log_sync_point.read_sync_point(chan_key)

                oldest_ts: Optional[str] = None
                if last and last.get("last_check_time"):
                    # Slack ts is canonical "<seconds>.<6-digit microseconds>";
                    # str(float) can produce 7+ decimals (e.g.
                    # "1777530605.9567418") which Slack's `oldest`/`latest`
                    # parser silently rejects (success=True, msg_count=0).
                    oldest_ts = f"{last['last_check_time'] / 1000.0:.6f}"
                else:
                    oldest_ts = sync_window_ts

                self.logger.debug(
                    f"  [{cid}] window: "
                    f"{'ts=' + oldest_ts if oldest_ts else 'full (no window limit)'} → now"
                )

                try:
                    count = await self._check_channel_changes(cid, oldest_ts)
                    total_updated += count
                    # Only persist checkpoint after a fully-successful scan so that
                    # a partial failure retries from the old window on the next run.
                    await self.audit_log_sync_point.update_sync_point(
                        chan_key, {"last_check_time": now_ms}
                    )
                except Exception as exc:
                    self.logger.error(
                        f"Change detection failed for {cid}: {exc}", exc_info=True
                    )
                    # Do NOT update checkpoint — next run will retry from old window.

            self.logger.info(f"✅ Change detection — {total_updated} records updated")

        except Exception as exc:
            self.logger.error(f"❌ Change detection failed: {exc}", exc_info=True)

    async def _refresh_user_caches(self) -> None:
        """
        Re-sync user caches from the DB without hitting the Slack API.
        Called at the start of change detection to capture any users
        added after the initial _sync_users() phase.
        """
        try:
            all_users = await self.data_entities_processor.get_all_app_users(
                connector_id=self.connector_id
            )
            for u in all_users:
                if u.source_user_id:
                    if u.email:
                        self.user_id_to_email_cache.setdefault(
                            u.source_user_id, u.email
                        )
                    if u.id:
                        self.user_id_to_internal_id_cache.setdefault(
                            u.source_user_id, u.id
                        )
        except Exception as exc:
            self.logger.error(f"_refresh_user_caches: {exc}")

    async def _find_burst_record_by_message_ts(
        self, channel_id: str, ts: str
    ) -> Optional[MessageRecord]:
        """
        Find the burst MessageRecord that contains the message with the given ts.

        Delegates to the data-store abstraction layer so that the query is
        properly encapsulated in the provider (ArangoDB / Neo4j) rather than
        in the connector.

        Returns None if no such record exists (e.g. the message is standalone).
        """
        try:
            async with self.data_store_provider.transaction() as tx:
                return await tx.find_slack_burst_record_by_ts(
                    connector_id=self.connector_id,
                    channel_id=channel_id,
                    ts=ts,
                )
        except Exception as exc:
            self.logger.error(f"_find_burst_record_by_message_ts({ts}): {exc}")
            return None

    async def _check_channel_changes(
        self, channel_id: str, oldest_ts: Optional[str]
    ) -> int:
        """
        Fully-paginated change scan for one channel within [oldest_ts, now].

        Handles:
          A) Single-message record edited  → rebuild block_containers, republish
          B) Burst member edited           → find burst, update block, republish
          C) Newly-threaded / grown thread → create or update thread RecordGroup
          D) ``message_changed`` subtype   → unwrap inner message, treat as edit

        Skips (non-actionable events in history):
          - Bot-only messages (no human ``user`` field)
          - System event messages (join, leave, archive, pin, reminder …)
          - ``message_deleted`` events
          - Thread replies (``thread_ts != ts``): replies are processed via
            ``_handle_new_thread`` which fetches them from the thread directly.

        Pagination:
          Iterates through ALL pages returned by ``conversations.history`` using
          the ``next_cursor`` from ``response_metadata``.  Each page acquires
          the rate limiter independently so we never exceed Slack's tier limits.

        Returns:
          Count of records that were updated (Cases A + B).
        """
        # Subtypes that carry no meaningful content — skip entirely
        SKIP_SUBTYPES: frozenset = frozenset({
            "bot_message",
            "channel_join",        "channel_leave",
            "channel_archive",     "channel_unarchive",
            "channel_name",        "channel_purpose",      "channel_topic",
            "group_join",          "group_leave",
            "group_archive",       "group_unarchive",
            "group_name",          "group_purpose",        "group_topic",
            "file_comment",        "pinned_item",          "unpinned_item",
            "reminder_add",        "reminder_delete",
            "slackbot_response",   "thread_broadcast",
        })

        # Permanent errors that mean we should stop and not retry for this channel
        TERMINAL_ERRORS: frozenset = frozenset({
            "channel_not_found", "not_in_channel", "is_archived",
            "missing_scope", "invalid_auth", "account_inactive",
        })

        updated = 0
        cursor: Optional[str] = None
        page   = 0

        # Stable upper-bound for the window — avoids fetching messages posted
        # mid-run that have not yet been processed by the messages sync phase.
        # `f"{...:.6f}"` matches Slack's canonical ts format; `str(time.time())`
        # can emit 7 decimals, which the API silently rejects.
        now_ts = f"{time.time():.6f}"

        while True:
            page += 1
            await self.rate_limiter.acquire()
            ds = await self._fresh_datasource()

            try:
                hist_kwargs: dict[str, Any] = dict(
                    channel=channel_id,
                    latest=now_ts,
                    limit=200,
                    cursor=cursor,
                )
                if oldest_ts:
                    hist_kwargs["oldest"] = oldest_ts
                resp = await ds.conversations_history(**hist_kwargs)
            except Exception as exc:
                self.logger.error(
                    f"conversations.history page {page} for {channel_id}: {exc}",
                    exc_info=True,
                )
                break

            if not resp or not resp.success:
                error = getattr(resp, "error", "unknown") if resp else "no_response"
                if error in TERMINAL_ERRORS:
                    self.logger.warning(
                        f"Channel {channel_id} skipped in change detection: {error}"
                    )
                else:
                    self.logger.warning(
                        f"conversations.history({channel_id}) page {page} → {error}"
                    )
                break

            msgs: list[dict[str, Any]] = resp.data.get("messages", [])
            if not msgs:
                break

            self.logger.debug(
                f"  [{channel_id}] page {page}: {len(msgs)} messages"
            )

            for md in msgs:
                ts = md.get("ts")
                if not ts:
                    continue

                subtype: str = md.get("subtype", "")

                # ── D: unwrap message_changed events ──────────────────────
                # Rarely appears in conversations.history but handle defensively.
                if subtype == "message_changed":
                    inner = md.get("message") or {}
                    if inner:
                        md      = inner
                        ts      = md.get("ts", ts)
                        subtype = md.get("subtype", "")

                # ── Skip non-content messages ──────────────────────────────
                if subtype in SKIP_SUBTYPES:
                    continue

                # Skip pure bot messages (bot_id set but no human user)
                if md.get("bot_id") and not md.get("user"):
                    continue

                # Skip deleted messages
                if subtype == "message_deleted":
                    continue

                # Skip thread replies — they live inside threads, not channel history.
                # The parent message check (Case C) will pull all replies via
                # conversations.replies when it detects reply_count > 0.
                if md.get("thread_ts") and md.get("thread_ts") != ts:
                    continue

                try:
                    # ── A / B: edits ───────────────────────────────────────
                    if self._is_edited(md):
                        updated += await self._handle_edited_message(
                            md, channel_id, ts
                        )

                    # ── C: newly-threaded or grown thread ──────────────────
                    # reply_count > 0 and thread_ts == ts → this is a thread root.
                    if md.get("reply_count", 0) > 0 and md.get("thread_ts") == ts:
                        await self._handle_new_thread(md, channel_id, ts)

                except Exception as exc:
                    self.logger.error(
                        f"Change check ts={ts} in {channel_id}: {exc}",
                        exc_info=True,
                    )

            # ── Advance pagination cursor ──────────────────────────────────
            cursor = (
                resp.data.get("response_metadata", {}).get("next_cursor") or ""
            )
            if not cursor:
                break

        return updated

    def _is_edited(self, md: dict[str, Any]) -> bool:
        return "edited" in md

    async def _handle_edited_message(
        self, md: dict[str, Any], channel_id: str, ts: str
    ) -> int:
        """
        Handle an edited Slack message.  Returns 1 if a record was updated, else 0.

        Case A: standalone single-message record (external_record_id == ts).
        Case B: message is a member of a burst record (start_ts ≤ ts ≤ end_ts).

        In both cases we build a fresh, properly-typed MessageRecord so that
        ``to_kafka_record()`` works and the DB is force-updated via a synthetic
        ``external_revision_id``.
        """
        # ── Case A: standalone single-message record ──────────────────────
        existing_base: Optional[Record] = None
        try:
            async with self.data_store_provider.transaction() as tx:
                existing_base = await tx.get_record_by_external_id(
                    connector_id=self.connector_id, external_id=ts
                )
        except Exception as exc:
            self.logger.error(f"DB lookup for ts={ts}: {exc}")
            return 0

        if existing_base:
            if not self._record_changed(md, existing_base):
                return 0

            rg_id = getattr(existing_base, "record_group_id", None)
            ctx   = self._make_ctx(channel_id, rg_id)
            text  = self._replace_mentions_in_text(
                md.get("text", ""), ctx.user_id_to_name, ctx.channel_id_to_name
            )
            bc = self._build_single_block_containers(md, ctx)

            src_ts_ms = int(float(ts) * 1000)
            is_edited = "edited" in md
            upd_ts_ms = src_ts_ms
            if is_edited:
                raw_ets = md.get("edited", {}).get("ts")
                if raw_ets:
                    upd_ts_ms = int(float(raw_ets) * 1000)

            channel_name = ctx.channel_id_to_name.get(ctx.channel_id, ctx.channel_id)
            updated_rec = MessageRecord(
                id=existing_base.id,
                org_id=self.data_entities_processor.org_id,
                record_name=f"{channel_name}_{self._slack_ts_to_utc_label(ts)}",
                record_type=RecordType.MESSAGE,
                record_group_type=RecordGroupType.SLACK_CHANNEL,
                external_record_id=ts,
                external_record_group_id=channel_id,
                record_group_id=rg_id,
                version=existing_base.version + 1,
                external_revision_id=str(get_epoch_timestamp_in_ms()),
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.SLACK_WORKSPACE,
                connector_id=self.connector_id,
                mime_type=MimeTypes.BLOCKS.value,
                weburl=existing_base.weburl,
                source_created_at=existing_base.source_created_at or src_ts_ms,
                source_updated_at=upd_ts_ms,
                content=text,
                is_edited=is_edited,
                edited_timestamp=upd_ts_ms if is_edited else None,
                start_ts=ts,
                end_ts=ts,
                slack_subtype=md.get("subtype"),
                slack_user_id=md.get("user", ""),
                author_id=md.get("user", ""),
                inherit_permissions=True,
                preview_renderable=False,
                block_containers=bc,
                mentioned_user_ids=self._extract_user_mentions(md.get("text", "")),
                mentioned_group_ids=self._extract_channel_mentions(md.get("text", "")),
                involved_user_source_ids=[md.get("user", "")] if md.get("user") else [],
            )
            await self.data_entities_processor.on_record_content_update(updated_rec)
            return 1

        # ── Case B: message is a member of a burst record ─────────────────
        burst_rec = await self._find_burst_record_by_message_ts(channel_id, ts)
        if not burst_rec:
            return 0  # message not yet synced — nothing to update

        ctx = self._make_ctx(channel_id, burst_rec.record_group_id)

        # Fetch all messages in the burst window from Slack and rebuild block_containers
        # (block_containers are NOT stored in the DB; we must reconstruct them).
        try:
            await self.rate_limiter.acquire()
            ds   = await self._fresh_datasource()
            resp = await ds.conversations_history(
                channel=channel_id,
                oldest=burst_rec.start_ts,
                latest=burst_rec.end_ts,
                inclusive=True,
                limit=200,
            )
            if not resp or not resp.success:
                self.logger.warning(
                    f"Could not fetch burst messages for channel={channel_id} "
                    f"ts={ts}; skipping burst update"
                )
                return 0

            burst_msgs = sorted(
                [
                    m for m in resp.data.get("messages", [])
                    if (
                        m.get("ts")
                        and burst_rec.start_ts <= m["ts"] <= burst_rec.end_ts
                    )
                ],
                key=lambda m: m["ts"],
            )
        except Exception as exc:
            self.logger.error(f"Fetching burst messages for ts={ts}: {exc}")
            return 0

        if not burst_msgs:
            return 0

        # Rebuild the block_containers with the updated message content
        bc = self._build_burst_block_containers(
            burst_msgs, burst_rec.external_record_id, ctx
        )
        # Update aggregated content
        new_content = "\n\n".join(
            self._replace_mentions_in_text(
                m.get("text", ""), ctx.user_id_to_name, ctx.channel_id_to_name
            )
            for m in burst_msgs if m.get("text")
        )
        burst_rec.content       = new_content
        burst_rec.block_containers = bc
        burst_rec.version      += 1
        # Synthetic revision forces _handle_updated_record to DB-upsert
        burst_rec.external_revision_id = str(get_epoch_timestamp_in_ms())

        await self.data_entities_processor.on_record_content_update(burst_rec)
        return 1

    def _make_ctx(
        self, channel_id: str, rg_id: Optional[str]
    ) -> ProcessingContext:
        return ProcessingContext(
            channel_id=channel_id,
            channel_groups_map={channel_id: rg_id} if rg_id else {},
            user_id_to_email=dict(self.user_id_to_email_cache),
            user_id_to_name=dict(self.user_id_to_name_cache),
            channel_id_to_name=dict(self.channel_id_to_name_cache),
            rate_limiter=self.rate_limiter,
        )

    async def _handle_new_thread(
        self, md: dict[str, Any], channel_id: str, ts: str
    ) -> None:
        """
        Handle a thread root discovered during change detection.

        Decision is gated on the per-thread sync_point, not on whether the
        thread RecordGroup node already exists. A full sync deletes sync_points
        (and sync edges) but keeps RG nodes; gating on RG existence would
        silently take the incremental path on full sync and leave the RG's
        edges (BELONGS_TO / INHERIT_PERMISSIONS / PERMISSION) unrecreated.

        sync_point absent  → _process_thread: re-emits the RG and refetches
                             every reply, rebuilding all edges.
        sync_point present → incremental: only fetch replies newer than the
                             stored ts.
        """
        rg_id: Optional[str] = None
        try:
            async with self.data_store_provider.transaction() as tx:
                existing_rg_chan = await tx.get_record_group_by_external_id(
                    external_id=channel_id,
                    connector_id=self.connector_id,
                )
                if existing_rg_chan:
                    rg_id = existing_rg_chan.id
        except Exception:
            pass

        ctx = self._make_ctx(channel_id, rg_id)

        thread_sp_key = generate_record_sync_point_key(
            RecordType.MESSAGE.value, "slack_thread", f"{channel_id}_{ts}"
        )

        sp_data = await self.audit_log_sync_point.read_sync_point(thread_sp_key)
        last_reply_ts = (sp_data or {}).get("last_reply_ts")

        # If we have a checkpoint, look up the thread RG arango id for the
        # incremental path. Failure here is fatal for the incremental branch
        # (we'd publish bursts with an empty record_group_id), so we fall
        # through to the full path on lookup error.
        thread_rg_id = ""
        if last_reply_ts:
            try:
                async with self.data_store_provider.transaction() as tx:
                    existing_rg = await tx.get_record_group_by_external_id(
                        external_id=f"thread_{channel_id}_{ts}",
                        connector_id=self.connector_id,
                    )
                    if existing_rg:
                        thread_rg_id = existing_rg.id or ""
            except Exception as exc:
                self.logger.error(f"[ThreadGrowth] RG lookup for thread {ts}: {exc}")

        # No checkpoint, or checkpoint exists but RG node missing/lookup
        # failed (orphan sync_point) ⇒ full path that rebuilds the RG.
        if not last_reply_ts or not thread_rg_id:
            self.logger.info(
                f"[ThreadGrowth] Fresh sync for thread ts={ts}, channel={channel_id}, "
                f"reply_count={md.get('reply_count')}, channel_rg_id={rg_id}"
            )
            await self._process_thread(md, ctx, rg_id)

            latest_reply = md.get("latest_reply") or ts
            await self.audit_log_sync_point.update_sync_point(
                thread_sp_key, {"last_reply_ts": latest_reply}
            )
            return

        # ── Incremental ───────────────────────────────────────────────────
        await self._process_thread_incremental(
            md, ctx, thread_rg_id, last_reply_ts,
        )

        latest_reply = md.get("latest_reply") or last_reply_ts
        await self.audit_log_sync_point.update_sync_point(
            thread_sp_key, {"last_reply_ts": latest_reply}
        )

    def _record_changed(
        self, md: dict[str, Any], existing: Any
    ) -> bool:
        """Return True if any detectable field has changed since last sync."""
        # Edit newly detected
        if "edited" in md and not getattr(existing, "is_edited", False):
            return True
        # Text changed after edit
        if "edited" in md:
            if md.get("text", "") != getattr(existing, "content", ""):
                return True
        # Reactions changed
        return self._reactions_changed(
            md.get("reactions", []),
            getattr(existing, "reactions", []) or [],
        )

    @staticmethod
    def _reactions_changed(
        current: list[dict], existing: list[dict]
    ) -> bool:
        def _key(r: dict) -> tuple:
            return (r.get("name", ""), r.get("count", 0),
                    tuple(sorted(r.get("users", []))))
        return {_key(r) for r in current} != {_key(r) for r in existing}

    # =========================================================================
    # 8.  DB / cache helpers
    # =========================================================================

    async def _channel_group_map(
        self, channel_ids: list[str]
    ) -> dict[str, str]:
        """
        Return channel_id → RecordGroup._key for the given channels.
        Serves from cache first; falls back to a single DB transaction
        for any cache misses.
        """
        result:   dict[str, str] = {}
        uncached: list[str]      = []

        for cid in channel_ids:
            if cid in self.channel_groups_cache:
                result[cid] = self.channel_groups_cache[cid]
            else:
                uncached.append(cid)

        if uncached:
            try:
                async with self.data_store_provider.transaction() as tx:
                    for cid in uncached:
                        rg = await tx.get_record_group_by_external_id(
                            external_id=cid, connector_id=self.connector_id
                        )
                        if rg:
                            result[cid] = rg.id
                            self.channel_groups_cache[cid] = rg.id
            except Exception as exc:
                self.logger.error(f"_channel_group_map DB fallback: {exc}")

        return result

    # =========================================================================
    # 8b. Channel filter cache helpers
    # =========================================================================

    @staticmethod
    def _is_invalid_cursor_error(error: Optional[str]) -> bool:
        if not error:
            return False
        err = error.lower()
        return "invalid_cursor" in err or "no longer valid" in err

    async def _populate_channel_filter_cache(self) -> None:
        """Fetch all channels from Slack and rebuild the in-memory filter cache.
        """
        # Fast path: another coroutine is already rebuilding — wait for it.
        if self._cache_rebuild_event is not None:
            await self._cache_rebuild_event.wait()
            return

        self._cache_rebuild_event = asyncio.Event()
        try:
            tmp: dict[str, dict[str, str]] = {}
            cursor: Optional[str] = None

            while True:
                await self.rate_limiter.acquire()
                ds = await self._fresh_datasource()
                resp = await ds.conversations_list(
                    exclude_archived=True,
                    types="public_channel,private_channel",
                    limit=PAGE_SIZE_CHANNELS,
                    cursor=cursor,
                )

                if not resp or not resp.success:
                    error = getattr(resp, "error", "no response") if resp else "no response"
                    raise RuntimeError(f"conversations.list cache rebuild failed: {error}")

                data = resp.data or {}
                for ch in data.get("channels", []):
                    cid = ch.get("id")
                    if not cid:
                        continue
                    tmp[cid] = {
                        "id": cid,
                        "label": ch.get("name") or f"Channel: {cid}",
                    }

                cursor = data.get("response_metadata", {}).get("next_cursor") or None
                if not cursor:
                    break

            # Atomic swap — readers never observe a partially-empty cache.
            self.channel_filter_cache = tmp
            self.logger.info(
                "Channel filter cache rebuilt: %d channels", len(self.channel_filter_cache)
            )
        finally:
            # Always signal waiters (even on error) and reset the sentinel so
            # the next caller can trigger a fresh rebuild if needed.
            ev = self._cache_rebuild_event
            self._cache_rebuild_event = None
            ev.set()

    # =========================================================================
    # 9.  Text extraction utilities
    # =========================================================================

    @staticmethod
    def _extract_user_mentions(text: str) -> list[str]:
        """Extract Slack user IDs from <@U…> / <@W…> syntax."""
        return re.findall(r"<@([UW]\w+)>", text)

    @staticmethod
    def _extract_channel_mentions(text: str) -> list[str]:
        """Extract Slack channel IDs from <#C…> / <#G…> syntax."""
        return re.findall(r"<#([CG]\w+)(?:\|[^>]+)?>", text)

    def _replace_mentions_in_text(
        self,
        text: str,
        user_id_to_name: dict[str, str],
        channel_id_to_name: dict[str, str],
    ) -> str:
        """
        Replace Slack mention syntax with actual names:
        - <@U09AEBH69JS> → @John Doe (or @email if name not available)
        - <#C123|general> → #general
        - <#C123> → #Channel Name

        Args:
            text: Original message text with Slack mention syntax
            user_id_to_name: Mapping of Slack user_id → user name
            channel_id_to_name: Mapping of Slack channel_id → channel name

        Returns:
            Text with mentions replaced by names
        """
        if not text:
            return text

        # Replace user mentions: <@U09AEBH69JS> or <@U09AEBH69JS|display_name>
        def replace_user_mention(match) -> str:
            user_id = match.group(1)
            display_name = match.group(2) if match.group(2) else None

            # Use display name from mention if available, otherwise lookup in cache
            if display_name:
                return f"@{display_name}"

            # Lookup user name in cache
            name = user_id_to_name.get(user_id)
            if name:
                return f"@{name}"

            # Fallback to email if available
            email = self.user_id_to_email_cache.get(user_id)
            if email:
                return f"@{email}"

            # Final fallback: keep the user ID (log warning for cache miss)
            self.logger.debug(f"User mention cache miss for user_id: {user_id}")
            return f"@{user_id}"

        # Pattern: <@U09AEBH69JS> or <@U09AEBH69JS|display_name>
        text = re.sub(r"<@([UW]\w+)(?:\|([^>]+))?>", replace_user_mention, text)

        # Replace channel mentions: <#C123> or <#C123|channel-name>
        def replace_channel_mention(match) -> str:
            channel_id = match.group(1)
            display_name = match.group(2) if match.group(2) else None

            # Use display name from mention if available
            if display_name:
                return f"#{display_name}"

            # Lookup channel name in cache
            name = channel_id_to_name.get(channel_id)
            if name:
                return f"#{name}"

            # Fallback: keep the channel ID (log warning for cache miss)
            self.logger.debug(f"Channel mention cache miss for channel_id: {channel_id}")
            return f"#{channel_id}"

        # Pattern: <#C123> or <#C123|channel-name>
        return re.sub(r"<#([CG]\w+)(?:\|([^>]+))?>", replace_channel_mention, text)


    @staticmethod
    def _extract_urls(text: str) -> list[str]:
        """Extract HTTP/HTTPS URLs from both Slack mrkdwn and plain text."""
        return [
            m[0] or m[1]
            for m in re.findall(r"<(https?://[^>|]+)(?:\|[^>]+)?>|(https?://\S+)", text)
            if m[0] or m[1]
        ]

    # =========================================================================
    # 10.  Link classification
    # =========================================================================

    @staticmethod
    def _classify_url(url: str) -> tuple[str, dict[str, Any]]:
        from urllib.parse import urlparse
        try:
            p    = urlparse(url)
            host = (p.hostname or "").lower()

            if "atlassian.net" in host or "jira" in host:
                m = re.search(r"/browse/([A-Z]+-\d+)", p.path)
                return "jira", {"ticket_key": m.group(1), "url": url} if m else {"url": url}

            if "github.com" in host:
                m = re.search(r"/([^/]+)/([^/]+)/(issues|pull)/(\d+)", p.path)
                if m:
                    return "github", {
                        "owner": m.group(1), "repo": m.group(2),
                        "type":  m.group(3), "number": m.group(4), "url": url,
                    }
                return "github", {"url": url}

            if "drive.google.com" in host or "docs.google.com" in host:
                m = re.search(r"/d/([a-zA-Z0-9-_]+)", p.path)
                return "gdrive", {"file_id": m.group(1), "url": url} if m else {"url": url}

            return "web", {"url": url}
        except Exception:
            return "web", {"url": url}

    @staticmethod
    def _unfurl_data(url: str, msg: dict[str, Any]) -> dict[str, Any]:
        """Match a URL to its Slack attachment (unfurl) metadata."""
        for att in msg.get("attachments", []):
            from_url = att.get("from_url", "")
            if from_url == url or url in from_url:
                return {
                    "service_name": att.get("service_name"),
                    "title":        att.get("title"),
                    "text":         att.get("text"),
                    "image_url":    att.get("image_url"),
                    "fields":       att.get("fields", []),
                    "author_name":  att.get("author_name"),
                }
        return {}

    # =========================================================================
    # 11.  Streaming
    # =========================================================================

    async def stream_record(
        self,
        record:    Record,
        user_id:   Optional[str] = None,
        convertTo: Optional[str] = None,
    ) -> StreamingResponse:
        try:
            if record.record_type == RecordType.MESSAGE:
                content_bytes = await self._build_message_blocks_for_streaming(record)

                return StreamingResponse(
                    iter([content_bytes]),
                    media_type=MimeTypes.BLOCKS.value,
                    headers={"Content-Disposition":
                             f'inline; filename="msg_{record.external_record_id}.json"'}
                )

            if record.record_type == RecordType.FILE:
                return create_stream_record_response(
                    self._stream_file_bytes(record),
                    filename=record.record_name or record.external_record_id,
                    mime_type=record.mime_type,
                    fallback_filename=f"file_{record.id}",
                )

            if record.record_type == RecordType.LINK:
                # Stream link as markdown (same as Linear connector)
                if not record.weburl:
                    raise ValueError(f"LinkRecord {record.external_record_id} missing weburl")
                link_name = record.record_name or "Link"
                markdown_content = f"# {link_name}\n\n[{record.weburl}]({record.weburl})"
                return StreamingResponse(
                    iter([markdown_content.encode("utf-8")]),
                    media_type=MimeTypes.MARKDOWN.value,
                    headers={
                        "Content-Disposition": f'inline; filename="{record.external_record_id}.md"'
                    },
                )

            raise HTTPException(400, f"Unsupported record type: {record.record_type}")

        except HTTPException:
            raise
        except Exception as exc:
            self.logger.error(f"stream_record failed: {exc}", exc_info=True)
            raise HTTPException(500, str(exc))

    async def _build_message_blocks_for_streaming(
        self, record: MessageRecord
    ) -> bytes:
        """
        Rebuild a BlocksContainer on-demand for a message record and return
        serialised JSON bytes.  Handles burst, thread, and single-message
        record sub-types by fetching the relevant messages from the Slack API.
        """
        ext_id = record.external_record_id
        ch     = record.external_record_group_id
        if not ext_id or not ch:
            raise HTTPException(400, f"Missing id/channel for record {record.id}")

        # Thread records have external_record_group_id = "thread_{channel_id}_{ts}",
        # extract the real Slack channel ID for API calls.
        if ch.startswith("thread_"):
            parts = ch.split("_", 2)  # ["thread", channel_id, thread_ts]
            if len(parts) >= 2:
                ch = parts[1]

        rg_id = getattr(record, "record_group_id", None)
        ctx   = self._make_ctx(ch, rg_id)

        slack_subtype = getattr(record, "slack_subtype", None)

        # ── Burst record: fetch messages in the time window ───────────────
        if slack_subtype == "burst" or ext_id.startswith("burst_"):
            start_ts = getattr(record, "start_ts", None)
            end_ts   = getattr(record, "end_ts", None)
            if not start_ts or not end_ts:
                raise HTTPException(400, f"Burst record {ext_id} missing start_ts/end_ts")

            await self.rate_limiter.acquire()
            ds   = await self._fresh_datasource()
            resp = await ds.conversations_history(
                channel=ch, oldest=start_ts, latest=end_ts,
                inclusive=True, limit=200,
            )
            if not resp or not resp.success:
                raise HTTPException(404, f"Could not fetch burst messages: {ext_id}")

            burst_msgs = sorted(
                [m for m in resp.data.get("messages", [])
                 if m.get("ts") and start_ts <= m["ts"] <= end_ts],
                key=lambda m: m["ts"],
            )
            if not burst_msgs:
                raise HTTPException(404, f"No messages found for burst: {ext_id}")

            # Resolve file ChildRecords from DB, keyed by message ts
            file_children_by_ts: dict[str, list[ChildRecord]] = {}
            try:
                async with self.data_store_provider.transaction() as tx:
                    child_records = await tx.get_records_by_parent(
                        connector_id=self.connector_id,
                        parent_external_record_id=ext_id,
                        record_type=RecordType.FILE.value,
                    )
                    ext_id_to_child: dict[str, ChildRecord] = {}
                    for cr in (child_records or []):
                        ext_id_to_child[cr.external_record_id] = ChildRecord(
                            child_type=ChildType.RECORD, child_id=cr.id, child_name=cr.record_name
                        )
                    for msg in burst_msgs:
                        mts = msg.get("ts", "")
                        for fd in msg.get("files", []):
                            fid = fd.get("id", "")
                            if fid in ext_id_to_child:
                                file_children_by_ts.setdefault(mts, []).append(ext_id_to_child[fid])
            except Exception:
                pass

            bc = self._build_burst_block_containers(
                burst_msgs, ext_id, ctx, file_child_records=file_children_by_ts
            )

        # ── Thread burst record: fetch thread replies in burst window ────
        elif slack_subtype == "thread_burst" or ext_id.startswith("thread_burst_"):
            start_ts = getattr(record, "start_ts", None)
            end_ts   = getattr(record, "end_ts", None)
            thread_ts_val = getattr(record, "thread_ts", None)
            if not start_ts or not end_ts or not thread_ts_val:
                raise HTTPException(
                    400,
                    f"Thread burst record {ext_id} missing start_ts/end_ts/thread_ts",
                )

            await self.rate_limiter.acquire()
            ds   = await self._fresh_datasource()
            resp = await ds.conversations_replies(
                channel=ch, ts=thread_ts_val,
                oldest=start_ts, latest=end_ts,
                inclusive=True, limit=200,
            )
            if not resp or not resp.success:
                raise HTTPException(404, f"Could not fetch thread burst messages: {ext_id}")

            burst_msgs = sorted(
                [m for m in resp.data.get("messages", [])
                 if m.get("ts") and start_ts <= m["ts"] <= end_ts],
                key=lambda m: m["ts"],
            )
            if not burst_msgs:
                raise HTTPException(404, f"No messages found for thread burst: {ext_id}")

            file_children_by_ts: dict[str, list[ChildRecord]] = {}
            try:
                async with self.data_store_provider.transaction() as tx:
                    child_records = await tx.get_records_by_parent(
                        connector_id=self.connector_id,
                        parent_external_record_id=ext_id,
                        record_type=RecordType.FILE.value,
                    )
                    ext_id_to_child: dict[str, ChildRecord] = {}
                    for cr in (child_records or []):
                        ext_id_to_child[cr.external_record_id] = ChildRecord(
                            child_type=ChildType.RECORD, child_id=cr.id, child_name=cr.record_name
                        )
                    for msg in burst_msgs:
                        mts = msg.get("ts", "")
                        for fd in msg.get("files", []):
                            fid = fd.get("id", "")
                            if fid in ext_id_to_child:
                                file_children_by_ts.setdefault(mts, []).append(ext_id_to_child[fid])
            except Exception:
                pass

            bc = self._build_burst_block_containers(
                burst_msgs, ext_id, ctx, file_child_records=file_children_by_ts
            )

        # ── Single message record ─────────────────────────────────────────
        else:
            await self.rate_limiter.acquire()
            ds   = await self._fresh_datasource()
            resp = await ds.conversations_history(
                channel=ch, oldest=ext_id, latest=ext_id,
                inclusive=True, limit=1,
            )
            msg = None
            if resp and resp.success:
                msgs = resp.data.get("messages", [])
                if msgs:
                    msg = msgs[0]

            if not msg:
                tid = getattr(record, "thread_id", None)
                if tid and tid != ext_id:
                    await self.rate_limiter.acquire()
                    resp = await (await self._fresh_datasource()).conversations_replies(
                        channel=ch, ts=tid, oldest=ext_id, latest=ext_id,
                        inclusive=True, limit=1,
                    )
                    if resp and resp.success:
                        msgs = resp.data.get("messages", [])
                        if msgs:
                            msg = msgs[0]

            if not msg:
                raise HTTPException(404, f"Message not found: {ext_id}")

            # Resolve file ChildRecords
            file_children: list[ChildRecord] = []
            try:
                async with self.data_store_provider.transaction() as tx:
                    child_records = await tx.get_records_by_parent(
                        connector_id=self.connector_id,
                        parent_external_record_id=ext_id,
                        record_type=RecordType.FILE.value,
                    )
                    for cr in (child_records or []):
                        file_children.append(
                            ChildRecord(child_type=ChildType.RECORD, child_id=cr.id, child_name=cr.record_name)
                        )
            except Exception:
                pass

            bc = self._build_single_block_containers(
                msg, ctx, children_records=file_children or None
            )

        blocks_json = bc.model_dump_json(indent=2, exclude_none=True)
        return blocks_json.encode("utf-8")

    async def _stream_file_bytes(
        self, record: FileRecord
    ) -> AsyncGenerator[bytes, None]:
        fid = record.external_record_id
        if not fid:
            raise HTTPException(400, f"No file ID for {record.id}")

        await self.rate_limiter.acquire()
        ds   = await self._fresh_datasource()
        info = await ds.files_info(file=fid)
        if not info or not info.success:
            raise HTTPException(404, f"File not found: {fid}")

        fd  = info.data.get("file", {})
        url = fd.get("url_private_download") or fd.get("url_private")
        if not url:
            raise HTTPException(404, f"No download URL for file {fid}")

        token = getattr(
            self.external_client.get_client(), "get_token", lambda: None
        )()
        if not token:
            self.logger.warning(f"No auth token available for file download {fid}, attempting without auth")
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as http:
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            async with http.stream("GET", url, headers=headers) as r:
                if r.status_code != 200:
                    raise HTTPException(r.status_code, f"Download failed: {fid}")
                content_type = r.headers.get("content-type", "")
                if "text/html" in content_type:
                    raise HTTPException(403, f"Received HTML instead of file content for {fid} — likely an auth issue")
                async for chunk in r.aiter_bytes(8192):
                    yield chunk


    # =========================================================================
    # 12.  Reindexing
    # =========================================================================

    async def reindex_records(self, record_results: list[Record]) -> None:
        """
        Reindex a batch of records:
          - Changed records → on_record_content_update (DB write + re-index event)
          - Unchanged records → reindex_existing_records (re-index event only)
        """
        if not record_results:
            return

        self.logger.info(f"Re-indexing {len(record_results)} records…")
        updated:     list[tuple[Record, list[Permission]]] = []
        not_updated: list[Record]                          = []

        for rec in record_results:
            try:
                result = await self._check_updated(rec)
                if result:
                    updated.append(result)
                else:
                    not_updated.append(rec)
            except Exception as exc:
                self.logger.error(f"reindex check failed for {rec.id}: {exc}")

        for rec, perms in updated:
            await self.data_entities_processor.on_record_content_update(rec)
            if perms:
                await self.data_entities_processor.on_updated_record_permissions(rec, perms)

        if not_updated:
            await self.data_entities_processor.reindex_existing_records(not_updated)

        self.logger.info(
            f"Reindex done — updated: {len(updated)}, unchanged: {len(not_updated)}"
        )

    async def _check_updated(
        self, rec: Record
    ) -> Optional[tuple[Record, list[Permission]]]:
        if rec.record_type == RecordType.MESSAGE:
            return await self._check_updated_message(rec)
        if rec.record_type == RecordType.FILE:
            return await self._check_updated_file(rec)
        return None   # Links are derived; they don't change independently

    async def _check_updated_message(
        self, rec: MessageRecord
    ) -> Optional[tuple[Record, list[Permission]]]:
        ts = rec.external_record_id
        ch = rec.external_record_group_id
        if not ts or not ch:
            return None

        # Burst records and thread-burst records are synthetic aggregates.
        # They are updated via _check_channel_changes, not here.
        if getattr(rec, "slack_subtype", None) in ("burst", "thread_burst"):
            return None
        if rec.external_record_id and rec.external_record_id.startswith(("burst_", "thread_")):
            return None

        await self.rate_limiter.acquire()
        ds   = await self._fresh_datasource()
        resp = await ds.conversations_history(
            channel=ch, oldest=ts, latest=ts, inclusive=True, limit=1
        )

        md: Optional[dict[str, Any]] = None
        if resp and resp.success:
            msgs = resp.data.get("messages", [])
            if msgs:
                md = msgs[0]

        if not md:
            tid = getattr(rec, "thread_id", None)
            if tid and tid != ts:
                await self.rate_limiter.acquire()
                r2 = await (await self._fresh_datasource()).conversations_replies(
                    channel=ch, ts=tid, oldest=ts, latest=ts, inclusive=True, limit=1
                )
                if r2 and r2.success:
                    msgs = r2.data.get("messages", [])
                    if msgs:
                        md = msgs[0]

        if not md:
            return None

        if not self._record_changed(md, rec):
            return None

        rg_id = getattr(rec, "record_group_id", None)
        ctx   = ProcessingContext(
            channel_id=ch,
            channel_groups_map={ch: rg_id} if rg_id else {},
            user_id_to_email=dict(self.user_id_to_email_cache),
            user_id_to_name=dict(self.user_id_to_name_cache),
            channel_id_to_name=dict(self.channel_id_to_name_cache),
            rate_limiter=self.rate_limiter,
        )
        updated = await self._to_message_record(md, ctx)
        if not updated:
            return None

        updated.id      = rec.id
        updated.version = rec.version + 1
        return (updated, [])

    async def _check_updated_file(
        self, rec: FileRecord
    ) -> Optional[tuple[Record, list[Permission]]]:
        fid = rec.external_record_id
        if not fid:
            return None

        await self.rate_limiter.acquire()
        ds   = await self._fresh_datasource()
        info = await ds.files_info(file=fid)
        if not info or not info.success:
            return None

        fd = info.data.get("file", {})
        if not fd:
            return None

        file_ts    = int(fd.get("created", 0)) * 1000
        existing_ts = rec.source_updated_at or rec.source_created_at
        if existing_ts and file_ts <= existing_ts:
            return None

        # Need the parent message to set parent_node_id correctly
        parent_ext_id = rec.parent_external_record_id
        ch            = rec.external_record_group_id
        if not parent_ext_id or not ch:
            return None

        try:
            parent = await self.data_entities_processor.get_record_by_external_id(
                connector_id=self.connector_id, external_record_id=parent_ext_id
            )
        except Exception:
            parent = None

        if not parent or not isinstance(parent, MessageRecord):
            return None

        rg_id = getattr(rec, "record_group_id", None)
        ctx   = ProcessingContext(
            channel_id=ch,
            channel_groups_map={ch: rg_id} if rg_id else {},
            user_id_to_email=dict(self.user_id_to_email_cache),
            user_id_to_name=dict(self.user_id_to_name_cache),
            channel_id_to_name=dict(self.channel_id_to_name_cache),
            rate_limiter=self.rate_limiter,
        )
        updated = await self._process_file(fd, parent, ctx)
        if not updated:
            return None

        updated.id      = rec.id
        updated.version = rec.version + 1
        return (updated, [])

    # =========================================================================
    # 13.  Dynamic filter options
    # =========================================================================

    async def get_filter_options(
        self,
        filter_key: str,
        page:   int = 1,
        limit:  int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None,
    ) -> FilterOptionsResponse:
        if filter_key != "channel_ids":
            raise ValueError(f"Unknown filter key: {filter_key}")

        try:
            page  = max(1, page)
            limit = max(1, min(limit, 100))

            if not self.channel_filter_cache or not search or not search.strip():
                await self._populate_channel_filter_cache()

            channels = list(self.channel_filter_cache.values())
            channels.sort(key=lambda c: (c.get("label", "").lower(), c.get("id", "")))

            if search and search.strip():
                sl = search.strip().lower()
                channels = [
                    c for c in channels
                    if sl in c.get("label", "").lower() or sl in c.get("id", "").lower()
                ]

            # Frontend uses cursor-based "Load More"; page offset is the fallback
            # for the very first request when no cursor has been issued yet.
            if cursor:
                try:
                    offset = max(0, int(cursor))
                except ValueError:
                    offset = 0
            else:
                offset = (page - 1) * limit

            page_items = channels[offset : offset + limit]
            options = [
                FilterOption(id=item["id"], label=item["label"])
                for item in page_items
            ]

            next_offset = offset + len(page_items)
            has_more    = next_offset < len(channels)
            next_cursor = str(next_offset) if has_more else None

            return FilterOptionsResponse(
                success=True, options=options, page=page, limit=limit,
                has_more=has_more,
                cursor=next_cursor,
            )

        except Exception as exc:
            self.logger.error(f"get_filter_options: {exc}", exc_info=True)
            return FilterOptionsResponse(
                success=False, options=[], page=page, limit=limit,
                has_more=False, message=str(exc),
            )

    # =========================================================================
    # 14.  BaseConnector interface
    # =========================================================================

    async def test_connection_and_access(self) -> bool:
        try:
            ds   = await self._fresh_datasource()
            resp = await ds.check_token_scopes()
            return bool(resp and resp.success)
        except Exception as exc:
            self.logger.error(f"Connection test failed: {exc}")
            return False

    async def run_incremental_sync(self) -> None:
        await self.run_sync()

    async def cleanup(self) -> None:
        self.logger.info("Slack connector cleanup complete")

    async def get_signed_url(self, record: Record) -> str:
        return ""   # Slack uses OAuth bearer tokens, not signed URLs

    async def handle_webhook_notification(self, notification: dict) -> None:
        self.logger.warning("Webhook notifications not yet implemented for Slack")

    # =========================================================================
    # 15.  Factory
    # =========================================================================

    @classmethod
    async def create_connector(
        cls,
        logger:               Logger,
        data_store_provider:  DataStoreProvider,
        config_service:       ConfigurationService,
        connector_id:         str,
        scope:                str,
        created_by:           str,
    ) -> "SlackConnector":
        dep = DataSourceEntitiesProcessor(logger, data_store_provider, config_service)
        await dep.initialize()
        return cls(logger, dep, data_store_provider, config_service, connector_id, scope, created_by)
