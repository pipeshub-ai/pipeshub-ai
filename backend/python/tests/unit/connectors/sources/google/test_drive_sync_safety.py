"""The Drive connectors' run safety: what a failure means, what the owners are told, which accounts lose access,
how long a checkpoint is held, domain-wide shares, and native-file revisions."""

from __future__ import annotations

import json
import logging
from typing import Any

import httplib2
import pytest
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.sources.google.common.access_problems import (
    AccessProblem,
    classify_access_problem,
)
from app.connectors.sources.google.common.connector_google_exceptions import (
    ServiceAccountKeyError,
)
from app.connectors.sources.google.drive.team.user_status import UserStatusTracker
from app.connectors.sources.google.drive.utils.checkpoint_hold import (
    HELD_RUNS_FIELD,
    MAX_HELD_RUNS,
    hold_again,
    released,
)
from app.connectors.sources.google.drive.utils.drive_full_sync_reconciler import (
    FullSyncLedger,
)
from app.connectors.sources.google.drive.utils.drive_run_report import (
    PERSISTENT_RUNS,
    DriveRunReport,
    message_for_connection_failure,
    notice_for,
)
from app.connectors.sources.google.drive.utils.folder_filter_utils import (
    drive_error_reasons,
    is_retryable_403,
    is_unrecognised_403,
)
from app.connectors.sources.google.drive.utils.native_revisions import (
    native_content_changed,
)
from app.models.entities import AppUser

LOGGER = logging.getLogger("drive-sync-safety")


def _http_error(status: int, reason: str, *, details: bool = False, uri: str = "https://www.googleapis.com/drive/v3/files") -> HttpError:
    body: dict[str, Any] = {"error": {"code": status, "message": "m", "errors": [{"reason": reason, "domain": "global"}]}}
    if details:
        body["error"]["details"] = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": reason.upper()}]
    return HttpError(httplib2.Response({"status": status}), json.dumps(body).encode(), uri=uri)


def _refresh(code: str, description: str) -> RefreshError:
    return RefreshError(f"{code}: {description}", {"error": code, "error_description": description})


class _SyncPoint:
    def __init__(self) -> None:
        self.data: dict[str, dict] = {}

    async def read_sync_point(self, key: str) -> dict:
        return dict(self.data.get(key) or {})

    async def update_sync_point(self, key: str, value: dict) -> None:
        self.data.setdefault(key, {}).update(value)


def _user(email: str, *, active: bool = True) -> AppUser:
    return AppUser(app_name="DRIVE WORKSPACE", connector_id="c1", source_user_id=email, email=email,
                   full_name=email, is_active=active)


class TestAccessProblems:
    @pytest.mark.parametrize("error, personal, admin, expected", [
        (_refresh("unauthorized_client", "Client is unauthorized"), False, True, AccessProblem.DELEGATION),
        (_refresh("invalid_grant", "Invalid JWT Signature."), False, True, AccessProblem.SERVICE_ACCOUNT_KEY),
        (_refresh("invalid_grant", "Invalid email or User ID"), False, True, AccessProblem.ADMIN_ACCOUNT),
        (_refresh("invalid_grant", "Invalid email or User ID"), False, False, AccessProblem.USER_UNAVAILABLE),
        (_refresh("invalid_grant", "Token has been expired or revoked."), True, True, AccessProblem.SIGNED_OUT),
        (_http_error(403, "accessNotConfigured"), False, True, AccessProblem.API_DISABLED),
        (_http_error(403, "insufficientPermissions"), True, True, AccessProblem.MISSING_SCOPE),
        (_http_error(403, "insufficientPermissions"), False, True, AccessProblem.DELEGATION),
        (_http_error(403, "forbidden", uri="https://admin.googleapis.com/admin/directory/v1/users"), False, True,
         AccessProblem.ADMIN_ACCOUNT),
    ])
    def test_setup_problems_are_named(self, error: BaseException, personal: bool, admin: bool,
                                      expected: AccessProblem) -> None:
        assert classify_access_problem(error, personal=personal, impersonating_admin=admin) is expected

    @pytest.mark.parametrize("error", [
        _http_error(503, "backendError"), _http_error(429, "rateLimitExceeded"),
        _http_error(403, "userRateLimitExceeded"), _http_error(404, "notFound"), TimeoutError("slow"),
    ])
    def test_passing_failures_are_not_setup_problems(self, error: BaseException) -> None:
        assert classify_access_problem(error, personal=False) is None

    def test_a_wrapped_failure_is_still_recognised(self) -> None:
        try:
            try:
                raise _refresh("unauthorized_client", "no")
            except RefreshError as inner:
                raise ValueError("Failed to initialize Google Admin client") from inner
        except ValueError as outer:
            assert classify_access_problem(outer, personal=False) is AccessProblem.DELEGATION


    def test_a_key_that_is_not_a_key_file_is_named(self) -> None:
        try:
            try:
                raise ServiceAccountKeyError("Invalid service account JSON")
            except ServiceAccountKeyError as inner:
                raise ValueError("Failed to initialize Google Admin client") from inner
        except ValueError as outer:
            assert classify_access_problem(outer, personal=False) is AccessProblem.UNREADABLE_KEY
            assert classify_access_problem(outer, personal=True) is None


