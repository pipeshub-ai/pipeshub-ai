"""Unit tests for app.connectors.sources.zoom.connector."""

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.zoom.connector import (
    ZoomConnector,
    ZoomMeetingDetail,
    ZoomMeetingReport,
    ZoomParticipant,
    ZoomUser,
)
from app.models.permission import PermissionType


def _make_connector() -> ZoomConnector:
    logger = MagicMock()
    dep = MagicMock()
    dep.org_id = "org-1"
    dep.on_new_app_users = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_new_records = AsyncMock()

    dsp = MagicMock()
    config_service = AsyncMock()

    return ZoomConnector(
        logger=logger,
        data_entities_processor=dep,
        data_store_provider=dsp,
        config_service=config_service,
        connector_id="zoom-conn-1",
    )


class TestZoomConnectorRecordBuilders:
    def test_build_meeting_record_uses_join_url_and_unique_name(self) -> None:
        connector = _make_connector()
        meeting_obj = ZoomMeetingReport(
            id=82768386593,
            topic="python basics overview",
            host_id="host-1",
            start_time="2026-03-30T18:00:00Z",
            end_time="2026-03-30T19:00:00Z",
            duration=60,
            type=2,
        )
        detail = ZoomMeetingDetail.model_validate(
            {"join_url": "https://us06web.zoom.us/j/82768386593?pwd=abc"}
        )

        rec = connector._build_meeting_record(
            meeting_obj=meeting_obj,
            meeting_uuid="uuid-1",
            meeting_detail=detail,
            host_email="host@example.com",
            record_group_id="group-1",
        )

        assert rec.record_name == "python basics overview (2026-03-30T18:00:00Z)"
        assert rec.external_revision_id == (
            "2026-03-30T18:00:00Z|2026-03-30T19:00:00Z|python basics overview"
        )
        assert rec.weburl == "https://us06web.zoom.us/j/82768386593?pwd=abc"

    def test_build_meeting_record_weburl_fallback(self) -> None:
        connector = _make_connector()
        meeting_obj = ZoomMeetingReport(
            id=111222333,
            topic="Fallback URL",
            host_id="host-1",
            start_time="",
            end_time="",
            duration=30,
            type=2,
        )

        rec = connector._build_meeting_record(
            meeting_obj=meeting_obj,
            meeting_uuid="uuid-2",
            meeting_detail=None,
            host_email="host@example.com",
            record_group_id="group-1",
        )

        assert rec.record_name == "Fallback URL"
        assert rec.weburl == "https://zoom.us/j/111222333"


class TestZoomConnectorPermissions:
    @pytest.mark.asyncio
    async def test_permissions_host_alt_hosts_participants_invitees_and_dedup(self) -> None:
        connector = _make_connector()
        connector._list_meeting_participants = AsyncMock(  # type: ignore[method-assign]
            return_value=[
                ZoomParticipant(user_email="p1@example.com"),
                ZoomParticipant(user_email="Host@Example.com"),  # duplicate with host
                ZoomParticipant(user_email="jchill@example.com"),  # duplicate with alt host
            ]
        )

        meeting_detail = ZoomMeetingDetail.model_validate({
            "settings": {
                "alternative_hosts": "jchill@example.com;thill@example.com",
                "meeting_invitees": [
                    {"email": "invitee1@example.com", "internal_user": False},
                    {"email": "p1@example.com", "internal_user": False},  # duplicate
                ],
            }
        })

        perms = await connector._build_meeting_permissions(
            meeting_detail=meeting_detail,
            encoded_uuid="encoded-uuid",
            host_email="host@example.com",
        )

        owner_emails = {
            p.email for p in perms if p.type == PermissionType.OWNER and p.email
        }
        read_emails = {
            p.email for p in perms if p.type == PermissionType.READ and p.email
        }

        assert owner_emails == {"host@example.com"}
        assert "jchill@example.com" in read_emails
        assert "thill@example.com" in read_emails
        assert "p1@example.com" in read_emails
        assert "invitee1@example.com" in read_emails
        # host should not be duplicated as READ
        assert "host@example.com" not in read_emails

    @pytest.mark.asyncio
    async def test_permissions_still_work_when_meeting_detail_missing(self) -> None:
        connector = _make_connector()
        connector._list_meeting_participants = AsyncMock(  # type: ignore[method-assign]
            return_value=[ZoomParticipant(user_email="participant@example.com")]
        )

        perms = await connector._build_meeting_permissions(
            meeting_detail=None,
            encoded_uuid="encoded-uuid",
            host_email="host@example.com",
        )
        emails = {p.email for p in perms if p.email}
        assert emails == {"host@example.com", "participant@example.com"}

    @pytest.mark.asyncio
    async def test_permissions_when_participants_fetch_fails(self) -> None:
        connector = _make_connector()
        connector._list_meeting_participants = AsyncMock(  # type: ignore[method-assign]
            side_effect=Exception("participants api failed")
        )
        meeting_detail = ZoomMeetingDetail.model_validate({
            "settings": {
                "alternative_hosts": "alt@example.com",
                "meeting_invitees": [{"email": "invitee@example.com"}],
            }
        })

        perms = await connector._build_meeting_permissions(
            meeting_detail=meeting_detail,
            encoded_uuid="encoded-uuid",
            host_email="host@example.com",
        )
        emails = {p.email for p in perms if p.email}
        assert emails == {"host@example.com", "alt@example.com", "invitee@example.com"}


