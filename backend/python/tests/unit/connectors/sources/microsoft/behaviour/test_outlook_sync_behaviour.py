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
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Optional
from unittest.mock import MagicMock

import httpx
import pytest
from azure.identity.aio import ClientSecretCredential
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

from app.connectors.core.base.sync_point.sync_point import (
    generate_record_sync_point_key,
)
from app.connectors.sources.microsoft.outlook.connector import OutlookConnector
from app.connectors.sources.microsoft.outlook_individual.connector import (
    OutlookIndividualConnector,
)
from app.models.entities import RecordGroupType, RecordType
from app.models.permission import EntityType, PermissionType

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

    def add_mailbox_user(self, email: str, user_id: str) -> None:
        self.active_users.append(SimpleNamespace(email=email, user_id=user_id, source_user_id=None))

    async def delete_record_by_external_id(self, connector_id: str, external_id: str, user_id: str) -> None:
        self.deleted_external.append(external_id)
        self.records.pop(external_id, None)

    async def get_record_by_conversation_index(self, *_: object) -> Optional[Record]:
        return None


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


def removed(message_id: str) -> dict[str, Any]:
    return {"id": message_id, "@removed": {"reason": "deleted"}}


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
        grants = {(p.email, p.type) for p in db.record_permissions["m1"]}
        assert grants == {(ANA, PermissionType.OWNER), (BO, PermissionType.READ)}
        assert db.record_groups[INBOX].group_type == RecordGroupType.MAILBOX
        assert [(p.email, p.type) for p in db.record_group_permissions[INBOX]] == [(ANA, PermissionType.OWNER)]
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

    async def test_a_failed_delta_page_keeps_the_folder_checkpoint(self, connector, api, db, checkpoints) -> None:
        serve_delta(api, {
            None: page([message("m1", "Plan")], delta_link=delta_url("D1")),
            "D1": page([message("m2", "New")], next_link=delta_url("p2")),
            "p2": graph_error(500, "ErrorInternalServerError"),
        })
        await connector.run_sync()

        await connector.run_sync()

        assert await folder_link(connector) == delta_url("D1")
        assert "m2" not in db.records, "the half-read window is retried next run instead of being half-applied"

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

    async def test_attachments_are_saved_under_their_mail_with_the_mails_access(self, connector, api, db) -> None:
        serve_delta(api, {None: page([message("m1", "Plan", attachments=True)], delta_link=delta_url("D1"))})
        api.on("GET", "/v1.0/users/u-ana/messages/m1/attachments", page([{
            "@odata.type": "#microsoft.graph.fileAttachment", "id": "a1", "name": "plan.pdf",
            "contentType": "application/pdf", "size": 10, "lastModifiedDateTime": SENT, "isInline": False,
        }]))

        await connector.run_sync()

        files = [r for r in db.records.values() if r.record_type == RecordType.FILE]
        assert [f.record_name for f in files] == ["plan.pdf"]
        assert {p.email for p in db.record_permissions[files[0].external_record_id]} == {ANA, BO}

    @pytest.mark.xfail(strict=True, reason=(
        "A mail change that fails to save (here, a delete hitting a database error) is only logged, and the "
        "folder's delta link still moves on, so the change is lost for good and the deleted mail stays "
        "searchable (outlook/connector.py:2145-2189, 383)"))
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

    @pytest.mark.xfail(strict=True, reason=(
        "When a mail's attachment list can't be read, the mail is saved without them and the folder's delta link "
        "still moves on, so those attachments are never synced unless the mail changes (outlook/connector.py:2511)"))
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

    async def test_group_conversation_posts_are_saved_as_group_mail(self, connector, api, db) -> None:
        serve_group(api, [member("u-ana", ANA)], threads=page([thread("t1", "Launch")]))
        api.on("GET", f"/v1.0/groups/{GROUP}/threads/t1/posts", page([post("p1")]))

        await connector.run_sync()

        (record,) = stored_by_type(db, RecordType.GROUP_MAIL).values()
        assert (record.external_record_id, record.record_name, record.external_record_group_id) == ("p1", "Launch", GROUP)
        assert [(p.entity_type, p.external_id) for p in db.record_permissions["p1"]] == [(EntityType.GROUP, GROUP)]

    @pytest.mark.xfail(strict=True, reason=(
        "When a group's member list can't be read, the group is saved with no members, so everyone loses the "
        "access the group gives (outlook/connector.py:858-894, 753-772)"))
    async def test_a_failed_member_read_keeps_the_stored_members(self, connector, api, db) -> None:
        serve_group(api, graph_error(500, "ErrorInternalServerError"))
        db.user_groups[GROUP] = [ANA]

        await connector.run_sync()

        assert db.user_groups[GROUP] == [ANA]

    @pytest.mark.xfail(strict=True, reason=(
        "When a later page of a group's members can't be read, the group is saved with only the members read so "
        "far, removing the rest (outlook/connector.py:874-880)"))
    async def test_a_failed_later_member_page_keeps_the_stored_members(self, connector, api, db) -> None:
        def members(request: httpx.Request) -> httpx.Response:
            if api.query(request).get("$skiptoken") == "2":
                return graph_error(500, "ErrorInternalServerError")
            return json_response(page([member("u-ana", ANA)], next_link=f"{GRAPH}/groups/{GROUP}/transitiveMembers?$skiptoken=2"))

        serve_group(api, members)
        db.user_groups[GROUP] = [ANA, BO]

        await connector.run_sync()

        assert db.user_groups[GROUP] == [ANA, BO]

    @pytest.mark.xfail(strict=True, reason=(
        "Group members are kept only if found in a user list read a second time; when that second read fails, "
        "every group is saved with no members (outlook/connector.py:440-465, 760)"))
    async def test_a_failed_user_list_refresh_does_not_empty_every_group(self, connector, api, db) -> None:
        serve_group(api, [member("u-ana", ANA)])
        api.on("GET", "/v1.0/users", [page([graph_user("u-ana", ANA)]), graph_error(500, "ErrorInternalServerError")])
        db.user_groups[GROUP] = [ANA]

        await connector.run_sync()

        assert db.user_groups[GROUP] == [ANA]

    @pytest.mark.xfail(strict=True, reason=(
        "A group deleted in Microsoft 365 is never removed: groups are read as a full list, so the 'deleted' branch "
        "never runs and the old members keep access to the group mailbox (outlook/connector.py:694)"))
    async def test_a_group_deleted_in_microsoft_365_is_removed(self, connector, api, db) -> None:
        serve_group(api, [member("u-ana", ANA)])
        await connector.run_sync()
        api.on("GET", "/v1.0/groups", page([]))

        await connector.run_sync()

        assert GROUP in db.deleted_groups


class TestGroupConversationCheckpoints:
    @pytest.mark.xfail(strict=True, reason=(
        "When a thread's posts can't be read, the group's 'synced up to' time still moves forward, so those posts "
        "are never read again (outlook/connector.py:1191, 1050)"))
    async def test_posts_that_failed_to_load_are_read_on_the_next_run(self, connector, api, db) -> None:
        serve_group(api, [member("u-ana", ANA)], threads=page([thread("t1", "Launch")]))
        posts = f"/v1.0/groups/{GROUP}/threads/t1/posts"
        api.on("GET", posts, graph_error(500, "ErrorInternalServerError"))
        await connector.run_sync()
        api.on("GET", posts, page([post("p1")]))

        await connector.run_sync()

        assert "p1" in db.records

    @pytest.mark.xfail(strict=True, reason=(
        "Only the first page of a group's threads is read: the next-page link is never followed, and the 'synced "
        "up to' time still moves forward, so older threads beyond the first page are never synced "
        "(outlook/connector.py:1069-1099)"))
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

        await personal.run_sync()

        assert await self.personal_link(personal) == me_delta_url("D1")
