"""Zoom connector — all past user meetings with incremental sync via report APIs."""

import asyncio
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from logging import Logger
from typing import Any, Optional
from uuid import uuid4

from fastapi import HTTPException  # pyright: ignore[reportMissingImports]
from fastapi.responses import StreamingResponse  # pyright: ignore[reportMissingImports]
from pydantic import BaseModel, Field  # pyright: ignore[reportMissingImports]

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    AppGroups,
    Connectors,
    MimeTypes,
    ProgressStatus,
)
from app.connectors.core.constants import IconPaths
from app.connectors.core.base.connector.connector_service import (
    BaseConnector,
    IncompleteSyncError,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.error.stream_errors import (
    connector_not_ready,
    raise_for_stream_fetch,
    to_stream_error,
)
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
from app.connectors.core.constants import CONNECTOR_EMAIL_IDENTITY_INFO
from app.connectors.core.registry.filters import (
    FilterCategory,
    FilterCollection,
    FilterField,
    FilterOptionsResponse,
    FilterType,
    load_connector_filters,
)
from app.connectors.core.sync.full_sync_coverage import FullSyncCoverage
from app.connectors.sources.zoom.common.apps import ZoomApp
from app.models.blocks import (
    BlockGroup,
    BlocksContainer,
    DataFormat,
    GroupSubType,
    GroupType,
)
from app.models.entities import (
    AppUser,
    MeetingRecord,
    OriginTypes,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.zoom.zoom import ZoomClient
from app.sources.external.zoom.zoom import ZoomDataSource
from app.utils.text_fragments import SourceFormat, build_text_fragment_url
from app.utils.time_conversion import get_epoch_timestamp_in_ms, parse_timestamp

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ZOOM_PAGE_SIZE = 300
ZOOM_REPORT_SYNC_KEY_PREFIX = "zoom_report_last_sync"
ZOOM_REPORT_MAX_RANGE_DAYS = 30
ZOOM_REPORT_MAX_HISTORY_DAYS = 180
ZOOM_TRANSCRIPT_LISTING_URL = "https://zoom.us/recording/meeting/transcript"

_ZOOM_CODE_NO_AI_TRANSCRIPT = 3322
_ZOOM_CODES_MEETING_NOT_FOUND = {3001, 3301}

# Maximum number of meetings processed concurrently within a single chunk.
# Kept conservative to stay under Zoom's Heavy-rate endpoint limits
# (GET /v2/report/meetings/{id}/participants is the bottleneck at ~10-20 req/s).
_MEETING_CONCURRENCY = 8

# Stored meetings read per page when a full sync keeps those older than its report range.
_STORED_MEETINGS_PAGE = 500


class ZoomReadError(Exception):
    """A listing Zoom answered in a way that cannot be the whole list."""


class ZoomIncompleteSyncError(IncompleteSyncError):
    """The run reached its end but left part of Zoom unread; see full_sync_coverage."""


def _page_items(data: object, key: str) -> Optional[list[Any]]:
    """The items of a listing's page, or None when the answer holds no such list: that is not an empty page."""
    items = data.get(key) if isinstance(data, dict) else None
    return items if isinstance(items, list) else None


def _next_page(data: dict[str, Any], current: Optional[str], what: str) -> Optional[str]:
    """The next page's token. Zoom sending the same one again would page for ever."""
    token = data.get("next_page_token") or None
    if token is not None and token == current:
        raise ZoomReadError(f"Zoom {what}: the page token came back")
    return token

# ---------------------------------------------------------------------------
# Zoom API response models
# ---------------------------------------------------------------------------


class ZoomUser(BaseModel):
    """User object returned by GET /v2/users."""

    id: str = ""
    email: str = ""
    first_name: str = ""
    last_name: str = ""
    display_name: str = ""
    created_at: Optional[str] = None
    user_created_at: Optional[str] = None
    last_login_time: Optional[str] = None

    model_config = {"extra": "ignore"}


class ZoomMeetingReport(BaseModel):
    """Meeting object returned by GET /v2/report/users/{userId}/meetings."""

    uuid: str = ""
    id: Optional[int] = None
    topic: str = "Zoom Meeting"
    host_id: str = ""
    start_time: str = ""
    end_time: str = ""
    duration: Optional[int] = None
    type: Optional[int] = None

    model_config = {"extra": "ignore"}


class ZoomMeetingInvitee(BaseModel):
    """Invitee entry inside meeting settings."""

    email: str = ""

    model_config = {"extra": "ignore"}


class ZoomMeetingSettings(BaseModel):
    """Subset of meeting settings used by this connector."""

    alternative_hosts: str = ""
    meeting_invitees: list[ZoomMeetingInvitee] = Field(default_factory=list)

    model_config = {"extra": "ignore"}


class ZoomMeetingDetail(BaseModel):
    """Full meeting object returned by GET /v2/meetings/{meetingId}."""

    id: Optional[int] = None
    join_url: str = ""
    settings: ZoomMeetingSettings = Field(default_factory=ZoomMeetingSettings)

    model_config = {"extra": "ignore"}


class ZoomParticipant(BaseModel):
    """Participant entry returned by GET /v2/report/meetings/{meetingId}/participants."""

    name: str = ""
    user_email: str = ""
    duration: int = 0
    join_time: str = ""
    leave_time: str = ""

    model_config = {"extra": "ignore"}


class ZoomRecordingDetail(BaseModel):
    """Top-level response from GET /v2/meetings/{meetingId}/recordings."""

    share_url: str = ""

    model_config = {"extra": "ignore"}


# ---------------------------------------------------------------------------
# ConnectorBuilder
# ---------------------------------------------------------------------------


@ConnectorBuilder("Zoom")\
    .in_group(AppGroups.ZOOM.value)\
    .with_description("Zoom")\
    .with_categories(["Video Conferencing", "Meetings"])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Zoom",
            authorize_url="https://zoom.us/oauth/authorize",
            token_url="https://zoom.us/oauth/token",
            redirect_uri="connectors/oauth/callback/Zoom",
            scopes=OAuthScopeConfig(
                personal_sync=[],
                team_sync=[
                    "user:read:list_users:admin",
                    "user:read:user:admin",
                    "report:read:user:admin",
                    "report:read:list_meeting_participants:admin",
                    "meeting:read:meeting:admin",
                    "cloud_recording:read:meeting_transcript:admin",
                    "cloud_recording:read:list_recording_files:admin",
                ],
                agent=[],
            ),
            fields=[
                AuthField(
                    name="clientId",
                    display_name="Client ID",
                    placeholder="OAuth Client ID",
                    description="OAuth Client ID from Zoom Marketplace",
                    field_type="TEXT",
                    max_length=256,
                ),
                AuthField(
                    name="clientSecret",
                    display_name="Client Secret",
                    placeholder="OAuth Client Secret",
                    description="OAuth Client Secret from Zoom Marketplace",
                    field_type="PASSWORD",
                    max_length=512,
                    is_secret=True,
                ),
            ],
            icon_path=IconPaths.connector_icon(Connectors.ZOOM.value),
            app_group=AppGroups.ZOOM.value,
            app_description="OAuth application for syncing Zoom users, meetings, and transcripts",
            app_categories=["Video Conferencing", "Meetings"],
        )
    ])\
    .with_info(CONNECTOR_EMAIL_IDENTITY_INFO)\
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.ZOOM.value))
        .with_realtime_support(False)
        .add_documentation_link(DocumentationLink(
            "Zoom OAuth App",
            "https://developers.zoom.us/docs/integrations/oauth/",
            "setup",
        ))
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_agent_support(True)
        .add_filter_field(CommonFields.enable_manual_sync_filter())
        .add_filter_field(FilterField(
            name="meetings",
            display_name="Index Meeting Transcripts",
            filter_type=FilterType.BOOLEAN,
            category=FilterCategory.INDEXING,
            description="Enable indexing of Zoom AI Companion meeting transcripts",
            default_value=True,
        ))
    )\
    .build_decorator()
