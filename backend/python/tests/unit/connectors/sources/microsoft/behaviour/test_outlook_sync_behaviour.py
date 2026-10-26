"""Outlook (team) connector, driven over a fake Microsoft 365 tenant.

The connector, its Outlook and users/groups data sources, the Graph SDK client
with kiota's retry middleware, and the Azure client-secret credential are real.
Every HTTP request is answered by an in-memory stub, and our databases are
in-memory fakes.
"""

from __future__ import annotations

import base64
import json
import logging
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Optional
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from azure.identity.aio import ClientSecretCredential
from fastapi import HTTPException
from ms_graph_fakes import (
    GRAPH,
    TENANT,
    TOKEN_PATH,
    FakeCheckpointStore,
    FakeConfigService,
    FakeRecordsDb,
    MicrosoftCloudStub,
    RecordingNotifications,
    bearer,
    graph_error,
    json_response,
    page,
    route_microsoft_http,
)
from msgraph import GraphServiceClient

from app.config.constants.arangodb import (
    AppStatus,
    CollectionNames,
    PermissionModel,
    ProgressStatus,
)
from app.connectors.core.base.connector.connector_service import (
    BaseConnector,
    ConnectorInitError,
)
from app.connectors.core.base.sync_point.sync_point import (
    generate_record_sync_point_key,
)
from app.connectors.core.base.token_service.startup_service import startup_service
from app.connectors.core.sync.full_sync_coverage import FullSyncSeen
from app.connectors.core.sync.sync_runner import run_sync_task
from app.connectors.sources.microsoft.common.outlook_constants import OutlookSyncConfig
from app.connectors.sources.microsoft.common.outlook_folders import notify_sync_outcome
from app.connectors.sources.microsoft.outlook.connector import OutlookConnector
from app.connectors.sources.microsoft.outlook_individual.connector import (
    OutlookIndividualConnector,
)
from app.models.entities import RecordGroupType, RecordType
from app.models.permission import EntityType, PermissionType
from app.services.notification.types import NotificationType

if TYPE_CHECKING:
    from app.models.entities import Record

CONNECTOR_ID = "outlook-1"
ANA = "ana@contoso.com"
BO = "bo@contoso.com"
INBOX = "inbox-1"
GROUP = "g-eng"
SENT = "2024-05-01T10:00:00Z"


class OutlookRecordsDb(FakeRecordsDb):
    """Adds the lookups and deletes only the Outlook connector makes."""

    def __init__(self) -> None:
        super().__init__()
        self.deleted_external: list[str] = []
        self.deleted_record_groups: list[str] = []
        self.withdrawn: list[str] = []

    async def get_all_app_users(self, connector_id: str, **_: object) -> list[Any]:
        return list(self.app_users)

    async def withdraw_app_user_access(self, connector_id: str, users: list[Any]) -> None:
        self.withdrawn.extend(u.source_user_id for u in users)
        self.app_users = [u for u in self.app_users if u not in users]

    def add_mailbox_user(self, email: str, user_id: str) -> None:
        self.active_users.append(SimpleNamespace(email=email, user_id=user_id, source_user_id=None))

    async def delete_record_by_external_id(self, connector_id: str, external_id: str, user_id: str | None = None) -> None:
        """Like the real processor, a mail's attachments go with it."""
        self.deleted_external.append(external_id)
        self.records.pop(external_id, None)
        for key in [k for k, r in self.records.items() if r.parent_external_record_id == external_id]:
            self.records.pop(key)

    def full_sync_running(self, connector_id: str) -> bool:
        # run_full_sync installs begin_full_sync_run on the instance for the run's length.
        return "begin_full_sync_run" in vars(self)

    async def get_records_in_record_group(
        self, connector_id: str, external_group_id: str, limit: int, after_key: str | None = None, **_: object
    ) -> list[Any]:
        in_group = sorted((r for r in self.records.values() if r.external_record_group_id == external_group_id), key=lambda r: r.id)
        return [r for r in in_group if after_key is None or r.id > after_key][:limit]

    async def get_record_by_conversation_index(self, *_: object) -> Optional[Record]:
        return None

    async def on_record_group_deleted(self, external_group_id: str, connector_id: str, **_: object) -> bool:
        """Like the real processor: the group goes with its subgroups and their records;
        False when the group is not stored (an earlier delete took it)."""
        self.deleted_record_groups.append(external_group_id)
        if external_group_id not in self.record_groups:
            return False
        subtree, pending = set(), [external_group_id]
        while pending:
            current = pending.pop()
            subtree.add(current)
            pending += [k for k, g in self.record_groups.items() if g.parent_external_group_id == current and k not in subtree]
        for group_id in subtree:
            self.record_groups.pop(group_id, None)
        for key in [k for k, r in self.records.items() if r.external_record_group_id in subtree]:
            self.records.pop(key)
        return True


@pytest.fixture(autouse=True)
def fresh_notification_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """The notification backoff is shared by every connector in the process; start each test empty."""
    monkeypatch.setattr(BaseConnector, "_notification_cache", {})


@pytest.fixture
def db() -> OutlookRecordsDb:
    records = OutlookRecordsDb()
    records.add_mailbox_user(ANA, "pipeshub-ana")
    return records


@pytest.fixture
def api(cloud: MicrosoftCloudStub, monkeypatch: pytest.MonkeyPatch) -> MicrosoftCloudStub:
    route_microsoft_http(monkeypatch, cloud, "app.sources.client.microsoft.microsoft")
    cloud.on("GET", "/v1.0/users", page([graph_user("u-ana", ANA), graph_user("u-bo", BO)]))
    cloud.on("GET", "/v1.0/groups", page([]))
    cloud.on("GET", "/v1.0/users/u-ana/mailFolders", page([folder(INBOX, "Inbox")]))
    cloud.on("GET", delta_path(), page([], delta_link=delta_url("D0")))
    return cloud


@pytest.fixture
async def connector(api: MicrosoftCloudStub, db: OutlookRecordsDb, checkpoints: FakeCheckpointStore) -> OutlookConnector:
    config = {"auth": {"tenantId": TENANT, "clientId": "client-1", "clientSecret": "secret-1"}}
    instance = OutlookConnector(
        logging.getLogger("test.outlook"), db, checkpoints, FakeConfigService(CONNECTOR_ID, config),
        CONNECTOR_ID, "team", "creator-1",
    )
    instance._notification_service = RecordingNotifications()
    assert await instance.init() is True
    return instance


def graph_user(user_id: str, email: str) -> dict[str, Any]:
    return {"id": user_id, "displayName": email.split("@")[0].title(), "mail": email, "userPrincipalName": email, "userType": "Member"}


def folder(folder_id: str, name: str) -> dict[str, Any]:
    return {"id": folder_id, "displayName": name, "childFolderCount": 0, "totalItemCount": 1}


def message(message_id: str, subject: str, *, change_key: str = "ck1", attachments: bool = False, to: str = BO) -> dict[str, Any]:
    return {
        "id": message_id, "subject": subject, "changeKey": change_key, "hasAttachments": attachments,
        "createdDateTime": SENT, "lastModifiedDateTime": SENT, "receivedDateTime": SENT,
        "webLink": f"https://outlook.office.com/mail/{message_id}", "conversationId": f"conv-{message_id}",
        "internetMessageId": f"<{message_id}@contoso.com>",
        "from": {"emailAddress": {"address": ANA, "name": "Ana"}},
        "toRecipients": [{"emailAddress": {"address": to, "name": "Bo"}}],
    }


def file_attachment(attachment_id: str, name: str = "plan.pdf") -> dict[str, Any]:
    return {"@odata.type": "#microsoft.graph.fileAttachment", "id": attachment_id, "name": name,
            "contentType": "application/pdf", "size": 10, "lastModifiedDateTime": SENT, "isInline": False}


def removed(message_id: str, reason: str = "deleted") -> dict[str, Any]:
    return {"id": message_id, "@removed": {"reason": reason}}


def delta_path(user: str = "u-ana", folder_id: str = INBOX) -> str:
    return f"/v1.0/users/{user}/mailFolders/{folder_id}/messages/delta()"


def delta_url(token: str, user: str = "u-ana", folder_id: str = INBOX) -> str:
    return f"{GRAPH}/users/{user}/mailFolders/{folder_id}/messages/delta()?$deltatoken={token}"


