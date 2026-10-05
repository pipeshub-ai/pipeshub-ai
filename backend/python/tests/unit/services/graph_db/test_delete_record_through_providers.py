"""DELETE /api/v1/records/{record_id} driven through the real Neo4j and ArangoDB
providers over a fake driver. The access check is not stubbed, so the tests pin
what it returns for each kind of stored record, that synced records are refused
with no destructive statement reaching the database, and that knowledge-base
uploads are deleted only for a write role in the caller's org.
"""

import inspect
import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

import app.connectors.api.router as router_mod
import app.edition_config  # noqa: F401  (binds the edition seams before the router loads)
from app.config.constants.arangodb import GraphNames
from app.connectors.api.router import delete_record
from app.schema.arango.graph import EDGE_DEFINITIONS
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

ORG_A = "org-A"
ORG_B = "org-B"
RECORD_ID = "rec-1"
USER = {"id": "ukey-a", "_key": "ukey-a", "userId": "user-a", "orgId": ORG_A, "email": "a@example.com"}
KB_APP = {"id": "kb-1", "_key": "kb-1", "name": "Handbook", "orgId": ORG_A, "type": "KB"}


def _record(**fields: Any) -> dict:
    return {
        "recordName": "doc",
        "externalRecordId": "ext-1",
        "recordType": "FILE",
        "orgId": ORG_A,
        "connectorId": "kb-1",
        "virtualRecordId": "vr-1",
        "version": 1,
        **fields,
    }


KB_FILE = _record(origin="UPLOAD", connectorName="KB")
KB_FOLDER = _record(origin="UPLOAD", connectorName="KB", mimeType="application/vnd.folder")
KB_LEGACY_NO_CONNECTOR_NAME = _record(origin="UPLOAD")
ARTIFACT = _record(origin="UPLOAD", connectorName="CODING_SANDBOX", recordType="ARTIFACT", connectorId="coding_sandbox_org-A")
CHAT_ATTACHMENT = _record(origin="UPLOAD", connectorName="ATTACHMENTS", connectorId="attachments_org-A")
DRIVE_FILE = _record(origin="CONNECTOR", connectorName="DRIVE", connectorId="conn-1")
GMAIL_MAIL = _record(origin="CONNECTOR", connectorName="GMAIL", recordType="MAIL", connectorId="conn-1")
OUTLOOK_MAIL = _record(origin="CONNECTOR", connectorName="OUTLOOK", recordType="MAIL", connectorId="conn-1")
LOCAL_FS_FILE = _record(origin="CONNECTOR", connectorName="LOCAL_FS", connectorId="conn-1")
CONFLUENCE_PAGE = _record(origin="CONNECTOR", connectorName="CONFLUENCE", recordType="WEBPAGE", connectorId="conn-1")
JIRA_TICKET = _record(origin="CONNECTOR", connectorName="JIRA", recordType="TICKET", connectorId="conn-1")
LEGACY_NO_ORIGIN = _record(connectorName="DRIVE", connectorId="conn-1")
LEGACY_NEITHER = _record(connectorId="conn-1")

KB_ACCESS = [{"type": "KNOWLEDGE_BASE", "source": KB_APP, "role": "WRITER", "folder": None}]
DIRECT_ACCESS = [{"type": "DIRECT", "source": USER, "role": "OWNER"}]
KB_CONTEXT = {"kb_id": "kb-1", "kb_name": "Handbook", "org_id": ORG_A}


class _Neo4jDriver:
    def __init__(self, record: dict | None, access: list | None, kb_context: dict | None, kb_role: str | None) -> None:
        self.record, self.access, self.kb_context, self.kb_role = record, access, kb_context, kb_role
        self.statements: list[tuple[str, dict]] = []

    async def execute_query(self, query: str, parameters: dict | None = None, txn_id: str | None = None) -> list:
        self.statements.append((query, parameters or {}))
        if "MATCH (u:User {userId: $user_id})" in query:
            return [{"u": dict(USER)}]
        if "RETURN allAccess" in query:
            return [{"allAccess": self.access}] if self.access else []
        if "AS metadata" in query:
            return [{"metadata": {"departments": [], "categories": [], "topics": [], "languages": []}}]
        if "AS kb_context" in query:
            return [{"kb_context": self.kb_context}] if self.kb_context else []
        if "RETURN role AS role" in query:
            return [{"role": self.kb_role}] if self.kb_role else []
        label = re.search(r"MATCH \(n:(\w+) \{id: \$key\}\)", query)
        if label and "DELETE" not in query and label.group(1) == "Record" and self.record is not None:
            return [{"n": {"id": RECORD_ID, **self.record}}]
        return []

    @property
    def destructive(self) -> list[str]:
        return [q for q, _ in self.statements if re.search(r"\bDELETE\b", q)]