_CORRUPT_KEY = json.dumps({
    "type": "service_account", "project_id": "p", "private_key_id": "k", "client_id": "1",
    "client_email": "sa@p.iam.gserviceaccount.com", "token_uri": "https://oauth2.googleapis.com/token",
    "private_key": "-----BEGIN PRIVATE KEY-----\nbm90IGEga2V5\n-----END PRIVATE KEY-----\n",
})


class TestUnreadableServiceAccountKey:
    """Turning on a connector whose saved key is not a usable key file says so, instead of 'try again'."""

    @pytest.mark.parametrize("saved_key", ["this is not a key file", _CORRUPT_KEY], ids=["not-json", "corrupt-key"])
    async def test_init_names_the_key_as_the_problem(self, saved_key: str) -> None:
        from unittest.mock import AsyncMock, MagicMock

        from app.connectors.sources.google.drive.team.connector import (
            GoogleDriveTeamConnector,
        )

        conn = object.__new__(GoogleDriveTeamConnector)
        conn.logger = LOGGER
        conn.connector_id = "drive-1"
        conn._thread_lease = MagicMock()
        conn.config_service = AsyncMock()
        conn.config_service.get_config = AsyncMock(return_value={
            "auth": {"adminEmail": "admin@acme.example", "serviceAccountJson": saved_key},
        })
        with pytest.raises(ConnectorInitError) as raised:
            await conn.init()
        message = str(raised.value)
        assert "not a valid JSON key file" in message and "upload" in message
        for internal in ("PEM", "JSON decode", "Expecting value", "delegated credentials", "Traceback"):
            assert internal not in message


class TestNotices:
    @pytest.mark.parametrize("problem", [p for p in AccessProblem if p is not AccessProblem.USER_UNAVAILABLE])
    def test_every_setup_problem_has_words_without_internals(self, problem: AccessProblem) -> None:
        notice = notice_for(problem, personal=problem in (AccessProblem.SIGNED_OUT, AccessProblem.MISSING_SCOPE),
                            admin_email="admin@acme.example", client_id="1234")
        assert notice is not None
        text = notice.event + " " + notice.message
        for internal in ("HttpError", "invalid_grant", "unauthorized_client", "Traceback", "googleapis.com", "{"):
            assert internal not in text

    def test_an_unavailable_user_is_not_reported(self) -> None:
        assert notice_for(AccessProblem.USER_UNAVAILABLE, personal=False) is None


class TestRunReport:
    async def _run(self, report: DriveRunReport, **kwargs: object) -> None:
        await report.finish(**kwargs)

    def _report(self, sent: list, sync_point: _SyncPoint) -> DriveRunReport:
        async def notify(**kwargs: object) -> None:
            sent.append(kwargs)

        return DriveRunReport(personal=False, notify=notify, title=lambda e: f"Drive: {e}", sync_point=sync_point,
                              logger=LOGGER, admin_email="admin@acme.example", client_id="1234")

    async def test_folders_nobody_can_open_are_named_once(self) -> None:
        sent: list = []
        report = self._report(sent, _SyncPoint())
        report.note_unreadable_folders(["folder-b", "folder-a", "folder-a"])
        await report.finish(complete=True)
        assert len(sent) == 1 and "2 folders" in sent[0]["message"]

    async def test_a_refused_connection_names_the_setup_problem(self) -> None:
        from unittest.mock import AsyncMock

        from app.connectors.sources.google.drive.team.connector import (
            GoogleDriveTeamConnector,
        )

        conn = object.__new__(GoogleDriveTeamConnector)
        conn.logger = LOGGER
        conn.drive_data_source = AsyncMock()
        conn.admin_data_source = AsyncMock()
        conn.drive_client = object()
        conn.admin_client = object()
        conn.config = {"credentials": {"auth": {"adminEmail": "admin@acme.example"}}}
        conn.admin_data_source.users_get = AsyncMock(
            side_effect=_refresh("unauthorized_client", "Client is unauthorized")
        )
        with pytest.raises(ConnectorInitError, match="Domain-wide delegation"):
            await conn.test_connection_and_access()
        assert message_for_connection_failure(_http_error(503, "backendError"), personal=False) is None

    async def test_a_setup_problem_is_reported_as_an_error(self) -> None:
        sent: list = []
        report = self._report(sent, _SyncPoint())
        report.note_failure(_refresh("unauthorized_client", "x"))
        await report.finish(complete=False)
        assert len(sent) == 1 and sent[0]["severity"].value == "error" and "1234" in sent[0]["message"]

    async def test_a_passing_gap_is_reported_only_when_it_lasts(self) -> None:
        sent: list = []
        sync_point = _SyncPoint()
        for run in range(PERSISTENT_RUNS + 2):
            report = self._report(sent, sync_point)
            report.note_failure(_http_error(503, "backendError"))
            await report.finish(complete=False)
            assert len(sent) == (1 if run + 1 >= PERSISTENT_RUNS else 0)

    async def test_a_complete_run_starts_the_count_over(self) -> None:
        sent: list = []
        sync_point = _SyncPoint()
        for complete in (False, False, True, False, False):
            await self._report(sent, sync_point).finish(complete=complete)
        assert sent == []

    async def test_a_failing_notifier_does_not_change_the_run(self) -> None:
        async def notify(**_: object) -> None:
            raise RuntimeError("broker down")

        report = DriveRunReport(personal=True, notify=notify, title=str, sync_point=_SyncPoint(), logger=LOGGER)
        report.note_failure(_refresh("invalid_grant", "Token has been expired or revoked."))
        await report.finish(complete=False)


