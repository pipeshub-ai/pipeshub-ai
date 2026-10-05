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
async def test_direct_edge_grants_without_tier3(graph):
    """Tier 2 permission edge present → tier 3 never called."""
    graph.get_edge.return_value = {"_id": "edges/e1"}
    authorizer = TieredRecordAuthorizer(graph)
    actor = _make_actor()
    record = _make_record()

    with patch(
        "app.services.artifact_registry.access.AccessPolicy.resolve_user_key",
        new=AsyncMock(return_value="user/user1"),
    ):
        await authorizer.authorize(actor, record)

    graph.check_record_access_with_details.assert_not_called()


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
    graph.get_edge.return_value = {"_id": "edges/e1"}
    authorizer = TieredRecordAuthorizer(graph)

    with patch(
        "app.services.artifact_registry.access.AccessPolicy.resolve_user_key",
        new=AsyncMock(return_value="user/user1"),
    ):
        await authorizer.authorize(_make_actor(), _stored_record(is_deleted=False))

    graph.get_edge.assert_awaited_once()
