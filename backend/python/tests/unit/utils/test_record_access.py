"""Unit tests for `caller_can_read_virtual_record`."""

import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock

from app.modules.authz.node_pdp_client import NodePdpClient
from app.schema.arango.edges import permissions_schema
from app.utils.record_access import (
    SERVICE_ACCOUNT_UPLOAD_PERMISSION_TYPE,
    caller_can_read_virtual_record,
    service_account_upload_permission_edges,
)
from tests.unit.modules.authz.pdp_fakes import ORG, FakeConfig, FakePdpHttp, allow

LOGGER = logging.getLogger("test")


def _graph(*, record_ids: list[str] | None = None, allowed: bool = True) -> AsyncMock:
    graph = AsyncMock()
    graph.get_records_by_virtual_record_id.return_value = record_ids if record_ids is not None else ["rec-1"]
    graph.check_record_access_with_details.return_value = {"id": "rec-1"} if allowed else None
    return graph


class TestCallerCanReadVirtualRecord:
    async def test_grants_when_any_owning_record_is_readable(self) -> None:
        graph = _graph()

        assert await caller_can_read_virtual_record(
            graph, user_id="user-1", org_id="org-1", virtual_record_id="vrid-1", logger=LOGGER,
        )

        graph.check_record_access_with_details.assert_awaited_once_with("user-1", "org-1", "rec-1")

    async def test_denies_when_the_access_check_returns_nothing(self) -> None:
        graph = _graph(allowed=False)

        assert not await caller_can_read_virtual_record(
            graph, user_id="user-1", org_id="org-1", virtual_record_id="vrid-1", logger=LOGGER,
        )

    async def test_denies_without_a_user(self) -> None:
        graph = _graph()

        assert not await caller_can_read_virtual_record(
            graph, user_id="", org_id="org-1", virtual_record_id="vrid-1", logger=LOGGER,
        )
        graph.get_records_by_virtual_record_id.assert_not_called()

    async def test_denies_when_no_record_owns_the_virtual_id(self) -> None:
        graph = _graph(record_ids=[])

        assert not await caller_can_read_virtual_record(
            graph, user_id="user-1", org_id="org-1", virtual_record_id="vrid-1", logger=LOGGER,
        )
        graph.check_record_access_with_details.assert_not_called()

    async def test_denies_when_the_lookup_fails(self) -> None:
        graph = _graph()
        graph.get_records_by_virtual_record_id.side_effect = RuntimeError("graph down")

        assert not await caller_can_read_virtual_record(
            graph, user_id="user-1", org_id="org-1", virtual_record_id="vrid-1", logger=LOGGER,
        )
        graph.check_record_access_with_details.assert_not_called()

    async def test_service_account_reads_an_org_granted_record(self) -> None:
        graph = _graph(allowed=False)
        graph.get_edge.return_value = {"type": "ORGANIZATION", "role": "READER"}

        assert await caller_can_read_virtual_record(
            graph, user_id="sa-1", org_id="org-1", virtual_record_id="vrid-1", logger=LOGGER,
            is_service_account=True,
        )

    async def test_service_account_cannot_read_a_private_record(self) -> None:
        graph = _graph(allowed=False)
        graph.get_edge.return_value = None

        assert not await caller_can_read_virtual_record(
            graph, user_id="sa-1", org_id="org-1", virtual_record_id="vrid-1", logger=LOGGER,
            is_service_account=True,
        )

    async def test_org_edge_does_not_grant_a_regular_user(self) -> None:
        graph = _graph(allowed=False)
        graph.get_edge.return_value = {"type": "ORGANIZATION", "role": "READER"}

        assert not await caller_can_read_virtual_record(
            graph, user_id="user-1", org_id="org-1", virtual_record_id="vrid-1", logger=LOGGER,
        )
        graph.get_edge.assert_not_called()


class _Pdp:
    def __init__(self, result: bool) -> None:
        self.result = result
        self.reqs: list = []

    async def can_read_chat_content(self, req) -> bool:
        self.reqs.append(req)
        return self.result


