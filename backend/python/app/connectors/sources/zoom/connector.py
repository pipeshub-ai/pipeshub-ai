"""Zoom connector — all past user meetings with incremental sync via report APIs."""

import asyncio
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from logging import Logger
from typing import Any, Optional
from uuid import uuid4

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
    FilterCategory,
    FilterCollection,
    FilterField,
    FilterOptionsResponse,
    FilterType,
    load_connector_filters,
)
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
from app.utils.time_conversion import get_epoch_timestamp_in_ms, parse_timestamp

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ZOOM_PAGE_SIZE = 300
ZOOM_REPORT_SYNC_KEY_PREFIX = "zoom_report_last_sync"
ZOOM_REPORT_MAX_RANGE_DAYS = 30
ZOOM_REPORT_MAX_HISTORY_DAYS = 180

_ZOOM_CODE_NO_AI_TRANSCRIPT = 3322
_ZOOM_CODES_MEETING_NOT_FOUND = {3001, 3301}

# Maximum number of meetings processed concurrently within a single chunk.
# Kept conservative to stay under Zoom's Heavy-rate endpoint limits
# (GET /v2/report/meetings/{id}/participants is the bottleneck at ~10-20 req/s).
_MEETING_CONCURRENCY = 8

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

    async def run_sync(self) -> None:
        """Sync meetings — checkpoint-driven; handles both first run and incremental."""
        try:
            if not self.data_source:
                await self.init()

            await self._load_filters()

            today = self._today()

            self.logger.info(
                "🚀 Zoom: per-user report sync up to %s", self._date_str(today)
            )

            users = await self._list_users()
            self.logger.info("👥 Zoom: %d active users found.", len(users))

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
                    continue

            self.logger.info("✅ Zoom: sync complete.")

        except Exception as exc:
            self.logger.error("❌ Zoom: sync failed: %s", exc, exc_info=True)
            raise

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

        if not chunks:
            await self._update_user_meeting_sync_point(user_id, today - timedelta(days=1))
            return

        sem = asyncio.Semaphore(_MEETING_CONCURRENCY)

        for from_str, to_str in chunks:
            raw_meetings = await self._list_report_meetings(user_id, from_str, to_str)

            results = await asyncio.gather(
                *[
                    self._process_one_meeting(sem, m, host_email, record_group_id)
                    for m in raw_meetings
                ],
                return_exceptions=True,
            )

            chunk_records: list[tuple[Record, list[Permission]]] = [
                r for r in results  # type: ignore[misc]
                if r is not None and not isinstance(r, Exception)
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
        Incremental:   start = last_sync_date - 1 day  (safety overlap)
        """
        six_months_ago = today - timedelta(days=ZOOM_REPORT_MAX_HISTORY_DAYS)

        if last_sync_date:
            start = last_sync_date - timedelta(days=1)
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
            raise RuntimeError("Zoom client is not initialised. Call init() first.")
        if self.data_source is None:
            raise RuntimeError("Zoom data source is not initialised. Call init() first.")

        config = await self.config_service.get_config(
            f"/services/connectors/{self.connector_id}/config"
        )
        if not config:
            raise RuntimeError("Zoom configuration not found in config store.")

        fresh_token: str = (config.get("credentials") or {}).get("access_token") or ""
        if not fresh_token:
            raise RuntimeError("No OAuth access token available for Zoom connector.")

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
        """GET /v2/users?status=active — paginated."""
        datasource = await self._get_fresh_datasource()
        all_users: list[ZoomUser] = []
        next_page_token: Optional[str] = None
        while True:
            resp = await datasource.users(
                status="active",
                page_size=ZOOM_PAGE_SIZE,
                next_page_token=next_page_token,
            )
            if not resp.success or not resp.data:
                self.logger.warning("Zoom: users() failed — %s", resp.message)
                break
            all_users.extend(
                ZoomUser.model_validate(u)
                for u in (resp.data.get("users") or [])  # type: ignore[union-attr]
            )
            next_page_token = resp.data.get("next_page_token")  # type: ignore[union-attr]
            if not next_page_token:
                break
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
            if not res.success or not res.data:
                zoom_code = res.data.get("code") if isinstance(res.data, dict) else None
                zoom_message = res.data.get("message") if isinstance(res.data, dict) else None
                self.logger.warning(
                    "Zoom: report_meetings(%s, %s–%s) failed "
                    "(http=%s, zoom_code=%s, zoom_message=%s)",
                    user_id, from_str, to_str, res.message, zoom_code, zoom_message,
                )
                break
            meetings.extend(
                ZoomMeetingReport.model_validate(m)
                for m in (res.data.get("meetings") or [])  # type: ignore[union-attr]
            )
            next_page_token = res.data.get("next_page_token")  # type: ignore[union-attr]
            if not next_page_token:
                break
        return meetings

    async def _list_meeting_participants(
        self, encoded_uuid: str
    ) -> list[ZoomParticipant]:
        """GET /v2/report/meetings/{uuid}/participants — paginated."""
        datasource = await self._get_fresh_datasource()
        participants: list[ZoomParticipant] = []
        next_page_token: Optional[str] = None
        while True:
            res = await datasource.report_meeting_participants(
                meetingId=encoded_uuid,
                page_size=ZOOM_PAGE_SIZE,
                next_page_token=next_page_token,
            )
            if not res.success or not res.data:
                break
            participants.extend(
                ZoomParticipant.model_validate(p)
                for p in (res.data.get("participants") or [])  # type: ignore[union-attr]
            )
            next_page_token = res.data.get("next_page_token")  # type: ignore[union-attr]
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

        Returns transcript text, or None on 3322 / not found / failure.
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
                elif zoom_code in _ZOOM_CODES_MEETING_NOT_FOUND:
                    self.logger.debug(
                        "Zoom: meeting not found/expired for %s (code %s)", meeting_uuid, zoom_code
                    )
                else:
                    self.logger.warning(
                        "Zoom: transcript metadata call failed for %s "
                        "(zoom_code=%s, message=%s)",
                        meeting_uuid, zoom_code, meta_resp.message,
                    )
                return None

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
                return None

            text = (
                dl_resp.data.get("transcript_text")  # type: ignore[union-attr]
                if isinstance(dl_resp.data, dict)
                else None
            )
            return str(text) if text else None

        except Exception as exc:
            self.logger.error(
                "❌ Zoom: transcript fetch failed for %s: %s", meeting_uuid, exc,
                exc_info=True,
            )
            return None

    # ============================================================================
    # Meeting concurrency helper
    # ============================================================================

    async def _process_one_meeting(
        self,
        sem: asyncio.Semaphore,
        meeting_obj: ZoomMeetingReport,
        host_email: str,
        record_group_id: Optional[str],
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
                meeting_id = str(meeting_obj.id) if meeting_obj.id is not None else ""
                meeting_detail = (
                    await self._get_meeting_detail(meeting_id) if meeting_id else None
                )

                # Fetch cloud recording share URL for this meeting.
                # Falls through silently on any error (scope not granted, no recording, etc.)
                recording_url = ""
                if meeting_id:
                    try:
                        ds = await self._get_fresh_datasource()
                        rec_resp = await ds.recording_get(meeting_id)
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
                )
                encoded_uuid = self._encode_uuid(meeting_uuid)
                perms = await self._build_meeting_permissions(
                    meeting_detail=meeting_detail,
                    encoded_uuid=encoded_uuid,
                    host_email=host_email,
                )
                return (rec, perms)
            except Exception as exc:
                self.logger.error(
                    "❌ Zoom: failed to process meeting %s: %s", meeting_uuid, exc,
                    exc_info=True,
                )
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
    ) -> MeetingRecord:
        topic = meeting_obj.topic.strip()
        host_id = meeting_obj.host_id
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
        weburl = (
            "https://zoom.us/recording/meeting/transcript"
            + "#:~:text=" + urllib.parse.quote(topic)
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
    ) -> list[Permission]:
        """Build permission list from host, alt-hosts, participants, and invitees.

        Flow:
        1. Host email → OWNER
        2. Alt-hosts (settings.alternative_hosts, semicolon-separated) → READ each
        3. Participants (report API) → READ each
        4. Invitees (settings.meeting_invitees[].email) → READ each
        5. All deduplicated via seen_emails set
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
            alt_email = raw_alt.strip().lower()
            if alt_email and alt_email not in seen_emails:
                perms.append(Permission(
                    email=alt_email,
                    type=PermissionType.READ,
                    entity_type=EntityType.USER,
                ))
                seen_emails.add(alt_email)

        # 3 — participants → READ
        try:
            participants = await self._list_meeting_participants(encoded_uuid)
            self._add_participant_permissions(perms, participants, seen_emails)
        except Exception as exc:
            self.logger.warning("Zoom: failed to fetch participants: %s", exc)

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

        return perms

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

        transcript = ""
        participants_md = ""

        if record.external_record_id and self.data_source:
            meeting_uuid = record.external_record_id
            transcript = await self._fetch_transcript(meeting_uuid) or ""

            try:
                encoded = self._encode_uuid(meeting_uuid)
                participants = await self._list_meeting_participants(encoded)
                participants_md = self._build_participants_markdown(participants)
            except Exception as exc:
                self.logger.warning(
                    "Zoom: could not fetch participants for %s: %s", meeting_uuid, exc
                )

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
            name = p.name.strip() or "—"
            email = p.user_email.strip() or "—"
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
    ) -> "ZoomConnector":
        """Factory method — creates the connector instance without calling init().

        init() is called lazily inside run_sync() and test_connection_and_access().
        """
        data_entities_processor = DataSourceEntitiesProcessor(
            logger,
            data_store_provider,
            config_service,
        )
        await data_entities_processor.initialize()
        return cls(
            logger=logger,
            data_entities_processor=data_entities_processor,
            data_store_provider=data_store_provider,
            config_service=config_service,
            connector_id=connector_id,
            scope=scope,
            created_by=created_by,
        )
