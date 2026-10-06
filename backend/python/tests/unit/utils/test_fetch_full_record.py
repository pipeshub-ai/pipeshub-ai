"""Tests for app.utils.fetch_full_record — record fetching tools."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.models.entities import TicketRecord
from app.services.graph_db.common.record_visibility import (
    RecordVisibility,
    matches_visibility,
)


class TestFetchFullRecordArgs:
    def test_valid_args(self):
        from app.utils.fetch_full_record import FetchFullRecordArgs

        args = FetchFullRecordArgs(record_ids=["r1", "r2"])
        assert args.record_ids == ["r1", "r2"]
        assert "Fetching full record" in args.reason

    def test_custom_reason(self):
        from app.utils.fetch_full_record import FetchFullRecordArgs

        args = FetchFullRecordArgs(record_ids=["r1"], reason="Need full context")
        assert args.reason == "Need full context"

    def test_missing_record_ids_fails(self):
        from app.utils.fetch_full_record import FetchFullRecordArgs

        with pytest.raises(ValidationError):
            FetchFullRecordArgs()

    def test_empty_record_ids(self):
        from app.utils.fetch_full_record import FetchFullRecordArgs

        args = FetchFullRecordArgs(record_ids=[])
        assert args.record_ids == []




# ===========================================================================
# _enrich_sql_table_with_fk_relations
# ===========================================================================


class _FkGraph:
    """Foreign keys and record documents, answered the way both graph providers answer.

    The edge reads return every neighbour as a dict, trashed or not: the trash
    keeps a dropped table's node and edges until the purge. The batched record
    read filters by org and visibility, as its query does.
    """

    def __init__(self, org_id: str = "org-1") -> None:
        self.org_id = org_id
        self.records: dict[str, dict] = {}
        self.fks: list[tuple[str, str]] = []
        self.lookups: list[tuple[list[str], str, RecordVisibility]] = []
        self.config_service = MagicMock()

    def table(self, record_id: str, *, trashed: bool = False, org_id: str | None = None) -> None:
        doc = {"_key": record_id, "id": record_id, "orgId": org_id or self.org_id, "recordName": record_id}
        if trashed:
            doc["isDeleted"] = True
        self.records[record_id] = doc

    def fk(self, child: str, parent: str) -> None:
        self.fks.append((child, parent))

    async def get_child_record_ids_by_relation_type(
        self, record_id: str, relation_type: str, transaction: str | None = None
    ) -> list[dict]:
        assert relation_type == "FOREIGN_KEY"
        return [
            {"record_id": c, "childTable": f"public.{c}", "sourceColumn": f"{p}_id", "targetColumn": "id"}
            for c, p in self.fks if p == record_id
        ]

    async def get_parent_record_ids_by_relation_type(
        self, record_id: str, relation_type: str, transaction: str | None = None
    ) -> list[dict]:
        assert relation_type == "FOREIGN_KEY"
        return [
            {"record_id": p, "parentTable": f"public.{p}", "sourceColumn": f"{p}_id", "targetColumn": "id"}
            for c, p in self.fks if c == record_id
        ]

    async def get_records_by_record_ids(
        self, record_ids: list[str], org_id: str, visibility: RecordVisibility = RecordVisibility.LIVE
    ) -> list[dict]:
        self.lookups.append((list(record_ids), org_id, visibility))
        return [
            dict(doc) for rid in record_ids
            if (doc := self.records.get(rid)) and doc["orgId"] == org_id and matches_visibility(doc, visibility)
        ]


def _shop(*, trashed: tuple[str, ...] = ()) -> _FkGraph:
    """orders -> customers, orders -> products, products -> suppliers, reviews -> orders."""
    graph = _FkGraph()
    for name in ("orders", "customers", "products", "suppliers", "reviews"):
        graph.table(name, trashed=name in trashed)
    graph.fk("orders", "customers")
    graph.fk("orders", "products")
    graph.fk("products", "suppliers")
    graph.fk("reviews", "orders")
    return graph


def _ids(relations: list[dict]) -> set[str]:
    return {rel["record_id"] for rel in relations}


class TestEnrichSqlTableWithFkRelations:
    @pytest.mark.asyncio
    async def test_enriches_with_fk_relations(self) -> None:
        from app.utils.fetch_full_record import _enrich_sql_table_with_fk_relations

        graph = _shop()
        result = await _enrich_sql_table_with_fk_relations(
            {"id": "orders", "record_name": "orders"}, graph, "org-1"
        )

        assert _ids(result["fk_parent_record_ids"]) == {"customers", "products"}
        assert _ids(result["fk_child_record_ids"]) == {"reviews"}
        parent = next(r for r in result["fk_parent_record_ids"] if r["record_id"] == "customers")
        assert parent == {
            "record_id": "customers", "parentTable": "public.customers",
            "sourceColumn": "customers_id", "targetColumn": "id",
        }

    @pytest.mark.asyncio
    async def test_leaves_out_tables_in_the_trash(self) -> None:
        """The edges outlive the drop; the trashed table's name and columns must not reach the agent."""
        from app.utils.fetch_full_record import _enrich_sql_table_with_fk_relations

        graph = _shop(trashed=("customers", "reviews"))
        result = await _enrich_sql_table_with_fk_relations(
            {"id": "orders", "record_name": "orders"}, graph, "org-1"
        )

        assert _ids(result["fk_parent_record_ids"]) == {"products"}
        assert result["fk_child_record_ids"] == []
        said = repr(result)
        assert "customers" not in said
        assert "reviews" not in said

    @pytest.mark.asyncio
    async def test_checks_every_neighbour_in_one_live_lookup(self) -> None:
        from app.utils.fetch_full_record import _enrich_sql_table_with_fk_relations

        graph = _shop()
        await _enrich_sql_table_with_fk_relations({"id": "orders"}, graph, "org-1")

        assert len(graph.lookups) == 1
        ids, org_id, visibility = graph.lookups[0]
        assert set(ids) == {"customers", "products", "reviews"}
        assert org_id == "org-1"
        assert visibility is RecordVisibility.LIVE

    @pytest.mark.asyncio
    async def test_no_neighbours_needs_no_lookup(self) -> None:
        from app.utils.fetch_full_record import _enrich_sql_table_with_fk_relations

        graph = _shop()
        graph.table("audit_log")
        result = await _enrich_sql_table_with_fk_relations({"id": "audit_log"}, graph, "org-1")

        assert graph.lookups == []
        assert result["fk_parent_record_ids"] == []
        assert result["fk_child_record_ids"] == []

    @pytest.mark.asyncio
    async def test_a_neighbour_in_another_org_is_left_out(self) -> None:
        from app.utils.fetch_full_record import _enrich_sql_table_with_fk_relations

        graph = _shop()
        graph.table("customers", org_id="org-2")
        result = await _enrich_sql_table_with_fk_relations({"id": "orders"}, graph, "org-1")

        assert _ids(result["fk_parent_record_ids"]) == {"products"}

    @pytest.mark.asyncio
    async def test_a_failed_live_lookup_lists_no_neighbours(self) -> None:
        from app.utils.fetch_full_record import _enrich_sql_table_with_fk_relations

        graph = _shop()
        graph.get_records_by_record_ids = AsyncMock(side_effect=RuntimeError("graph down"))
        result = await _enrich_sql_table_with_fk_relations({"id": "orders"}, graph, "org-1")

        assert result["fk_parent_record_ids"] == []
        assert result["fk_child_record_ids"] == []

    @pytest.mark.asyncio
    async def test_without_an_org_no_neighbour_is_listed(self) -> None:
        from app.utils.fetch_full_record import _enrich_sql_table_with_fk_relations

        graph = _shop()
        result = await _enrich_sql_table_with_fk_relations({"id": "orders"}, graph, None)

        assert graph.lookups == []
        assert result["fk_parent_record_ids"] == []
        assert result["fk_child_record_ids"] == []

    @pytest.mark.asyncio
    async def test_returns_copy_not_original(self):
        from app.utils.fetch_full_record import _enrich_sql_table_with_fk_relations

        record = {"id": "orders", "record_name": "orders"}
        result = await _enrich_sql_table_with_fk_relations(record, _shop(), "org-1")

        assert "fk_parent_record_ids" not in record
        assert "fk_parent_record_ids" in result

    @pytest.mark.asyncio
    async def test_skips_when_no_record_id(self):
        from app.utils.fetch_full_record import _enrich_sql_table_with_fk_relations

        record = {"record_name": "no_id_table"}
        result = await _enrich_sql_table_with_fk_relations(record, _shop(), "org-1")
        assert result is record

    @pytest.mark.asyncio
    async def test_uses_record_id_field(self):
        from app.utils.fetch_full_record import _enrich_sql_table_with_fk_relations

        result = await _enrich_sql_table_with_fk_relations({"record_id": "products"}, _shop(), "org-1")

        assert _ids(result["fk_parent_record_ids"]) == {"suppliers"}
        assert _ids(result["fk_child_record_ids"]) == {"orders"}

    @pytest.mark.asyncio
    async def test_child_fetch_exception_handled(self):
        from app.utils.fetch_full_record import _enrich_sql_table_with_fk_relations

        graph = _shop()
        graph.get_child_record_ids_by_relation_type = AsyncMock(side_effect=RuntimeError("graph down"))
        result = await _enrich_sql_table_with_fk_relations({"id": "orders"}, graph, "org-1")

        assert result["fk_child_record_ids"] == []
        assert _ids(result["fk_parent_record_ids"]) == {"customers", "products"}

    @pytest.mark.asyncio
    async def test_parent_fetch_exception_handled(self):
        from app.utils.fetch_full_record import _enrich_sql_table_with_fk_relations

        graph = _shop()
        graph.get_parent_record_ids_by_relation_type = AsyncMock(side_effect=RuntimeError("graph down"))
        result = await _enrich_sql_table_with_fk_relations({"id": "orders"}, graph, "org-1")

        assert _ids(result["fk_child_record_ids"]) == {"reviews"}
        assert result["fk_parent_record_ids"] == []


