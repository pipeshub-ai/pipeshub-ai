"""Unit tests for `caller_can_read_virtual_record`, and for when a graph grant on
chat content counts."""

import logging
from unittest.mock import AsyncMock

import pytest

from app.modules.authz.chat_content_access import (
    NoChatContentAccess,
    resolve_read_access,
)
from app.schema.arango.edges import permissions_schema
from app.utils.error_class import mark_error
from app.utils.record_access import (
    SERVICE_ACCOUNT_UPLOAD_PERMISSION_TYPE,
    caller_can_read_virtual_record,
    service_account_upload_permission_edges,
)
from tests.support.log_capture import failure_line
from tests.support.record_access import InMemoryRecordAccess

LOGGER = logging.getLogger("test")


def _graph(
    *, record_ids: list[str] | None = None, allowed: bool = True
) -> tuple[AsyncMock, InMemoryRecordAccess]:
    graph = AsyncMock()
    graph.get_records_by_virtual_record_id.return_value = record_ids if record_ids is not None else ["rec-1"]
    access = InMemoryRecordAccess().user("user-1", "org-1").user("sa-1", "org-1").record("rec-1", "org-1")
    if allowed:
        access.grant("rec-1", user="user-1")
    return access.serve(graph), access


class TestCallerCanReadVirtualRecord:
    async def test_grants_when_any_owning_record_is_readable(self) -> None:
        graph, access = _graph()

        assert await caller_can_read_virtual_record(
            graph, user_id="user-1", org_id="org-1", virtual_record_id="vrid-1", logger=LOGGER,
        )

        assert access.asked_about == [("user-1", "org-1", "rec-1")]

    async def test_denies_when_the_access_check_returns_nothing(self) -> None:
        graph, access = _graph(allowed=False)

        assert not await caller_can_read_virtual_record(
            graph, user_id="user-1", org_id="org-1", virtual_record_id="vrid-1", logger=LOGGER,
        )

    async def test_denies_without_a_user(self) -> None:
        graph, access = _graph()

        assert not await caller_can_read_virtual_record(
            graph, user_id="", org_id="org-1", virtual_record_id="vrid-1", logger=LOGGER,
        )
        graph.get_records_by_virtual_record_id.assert_not_called()

    async def test_denies_when_no_record_owns_the_virtual_id(self) -> None:
        graph, access = _graph(record_ids=[])

        assert not await caller_can_read_virtual_record(
            graph, user_id="user-1", org_id="org-1", virtual_record_id="vrid-1", logger=LOGGER,
        )
        assert access.asked_about == []

    async def test_denies_when_the_lookup_fails(self) -> None:
        graph, access = _graph()
        graph.get_records_by_virtual_record_id.side_effect = RuntimeError("graph down")

        assert not await caller_can_read_virtual_record(
            graph, user_id="user-1", org_id="org-1", virtual_record_id="vrid-1", logger=LOGGER,
        )
        assert access.asked_about == []

    @pytest.mark.parametrize("stamped", [False, True], ids=["unrecorded", "db_layer_recorded"])
    async def test_denies_when_the_access_check_cannot_be_made_and_logs_it_without_its_text(
        self, caplog: pytest.LogCaptureFixture, stamped: bool,
    ) -> None:
        """At WARNING either way: db.query.failed stamps the class, not a level already logged."""
        down = ConnectionError("graph down at 10.0.0.7:8529")
        if stamped:
            mark_error(down, "unavailable")
        graph, access = _graph()
        access.fail_with(down)

        with caplog.at_level(logging.DEBUG, logger=LOGGER.name):
            assert not await caller_can_read_virtual_record(
                graph, user_id="user-1", org_id="org-1", virtual_record_id="vrid-1", logger=LOGGER,
            )

        fields = failure_line(caplog.records, "record_access.check_failed", "unavailable", logging.WARNING, "10.0.0.7")
        assert fields["record_id"] == "rec-1"

    @pytest.mark.parametrize("edge", [{"type": "ORGANIZATION", "role": "READER"}, None], ids=["granted", "not_granted"])
    async def test_a_service_account_whose_access_check_fails_still_gets_the_org_grant_answer(self, edge: dict | None) -> None:
        graph, access = _graph()
        access.fail_with(ConnectionError("graph down"))
        graph.get_edge.return_value = edge

        assert await caller_can_read_virtual_record(
            graph, user_id="sa-1", org_id="org-1", virtual_record_id="vrid-1", logger=LOGGER,
            is_service_account=True,
        ) is (edge is not None)

    async def test_service_account_reads_an_org_granted_record(self) -> None:
        graph, access = _graph(allowed=False)
        graph.get_edge.return_value = {"type": "ORGANIZATION", "role": "READER"}

        assert await caller_can_read_virtual_record(
            graph, user_id="sa-1", org_id="org-1", virtual_record_id="vrid-1", logger=LOGGER,
            is_service_account=True,
        )

    async def test_service_account_cannot_read_a_private_record(self) -> None:
        graph, access = _graph(allowed=False)
        graph.get_edge.return_value = None

        assert not await caller_can_read_virtual_record(
            graph, user_id="sa-1", org_id="org-1", virtual_record_id="vrid-1", logger=LOGGER,
            is_service_account=True,
        )

    async def test_org_edge_does_not_grant_a_regular_user(self) -> None:
        graph, access = _graph(allowed=False)
        graph.get_edge.return_value = {"type": "ORGANIZATION", "role": "READER"}

        assert not await caller_can_read_virtual_record(
            graph, user_id="user-1", org_id="org-1", virtual_record_id="vrid-1", logger=LOGGER,
        )
        graph.get_edge.assert_not_called()


