"""can_read_record: existing ACL OR the Node PDP for another user's chat content."""

from __future__ import annotations

import pytest

from app.modules.authz.chat_content_access import (
    can_read_chat_content_via_pdp,
    can_read_record,
    resolve_read_access,
)
from app.modules.authz.node_pdp_client import (
    ChatContentCheck,
    DenyAllPdpClient,
    NodePdpClient,
    PdpConnectError,
    set_node_pdp_client,
)

from .pdp_fakes import (
    ORG,
    B,
    C,
    FakeConfig,
    FakeNodeRules,
    FakePdpHttp,
    Graph,
    allow,
    deny,
)


class RecordingPdp:
    def __init__(self, result: bool = True) -> None:
        self.result = result
        self.reqs: list[ChatContentCheck] = []

    async def can_read_chat_content(self, req: ChatContentCheck) -> bool:
        self.reqs.append(req)
        return self.result


@pytest.fixture
def graph() -> Graph:
    g = Graph()
    for u in (B, C):
        g.user(u)
    return g


class TestExistingAclWins:
    async def test_acl_grant_never_asks_pdp(self, graph: Graph) -> None:
        graph.attachment("att-1")
        graph.acl.add((C, "att-1"))
        pdp = RecordingPdp(False)
        assert await can_read_record(graph, pdp, user_id=C, org_id=ORG, record="att-1")
        assert pdp.reqs == []

    async def test_uploader_with_owner_edge_is_true_without_pdp_call(self, graph: Graph) -> None:
        """PI-12: uploader B (OWNER edge) allowed, PDP unreachable; C denied."""
        graph.attachment("att-1")
        graph.acl.add((B, "att-1"))
        down = NodePdpClient(FakeConfig(), FakePdpHttp(PdpConnectError("x"), PdpConnectError("x")))
        assert await can_read_record(graph, down, user_id=B, org_id=ORG, record="att-1")
        assert not await can_read_record(graph, down, user_id=C, org_id=ORG, record="att-1")


class TestAttachmentPath:
    async def test_attachment_goes_to_pdp_with_owner_resolved_from_edge(self, graph: Graph) -> None:
        graph.attachment("att-1")
        pdp = RecordingPdp(True)
        assert await can_read_record(
            graph, pdp, user_id=C, org_id=ORG, record="att-1", conversation_id="conv-9", acl_version=3,
        )
        (req,) = pdp.reqs
        assert (req.resource_type, req.record_id, req.owner_user_id) == ("chatAttachment", "att-1", B)
        assert (req.user_id, req.org_id, req.conversation_id, req.acl_version) == (C, ORG, "conv-9", 3)
        assert req.kind is None and req.run_id is None

    async def test_conversation_is_omitted_when_unknown(self, graph: Graph) -> None:
        graph.attachment("att-1")
        pdp = RecordingPdp(True)
        await can_read_record(graph, pdp, user_id=C, org_id=ORG, record="att-1")
        assert pdp.reqs[0].conversation_id is None

    async def test_pdp_deny(self, graph: Graph) -> None:
        graph.attachment("att-1")
        assert not await can_read_record(graph, RecordingPdp(False), user_id=C, org_id=ORG, record="att-1")

    async def test_record_entity_is_accepted(self, graph: Graph) -> None:
        graph.attachment("att-1")

        class Rec:
            id = "att-1"
            org_id = ORG
            connector_name = type("E", (), {"value": "ATTACHMENTS"})()
            record_type = type("E", (), {"value": "FILE"})()

        pdp = RecordingPdp(True)
        assert await can_read_record(graph, pdp, user_id=C, org_id=ORG, record=Rec())
        assert pdp.reqs[0].record_id == "att-1"

    async def test_trashed_attachment_never_reaches_pdp(self, graph: Graph) -> None:
        """A record in the trash keeps its chat rows; the PDP must not reopen it (soft delete, #3695)."""
        graph.attachment("att-1")
        graph.docs["records"]["att-1"]["isDeleted"] = True
        pdp = RecordingPdp(True)
        assert not await can_read_record(graph, pdp, user_id=C, org_id=ORG, record="att-1")
        assert not await can_read_chat_content_via_pdp(graph, pdp, user_id=C, org_id=ORG, record="att-1")
        assert pdp.reqs == []

    async def test_trashed_record_entity_never_reaches_pdp(self, graph: Graph) -> None:
        graph.attachment("att-1")

        class Rec:
            id = "att-1"
            org_id = ORG
            is_deleted = True
            connector_name = type("E", (), {"value": "ATTACHMENTS"})()
            record_type = type("E", (), {"value": "FILE"})()

        pdp = RecordingPdp(True)
        assert not await can_read_chat_content_via_pdp(graph, pdp, user_id=C, org_id=ORG, record=Rec())
        assert pdp.reqs == []

    async def test_other_org_record_never_reaches_pdp(self, graph: Graph) -> None:
        graph.attachment("att-1", org="b" * 24)
        pdp = RecordingPdp(True)
        assert not await can_read_record(graph, pdp, user_id=C, org_id=ORG, record="att-1")
        assert pdp.reqs == []


