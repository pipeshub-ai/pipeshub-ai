"""Every knowledge-base delete path names, in each record's ``deleteRecord``
payload, the taxonomy entities the record linked to (``taxonomyRefs``), read
before its edges are removed: folder, file and bulk deletes
(``delete_records_recursive``), its one-record form (``delete_single_record``)
and the trash purge (``purge_trashed_records``, and the page listing whose
payloads the purge outbox keeps), on both stores.
The single-record route is covered in ``test_delete_record_through_providers``.

Connector records carry none: their deletes do not clean taxonomy points yet.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.neo4j import collection_to_label
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

ORG = "org-1"


def _kb(key: str, *, vrid: str | None = "vr", key_field: str = "_key") -> dict:
    record = {key_field: key, "orgId": ORG, "recordName": key, "connectorId": "kb-1",
              "connectorName": "KB", "origin": "UPLOAD"}
    if vrid:
        record["virtualRecordId"] = f"{vrid}-{key}"
    return record


def _drive(key: str, key_field: str = "_key") -> dict:
    return {key_field: key, "orgId": ORG, "recordName": key, "connectorId": "conn-1",
            "connectorName": "DRIVE", "origin": "CONNECTOR", "virtualRecordId": f"vr-{key}"}


# (record id, taxonomy collection, node id): the edges the graph still holds.
LINKS = [
    ("r1", "topics", "t-shared"),
    ("r1", "categories", "c1"),
    ("r1", "departments", "d1"),
    ("r2", "topics", "t-shared"),
    ("r2", "subcategories1", "s1"),
    ("drive-1", "topics", "t-shared"),
]
R1_REFS = [
    {"id": "t-shared", "type": "topic"},
    {"id": "c1", "type": "category"},
    {"id": "d1", "type": "department"},
]
R2_REFS = [{"id": "t-shared", "type": "topic"}, {"id": "s1", "type": "subcategory"}]


def _arango_links(calls: list[str]) -> Callable[..., Awaitable[list[dict]]]:
    async def read(keys: list[str], transaction: str | None = None, *, include_departments: bool = False) -> list[dict]:
        calls.append("links")
        assert include_departments, "the delete must read department links too"
        return [
            {"recordId": rec, "collection": collection, "entityId": node, "name": node}
            for rec, collection, node in LINKS if rec in keys
        ]
    return read


def _payloads(result: dict) -> dict[str, dict]:
    return {p["recordId"]: p for p in (result.get("eventData") or {}).get("payloads") or []}


# ---------------------------------------------------------------------------
# ArangoDB
# ---------------------------------------------------------------------------


def _arango() -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(MagicMock(spec=logging.Logger), MagicMock())
    provider.http_client = AsyncMock()
    return provider


def _arango_recursive(
    provider: ArangoHTTPProvider, records: list[dict], calls: list[str], **links_override: AsyncMock,
) -> list[Any]:
    inventory = {
        "valid_root_keys": [records[0]["_key"]],
        "records_with_type": [{"record": rec, "type_target": None} for rec in records],
        "guard_edges": [],
    }

    async def delete_edges(*_args: object, **_kwargs: object) -> tuple[int, list]:
        calls.append("edges")
        return 0, []

    return [
        patch.object(provider, "_get_all_edge_collections", AsyncMock(return_value=["belongsToTopic"])),
        patch.object(provider, "begin_transaction", AsyncMock(return_value="txn1")),
        patch.object(provider, "commit_transaction", AsyncMock()),
        patch.object(provider, "execute_query", AsyncMock(return_value=[inventory])),
        patch.object(
            provider, "get_record_taxonomy_links",
            links_override.get("links") or AsyncMock(side_effect=_arango_links(calls)),
        ),
        patch.object(provider, "_delete_edges_by_node_ids", AsyncMock(side_effect=delete_edges)),
        patch.object(provider, "_delete_isoftype_targets_from_collected", AsyncMock()),
        patch.object(provider, "_delete_nodes_by_keys", AsyncMock(return_value=(len(records), 0))),
    ]


async def _run_with(patches: list, call: Callable[[], Awaitable[dict]]) -> dict:
    for p in patches:
        p.start()
    try:
        return await call()
    finally:
        for p in reversed(patches):
            p.stop()


class TestArangoRecursiveDelete:
    @pytest.mark.asyncio
    async def test_each_kb_record_names_its_entities_read_before_the_edge_sweep(self) -> None:
        provider, calls = _arango(), []
        folder = _kb("folder-1", vrid=None)
        links = AsyncMock(side_effect=_arango_links(calls))

        result = await _run_with(
            _arango_recursive(provider, [folder, _kb("r1"), _kb("r2")], calls, links=links),
            lambda: provider.delete_records_recursive(["folder-1"], "kb-1"),
        )

        assert calls == ["links", "edges"]
        # A folder has no vectors, so no deleteRecord and nothing to read.
        links.assert_awaited_once_with(["r1", "r2"], include_departments=True)
        payloads = _payloads(result)
        assert set(payloads) == {"r1", "r2"}
        assert payloads["r1"]["taxonomyRefs"] == R1_REFS
        assert payloads["r2"]["taxonomyRefs"] == R2_REFS

    @pytest.mark.asyncio
    async def test_a_connector_record_names_none_and_is_not_read(self) -> None:
        provider, calls = _arango(), []

        result = await _run_with(
            _arango_recursive(provider, [_drive("drive-1")], calls),
            lambda: provider.delete_records_recursive(["drive-1"], "conn-1"),
        )

        assert calls == ["edges"]
        assert "taxonomyRefs" not in _payloads(result)["drive-1"]

    @pytest.mark.asyncio
    async def test_a_record_without_links_names_none(self) -> None:
        provider, calls = _arango(), []

        result = await _run_with(
            _arango_recursive(provider, [_kb("r9")], calls),
            lambda: provider.delete_records_recursive(["r9"], "kb-1"),
        )

        assert "taxonomyRefs" not in _payloads(result)["r9"]

    @pytest.mark.asyncio
    async def test_a_failed_link_read_still_deletes_and_publishes(self) -> None:
        provider, calls = _arango(), []

        result = await _run_with(
            _arango_recursive(
                provider, [_kb("r1")], calls, links=AsyncMock(side_effect=RuntimeError("graph busy")),
            ),
            lambda: provider.delete_records_recursive(["r1"], "kb-1"),
        )

        assert result["success"] is True
        assert result["successfully_deleted"] == 1
        assert "taxonomyRefs" not in _payloads(result)["r1"]


class TestArangoSingleRecordDelete:
    """``delete_single_record`` promises the cleanup ``delete_records_recursive`` does."""

    @pytest.mark.asyncio
    async def test_a_kb_record_names_its_entities_read_before_the_edge_sweep(self) -> None:
        provider, calls = _arango(), []
        links = AsyncMock(side_effect=_arango_links(calls))

        result = await _run_with(
            _arango_recursive(provider, [_kb("r1")], calls, links=links),
            lambda: provider.delete_single_record("r1"),
        )

        assert calls == ["links", "edges"]
        links.assert_awaited_once_with(["r1"], include_departments=True)
        assert _payloads(result)["r1"]["taxonomyRefs"] == R1_REFS

    @pytest.mark.asyncio
    async def test_a_connector_record_names_none_and_is_not_read(self) -> None:
        provider, calls = _arango(), []

        result = await _run_with(
            _arango_recursive(provider, [_drive("drive-1")], calls),
            lambda: provider.delete_single_record("drive-1"),
        )

        assert calls == ["edges"]
        assert "taxonomyRefs" not in _payloads(result)["drive-1"]


class TestArangoPurge:
    @staticmethod
    def _provider(due_rows: list[dict], calls: list[str]) -> ArangoHTTPProvider:
        provider = _arango()

        async def delete_edges(*_args: object, **_kwargs: object) -> tuple[int, list]:
            calls.append("edges")
            return 0, []

        provider._get_all_edge_collections = AsyncMock(return_value=["belongsToTopic"])
        provider._begin_purge_transaction = AsyncMock(return_value="txn1")
        provider.commit_transaction = AsyncMock()
        provider.execute_query = AsyncMock(return_value=due_rows)
        provider.get_record_taxonomy_links = AsyncMock(side_effect=_arango_links(calls))
        provider._delete_edges_by_node_ids = AsyncMock(side_effect=delete_edges)
        provider._delete_isoftype_targets_from_collected = AsyncMock()
        provider._delete_nodes_by_keys = AsyncMock(
            return_value=(sum(1 for row in due_rows if row["due"]), 0)
        )
        return provider

    @pytest.mark.asyncio
    async def test_a_purged_kb_record_names_its_entities_read_before_its_edges_go(self) -> None:
        calls: list[str] = []
        provider = self._provider([
            {"record": _kb("r1"), "type_target": None, "due": True},
            {"record": _drive("drive-1"), "type_target": None, "due": True},
            {"record": _kb("r2"), "type_target": None, "due": False},  # restored meanwhile
        ], calls)

        result = await provider.purge_trashed_records(["r1", "drive-1", "r2"], ORG, 10)

        assert calls == ["links", "edges"]
        provider.get_record_taxonomy_links.assert_awaited_once_with(["r1"], include_departments=True)
        purged = {row["id"]: row["deleteRecordPayload"] for row in result["purged"]}
        assert purged["r1"]["taxonomyRefs"] == R1_REFS
        assert "taxonomyRefs" not in purged["drive-1"]
        assert result["kept"] == ["r2"]

    @pytest.mark.asyncio
    async def test_the_page_listing_carries_the_refs_the_purge_outbox_keeps(self) -> None:
        """A purge whose answer is lost publishes the listing's payloads."""
        calls: list[str] = []
        provider = self._provider([], calls)
        provider.execute_query = AsyncMock(return_value=[
            {"rec": {**_kb("r1"), "deletedAtTimestamp": 5}, "type_doc": None, "held": False, "skip": False},
            {"rec": {**_drive("drive-1"), "deletedAtTimestamp": 5}, "type_doc": None, "held": False, "skip": False},
            {"rec": {**_kb("r2"), "deletedAtTimestamp": 5}, "type_doc": None, "held": True, "skip": False},
        ])

        listing = await provider.get_purgeable_trashed_records(ORG, 10)

        provider.get_record_taxonomy_links.assert_awaited_once_with(["r1"], include_departments=True)
        rows = {row["id"]: row["deleteRecordPayload"] for row in listing["records"]}
        assert set(rows) == {"r1", "drive-1"}
        assert rows["r1"]["taxonomyRefs"] == R1_REFS
        assert "taxonomyRefs" not in rows["drive-1"]