class TestUserStatus:
    async def test_suspended_and_deleted_accounts_are_withdrawn_once(self) -> None:
        sync_point = _SyncPoint()
        tracker = UserStatusTracker(sync_point)
        first = await tracker.compare([_user("a@x"), _user("b@x", active=False), _user("c@x")])
        assert first.withdraw == {"b@x"} and first.inactive == {"b@x"}
        await tracker.save()
        second = await tracker.compare([_user("a@x"), _user("b@x", active=False)])
        assert second.withdraw == {"c@x"} and second.inactive == {"b@x", "c@x"}
        await tracker.save()
        third = await tracker.compare([_user("a@x"), _user("b@x")])
        assert third.restore == {"b@x"} and not third.withdraw

    async def test_a_deleted_account_is_restored_when_it_returns(self) -> None:
        sync_point = _SyncPoint()
        tracker = UserStatusTracker(sync_point)
        await tracker.compare([_user("a@x")])
        await tracker.save()
        second = await tracker.compare([])
        assert second.withdraw == {"a@x"}
        await tracker.save()
        third = await tracker.compare([_user("a@x")])
        assert third.restore == {"a@x"} and not third.withdraw

    async def test_nothing_is_saved_until_asked(self) -> None:
        sync_point = _SyncPoint()
        tracker = UserStatusTracker(sync_point)
        await tracker.compare([_user("b@x", active=False)])
        assert sync_point.data == {}


class TestCheckpointHold:
    def test_held_up_to_the_limit_then_released(self) -> None:
        point: dict = {}
        for _ in range(MAX_HELD_RUNS - 1):
            hold = hold_again(point)
            assert hold is not None
            point.update(hold)
        assert hold_again(point) is None
        assert released(point) == {HELD_RUNS_FIELD: 0}
        assert released({}) == {}


class TestNativeRevisions:
    @staticmethod
    def _lister(pages: list[dict]) -> object:
        calls = {"n": 0}

        async def list_revisions(**_: object) -> dict:
            page = pages[calls["n"]]
            calls["n"] += 1
            return page

        return list_revisions

    async def test_a_rename_without_a_new_revision_is_not_a_content_change(self) -> None:
        pages = [{"revisions": [{"id": "1", "modifiedTime": "2026-01-01T00:00:00.000Z"}], "nextPageToken": "p2"},
                 {"revisions": [{"id": "2", "modifiedTime": "2026-01-02T00:00:00.000Z"}]}]
        stored = 1767312000000  # 2026-01-02T00:00:00Z
        assert await native_content_changed("f", stored, self._lister(pages), LOGGER) is False

    async def test_the_stored_time_cut_to_seconds_is_not_older_than_its_own_revision(self) -> None:
        from app.utils.time_conversion import parse_timestamp

        modified = "2026-01-02T00:00:00.734Z"
        pages = [{"revisions": [{"id": "1", "modifiedTime": modified}]}]
        stored = parse_timestamp(modified)
        assert await native_content_changed("f", stored, self._lister(pages), LOGGER) is False

    async def test_a_newer_revision_is_a_content_change(self) -> None:
        pages = [{"revisions": [{"id": "3", "modifiedTime": "2026-01-03T00:00:00.000Z"}]}]
        assert await native_content_changed("f", 1767312000000, self._lister(pages), LOGGER) is True

    async def test_revisions_it_may_not_read_count_as_changed(self) -> None:
        async def refused(**_: object) -> dict:
            raise _http_error(403, "insufficientFilePermissions")

        assert await native_content_changed("f", 1767312000000, refused, LOGGER) is True

    async def test_an_outage_raises(self) -> None:
        async def down(**_: object) -> dict:
            raise _http_error(503, "backendError")

        with pytest.raises(HttpError):
            await native_content_changed("f", 1767312000000, down, LOGGER)


class TestReasons:
    def test_quota_reasons_are_read_from_errors_even_when_details_are_present(self) -> None:
        error = _http_error(403, "rateLimitExceeded", details=True)
        assert {"rateLimitExceeded", "RATELIMITEXCEEDED"} <= drive_error_reasons(error)
        assert is_retryable_403(error) and not is_unrecognised_403(error)


class TestLedger:
    def test_gaps(self) -> None:
        ledger = FullSyncLedger()
        assert not ledger.has_gaps
        ledger.fail_item()
        assert ledger.has_gaps
        other = FullSyncLedger()
        other.note_skipped_drive()
        assert other.has_gaps
