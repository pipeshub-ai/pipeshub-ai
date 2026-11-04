"""The SharePoint connector's safety modules: what a run could not read, the tenant-wide claim groups, the
removal of what a complete listing lacks, the memory of known containers, and SharePoint REST reads that
retry rather than pass a failure off as an empty answer."""

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from azure.core.exceptions import ClientAuthenticationError

from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.core.sync.full_sync_coverage import FullSyncCoverage
from app.connectors.sources.microsoft.sharepoint_online.container_tracker import (
    ContainerTracker,
)
from app.connectors.sources.microsoft.sharepoint_online.drive_reconciler import (
    DriveReconciler,
)
from app.connectors.sources.microsoft.sharepoint_online.everyone_groups import (
    EVERYONE,
    EVERYONE_EXCEPT_EXTERNAL,
    everyone_group_for_claim,
    everyone_groups,
)
from app.connectors.sources.microsoft.sharepoint_online.graph_permission_check import (
    GRAPH_PERMISSION,
    graph_sites_fullcontrol_missing,
)
from app.connectors.sources.microsoft.sharepoint_online.sync_health import (
    SharePointSyncIncomplete,
    SyncHealth,
)
from app.models.entities import AppUser


class TestSyncHealth:
    def test_a_run_that_skipped_nothing_is_complete(self) -> None:
        health = SyncHealth()

        health.raise_if_incomplete()

        assert health.complete

    def test_a_skipped_part_makes_the_run_fail_and_names_it(self) -> None:
        health = SyncHealth()
        health.skipped("libraries of site HR", "listing failed")

        with pytest.raises(SharePointSyncIncomplete, match="libraries of site HR: listing failed"):
            health.raise_if_incomplete()
        assert not health.complete

    def test_the_summary_names_the_first_parts_and_counts_the_rest(self) -> None:
        health = SyncHealth()
        for n in range(7):
            health.skipped(f"part {n}", "x")

        assert health.summary(limit=2) == "part 0: x; part 1: x; and 5 more"


class TestEveryoneGroups:
    @pytest.mark.parametrize(("login_name", "group"), [
        ("c:0-.f|rolemanager|spo-grid-all-users/0f6e", EVERYONE_EXCEPT_EXTERNAL),
        ("c:0(.s|true", EVERYONE),
        ("i:0#.f|membership|ana@contoso.com", None),
        ("c:0t.c|tenant|0f6e", None),
    ])
    def test_a_claim_names_its_group(self, login_name: str, group: str | None) -> None:
        assert everyone_group_for_claim(login_name) == group

    def test_guests_are_in_everyone_only_and_disabled_accounts_in_neither(self) -> None:
        def user(email: str, *, guest: bool = False, active: bool = True) -> AppUser:
            return AppUser(app_name="SHAREPOINT ONLINE", connector_id="c1", source_user_id=email, email=email,
                           full_name=email, is_active=active, is_guest=guest)

        groups = {g.source_user_group_id: [m.email for m in members] for g, members in everyone_groups(
            "SHAREPOINT ONLINE", "c1",
            [user("ana@contoso.com"), user("gus@partner.example", guest=True), user("old@contoso.com", active=False)],
        )}

        assert groups == {
            EVERYONE: ["ana@contoso.com", "gus@partner.example"],
            EVERYONE_EXCEPT_EXTERNAL: ["ana@contoso.com"],
        }


def _record(record_id: str, external_id: str) -> SimpleNamespace:
    return SimpleNamespace(id=record_id, external_record_id=external_id)


class TestDriveReconciler:
    async def test_only_records_the_source_confirms_gone_are_removed(self) -> None:
        processor = SimpleNamespace(
            get_records_in_record_group=AsyncMock(return_value=[
                _record("r1", "kept"), _record("r2", "gone"), _record("r3", "unlisted-but-there"),
            ]),
            on_record_deleted=AsyncMock(),
        )
        confirmed_gone = {"gone"}

        async def is_gone(external_id: str) -> bool:
            return external_id in confirmed_gone

        removed = await DriveReconciler(processor, "c1", SimpleNamespace(info=lambda *_: None)).remove_unlisted(
            "drive-1", {"kept"}, is_gone
        )

        assert removed == 1
        processor.on_record_deleted.assert_awaited_once_with(record_id="r2")

    async def test_stored_records_are_read_a_page_at_a_time(self) -> None:
        first = [_record(f"r{n:03d}", f"e{n}") for n in range(500)]
        processor = SimpleNamespace(
            get_records_in_record_group=AsyncMock(side_effect=[first, [_record("r999", "late")]]),
            on_record_deleted=AsyncMock(),
        )

        async def is_gone(external_id: str) -> bool:
            return external_id == "late"

        await DriveReconciler(processor, "c1", SimpleNamespace(info=lambda *_: None)).remove_unlisted(
            "drive-1", {f"e{n}" for n in range(500)}, is_gone
        )

        assert processor.get_records_in_record_group.await_args_list[1].kwargs["after_key"] == "r499"
        processor.on_record_deleted.assert_awaited_once_with(record_id="r999")