class TestNoPdpForUnsupportedCases:
    async def test_kb_record_never_asks_pdp(self, graph: Graph) -> None:
        graph.docs["records"]["kb-1"] = {"_key": "kb-1", "orgId": ORG, "connectorName": "KNOWLEDGE_BASE",
                                         "recordType": "FILE"}
        pdp = RecordingPdp(True)
        assert not await can_read_record(graph, pdp, user_id=C, org_id=ORG, record="kb-1")
        assert pdp.reqs == []

    async def test_service_account_upload_without_user_owner_is_denied_not_asked(self, graph: Graph) -> None:
        graph.attachment("att-sa", owner=None)
        graph.perm.append({"from_id": ORG, "from_collection": "organizations", "to_id": "att-sa",
                           "type": "ORGANIZATION", "role": "OWNER"})
        pdp = RecordingPdp(True)
        assert not await can_read_record(graph, pdp, user_id=C, org_id=ORG, record="att-sa")
        assert pdp.reqs == []

    async def test_reader_edge_is_not_an_owner(self, graph: Graph) -> None:
        graph.attachment("att-1", owner=None)
        graph.perm.append({"from_id": f"key-{B}", "to_id": "att-1", "type": "USER", "role": "READER"})
        pdp = RecordingPdp(True)
        assert not await can_read_record(graph, pdp, user_id=C, org_id=ORG, record="att-1")
        assert pdp.reqs == []

    async def test_owner_asking_about_own_file_is_not_sent(self, graph: Graph) -> None:
        graph.attachment("att-1")
        pdp = RecordingPdp(True)
        assert not await can_read_chat_content_via_pdp(graph, pdp, user_id=B, org_id=ORG, record="att-1")
        assert pdp.reqs == []

    async def test_unknown_record(self, graph: Graph) -> None:
        pdp = RecordingPdp(True)
        assert not await can_read_record(graph, pdp, user_id=C, org_id=ORG, record="nope")
        assert pdp.reqs == []

    async def test_missing_identity(self, graph: Graph) -> None:
        graph.attachment("att-1")
        pdp = RecordingPdp(True)
        assert not await can_read_record(graph, pdp, user_id=None, org_id=ORG, record="att-1")
        assert not await can_read_record(graph, pdp, user_id=C, org_id=None, record="att-1")
        assert pdp.reqs == []

    async def test_graph_error_denies(self, graph: Graph) -> None:
        graph.attachment("att-1")

        async def boom(*a, **k) -> None:
            raise RuntimeError("graph down")

        graph.get_edges_to_node = boom  # type: ignore[assignment]
        pdp = RecordingPdp(True)
        assert not await can_read_chat_content_via_pdp(graph, pdp, user_id=C, org_id=ORG, record="att-1")
        assert pdp.reqs == []


class TestArtifactPath:
    async def test_artifact_sends_run_id_visibility_and_temporary(self, graph: Graph) -> None:
        graph.artifact("art-1", visibility="STAGING", isTemporary=True)
        pdp = RecordingPdp(True)
        assert await can_read_record(graph, pdp, user_id=C, org_id=ORG, record="art-1", acl_version=2)
        (req,) = pdp.reqs
        assert req.resource_type == "chatArtifact"
        assert (req.conversation_id, req.run_id, req.owner_user_id) == ("conv-1", "run-1", B)
        assert req.kind is not None
        assert (req.kind.visibility, req.kind.is_temporary) == ("STAGING", True)

    async def test_artifact_without_conversation_is_not_chat_content(self, graph: Graph) -> None:
        graph.artifact("art-1", conv=None)
        pdp = RecordingPdp(True)
        assert not await can_read_record(graph, pdp, user_id=C, org_id=ORG, record="art-1")
        assert pdp.reqs == []

    async def test_caller_conversation_must_match_artifact_conversation(self, graph: Graph) -> None:
        graph.artifact("art-1", conv="chat-b")
        pdp = RecordingPdp(True)
        assert not await can_read_record(graph, pdp, user_id=C, org_id=ORG, record="art-1",
                                         conversation_id="chat-a")
        assert pdp.reqs == []

    async def test_acl_version_keys_the_cache_only_for_the_decided_chat(self, graph: Graph) -> None:
        graph.artifact("art-1", conv="chat-b")
        graph.attachment("att-1")
        pdp = RecordingPdp(True)
        await can_read_record(graph, pdp, user_id=C, org_id=ORG, record="art-1",
                              conversation_id="chat-b", acl_version=4)
        await can_read_record(graph, pdp, user_id=C, org_id=ORG, record="art-1", acl_version=4)
        await can_read_record(graph, pdp, user_id=C, org_id=ORG, record="att-1", acl_version=4)
        assert [r.acl_version for r in pdp.reqs] == [4, None, None]

    async def test_legacy_artifact_without_run_id_is_sent_without_it(self, graph: Graph) -> None:
        graph.artifact("art-1", runId=None)
        pdp = RecordingPdp(False)
        await can_read_record(graph, pdp, user_id=C, org_id=ORG, record="art-1")
        assert pdp.reqs[0].run_id is None


