"""delete_record is the user-facing delete: it must be org-scoped on every graph
backend, and KB records additionally need a write role on their KB. Connector
deletes by external id are internal and keep working without the org argument."""

from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

ORG_A = "org-A"
ORG_B = "org-B"


def _record(org_id: str, **overrides) -> dict:
    return {
        "id": "rec-1",
        "orgId": org_id,
        "connectorId": "conn-1",
        "connectorName": "DRIVE",
        "origin": "CONNECTOR",
        "virtualRecordId": "vr-1",
        **overrides,
    }


def _kb_record(org_id: str) -> dict:
    return _record(org_id, connectorName="KB", origin="UPLOAD")


def _neo4j(record: dict | None, kb_role: str | None = "OWNER") -> Neo4jProvider:
    async def get_document(key: str, collection: str, transaction: str | None = None) -> dict | None:
        if record is None or collection != CollectionNames.RECORDS.value:
            return None
        return dict(record)

    provider = Neo4jProvider(MagicMock(), MagicMock())
    provider.client = AsyncMock()
    provider.get_document = AsyncMock(side_effect=get_document)
    provider._get_kb_context_for_record = AsyncMock(return_value={"kb_id": "kb-1"})
    provider.get_user_by_user_id = AsyncMock(return_value={"id": "ukey-a"})
    provider.get_user_kb_permission = AsyncMock(return_value=kb_role)
    provider.delete_records_and_relations = AsyncMock()
    provider._create_deleted_record_event_payload = AsyncMock(return_value={"recordId": "rec-1"})
    return provider


class TestNeo4jDeleteRecordAuthz:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("make_record", [_record, _kb_record], ids=["connector", "kb"])
    async def test_record_in_other_org_is_not_found(self, make_record: Callable[[str], dict]) -> None:
        provider = _neo4j(make_record(ORG_B), kb_role="OWNER")

        result = await provider.delete_record("rec-1", "user-a", ORG_A)

        assert result["success"] is False
        assert result["code"] == 404
        provider.delete_records_and_relations.assert_not_awaited()
        assert "eventData" not in result

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kb_role", ["READER", "COMMENTER", None])
    async def test_kb_record_without_write_role_is_forbidden(self, kb_role: str | None) -> None:
        provider = _neo4j(_kb_record(ORG_A), kb_role=kb_role)

        result = await provider.delete_record("rec-1", "user-a", ORG_A)

        assert result["success"] is False
        assert result["code"] == 403
        provider.delete_records_and_relations.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kb_role", ["OWNER", "WRITER", "FILEORGANIZER"])
    async def test_kb_record_with_write_role_is_deleted(self, kb_role: str) -> None:
        provider = _neo4j(_kb_record(ORG_A), kb_role=kb_role)

        result = await provider.delete_record("rec-1", "user-a", ORG_A)

        assert result["success"] is True
        assert result["isKb"] is True
        provider.delete_records_and_relations.assert_awaited_once()
        assert result["eventData"]["eventType"] == "deleteRecord"
        provider.get_user_kb_permission.assert_awaited_once_with("kb-1", "ukey-a", None)

    @pytest.mark.asyncio
    async def test_connector_record_in_same_org_is_deleted_without_kb_check(self) -> None:
        provider = _neo4j(_record(ORG_A))

        result = await provider.delete_record("rec-1", "user-a", ORG_A)

        assert result["success"] is True
        assert result["isKb"] is False
        provider.delete_records_and_relations.assert_awaited_once()
        assert result["eventData"]["eventType"] == "deleteRecord"
        provider.get_user_kb_permission.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_by_external_id_scopes_to_the_records_own_org(self) -> None:
        """Connector sync (Outlook) calls this without an org; it must keep working."""
        provider = _neo4j(_record(ORG_A))
        record = MagicMock()
        record.id = "rec-1"
        record.org_id = ORG_A
        provider.get_record_by_external_id = AsyncMock(return_value=record)

        with patch.object(provider, "delete_record", wraps=provider.delete_record) as delete:
            await provider.delete_record_by_external_id("conn-1", "ext-1", "user-a")

        delete.assert_awaited_once_with("rec-1", "user-a", ORG_A, None)
        provider.delete_records_and_relations.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_org_never_matches_a_record_without_org(self) -> None:
        provider = _neo4j(_record(""))

        result = await provider.delete_record("rec-1", "user-a", "")

        assert result["code"] == 404
        provider.delete_records_and_relations.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_by_external_id_skips_record_without_org(self) -> None:
        provider = _neo4j(_record(""))
        record = MagicMock()
        record.id = "rec-1"
        record.org_id = ""
        provider.get_record_by_external_id = AsyncMock(return_value=record)

        await provider.delete_record_by_external_id("conn-1", "ext-1", "user-a")

        provider.delete_records_and_relations.assert_not_awaited()


@pytest.fixture
def arango() -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(MagicMock(), MagicMock())
    provider.http_client = AsyncMock()
    return provider


class TestArangoDeleteRecordOrgScope:
    @pytest.mark.asyncio
    async def test_record_in_other_org_is_not_found(self, arango) -> None:
        arango.http_client.get_document.return_value = {
            "_key": "rec-1", "orgId": ORG_B, "connectorName": "DRIVE", "origin": "CONNECTOR",
        }
        with patch.object(arango, "delete_google_drive_record", new_callable=AsyncMock) as drive:
            result = await arango.delete_record("rec-1", "user-a", ORG_A)

        assert result["success"] is False
        assert result["code"] == 404
        drive.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_record_in_same_org_routes_to_connector_check(self, arango) -> None:
        arango.http_client.get_document.return_value = {
            "_key": "rec-1", "orgId": ORG_A, "connectorName": "DRIVE", "origin": "CONNECTOR",
        }
        with patch.object(
            arango, "delete_google_drive_record",
            new_callable=AsyncMock, return_value={"success": True},
        ) as drive:
            result = await arango.delete_record("rec-1", "user-a", ORG_A)

        assert result["success"] is True
        drive.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_by_external_id_scopes_to_the_records_own_org(self, arango) -> None:
        record = MagicMock()
        record.id = "rec-1"
        record.org_id = ORG_A
        with patch.object(
            arango, "get_record_by_external_id", new_callable=AsyncMock, return_value=record,
        ), patch.object(
            arango, "delete_record", new_callable=AsyncMock, return_value={"success": True},
        ) as delete:
            await arango.delete_record_by_external_id("conn-1", "ext-1", "user-a")

        delete.assert_awaited_once_with("rec-1", "user-a", ORG_A, transaction=None)

    @pytest.mark.asyncio
    async def test_empty_org_never_matches_a_record_without_org(self, arango) -> None:
        arango.http_client.get_document.return_value = {
            "_key": "rec-1", "orgId": "", "connectorName": "DRIVE", "origin": "CONNECTOR",
        }
        with patch.object(arango, "delete_google_drive_record", new_callable=AsyncMock) as drive:
            result = await arango.delete_record("rec-1", "user-a", "")

        assert result["code"] == 404
        drive.assert_not_awaited()