class TestZoomConnectorFreshDatasource:
    @pytest.mark.asyncio
    async def test_get_fresh_datasource_updates_internal_token(self) -> None:
        connector = _make_connector()

        internal = MagicMock()
        internal.get_token.return_value = "old-token"
        external_client = MagicMock()
        external_client.get_client.return_value = internal
        connector.external_client = external_client
        connector.data_source = MagicMock()
        connector.config_service.get_config = AsyncMock(
            return_value={"credentials": {"access_token": "new-token"}}
        )

        ds = await connector._get_fresh_datasource()
        assert ds is connector.data_source
        internal.set_token.assert_called_once_with("new-token")

    @pytest.mark.asyncio
    async def test_get_fresh_datasource_no_update_when_token_same(self) -> None:
        connector = _make_connector()

        internal = MagicMock()
        internal.get_token.return_value = "same-token"
        external_client = MagicMock()
        external_client.get_client.return_value = internal
        connector.external_client = external_client
        connector.data_source = MagicMock()
        connector.config_service.get_config = AsyncMock(
            return_value={"credentials": {"access_token": "same-token"}}
        )

        await connector._get_fresh_datasource()
        internal.set_token.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_fresh_datasource_raises_when_token_missing(self) -> None:
        connector = _make_connector()
        connector.external_client = MagicMock()
        connector.data_source = MagicMock()
        connector.config_service.get_config = AsyncMock(return_value={"credentials": {}})

        with pytest.raises(RuntimeError, match="No OAuth access token available"):
            await connector._get_fresh_datasource()