class TestContainerTracker:
    async def test_remembers_the_ids_it_is_given(self) -> None:
        stored: dict = {}

        async def read(key: str) -> dict:
            return stored.get(key, {})

        async def update(key: str, sync_point_data: dict) -> None:
            stored[key] = sync_point_data

        tracker = ContainerTracker(SimpleNamespace(read_sync_point=read, update_sync_point=update), "k")
        assert await tracker.known() == set()

        await tracker.remember({"b", "a"})

        assert await tracker.known() == {"a", "b"}
        assert stored["k"] == {"ids": ["a", "b"]}


class TestCoverage:
    def test_a_connector_that_skipped_part_of_the_source_reports_an_incomplete_full_sync(self) -> None:
        from app.connectors.sources.microsoft.sharepoint_online.connector import (
            SharePointConnector,
        )

        connector = SharePointConnector.__new__(SharePointConnector)
        connector._health = SyncHealth()
        assert connector.full_sync_coverage() == FullSyncCoverage(complete=True)

        connector._health.skipped("site HR", "boom")

        assert connector.full_sync_coverage() == FullSyncCoverage(complete=False)


def _rest_connector(tokens: list[str]) -> object:
    from app.connectors.sources.microsoft.sharepoint_online.connector import (
        SharePointConnector,
    )

    connector = SharePointConnector.__new__(SharePointConnector)
    connector.rate_limiter = asyncio.Semaphore(1)
    connector._get_sharepoint_access_token = AsyncMock(side_effect=tokens)
    return connector


def _answers(*responses: httpx.Response) -> MagicMock:
    client = MagicMock()
    client.get = AsyncMock(side_effect=list(responses))
    return client