class _ArangoDriver:
    def __init__(self, record: dict | None, access: list | None, kb_context: dict | None, kb_role: str | None) -> None:
        self.record, self.access, self.kb_context, self.kb_role = record, access, kb_context, kb_role
        self.statements: list[tuple[str, dict]] = []

    async def get_document(self, collection: str, key: str, txn_id: str | None = None, **_: Any) -> dict | None:
        if collection == "records" and self.record is not None:
            return {"_key": RECORD_ID, "_id": f"records/{RECORD_ID}", **self.record}
        return None

    async def execute_aql(self, query: str, bind_vars: dict | None = None, txn_id: str | None = None, **_: Any) -> list:
        self.statements.append((query, bind_vars or {}))
        if "directAccessPermissionEdge" in query:
            return [self.access if self.access else None]
        if "FILTER user.userId == @user_id" in query:
            return [dict(USER)]
        if "LET departments" in query:
            return [{"departments": [], "categories": [], "topics": [], "languages": []}]
        if "kb_candidate" in query:
            return [self.kb_context]
        if "all_roles" in query:
            return [self.kb_role]
        return []

    async def get_graph(self, graph_name: str) -> dict | None:
        # GET /_api/gharial/{graph} as ArangoDB answers it (and None for 404, as the client maps it).
        if graph_name != GraphNames.KNOWLEDGE_GRAPH.value:
            return None
        return {
            "error": False,
            "code": 200,
            "graph": {
                "_key": graph_name,
                "_id": f"_graphs/{graph_name}",
                "_rev": "_jT3hU2a---",
                "name": graph_name,
                "edgeDefinitions": [
                    {
                        "collection": ed["edge_collection"],
                        "from": ed["from_vertex_collections"],
                        "to": ed["to_vertex_collections"],
                    }
                    for ed in EDGE_DEFINITIONS
                ],
                "orphanCollections": [],
            },
        }

    @property
    def destructive(self) -> list[str]:
        return [q for q, _ in self.statements if re.search(r"\bREMOVE\b", q)]

    @property
    def edge_collections_cleared(self) -> set[str]:
        return {
            bind["@edge_collection"]
            for q, bind in self.statements
            if "@edge_collection" in bind and re.search(r"\bREMOVE\b", q)
        }


def _neo4j(record: dict | None, access: list | None, kb_context: dict | None, kb_role: str | None) -> tuple[Any, Any]:
    provider = Neo4jProvider(MagicMock(), MagicMock())
    driver = _Neo4jDriver(record, access, kb_context, kb_role)
    provider.client = driver
    provider._get_user_app_ids = AsyncMock(return_value=["conn-1"])
    provider.get_user_by_user_id = AsyncMock(return_value=dict(USER))
    return provider, driver


def _arango(record: dict | None, access: list | None, kb_context: dict | None, kb_role: str | None) -> tuple[Any, Any]:
    provider = ArangoHTTPProvider(MagicMock(), MagicMock())
    driver = _ArangoDriver(record, access, kb_context, kb_role)
    provider.http_client = driver
    provider._get_user_app_ids = AsyncMock(return_value=["conn-1"])
    return provider, driver


BACKENDS = [pytest.param(_neo4j, id="neo4j"), pytest.param(_arango, id="arango")]


def _request(user_id: str = "user-a", org_id: str = ORG_A) -> MagicMock:
    user = {"userId": user_id, "orgId": org_id}
    req = MagicMock()
    req.state.user.get = lambda k, default=None: user.get(k, default)
    req.app.container.logger.return_value = MagicMock()
    return req