class TestZoomConnectorSyncFlow:
    @pytest.mark.asyncio
    async def test_run_sync_continues_when_one_user_fails(self) -> None:
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._load_filters = AsyncMock()  # type: ignore[method-assign]
        connector._today = MagicMock(return_value=date(2026, 3, 31))  # type: ignore[method-assign]
        connector._list_users = AsyncMock(  # type: ignore[method-assign]
            return_value=[ZoomUser(id="u1", email="u1@example.com"), ZoomUser(id="u2", email="u2@example.com")]
        )
        connector._build_app_users = MagicMock(return_value=[])  # type: ignore[method-assign]
        connector._sync_meetings_for_user = AsyncMock(  # type: ignore[method-assign]
            side_effect=[Exception("u1 failed"), None]
        )

        await connector.run_sync()

        assert connector._sync_meetings_for_user.await_count == 2

    @pytest.mark.asyncio
    async def test_run_sync_raises_on_fatal_error(self) -> None:
        """Top-level failure (e.g. list_users crash) re-raises after logging."""
        connector = _make_connector()
        connector.data_source = MagicMock()
        connector._load_filters = AsyncMock()  # type: ignore[method-assign]
        connector._today = MagicMock(return_value=date(2026, 3, 31))  # type: ignore[method-assign]
        connector._list_users = AsyncMock(  # type: ignore[method-assign]
            side_effect=RuntimeError("auth failed")
        )

        with pytest.raises(RuntimeError, match="auth failed"):
            await connector.run_sync()

    @pytest.mark.asyncio
    async def test_run_incremental_sync_delegates_to_run_sync(self) -> None:
        connector = _make_connector()
        connector.run_sync = AsyncMock()  # type: ignore[method-assign]
        await connector.run_incremental_sync()
        connector.run_sync.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_meetings_for_user_no_chunks_updates_sync_point(self) -> None:
        """When calculate_sync_chunks returns [] the sync-point is still bumped."""
        connector = _make_connector()
        today = date(2026, 3, 31)
        zoom_user = ZoomUser(id="u1", email="u1@example.com", created_at=None)

        # last_sync = tomorrow → start = today >= today → empty chunks
        connector._get_user_meeting_sync_point = AsyncMock(  # type: ignore[method-assign]
            return_value=today + timedelta(days=1)
        )
        connector._update_user_meeting_sync_point = AsyncMock()  # type: ignore[method-assign]
        connector._list_report_meetings = AsyncMock(return_value=[])  # type: ignore[method-assign]
        connector._build_record_group = MagicMock(  # type: ignore[method-assign]
            return_value=(MagicMock(id="rg-1"), [])
        )

        await connector._sync_meetings_for_user(zoom_user, today)

        connector._list_report_meetings.assert_not_awaited()
        connector._update_user_meeting_sync_point.assert_awaited_once_with(
            "u1", today - timedelta(days=1)
        )

    @pytest.mark.asyncio
    async def test_sync_meetings_for_user_skips_meeting_without_uuid(self) -> None:
        """Meetings with empty uuid are skipped; sync point still updated."""
        connector = _make_connector()
        today = date(2026, 3, 31)
        zoom_user = ZoomUser(id="u1", email="u1@example.com")

        connector._get_user_meeting_sync_point = AsyncMock(return_value=None)  # type: ignore[method-assign]
        connector._update_user_meeting_sync_point = AsyncMock()  # type: ignore[method-assign]
        connector._build_record_group = MagicMock(  # type: ignore[method-assign]
            return_value=(MagicMock(id="rg-1"), [])
        )
        connector._list_report_meetings = AsyncMock(  # type: ignore[method-assign]
            return_value=[ZoomMeetingReport(id=111, uuid="")]  # empty uuid
        )

        await connector._sync_meetings_for_user(zoom_user, today)

        connector.data_entities_processor.on_new_records.assert_not_awaited()
        connector._update_user_meeting_sync_point.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_meetings_per_meeting_exception_continues(self) -> None:
        """One bad meeting should not abort others in the same chunk."""
        connector = _make_connector()
        today = date(2026, 3, 31)
        zoom_user = ZoomUser(id="u1", email="host@example.com")

        # Use incremental mode to produce a single chunk and keep flush count deterministic.
        connector._get_user_meeting_sync_point = AsyncMock(return_value=today)  # type: ignore[method-assign]
        connector._update_user_meeting_sync_point = AsyncMock()  # type: ignore[method-assign]
        connector._build_record_group = MagicMock(  # type: ignore[method-assign]
            return_value=(MagicMock(id="rg-1"), [])
        )
        connector._list_report_meetings = AsyncMock(  # type: ignore[method-assign]
            return_value=[
                ZoomMeetingReport(id=111, uuid="bad-uuid"),
                ZoomMeetingReport(id=222, uuid="good-uuid"),
            ]
        )
        connector._get_meeting_detail = AsyncMock(return_value=None)  # type: ignore[method-assign]
        connector._build_meeting_permissions = AsyncMock(return_value=[])  # type: ignore[method-assign]

        # First meeting build raises, second succeeds
        original_build = connector._build_meeting_record
        call_count = {"n": 0}

        def _side_effect(**kwargs) -> object:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise ValueError("bad meeting data")
            return original_build(**kwargs)

        connector._build_meeting_record = _side_effect  # type: ignore[method-assign]

        await connector._sync_meetings_for_user(zoom_user, today)

        # Second meeting still recorded
        connector.data_entities_processor.on_new_records.assert_awaited_once()
        connector._update_user_meeting_sync_point.assert_awaited_once()