class TestSharePointRestReads:
    URL = "https://contoso.sharepoint.com/sites/hr/_api/web/sitegroups"

    async def test_a_401_is_tried_once_more_with_the_credentials_current_token(self) -> None:
        connector = _rest_connector(["old", "new"])
        client = _answers(httpx.Response(401), httpx.Response(200, json={"d": {}}))

        response = await connector._sharepoint_rest_get(client, self.URL, "contoso.sharepoint.com")

        assert response.status_code == 200
        assert client.get.await_args_list[1].kwargs["headers"]["Authorization"] == "Bearer new"

    async def test_a_second_401_fails_the_read(self) -> None:
        connector = _rest_connector(["old", "new"])
        client = _answers(httpx.Response(401), httpx.Response(401))

        with pytest.raises(RuntimeError, match="answered 401"):
            await connector._sharepoint_rest_get(client, self.URL, "contoso.sharepoint.com")

    async def test_throttling_waits_as_told_then_reads(self, monkeypatch: pytest.MonkeyPatch) -> None:
        slept: list[float] = []

        async def sleep(seconds: float) -> None:
            slept.append(seconds)

        monkeypatch.setattr(asyncio, "sleep", sleep)
        connector = _rest_connector(["t", "t"])
        client = _answers(httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, json={"d": {}}))

        response = await connector._sharepoint_rest_get(client, self.URL, "contoso.sharepoint.com")

        assert response.status_code == 200
        assert slept == [7]

    async def test_a_dropped_connection_is_tried_again(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def sleep(_seconds: float) -> None:
            return None

        monkeypatch.setattr(asyncio, "sleep", sleep)
        connector = _rest_connector(["t", "t"])
        client = MagicMock()
        client.get = AsyncMock(side_effect=[httpx.ConnectError("reset"), httpx.Response(200, json={"d": {}})])

        response = await connector._sharepoint_rest_get(client, self.URL, "contoso.sharepoint.com")

        assert response.status_code == 200


class _GraphError(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.response_status_code = status


def _graph_client(root: object, permissions: object) -> SimpleNamespace:
    def answer(result: object) -> AsyncMock:
        return AsyncMock(side_effect=result) if isinstance(result, Exception) else AsyncMock(return_value=result)

    site = SimpleNamespace(get=answer(root), permissions=SimpleNamespace(get=answer(permissions)))
    return SimpleNamespace(sites=SimpleNamespace(by_site_id=lambda _site_id: site))


class TestGraphPermissionCheck:
    @pytest.mark.parametrize(("root", "permissions", "missing"), [
        ("site", [], False),
        ("site", _GraphError(403), True),
        ("site", _GraphError(429), None),
        ("site", _GraphError(503), None),
        (_GraphError(403), _GraphError(403), None),
    ])
    async def test_missing_only_when_the_site_reads_and_its_permissions_answer_403(
        self, root: object, permissions: object, missing: bool | None
    ) -> None:
        assert await graph_sites_fullcontrol_missing(_graph_client(root, permissions), asyncio.Semaphore(1)) is missing


def _permission_connector(permissions: object) -> object:
    from app.connectors.sources.microsoft.sharepoint_online.connector import (
        SharePointConnector,
    )

    connector = SharePointConnector.__new__(SharePointConnector)
    connector.client = _graph_client("site", permissions)
    connector.rate_limiter = asyncio.Semaphore(1)
    connector.logger = MagicMock()
    connector.created_by = "admin"
    connector.notify = AsyncMock()
    stored: dict = {}

    async def read(key: str) -> dict:
        return stored.get(key, {})

    async def update(key: str, sync_point_data: dict) -> None:
        stored[key] = sync_point_data

    connector.drive_delta_sync_point = SimpleNamespace(read_sync_point=read, update_sync_point=update)
    return connector


class TestMissingGraphPermission:
    async def test_the_connection_test_names_the_missing_graph_permission(self) -> None:
        connector = _permission_connector(_GraphError(403))
        connector.client.sites.get = AsyncMock()

        async def rest_pages(_url: str) -> AsyncIterator[dict]:
            yield {}

        connector._sharepoint_rest_pages = rest_pages
        connector.sharepoint_domain = "https://contoso.sharepoint.com"

        with pytest.raises(ConnectorInitError, match=GRAPH_PERMISSION):
            await connector.test_connection_and_access()

    async def test_the_admin_is_told_once_while_it_stays_missing(self) -> None:
        connector = _permission_connector(_GraphError(403))

        await connector._report_missing_graph_permission()
        await connector._report_missing_graph_permission()

        connector.notify.assert_awaited_once()
        assert "Sites.FullControl.All" in connector.notify.await_args.kwargs["title"]

    async def test_an_unclear_answer_tells_nobody(self) -> None:
        connector = _permission_connector(_GraphError(429))

        await connector._report_missing_graph_permission()

        connector.notify.assert_not_awaited()


class TestTokenFailure:
    async def test_a_credential_that_stops_working_is_reported_once_a_run(self) -> None:
        """Every SharePoint REST read asks for a token; the admin gets one notification, not one per read."""
        connector = _permission_connector([])
        connector.sharepoint_domain = "https://contoso.sharepoint.com"
        connector.credential = SimpleNamespace(get_token=AsyncMock(side_effect=ClientAuthenticationError("expired")))
        connector._token_failure_notified = False

        assert await connector._get_sharepoint_access_token() is None
        assert await connector._get_sharepoint_access_token() is None

        connector.notify.assert_awaited_once()


class TestFilteredOutContainers:
    async def test_an_excluded_container_is_deleted_once_and_again_after_it_is_included_and_excluded_again(
        self,
    ) -> None:
        connector = _permission_connector([])
        connector.connector_id = "c1"
        connector.data_entities_processor = SimpleNamespace(on_record_group_deleted=AsyncMock())
        forgotten: list[str] = []

        async def forget(group_id: str) -> None:
            forgotten.append(group_id)

        await connector._remove_filtered_out("k", {"site-a", "site-b"}, forget)
        await connector._remove_filtered_out("k", {"site-a", "site-b"}, forget)
        await connector._remove_filtered_out("k", {"site-b"}, forget)
        await connector._remove_filtered_out("k", {"site-a", "site-b"}, forget)

        deleted = [c.args[0] for c in connector.data_entities_processor.on_record_group_deleted.await_args_list]
        assert deleted == ["site-a", "site-b", "site-a"]
        assert forgotten == deleted