# ===========================================================================
# _apply_live_ticket_context_metadata
# ===========================================================================


class TestApplyLiveTicketContextMetadata:
    @pytest.mark.asyncio
    async def test_skips_non_ticket(self):
        from app.utils.fetch_full_record import _apply_live_ticket_context_metadata

        record = {"id": "r1", "record_type": "FILE", "context_metadata": "original"}
        config_service = MagicMock()
        graph_provider = AsyncMock()

        with patch.object(
            TicketRecord,
            "to_llm_context_with_live_fields",
            new=AsyncMock(),
        ) as mock_live:
            await _apply_live_ticket_context_metadata(
                record,
                config_service=config_service,
                graph_provider=graph_provider,
                frontend_url=None,
            )

        mock_live.assert_not_called()
        assert record["context_metadata"] == "original"

    @pytest.mark.asyncio
    async def test_skips_without_config_service(self):
        from app.utils.fetch_full_record import _apply_live_ticket_context_metadata

        record = {"id": "r1", "record_type": "TICKET", "context_metadata": "original"}

        with patch.object(
            TicketRecord,
            "to_llm_context_with_live_fields",
            new=AsyncMock(),
        ) as mock_live:
            await _apply_live_ticket_context_metadata(
                record,
                config_service=None,
                graph_provider=AsyncMock(),
                frontend_url=None,
            )

        mock_live.assert_not_called()
        assert record["context_metadata"] == "original"

    @pytest.mark.asyncio
    async def test_upgrades_ticket_with_live_fields(self):
        from app.utils.fetch_full_record import _apply_live_ticket_context_metadata

        record = {"id": "ticket-1", "record_type": "TICKET"}
        config_service = MagicMock()
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={"status": "OPEN"})

        ticket = MagicMock(spec=TicketRecord)
        ticket.to_llm_context_with_live_fields = AsyncMock(return_value="live context")

        with patch(
            "app.utils.fetch_full_record.create_record_instance_from_dict",
            return_value=ticket,
        ):
            await _apply_live_ticket_context_metadata(
                record,
                config_service=config_service,
                graph_provider=graph_provider,
                frontend_url="http://frontend",
            )

        assert record["context_metadata"] == "live context"
        ticket.to_llm_context_with_live_fields.assert_awaited_once_with(
            frontend_url="http://frontend",
            config_service=config_service,
        )