# ---------------------------------------------------------------------------
# Neo4j
# ---------------------------------------------------------------------------


def _neo4j(calls: list[str], *, deleted_rows: list[dict] | None = None, inventory: dict | None = None,
           purge_rows: list[dict] | None = None) -> Neo4jProvider:
    provider = Neo4jProvider(MagicMock(spec=logging.Logger), MagicMock())
    provider.client = AsyncMock()
    provider.begin_transaction = AsyncMock(return_value="txn1")
    provider.commit_transaction = AsyncMock()
    gone: set[str] = set()

    async def execute_query(query: str, parameters: dict | None = None, txn_id: str | None = None) -> list:
        parameters = parameters or {}
        if "AS nodeLabels" in query:
            calls.append("links")
            # A deleted record has no edges left to read.
            return [
                {"recordId": rec, "entityId": node, "nodeLabels": [collection_to_label(collection)], "name": node}
                for rec, collection, node in LINKS
                if rec in parameters.get("keys", []) and rec not in gone
                and collection_to_label(collection) in parameters.get("labels", [])
            ]
        if "AS inventory" in query:
            return [{"inventory": inventory}]
        if "DETACH DELETE" in query:
            calls.append("delete")
            gone.update(
                parameters.get("deleted_ids") or parameters.get("root_ids") or parameters.get("record_ids")
                or parameters.get("keys") or []
            )
            if purge_rows is not None:
                return purge_rows
            return [{"root_ids": parameters.get("root_ids"), "deleted": deleted_rows}] if deleted_rows else []
        return []

    provider.client.execute_query = AsyncMock(side_effect=execute_query)
    return provider


