"""DELETE /api/v1/records/{record_id} must be scoped to the caller's org, and
KB records additionally to the caller's role on the KB.

The route is driven end to end against a real Neo4jProvider whose I/O is
mocked, so the tests exercise the actual authorization decision rather than a
stubbed provider result.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import CollectionNames
from app.connectors.api.router import delete_record
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

ORG_A = "org-A"
ORG_B = "org-B"
RECORD_ID = "rec-1"

CONNECTOR = {"connectorName": "DRIVE", "origin": "CONNECTOR"}
KB = {"connectorName": "KB", "origin": "UPLOAD"}


def _request(user_id: str = "user-a", org_id: str | None = ORG_A) -> MagicMock:
    user = {"userId": user_id, "orgId": org_id}
    req = MagicMock()
    req.state.user.get = lambda k, default=None: user.get(k, default)
    req.app.container.logger.return_value = MagicMock()
    return req


def _provider(record_org: str, kind: dict, kb_role: str | None = "OWNER") -> Neo4jProvider:
    record = {
        "id": RECORD_ID,
        "orgId": record_org,
        "connectorId": "conn-1",
        "virtualRecordId": "vr-1",
        **kind,
    }

    async def get_document(key: str, collection: str, transaction: str | None = None) -> dict | None:
        return dict(record) if collection == CollectionNames.RECORDS.value else None

    provider = Neo4jProvider(MagicMock(), MagicMock())
    provider.client = AsyncMock()
    provider.get_document = AsyncMock(side_effect=get_document)
    provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb-1"})
    provider.get_user_by_user_id = AsyncMock(return_value={"id": "ukey-a"})
    provider.get_user_kb_permission = AsyncMock(return_value=kb_role)
    provider.delete_records_and_relations = AsyncMock()
    provider._create_deleted_record_event_payload = AsyncMock(
        return_value={"recordId": RECORD_ID, "virtualRecordId": "vr-1"}
    )
    return provider


def _assert_untouched(provider: Neo4jProvider, kafka: AsyncMock) -> None:
    provider.delete_records_and_relations.assert_not_awaited()
    kafka.publish_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [CONNECTOR, KB], ids=["connector", "kb"])
async def test_cross_org_delete_is_404_and_leaves_graph_and_vectors(kind: dict) -> None:
    provider = _provider(record_org=ORG_B, kind=kind, kb_role="OWNER")
    kafka = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await delete_record(RECORD_ID, _request(org_id=ORG_A), provider, kafka)

    assert exc.value.status_code == 404
    _assert_untouched(provider, kafka)


@pytest.mark.asyncio
@pytest.mark.parametrize("kb_role", ["READER", "COMMENTER", None])
async def test_kb_record_without_write_role_gets_403(kb_role: str | None) -> None:
    provider = _provider(record_org=ORG_A, kind=KB, kb_role=kb_role)
    kafka = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await delete_record(RECORD_ID, _request(org_id=ORG_A), provider, kafka)

    assert exc.value.status_code == 403
    _assert_untouched(provider, kafka)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [CONNECTOR, KB], ids=["connector", "kb"])
async def test_same_org_delete_removes_record_and_publishes_vector_cleanup(kind: dict) -> None:
    provider = _provider(record_org=ORG_A, kind=kind, kb_role="WRITER")
    kafka = AsyncMock()

    result = await delete_record(RECORD_ID, _request(org_id=ORG_A), provider, kafka)

    assert result["success"] is True
    provider.delete_records_and_relations.assert_awaited_once()
    kafka.publish_event.assert_awaited_once()
    topic, event = kafka.publish_event.await_args.args
    assert topic == "record-events"
    assert event["eventType"] == "deleteRecord"


@pytest.mark.asyncio
async def test_missing_org_in_auth_state_is_rejected_before_touching_the_graph() -> None:
    provider = _provider(record_org=ORG_A, kind=CONNECTOR)
    kafka = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await delete_record(RECORD_ID, _request(org_id=None), provider, kafka)

    assert exc.value.status_code == 401
    _assert_untouched(provider, kafka)
    provider.get_document.assert_not_awaited()