async def _delete(provider: Any, kafka: AsyncMock, org_id: str = ORG_A) -> dict:
    return await delete_record(RECORD_ID, _request(org_id=org_id), provider, kafka)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize(
    "record",
    [KB_FILE, KB_FOLDER, KB_LEGACY_NO_CONNECTOR_NAME, ARTIFACT, CHAT_ATTACHMENT, DRIVE_FILE, GMAIL_MAIL,
     JIRA_TICKET, LEGACY_NO_ORIGIN, LEGACY_NEITHER],
    ids=["kb-file", "kb-folder", "kb-legacy-no-connector-name", "artifact", "chat-attachment", "drive",
         "gmail", "jira", "legacy-no-origin", "legacy-neither"],
)
async def test_access_result_carries_the_stored_origin_and_connector_name(backend: Any, record: dict) -> None:
    provider, _ = backend(record, DIRECT_ACCESS, None, None)

    result = await provider.check_record_access_with_details(user_id="user-a", org_id=ORG_A, record_id=RECORD_ID)

    assert isinstance(result, dict)
    returned = result["record"]
    assert returned.get("origin") == record.get("origin")
    assert returned.get("connectorName") == record.get("connectorName")
    assert returned.get("orgId") == ORG_A
    assert returned.get("id") == RECORD_ID


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", BACKENDS)
async def test_access_result_is_none_without_an_access_path_or_without_the_record(backend: Any) -> None:
    provider, _ = backend(KB_FILE, None, None, None)
    assert await provider.check_record_access_with_details("user-a", ORG_A, RECORD_ID) is None

    provider, _ = backend(None, DIRECT_ACCESS, None, None)
    assert await provider.check_record_access_with_details("user-a", ORG_A, RECORD_ID) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize(
    "record",
    [DRIVE_FILE, GMAIL_MAIL, OUTLOOK_MAIL, LOCAL_FS_FILE, CONFLUENCE_PAGE, JIRA_TICKET, LEGACY_NO_ORIGIN, LEGACY_NEITHER],
    ids=["drive", "gmail", "outlook", "local-fs", "confluence", "jira", "legacy-no-origin", "legacy-neither"],
)
@pytest.mark.parametrize("role", ["OWNER", "WRITER", "READER"])
async def test_synced_record_is_refused_whatever_the_callers_role(backend: Any, record: dict, role: str) -> None:
    provider, driver = backend(record, [{"type": "DIRECT", "source": USER, "role": role}], None, None)
    kafka = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await _delete(provider, kafka)

    assert exc.value.status_code == 403
    assert "source app" in exc.value.detail
    assert driver.destructive == []
    kafka.publish_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize(
    "record", [KB_FILE, KB_FOLDER, KB_LEGACY_NO_CONNECTOR_NAME], ids=["kb-file", "kb-folder", "kb-legacy"]
)
@pytest.mark.parametrize("kb_role", ["OWNER", "WRITER", "FILEORGANIZER"])
async def test_kb_upload_is_deleted_for_a_writer(backend: Any, record: dict, kb_role: str) -> None:
    provider, driver = backend(record, KB_ACCESS, KB_CONTEXT, kb_role)
    kafka = AsyncMock()

    result = await _delete(provider, kafka)

    assert result["success"] is True
    assert driver.destructive, "nothing was deleted from the graph"
    kafka.publish_event.assert_awaited_once()
    topic, event = kafka.publish_event.await_args.args
    assert topic == "record-events"
    assert event["eventType"] == "deleteRecord"
    assert event["payload"]["recordId"] == RECORD_ID
    assert event["payload"]["virtualRecordId"] == "vr-1"


@pytest.mark.asyncio
async def test_arango_kb_upload_delete_clears_every_edge_collection_of_the_graph() -> None:
    """Enrichment links a record to taxonomy nodes the fixed KB edge list never named."""
    provider, driver = _arango(KB_FILE, KB_ACCESS, KB_CONTEXT, "WRITER")

    await _delete(provider, AsyncMock())

    assert {ed["edge_collection"] for ed in EDGE_DEFINITIONS} <= driver.edge_collections_cleared


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("kb_role", ["READER", "COMMENTER", None, "reader", "writer", "ORGANIZER", ""])
async def test_kb_upload_is_refused_without_a_write_role(backend: Any, kb_role: str | None) -> None:
    provider, driver = backend(KB_FILE, KB_ACCESS, KB_CONTEXT, kb_role)
    kafka = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await _delete(provider, kafka)

    assert exc.value.status_code == 403
    assert driver.destructive == []
    kafka.publish_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("record", [ARTIFACT, CHAT_ATTACHMENT], ids=["artifact", "chat-attachment"])