# ============================================================================
# Sync-point / checkpoint
# ============================================================================

class TestZoomSyncPoint:
    @pytest.mark.asyncio
    async def test_get_user_meeting_sync_point_valid_date(self) -> None:
        connector = _make_connector()
        connector.sync_point = MagicMock()
        connector.sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_date": "2026-03-20"}
        )
        result = await connector._get_user_meeting_sync_point("u1")
        assert result == date(2026, 3, 20)

    @pytest.mark.asyncio
    async def test_get_user_meeting_sync_point_missing_returns_none(self) -> None:
        connector = _make_connector()
        connector.sync_point = MagicMock()
        connector.sync_point.read_sync_point = AsyncMock(return_value={})
        result = await connector._get_user_meeting_sync_point("u1")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_user_meeting_sync_point_bad_date_returns_none(self) -> None:
        connector = _make_connector()
        connector.sync_point = MagicMock()
        connector.sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_date": "not-a-date"}
        )
        result = await connector._get_user_meeting_sync_point("u1")
        assert result is None

    @pytest.mark.asyncio
    async def test_update_user_meeting_sync_point_writes_correct_key(self) -> None:
        connector = _make_connector()
        connector.sync_point = MagicMock()
        connector.sync_point.update_sync_point = AsyncMock()

        await connector._update_user_meeting_sync_point("u-xyz", date(2026, 3, 30))

        connector.sync_point.update_sync_point.assert_awaited_once_with(
            "zoom_report_last_sync/u-xyz",
            {"last_sync_date": "2026-03-30"},
        )


# ============================================================================
# _calculate_sync_chunks
# ============================================================================

class TestCalculateSyncChunks:
    def test_incremental_has_one_day_overlap(self) -> None:
        today = date(2026, 3, 31)
        last = date(2026, 3, 20)
        chunks = ZoomConnector._calculate_sync_chunks(
            user_created_at=None, last_sync_date=last, today=today
        )
        # start = 2026-03-19  → 12 days < 30 → single chunk
        assert len(chunks) == 1
        assert chunks[0][0] == "2026-03-19"
        assert chunks[0][1] == "2026-03-31"

    def test_no_chunks_when_start_equals_today(self) -> None:
        today = date(2026, 3, 31)
        # last_sync = today → start = today - 1, still < today, so 1 chunk
        last = today + timedelta(days=1)  # last is in the future
        chunks = ZoomConnector._calculate_sync_chunks(
            user_created_at=None, last_sync_date=last, today=today
        )
        assert chunks == []

    def test_first_sync_respects_six_month_limit(self) -> None:
        today = date(2026, 3, 31)
        # user created 2 years ago — must be capped at 180 days ago
        chunks = ZoomConnector._calculate_sync_chunks(
            user_created_at="2020-01-01T00:00:00Z",
            last_sync_date=None,
            today=today,
        )
        # 180 days / 30 days per chunk = exactly 6 chunks
        assert len(chunks) == 6
        assert chunks[0][0] == (today - timedelta(days=180)).isoformat()

    def test_first_sync_uses_user_created_at_if_recent(self) -> None:
        today = date(2026, 3, 31)
        user_date = today - timedelta(days=10)
        chunks = ZoomConnector._calculate_sync_chunks(
            user_created_at=f"{user_date.isoformat()}T00:00:00Z",
            last_sync_date=None,
            today=today,
        )
        assert len(chunks) == 1
        assert chunks[0][0] == user_date.isoformat()

    def test_first_sync_falls_back_on_invalid_created_at(self) -> None:
        today = date(2026, 3, 31)
        chunks = ZoomConnector._calculate_sync_chunks(
            user_created_at="not-a-date",
            last_sync_date=None,
            today=today,
        )
        assert len(chunks) == 6
        assert chunks[0][0] == (today - timedelta(days=180)).isoformat()

    def test_chunks_are_at_most_30_days_each(self) -> None:
        today = date(2026, 3, 31)
        chunks = ZoomConnector._calculate_sync_chunks(
            user_created_at="2020-01-01T00:00:00Z",
            last_sync_date=None,
            today=today,
        )
        for from_str, to_str in chunks:
            from_d = date.fromisoformat(from_str)
            to_d = date.fromisoformat(to_str)
            assert (to_d - from_d).days <= 30

    def test_consecutive_chunks_are_contiguous(self) -> None:
        today = date(2026, 3, 31)
        chunks = ZoomConnector._calculate_sync_chunks(
            user_created_at="2020-01-01T00:00:00Z",
            last_sync_date=None,
            today=today,
        )
        for i in range(len(chunks) - 1):
            assert chunks[i][1] == chunks[i + 1][0]