def _neo4j_inventory(records: list[dict]) -> dict:
    return {
        "valid_root_keys": [records[0]["id"]],
        "records_with_type": [{"record": rec, "type_doc": None} for rec in records],
    }


class TestNeo4jRecursiveDelete:
    @pytest.mark.asyncio
    async def test_each_kb_record_names_its_entities_read_before_the_detach_delete(self) -> None:
        calls: list[str] = []
        records = [_kb("folder-1", vrid=None, key_field="id"), _kb("r1", key_field="id"), _kb("r2", key_field="id")]
        provider = _neo4j(calls, inventory=_neo4j_inventory(records))

        result = await provider.delete_records_recursive(["folder-1"], "kb-1")

        assert calls == ["links", "delete"]
        payloads = _payloads(result)
        assert set(payloads) == {"r1", "r2"}
        assert payloads["r1"]["taxonomyRefs"] == R1_REFS
        assert payloads["r2"]["taxonomyRefs"] == R2_REFS

    @pytest.mark.asyncio
    async def test_a_folder_scoped_delete_names_the_entities_of_what_it_removed(self) -> None:
        """The statement re-checks containment; a record moved out meanwhile is
        kept and gets no event."""
        calls: list[str] = []
        r1, r2 = _kb("r1", key_field="id"), _kb("r2", key_field="id")
        provider = _neo4j(
            calls,
            inventory=_neo4j_inventory([r1, r2]),
            deleted_rows=[{"record": r1, "type_doc": None}],
        )

        result = await provider.delete_records_recursive(["r1", "r2"], "kb-1", within_folder_id="folder-1")

        assert calls == ["links", "delete"]
        payloads = _payloads(result)
        assert list(payloads) == ["r1"]
        assert payloads["r1"]["taxonomyRefs"] == R1_REFS

    @pytest.mark.asyncio
    async def test_a_connector_record_names_none_and_is_not_read(self) -> None:
        calls: list[str] = []
        provider = _neo4j(calls, inventory=_neo4j_inventory([_drive("drive-1", key_field="id")]))

        result = await provider.delete_records_recursive(["drive-1"], "conn-1")

        assert calls == ["delete"]
        assert "taxonomyRefs" not in _payloads(result)["drive-1"]

    @pytest.mark.asyncio
    async def test_a_failed_link_read_still_deletes_and_publishes(self) -> None:
        calls: list[str] = []
        provider = _neo4j(calls, inventory=_neo4j_inventory([_kb("r1", key_field="id")]))
        provider.get_record_taxonomy_links = AsyncMock(side_effect=RuntimeError("graph busy"))

        result = await provider.delete_records_recursive(["r1"], "kb-1")

        assert result["success"] is True
        assert calls == ["delete"]
        assert "taxonomyRefs" not in _payloads(result)["r1"]