def _attachment_graph(*, connector: str = "ATTACHMENTS") -> AsyncMock:
    graph = _graph(allowed=False)
    graph.get_document.side_effect = lambda key, coll, *a, **k: {
        ("rec-1", "records"): {"_key": "rec-1", "orgId": "org-1", "connectorName": connector, "recordType": "FILE"},
        ("ukey-b", "users"): {"_key": "ukey-b", "userId": "user-b"},
    }.get((key, coll))
    graph.get_edges_to_node.return_value = [
        {"from_id": "ukey-b", "from_collection": "users", "to_id": "rec-1", "type": "USER", "role": "OWNER"},
    ]
    return graph


class TestChatAttachmentViaPdp:
    """PH07-08."""

    async def test_attachment_without_acl_path_is_decided_by_the_pdp(self) -> None:
        pdp = _Pdp(True)
        assert await caller_can_read_virtual_record(
            _attachment_graph(), user_id="user-c", org_id="org-1", virtual_record_id="vrid-1",
            logger=LOGGER, conversation_id="conv-1", acl_version=5, pdp=pdp,
        )
        (req,) = pdp.reqs
        assert (req.record_id, req.owner_user_id, req.conversation_id, req.acl_version) == (
            "rec-1", "user-b", "conv-1", 5,
        )

    async def test_pdp_deny(self) -> None:
        assert not await caller_can_read_virtual_record(
            _attachment_graph(), user_id="user-c", org_id="org-1", virtual_record_id="vrid-1",
            logger=LOGGER, pdp=_Pdp(False),
        )

    async def test_non_attachment_record_never_asks_the_pdp(self) -> None:
        pdp = _Pdp(True)
        assert not await caller_can_read_virtual_record(
            _attachment_graph(connector="KNOWLEDGE_BASE"), user_id="user-c", org_id="org-1",
            virtual_record_id="vrid-1", logger=LOGGER, pdp=pdp,
        )
        assert pdp.reqs == []

    async def test_acl_grant_short_circuits_the_pdp(self) -> None:
        pdp = _Pdp(False)
        assert await caller_can_read_virtual_record(
            _graph(), user_id="user-b", org_id="org-1", virtual_record_id="vrid-1", logger=LOGGER, pdp=pdp,
        )
        assert pdp.reqs == []

    async def test_service_account_without_user_never_asks_the_pdp(self) -> None:
        pdp = _Pdp(True)
        graph = _attachment_graph()
        graph.get_edge.return_value = None
        assert not await caller_can_read_virtual_record(
            graph, user_id=None, org_id="org-1", virtual_record_id="vrid-1", logger=LOGGER,
            is_service_account=True, pdp=pdp,
        )
        assert pdp.reqs == []

    async def test_unset_pdp_denies(self) -> None:
        assert not await caller_can_read_virtual_record(
            _attachment_graph(), user_id="user-c", org_id="org-1", virtual_record_id="vrid-1", logger=LOGGER,
        )


class TestFlagOffRecipientParity:
    """PR-7.3: with READER grants gone, a shared-chat recipient reads the owner's attachment via the PDP."""

    async def test_recipient_without_reader_edge_sends_the_request_node_allows(self) -> None:
        golden = json.loads(
            (
                Path(__file__).parents[4] / "nodejs/apps/tests/modules/authz/fixtures/flag-off-attachment-check.json"
            ).read_text()
        )
        graph = _attachment_graph()
        graph.get_document.side_effect = lambda key, coll, *a, **k: {
            ("rec-1", "records"): {"_key": "rec-1", "orgId": ORG, "connectorName": "ATTACHMENTS", "recordType": "FILE"},
            ("ukey-b", "users"): {"_key": "ukey-b", "userId": "u-owner-a"},
        }.get((key, coll))
        http = FakePdpHttp(allow(4))

        assert await caller_can_read_virtual_record(
            graph, user_id="u-reader-c", org_id=ORG, virtual_record_id="vrid-1", logger=LOGGER,
            conversation_id="chat-x", pdp=NodePdpClient(FakeConfig(), http),
        )

        assert [c["json"] for c in http.calls] == [golden]
        graph.check_record_access_with_details.assert_awaited_once_with("u-reader-c", ORG, "rec-1")
        graph.batch_create_edges.assert_not_called()


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