# ============================================================================
# API wrappers
# ============================================================================

class TestZoomApiWrappers:
    @pytest.mark.asyncio
    async def test_list_users_paginates_and_collects_all(self) -> None:
        connector = _make_connector()
        page1 = MagicMock(success=True, data={
            "users": [{"id": "u1"}, {"id": "u2"}],
            "next_page_token": "tok1",
        })
        page2 = MagicMock(success=True, data={
            "users": [{"id": "u3"}],
            "next_page_token": None,
        })
        ds = MagicMock()
        ds.users = AsyncMock(side_effect=[page1, page2])
        connector._get_fresh_datasource = AsyncMock(return_value=ds)  # type: ignore[method-assign]

        users = await connector._list_users()

        assert len(users) == 3
        assert ds.users.await_count == 2

    @pytest.mark.asyncio
    async def test_list_users_breaks_on_failure(self) -> None:
        connector = _make_connector()
        fail = MagicMock(success=False, data=None, message="401")
        ds = MagicMock()
        ds.users = AsyncMock(return_value=fail)
        connector._get_fresh_datasource = AsyncMock(return_value=ds)  # type: ignore[method-assign]

        users = await connector._list_users()
        assert users == []

    @pytest.mark.asyncio
    async def test_list_report_meetings_paginates(self) -> None:
        connector = _make_connector()
        page1 = MagicMock(success=True, data={
            "meetings": [{"uuid": "m1"}, {"uuid": "m2"}],
            "next_page_token": "tok",
        })
        page2 = MagicMock(success=True, data={
            "meetings": [{"uuid": "m3"}],
            "next_page_token": None,
        })
        ds = MagicMock()
        ds.report_meetings = AsyncMock(side_effect=[page1, page2])
        connector._get_fresh_datasource = AsyncMock(return_value=ds)  # type: ignore[method-assign]

        meetings = await connector._list_report_meetings("u1", "2026-03-01", "2026-03-31")
        assert [m.uuid for m in meetings] == ["m1", "m2", "m3"]

    @pytest.mark.asyncio
    async def test_list_report_meetings_breaks_on_failure_with_code(self) -> None:
        connector = _make_connector()
        fail = MagicMock(
            success=False,
            data={"code": 124, "message": "Access token is expired."},
            message="Failed with status 401",
        )
        ds = MagicMock()
        ds.report_meetings = AsyncMock(return_value=fail)
        connector._get_fresh_datasource = AsyncMock(return_value=ds)  # type: ignore[method-assign]

        meetings = await connector._list_report_meetings("u1", "2026-03-01", "2026-03-31")
        assert meetings == []

    @pytest.mark.asyncio
    async def test_list_meeting_participants_paginates(self) -> None:
        connector = _make_connector()
        page1 = MagicMock(success=True, data={
            "participants": [{"user_email": "a@x.com"}],
            "next_page_token": "t",
        })
        page2 = MagicMock(success=True, data={
            "participants": [{"user_email": "b@x.com"}],
            "next_page_token": None,
        })
        ds = MagicMock()
        ds.report_meeting_participants = AsyncMock(side_effect=[page1, page2])
        connector._get_fresh_datasource = AsyncMock(return_value=ds)  # type: ignore[method-assign]

        result = await connector._list_meeting_participants("encoded-uuid")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_get_meeting_detail_returns_none_on_failure(self) -> None:
        connector = _make_connector()
        ds = MagicMock()
        ds.meeting = AsyncMock(return_value=MagicMock(success=False, data=None, message="404"))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)  # type: ignore[method-assign]

        result = await connector._get_meeting_detail("999")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_meeting_detail_returns_none_on_exception(self) -> None:
        connector = _make_connector()
        ds = MagicMock()
        ds.meeting = AsyncMock(side_effect=Exception("network error"))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)  # type: ignore[method-assign]

        result = await connector._get_meeting_detail("999")
        assert result is None