class TestNeo4jSingleRecordDelete:
    """``delete_single_record`` promises the cleanup ``delete_records_recursive`` does."""

    @pytest.mark.asyncio
    async def test_a_kb_record_names_its_entities_read_before_the_detach_delete(self) -> None:
        calls: list[str] = []
        provider = _neo4j(calls, inventory=_neo4j_inventory([_kb("r1", key_field="id")]))

        result = await provider.delete_single_record("r1")

        assert calls[0] == "links"
        assert "delete" in calls[1:]
        assert _payloads(result)["r1"]["taxonomyRefs"] == R1_REFS

    @pytest.mark.asyncio
    async def test_a_connector_record_names_none_and_is_not_read(self) -> None:
        calls: list[str] = []
        provider = _neo4j(calls, inventory=_neo4j_inventory([_drive("drive-1", key_field="id")]))

        result = await provider.delete_single_record("drive-1")

        assert "links" not in calls
        assert "taxonomyRefs" not in _payloads(result)["drive-1"]


class TestNeo4jPurge:
    @staticmethod
    def _row(record: dict, *, due: bool) -> dict[str, Any]:
        return {"id": record["id"], "due": due, "rec": record, "type_doc": None}

    @pytest.mark.asyncio
    async def test_a_purged_kb_record_names_its_entities_read_before_the_statement(self) -> None:
        """Only the statement knows which records are due, so the links of the
        page are read by key first and the knowledge-base check uses its rows."""
        calls: list[str] = []
        r1, r2, drive = _kb("r1", key_field="id"), _kb("r2", key_field="id"), _drive("drive-1", key_field="id")
        provider = _neo4j(
            calls,
            purge_rows=[self._row(r1, due=True), self._row(drive, due=True), self._row(r2, due=False)],
        )

        result = await provider.purge_trashed_records(["r1", "drive-1", "r2"], ORG, 10)

        assert calls == ["links", "delete"]
        purged = {row["id"]: row["deleteRecordPayload"] for row in result["purged"]}
        assert purged["r1"]["taxonomyRefs"] == R1_REFS
        # Its links were read with the page, but a connector record names none.
        assert "taxonomyRefs" not in purged["drive-1"]
        assert result["kept"] == ["r2"]

    @pytest.mark.asyncio
    async def test_a_failed_link_read_still_purges(self) -> None:
        calls: list[str] = []
        r1 = _kb("r1", key_field="id")
        provider = _neo4j(calls, purge_rows=[self._row(r1, due=True)])
        provider.get_record_taxonomy_links = AsyncMock(side_effect=RuntimeError("graph busy"))

        result = await provider.purge_trashed_records(["r1"], ORG, 10)

        assert [row["id"] for row in result["purged"]] == ["r1"]
        assert "taxonomyRefs" not in result["purged"][0]["deleteRecordPayload"]

    @pytest.mark.asyncio
    async def test_the_page_listing_carries_the_refs_the_purge_outbox_keeps(self) -> None:
        calls: list[str] = []
        provider = _neo4j(calls)
        provider._execute_hinted = AsyncMock(return_value=[
            {"rec": {**_kb("r1", key_field="id"), "deletedAtTimestamp": 5}, "type_doc": None},
            {"rec": {**_drive("drive-1", key_field="id"), "deletedAtTimestamp": 5}, "type_doc": None},
            {"rec": {**_kb("r2", key_field="id"), "deletedAtTimestamp": 5}, "type_doc": None, "skip": True},
        ])

        listing = await provider.get_purgeable_trashed_records(ORG, 10)

        rows = {row["id"]: row["deleteRecordPayload"] for row in listing["records"]}
        assert set(rows) == {"r1", "drive-1"}
        assert rows["r1"]["taxonomyRefs"] == R1_REFS
        assert "taxonomyRefs" not in rows["drive-1"]