class TestConsentScenarios:
    """The security requirements, end to end through can_read_record."""

    @pytest.fixture
    def node(self) -> FakeNodeRules:
        n = FakeNodeRules()
        n.members = {"chat-b": {C}, "chat-a": {C}}
        return n

    async def test_no_consent_denies(self, graph: Graph, node: FakeNodeRules) -> None:
        graph.attachment("att-1")
        node.files_shared[("chat-b", "att-1")] = False
        assert not await can_read_record(graph, node, user_id=C, org_id=ORG, record="att-1",
                                         conversation_id="chat-b")

    async def test_consent_allows(self, graph: Graph, node: FakeNodeRules) -> None:
        graph.attachment("att-1")
        node.files_shared[("chat-b", "att-1")] = True
        assert await can_read_record(graph, node, user_id=C, org_id=ORG, record="att-1",
                                     conversation_id="chat-b")

    async def test_removal_denies_immediately_on_preview_path(self, graph: Graph, node: FakeNodeRules) -> None:
        graph.attachment("att-1")
        node.files_shared[("chat-b", "att-1")] = True
        assert await can_read_record(graph, node, user_id=C, org_id=ORG, record="att-1")
        node.members["chat-b"].discard(C)
        assert not await can_read_record(graph, node, user_id=C, org_id=ORG, record="att-1")

    async def test_removal_with_real_client_denies_on_preview_even_after_cached_run_allow(
        self, graph: Graph,
    ) -> None:
        graph.attachment("att-1")
        http = FakePdpHttp(allow(4), allow(4), deny(5))
        client = NodePdpClient(FakeConfig(), http)
        kw = {"user_id": C, "org_id": ORG, "record": "att-1", "conversation_id": "chat-b"}
        assert await can_read_record(graph, client, acl_version=4, **kw)
        assert await can_read_record(graph, client, acl_version=4, **kw)  # cached, no Node call
        assert len(http.calls) == 1
        # Download/preview carry no aclVersion: they always ask, so removal bites at once.
        assert await can_read_record(graph, client, **kw)
        assert not await can_read_record(graph, client, **kw)

    async def test_grant_on_chat_b_never_authorizes_chat_a(self, graph: Graph, node: FakeNodeRules) -> None:
        graph.attachment("att-1")
        node.files_shared[("chat-b", "att-1")] = True
        assert await can_read_record(graph, node, user_id=C, org_id=ORG, record="att-1",
                                     conversation_id="chat-b")
        assert not await can_read_record(graph, node, user_id=C, org_id=ORG, record="att-1",
                                         conversation_id="chat-a")

    async def test_pdp_down_denies_never_allows(self, graph: Graph) -> None:
        graph.attachment("att-1")
        graph.artifact("art-1")
        down = NodePdpClient(FakeConfig(), FakePdpHttp(default=PdpConnectError("refused")))
        for rid in ("att-1", "art-1"):
            assert not await can_read_record(graph, down, user_id=C, org_id=ORG, record=rid)

    async def test_unset_pdp_accessor_denies(self, graph: Graph) -> None:
        set_node_pdp_client(None)
        graph.attachment("att-1")
        assert not await can_read_record(graph, None, user_id=C, org_id=ORG, record="att-1")
        assert isinstance(DenyAllPdpClient(), DenyAllPdpClient)

    async def test_unknown_and_denied_are_indistinguishable(self, graph: Graph, node: FakeNodeRules) -> None:
        graph.attachment("att-1")
        denied = await resolve_read_access(graph, node, user_id=C, org_id=ORG, record="att-1")
        unknown = await resolve_read_access(graph, node, user_id=C, org_id=ORG, record="ghost")
        assert denied is None and unknown is None


class TestResolveReadAccess:
    async def test_acl_details_are_returned_unchanged(self, graph: Graph) -> None:
        graph.attachment("att-1")
        graph.acl.add((C, "att-1"))
        assert await resolve_read_access(graph, RecordingPdp(False), user_id=C, org_id=ORG,
                                         record="att-1") == {"record": {"id": "att-1"}}

    async def test_pdp_allow_returns_minimal_details(self, graph: Graph) -> None:
        graph.attachment("att-1")
        details = await resolve_read_access(graph, RecordingPdp(True), user_id=C, org_id=ORG, record="att-1")
        assert details is not None
        assert details["viaChatContentPdp"] is True
        assert details["record"]["_key"] == "att-1"