class ZoomConnector(BaseConnector):
    """Zoom connector — syncs past meetings, AI Companion transcripts, and
    builds permissions from host, alt-hosts, participants, and invitees via OAuth."""

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
            ZoomApp(connector_id),
            logger,
            data_entities_processor,
            data_store_provider,
            config_service,
            connector_id,
            scope,
            created_by,
        )
        self.connector_id = connector_id
        self.connector_name = Connectors.ZOOM
        self.external_client: Optional[ZoomClient] = None
        self.data_source: Optional[ZoomDataSource] = None

        org_id = self.data_entities_processor.org_id
        self.sync_point = SyncPoint(
            connector_id=self.connector_id,
            org_id=org_id,
            sync_data_point_type=SyncDataPointType.RECORDS,
            data_store_provider=data_store_provider,
        )
        self.sync_filters: Optional[FilterCollection] = None
        self.indexing_filters: Optional[FilterCollection] = None
        # Set False when this sync left part of Zoom unread; see full_sync_coverage.
        self._read_everything = True
        # The listed users' addresses by Zoom user id, this run.
        self._user_emails: dict[str, str] = {}

    # ============================================================================
    # Initialization
    # ============================================================================

    async def init(self) -> bool:
        try:
            self.external_client = await ZoomClient.build_from_services(
                logger=self.logger,
                config_service=self.config_service,
                connector_instance_id=self.connector_id,
            )
            self.data_source = ZoomDataSource(self.external_client)
            self.logger.info("✅ Zoom connector initialised (OAuth).")
            return True
        except Exception as exc:
            self.logger.error("❌ Zoom connector init failed: %s", exc, exc_info=True)
            return False

    # ============================================================================
    # Core sync
    # ============================================================================

    async def _register_authenticated_identity(self) -> None:
        """Record which source account this connector is authenticated as, so a creator whose
        PipesHub email differs still resolves that account's permissions for this connector."""
        try:
            datasource = await self._get_fresh_datasource()
            response = await datasource.user("me")
        except Exception as e:
            self.logger.debug("Could not read the authenticated Zoom user: %s", e)
            return
        if not response.success or not isinstance(response.data, dict):
            self.logger.warning(
                "Zoom connector %s: could not read the authenticated Zoom user (%s: %s). If Zoom "
                "reports a missing scope, add user:read:user:admin to the Zoom app and "
                "re-authorize the connector",
                self.connector_id, response.message, response.data or response.error,
            )
            return
        email, source_user_id = response.data.get("email"), response.data.get("id")
        await self.register_authenticated_source_user(
            email.strip().lower() if isinstance(email, str) else None,
            source_user_id.strip() if isinstance(source_user_id, str) else None,
        )

    async def run_sync(self) -> None:
        """Sync meetings — checkpoint-driven; handles both first run and incremental.

        Every user is attempted. A run that left part of Zoom unread ends as incomplete once the others are done."""
        self._read_everything = True
        try:
            if not self.data_source:
                await self.init()

            await self._register_authenticated_identity()

            await self._load_filters()

            today = self._today()

            self.logger.info(
                "🚀 Zoom: per-user report sync up to %s", self._date_str(today)
            )

            users = await self._list_users()
            self.logger.info("👥 Zoom: %d active users found.", len(users))
            self._user_emails = {u.id.strip(): u.email.strip().lower() for u in users if u.id.strip() and u.email.strip()}

            app_users = self._build_app_users(users)
            if app_users:
                await self.data_entities_processor.on_new_app_users(app_users)
                self.logger.info("👥 Zoom: synced %d app users", len(app_users))

            for zoom_user in users:
                try:
                    await self._sync_meetings_for_user(zoom_user, today)
                except Exception as exc:
                    self.logger.error(
                        "❌ Zoom: error syncing user %s: %s", zoom_user.id or "unknown", exc, exc_info=True
                    )
                    self._read_everything = False
                    continue

            if not self._read_everything:
                raise ZoomIncompleteSyncError(
                    "Zoom sync incomplete: some users or meetings could not be read; see the errors above"
                )
            self.logger.info("✅ Zoom: sync complete.")

        except Exception as exc:
            self.logger.error("❌ Zoom: sync failed: %s", exc, exc_info=True)
            raise

    def full_sync_coverage(self) -> FullSyncCoverage:
        """A full sync that left part of Zoom unread unlinks nothing it did not write."""
        return FullSyncCoverage(complete=self._read_everything)

    async def run_incremental_sync(self) -> None:
        """Incremental sync — delegates to run_sync which handles checkpoint logic."""
        self.logger.info("🔄 Zoom: starting incremental sync for connector %s", self.connector_id)
        await self.run_sync()
        self.logger.info("✅ Zoom: incremental sync completed.")

    async def _sync_meetings_for_user(
        self,
        zoom_user: ZoomUser,
        today: date,
    ) -> None:
        """Fetch meetings in 30-day chunks, build permissions, persist records."""
        user_id = zoom_user.id
        host_email = zoom_user.email.strip().lower()

        group, group_perms = self._build_record_group(zoom_user)
        await self.data_entities_processor.on_new_record_groups([(group, group_perms)])
        record_group_id = group.id

        last_sync = await self._get_user_meeting_sync_point(user_id)
        chunks = self._calculate_sync_chunks(
            user_created_at=zoom_user.created_at,
            last_sync_date=last_sync,
            today=today,
        )
        self.logger.debug(
            "Zoom user %s: last_sync=%s, chunks=%d",
            user_id,
            self._date_str(last_sync) if last_sync else "None",
            len(chunks),
        )

        if self.data_entities_processor.full_sync_running(self.connector_id):
            await self._keep_meetings_before(user_id, date.fromisoformat(chunks[0][0]) if chunks else today)

        if not chunks:
            await self._update_user_meeting_sync_point(user_id, today - timedelta(days=1))
            return

        sem = asyncio.Semaphore(_MEETING_CONCURRENCY)

        for from_str, to_str in chunks:
            raw_meetings = await self._list_report_meetings(user_id, from_str, to_str)

            results = await asyncio.gather(
                *[
                    self._process_one_meeting(sem, m, host_email, record_group_id, user_id)
                    for m in raw_meetings
                ],
                return_exceptions=True,
            )

            if any(isinstance(r, BaseException) for r in results):
                self._read_everything = False
            chunk_records: list[tuple[Record, list[Permission]]] = [
                r for r in results  # type: ignore[misc]
                if r is not None and not isinstance(r, BaseException)
            ]

            # Flush each chunk immediately — bounds memory and ensures partial
            # progress is persisted even if a later chunk fails.
            if chunk_records:
                await self.data_entities_processor.on_new_records(chunk_records)
                self.logger.debug(
                    "Zoom user %s: flushed %d records for chunk %s–%s",
                    user_id, len(chunk_records), from_str, to_str,
                )

        await self._update_user_meeting_sync_point(user_id, today - timedelta(days=1))

    async def _keep_meetings_before(self, user_id: str, read_from: date) -> None:
        """Note kept the user's stored meetings that started before what this full sync lists.

        Zoom's report API lists one month per call and only within the last six months
        (ZOOM_REPORT_MAX_HISTORY_DAYS), so an older meeting is unread, not gone. Its grants
        cannot change once it is over, so they are kept as stored rather than dropped as a
        reported window would drop them. The report's from/to dates may be in the account's
        timezone, so the first day read is kept too: a meeting on it may not have been listed.
        """
        cut = datetime.combine(read_from + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
        cut_ms = int(cut.timestamp() * 1000)
        processor = self.data_entities_processor
        kept: list[str] = []
        after: Optional[str] = None
        while True:
            page = await processor.get_records_in_record_group(
                self.connector_id, user_id, _STORED_MEETINGS_PAGE, after
            )
            kept.extend(
                r.id for r in page
                if isinstance(r.source_created_at, int) and r.source_created_at < cut_ms
            )
            if len(page) < _STORED_MEETINGS_PAGE:
                break
            after = page[-1].id
        processor.note_kept_records(self.connector_id, kept)

    # ============================================================================
    # Utilities
    # ============================================================================

    @staticmethod
    def _date_str(d: date) -> str:
        return d.strftime("%Y-%m-%d")

    @staticmethod
    def _parse_date(s: str) -> date:
        return date.fromisoformat(s)

    @staticmethod
    def _today() -> date:
        return datetime.now(tz=timezone.utc).date()

    @staticmethod
    def _calculate_sync_chunks(
        user_created_at: Optional[str],
        last_sync_date: Optional[date],
        today: date,
    ) -> list[tuple[str, str]]:
        """Split the sync window into <=30-day chunks.

        Initial sync:  start = max(user.created_at, today - 180 days)
        Incremental:   start = last_sync_date - 1 day  (safety overlap), never before today - 180 days: Zoom
                       refuses a report that reaches past six months, so an older checkpoint would fail every run.
        """
        six_months_ago = today - timedelta(days=ZOOM_REPORT_MAX_HISTORY_DAYS)

        if last_sync_date:
            start = max(last_sync_date - timedelta(days=1), six_months_ago)
        else:
            if user_created_at:
                try:
                    user_date = datetime.fromisoformat(user_created_at).date()
                except ValueError:
                    user_date = six_months_ago
            else:
                user_date = six_months_ago
            start = max(user_date, six_months_ago)

        if start >= today:
            return []

        chunks: list[tuple[str, str]] = []
        current = start
        while current < today:
            chunk_end = min(current + timedelta(days=ZOOM_REPORT_MAX_RANGE_DAYS), today)
            chunks.append((current.isoformat(), chunk_end.isoformat()))
            current = chunk_end
        return chunks

    @staticmethod
    def _encode_uuid(meeting_uuid: str) -> str:
        """URL-encode meeting UUID for Zoom API paths.

        Zoom requires **double** percent-encoding when the UUID starts with ``/``
        or contains ``//``.  Otherwise a **single** encode suffices.
        """
        if meeting_uuid.startswith("/") or "//" in meeting_uuid:
            return urllib.parse.quote(
                urllib.parse.quote(meeting_uuid, safe=""), safe=""
            )
        return urllib.parse.quote(meeting_uuid, safe="")

    async def _get_fresh_datasource(self) -> ZoomDataSource:
        """Return ZoomDataSource with an always-fresh OAuth access token.

        The background token-refresh service writes new tokens into the config
        store.  This method re-reads ``credentials.access_token`` from the config
        store on every call and patches the client's Authorization header if the
        token has changed — identical to the Jira connector pattern.
        """
        if not self.external_client:
            raise connector_not_ready(self.display_name)
        if self.data_source is None:
            raise connector_not_ready(self.display_name)

        config = await self.config_service.get_config(
            f"/services/connectors/{self.connector_id}/config"
        )
        if not config:
            raise connector_not_ready(self.display_name)

        fresh_token: str = (config.get("credentials") or {}).get("access_token") or ""
        if not fresh_token:
            raise connector_not_ready(self.display_name)

        internal = self.external_client.get_client()
        if internal.get_token() != fresh_token:
            self.logger.debug("🔄 Zoom: updating client with refreshed access token")
            internal.set_token(fresh_token)

        return self.data_source

    async def _load_filters(self) -> None:
        self.sync_filters, self.indexing_filters = await load_connector_filters(
            self.config_service, "zoom", self.connector_id, self.logger
        )

    # ============================================================================
    # Checkpoint (per Zoom user id)
    # ============================================================================

    def _user_report_sync_key(self, zoom_user_id: str) -> str:
        return f"{ZOOM_REPORT_SYNC_KEY_PREFIX}/{zoom_user_id}"

    async def _get_user_meeting_sync_point(self, zoom_user_id: str) -> Optional[date]:
        data = await self.sync_point.read_sync_point(
            self._user_report_sync_key(zoom_user_id)
        )
        raw = data.get("last_sync_date")
        if raw:
            try:
                return self._parse_date(raw)
            except ValueError:
                pass
        return None

    async def _update_user_meeting_sync_point(self, zoom_user_id: str, d: date) -> None:
        await self.sync_point.update_sync_point(
            self._user_report_sync_key(zoom_user_id),
            {"last_sync_date": self._date_str(d)},
        )

    # ============================================================================
    # API wrappers
    # ============================================================================

    async def _list_users(self) -> list[ZoomUser]:
        """GET /v2/users?status=active — paginated. A page that cannot be read marks this sync incomplete."""
        datasource = await self._get_fresh_datasource()
        all_users: list[ZoomUser] = []
        next_page_token: Optional[str] = None
        while True:
            resp = await datasource.users(
                status="active",
                page_size=ZOOM_PAGE_SIZE,
                next_page_token=next_page_token,
            )
            page = _page_items(resp.data, "users") if resp.success else None
            if page is None:
                self.logger.warning("Zoom: users() failed — %s", resp.message)
                self._read_everything = False
                break
            try:
                next_page_token = _next_page(resp.data, next_page_token, "user list")  # type: ignore[arg-type]
            except ZoomReadError as exc:
                self.logger.warning("%s", exc)
                self._read_everything = False
                break
            all_users.extend(ZoomUser.model_validate(u) for u in page)
            if not next_page_token:
                break
        if not all_users:
            # The account that authorized the app is always active, so a list with no one in it is not Zoom's answer.
            self._read_everything = False
        return all_users

    async def _list_report_meetings(
        self, user_id: str, from_str: Optional[str], to_str: str
    ) -> list[ZoomMeetingReport]:
        """GET /v2/report/users/{userId}/meetings — paginated."""
        datasource = await self._get_fresh_datasource()
        meetings: list[ZoomMeetingReport] = []
        next_page_token: Optional[str] = None
        while True:
            res = await datasource.report_meetings(
                userId=user_id,
                from_=from_str,
                to=to_str,
                page_size=ZOOM_PAGE_SIZE,
                next_page_token=next_page_token,
            )
            if not res.success:
                zoom_code = res.data.get("code") if isinstance(res.data, dict) else None
                zoom_message = res.data.get("message") if isinstance(res.data, dict) else None
                self.logger.warning(
                    "Zoom: report_meetings(%s, %s–%s) failed "
                    "(http=%s, zoom_code=%s, zoom_message=%s)",
                    user_id, from_str, to_str, res.message, zoom_code, zoom_message,
                )
                # A partial list would let the user's checkpoint move past the lost meetings.
                raise RuntimeError(
                    f"Zoom report_meetings({user_id}, {from_str}–{to_str}) failed: "
                    f"http={res.message}, zoom_code={zoom_code}"
                )
            page = _page_items(res.data, "meetings")
            if page is None:
                # A 2xx without a meeting list is not an empty month; ending here would drop the rest.
                raise RuntimeError(
                    f"Zoom report_meetings({user_id}, {from_str}–{to_str}) returned no body or no meeting list: "
                    f"http={res.message}"
                )
            meetings.extend(ZoomMeetingReport.model_validate(m) for m in page)
            next_page_token = _next_page(res.data, next_page_token, "meeting report")  # type: ignore[arg-type]
            if not next_page_token:
                break
        return meetings

    async def _list_meeting_participants(
        self,
        encoded_uuid: str,
        raise_on_error: bool = False,
        read_so_far: Optional[list[ZoomParticipant]] = None,
    ) -> list[ZoomParticipant]:
        """GET /v2/report/meetings/{uuid}/participants — paginated.

        Sync builds permissions best-effort and must not abort on one bad
        meeting, so failures stop pagination silently unless *raise_on_error*.
        Pages are collected into *read_so_far* when given, so a caller still
        has the pages read before a raise.
        """
        datasource = await self._get_fresh_datasource()
        participants: list[ZoomParticipant] = [] if read_so_far is None else read_so_far
        next_page_token: Optional[str] = None
        while True:
            res = await datasource.report_meeting_participants(
                meetingId=encoded_uuid,
                page_size=ZOOM_PAGE_SIZE,
                next_page_token=next_page_token,
            )
            page = _page_items(res.data, "participants") if res.success else None
            if page is None:
                if raise_on_error:
                    # A 2xx without a participant list is a failed read too, not the end of the list.
                    message = (
                        res.message or res.error
                        if not res.success
                        else "Zoom returned a participants page with no participant list"
                    )
                    self.logger.warning(
                        "Zoom: participants call failed for %s — %s", encoded_uuid, message,
                    )
                    raise_for_stream_fetch(
                        success=False,
                        has_payload=False,
                        connector=self.display_name,
                        status=res.status_code,
                        message=message,
                    )
                break
            participants.extend(ZoomParticipant.model_validate(p) for p in page)
            next_page_token = _next_page(res.data, next_page_token, f"participants of {encoded_uuid}")  # type: ignore[arg-type]
            if not next_page_token:
                break
        return participants

    async def _get_meeting_detail(self, meeting_id: str) -> Optional[ZoomMeetingDetail]:
        """GET /v2/meetings/{meetingId} — returns full meeting object or None."""
        try:
            datasource = await self._get_fresh_datasource()
            resp = await datasource.meeting(meeting_id)
            if resp.success and isinstance(resp.data, dict):
                return ZoomMeetingDetail.model_validate(resp.data)
            self.logger.debug(
                "Zoom: meeting detail for %s unavailable — %s", meeting_id, resp.message
            )
            return None
        except Exception as exc:
            self.logger.debug("Zoom: could not fetch meeting detail for %s: %s", meeting_id, exc)
            return None

    async def _fetch_transcript(self, meeting_uuid: str) -> Optional[str]:
        """Fetch AI Companion transcript for a past meeting.

        Returns None only when Zoom itself says there is nothing to fetch (the
        3322 / meeting-not-found body codes). Every other failure raises, so an
        expired token is not served to the caller as an empty transcript.
        """
        datasource = await self._get_fresh_datasource()
        raw_uuid = str(meeting_uuid or "").strip()
        if not raw_uuid:
            return None
        encoded = self._encode_uuid(raw_uuid)

        try:
            meta_resp = await datasource.meeting_transcript_metadata(encoded)

            if not meta_resp.success:
                zoom_code = (
                    meta_resp.data.get("code")  # type: ignore[union-attr]
                    if isinstance(meta_resp.data, dict)
                    else None
                )
                if zoom_code == _ZOOM_CODE_NO_AI_TRANSCRIPT:
                    self.logger.debug(
                        "Zoom: no AI transcript for %s (code 3322)", meeting_uuid
                    )
                    return None
                if zoom_code in _ZOOM_CODES_MEETING_NOT_FOUND:
                    self.logger.debug(
                        "Zoom: meeting not found/expired for %s (code %s)", meeting_uuid, zoom_code
                    )
                    return None
                self.logger.warning(
                    "Zoom: transcript metadata call failed for %s "
                    "(zoom_code=%s, message=%s)",
                    meeting_uuid, zoom_code, meta_resp.message,
                )
                raise_for_stream_fetch(
                    success=False,
                    has_payload=False,
                    connector=self.display_name,
                    status=meta_resp.status_code,
                    message=meta_resp.message or meta_resp.error,
                )

            if not isinstance(meta_resp.data, dict):
                return None

            download_url = meta_resp.data.get("download_url")
            if not download_url:
                self.logger.warning(
                    "Zoom: transcript metadata has no download_url for %s", meeting_uuid
                )
                return None

            dl_resp = await datasource.meeting_transcript_download(str(download_url))
            if not dl_resp.success:
                self.logger.warning(
                    "Zoom: transcript download failed for %s — %s", meeting_uuid, dl_resp.message
                )
                raise_for_stream_fetch(
                    success=False,
                    has_payload=False,
                    connector=self.display_name,
                    status=dl_resp.status_code,
                    message=dl_resp.message or dl_resp.error,
                )

            text = (
                dl_resp.data.get("transcript_text")  # type: ignore[union-attr]
                if isinstance(dl_resp.data, dict)
                else None
            )
            return str(text) if text else None

        except HTTPException:
            raise
        except Exception as exc:
            self.logger.error(
                "❌ Zoom: transcript fetch failed for %s: %s", meeting_uuid, exc,
                exc_info=True,
            )
            raise to_stream_error(exc, connector=self.display_name) from exc

    # ============================================================================
    # Meeting concurrency helper
    # ============================================================================

    async def _process_one_meeting(
        self,
        sem: asyncio.Semaphore,
        meeting_obj: ZoomMeetingReport,
        host_email: str,
        record_group_id: Optional[str],
        host_user_id: str = "",
    ) -> Optional[tuple[Record, list[Permission]]]:
        """Fetch detail + permissions for a single meeting under the shared semaphore.

        Returns (Record, permissions) on success, or None if the meeting should be
        skipped (missing UUID) or if an error occurs (already logged).
        """
        meeting_uuid = meeting_obj.uuid.strip()
        if not meeting_uuid:
            return None

        async with sem:
            try:
                encoded_uuid = self._encode_uuid(meeting_uuid)
                meeting_id = str(meeting_obj.id) if meeting_obj.id is not None else ""
                meeting_detail = (
                    await self._get_meeting_detail(meeting_id) if meeting_id else None
                )

                # Fetch cloud recording share URL for this meeting.
                # Falls through silently on any error (scope not granted, no recording, etc.)
                # By the instance's UUID: a meeting number names its latest instance.
                recording_url = ""
                if meeting_id:
                    try:
                        ds = await self._get_fresh_datasource()
                        rec_resp = await ds.recording_get(encoded_uuid)
                        if rec_resp.success and rec_resp.data:
                            recording = ZoomRecordingDetail.model_validate(rec_resp.data)
                            recording_url = recording.share_url
                    except Exception as rec_exc:
                        self.logger.warning(
                            "Zoom: recording fetch failed for %s: %s", meeting_id, rec_exc
                        )

                rec = self._build_meeting_record(
                    meeting_obj=meeting_obj,
                    meeting_uuid=meeting_uuid,
                    meeting_detail=meeting_detail,
                    host_email=host_email,
                    record_group_id=record_group_id,
                    recording_url=recording_url,
                    host_user_id=host_user_id,
                )
                perms, participants_read = await self._build_meeting_permissions(
                    meeting_detail=meeting_detail,
                    encoded_uuid=encoded_uuid,
                    host_email=host_email,
                )
                if not participants_read or meeting_detail is None:
                    # Only some of the meeting's grants were read, so add them and
                    # keep the stored ones rather than replace them with fewer.
                    rec.adds_permissions = True
                return (rec, perms)
            except Exception as exc:
                self.logger.error(
                    "❌ Zoom: failed to process meeting %s: %s", meeting_uuid, exc,
                    exc_info=True,
                )
                self._read_everything = False
                return None

    # ============================================================================
    # Record / permission builders
    # ============================================================================

    @staticmethod
    def _zoom_iso_to_ms(raw: Optional[str]) -> Optional[int]:
        """Parse Zoom datetime fields (e.g. ``2026-03-26T09:53:35Z``) to epoch ms."""
        if not raw or not isinstance(raw, str) or not raw.strip():
            return None
        try:
            return parse_timestamp(raw.strip())
        except Exception:
            return None

    def _build_app_users(self, zoom_users: list[ZoomUser]) -> list[AppUser]:
        """Map Zoom directory users to AppUser."""
        org_id = self.data_entities_processor.org_id
        now_ms = get_epoch_timestamp_in_ms()
        out: list[AppUser] = []
        for u in zoom_users:
            user_id = u.id.strip()
            if not user_id:
                continue
            email = u.email.strip().lower()
            if not email:
                self.logger.debug(
                    "Zoom: skipping app user without email (user id=%s)", user_id
                )
                continue
            display = u.display_name.strip()
            full_name = display or f"{u.first_name} {u.last_name}".strip() or email

            created_ms = (
                self._zoom_iso_to_ms(u.created_at)
                or self._zoom_iso_to_ms(u.user_created_at)
                or now_ms
            )
            updated_ms = self._zoom_iso_to_ms(u.last_login_time) or created_ms

            out.append(
                AppUser(
                    app_name=Connectors.ZOOM,
                    connector_id=self.connector_id,
                    source_user_id=user_id,
                    org_id=org_id,
                    email=email,
                    full_name=full_name,
                    is_active=True,
                    created_at=created_ms,
                    updated_at=updated_ms,
                    source_created_at=created_ms,
                    source_updated_at=updated_ms,
                )
            )
        return out

    def _build_record_group(
        self, zoom_user: ZoomUser
    ) -> tuple[RecordGroup, list[Permission]]:
        user_id = zoom_user.id
        email = zoom_user.email.strip().lower()
        display_name = f"{zoom_user.first_name} {zoom_user.last_name}".strip() or email

        now_ms = get_epoch_timestamp_in_ms()
        group = RecordGroup(
            id=str(uuid4()),
            org_id=self.data_entities_processor.org_id,
            name=display_name,
            external_group_id=user_id,
            connector_name=Connectors.ZOOM,
            connector_id=self.connector_id,
            group_type=RecordGroupType.USER_GROUP,
            inherit_permissions=False,
            created_at=now_ms,
            updated_at=now_ms,
        )
        perms: list[Permission] = []
        if email:
            perms.append(Permission(
                email=email,
                type=PermissionType.OWNER,
                entity_type=EntityType.USER,
            ))
        return group, perms

    def _build_meeting_record(
        self,
        meeting_obj: ZoomMeetingReport,
        meeting_uuid: str,
        meeting_detail: Optional[ZoomMeetingDetail],
        host_email: str,
        record_group_id: Optional[str],
        recording_url: str = "",
        host_user_id: str = "",
    ) -> MeetingRecord:
        topic = meeting_obj.topic.strip()
        # Zoom's report items name no host: a user's report lists the meetings that user hosts.
        host_id = meeting_obj.host_id or host_user_id
        start_time = meeting_obj.start_time
        end_time = meeting_obj.end_time
        duration_minutes = meeting_obj.duration
        meeting_type = meeting_obj.type

        source_ts: Optional[int] = None
        if start_time:
            try:
                dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                source_ts = int(dt.timestamp() * 1000)
            except ValueError:
                pass

        revision_id = f"{start_time}|{end_time}|{topic}"

        display_name = f"{topic} ({start_time})" if start_time else topic

        # Transcript listing page with #:~:text= fragment so the browser scrolls to and
        # highlights the specific meeting row when the user clicks through from search results.
        weburl = build_text_fragment_url(
            ZOOM_TRANSCRIPT_LISTING_URL, topic, SourceFormat.PLAIN
        )

        now_ms = get_epoch_timestamp_in_ms()
        record = MeetingRecord(
            id=str(uuid4()),
            org_id=self.data_entities_processor.org_id,
            record_name=display_name,
            record_type=RecordType.MEETING,
            external_record_id=meeting_uuid,
            external_revision_id=revision_id,
            external_record_group_id=host_id or None,
            record_group_id=record_group_id,
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.ZOOM,
            connector_id=self.connector_id,
            mime_type=MimeTypes.BLOCKS.value,
            weburl=weburl,
            inherit_permissions=False,
            source_created_at=source_ts,
            source_updated_at=source_ts,
            created_at=now_ms,
            updated_at=now_ms,
            host_email=host_email,
            host_id=host_id,
            meeting_type=meeting_type,
            duration_minutes=duration_minutes,
            start_time=start_time,
            end_time=end_time,
            recording_url=recording_url or None,
            preview_renderable=False,
            is_dependent_node=False,
            parent_node_id=None,
        )

        if self.indexing_filters and not self.indexing_filters.is_enabled("meetings"):
            record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

        return record

    # ============================================================================
    # Permissions
    # ============================================================================

    async def _build_meeting_permissions(
        self,
        meeting_detail: Optional[ZoomMeetingDetail],
        encoded_uuid: str,
        host_email: str,
    ) -> tuple[list[Permission], bool]:
        """Build permission list from host, alt-hosts, participants, and invitees.

        Flow:
        1. Host email → OWNER
        2. Alt-hosts (settings.alternative_hosts, semicolon-separated) → READ each
        3. Participants (report API) → READ each
        4. Invitees (settings.meeting_invitees[].email) → READ each
        5. All deduplicated via seen_emails set

        Returns (permissions, whether the participants were read in full).
        """
        perms: list[Permission] = []
        seen_emails: set[str] = set()

        # 1 — host → OWNER
        if host_email:
            perms.append(Permission(
                email=host_email,
                type=PermissionType.OWNER,
                entity_type=EntityType.USER,
            ))
            seen_emails.add(host_email)

        settings = meeting_detail.settings if meeting_detail else ZoomMeetingSettings()

        # 2 — alt-hosts → READ
        for raw_alt in settings.alternative_hosts.split(";"):
            # Zoom lists an alternative host by address or by user id.
            alt = raw_alt.strip()
            alt_email = alt.lower() if "@" in alt else self._user_emails.get(alt, "")
            if alt_email and alt_email not in seen_emails:
                perms.append(Permission(
                    email=alt_email,
                    type=PermissionType.READ,
                    entity_type=EntityType.USER,
                ))
                seen_emails.add(alt_email)

        # 3 — participants → READ
        # A read that fails part way still grants the participants it returned;
        # the caller then adds these grants instead of replacing the stored ones.
        participants_read = True
        read_so_far: list[ZoomParticipant] = []
        try:
            participants = await self._list_meeting_participants(
                encoded_uuid, raise_on_error=True, read_so_far=read_so_far
            )
        except Exception as exc:
            participants = read_so_far
            participants_read = False
            self.logger.warning("Zoom: failed to fetch participants: %s", exc)
        self._add_participant_permissions(perms, participants, seen_emails)

        # 4 — invitees → READ
        for inv in settings.meeting_invitees:
            inv_email = inv.email.strip().lower()
            if inv_email and inv_email not in seen_emails:
                perms.append(Permission(
                    email=inv_email,
                    type=PermissionType.READ,
                    entity_type=EntityType.USER,
                ))
                seen_emails.add(inv_email)

        return perms, participants_read

    @staticmethod
    def _add_participant_permissions(
        perms: list[Permission],
        participants: list[ZoomParticipant],
        seen_emails: set[str],
    ) -> None:
        """Append READ permission per participant email."""
        for p in participants:
            p_email = p.user_email.strip().lower()
            if p_email and p_email not in seen_emails:
                perms.append(Permission(
                    email=p_email,
                    type=PermissionType.READ,
                    entity_type=EntityType.USER,
                ))
                seen_emails.add(p_email)

    # ============================================================================
    # stream_record
    # ============================================================================

    async def stream_record(
        self,
        record: Record,
        user_id: Optional[str] = None,
        convertTo: Optional[str] = None,
    ) -> StreamingResponse:

        if not self.data_source:
            raise connector_not_ready(self.display_name)

        transcript = ""
        participants_md = ""

        if record.external_record_id:
            meeting_uuid = record.external_record_id
            transcript = await self._fetch_transcript(meeting_uuid) or ""

            encoded = self._encode_uuid(meeting_uuid)
            participants = await self._list_meeting_participants(
                encoded, raise_on_error=True
            )
            participants_md = self._build_participants_markdown(participants)

        block_groups = [
            BlockGroup(
                id=str(uuid4()),
                index=0,
                name="Transcript",
                type=GroupType.TEXT_SECTION,
                sub_type=GroupSubType.CONTENT,
                description="AI Companion transcript for the meeting",
                source_group_id=record.external_record_id,
                format=DataFormat.MARKDOWN,
                weburl=record.weburl or "",
                requires_processing=True,
                data=transcript,
            ),
            BlockGroup(
                id=str(uuid4()),
                index=1,
                name="Participants",
                type=GroupType.TEXT_SECTION,
                sub_type=GroupSubType.CONTENT,
                description="Meeting participants with attendance duration and timing",
                source_group_id=record.external_record_id,
                format=DataFormat.MARKDOWN,
                weburl=record.weburl or "",
                requires_processing=True,
                data=participants_md,
            ),
        ]

        container = BlocksContainer(block_groups=block_groups, blocks=[])
        payload = container.model_dump_json().encode("utf-8")
        return StreamingResponse(iter([payload]), media_type=MimeTypes.BLOCKS.value)

    @staticmethod
    def _table_cell(text: str) -> str:
        """Text for one markdown table cell: a line break or a pipe would end it."""
        return " ".join(text.split()).replace("|", "\\|")

    @staticmethod
    def _build_participants_markdown(participants: list[ZoomParticipant]) -> str:
        """Render a participant list as a markdown table.

        Columns: Name | Email | Duration (min) | Joined | Left
        """
        if not participants:
            return ""

        rows = [
            "| Name | Email | Duration (min) | Joined | Left |",
            "| --- | --- | --- | --- | --- |",
        ]
        for p in participants:
            name = ZoomConnector._table_cell(p.name) or "—"
            email = ZoomConnector._table_cell(p.user_email) or "—"
            duration_min = round(p.duration / 60, 1)
            join_time = (p.join_time or "—").replace("T", " ").replace("Z", " UTC")
            leave_time = (p.leave_time or "—").replace("T", " ").replace("Z", " UTC")
            rows.append(
                f"| {name} | {email} | {duration_min} | {join_time} | {leave_time} |"
            )
        return "\n".join(rows)

    # ============================================================================
    # Abstract method implementations
    # ============================================================================

    async def test_connection_and_access(self) -> bool:
        """Validate credentials by fetching at least one active user."""
        try:
            if not self.data_source:
                await self.init()
            datasource = await self._get_fresh_datasource()
            resp = await datasource.users(
                status="active", page_size=1
            )
            if resp.success:
                self.logger.info("✅ Zoom connection test successful")
                return True
            self.logger.error("❌ Zoom connection test failed: %s", resp.message)
            return False
        except Exception as exc:
            self.logger.error("❌ Zoom connection test failed: %s", exc, exc_info=True)
            return False

    def get_signed_url(self, record: Record) -> Optional[str]:
        return None

    def handle_webhook_notification(self, notification: dict[str, Any]) -> None:
        pass

    async def cleanup(self) -> None:
        try:
            self.logger.info("Cleaning up Zoom connector resources")
            if self.external_client:
                try:
                    internal = self.external_client.get_client()
                    if internal and hasattr(internal, "close"):
                        await internal.close()
                        self.logger.debug("Closed Zoom HTTP client connection")
                except Exception as exc:
                    self.logger.debug(
                        "Error closing Zoom client (may be expected during shutdown): %s", exc
                    )
                finally:
                    self.external_client = None
            self.data_source = None
            self.logger.info("Zoom connector cleanup completed")
        except Exception as exc:
            self.logger.warning("Error during Zoom cleanup: %s", exc)

    async def reindex_records(self, record_results: list[Record]) -> None:
        """Reindex Zoom meeting records.

        Transcript text is fetched live in ``stream_record``, not refreshed here.
        Publishes ``reindexRecord`` events only.
        """
        try:
            if not record_results:
                return

            self.logger.info("Starting reindex for %d Zoom records", len(record_results))

            reindexable: list[Record] = []
            skipped_count = 0

            for record in record_results:
                if isinstance(record, MeetingRecord):
                    reindexable.append(record)
                else:
                    self.logger.warning(
                        "⚠️ Record %s (%s) is not a MeetingRecord (%s), skipping reindex",
                        record.id, record.record_type, type(record).__name__,
                    )
                    skipped_count += 1

            if reindexable:
                try:
                    await self.data_entities_processor.reindex_existing_records(
                        reindexable
                    )
                    self.logger.info(
                        "Published reindex events for %d Zoom meeting records", len(reindexable)
                    )
                except NotImplementedError as e:
                    self.logger.warning(
                        "Cannot reindex records - to_kafka_record not implemented: %s", e
                    )

            if skipped_count:
                self.logger.warning(
                    "⚠️ Skipped reindex for %d records not usable as MeetingRecord", skipped_count
                )

        except Exception as e:
            self.logger.error("❌ Error during Zoom reindex: %s", e, exc_info=True)
            raise

    async def get_filter_options(
        self,
        filter_key: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        cursor: Optional[str] = None,
    ) -> FilterOptionsResponse:
        return FilterOptionsResponse(options=[], has_more=False)

    # ============================================================================
    # Factory
    # ============================================================================

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
    ) -> "ZoomConnector":
        """Factory method — creates the connector instance without calling init().

        init() is called lazily inside run_sync() and test_connection_and_access().
        """
        return cls(
            logger=logger,
            data_entities_processor=data_entities_processor,
            data_store_provider=data_store_provider,
            config_service=config_service,
            connector_id=connector_id,
            scope=scope,
            created_by=created_by,
        )