def serve_delta(api: MicrosoftCloudStub, pages_by_token: dict[Optional[str], Any], **kwargs: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        answer = pages_by_token[api.query(request).get("$deltatoken")]
        return answer if isinstance(answer, httpx.Response) else json_response(answer)

    api.on("GET", delta_path(**kwargs), handler)


async def folder_link(connector: OutlookConnector, user: str = "u-ana", folder_id: str = INBOX) -> Optional[str]:
    key = generate_record_sync_point_key(RecordType.MAIL.value, "folders", f"{user}_{folder_id}")
    return (await connector.email_delta_sync_point.read_sync_point(key)).get("delta_link")


def unified_group(group_id: str = GROUP, name: str = "Eng") -> dict[str, Any]:
    return {"id": group_id, "displayName": name, "mail": f"{name.lower()}@contoso.com", "mailNickname": name.lower(),
            "groupTypes": ["Unified"], "mailEnabled": True, "securityEnabled": False, "createdDateTime": SENT}


def member(user_id: str, email: str) -> dict[str, Any]:
    return {"@odata.type": "#microsoft.graph.user", "id": user_id, "mail": email, "displayName": email.split("@")[0]}


def serve_group(api: MicrosoftCloudStub, members: object, *, threads: object = None) -> None:
    api.on("GET", "/v1.0/groups", page([unified_group(), {**unified_group("g-sec", "Sec"), "groupTypes": []}]))
    api.on("GET", f"/v1.0/groups/{GROUP}/transitiveMembers", members if not isinstance(members, list) else page(members))
    api.on("GET", f"/v1.0/groups/{GROUP}/threads", threads if threads is not None else page([]))
    api.on("GET", f"/v1.0/groups/{GROUP}", unified_group())


def thread(thread_id: str, topic: str) -> dict[str, Any]:
    return {"id": thread_id, "topic": topic, "lastDeliveredDateTime": SENT, "hasAttachments": False}


def post(post_id: str, received: str = SENT) -> dict[str, Any]:
    return {"id": post_id, "receivedDateTime": received, "hasAttachments": False,
            "from": {"emailAddress": {"address": ANA, "name": "Ana"}}}


async def run_full_sync(connector: OutlookConnector, db: OutlookRecordsDb) -> dict[str, set[str]]:
    """Run ``run_sync`` as the sync runner runs a full sync, and return what it tells the
    sweep (``unlink_nodes_missed_by_full_sync``) the sync saw. The sweep unlinks the rest.

    The fake db notes each group, record group and record written, as the real processor does.
    """
    seen = FullSyncSeen()
    tracked = {
        "on_new_user_groups": (CollectionNames.GROUPS.value, lambda g: g.source_user_group_id),
        "on_new_record_groups": (CollectionNames.RECORD_GROUPS.value, lambda g: g.external_group_id),
        "on_new_records": (CollectionNames.RECORDS.value, lambda r: r.external_record_id),
    }

    def tracking(name: str) -> Any:  # noqa: ANN401
        collection, key = tracked[name]
        original = getattr(db, name)

        async def write(items: list[tuple[Any, Any]], **kwargs: object) -> None:
            for item, _ in items:
                seen.add(collection, key(item))
            await original(items, **kwargs)

        return write

    for name in tracked:
        setattr(db, name, tracking(name))
    db.begin_full_sync_run = lambda connector_id: seen
    db.end_full_sync_run = lambda connector_id, run: None
    graph = AsyncMock()
    graph.get_document.return_value = {"id": CONNECTOR_ID, "status": AppStatus.FULL_SYNCING.value}
    graph.unlink_nodes_missed_by_full_sync.return_value = (0, True)
    try:
        await run_sync_task(connector, CONNECTOR_ID, graph, logging.getLogger("test.full_sync"),
                            start_status=AppStatus.FULL_SYNCING.value)
    finally:
        for name in [*tracked, "begin_full_sync_run", "end_full_sync_run"]:
            delattr(db, name)
    graph.unlink_nodes_missed_by_full_sync.assert_awaited_once()
    db.unlink_window = graph.unlink_nodes_missed_by_full_sync.await_args.kwargs
    return graph.unlink_nodes_missed_by_full_sync.await_args.args[1]


def stored_by_type(db: OutlookRecordsDb, record_type: RecordType) -> dict[str, Any]:
    return {k: r for k, r in db.records.items() if r.record_type == record_type}


class TestClientAndTokens:
    async def test_the_sdk_and_credential_are_real_and_graph_calls_carry_the_app_token(self, connector, api) -> None:
        wrapper = connector.external_client.get_client()
        assert type(wrapper.get_ms_graph_service_client()) is GraphServiceClient
        assert type(wrapper.credential) is ClientSecretCredential
        assert not isinstance(sys.modules["msgraph"], MagicMock)

        assert await connector.test_connection_and_access() is True

        (request,) = api.calls("GET", "/v1.0/users")
        assert bearer(request) == "Bearer fake-graph-token-1"
        assert api.query(request)["$top"] == "1"

    async def test_an_expiring_token_is_renewed_before_the_next_request(self, connector, api) -> None:
        api.token_lifetime_s = 0
        await connector.test_connection_and_access()
        await connector.test_connection_and_access()

        tokens = [bearer(r) for r in api.calls("GET", "/v1.0/users")]
        assert len(set(tokens)) == 2, tokens

    async def test_a_revoked_secret_fails_the_run_instead_of_syncing_nothing(self, connector, api, db) -> None:
        api.token_failure = json_response({"error": "invalid_client", "error_description": "AADSTS7000222: expired"}, status=401)

        with pytest.raises(Exception, match="AADSTS7000222"):
            await connector.run_sync()

        assert db.records == {}


async def notes(connector: object) -> list[dict[str, Any]]:
    """The notifications the connector sent; ``notify`` publishes in a background task."""
    for task in list(getattr(connector, "_background_tasks", ())):
        await task
    return connector._notification_service.sent


class TestAuthErrorsAndNotifications:
    async def test_a_wrong_client_secret_fails_the_connection_test_with_what_to_fix(self, connector, api) -> None:
        api.token_failure = json_response(
            {"error": "invalid_client", "error_description": "AADSTS7000215: Invalid client secret provided."}, status=401,
        )

        with pytest.raises(ConnectorInitError, match="client secret is wrong"):
            await connector.test_connection_and_access()

    async def test_the_connection_test_checks_mail_and_groups_and_names_a_missing_permission(self, connector, api) -> None:
        api.on("GET", "/v1.0/groups", graph_error(403, "Authorization_RequestDenied", "Insufficient privileges"))

        with pytest.raises(ConnectorInitError, match=r"Group\.Read\.All.*admin consent"):
            await connector.test_connection_and_access()

        (mail_probe,) = api.calls("GET", "/v1.0/users/u-ana/mailFolders")
        assert api.query(mail_probe)["$top"] == "1"

    async def test_a_revoked_secret_sends_one_sign_in_notification_with_the_instance_name(self, connector, api) -> None:
        connector.connector_instance_name = "Sales mail"
        api.token_failure = json_response({"error": "invalid_client", "error_description": "AADSTS7000222: expired"}, status=401)

        with pytest.raises(Exception, match="AADSTS7000222"):
            await connector.run_sync()

        (note,) = await notes(connector)
        assert note["type"] == NotificationType.CONNECTOR_AUTH_ERROR
        assert note["title"] == "Sales mail: sign-in failed"
        assert "client secret has expired" in note["message"]

    async def test_a_failed_run_is_notified(self, connector, api) -> None:
        api.on("GET", "/v1.0/users/u-ana/mailFolders", graph_error(403, "ErrorAccessDenied"))

        with pytest.raises(Exception, match="All 1 mailboxes failed"):
            await connector.run_sync()

        (note,) = await notes(connector)
        assert (note["type"], note["title"]) == (NotificationType.CONNECTOR_SYNC_ERROR, "Outlook: sync failed")

    async def test_what_a_run_skipped_is_summed_up_in_one_notification(self, connector, api, db) -> None:
        db.add_mailbox_user(BO, "pipeshub-bo")
        api.on("GET", "/v1.0/users/u-ana/mailFolders", graph_error(403, "ErrorAccessDenied"))
        api.on("GET", "/v1.0/users/u-bo/mailFolders", page([folder("bo-inbox", "Inbox"), folder("bo-old", "Old")]))
        serve_delta(api, {None: page([message("b1", "Hi", to=ANA)], delta_link=delta_url("B1", "u-bo", "bo-inbox"))},
                    user="u-bo", folder_id="bo-inbox")
        api.on("GET", delta_path("u-bo", "bo-old"), graph_error(500, "ErrorInternalServerError"))

        await connector.run_sync()

        (note,) = await notes(connector)
        assert note["message"].startswith("Skipped after errors: mailboxes 1, folders 1.")
        assert "b1" in db.records

    async def test_a_failed_groups_read_is_a_skip_not_a_failed_run(self, connector, api, db) -> None:
        """A failed full sync is owed again, which clears every saved delta link first."""
        serve_delta(api, {None: page([message("m1", "Plan")], delta_link=delta_url("D1"))})
        api.on("GET", "/v1.0/groups", graph_error(403, "Authorization_RequestDenied"))

        await connector.run_sync()

        (note,) = await notes(connector)
        assert note["message"].startswith("Skipped after errors: groups 1.")
        assert "m1" in db.records
        assert connector.full_sync_coverage().complete is False

    async def test_a_preview_reads_the_mailbox_from_the_folder_tree_and_maps_graph_errors(self, connector, api, db) -> None:
        serve_delta(api, {None: page([message("m1", "Plan")], delta_link=delta_url("D1"))})
        await connector.run_sync()
        connector._user_cache, connector._user_cache_timestamp = {}, None
        user_listings = len(api.calls("GET", "/v1.0/users"))
        mail = "/v1.0/users/u-ana/messages/m1"

        api.on("GET", mail, graph_error(404, "ErrorItemNotFound"))
        with pytest.raises(HTTPException) as gone:
            await connector.stream_record(db.records["m1"])
        api.on("GET", mail, graph_error(401, "InvalidAuthenticationToken"))
        with pytest.raises(HTTPException) as expired:
            await connector.stream_record(db.records["m1"])

        assert gone.value.status_code == 404
        assert expired.value.status_code == 409
        assert "Reconnect" in expired.value.detail
        assert len(api.calls("GET", "/v1.0/users")) == user_listings, "the owner comes from the mailbox id"


class TestMailDelta:
    async def test_first_sync_reads_every_page_saves_mail_with_recipients_and_keeps_the_delta_link(
        self, connector, api, db, checkpoints
    ) -> None:
        serve_delta(api, {
            None: page([message("m1", "Plan")], next_link=delta_url("p2")),
            "p2": page([message("m2", "Budget")], delta_link=delta_url("D1")),
        })

        await connector.run_sync()

        mails = stored_by_type(db, RecordType.MAIL)
        assert {m.record_name for m in mails.values()} == {"Plan", "Budget"}
        assert await folder_link(connector) == delta_url("D1")
        assert db.record_permissions["m1"] == []
        assert db.records["m1"].inherit_permissions is True
        mailbox_id = "mailbox:u-ana"
        assert db.record_groups[mailbox_id].permission_model == PermissionModel.RECORD_GROUP_LEVEL
        assert db.record_groups[mailbox_id].inherit_permissions is False
        assert [(p.email, p.type) for p in db.record_group_permissions[mailbox_id]] == [(ANA, PermissionType.OWNER)]
        assert db.record_groups[INBOX].parent_external_group_id == mailbox_id
        assert db.record_groups[INBOX].permission_model == PermissionModel.RECORD_GROUP_LEVEL
        assert db.record_groups[INBOX].inherit_permissions is True
        assert db.record_group_permissions[INBOX] == []
        assert api.calls("GET", "/v1.0/users/u-bo/mailFolders") == [], "only users active in PipesHub are synced"

    async def test_next_sync_uses_the_saved_link_and_applies_deletes_edits_and_moves(
        self, connector, api, db, checkpoints
    ) -> None:
        api.on("GET", "/v1.0/users/u-ana/mailFolders", page([folder(INBOX, "Inbox"), folder("archive", "Archive")]))
        serve_delta(api, {
            None: page([message("m1", "Plan"), message("m2", "Old")], delta_link=delta_url("D1")),
            "D1": page([removed("m2"), message("m1", "Plan v2", change_key="ck2")], delta_link=delta_url("D2")),
        })
        serve_delta(api, {
            None: page([message("m4", "Archived")], delta_link=delta_url("A1", folder_id="archive")),
            "A1": page([message("m3", "Moved")], delta_link=delta_url("A2", folder_id="archive")),
        }, folder_id="archive")
        await connector.run_sync()
        db.records["m3"] = db.records["m1"].model_copy(update={"external_record_id": "m3", "id": "rec-m3", "external_record_group_id": INBOX})

        await connector.run_sync()

        assert db.deleted_external == ["m2"]
        assert db.records["m1"].record_name == "Plan v2"
        assert db.records["m1"].version == 1
        assert db.records["m3"].external_record_group_id == "archive"
        assert db.records["m3"].id == "rec-m3", "a moved mail keeps its record"
        assert await folder_link(connector) == delta_url("D2")

    async def test_a_mail_removed_with_reason_changed_is_deleted_not_saved_empty(self, connector, api, db) -> None:
        serve_delta(api, {
            None: page([message("m1", "Plan")], delta_link=delta_url("D1")),
            "D1": page([removed("m1", "changed")], delta_link=delta_url("D2")),
        })
        await connector.run_sync()

        await connector.run_sync()

        assert "m1" not in db.records
        assert db.deleted_external == ["m1"]

    async def test_a_failed_delta_page_keeps_the_folder_checkpoint(self, connector, api, db, checkpoints) -> None:
        serve_delta(api, {
            None: page([message("m1", "Plan")], delta_link=delta_url("D1")),
            "D1": page([message("m2", "New")], next_link=delta_url("p2")),
            "p2": graph_error(500, "ErrorInternalServerError"),
        })
        await connector.run_sync()

        with pytest.raises(Exception, match="All 1 mailboxes failed"):
            await connector.run_sync()

        assert await folder_link(connector) == delta_url("D1"), "the window is read again next run"
        assert "m2" in db.records, "each page is written before the next is fetched"

    async def test_each_delta_page_is_saved_before_the_next_page_is_read(self, connector, api, db) -> None:
        first_page_saved_before_second_read = []

        def pages(request: httpx.Request) -> httpx.Response:
            if api.query(request).get("$deltatoken") != "p2":
                return json_response(page([message("m1", "Plan")], next_link=delta_url("p2")))
            first_page_saved_before_second_read.append("m1" in db.records)
            return json_response(page([message("m2", "New")], delta_link=delta_url("D1")))

        api.on("GET", delta_path(), pages)

        await connector.run_sync()

        assert first_page_saved_before_second_read == [True]
        assert {"m1", "m2"} <= set(db.records)
        assert await folder_link(connector) == delta_url("D1")

    async def test_a_throttled_page_is_retried_after_the_retry_after_delay(self, connector, api, db, backoff_sleeps) -> None:
        serve_delta(api, {None: page([message("m1", "Plan")], delta_link=delta_url("D1"))})
        answer = api._routes[0][2]
        api.on("GET", delta_path(), [graph_error(429, "ApplicationThrottled", headers={"Retry-After": "5"}), answer])

        await connector.run_sync()

        assert 5 in backoff_sleeps
        assert "m1" in db.records

    async def test_one_mailbox_that_cannot_be_read_does_not_stop_the_others(self, connector, api, db) -> None:
        db.add_mailbox_user(BO, "pipeshub-bo")
        api.on("GET", "/v1.0/users/u-ana/mailFolders", graph_error(403, "ErrorAccessDenied"))
        api.on("GET", "/v1.0/users/u-bo/mailFolders", page([folder("bo-inbox", "Inbox")]))
        serve_delta(api, {None: page([message("b1", "Hi", to=ANA)], delta_link=delta_url("B1", "u-bo", "bo-inbox"))},
                    user="u-bo", folder_id="bo-inbox")

        await connector.run_sync()

        assert set(stored_by_type(db, RecordType.MAIL)) == {"b1"}
        assert connector.full_sync_coverage().complete is False, "ana's stored mail must not be unlinked"

    @pytest.mark.parametrize("code", ["MailboxNotEnabledForRESTAPI", "ResourceNotFound"])
    async def test_a_user_without_a_mailbox_is_an_empty_mailbox_not_a_failure(self, connector, api, db, code) -> None:
        db.add_mailbox_user(BO, "pipeshub-bo")
        api.on("GET", "/v1.0/users/u-bo/mailFolders", graph_error(404, code))
        serve_delta(api, {None: page([message("m1", "Plan")], delta_link=delta_url("D1"))})

        await connector.run_sync()

        assert set(stored_by_type(db, RecordType.MAIL)) == {"m1"}
        assert connector.full_sync_coverage().complete is True
        assert await notes(connector) == []

    async def test_a_mailbox_that_answers_no_mailbox_once_keeps_its_stored_mail(self, connector, api, db) -> None:
        serve_delta(api, {None: page([message("m1", "Plan")], delta_link=delta_url("D1"))})
        await connector.run_sync()

        api.on("GET", "/v1.0/users/u-ana/mailFolders", graph_error(404, "MailboxNotEnabledForRESTAPI"))
        await connector.run_sync()

        assert "m1" in db.records
        assert db.deleted_record_groups == []

    async def test_any_other_404_on_the_folder_listing_is_still_a_failed_mailbox(self, connector, api, db) -> None:
        db.add_mailbox_user(BO, "pipeshub-bo")
        api.on("GET", "/v1.0/users/u-bo/mailFolders", graph_error(404, "ErrorItemNotFound"))
        serve_delta(api, {None: page([message("m1", "Plan")], delta_link=delta_url("D1"))})

        await connector.run_sync()

        assert connector.full_sync_coverage().complete is False
        assert connector._skipped["mailboxes"] == 1

    async def test_a_run_where_every_mailbox_failed_fails(self, connector, api, db) -> None:
        api.on("GET", "/v1.0/users/u-ana/mailFolders", graph_error(403, "ErrorAccessDenied"))

        with pytest.raises(Exception, match="All 1 mailboxes failed"):
            await connector.run_sync()

    async def test_a_clean_full_sync_reports_full_coverage(self, connector, api) -> None:
        serve_delta(api, {None: page([message("m1", "Plan")], delta_link=delta_url("D1"))})

        await connector.run_sync()

        assert connector.full_sync_coverage().complete is True

    async def test_an_expired_delta_link_reads_the_folder_again_in_full(self, connector, api, db) -> None:
        serve_delta(api, {
            None: page([message("m1", "Plan")], delta_link=delta_url("D1")),
            "D1": graph_error(410, "syncStateNotFound", "The sync state is no longer valid"),
        })
        await connector.run_sync()
        serve_delta(api, {
            None: page([message("m1", "Plan"), message("m2", "New")], delta_link=delta_url("F1")),
            "D1": graph_error(410, "syncStateNotFound", "The sync state is no longer valid"),
        })

        await connector.run_sync()

        assert {"m1", "m2"} <= set(stored_by_type(db, RecordType.MAIL))
        assert await folder_link(connector) == delta_url("F1")
        assert connector.full_sync_coverage().complete is True

    async def test_another_delta_error_keeps_the_link_instead_of_reading_as_empty(self, connector, api, db) -> None:
        api.on("GET", "/v1.0/users/u-ana/mailFolders", page([folder(INBOX, "Inbox"), folder("archive", "Archive")]))
        serve_delta(api, {None: page([message("m1", "Plan")], delta_link=delta_url("D1")), "D1": page([], delta_link=delta_url("D2"))})
        serve_delta(api, {
            None: page([message("m4", "Archived")], delta_link=delta_url("A1", folder_id="archive")),
            "A1": graph_error(403, "ErrorAccessDenied"),
        }, folder_id="archive")
        await connector.run_sync()

        await connector.run_sync()

        assert await folder_link(connector, folder_id="archive") == delta_url("A1", folder_id="archive")
        assert len(api.calls("GET", delta_path(folder_id="archive"))) == 2, "no full re-read"
        assert connector.full_sync_coverage().complete is False

    async def test_attachments_are_saved_under_their_mail_with_the_mails_access(self, connector, api, db) -> None:
        serve_delta(api, {None: page([message("m1", "Plan", attachments=True)], delta_link=delta_url("D1"))})
        api.on("GET", "/v1.0/users/u-ana/messages/m1/attachments", page([{
            "@odata.type": "#microsoft.graph.fileAttachment", "id": "a1", "name": "plan.pdf",
            "contentType": "application/pdf", "size": 10, "lastModifiedDateTime": SENT, "isInline": False,
        }]))

        await connector.run_sync()

        files = [r for r in db.records.values() if r.record_type == RecordType.FILE]
        assert [f.record_name for f in files] == ["plan.pdf"]
        assert files[0].inherit_permissions is True
        assert files[0].parent_external_record_id == "m1"
        assert db.record_permissions[files[0].external_record_id] == []

    async def test_a_change_that_fails_to_apply_is_retried_on_the_next_run(self, connector, api, db, monkeypatch) -> None:
        serve_delta(api, {
            None: page([message("m1", "Plan")], delta_link=delta_url("D1")),
            "D1": page([removed("m1")], delta_link=delta_url("D2")),
            "D2": page([], delta_link=delta_url("D3")),
        })
        await connector.run_sync()
        real_delete = db.delete_record_by_external_id

        async def failing_delete(*args: object) -> None:
            raise RuntimeError("database unavailable")

        monkeypatch.setattr(db, "delete_record_by_external_id", failing_delete)
        await connector.run_sync()
        monkeypatch.setattr(db, "delete_record_by_external_id", real_delete)

        await connector.run_sync()

        assert "m1" not in db.records

    async def test_a_mail_that_fails_every_run_stops_holding_its_folder_back_after_three_runs(
        self, connector, api, db, checkpoints,
    ) -> None:
        serve_delta(api, {
            None: page([message("m1", "Plan"), message("m2", "Broken", attachments=True)], delta_link=delta_url("D1")),
            "D1": page([], delta_link=delta_url("D2")),
        })
        api.on("GET", "/v1.0/users/u-ana/messages/m2/attachments", graph_error(500, "ErrorInternalServerError"))

        for _ in range(OutlookSyncConfig.MAX_HELD_BACK_RUNS):
            await connector.run_sync()
            assert await folder_link(connector) is None, "the link is held back for the failed mail"
        assert checkpoints.values_for(f"u-ana_{INBOX}")["held_back_runs"] == OutlookSyncConfig.MAX_HELD_BACK_RUNS
        connector._notification_service.sent.clear()

        await connector.run_sync()

        assert await folder_link(connector) == delta_url("D1")
        assert checkpoints.values_for(f"u-ana_{INBOX}")["held_back_runs"] == 0
        (note,) = await notes(connector)
        assert note["message"].startswith("Skipped after errors: mails 1.")
        await connector.run_sync()
        assert api.query(api.calls("GET", delta_path())[-1]).get("$deltatoken") == "D1"

    async def test_attachments_that_failed_to_list_are_picked_up_later(self, connector, api, db) -> None:
        serve_delta(api, {
            None: page([message("m1", "Plan", attachments=True)], delta_link=delta_url("D1")),
            "D1": page([], delta_link=delta_url("D2")),
        })
        attachments = "/v1.0/users/u-ana/messages/m1/attachments"
        api.on("GET", attachments, graph_error(500, "ErrorInternalServerError"))
        await connector.run_sync()
        api.on("GET", attachments, page([{"@odata.type": "#microsoft.graph.fileAttachment", "id": "a1", "name": "plan.pdf",
                                          "contentType": "application/pdf", "size": 10, "isInline": False}]))

        await connector.run_sync()

        assert any(r.record_name == "plan.pdf" for r in db.records.values())


    async def test_a_read_or_flag_change_does_not_reindex_the_mail_or_its_attachments(self, connector, api, db) -> None:
        serve_delta(api, {
            None: page([message("m1", "Plan", attachments=True)], delta_link=delta_url("D1")),
            "D1": page([{**message("m1", "Plan", change_key="ck2", attachments=True), "isRead": True}],
                       delta_link=delta_url("D2")),
        })
        api.on("GET", "/v1.0/users/u-ana/messages/m1/attachments", page([file_attachment("a1")]))
        await connector.run_sync()
        revision = db.records["m1"].external_revision_id

        await connector.run_sync()

        assert db.records["m1"].external_revision_id == revision
        assert db.records["m1"].version == 0
        assert db.records["a1"].version == 0

    async def test_every_page_of_a_mails_attachments_is_read(self, connector, api, db) -> None:
        serve_delta(api, {None: page([message("m1", "Plan", attachments=True)], delta_link=delta_url("D1"))})

        def attachments(request: httpx.Request) -> httpx.Response:
            if api.query(request).get("$skiptoken") == "p2":
                return json_response(page([file_attachment("a2", "notes.pdf")]))
            return json_response(page([file_attachment("a1")], next_link=f"{GRAPH}/users/u-ana/messages/m1/attachments?$skiptoken=p2"))

        api.on("GET", "/v1.0/users/u-ana/messages/m1/attachments", attachments)

        await connector.run_sync()

        assert {r.record_name for r in stored_by_type(db, RecordType.FILE).values()} == {"plan.pdf", "notes.pdf"}

    async def test_a_repeated_attachments_next_link_fails_the_mail_instead_of_looping(self, connector, api, db) -> None:
        serve_delta(api, {None: page([message("m1", "Plan", attachments=True)], delta_link=delta_url("D1"))})
        attachments = "/v1.0/users/u-ana/messages/m1/attachments"
        api.on("GET", attachments, page([file_attachment("a1")], next_link=f"{GRAPH}{attachments[5:]}?$skiptoken=same"))

        await connector.run_sync()

        assert len(api.calls("GET", attachments)) == 2, "the repeated link is followed once, then refused"
        assert connector.full_sync_coverage().complete is False

    async def test_the_attachment_listing_selects_metadata_so_graph_sends_no_file_content(self, connector, api, db) -> None:
        serve_delta(api, {None: page([message("m1", "Plan", attachments=True)], delta_link=delta_url("D1"))})
        api.on("GET", "/v1.0/users/u-ana/messages/m1/attachments", page([file_attachment("a1")]))

        await connector.run_sync()

        [listing] = api.calls("GET", "/v1.0/users/u-ana/messages/m1/attachments")
        assert api.query(listing)["$select"] == "id,name,contentType,size,lastModifiedDateTime,isInline"

    async def test_an_attachment_streams_its_raw_content_in_chunks(self, connector, api, db, monkeypatch) -> None:
        """Plain text whose characters happen to be valid base64 must arrive byte for byte."""
        monkeypatch.setattr(OutlookSyncConfig, "ATTACHMENT_CHUNK_SIZE", 4)
        serve_delta(api, {None: page([message("m1", "Plan", attachments=True)], delta_link=delta_url("D1"))})
        api.on("GET", "/v1.0/users/u-ana/messages/m1/attachments", page([file_attachment("a1", "people.csv")]))
        await connector.run_sync()
        csv = b"name,age\nbob,42\n"
        value = "/v1.0/users/u-ana/messages/m1/attachments/a1/$value"
        api.on("GET", value, httpx.Response(200, content=csv))

        response = await connector.stream_record(db.records["a1"])
        chunks = [chunk async for chunk in response.body_iterator]

        assert b"".join(chunks) == csv
        assert len(chunks) > 1
        assert api.calls("GET", "/v1.0/users/u-ana/messages/m1/attachments/a1") == [], "no JSON contentBytes read"
        api.on("GET", value, graph_error(404, "ErrorItemNotFound"))
        with pytest.raises(HTTPException) as gone:
            await connector.stream_record(db.records["a1"])
        assert gone.value.status_code == 404

    async def test_attached_mails_get_no_record_and_a_huge_file_is_saved_unindexed(self, connector, api, db) -> None:
        serve_delta(api, {None: page([message("m1", "Plan", attachments=True)], delta_link=delta_url("D1"))})
        api.on("GET", "/v1.0/users/u-ana/messages/m1/attachments", page([
            file_attachment("a1"),
            {**file_attachment("big", "scan.pdf"), "size": OutlookSyncConfig.MAX_INDEXED_ATTACHMENT_BYTES + 1},
            {"@odata.type": "#microsoft.graph.itemAttachment", "id": "fwd", "name": "Fwd: Plan",
             "contentType": "message/rfc822", "size": 2000, "lastModifiedDateTime": SENT},
        ]))

        await connector.run_sync()

        assert "fwd" not in db.records
        assert db.records["big"].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value
        assert db.records["a1"].indexing_status != ProgressStatus.AUTO_INDEX_OFF.value


class TestGroups:
    async def test_mail_enabled_microsoft_365_groups_are_saved_with_every_member_page(self, connector, api, db) -> None:
        def members(request: httpx.Request) -> httpx.Response:
            if api.query(request).get("$skiptoken") == "2":
                return json_response(page([member("u-bo", BO)]))
            return json_response(page([member("u-ana", ANA)], next_link=f"{GRAPH}/groups/{GROUP}/transitiveMembers?$skiptoken=2"))

        serve_group(api, members)

        await connector.run_sync()

        assert db.user_groups == {GROUP: [ANA, BO]}
        assert db.record_groups[GROUP].group_type == RecordGroupType.GROUP_MAILBOX
        (grant,) = db.record_group_permissions[GROUP]
        assert (grant.entity_type, grant.external_id, grant.type) == (EntityType.GROUP, GROUP, PermissionType.READ)

    async def test_nested_groups_and_devices_among_the_members_are_passed_over(self, connector, api, db) -> None:
        serve_group(api, [
            member("u-ana", ANA),
            {"@odata.type": "#microsoft.graph.group", "id": "g-nested", "displayName": "Nested", "mail": None},
            {"@odata.type": "#microsoft.graph.device", "id": "d1", "displayName": "Laptop"},
        ])

        await connector.run_sync()

        assert db.user_groups == {GROUP: [ANA]}
        assert connector._skipped["groups"] == 0

    async def test_group_conversation_posts_are_saved_as_group_mail(self, connector, api, db) -> None:
        serve_group(api, [member("u-ana", ANA)], threads=page([thread("t1", "Launch")]))
        api.on("GET", f"/v1.0/groups/{GROUP}/threads/t1/posts", page([post("p1")]))

        await connector.run_sync()

        (record,) = stored_by_type(db, RecordType.GROUP_MAIL).values()
        assert (record.external_record_id, record.record_name, record.external_record_group_id) == ("p1", "Launch", GROUP)
        assert record.inherit_permissions is True
        assert db.record_permissions["p1"] == []

    async def test_a_post_and_its_attachments_link_to_the_groups_mailbox(self, connector, api, db) -> None:
        serve_group(api, [member("u-ana", ANA)], threads=page([thread("t1", "Launch")]))
        api.on("GET", f"/v1.0/groups/{GROUP}/threads/t1/posts", page([{**post("p1"), "hasAttachments": True}]))
        api.on("GET", f"/v1.0/groups/{GROUP}/threads/t1/posts/p1", {**post("p1"), "attachments": [
            file_attachment("pa1"),
            {"@odata.type": "#microsoft.graph.referenceAttachment", "id": "link1", "name": "Spec", "size": 10},
        ]})

        await connector.run_sync()

        mailbox = "https://outlook.office365.com/groups/contoso.com/eng/mail"
        assert db.records["p1"].weburl == mailbox
        assert db.records["pa1"].weburl == mailbox
        assert "link1" not in db.records

    async def test_a_failed_member_read_keeps_the_stored_members(self, connector, api, db) -> None:
        serve_group(api, graph_error(500, "ErrorInternalServerError"))
        db.user_groups[GROUP] = [ANA]

        await connector.run_sync()

        assert db.user_groups[GROUP] == [ANA]

    async def test_a_failed_later_member_page_keeps_the_stored_members(self, connector, api, db) -> None:
        def members(request: httpx.Request) -> httpx.Response:
            if api.query(request).get("$skiptoken") == "2":
                return graph_error(500, "ErrorInternalServerError")
            return json_response(page([member("u-ana", ANA)], next_link=f"{GRAPH}/groups/{GROUP}/transitiveMembers?$skiptoken=2"))

        serve_group(api, members)
        db.user_groups[GROUP] = [ANA, BO]

        await connector.run_sync()

        assert db.user_groups[GROUP] == [ANA, BO]

    async def test_a_failed_user_list_refresh_does_not_empty_every_group(self, connector, api, db) -> None:
        serve_group(api, [member("u-ana", ANA)])
        api.on("GET", "/v1.0/users", [page([graph_user("u-ana", ANA)]), graph_error(500, "ErrorInternalServerError")])
        db.user_groups[GROUP] = [ANA]

        await connector.run_sync()

        assert db.user_groups[GROUP] == [ANA]
        assert len(api.calls("GET", "/v1.0/users")) == 1, "the user list is read once per run"

    async def test_a_failed_later_user_page_fails_the_run_and_keeps_the_stored_members(self, connector, api, db) -> None:
        def users(request: httpx.Request) -> httpx.Response:
            if api.query(request).get("$skiptoken") == "2":
                return graph_error(500, "ErrorInternalServerError")
            return json_response(page([graph_user("u-ana", ANA)], next_link=f"{GRAPH}/users?$skiptoken=2"))

        serve_group(api, [member("u-ana", ANA), member("u-bo", BO)])
        api.on("GET", "/v1.0/users", users)
        db.user_groups[GROUP] = [ANA, BO]

        with pytest.raises(Exception, match="page 2"):
            await connector.run_sync()

        assert db.user_groups[GROUP] == [ANA, BO]

    async def test_a_group_deleted_in_microsoft_365_is_removed(self, connector, api, db) -> None:
        serve_group(api, [member("u-ana", ANA)])
        await connector.run_sync()
        api.on("GET", "/v1.0/groups", page([]))

        await connector.run_sync()

        assert GROUP in db.deleted_groups

    async def test_a_group_narrowed_out_of_the_groups_filter_is_unlinked_by_the_full_sync_the_change_forces(
        self, connector, api, db, checkpoints,
    ) -> None:
        serve_group(api, [member("u-ana", ANA)], threads=page([thread("t1", "Launch")]))
        api.on("GET", f"/v1.0/groups/{GROUP}/threads/t1/posts", page([post("p1")]))
        await connector.run_sync()
        assert "p1" in db.records

        # Saving new sync filters marks the connector pendingFullSync, and that full sync
        # starts by deleting the connector's sync points.
        connector.config_service.config["filters"] = {
            "sync": {"values": {"groups": {"operator": "not_in", "value": ["eng@contoso.com"], "type": "multiselect"}}},
        }
        checkpoints.sync_points.clear()
        seen = await run_full_sync(connector, db)

        assert GROUP not in seen[CollectionNames.GROUPS.value], "its members' grant is unlinked"
        assert GROUP not in seen[CollectionNames.RECORD_GROUPS.value]
        assert "p1" not in seen[CollectionNames.RECORDS.value]
        assert "mailbox:u-ana" in seen[CollectionNames.RECORD_GROUPS.value]
        assert connector.full_sync_coverage().complete is True


def set_sync_filters(connector: OutlookConnector, **values: dict[str, Any]) -> None:
    connector.config_service.config["filters"] = {"sync": {"values": values}}


class TestFilters:
    async def test_standard_folders_match_by_well_known_name_in_a_localized_mailbox(self, connector, api, db) -> None:
        api.on("GET", "/v1.0/users/u-ana/mailFolders", page([
            {**folder(INBOX, "Posteingang"), "childFolderCount": 1},
            folder("arch", "Archiv"),
            folder("proj", "Projekte"),
        ]))
        api.on("GET", "/v1.0/users/u-ana/mailFolders/inbox", folder(INBOX, "Posteingang"))
        api.on("GET", "/v1.0/users/u-ana/mailFolders/archive", graph_error(404, "ErrorItemNotFound"))
        # Read once, on the first inventory, for subfolders an earlier version synced.
        api.on("GET", f"/v1.0/users/u-ana/mailFolders/{INBOX}/childFolders", page([{**folder("sub", "Q3"), "parentFolderId": INBOX}]))
        set_sync_filters(
            connector,
            folders={"operator": "in", "value": ["Inbox", "Archive"], "type": "multiselect"},
            custom_folders={"operator": "is", "value": False, "type": "boolean"},
        )

        await connector.run_sync()

        assert set(db.record_groups) == {"mailbox:u-ana", INBOX}, "Archiv is not the mailbox's archive folder"
        assert api.calls("GET", delta_path(folder_id="sub")) == [], \
            "a subfolder of Inbox is a custom folder, and Custom Folders is off"

    async def test_standard_folders_not_in_drops_the_selected_folder_and_keeps_custom_ones(self, connector, api, db) -> None:
        api.on("GET", "/v1.0/users/u-ana/mailFolders", page([folder(INBOX, "Inbox"), folder("proj", "Projects")]))
        api.on("GET", "/v1.0/users/u-ana/mailFolders/inbox", folder(INBOX, "Inbox"))
        api.on("GET", delta_path(folder_id="proj"), page([], delta_link=delta_url("P1", folder_id="proj")))
        set_sync_filters(connector, folders={"operator": "not_in", "value": ["Inbox"], "type": "multiselect"})

        await connector.run_sync()

        assert set(db.record_groups) == {"mailbox:u-ana", "proj"}
        assert api.calls("GET", delta_path()) == []

    async def test_users_not_in_skips_the_selected_mailbox(self, connector, api, db) -> None:
        db.add_mailbox_user(BO, "pipeshub-bo")
        api.on("GET", "/v1.0/users/u-bo/mailFolders", page([]))
        set_sync_filters(connector, users={"operator": "not_in", "value": [ANA.upper()], "type": "multiselect"})

        await connector.run_sync()

        assert api.calls("GET", "/v1.0/users/u-ana/mailFolders") == []
        assert len(api.calls("GET", "/v1.0/users/u-bo/mailFolders")) == 1


TRASH = "trash-1"


def serve_deleted_items(api: MicrosoftCloudStub, user: str = "u-ana", child_count: int = 0) -> None:
    trash = {**folder(TRASH, "Gelöschte Elemente"), "childFolderCount": child_count}
    api.on("GET", f"/v1.0/users/{user}/mailFolders/deleteditems", trash)
    return trash


def id_type_marker(checkpoints: FakeCheckpointStore, folder_id: str = INBOX) -> str | None:
    """The id type the folder's delta link was saved with."""
    return next((v.get("id_type") for k, v in checkpoints.sync_points.items()
                 if "/folders/" in k and k.endswith(folder_id)), None)


def forget_id_type_marker(checkpoints: FakeCheckpointStore) -> None:
    """The state an installation from before immutable ids is in: links saved without an id type."""
    for value in checkpoints.sync_points.values():
        value.pop("id_type", None)


class TestDeletionsAndIds:
    async def test_deleted_items_is_not_synced_by_default_and_can_be_selected(self, connector, api, db) -> None:
        trash = serve_deleted_items(api)
        api.on("GET", "/v1.0/users/u-ana/mailFolders", page([folder(INBOX, "Inbox"), trash]))
        api.on("GET", "/v1.0/users/u-ana/mailFolders/inbox", folder(INBOX, "Inbox"))
        api.on("GET", delta_path(folder_id=TRASH), page([message("t1", "Old")], delta_link=delta_url("T1", folder_id=TRASH)))

        await connector.run_sync()
        assert TRASH not in db.record_groups
        assert api.calls("GET", delta_path(folder_id=TRASH)) == []

        set_sync_filters(connector, folders={"operator": "in", "value": ["Inbox", "Deleted Items"], "type": "multiselect"})
        await connector.run_sync()
        assert TRASH in db.record_groups
        assert "t1" in db.records

    async def test_a_mail_moved_into_deleted_items_is_gone_from_search(self, connector, api, db) -> None:
        trash = serve_deleted_items(api)
        api.on("GET", "/v1.0/users/u-ana/mailFolders", page([folder(INBOX, "Inbox"), trash]))
        serve_delta(api, {
            None: page([message("m1", "Plan", attachments=True)], delta_link=delta_url("D1")),
            "D1": page([removed("m1")], delta_link=delta_url("D2")),
        })
        api.on("GET", "/v1.0/users/u-ana/messages/m1/attachments", page([file_attachment("a1")]))
        await connector.run_sync()
        assert {"m1", "a1"} <= set(db.records)

        await connector.run_sync()

        assert db.deleted_external == ["m1"]
        assert "m1" not in db.records
        assert "a1" not in db.records
        assert await folder_link(connector) == delta_url("D2")

    async def test_a_folder_deleted_in_outlook_is_removed_with_its_subfolders(self, connector, api, db) -> None:
        api.on("GET", "/v1.0/users/u-ana/mailFolders", page([folder(INBOX, "Inbox"), {**folder("proj", "Projects"), "childFolderCount": 1}]))
        api.on("GET", "/v1.0/users/u-ana/mailFolders/proj/childFolders", page([{**folder("sub", "Q3"), "parentFolderId": "proj"}]))
        api.on("GET", delta_path(folder_id="proj"), page([message("p1", "Spec")], delta_link=delta_url("P1", folder_id="proj")))
        api.on("GET", delta_path(folder_id="sub"), page([message("s1", "Notes")], delta_link=delta_url("S1", folder_id="sub")))
        await connector.run_sync()
        assert {"proj", "sub"} <= set(db.record_groups)

        # Deleting Projects in Outlook moves it, id and subtree unchanged, under Deleted Items.
        trash = serve_deleted_items(api, child_count=1)
        api.on("GET", "/v1.0/users/u-ana/mailFolders", page([folder(INBOX, "Inbox"), trash]))

        await connector.run_sync()

        assert set(db.deleted_record_groups) == {"proj", "sub"}
        assert not {"p1", "s1"} & set(db.records)
        assert api.calls("GET", f"/v1.0/users/u-ana/mailFolders/{TRASH}/childFolders") == []

    async def test_mailbox_calls_ask_graph_for_immutable_ids_and_group_calls_do_not(self, connector, api, db) -> None:
        serve_group(api, [member("u-ana", ANA)], threads=page([thread("t1", "Launch")]))
        api.on("GET", f"/v1.0/groups/{GROUP}/threads/t1/posts", page([post("p1")]))
        serve_delta(api, {
            None: page([message("m1", "Plan")], next_link=delta_url("p2")),
            "p2": page([message("m2", "Budget")], delta_link=delta_url("D1")),
        })

        await connector.run_sync()

        mailbox = [r for r in api.graph_calls() if "/users/u-ana/" in api.path_of(r)]
        assert mailbox and all('IdType="ImmutableId"' in r.headers.get("Prefer", "") for r in mailbox)
        first_delta, next_page = api.calls("GET", delta_path())
        assert first_delta.headers["Prefer"] == 'odata.maxpagesize=100, IdType="ImmutableId"'
        assert next_page.headers["Prefer"] == 'IdType="ImmutableId"'
        group = [r for r in api.graph_calls() if "/groups" in api.path_of(r)]
        assert group and all("IdType" not in r.headers.get("Prefer", "") for r in group)

    @pytest.mark.parametrize("destination_first", [False, True])
    async def test_a_mail_moved_between_synced_folders_keeps_its_record_and_is_not_reindexed(
        self, connector, api, db, destination_first,
    ) -> None:
        folders = [folder(INBOX, "Inbox"), folder("archive", "Archive")]
        api.on("GET", "/v1.0/users/u-ana/mailFolders", page(folders[::-1] if destination_first else folders))
        serve_delta(api, {
            None: page([message("m1", "Plan", attachments=True)], delta_link=delta_url("D1")),
            "D1": page([removed("m1")], delta_link=delta_url("D2")),
        })
        serve_delta(api, {
            None: page([], delta_link=delta_url("A1", folder_id="archive")),
            "A1": page([message("m1", "Plan", attachments=True)], delta_link=delta_url("A2", folder_id="archive")),
        }, folder_id="archive")
        api.on("GET", "/v1.0/users/u-ana/messages/m1/attachments", page([file_attachment("a1")]))
        await connector.run_sync()
        before = db.records["m1"]

        await connector.run_sync()

        assert db.deleted_external == []
        after = db.records["m1"]
        assert (after.id, after.external_revision_id, after.version) == (before.id, before.external_revision_id, 0)
        assert after.external_record_group_id == "archive"
        assert db.records["a1"].external_record_group_id == "archive"
        assert db.records["a1"].version == 0
        assert await folder_link(connector) == delta_url("D2")

    async def test_a_removal_that_fails_to_apply_keeps_the_folder_link(self, connector, api, db, monkeypatch) -> None:
        serve_delta(api, {
            None: page([message("m1", "Plan")], delta_link=delta_url("D1")),
            "D1": page([removed("m1")], delta_link=delta_url("D2")),
        })
        await connector.run_sync()

        async def failing_delete(*args: object) -> None:
            raise RuntimeError("database unavailable")

        monkeypatch.setattr(db, "delete_record_by_external_id", failing_delete)
        await connector.run_sync()

        assert await folder_link(connector) == delta_url("D1")
        assert connector.full_sync_coverage().complete is False

    async def test_the_first_run_after_the_upgrade_reads_mail_again_under_immutable_ids_once(
        self, connector, api, db, checkpoints,
    ) -> None:
        # Before the upgrade: the mail and its delta link carry the old id.
        serve_delta(api, {None: page([message("old-m1", "Plan", attachments=True)], delta_link=delta_url("OLD"))})
        api.on("GET", "/v1.0/users/u-ana/messages/old-m1/attachments", page([file_attachment("old-a1")]))
        await connector.run_sync()
        forget_id_type_marker(checkpoints)

        # A failed pass deletes nothing and leaves the move for the next run.
        serve_delta(api, {None: graph_error(500, "ErrorInternalServerError")})
        with pytest.raises(Exception, match="All 1 mailboxes failed"):
            await connector.run_sync()
        assert {"old-m1", "old-a1"} <= set(db.records)
        assert id_type_marker(checkpoints) is None

        # Graph now answers with the immutable id; the old link is not continued.
        serve_delta(api, {
            None: page([message("m1", "Plan", attachments=True)], delta_link=delta_url("D1")),
            "D1": page([], delta_link=delta_url("D2")),
            "OLD": graph_error(500, "ErrorInternalServerError"),
        })
        api.on("GET", "/v1.0/users/u-ana/messages/m1/attachments", page([file_attachment("a1")]))
        await connector.run_sync()

        assert set(db.deleted_external) == {"old-m1"}, "its attachment goes with it"
        assert set(db.records) == {"m1", "a1"}
        assert id_type_marker(checkpoints) == "ImmutableId"
        assert await folder_link(connector) == delta_url("D1")

        await connector.run_sync()
        assert api.query(api.calls("GET", delta_path())[-1]).get("$deltatoken") == "D1", "once only"
        assert set(db.deleted_external) == {"old-m1"}, "its attachment goes with it"


class TestDisabledAccounts:
    async def test_a_disabled_account_loses_its_mailbox_and_groups_until_it_is_enabled_again(self, connector, api, db) -> None:
        serve_group(api, [member("u-ana", ANA), member("u-bo", BO)])
        folders = "/v1.0/users/u-ana/mailFolders"
        await connector.run_sync()
        assert db.user_groups[GROUP] == [ANA, BO]
        assert len(api.calls("GET", folders)) == 1

        api.on("GET", "/v1.0/users", page([{**graph_user("u-ana", ANA), "accountEnabled": False}, graph_user("u-bo", BO)]))
        await connector.run_sync()

        assert db.user_groups[GROUP] == [BO], "transitiveMembers lists disabled accounts too"
        assert db.withdrawn == ["u-ana"]
        assert "u-ana" not in {u.source_user_id for u in db.app_users}
        assert len(api.calls("GET", folders)) == 1, "the mailbox is not synced"

        api.on("GET", "/v1.0/users", page([{**graph_user("u-ana", ANA), "accountEnabled": True}, graph_user("u-bo", BO)]))
        await connector.run_sync()

        assert db.user_groups[GROUP] == [ANA, BO]
        assert "u-ana" in {u.source_user_id for u in db.app_users}
        assert len(api.calls("GET", folders)) == 2


class TestGroupConversationCheckpoints:
    async def test_posts_that_failed_to_load_are_read_on_the_next_run(self, connector, api, db) -> None:
        serve_group(api, [member("u-ana", ANA)], threads=page([thread("t1", "Launch")]))
        posts = f"/v1.0/groups/{GROUP}/threads/t1/posts"
        api.on("GET", posts, graph_error(500, "ErrorInternalServerError"))
        await connector.run_sync()
        assert connector.full_sync_coverage().complete is False
        api.on("GET", posts, page([post("p1")]))

        await connector.run_sync()

        assert "p1" in db.records

    async def test_the_received_date_filter_applies_to_group_posts(self, connector, api, db) -> None:
        old = "2024-01-15T08:00:00Z"
        serve_group(api, [member("u-ana", ANA)], threads=page([
            {**thread("t-old", "Old"), "lastDeliveredDateTime": old},
            thread("t1", "Launch"),
        ]))
        api.on("GET", f"/v1.0/groups/{GROUP}/threads/t-old/posts", page([post("p-old", old)]))
        api.on("GET", f"/v1.0/groups/{GROUP}/threads/t1/posts", page([post("p-before", old), post("p1")]))
        april = int(datetime(2024, 4, 1, tzinfo=timezone.utc).timestamp() * 1000)
        set_sync_filters(connector, received_date={"operator": "is_after", "type": "datetime", "value": {"start": april, "end": None}})

        await connector.run_sync()

        assert set(stored_by_type(db, RecordType.GROUP_MAIL)) == {"p1"}
        assert api.calls("GET", f"/v1.0/groups/{GROUP}/threads/t-old/posts") == [], "a thread older than the window is not read"

    async def test_the_cursor_is_graphs_last_delivery_time_not_the_host_clock(self, connector, api, db) -> None:
        later = "2024-05-02T09:30:00Z"
        serve_group(api, [member("u-ana", ANA)], threads=page([thread("t1", "Launch")]))
        api.on("GET", f"/v1.0/groups/{GROUP}/threads/t1/posts", page([post("p1")]))
        await connector.run_sync()
        key = generate_record_sync_point_key("group_conversations", "group", GROUP)
        saved = await connector.group_conversations_sync_point.read_sync_point(key)
        assert saved["last_sync_timestamp"] == SENT

        # A post Graph delivered after the cursor but before this host's clock is still read.
        serve_group(api, [member("u-ana", ANA)], threads=page([{**thread("t1", "Launch"), "lastDeliveredDateTime": later}]))
        api.on("GET", f"/v1.0/groups/{GROUP}/threads/t1/posts", page([post("p1"), post("p2", later)]))
        await connector.run_sync()

        assert "p2" in db.records
        saved = await connector.group_conversations_sync_point.read_sync_point(key)
        assert saved["last_sync_timestamp"] == later

    async def test_a_post_that_shows_up_late_with_an_earlier_time_is_still_read(self, connector, api, db) -> None:
        serve_group(api, [member("u-ana", ANA)], threads=page([thread("t1", "Launch"), thread("t2", "Ops")]))
        api.on("GET", f"/v1.0/groups/{GROUP}/threads/t1/posts", page([post("p1")]))
        api.on("GET", f"/v1.0/groups/{GROUP}/threads/t2/posts", page([]))
        await connector.run_sync()
        versions = db.records["p1"].version

        # p2 was delivered 5 minutes before the cursor but only became visible now.
        api.on("GET", f"/v1.0/groups/{GROUP}/threads/t2/posts", page([post("p2", "2024-05-01T09:55:00Z")]))
        await connector.run_sync()

        assert "p2" in db.records
        assert db.records["p1"].version == versions, "the replayed post is not re-indexed"

    async def test_every_page_of_group_threads_is_read(self, connector, api, db) -> None:
        def threads(request: httpx.Request) -> httpx.Response:
            if api.query(request).get("$skiptoken") == "2":
                return json_response(page([thread("t2", "Older")]))
            return json_response(page([thread("t1", "Launch")], next_link=f"{GRAPH}/groups/{GROUP}/threads?$skiptoken=2"))

        serve_group(api, [member("u-ana", ANA)], threads=threads)
        api.on("GET", f"/v1.0/groups/{GROUP}/threads/t1/posts", page([post("p1")]))
        api.on("GET", f"/v1.0/groups/{GROUP}/threads/t2/posts", page([post("p2")]))

        await connector.run_sync()

        assert {"p1", "p2"} <= set(db.records)


OAUTH_CONFIG_ID = "oauth-1"
def forget_folder_inventory(checkpoints: FakeCheckpointStore) -> None:
    """The state a mailbox synced by a version without folder inventories is in."""
    for key in [k for k in checkpoints.sync_points if "/folder_inventory/" in k]:
        del checkpoints.sync_points[key]


class TestFolderInventoryAndFullReads:
    async def test_a_deleted_folder_that_comes_back_is_read_again_in_full(self, connector, api, db) -> None:
        listing = "/v1.0/users/u-ana/mailFolders"
        api.on("GET", listing, page([folder(INBOX, "Inbox"), folder("proj", "Projects")]))
        serve_delta(api, {None: page([message("p1", "Spec")], delta_link=delta_url("P1", folder_id="proj")),
                          "P1": page([], delta_link=delta_url("P2", folder_id="proj"))}, folder_id="proj")
        await connector.run_sync()
        api.on("GET", listing, page([folder(INBOX, "Inbox")]))
        await connector.run_sync()
        assert "p1" not in db.records
        assert await connector.email_delta_sync_point.read_sync_point(
            connector._folder_sync_point_key(SimpleNamespace(source_user_id="u-ana"), "proj")) == {}

        # Restored from Deleted Items with the same id: its old link would report no mail.
        api.on("GET", listing, page([folder(INBOX, "Inbox"), folder("proj", "Projects")]))
        await connector.run_sync()

        assert "p1" in db.records

    async def test_a_subfolder_moved_out_of_a_deleted_folder_keeps_its_mail(self, connector, api, db) -> None:
        listing = "/v1.0/users/u-ana/mailFolders"
        api.on("GET", listing, page([folder(INBOX, "Inbox"), {**folder("proj", "Projects"), "childFolderCount": 1}]))
        api.on("GET", "/v1.0/users/u-ana/mailFolders/proj/childFolders", page([{**folder("sub", "Q3"), "parentFolderId": "proj"}]))
        api.on("GET", delta_path(folder_id="proj"), page([], delta_link=delta_url("P1", folder_id="proj")))
        serve_delta(api, {None: page([message("s1", "Notes")], delta_link=delta_url("S1", folder_id="sub")),
                          "S1": page([], delta_link=delta_url("S2", folder_id="sub"))}, folder_id="sub")
        await connector.run_sync()

        # Q3 moved under Inbox, then Projects deleted.
        api.on("GET", listing, page([{**folder(INBOX, "Inbox"), "childFolderCount": 1}]))
        api.on("GET", f"/v1.0/users/u-ana/mailFolders/{INBOX}/childFolders", page([{**folder("sub", "Q3"), "parentFolderId": INBOX}]))
        await connector.run_sync()

        assert "proj" not in db.record_groups
        assert db.record_groups["sub"].parent_external_group_id == INBOX
        assert "s1" in db.records

    async def test_a_folder_whose_delete_failed_is_deleted_on_the_next_run(self, connector, api, db, monkeypatch) -> None:
        listing = "/v1.0/users/u-ana/mailFolders"
        api.on("GET", listing, page([folder(INBOX, "Inbox"), folder("old", "Old")]))
        api.on("GET", delta_path(folder_id="old"), page([message("o1", "Old mail")], delta_link=delta_url("O1", folder_id="old")))
        await connector.run_sync()
        api.on("GET", listing, page([folder(INBOX, "Inbox")]))
        real_delete = db.on_record_group_deleted
        monkeypatch.setattr(db, "on_record_group_deleted", AsyncMock(return_value=False))

        await connector.run_sync()
        assert "o1" in db.records

        monkeypatch.setattr(db, "on_record_group_deleted", real_delete)
        await connector.run_sync()
        assert "old" not in db.record_groups
        assert "o1" not in db.records

    async def test_a_child_folder_listing_short_of_its_count_removes_nothing(self, connector, api, db) -> None:
        listing = "/v1.0/users/u-ana/mailFolders"
        api.on("GET", listing, page([{**folder(INBOX, "Inbox"), "childFolderCount": 2}]))
        children = f"/v1.0/users/u-ana/mailFolders/{INBOX}/childFolders"
        api.on("GET", children, page([{**folder("a", "A"), "parentFolderId": INBOX}, {**folder("b", "B"), "parentFolderId": INBOX}]))
        api.on("GET", delta_path(folder_id="b"), page([message("b1", "B mail")], delta_link=delta_url("B1", folder_id="b")))
        await connector.run_sync()

        # Graph's offset paging skipped B (a sibling was deleted between two pages).
        def child_pages(request: httpx.Request) -> httpx.Response:
            if api.query(request).get("$skiptoken") == "2":
                return json_response(page([]))
            return json_response(page([{**folder("a", "A"), "parentFolderId": INBOX}], next_link=f"{GRAPH}{children[5:]}?$skiptoken=2"))

        api.on("GET", children, child_pages)
        await connector.run_sync()

        assert "b1" in db.records
        assert db.deleted_record_groups == []
        assert connector.full_sync_coverage().complete is False

    async def test_a_hidden_child_folder_in_the_count_does_not_keep_a_deleted_folder(self, connector, api, db) -> None:
        listing = "/v1.0/users/u-ana/mailFolders"
        children = f"/v1.0/users/u-ana/mailFolders/{INBOX}/childFolders"
        # childFolderCount includes a hidden folder the listing leaves out (e.g. "Conversation History").
        api.on("GET", listing, page([{**folder(INBOX, "Inbox"), "childFolderCount": 3}]))
        api.on("GET", children, page([{**folder("a", "A"), "parentFolderId": INBOX}, {**folder("b", "B"), "parentFolderId": INBOX}]))
        api.on("GET", delta_path(folder_id="b"), page([message("b1", "B mail")], delta_link=delta_url("B1", folder_id="b")))
        await connector.run_sync()

        api.on("GET", listing, page([{**folder(INBOX, "Inbox"), "childFolderCount": 2}]))
        api.on("GET", children, page([{**folder("a", "A"), "parentFolderId": INBOX}]))
        await connector.run_sync()

        assert "b" not in db.record_groups
        assert "b1" not in db.records

    async def test_child_folders_follow_the_next_link(self, connector, api, db) -> None:
        api.on("GET", "/v1.0/users/u-ana/mailFolders", page([{**folder(INBOX, "Inbox"), "childFolderCount": 2}]))
        children = f"/v1.0/users/u-ana/mailFolders/{INBOX}/childFolders"

        def child_pages(request: httpx.Request) -> httpx.Response:
            if api.query(request).get("$skiptoken") == "2":
                return json_response(page([{**folder("b", "B"), "parentFolderId": INBOX}]))
            # A page shorter than $top that still has a next page.
            return json_response(page([{**folder("a", "A"), "parentFolderId": INBOX}], next_link=f"{GRAPH}{children[5:]}?$skiptoken=2"))

        api.on("GET", children, child_pages)

        await connector.run_sync()

        assert {"a", "b"} <= set(db.record_groups)

    async def test_an_upgraded_mailbox_drops_deleted_items_synced_before(self, connector, api, db, checkpoints) -> None:
        trash = serve_deleted_items(api)
        api.on("GET", "/v1.0/users/u-ana/mailFolders", page([folder(INBOX, "Inbox"), trash]))
        api.on("GET", "/v1.0/users/u-ana/mailFolders/inbox", folder(INBOX, "Inbox"))
        api.on("GET", delta_path(folder_id=TRASH), page([message("t1", "Old")], delta_link=delta_url("T1", folder_id=TRASH)))
        set_sync_filters(connector, folders={"operator": "in", "value": ["Inbox", "Deleted Items"], "type": "multiselect"})
        await connector.run_sync()
        assert "t1" in db.records
        # An earlier version synced Deleted Items by default and kept no folder inventory.
        set_sync_filters(connector)
        forget_folder_inventory(checkpoints)

        await connector.run_sync()

        assert TRASH not in db.record_groups
        assert "t1" not in db.records

    async def test_a_full_sync_leaves_unlisted_folders_to_the_sweep(self, connector, api, db, checkpoints) -> None:
        trash = serve_deleted_items(api)
        api.on("GET", "/v1.0/users/u-ana/mailFolders", page([folder(INBOX, "Inbox"), trash]))
        api.on("GET", "/v1.0/users/u-ana/mailFolders/inbox", folder(INBOX, "Inbox"))
        api.on("GET", delta_path(folder_id=TRASH), page([message("t1", "Old")], delta_link=delta_url("T1", folder_id=TRASH)))
        set_sync_filters(connector, folders={"operator": "in", "value": ["Inbox", "Deleted Items"], "type": "multiselect"})
        await connector.run_sync()
        set_sync_filters(connector)
        checkpoints.sync_points.clear()  # what a full sync does first

        seen = await run_full_sync(connector, db)

        assert db.deleted_record_groups == [], "a filter change is unlinked by the sweep, not deleted"
        assert "t1" not in seen[CollectionNames.RECORDS.value]

    async def test_include_mode_keeps_a_selected_folder_graph_answered_404_for(self, connector, api, db) -> None:
        api.on("GET", "/v1.0/users/u-ana/mailFolders", page([folder(INBOX, "Inbox"), folder("sent", "Sent Items")]))
        api.on("GET", "/v1.0/users/u-ana/mailFolders/inbox", folder(INBOX, "Inbox"))
        api.on("GET", "/v1.0/users/u-ana/mailFolders/sentitems", folder("sent", "Sent Items"))
        api.on("GET", delta_path(folder_id="sent"), page([message("s1", "Sent")], delta_link=delta_url("S1", folder_id="sent")))
        set_sync_filters(connector, custom_folders={"operator": "is", "value": False, "type": "boolean"})
        await connector.run_sync()
        assert "s1" in db.records

        api.on("GET", "/v1.0/users/u-ana/mailFolders/sentitems", graph_error(404, "ErrorItemNotFound"))
        await connector.run_sync()

        assert "s1" in db.records
        assert "sent" not in db.deleted_record_groups
        assert connector.full_sync_coverage().complete is False

    async def test_mail_deleted_while_the_delta_link_was_expired_is_removed(self, connector, api, db) -> None:
        serve_delta(api, {
            None: page([message("m1", "Plan", attachments=True), message("m2", "Old")], delta_link=delta_url("D1")),
            "D1": graph_error(410, "syncStateNotFound"),
        })
        api.on("GET", "/v1.0/users/u-ana/messages/m1/attachments", page([file_attachment("a1")]))
        await connector.run_sync()
        serve_delta(api, {
            None: page([message("m1", "Plan", attachments=True)], delta_link=delta_url("F1")),
            "D1": graph_error(410, "syncStateNotFound"),
        })

        await connector.run_sync()

        assert {"m1", "a1"} <= set(db.records)
        assert "m2" not in db.records

    async def test_a_full_sync_after_narrowing_the_date_window_deletes_no_mail_and_lets_the_sweep_unlink_older_mail(self, connector, api, db, checkpoints) -> None:
        old = "2024-01-15T08:00:00Z"
        old_mail = {**message("m-old", "Old"), "createdDateTime": old, "receivedDateTime": old}
        serve_delta(api, {None: page([old_mail, message("m1", "Plan")], delta_link=delta_url("D1"))})
        await connector.run_sync()
        april = int(datetime(2024, 4, 1, tzinfo=timezone.utc).timestamp() * 1000)
        set_sync_filters(connector, received_date={"operator": "is_after", "type": "datetime", "value": {"start": april, "end": None}})
        serve_delta(api, {None: page([message("m1", "Plan")], delta_link=delta_url("D2"))})
        checkpoints.sync_points.clear()  # a filter change forces a full sync, which clears every sync point

        seen = await run_full_sync(connector, db)

        assert db.deleted_external == [], "out-of-window mail is not deleted"
        assert "m-old" not in seen[CollectionNames.RECORDS.value]
        assert "m-old" in db.records
        # A narrowed window: the sweep is told no window, so it unlinks the older unseen mail.
        assert db.unlink_window.get("window_start_ms") is None

    async def test_an_expired_link_reread_keeps_mail_outside_the_date_window(self, connector, api, db) -> None:
        old = "2024-01-15T08:00:00Z"
        old_mail = {**message("m-old", "Old"), "createdDateTime": old, "receivedDateTime": old}
        serve_delta(api, {None: page([old_mail, message("m1", "Plan"), message("m2", "Gone")], delta_link=delta_url("D1"))})
        await connector.run_sync()
        april = int(datetime(2024, 4, 1, tzinfo=timezone.utc).timestamp() * 1000)
        set_sync_filters(connector, received_date={"operator": "is_after", "type": "datetime", "value": {"start": april, "end": None}})
        serve_delta(api, {
            None: page([message("m1", "Plan")], delta_link=delta_url("F1")),
            "D1": graph_error(410, "syncStateNotFound"),
        })

        await connector.run_sync()

        assert "m2" not in db.records, "in the window and not read: deleted in Outlook"
        assert "m-old" in db.records, "outside the window: the read could not see it"

    async def test_the_id_migration_finishes_per_folder_despite_a_failing_mail(
        self, connector, api, db, checkpoints,
    ) -> None:
        api.on("GET", "/v1.0/users/u-ana/mailFolders", page([folder(INBOX, "Inbox"), folder("archive", "Archive")]))
        serve_delta(api, {None: page([message("old-m1", "Plan"), message("old-m2", "Report")], delta_link=delta_url("OLD"))})
        serve_delta(api, {None: page([message("old-r1", "Kept")], delta_link=delta_url("OLDA", folder_id="archive"))},
                    folder_id="archive")
        await connector.run_sync()
        forget_id_type_marker(checkpoints)
        serve_delta(api, {None: page([message("m1", "Plan"), message("m2", "Report", attachments=True)], delta_link=delta_url("D1"))})
        serve_delta(api, {None: page([message("r1", "Kept")], delta_link=delta_url("A1", folder_id="archive")),
                          "A1": page([], delta_link=delta_url("A2", folder_id="archive"))}, folder_id="archive")
        api.on("GET", "/v1.0/users/u-ana/messages/m2/attachments", graph_error(500, "ErrorInternalServerError"))

        await connector.run_sync()

        assert not {"old-m1", "old-m2", "old-r1"} & set(db.records), "no copy under an old id stays searchable"
        assert {"m1", "r1"} <= set(db.records)
        assert id_type_marker(checkpoints, "archive") == "ImmutableId"
        assert await folder_link(connector) == delta_url("OLD"), "the failed mail is retried"

        await connector.run_sync()
        assert api.query(api.calls("GET", delta_path(folder_id="archive"))[-1]).get("$deltatoken") == "A1"

    async def test_thread_edges_get_light_mail_references(self, connector, api, db, monkeypatch) -> None:
        serve_delta(api, {None: page([message("m1", "Plan")], delta_link=delta_url("D1"))})
        passed = []

        async def edges(org_id: str, user: object, refs: list) -> int:
            passed.extend(refs)
            return 0

        monkeypatch.setattr(connector, "_create_all_thread_edges_for_user", edges)

        await connector.run_sync()

        (ref,) = passed
        assert type(ref).__name__ == "MailThreadRef"
        assert (ref.id, ref.thread_id) == (db.records["m1"].id, "conv-m1")

    async def test_a_reply_finds_a_parent_synced_in_the_same_run_without_a_db_lookup(
        self, connector, api, db, checkpoints, monkeypatch,
    ) -> None:
        root = bytes(22)
        index = {"m1": root, "r1": root + b"\x01" * 5, "r2": bytes(range(22)) + b"\x02" * 5}
        serve_delta(api, {None: page(
            [{**message(m, m), "conversationId": "conv-1", "conversationIndex": base64.b64encode(i).decode()} for m, i in index.items()],
            delta_link=delta_url("D1"),
        )})
        lookups, created = [], []

        async def stored_parent(connector_id: str, parent_index: str, thread_id: str, user_id: str) -> Record:
            lookups.append(parent_index)
            return SimpleNamespace(id="stored-parent")

        async def batch_create_edges(edges: list[dict[str, Any]], collection: str) -> None:
            created.extend(edges)

        monkeypatch.setattr(db, "get_record_by_conversation_index", stored_parent)
        checkpoints.batch_create_edges = batch_create_edges

        await connector.run_sync()

        assert lookups == [base64.b64encode(bytes(range(22))).decode()], "only the parent not synced in this run is looked up"
        assert {(e["from_id"], e["to_id"]) for e in created} == {
            (db.records["m1"].id, db.records["r1"].id), ("stored-parent", db.records["r2"].id),
        }

    async def test_an_unchanged_mail_is_not_rewritten_and_its_attachments_not_listed(
        self, connector, api, db, checkpoints,
    ) -> None:
        serve_delta(api, {
            None: page([message("m1", "Plan", attachments=True)], delta_link=delta_url("D1")),
            "D1": page([{**message("m1", "Plan", attachments=True, change_key="ck2"), "isRead": True}], delta_link=delta_url("D2")),
            "D2": page([], delta_link=delta_url("D3")),
        })
        attachments = "/v1.0/users/u-ana/messages/m1/attachments"
        api.on("GET", attachments, page([file_attachment("a1")]))
        await connector.run_sync()
        writes = len(db.record_batches)

        await connector.run_sync()

        assert len(api.calls("GET", attachments)) == 1
        assert len(db.record_batches) == writes
        assert await folder_link(connector) == delta_url("D2")

        checkpoints.sync_points.clear()  # what a full sync does first
        seen = await run_full_sync(connector, db)
        assert "m1" in seen[CollectionNames.RECORDS.value], "a full sync still writes it"

    async def test_an_attachment_listed_without_a_content_type_keeps_its_record(self, connector, api, db) -> None:
        draft = {**message("m1", "Plan", attachments=True), "isDraft": True}
        serve_delta(api, {
            None: page([draft], delta_link=delta_url("D1")),
            "D1": page([{**draft, "changeKey": "ck2"}], delta_link=delta_url("D2")),
        })
        attachments = "/v1.0/users/u-ana/messages/m1/attachments"
        api.on("GET", attachments, page([file_attachment("a1"), file_attachment("a2", "old.pdf")]))
        await connector.run_sync()
        api.on("GET", attachments, page([{**file_attachment("a1"), "contentType": None}]))

        await connector.run_sync()

        assert "a1" in db.records, "skipped this time, still on the mail"
        assert "a2" not in db.records

    async def test_a_group_post_attachment_that_failed_is_synced_on_the_next_run(self, connector, api, db, monkeypatch) -> None:
        # The cursor (latest delivery) is an hour past the post, beyond the overlap.
        serve_group(api, [member("u-ana", ANA)], threads=page([{**thread("t1", "Launch"), "lastDeliveredDateTime": "2024-05-01T11:00:00Z"}]))
        api.on("GET", f"/v1.0/groups/{GROUP}/threads/t1/posts", page([{**post("p1"), "hasAttachments": True}]))
        api.on("GET", f"/v1.0/groups/{GROUP}/threads/t1/posts/p1", {**post("p1"), "attachments": [file_attachment("a1")]})
        real_lookup = db.get_record_by_external_id

        async def failing_lookup(connector_id: str, external_id: str) -> Record | None:
            if external_id == "a1":
                raise RuntimeError("db unavailable")
            return await real_lookup(connector_id, external_id)

        monkeypatch.setattr(db, "get_record_by_external_id", failing_lookup)
        await connector.run_sync()
        assert "a1" not in db.records

        monkeypatch.setattr(db, "get_record_by_external_id", real_lookup)
        await connector.run_sync()

        assert "a1" in db.records

    async def test_a_group_post_attachment_over_the_cap_is_refused_without_loading_the_post(self, connector, api, db) -> None:
        serve_group(api, [member("u-ana", ANA)], threads=page([thread("t1", "Launch")]))
        api.on("GET", f"/v1.0/groups/{GROUP}/threads/t1/posts", page([{**post("p1"), "hasAttachments": True}]))
        post_path = f"/v1.0/groups/{GROUP}/threads/t1/posts/p1"
        api.on("GET", post_path, {**post("p1"), "attachments": [
            {**file_attachment("big", "video.mp4"), "size": OutlookSyncConfig.MAX_INDEXED_ATTACHMENT_BYTES + 1},
        ]})
        await connector.run_sync()
        reads = len(api.calls("GET", post_path))

        with pytest.raises(HTTPException) as refused:
            await connector.stream_record(db.records["big"])

        assert refused.value.status_code == 422
        assert "25 MB" in refused.value.detail
        assert len(api.calls("GET", post_path)) == reads

    async def test_a_small_post_attachment_beside_large_ones_is_refused_before_content_is_loaded(
        self, connector, api, db,
    ) -> None:
        serve_group(api, [member("u-ana", ANA)], threads=page([thread("t1", "Launch")]))
        api.on("GET", f"/v1.0/groups/{GROUP}/threads/t1/posts", page([{**post("p1"), "hasAttachments": True}]))
        post_path = f"/v1.0/groups/{GROUP}/threads/t1/posts/p1"
        half = OutlookSyncConfig.MAX_INDEXED_ATTACHMENT_BYTES // 2 + 1
        api.on("GET", post_path, {**post("p1"), "attachments": [
            file_attachment("small"), {**file_attachment("big1", "a.mp4"), "size": half}, {**file_attachment("big2", "b.mp4"), "size": half},
        ]})
        await connector.run_sync()
        reads = len(api.calls("GET", post_path))
        connector._group_post_thread_id = AsyncMock(return_value="t1")  # the fake store keeps no typed records

        with pytest.raises(HTTPException) as refused:
            await connector.stream_record(db.records["small"])

        assert refused.value.status_code == 422
        [metadata_read] = api.calls("GET", post_path)[reads:]
        assert "contentType" in api.query(metadata_read)["$expand"], "only the attachment metadata was read"

    async def test_a_post_attachment_streams_after_the_size_check(self, connector, api, db) -> None:
        serve_group(api, [member("u-ana", ANA)], threads=page([thread("t1", "Launch")]))
        api.on("GET", f"/v1.0/groups/{GROUP}/threads/t1/posts", page([{**post("p1"), "hasAttachments": True}]))
        post_path = f"/v1.0/groups/{GROUP}/threads/t1/posts/p1"
        api.on("GET", post_path, {**post("p1"), "attachments": [
            {**file_attachment("small"), "contentBytes": base64.b64encode(b"hello").decode()},
        ]})
        await connector.run_sync()
        connector._group_post_thread_id = AsyncMock(return_value="t1")  # the fake store keeps no typed records

        response = await connector.stream_record(db.records["small"])

        assert b"".join([chunk async for chunk in response.body_iterator]) == b"hello"

    async def test_a_failed_run_notification_carries_no_raw_error_text(self, connector) -> None:
        await notify_sync_outcome(connector, Exception("Code: 500 https://graph.microsoft.com/v1.0/users/u-ana/x"), {})

        (note,) = await notes(connector)
        assert "Microsoft Graph answered 500" in note["message"]
        assert "https://" not in note["message"]

    @pytest.mark.parametrize("auth", [
        {"tenantId": "not a tenant!", "clientId": "client-1", "clientSecret": "secret-1"},
        {"tenantId": TENANT, "clientId": "client-1", "clientSecret": ""},
    ])
    async def test_init_with_malformed_credentials_says_what_to_check(self, api, db, checkpoints, auth) -> None:
        instance = OutlookConnector(
            logging.getLogger("test.outlook"), db, checkpoints, FakeConfigService(CONNECTOR_ID, {"auth": auth}),
            CONNECTOR_ID, "team", "creator-1",
        )

        with pytest.raises(ConnectorInitError, match=r"credentials are not valid.*Directory \(Tenant\) ID"):
            await instance.init()

    async def test_a_second_init_closes_the_client_it_replaces(self, connector) -> None:
        first = connector.external_client.get_client()

        assert await connector.init() is True

        assert first.credential is None
        assert connector.external_client.get_client().credential is not None


ME_INBOX = "me-inbox"
OID = "u-ana"


def delegated_token(version: int) -> str:
    """An unsigned JWT: the connector only reads the signed-in user's object id from it."""

    def part(payload: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")

    return f"{part({'alg': 'none'})}.{part({'oid': OID, 'ver': version})}.sig"


class PersonalConfigService(FakeConfigService):
    async def get_config(self, path: str, default: object = None, **kwargs: object) -> object:
        if path.startswith("/services/oauth/"):
            return [{"_id": OAUTH_CONFIG_ID, "config": {"tenantId": TENANT, "clientId": "client-1", "clientSecret": "secret-1"}}]
        return await super().get_config(path, default, **kwargs)


class PersonalRecordsDb(OutlookRecordsDb):
    async def get_app_creator_user(self, connector_id: str) -> SimpleNamespace:
        return SimpleNamespace(user_id="pipeshub-ana", email=ANA)


def me_delta_path(folder_id: str = ME_INBOX) -> str:
    return delta_path(OID, folder_id)


def me_delta_url(token: str, folder_id: str = ME_INBOX) -> str:
    return delta_url(token, OID, folder_id)


def serve_me_delta(api: MicrosoftCloudStub, pages_by_token: dict[Optional[str], Any], folder_id: str = ME_INBOX) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        answer = pages_by_token[api.query(request).get("$deltatoken")]
        return answer(request) if callable(answer) else answer if isinstance(answer, httpx.Response) else json_response(answer)

    api.on("GET", me_delta_path(folder_id), handler)


class TestOutlookPersonal:
    @pytest.fixture
    def personal_api(self, cloud: MicrosoftCloudStub, monkeypatch: pytest.MonkeyPatch) -> MicrosoftCloudStub:
        route_microsoft_http(monkeypatch, cloud)
        cloud.on("GET", f"/v1.0/users/{OID}/mailFolders", page([folder(ME_INBOX, "Inbox")]))
        return cloud

    @pytest.fixture
    async def personal(self, personal_api: MicrosoftCloudStub, checkpoints: FakeCheckpointStore) -> OutlookIndividualConnector:
        config = {"auth": {"oauthConfigId": OAUTH_CONFIG_ID}, "credentials": {"access_token": delegated_token(1)}}
        self.config_service = PersonalConfigService("outlook-personal-1", config)
        self.db = PersonalRecordsDb()
        instance = OutlookIndividualConnector(
            logging.getLogger("test.outlook_personal"), self.db, checkpoints, self.config_service,
            "outlook-personal-1", "personal", "creator-1",
        )
        instance._notification_service = RecordingNotifications()
        assert await instance.init() is True
        return instance

    async def personal_link(self, connector: OutlookIndividualConnector, folder_id: str = ME_INBOX) -> Optional[str]:
        key = generate_record_sync_point_key(RecordType.MAIL.value, "folders", folder_id)
        return (await connector.email_delta_sync_point.read_sync_point(key)).get("delta_link")

    async def test_every_page_of_a_mails_attachments_is_read(self, personal, personal_api) -> None:
        serve_me_delta(personal_api, {None: page([message("m1", "Plan", attachments=True)], delta_link=me_delta_url("D1"))})
        attachments = f"/v1.0/users/{OID}/messages/m1/attachments"

        def pages(request: httpx.Request) -> httpx.Response:
            if personal_api.query(request).get("$skiptoken") == "p2":
                return json_response(page([file_attachment("a2", "notes.pdf")]))
            return json_response(page([file_attachment("a1")], next_link=f"{GRAPH}{attachments[5:]}?$skiptoken=p2"))

        personal_api.on("GET", attachments, pages)

        await personal.run_sync()

        assert {r.record_name for r in self.db.records.values() if r.record_type == RecordType.FILE} == {"plan.pdf", "notes.pdf"}

    async def test_a_repeated_attachments_next_link_fails_the_mail_instead_of_looping(self, personal, personal_api) -> None:
        serve_me_delta(personal_api, {None: page([message("m1", "Plan", attachments=True)], delta_link=me_delta_url("D1"))})
        attachments = f"/v1.0/users/{OID}/messages/m1/attachments"
        personal_api.on("GET", attachments, page([file_attachment("a1")], next_link=f"{GRAPH}{attachments[5:]}?$skiptoken=same"))

        await personal.run_sync()

        assert len(personal_api.calls("GET", attachments)) == 2, "the repeated link is followed once, then refused"
        assert personal.full_sync_coverage().complete is False

    async def test_sync_reads_every_page_of_the_users_own_mail_with_their_token(self, personal, personal_api) -> None:
        serve_me_delta(personal_api, {
            None: page([message("m1", "Plan")], next_link=me_delta_url("p2")),
            "p2": page([message("m2", "Budget")], delta_link=me_delta_url("D1")),
        })

        await personal.run_sync()

        assert {r.record_name for r in self.db.records.values()} == {"Plan", "Budget"}
        assert await self.personal_link(personal) == me_delta_url("D1")
        graph = personal_api.graph_calls()
        assert graph and all(bearer(r) == f"Bearer {delegated_token(1)}" for r in graph)
        assert personal_api.calls("POST", TOKEN_PATH) == [], "a delegated token is used as given"

    async def test_a_token_refreshed_in_the_background_is_used_for_the_next_folder(self, personal, personal_api) -> None:
        personal_api.on("GET", f"/v1.0/users/{OID}/mailFolders", page([folder(ME_INBOX, "Inbox"), folder("sent", "Sent")]))

        def rotate_then_answer(request: httpx.Request) -> httpx.Response:
            self.config_service.config["credentials"]["access_token"] = delegated_token(2)
            return json_response(page([message("m1", "Plan")], delta_link=me_delta_url("D1")))

        serve_me_delta(personal_api, {None: rotate_then_answer})
        serve_me_delta(personal_api, {None: page([message("s1", "Sent")], delta_link=me_delta_url("S1", "sent"))}, "sent")

        await personal.run_sync()

        (sent_request,) = personal_api.calls("GET", me_delta_path("sent"))
        assert bearer(sent_request) == f"Bearer {delegated_token(2)}"
        assert {"m1", "s1"} <= set(self.db.records)

    async def test_a_change_that_fails_to_apply_keeps_the_folder_checkpoint(self, personal, personal_api, monkeypatch) -> None:
        serve_me_delta(personal_api, {
            None: page([message("m1", "Plan")], delta_link=me_delta_url("D1")),
            "D1": page([removed("m1")], delta_link=me_delta_url("D2")),
        })
        await personal.run_sync()

        async def failing_delete(*args: object) -> None:
            raise RuntimeError("database unavailable")

        monkeypatch.setattr(self.db, "delete_record_by_external_id", failing_delete)
        await personal.run_sync()

        assert await self.personal_link(personal) == me_delta_url("D1")

    async def test_a_failed_delta_page_keeps_the_folder_checkpoint(self, personal, personal_api) -> None:
        serve_me_delta(personal_api, {
            None: page([message("m1", "Plan")], delta_link=me_delta_url("D1")),
            "D1": page([message("m2", "New")], next_link=me_delta_url("p2")),
            "p2": graph_error(500, "ErrorInternalServerError"),
        })
        await personal.run_sync()

        with pytest.raises(Exception, match="All 1 folders failed"):
            await personal.run_sync()

        assert await self.personal_link(personal) == me_delta_url("D1")
        assert personal.full_sync_coverage().complete is False

    async def test_an_expired_delta_link_reads_the_folder_again_in_full(self, personal, personal_api) -> None:
        serve_me_delta(personal_api, {
            None: page([message("m1", "Plan")], delta_link=me_delta_url("D1")),
            "D1": graph_error(410, "syncStateNotFound"),
        })
        await personal.run_sync()
        serve_me_delta(personal_api, {
            None: page([message("m1", "Plan"), message("m2", "New")], delta_link=me_delta_url("F1")),
            "D1": graph_error(410, "syncStateNotFound"),
        })

        await personal.run_sync()

        assert {"m1", "m2"} <= set(self.db.records)
        assert await self.personal_link(personal) == me_delta_url("F1")
        assert personal.full_sync_coverage().complete is True

    async def test_a_read_or_flag_change_does_not_reindex_but_an_edit_does(self, personal, personal_api) -> None:
        serve_me_delta(personal_api, {
            None: page([message("m1", "Plan")], delta_link=me_delta_url("D1")),
            "D1": page([{**message("m1", "Plan", change_key="ck2"), "isRead": True, "flag": {"flagStatus": "flagged"}}],
                       delta_link=me_delta_url("D2")),
            "D2": page([message("m1", "Plan v2", change_key="ck3")], delta_link=me_delta_url("D3")),
        })
        await personal.run_sync()
        revision = self.db.records["m1"].external_revision_id

        await personal.run_sync()
        assert (self.db.records["m1"].external_revision_id, self.db.records["m1"].version) == (revision, 0)

        await personal.run_sync()
        assert self.db.records["m1"].external_revision_id != revision
        assert self.db.records["m1"].version == 1

    async def test_attachments_that_failed_to_list_keep_the_folder_checkpoint(self, personal, personal_api) -> None:
        serve_me_delta(personal_api, {
            None: page([message("m1", "Plan")], delta_link=me_delta_url("D1")),
            "D1": page([message("m2", "Report", attachments=True)], delta_link=me_delta_url("D2")),
        })
        await personal.run_sync()
        personal_api.on("GET", f"/v1.0/users/{OID}/messages/m2/attachments", graph_error(500, "ErrorInternalServerError"))

        await personal.run_sync()

        assert await self.personal_link(personal) == me_delta_url("D1")

    async def test_an_attachment_removed_from_a_draft_is_dropped(self, personal, personal_api) -> None:
        draft = {**message("m1", "Plan", attachments=True), "isDraft": True}
        serve_me_delta(personal_api, {
            None: page([draft], delta_link=me_delta_url("D1")),
            "D1": page([{**draft, "changeKey": "ck2"}], delta_link=me_delta_url("D2")),
        })
        attachments = f"/v1.0/users/{OID}/messages/m1/attachments"
        personal_api.on("GET", attachments, page([file_attachment("a1"), file_attachment("a2", "old.pdf")]))
        await personal.run_sync()
        personal_api.on("GET", attachments, page([file_attachment("a1")]))

        await personal.run_sync()

        assert "a1" in self.db.records
        assert "a2" not in self.db.records
        assert all(personal_api.query(r)["$select"] == "id,name,contentType,size,lastModifiedDateTime,isInline"
                   for r in personal_api.calls("GET", attachments)), "no contentBytes in the listing"

    async def test_a_mail_removed_with_reason_changed_is_deleted_not_saved_empty(self, personal, personal_api) -> None:
        serve_me_delta(personal_api, {
            None: page([message("m1", "Plan")], delta_link=me_delta_url("D1")),
            "D1": page([removed("m1", "changed")], delta_link=me_delta_url("D2")),
        })
        await personal.run_sync()

        await personal.run_sync()

        assert "m1" not in self.db.records

    async def test_every_page_of_child_folders_is_read(self, personal, personal_api, monkeypatch) -> None:
        monkeypatch.setattr(OutlookSyncConfig, "MAX_FOLDER_PAGE_SIZE", 2)
        personal_api.on("GET", f"/v1.0/users/{OID}/mailFolders", page([{**folder(ME_INBOX, "Inbox"), "childFolderCount": 3}]))
        children = [{**folder(f"c{i}", f"Child {i}"), "parentFolderId": ME_INBOX} for i in range(3)]

        def child_folders(request: httpx.Request) -> httpx.Response:
            query = personal_api.query(request)
            assert query["$top"] == "2"
            skip = int(query.get("$skip", "0"))
            return json_response(page(children[skip:skip + 2]))

        personal_api.on("GET", f"/v1.0/users/{OID}/mailFolders/{ME_INBOX}/childFolders", child_folders)
        serve_me_delta(personal_api, {None: page([], delta_link=me_delta_url("D1"))})

        await personal.run_sync()

        assert {"c0", "c1", "c2"} <= set(self.db.record_groups)

    async def test_a_failed_child_folder_listing_fails_the_run(self, personal, personal_api) -> None:
        personal_api.on("GET", f"/v1.0/users/{OID}/mailFolders", page([{**folder(ME_INBOX, "Inbox"), "childFolderCount": 1}]))
        personal_api.on("GET", f"/v1.0/users/{OID}/mailFolders/{ME_INBOX}/childFolders", graph_error(500, "ErrorInternalServerError"))

        with pytest.raises(Exception, match="child folders"):
            await personal.run_sync()

    async def test_a_folder_deleted_in_outlook_is_removed_with_its_mail(self, personal, personal_api) -> None:
        personal_api.on("GET", f"/v1.0/users/{OID}/mailFolders", page([folder(ME_INBOX, "Inbox"), folder("old", "Old")]))
        serve_me_delta(personal_api, {None: page([message("m1", "Plan")], delta_link=me_delta_url("D1")),
                                      "D1": page([], delta_link=me_delta_url("D2"))})
        serve_me_delta(personal_api, {None: page([message("o1", "Old mail")], delta_link=me_delta_url("O1", "old"))}, "old")
        await personal.run_sync()
        assert "o1" in self.db.records
        personal_api.on("GET", f"/v1.0/users/{OID}/mailFolders", page([folder(ME_INBOX, "Inbox")]))

        await personal.run_sync()

        assert self.db.deleted_record_groups == ["old"]
        assert "o1" not in self.db.records
        assert "m1" in self.db.records

    async def test_deleted_items_and_the_folders_deleted_into_it_are_not_synced_unless_selected(
        self, personal, personal_api,
    ) -> None:
        folders = f"/v1.0/users/{OID}/mailFolders"
        personal_api.on("GET", folders, page([folder(ME_INBOX, "Inbox"), folder("old", "Old")]))
        serve_me_delta(personal_api, {None: page([], delta_link=me_delta_url("D1")), "D1": page([], delta_link=me_delta_url("D2"))})
        serve_me_delta(personal_api, {None: page([message("o1", "Old mail")], delta_link=me_delta_url("O1", "old"))}, "old")
        await personal.run_sync()
        assert "o1" in self.db.records

        # Deleting "Old" in Outlook moves it, id unchanged, under Deleted Items.
        trash = serve_deleted_items(personal_api, OID, child_count=1)
        personal_api.on("GET", folders, page([folder(ME_INBOX, "Inbox"), trash]))
        personal_api.on("GET", f"{folders}/{TRASH}/childFolders", page([{**folder("old", "Old"), "parentFolderId": TRASH}]))
        serve_me_delta(personal_api, {None: page([message("t1", "Trashed")], delta_link=me_delta_url("T1", TRASH))}, TRASH)
        await personal.run_sync()

        assert self.db.deleted_record_groups == ["old"]
        assert not {"o1", "t1"} & set(self.db.records)
        assert personal_api.calls("GET", f"{folders}/{TRASH}/childFolders") == []

        self.config_service.config["filters"] = {
            "sync": {"values": {"folders": {"operator": "in", "value": [TRASH], "type": "multiselect"}}},
        }
        await personal.run_sync()
        assert {TRASH, "old"} <= set(self.db.record_groups)
        assert "t1" in self.db.records

    @pytest.mark.parametrize("destination_first", [False, True])
    async def test_a_mail_moved_between_folders_keeps_its_record(self, personal, personal_api, destination_first) -> None:
        folders = [folder(ME_INBOX, "Inbox"), folder("archive", "Archive")]
        personal_api.on("GET", f"/v1.0/users/{OID}/mailFolders", page(folders[::-1] if destination_first else folders))
        serve_me_delta(personal_api, {
            None: page([message("m1", "Plan")], delta_link=me_delta_url("D1")),
            "D1": page([removed("m1")], delta_link=me_delta_url("D2")),
        })
        serve_me_delta(personal_api, {
            None: page([], delta_link=me_delta_url("A1", "archive")),
            "A1": page([message("m1", "Plan")], delta_link=me_delta_url("A2", "archive")),
        }, "archive")
        await personal.run_sync()
        before = self.db.records["m1"]

        await personal.run_sync()

        after = self.db.records["m1"]
        assert self.db.deleted_external == []
        assert (after.id, after.external_revision_id, after.version) == (before.id, before.external_revision_id, 0)
        assert after.external_record_group_id == "archive"
        assert await self.personal_link(personal) == me_delta_url("D2")

    async def test_the_first_run_after_the_upgrade_reads_mail_again_under_immutable_ids_once(
        self, personal, personal_api, checkpoints,
    ) -> None:
        serve_me_delta(personal_api, {None: page([message("old-m1", "Plan")], delta_link=me_delta_url("OLD"))})
        await personal.run_sync()
        forget_id_type_marker(checkpoints)
        serve_me_delta(personal_api, {
            None: page([message("m1", "Plan")], delta_link=me_delta_url("D1")),
            "D1": page([], delta_link=me_delta_url("D2")),
            "OLD": graph_error(500, "ErrorInternalServerError"),
        })

        await personal.run_sync()
        assert set(self.db.records) == {"m1"}
        assert id_type_marker(checkpoints, ME_INBOX) == "ImmutableId"

        await personal.run_sync()
        assert personal_api.query(personal_api.calls("GET", me_delta_path())[-1]).get("$deltatoken") == "D1"
        assert all('IdType="ImmutableId"' in r.headers.get("Prefer", "") for r in personal_api.graph_calls())

    async def test_a_token_rejected_during_sync_is_refreshed_and_the_call_repeated(self, personal, personal_api, monkeypatch) -> None:
        self.config_service.config["credentials"]["refresh_token"] = "rt-1"
        refreshes = []
        config = self.config_service.config

        class TokenRefresher:
            async def refresh_now(self, connector_id: str, connector_type: str, refresh_token: str) -> None:
                refreshes.append((connector_id, connector_type, refresh_token))
                config["credentials"]["access_token"] = delegated_token(2)

        monkeypatch.setattr(startup_service, "get_token_refresh_service", TokenRefresher)

        def expired_token_first(request: httpx.Request) -> httpx.Response:
            if bearer(request) == f"Bearer {delegated_token(1)}":
                return graph_error(401, "InvalidAuthenticationToken")
            return json_response(page([message("m1", "Plan")], delta_link=me_delta_url("D1")))

        serve_me_delta(personal_api, {None: expired_token_first})

        await personal.run_sync()

        assert refreshes == [("outlook-personal-1", "OUTLOOK PERSONAL", "rt-1")]
        assert "m1" in self.db.records
        assert await self.personal_link(personal) == me_delta_url("D1")

    async def test_an_attachment_rejected_for_an_expired_token_streams_after_a_refresh(
        self, personal, personal_api, monkeypatch,
    ) -> None:
        self.config_service.config["credentials"]["refresh_token"] = "rt-1"
        config = self.config_service.config

        class TokenRefresher:
            async def refresh_now(self, *_: str) -> None:
                config["credentials"]["access_token"] = delegated_token(2)

        monkeypatch.setattr(startup_service, "get_token_refresh_service", TokenRefresher)
        serve_me_delta(personal_api, {None: page([message("m1", "Plan", attachments=True)], delta_link=me_delta_url("D1"))})
        personal_api.on("GET", f"/v1.0/users/{OID}/messages/m1/attachments", page([file_attachment("a1")]))
        await personal.run_sync()

        def value(request: httpx.Request) -> httpx.Response:
            if bearer(request) == f"Bearer {delegated_token(1)}":
                return graph_error(401, "InvalidAuthenticationToken")
            return httpx.Response(200, content=b"%PDF-1.7")

        personal_api.on("GET", "/v1.0/me/messages/m1/attachments/a1/$value", value)

        response = await personal.stream_record(self.db.records["a1"])

        assert b"".join([chunk async for chunk in response.body_iterator]) == b"%PDF-1.7"

    async def test_throttled_mail_and_attachment_reads_are_retried_after_the_retry_after_delay(
        self, personal, personal_api, backoff_sleeps,
    ) -> None:
        def throttled(seconds: str) -> httpx.Response:
            return graph_error(429, "ApplicationThrottled", headers={"Retry-After": seconds})

        delta = page([message("m1", "Plan", attachments=True)], delta_link=me_delta_url("D1"))
        personal_api.on("GET", me_delta_path(), [throttled("5"), delta])
        personal_api.on("GET", f"/v1.0/users/{OID}/messages/m1/attachments", [throttled("6"), page([file_attachment("a1")])])

        await personal.run_sync()

        assert {5, 6} <= set(backoff_sleeps)
        assert {"m1", "a1"} <= set(self.db.records)
        assert await self.personal_link(personal) == me_delta_url("D1")

        personal_api.on("GET", "/v1.0/me/messages/m1/attachments/a1/$value",
                        [throttled("7"), httpx.Response(200, content=b"%PDF-1.7")])
        response = await personal.stream_record(self.db.records["a1"])

        assert b"".join([chunk async for chunk in response.body_iterator]) == b"%PDF-1.7"
        assert 7 in backoff_sleeps

    async def test_a_preview_asks_to_sign_in_again_when_the_token_is_gone_or_rejected(self, personal, personal_api) -> None:
        serve_me_delta(personal_api, {None: page([message("m1", "Plan")], delta_link=me_delta_url("D1"))})
        await personal.run_sync()
        personal_api.on("GET", f"/v1.0/users/{OID}/messages/m1", graph_error(401, "InvalidAuthenticationToken"))

        with pytest.raises(HTTPException) as rejected:
            await personal.stream_record(self.db.records["m1"])
        self.config_service.config["credentials"] = {}
        with pytest.raises(HTTPException) as signed_out:
            await personal.stream_record(self.db.records["m1"])

        assert (rejected.value.status_code, signed_out.value.status_code) == (409, 409)
        assert "Reconnect" in rejected.value.detail
        assert "Sign in again" in signed_out.value.detail

    async def test_a_skipped_folder_is_notified_once(self, personal, personal_api) -> None:
        personal_api.on("GET", f"/v1.0/users/{OID}/mailFolders", page([folder(ME_INBOX, "Inbox"), folder("old", "Old")]))
        serve_me_delta(personal_api, {None: page([message("m1", "Plan")], delta_link=me_delta_url("D1"))})
        personal_api.on("GET", me_delta_path("old"), graph_error(500, "ErrorInternalServerError"))

        await personal.run_sync()

        (note,) = await notes(personal)
        assert note["message"].startswith("Skipped after errors: folders 1.")


    async def test_a_refresh_microsoft_rejects_is_tried_once_and_stops_the_run(self, personal, personal_api, monkeypatch) -> None:
        self.config_service.config["credentials"]["refresh_token"] = "rt-revoked"
        refreshes = []

        class TokenRefresher:
            async def refresh_now(self, *args: str) -> None:
                refreshes.append(args)
                raise RuntimeError("AADSTS70008: the refresh token has expired")

        monkeypatch.setattr(startup_service, "get_token_refresh_service", TokenRefresher)
        personal_api.on("GET", f"/v1.0/users/{OID}/mailFolders", page([folder(ME_INBOX, "Inbox"), folder("sent", "Sent")]))
        personal_api.on("GET", me_delta_path(), graph_error(401, "InvalidAuthenticationToken"))
        personal_api.on("GET", me_delta_path("sent"), graph_error(401, "InvalidAuthenticationToken"))

        with pytest.raises(Exception, match="401"):  # the Graph code is not in newer Kiota error text
            await personal.run_sync()

        assert len(refreshes) == 1, "each rejected refresh counts toward deactivating the connector"
        assert personal_api.calls("GET", me_delta_path("sent")) == [], "the run stops at the first one"
        (note,) = await notes(personal)
        assert note["type"] == NotificationType.CONNECTOR_AUTH_ERROR

    async def test_a_token_swap_closes_the_client_replaced_by_the_swap_before(self, personal) -> None:
        def http(client: Any) -> httpx.AsyncClient:  # noqa: ANN401
            return client.get_client().client.request_adapter._http_client

        first = personal.external_client
        self.config_service.config["credentials"]["access_token"] = delegated_token(2)
        await personal._get_fresh_graph_client()
        second = personal.external_client
        assert not http(first).is_closed, "a preview may still stream through it"

        self.config_service.config["credentials"]["access_token"] = delegated_token(3)
        await personal._get_fresh_graph_client()

        assert http(first).is_closed
        assert not http(second).is_closed
        await personal.cleanup()
        assert http(second).is_closed

    async def test_an_upgraded_mailbox_drops_deleted_items_synced_before(self, personal, personal_api, checkpoints) -> None:
        folders = f"/v1.0/users/{OID}/mailFolders"
        trash = serve_deleted_items(personal_api, OID)
        personal_api.on("GET", folders, page([folder(ME_INBOX, "Inbox"), trash]))
        serve_me_delta(personal_api, {None: page([], delta_link=me_delta_url("D1")), "D1": page([], delta_link=me_delta_url("D2"))})
        serve_me_delta(personal_api, {None: page([message("t1", "Trashed")], delta_link=me_delta_url("T1", TRASH))}, TRASH)
        self.config_service.config["filters"] = {
            "sync": {"values": {"folders": {"operator": "in", "value": [ME_INBOX, TRASH], "type": "multiselect"}}},
        }
        await personal.run_sync()
        assert "t1" in self.db.records
        # An earlier version synced Deleted Items by default and kept no folder inventory.
        self.config_service.config["filters"] = {}
        forget_folder_inventory(checkpoints)

        await personal.run_sync()

        assert TRASH not in self.db.record_groups
        assert "t1" not in self.db.records


class TestConnectorMetadata:
    @pytest.mark.parametrize("connector_class", [OutlookConnector, OutlookIndividualConnector])
    def test_both_connectors_declare_a_rate_limit_info_and_a_docs_page(self, connector_class) -> None:
        metadata = connector_class._connector_metadata

        assert metadata["resilienceConfig"]["rate_limit"] == OutlookSyncConfig.GRAPH_RATE_LIMIT
        assert metadata["connectorInfo"]
        urls = [link["url"] for link in metadata["config"]["documentationLinks"]]
        page = "outlook-personal" if connector_class is OutlookIndividualConnector else "outlook"
        assert f"https://docs.pipeshub.com/connectors/microsoft-365/{page}" in urls

    def test_team_shows_no_redirect_uri_it_does_not_have(self) -> None:
        assert OutlookConnector._connector_metadata["config"]["auth"]["conditionalDisplay"] == {}


def real_graph_error_text(status: int, code: str, *, wrapped_as: str = "kiota") -> str:
    """A failed call's error text as the data source passes it on: "Outlook API call failed: "
    and the str() of the SDK's ODataError, or an error ending in "(status NNN)"."""
    from msgraph.generated.models.o_data_errors.main_error import MainError
    from msgraph.generated.models.o_data_errors.o_data_error import ODataError

    if wrapped_as == "status":
        return f"Outlook API call failed: {code}: The request failed (status {status})"
    error = ODataError()
    error.response_status_code = status
    error.error = MainError(code=code, message="error")
    return f"Outlook API call failed: {error}"


class TestGraphErrorText:
    @pytest.mark.parametrize("wrapped_as", ["kiota", "status"])
    def test_each_matcher_reads_the_status_from_the_real_error_text(self, wrapped_as) -> None:
        from app.connectors.sources.microsoft.common.outlook_constants import (
            graph_error_status,
            is_expired_delta_link_error,
            is_folder_not_found_error,
            is_no_mailbox_error,
        )

        assert not is_no_mailbox_error(real_graph_error_text(404, "ErrorItemNotFound", wrapped_as=wrapped_as))
        assert graph_error_status(real_graph_error_text(403, "ErrorAccessDenied", wrapped_as=wrapped_as)) == 403
        assert graph_error_status(real_graph_error_text(401, "InvalidAuthenticationToken", wrapped_as=wrapped_as)) == 401
        assert is_folder_not_found_error(real_graph_error_text(404, "itemNotFound", wrapped_as=wrapped_as))
        assert is_expired_delta_link_error(real_graph_error_text(410, "Gone", wrapped_as=wrapped_as))
        assert not is_folder_not_found_error(real_graph_error_text(500, "ErrorInternalServerError", wrapped_as=wrapped_as))
        assert not is_expired_delta_link_error(real_graph_error_text(500, "ErrorInternalServerError", wrapped_as=wrapped_as))
        assert graph_error_status("no status here") is None

    def test_a_mail_missing_in_outlook_streams_as_404_with_either_text(self) -> None:
        from app.connectors.sources.microsoft.common.outlook_folders import (
            graph_stream_error,
        )

        for wrapped_as in ("kiota", "status"):
            error = Exception(real_graph_error_text(404, "ErrorItemNotFound", wrapped_as=wrapped_as))
            assert graph_stream_error(error, "Outlook").status_code == 404