def _attachment_graph(*, connector: str = "ATTACHMENTS") -> AsyncMock:
    """rec-1 is a chat attachment user-b uploaded: the uploader's grant is the only one on it."""
    graph, access = _graph(allowed=False)
    access.record("rec-1", "org-1", connectorName=connector, recordType="FILE").user("user-b", "org-1", key="ukey-b")
    access.grant("rec-1", user="user-b", role="OWNER")
    graph.get_document.side_effect = lambda key, coll, *a, **k: {
        ("rec-1", "records"): {"_key": "rec-1", "orgId": "org-1", "connectorName": connector, "recordType": "FILE"},
        ("ukey-b", "users"): {"_key": "ukey-b", "userId": "user-b"},
    }.get((key, coll))
    graph.get_edges_to_node.return_value = [
        {"from_id": "ukey-b", "from_collection": "users", "to_id": "rec-1", "type": "USER", "role": "OWNER"},
    ]
    return graph


class TestChatAttachmentWithoutAnAclPath:
    async def test_is_denied_by_default(self) -> None:
        assert not await caller_can_read_virtual_record(
            _attachment_graph(), user_id="user-c", org_id="org-1", virtual_record_id="vrid-1", logger=LOGGER,
        )


class _EdgesGrantNoChatContent:
    """An access under which graph edges grant chat content to nobody but its uploader."""

    graph_grants_chat_content = False

    def __init__(self, allow: bool) -> None:
        self.allow = allow
        self.asked = 0

    async def can_read_chat_content(self, graph, **kwargs) -> bool:
        self.asked += 1
        return self.allow


ATTACHMENT = {"_key": "rec-1", "connectorName": "ATTACHMENTS", "recordType": "FILE"}
ARTIFACT = {"_key": "rec-1", "connectorName": "CODING_SANDBOX", "recordType": "ARTIFACT"}
OTHER_RECORD = {"_key": "rec-1", "connectorName": "KNOWLEDGE_BASE", "recordType": "FILE"}


def _granted_on(record: dict) -> AsyncMock:
    """rec-1 is `record`, and a graph grant on it reaches user-c."""
    graph, access = _graph(allowed=False)
    access.record("rec-1", "org-1", **record).user("user-c", "org-1").grant("rec-1", user="user-c")
    return graph


async def _can_read(graph: AsyncMock, **kwargs) -> bool:
    return await caller_can_read_virtual_record(
        graph, user_id="user-c", org_id="org-1", virtual_record_id="vrid-1", logger=LOGGER, **kwargs,
    )


class TestGraphGrantsOnChatContent:
    def test_by_default_graph_edges_grant_chat_content(self) -> None:
        assert NoChatContentAccess().graph_grants_chat_content is True

    async def test_with_the_default_access_a_grant_on_an_attachment_counts(self) -> None:
        assert await _can_read(_granted_on(ATTACHMENT), chat_access=NoChatContentAccess())

    @pytest.mark.parametrize("record", [ATTACHMENT, ARTIFACT], ids=["attachment", "artifact"])
    @pytest.mark.parametrize("allow", [False, True])
    async def test_otherwise_the_access_decides_chat_content(self, record: dict, allow: bool) -> None:
        access = _EdgesGrantNoChatContent(allow)
        assert await _can_read(_granted_on(record), chat_access=access) is allow
        assert access.asked == 1

    async def test_other_records_keep_their_graph_grant(self) -> None:
        access = _EdgesGrantNoChatContent(False)
        assert await _can_read(_granted_on(OTHER_RECORD), chat_access=access)
        assert access.asked == 0

    async def test_resolve_read_access_keeps_the_details_when_the_access_allows(self) -> None:
        graph = _granted_on(ATTACHMENT)
        kwargs = {"user_id": "user-c", "org_id": "org-1", "record": "rec-1"}
        # What the graph's own details read answers for this grant.
        details = await graph.check_record_access_with_details("user-c", "org-1", "rec-1")
        assert details["record"]["connectorName"] == ATTACHMENT["connectorName"]
        assert await resolve_read_access(graph, _EdgesGrantNoChatContent(True), **kwargs) == details
        assert await resolve_read_access(graph, _EdgesGrantNoChatContent(False), **kwargs) is None
        assert await resolve_read_access(graph, NoChatContentAccess(), **kwargs) == details


class TestServiceAccountUploadEdges:
    def test_the_edges_grant_the_org_read_access_with_the_type_the_check_accepts(self) -> None:
        edges = service_account_upload_permission_edges("org-1", ["rec-1", "rec-2"], 1700)

        assert [e["to_id"] for e in edges] == ["rec-1", "rec-2"]
        for edge in edges:
            assert edge["from_id"] == "org-1"
            assert edge["from_collection"] == "organizations"
            assert edge["to_collection"] == "records"
            assert edge["type"] == SERVICE_ACCOUNT_UPLOAD_PERMISSION_TYPE
            assert edge["role"] == "READER"

    def test_arango_accepts_the_edge_type(self) -> None:
        """ArangoDB validates permission edges; a type missing here fails every upload there."""
        allowed = permissions_schema["rule"]["properties"]["type"]["enum"]
        assert SERVICE_ACCOUNT_UPLOAD_PERMISSION_TYPE in allowed
