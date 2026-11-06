"""Unit tests for TieredRecordAuthorizer.

Covers:
- Org mismatch → RecordAccessDeniedError (no I/O)
- Direct permission edge present → granted without hitting check_record_access
- Direct edge absent, check_record_access passes → granted
- All three tiers fail → RecordAccessDeniedError
- Service account (ArtifactNotFoundError in resolve_user_key) → skips tier 2 gracefully
- No-external-call on org mismatch (tier 1 short-circuit)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, OriginTypes
from app.models.entities import Record, RecordType
from app.modules.authz.chat_content_access import NoChatContentAccess
from app.services.record_content.authorizer import TieredRecordAuthorizer
from app.services.record_content.models import (
    RecordAccessDeniedError,
    RecordNotFoundError,
)


def _make_actor(org_id="org1", user_id="user1"):
    actor = MagicMock()
    actor.org_id = org_id
    actor.user_id = user_id
    return actor


def _make_record(org_id="org1", record_id="rec1"):
    record = MagicMock()
    record.org_id = org_id
    record.id = record_id
    # Support dict-style access fallback
    record.get = lambda key, default=None: {
        "orgId": org_id,
        "_key": record_id,
    }.get(key, default)
    return record


@pytest.fixture
def graph():
    g = AsyncMock()
    g.get_edge = AsyncMock(return_value=None)
    g.check_record_access_with_details = AsyncMock(return_value=None)
    return g


@pytest.mark.asyncio
async def test_org_mismatch_raises_without_io(graph):
    """Tier 1 rejects cross-org records before any I/O."""
    authorizer = TieredRecordAuthorizer(graph)
    actor = _make_actor(org_id="orgA")
    record = _make_record(org_id="orgB")

    with pytest.raises(RecordAccessDeniedError):
        await authorizer.authorize(actor, record)

    graph.get_edge.assert_not_called()
    graph.check_record_access_with_details.assert_not_called()


@pytest.mark.asyncio
async def test_a_direct_edge_alone_no_longer_grants(graph):
    """A user->record edge used to be accepted on its own, skipping the
    STRICT/RESTRICTED rule and the connector gate. The permission model decides."""
    graph.get_edge.return_value = {"_id": "edges/e1"}
    graph.check_record_access_with_details.return_value = None
    authorizer = TieredRecordAuthorizer(graph)

    with pytest.raises(RecordAccessDeniedError):
        await authorizer.authorize(_make_actor(), _make_record())

    graph.get_edge.assert_not_called()
    graph.check_record_access_with_details.assert_awaited_once()


@pytest.mark.asyncio
async def test_tier3_grants_when_edge_absent(graph):
    """Edge absent, tier 3 returns non-None → granted."""
    graph.get_edge.return_value = None
    graph.check_record_access_with_details.return_value = {"granted": True}
    authorizer = TieredRecordAuthorizer(graph)
    actor = _make_actor()
    record = _make_record()

    with patch(
        "app.services.artifact_registry.access.AccessPolicy.resolve_user_key",
        new=AsyncMock(return_value="user/user1"),
    ):
        await authorizer.authorize(actor, record)


@pytest.mark.asyncio
async def test_all_tiers_fail_raises(graph):
    """All three tiers fail → RecordAccessDeniedError."""
    graph.get_edge.return_value = None
    graph.check_record_access_with_details.return_value = None
    authorizer = TieredRecordAuthorizer(graph)
    actor = _make_actor()
    record = _make_record()

    with patch(
        "app.services.artifact_registry.access.AccessPolicy.resolve_user_key",
        new=AsyncMock(return_value="user/user1"),
    ):
        with pytest.raises(RecordAccessDeniedError):
            await authorizer.authorize(actor, record)


@pytest.mark.asyncio
async def test_service_account_no_user_node_falls_to_tier3(graph):
    """When resolve_user_key raises ArtifactNotFoundError (no user node),
    tier 2 is skipped and tier 3 decides."""
    from app.services.artifact_registry.access import ArtifactNotFoundError

    graph.get_edge.return_value = None
    graph.check_record_access_with_details.return_value = {"granted": True}
    authorizer = TieredRecordAuthorizer(graph)
    actor = _make_actor()
    record = _make_record()

    with patch(
        "app.services.artifact_registry.access.AccessPolicy.resolve_user_key",
        new=AsyncMock(side_effect=ArtifactNotFoundError("no node")),
    ):
        await authorizer.authorize(actor, record)

    graph.check_record_access_with_details.assert_called_once()


@pytest.mark.asyncio
async def test_service_account_denied_by_tier3(graph):
    """Service account with no user node + tier 3 also denies → error."""
    from app.services.artifact_registry.access import ArtifactNotFoundError

    graph.get_edge.return_value = None
    graph.check_record_access_with_details.return_value = None
    authorizer = TieredRecordAuthorizer(graph)
    actor = _make_actor()
    record = _make_record()

    with patch(
        "app.services.artifact_registry.access.AccessPolicy.resolve_user_key",
        new=AsyncMock(side_effect=ArtifactNotFoundError("no node")),
    ):
        with pytest.raises(RecordAccessDeniedError):
            await authorizer.authorize(actor, record)


def _chat_graph() -> AsyncMock:
    g = AsyncMock()
    g.get_edge = AsyncMock(return_value=None)
    g.check_record_access_with_details = AsyncMock(return_value=None)
    g.get_document = AsyncMock(side_effect=lambda key, coll, *a, **k: {
        ("rec1", "records"): {"_key": "rec1", "orgId": "org1", "connectorName": "ATTACHMENTS", "recordType": "FILE"},
        ("ukey-b", "users"): {"_key": "ukey-b", "userId": "user-b"},
    }.get((key, coll)))
    g.get_edges_to_node = AsyncMock(return_value=[
        {"from_id": "ukey-b", "from_collection": "users", "to_id": "rec1", "type": "USER", "role": "OWNER"},
    ])
    return g


def _attachment_record() -> object:
    from types import SimpleNamespace

    return SimpleNamespace(id="rec1", org_id="org1", connector_name="ATTACHMENTS", record_type="FILE")


@pytest.mark.asyncio
async def test_tier4_denies_chat_content_by_default() -> None:
    with pytest.raises(RecordAccessDeniedError):
        await TieredRecordAuthorizer(_chat_graph()).authorize(_make_actor(), _attachment_record())


# ---------------------------------------------------------------------------
# Tier 0: a record in the trash
# ---------------------------------------------------------------------------


def _stored_record(*, is_deleted: bool) -> Record:
    return Record(
        id="rec1",
        org_id="org1",
        record_name="report.pdf",
        record_type=RecordType.FILE,
        external_record_id="ext-1",
        version=1,
        origin=OriginTypes.UPLOAD,
        connector_name=Connectors.KNOWLEDGE_BASE,
        connector_id="kb1",
        is_deleted=is_deleted,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "record",
    [
        pytest.param(_stored_record(is_deleted=True), id="record-model"),
        pytest.param({"_key": "rec1", "orgId": "org1", "isDeleted": True}, id="stored-document"),
    ],
)
async def test_a_trashed_record_is_not_found_even_with_a_direct_edge(graph, record) -> None:
    """Trashed records keep their permission edges for restore; tier 2 alone would grant."""
    graph.get_edge.return_value = {"_id": "edges/e1"}
    authorizer = TieredRecordAuthorizer(graph)

    with pytest.raises(RecordNotFoundError, match="rec1 was deleted"):
        await authorizer.authorize(_make_actor(), record)

    graph.get_edge.assert_not_called()
    graph.check_record_access_with_details.assert_not_called()


@pytest.mark.asyncio
async def test_a_live_record_still_goes_through_the_tiers(graph) -> None:
    """Tier 0 lets a live record on to the org check and the permission model,
    which alone decides: a direct edge is not read."""
    graph.get_edge.return_value = {"_id": "edges/e1"}
    graph.check_record_access_with_details.return_value = {"record": {"id": "rec1"}}
    authorizer = TieredRecordAuthorizer(graph)

    await authorizer.authorize(_make_actor(), _stored_record(is_deleted=False))

    graph.check_record_access_with_details.assert_awaited_once_with("user1", "org1", "rec1")
    graph.get_edge.assert_not_called()


@pytest.mark.asyncio
async def test_a_live_record_the_model_denies_is_denied_not_missing(graph) -> None:
    graph.get_edge.return_value = {"_id": "edges/e1"}
    authorizer = TieredRecordAuthorizer(graph)

    with pytest.raises(RecordAccessDeniedError):
        await authorizer.authorize(_make_actor(), _stored_record(is_deleted=False))

    graph.check_record_access_with_details.assert_awaited_once_with("user1", "org1", "rec1")


# ---------------------------------------------------------------------------
# Graph grants on chat content, per the installed ChatContentAccess
# ---------------------------------------------------------------------------


class _EdgesGrantNoChatContent:
    """An access under which graph grants on chat content count for nobody: it decides, uploader included."""

    graph_grants_chat_content = False

    def __init__(self, allow: bool) -> None:
        self.allow = allow
        self.asked = 0

    async def can_read_chat_content(self, graph, **kwargs) -> bool:
        self.asked += 1
        return self.allow


def _acl_graph(acl_record: dict) -> AsyncMock:
    """A permission model that grants the record, as it does through any edge on it."""
    g = AsyncMock()
    g.get_edge = AsyncMock(return_value={"_id": "edges/e1", "role": "READER"})
    g.check_record_access_with_details = AsyncMock(return_value={"record": acl_record})
    return g


_ATTACHMENT_DOC = {"_key": "rec1", "connectorName": "ATTACHMENTS"}


async def _authorize(graph: AsyncMock, access: object, record: object) -> None:
    await TieredRecordAuthorizer(graph, access).authorize(_make_actor(), record)


@pytest.mark.asyncio
async def test_with_the_default_access_a_grant_on_an_attachment_grants() -> None:
    access = NoChatContentAccess()
    await _authorize(_acl_graph(_ATTACHMENT_DOC), access, _attachment_record())


@pytest.mark.asyncio
@pytest.mark.parametrize("allow", [False, True])
async def test_otherwise_a_grant_on_chat_content_is_left_to_the_access(allow: bool) -> None:
    graph = _acl_graph(_ATTACHMENT_DOC)
    access = _EdgesGrantNoChatContent(allow)
    if allow:
        await _authorize(graph, access, _attachment_record())
    else:
        with pytest.raises(RecordAccessDeniedError):
            await _authorize(graph, access, _attachment_record())
    assert access.asked == 1


@pytest.mark.asyncio
async def test_otherwise_the_uploader_is_decided_by_the_access_not_by_an_edge() -> None:
    """No direct edge is read, not even the uploader's OWNER edge: the access
    must allow its uploader itself."""
    graph = _acl_graph(_ATTACHMENT_DOC)
    graph.get_edge.return_value = {"_id": "edges/e1", "role": "OWNER"}
    access = _EdgesGrantNoChatContent(True)
    await _authorize(graph, access, _attachment_record())
    assert access.asked == 1
    graph.get_edge.assert_not_called()


@pytest.mark.asyncio
async def test_otherwise_a_grant_on_another_record_still_grants() -> None:
    graph = _acl_graph({"_key": "rec1", "connectorName": "KB", "recordType": "FILE"})
    access = _EdgesGrantNoChatContent(False)
    await _authorize(graph, access, _stored_record(is_deleted=False))
    assert access.asked == 0