# ===========================================================================
# _fetch_multiple_records_impl
# ===========================================================================


class TestFetchMultipleRecordsImpl:
    @pytest.mark.asyncio
    async def test_found_records(self):
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        records_map = {
            "vr1": {"id": "r1", "record_name": "test1", "content": "Record 1 data"},
            "vr2": {"id": "r2", "record_name": "test2", "content": "Record 2 data"},
        }
        result = await _fetch_multiple_records_impl(["r1", "r2"], records_map)
        assert result["ok"] is True
        assert len(result["records"]) == 2

    @pytest.mark.asyncio
    async def test_map_hit_ticket_upgrades_live_context_metadata(self):
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        records_map = {
            "vr1": {
                "id": "r1",
                "record_name": "test",
                "record_type": "TICKET",
                "context_metadata": "graph-only",
            },
        }
        graph_provider = AsyncMock()
        graph_provider.config_service = MagicMock()

        with patch(
            "app.utils.fetch_full_record._apply_live_ticket_context_metadata",
            new=AsyncMock(),
        ) as mock_upgrade:
            result = await _fetch_multiple_records_impl(
                ["r1"],
                records_map,
                graph_provider=graph_provider,
            )

        assert result["ok"] is True
        mock_upgrade.assert_awaited_once()
        assert mock_upgrade.call_args[0][0]["id"] == "r1"

    @pytest.mark.asyncio
    async def test_graph_fallback_ticket_upgrades_live_context_metadata(self):
        from app.config.constants.arangodb import ProgressStatus
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        records_map = {}
        graph_provider = AsyncMock()
        graph_provider.check_record_access_with_details = AsyncMock(return_value=True)
        graph_provider.get_document = AsyncMock(return_value={
            "indexingStatus": ProgressStatus.COMPLETED.value,
            "virtualRecordId": "vrid-1",
            "recordType": "TICKET",
        })
        graph_provider.config_service = MagicMock()

        async def fake_get_record(vrid, map_, *args, **kwargs):
            map_[vrid] = {
                "id": "r1",
                "record_type": "TICKET",
                "context_metadata": "graph-only",
            }

        mock_blob_instance = MagicMock()
        mock_blob_instance.config_service = MagicMock()
        mock_blob_instance.config_service.get_config = AsyncMock(return_value={})

        with patch(
            "app.utils.fetch_full_record.BlobStorage",
            return_value=mock_blob_instance,
        ), patch(
            "app.utils.fetch_full_record.get_record",
            side_effect=fake_get_record,
        ), patch(
            "app.utils.fetch_full_record._apply_live_ticket_context_metadata",
            new=AsyncMock(),
        ) as mock_upgrade:
            result = await _fetch_multiple_records_impl(
                ["r1"],
                records_map,
                graph_provider=graph_provider,
                org_id="org-1",
                user_id="u1",
            )

        assert result["ok"] is True
        mock_upgrade.assert_awaited_once()
        assert mock_upgrade.call_args[0][0]["id"] == "r1"

    @pytest.mark.asyncio
    async def test_partial_found(self):
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        records_map = {
            "vr1": {"id": "r1", "record_name": "test", "content": "data"},
        }
        result = await _fetch_multiple_records_impl(["r1", "r_missing"], records_map)
        assert result["ok"] is True
        assert len(result["records"]) == 1
        assert "r_missing" in result["not_available_ids"]

    @pytest.mark.asyncio
    async def test_none_found(self):
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        records_map = {
            "vr1": {"id": "r1", "content": "data"},
        }
        result = await _fetch_multiple_records_impl(["r_missing"], records_map)
        assert result["ok"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_empty_record_ids(self):
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        result = await _fetch_multiple_records_impl([], {"vr1": {"id": "r1"}})
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_empty_virtual_record_map(self):
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        result = await _fetch_multiple_records_impl(["r1"], {})
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_none_values_in_map_skipped(self):
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        records_map = {
            "vr1": None,
            "vr2": {"id": "r2", "record_name": "test", "content": "data"},
        }
        result = await _fetch_multiple_records_impl(["r2"], records_map)
        assert result["ok"] is True
        assert len(result["records"]) == 1

    @pytest.mark.asyncio
    async def test_sql_table_enriched_with_fk_when_graph_provider(self):
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        records_map = {
            "vr1": {"id": "products", "record_name": "products", "record_type": "SQL_TABLE"},
        }
        result = await _fetch_multiple_records_impl(
            ["products"], records_map, graph_provider=_shop(), org_id="org-1"
        )

        assert result["ok"] is True
        rec = result["records"][0]
        assert _ids(rec["fk_child_record_ids"]) == {"orders"}
        assert _ids(rec["fk_parent_record_ids"]) == {"suppliers"}

    @pytest.mark.asyncio
    async def test_sql_table_lists_only_live_neighbours(self) -> None:
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        records_map = {
            "vr1": {"id": "orders", "record_name": "orders", "record_type": "SQL_TABLE"},
            "vr2": {"id": "products", "record_name": "products", "record_type": "SQL_TABLE"},
        }
        result = await _fetch_multiple_records_impl(
            ["orders", "products"], records_map,
            graph_provider=_shop(trashed=("customers", "suppliers")), org_id="org-1",
        )

        by_id = {rec["id"]: rec for rec in result["records"]}
        assert _ids(by_id["orders"]["fk_parent_record_ids"]) == {"products"}
        assert _ids(by_id["orders"]["fk_child_record_ids"]) == {"reviews"}
        assert by_id["products"]["fk_parent_record_ids"] == []
        assert _ids(by_id["products"]["fk_child_record_ids"]) == {"orders"}
        said = repr(result)
        assert "customers" not in said
        assert "suppliers" not in said

    @pytest.mark.asyncio
    async def test_sql_table_not_enriched_without_graph_provider(self):
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        records_map = {
            "vr1": {"id": "r1", "record_name": "test", "record_type": "SQL_TABLE"},
        }
        result = await _fetch_multiple_records_impl(["r1"], records_map)

        assert result["ok"] is True
        rec = result["records"][0]
        assert "fk_child_record_ids" not in rec

    @pytest.mark.asyncio
    async def test_non_sql_table_not_enriched(self):
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        records_map = {
            "vr1": {"id": "r1", "record_name": "test", "record_type": "DOCUMENT"},
        }
        graph_provider = AsyncMock()

        result = await _fetch_multiple_records_impl(
            ["r1"], records_map, graph_provider=graph_provider
        )

        assert result["ok"] is True
        rec = result["records"][0]
        assert "fk_child_record_ids" not in rec

    @pytest.mark.asyncio
    async def test_missing_record_fetched_from_blob(self):
        from app.config.constants.arangodb import ProgressStatus
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        records_map = {}
        graph_provider = AsyncMock()
        graph_provider.check_record_access_with_details = AsyncMock(return_value=True)
        graph_provider.get_document = AsyncMock(return_value={
            "indexingStatus": ProgressStatus.COMPLETED.value,
            "virtualRecordId": "vrid-1",
            "recordType": "DOCUMENT",
        })
        graph_provider.config_service = MagicMock()

        async def fake_get_record(vrid, map_, *args, **kwargs):
            map_[vrid] = {"id": "r1", "content": "fetched from blob"}

        mock_blob_instance = MagicMock()
        mock_blob_instance.config_service = MagicMock()
        mock_blob_instance.config_service.get_config = AsyncMock(return_value={})

        with patch(
            "app.utils.fetch_full_record.BlobStorage",
            return_value=mock_blob_instance,
        ), patch(
            "app.utils.fetch_full_record.get_record",
            side_effect=fake_get_record,
        ):
            result = await _fetch_multiple_records_impl(
                ["r1"], records_map,
                graph_provider=graph_provider,
                org_id="org-1",
                user_id="u1",
            )

        assert result["ok"] is True
        assert result["record_count"] == 1

    @pytest.mark.asyncio
    async def test_fetched_sql_table_enriched_with_fk(self):
        from app.config.constants.arangodb import ProgressStatus
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        records_map = {}
        graph_provider = _shop(trashed=("customers",))
        graph_provider.check_record_access_with_details = AsyncMock(return_value=True)
        graph_provider.get_document = AsyncMock(return_value={
            "indexingStatus": ProgressStatus.COMPLETED.value,
            "virtualRecordId": "vrid-1",
            "recordType": "SQL_TABLE",
        })

        async def fake_get_record(vrid, map_, *args, **kwargs):
            map_[vrid] = {"id": "orders", "record_type": "SQL_TABLE", "content": "data"}

        mock_blob_instance = MagicMock()
        mock_blob_instance.config_service = MagicMock()
        mock_blob_instance.config_service.get_config = AsyncMock(return_value={})

        with patch(
            "app.utils.fetch_full_record.BlobStorage",
            return_value=mock_blob_instance,
        ), patch(
            "app.utils.fetch_full_record.get_record",
            side_effect=fake_get_record,
        ):
            result = await _fetch_multiple_records_impl(
                ["orders"], records_map,
                graph_provider=graph_provider,
                org_id="org-1",
                user_id="u1",
            )

        assert result["ok"] is True
        rec = result["records"][0]
        assert _ids(rec["fk_child_record_ids"]) == {"reviews"}
        assert _ids(rec["fk_parent_record_ids"]) == {"products"}

    @pytest.mark.asyncio
    async def test_recordType_key_also_triggers_fk_enrichment(self):
        from app.utils.fetch_full_record import _fetch_multiple_records_impl

        records_map = {
            "vr1": {"id": "suppliers", "record_name": "suppliers", "recordType": "SQL_TABLE"},
        }
        result = await _fetch_multiple_records_impl(
            ["suppliers"], records_map, graph_provider=_shop(), org_id="org-1"
        )

        assert result["ok"] is True
        rec = result["records"][0]
        assert _ids(rec["fk_child_record_ids"]) == {"products"}


# ===========================================================================
# create_fetch_full_record_tool
# ===========================================================================


class TestFetchMultipleRecordsImplGraphFallback:
    """Covers the org_id + graph_provider fallback branch (lines 97-125 in source).

    Every case here resolves an ID that is NOT in the map, so each one needs
    `user_id` — that path re-checks access itself and is skipped without it.
    """

    def _make_graph_provider(self, *, document=None, raises=None, endpoints=None, access=True):
        gp = MagicMock()
        gp.config_service = MagicMock()
        if raises is not None:
            gp.get_document = AsyncMock(side_effect=raises)
        else:
            gp.get_document = AsyncMock(return_value=document)
        gp.config_service.get_config = AsyncMock(
            return_value=endpoints if endpoints is not None else {},
        )
        gp.check_record_access_with_details = AsyncMock(
            return_value={"record": {"_key": "r1"}} if access else None,
        )
        return gp

    @pytest.mark.asyncio
    async def test_graph_fallback_returns_blob_record(self):
        """graphDb lookup hits, indexing COMPLETED, BlobStorage populates the map."""
        from app.utils import fetch_full_record as ffr

        graph_provider = self._make_graph_provider(
            document={"indexingStatus": "COMPLETED", "virtualRecordId": "vrid-1"},
            endpoints={"frontend": {"publicEndpoint": "https://app.example"}},
        )

        async def _fake_get_record(vrid, results_map, *args, **kwargs):
            results_map[vrid] = {"id": "r1", "content": "blob-content"}

        with patch.object(ffr, "BlobStorage") as blob_cls, patch.object(
            ffr, "get_record", new=AsyncMock(side_effect=_fake_get_record),
        ) as mock_get_record:
            blob_cls.return_value = MagicMock(
                config_service=graph_provider.config_service,
            )

            result = await ffr._fetch_multiple_records_impl(
                ["r1"], {}, org_id="org-1", graph_provider=graph_provider, user_id="u1",
            )

        assert result["ok"] is True
        assert len(result["records"]) == 1
        assert result["records"][0]["virtual_record_id"] == "vrid-1"
        assert result["records"][0]["content"] == "blob-content"
        mock_get_record.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_graph_fallback_endpoint_config_exception(self):
        """If fetching endpoints config raises, we still proceed with frontend_url=None."""
        from app.utils import fetch_full_record as ffr

        graph_provider = self._make_graph_provider(
            document={"indexingStatus": "COMPLETED", "virtualRecordId": "vrid-1"},
        )
        graph_provider.config_service.get_config = AsyncMock(
            side_effect=RuntimeError("etcd down"),
        )

        async def _fake_get_record(vrid, results_map, *args, **kwargs):
            results_map[vrid] = {"id": "r1", "content": "blob-content"}

        with patch.object(ffr, "BlobStorage") as blob_cls, patch.object(
            ffr, "get_record", new=AsyncMock(side_effect=_fake_get_record),
        ):
            blob_cls.return_value = MagicMock(
                config_service=graph_provider.config_service,
            )

            result = await ffr._fetch_multiple_records_impl(
                ["r1"], {}, org_id="org-1", graph_provider=graph_provider, user_id="u1",
            )

        assert result["ok"] is True
        assert len(result["records"]) == 1

    @pytest.mark.asyncio
    async def test_graph_fallback_indexing_not_completed(self):
        """If indexingStatus is not COMPLETED, record is marked not available."""
        from app.utils import fetch_full_record as ffr

        graph_provider = self._make_graph_provider(
            document={"indexingStatus": "IN_PROGRESS", "virtualRecordId": "vrid-1"},
        )

        result = await ffr._fetch_multiple_records_impl(
            ["r1"], {}, org_id="org-1", graph_provider=graph_provider, user_id="u1",
        )

        assert result["ok"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_graph_fallback_document_not_found(self):
        """graph_provider.get_document returns None → record not available."""
        from app.utils import fetch_full_record as ffr

        graph_provider = self._make_graph_provider(document=None)

        result = await ffr._fetch_multiple_records_impl(
            ["r1"], {}, org_id="org-1", graph_provider=graph_provider, user_id="u1",
        )

        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_graph_fallback_exception_swallowed(self):
        """Exceptions inside the fallback block are swallowed; record marked not available."""
        from app.utils import fetch_full_record as ffr

        graph_provider = self._make_graph_provider(raises=RuntimeError("arango down"))

        result = await ffr._fetch_multiple_records_impl(
            ["r1"], {}, org_id="org-1", graph_provider=graph_provider, user_id="u1",
        )

        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_graph_fallback_blob_record_missing(self):
        """get_record does not populate the map → record marked not available."""
        from app.utils import fetch_full_record as ffr

        graph_provider = self._make_graph_provider(
            document={"indexingStatus": "COMPLETED", "virtualRecordId": "vrid-1"},
        )

        async def _fake_get_record(vrid, results_map, *args, **kwargs):
            pass

        with patch.object(ffr, "BlobStorage") as blob_cls, patch.object(
            ffr, "get_record", new=AsyncMock(side_effect=_fake_get_record),
        ):
            blob_cls.return_value = MagicMock(
                config_service=graph_provider.config_service,
            )

            result = await ffr._fetch_multiple_records_impl(
                ["r1"], {}, org_id="org-1", graph_provider=graph_provider, user_id="u1",
            )

        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_graph_fallback_endpoints_not_dict(self):
        """If endpoints_config is not a dict, we skip it cleanly and continue."""
        from app.utils import fetch_full_record as ffr

        graph_provider = self._make_graph_provider(
            document={"indexingStatus": "COMPLETED", "virtualRecordId": "vrid-1"},
            endpoints="not-a-dict",  # pyright: ignore [reportArgumentType]
        )

        async def _fake_get_record(vrid, results_map, *args, **kwargs):
            results_map[vrid] = {"id": "r1"}

        with patch.object(ffr, "BlobStorage") as blob_cls, patch.object(
            ffr, "get_record", new=AsyncMock(side_effect=_fake_get_record),
        ):
            blob_cls.return_value = MagicMock(
                config_service=graph_provider.config_service,
            )

            result = await ffr._fetch_multiple_records_impl(
                ["r1"], {}, org_id="org-1", graph_provider=graph_provider, user_id="u1",
            )

        assert result["ok"] is True


class TestColdPathAccessControl:
    """The map is built from ACL-filtered retrieval results, but an arbitrary
    Record ID is not — the model can now get one from navigate/lookup_record.
    So resolving an ID that is not in the map has to re-check access itself."""

    def _graph_provider(self, *, access):
        gp = MagicMock()
        gp.config_service = MagicMock()
        gp.config_service.get_config = AsyncMock(return_value={})
        gp.check_record_access_with_details = AsyncMock(
            return_value={"record": {"_key": "r1"}} if access else None,
        )
        gp.get_document = AsyncMock(return_value={
            "indexingStatus": "COMPLETED", "virtualRecordId": "vrid-1",
        })
        return gp

    @pytest.mark.asyncio
    async def test_denied_record_is_never_read(self):
        from app.utils import fetch_full_record as ffr

        graph_provider = self._graph_provider(access=False)

        result = await ffr._fetch_multiple_records_impl(
            ["r1"], {}, org_id="org-1", graph_provider=graph_provider, user_id="u1",
        )

        assert result["ok"] is False
        graph_provider.get_document.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_denied_looks_the_same_as_missing(self):
        """A denial must not be distinguishable from a non-existent record."""
        from app.utils import fetch_full_record as ffr

        denied = await ffr._fetch_multiple_records_impl(
            ["r1"], {"vr0": {"id": "other"}},
            org_id="org-1", graph_provider=self._graph_provider(access=False), user_id="u1",
        )
        missing = await ffr._fetch_multiple_records_impl(
            ["r1"], {"vr0": {"id": "other"}},
            org_id="org-1", graph_provider=None, user_id="u1",
        )

        assert denied == missing

    @pytest.mark.asyncio
    async def test_without_user_id_the_path_is_skipped_not_served_unchecked(self):
        from app.utils import fetch_full_record as ffr

        graph_provider = self._graph_provider(access=True)

        result = await ffr._fetch_multiple_records_impl(
            ["r1"], {}, org_id="org-1", graph_provider=graph_provider,
        )

        assert result["ok"] is False
        graph_provider.get_document.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_records_already_in_the_map_are_not_re_checked(self):
        """Those came from an ACL-filtered search — re-checking each one would
        add a permission traversal per record for no security gain."""
        from app.utils import fetch_full_record as ffr

        graph_provider = self._graph_provider(access=True)

        result = await ffr._fetch_multiple_records_impl(
            ["r1"], {"vr1": {"id": "r1", "record_name": "test", "content": "data"}},
            org_id="org-1", graph_provider=graph_provider, user_id="u1",
        )

        assert result["ok"] is True
        graph_provider.check_record_access_with_details.assert_not_awaited()


class TestCreateFetchFullRecordTool:
    def test_creates_tool(self):
        from app.utils.fetch_full_record import create_fetch_full_record_tool

        records_map = {"vr1": {"id": "r1", "content": "data"}}
        tool = create_fetch_full_record_tool(records_map)
        assert tool.name == "fetch_full_record"

    def test_creates_tool_with_optional_deps(self):
        from app.utils.fetch_full_record import create_fetch_full_record_tool

        tool = create_fetch_full_record_tool(
            virtual_record_id_to_result={},
            graph_provider=AsyncMock(),
            blob_store=AsyncMock(),
            org_id="org-1",
        )
        assert tool.name == "fetch_full_record"

    @pytest.mark.asyncio
    async def test_tool_invocation_success(self):
        from app.utils.fetch_full_record import create_fetch_full_record_tool

        records_map = {"vr1": {"id": "r1", "record_name": "test", "content": "data"}}
        tool = create_fetch_full_record_tool(records_map)
        result = await tool.ainvoke({"record_ids": ["r1"], "reason": "test"})
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_tool_invocation_not_found(self):
        from app.utils.fetch_full_record import create_fetch_full_record_tool

        records_map = {}
        tool = create_fetch_full_record_tool(records_map)
        result = await tool.ainvoke({"record_ids": ["missing"], "reason": "test"})
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_tool_invocation_exception_returns_error_dict(self):
        """When _fetch_multiple_records_impl raises, the tool catches and returns error dict."""
        from unittest.mock import patch as _patch

        from app.utils.fetch_full_record import create_fetch_full_record_tool

        records_map = {"vr1": {"id": "r1", "content": "data"}}
        tool = create_fetch_full_record_tool(records_map)

        with _patch(
            "app.utils.fetch_full_record._fetch_multiple_records_impl",
            side_effect=RuntimeError("unexpected failure"),
        ):
            result = await tool.ainvoke({"record_ids": ["r1"], "reason": "test"})

        assert result["ok"] is False
        assert "Failed to fetch records" in result["error"]
        assert "unexpected failure" in result["error"]

    @pytest.mark.asyncio
    async def test_tool_invocation_generic_exception(self):
        """Cover the except branch with a different exception type."""
        from unittest.mock import patch as _patch

        from app.utils.fetch_full_record import create_fetch_full_record_tool

        records_map = {}
        tool = create_fetch_full_record_tool(records_map)

        with _patch(
            "app.utils.fetch_full_record._fetch_multiple_records_impl",
            side_effect=ValueError("bad value"),
        ):
            result = await tool.ainvoke({"record_ids": ["x"], "reason": "test"})

        assert result["ok"] is False
        assert "bad value" in result["error"]

    @pytest.mark.asyncio
    async def test_tool_passes_graph_provider_and_blob_store(self):
        """Verify that optional deps are forwarded to _fetch_multiple_records_impl."""
        from unittest.mock import patch as _patch

        from app.utils.fetch_full_record import create_fetch_full_record_tool

        mock_gp = AsyncMock()
        mock_bs = AsyncMock()
        records_map = {"vr1": {"id": "r1"}}

        tool = create_fetch_full_record_tool(
            records_map,
            graph_provider=mock_gp,
            blob_store=mock_bs,
            org_id="org-1",
        )

        with _patch(
            "app.utils.fetch_full_record._fetch_multiple_records_impl",
            new_callable=AsyncMock,
            return_value={"ok": True, "records": [], "record_count": 0},
        ) as mock_impl:
            await tool.ainvoke({"record_ids": ["r1"], "reason": "test"})

        mock_impl.assert_awaited_once()
        call_args = mock_impl.call_args
        assert call_args.kwargs["graph_provider"] is mock_gp
        assert call_args.kwargs["blob_store"] is mock_bs
        assert call_args.kwargs["org_id"] == "org-1"