# ============================================================================
# _fetch_transcript branches
# ============================================================================

class TestFetchTranscript:
    def _make_ds(self) -> MagicMock:
        return MagicMock()

    @pytest.mark.asyncio
    async def test_empty_uuid_returns_none(self) -> None:
        connector = _make_connector()
        ds = self._make_ds()
        connector._get_fresh_datasource = AsyncMock(return_value=ds)  # type: ignore[method-assign]
        result = await connector._fetch_transcript("")
        assert result is None
        ds.meeting_transcript_metadata.assert_not_called()

    @pytest.mark.asyncio
    async def test_code_3322_returns_none_silently(self) -> None:
        connector = _make_connector()
        ds = self._make_ds()
        ds.meeting_transcript_metadata = AsyncMock(return_value=MagicMock(
            success=False,
            data={"code": 3322},
            message="no transcript",
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)  # type: ignore[method-assign]

        result = await connector._fetch_transcript("some-uuid")
        assert result is None

    @pytest.mark.asyncio
    async def test_code_3001_meeting_not_found_returns_none(self) -> None:
        connector = _make_connector()
        ds = self._make_ds()
        ds.meeting_transcript_metadata = AsyncMock(return_value=MagicMock(
            success=False,
            data={"code": 3001},
            message="not found",
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)  # type: ignore[method-assign]

        result = await connector._fetch_transcript("some-uuid")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_download_url_returns_none(self) -> None:
        connector = _make_connector()
        ds = self._make_ds()
        ds.meeting_transcript_metadata = AsyncMock(return_value=MagicMock(
            success=True,
            data={"download_url": None},
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)  # type: ignore[method-assign]

        result = await connector._fetch_transcript("some-uuid")
        assert result is None

    @pytest.mark.asyncio
    async def test_download_failure_returns_none(self) -> None:
        connector = _make_connector()
        ds = self._make_ds()
        ds.meeting_transcript_metadata = AsyncMock(return_value=MagicMock(
            success=True,
            data={"download_url": "https://zoom.us/dl/transcript"},
        ))
        ds.meeting_transcript_download = AsyncMock(return_value=MagicMock(
            success=False, message="403",
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)  # type: ignore[method-assign]

        result = await connector._fetch_transcript("some-uuid")
        assert result is None

    @pytest.mark.asyncio
    async def test_successful_transcript_returned(self) -> None:
        connector = _make_connector()
        ds = self._make_ds()
        ds.meeting_transcript_metadata = AsyncMock(return_value=MagicMock(
            success=True,
            data={"download_url": "https://zoom.us/dl/transcript"},
        ))
        ds.meeting_transcript_download = AsyncMock(return_value=MagicMock(
            success=True,
            data={"transcript_text": "Hello world"},
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)  # type: ignore[method-assign]

        result = await connector._fetch_transcript("some-uuid")
        assert result == "Hello world"

    @pytest.mark.asyncio
    async def test_exception_during_fetch_returns_none(self) -> None:
        connector = _make_connector()
        ds = self._make_ds()
        ds.meeting_transcript_metadata = AsyncMock(side_effect=RuntimeError("crash"))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)  # type: ignore[method-assign]

        result = await connector._fetch_transcript("some-uuid")
        assert result is None


# ============================================================================
# _build_app_users
# ============================================================================

class TestBuildAppUsers:
    def test_maps_users_with_display_name(self) -> None:
        connector = _make_connector()
        users = [
            ZoomUser(
                id="u1",
                email="User@Example.com",
                display_name="Display Name",
                first_name="First",
                last_name="Last",
                created_at="2026-01-01T00:00:00Z",
            )
        ]
        result = connector._build_app_users(users)
        assert len(result) == 1
        assert result[0].email == "user@example.com"
        assert result[0].full_name == "Display Name"

    def test_falls_back_to_first_last_name(self) -> None:
        connector = _make_connector()
        users = [ZoomUser(id="u1", email="a@b.com", first_name="Joe", last_name="Smith")]
        result = connector._build_app_users(users)
        assert result[0].full_name == "Joe Smith"

    def test_skips_users_without_email(self) -> None:
        connector = _make_connector()
        users = [ZoomUser(id="u1", email="")]
        result = connector._build_app_users(users)
        assert result == []

    def test_skips_users_without_id(self) -> None:
        connector = _make_connector()
        users = [ZoomUser(id="", email="a@b.com")]
        result = connector._build_app_users(users)
        assert result == []

    def test_multiple_users_all_mapped(self) -> None:
        connector = _make_connector()
        users = [
            ZoomUser(id="u1", email="a@b.com", first_name="A", last_name="B"),
            ZoomUser(id="u2", email="c@d.com", first_name="C", last_name="D"),
        ]
        result = connector._build_app_users(users)
        assert len(result) == 2


# ============================================================================
# _encode_uuid
# ============================================================================

class TestEncodeUuid:
    def test_simple_uuid_single_encoded(self) -> None:
        uuid = "WJx2ow0jRU2wFut0EeIrEA=="
        encoded = ZoomConnector._encode_uuid(uuid)
        assert "==" not in encoded
        assert "%3D%3D" in encoded

    def test_uuid_starting_with_slash_double_encoded(self) -> None:
        uuid = "/Mn/RVBn=="
        encoded = ZoomConnector._encode_uuid(uuid)
        # Double encoded: / → %25 2F
        assert "%252F" in encoded

    def test_uuid_containing_double_slash_double_encoded(self) -> None:
        uuid = "abc//def"
        encoded = ZoomConnector._encode_uuid(uuid)
        assert "%252F" in encoded


# ============================================================================
# _zoom_iso_to_ms
# ============================================================================

class TestZoomIsoToMs:
    def test_valid_iso_z_suffix(self) -> None:
        result = ZoomConnector._zoom_iso_to_ms("2026-03-30T18:00:00Z")
        assert isinstance(result, int)
        assert result > 0

    def test_none_returns_none(self) -> None:
        assert ZoomConnector._zoom_iso_to_ms(None) is None

    def test_empty_returns_none(self) -> None:
        assert ZoomConnector._zoom_iso_to_ms("") is None

    def test_invalid_string_returns_none(self) -> None:
        assert ZoomConnector._zoom_iso_to_ms("not-a-date") is None

    def test_non_string_returns_none(self) -> None:
        assert ZoomConnector._zoom_iso_to_ms(12345) is None  # type: ignore[arg-type]


# ============================================================================
# init / test_connection_and_access
# ============================================================================

class TestZoomConnectorInit:
    @pytest.mark.asyncio
    async def test_init_success(self) -> None:
        connector = _make_connector()
        mock_client = MagicMock()
        mock_client.get_client.return_value = MagicMock()

        with patch(
            "app.connectors.sources.zoom.connector.ZoomClient"
        ) as MockClient, patch(
            "app.connectors.sources.zoom.connector.ZoomDataSource"
        ):
            MockClient.build_from_services = AsyncMock(return_value=mock_client)
            result = await connector.init()

        assert result is True
        assert connector.external_client is mock_client

    @pytest.mark.asyncio
    async def test_init_failure_returns_false(self) -> None:
        connector = _make_connector()
        with patch(
            "app.connectors.sources.zoom.connector.ZoomClient"
        ) as MockClient:
            MockClient.build_from_services = AsyncMock(
                side_effect=Exception("bad creds")
            )
            result = await connector.init()

        assert result is False

    @pytest.mark.asyncio
    async def test_test_connection_and_access_success(self) -> None:
        connector = _make_connector()
        connector.data_source = MagicMock()
        ds = MagicMock()
        ds.users = AsyncMock(return_value=MagicMock(success=True))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)  # type: ignore[method-assign]

        result = await connector.test_connection_and_access()
        assert result is True

    @pytest.mark.asyncio
    async def test_test_connection_and_access_failure(self) -> None:
        connector = _make_connector()
        connector.data_source = MagicMock()
        ds = MagicMock()
        ds.users = AsyncMock(return_value=MagicMock(success=False, message="401"))
        connector._get_fresh_datasource = AsyncMock(return_value=ds)  # type: ignore[method-assign]

        result = await connector.test_connection_and_access()
        assert result is False