async def test_upload_outside_a_kb_is_404_and_left_in_place(backend: Any, record: dict) -> None:
    """Artifacts and chat attachments pass the route rule (origin UPLOAD) and stop at the KB lookup."""
    provider, driver = backend(record, DIRECT_ACCESS, None, "OWNER")
    kafka = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await _delete(provider, kafka)

    assert exc.value.status_code == 404
    assert "Knowledge base context not found" in exc.value.detail
    assert driver.destructive == []
    kafka.publish_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_neo4j_kb_delete_invalidates_the_kb_records_cache() -> None:
    provider, _ = _neo4j(KB_FILE, KB_ACCESS, KB_CONTEXT, "OWNER")

    with patch.object(router_mod, "notify_kb_records_changed", new_callable=AsyncMock) as notify:
        await _delete(provider, AsyncMock())

    notify.assert_awaited_once_with("kb-1", ORG_A)


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("record", [KB_FILE, DRIVE_FILE], ids=["kb", "drive"])
async def test_other_orgs_record_without_an_access_path_is_404(backend: Any, record: dict) -> None:
    provider, driver = backend({**record, "orgId": ORG_B}, None, KB_CONTEXT, "OWNER")
    kafka = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await _delete(provider, kafka)

    assert exc.value.status_code == 404
    assert exc.value.detail == "You do not have access to this record"
    assert driver.destructive == []


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", BACKENDS)
async def test_other_orgs_kb_record_is_404_even_when_an_access_path_exists(backend: Any) -> None:
    """Org scoping must not rest on the access check alone: the provider re-checks orgId."""
    provider, driver = backend({**KB_FILE, "orgId": ORG_B}, KB_ACCESS, KB_CONTEXT, "OWNER")
    kafka = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await _delete(provider, kafka)

    assert exc.value.status_code == 404
    assert driver.destructive == []
    kafka.publish_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", BACKENDS)
async def test_missing_record_and_foreign_record_are_indistinguishable(backend: Any) -> None:
    missing, _ = backend(None, None, None, None)
    foreign, _ = backend({**KB_FILE, "orgId": ORG_B}, None, None, None)

    outcomes = []
    for provider in (missing, foreign):
        with pytest.raises(HTTPException) as exc:
            await _delete(provider, AsyncMock())
        outcomes.append((exc.value.status_code, exc.value.detail))

    assert outcomes[0] == outcomes[1]
    assert outcomes[0][0] == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", BACKENDS)
async def test_kb_record_without_org_id_is_not_deletable(backend: Any) -> None:
    record = {k: v for k, v in KB_FILE.items() if k != "orgId"}
    provider, driver = backend(record, KB_ACCESS, KB_CONTEXT, "OWNER")

    with pytest.raises(HTTPException) as exc:
        await _delete(provider, AsyncMock())

    assert exc.value.status_code == 404
    assert driver.destructive == []


def _typed(record: dict) -> MagicMock:
    typed = MagicMock()
    typed.id = RECORD_ID
    typed.org_id = record.get("orgId")
    return typed


@pytest.mark.asyncio
async def test_neo4j_sync_delete_by_external_id_still_removes_a_synced_record() -> None:
    provider, driver = _neo4j(OUTLOOK_MAIL, None, None, None)
    provider.get_record_by_external_id = AsyncMock(return_value=_typed(OUTLOOK_MAIL))

    await provider.delete_record_by_external_id("conn-1", "ext-1", "user-a")

    assert any("DETACH DELETE" in q for q in driver.destructive)


@pytest.mark.asyncio
async def test_arango_sync_delete_by_external_id_reaches_the_outlook_branch() -> None:
    provider, _ = _arango(OUTLOOK_MAIL, None, None, None)
    provider.get_record_by_external_id = AsyncMock(return_value=_typed(OUTLOOK_MAIL))
    provider.delete_outlook_record = AsyncMock(return_value={"success": True})

    await provider.delete_record_by_external_id("conn-1", "ext-1", "user-a")

    provider.delete_outlook_record.assert_awaited_once()


@pytest.mark.asyncio
async def test_arango_sync_delete_of_a_record_without_org_raises_instead_of_passing_silently() -> None:
    record = {k: v for k, v in OUTLOOK_MAIL.items() if k != "orgId"}
    provider, _ = _arango(record, None, None, None)
    provider.get_record_by_external_id = AsyncMock(return_value=_typed(record))
    provider.delete_outlook_record = AsyncMock(return_value={"success": True})

    with pytest.raises(Exception, match="Record not found"):
        await provider.delete_record_by_external_id("conn-1", "ext-1", "user-a")

    provider.delete_outlook_record.assert_not_awaited()


@pytest.mark.parametrize("provider_cls", [Neo4jProvider, ArangoHTTPProvider])
def test_delete_record_takes_org_id_before_transaction(provider_cls: type) -> None:
    assert list(inspect.signature(provider_cls.delete_record).parameters) == [
        "self", "record_id", "user_id", "org_id", "transaction",
    ]
