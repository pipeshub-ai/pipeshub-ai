"""The KG-13 backfill command: dry run by default, pages through one org,
reports a failed page and carries on."""
from __future__ import annotations

import io
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.config.constants.arangodb import Connectors, OriginTypes
from app.models.entities import MailRecord, RecordType
from app.scripts.kg_record_people import LINKED_TYPES, backfill, build_parser

BASE = {
    "org_id": "org-1", "record_name": "m", "origin": OriginTypes.CONNECTOR.value,
    "connector_name": Connectors.GOOGLE_MAIL, "connector_id": "conn-1", "version": 1,
    "record_type": RecordType.MAIL,
}


def _mail(key: str) -> MailRecord:
    return MailRecord(id=key, external_record_id=key, from_email="ann@acme.com", to_emails=["bob@acme.com"], **BASE)


def _graph(pages: list[list[str]], failing: frozenset[str] = frozenset()) -> MagicMock:
    graph = MagicMock()
    graph.page_record_ids_by_type = AsyncMock(side_effect=[*pages, []])

    async def _records(ids: list[str]) -> dict:
        if set(ids) & failing:
            raise RuntimeError("down")
        return {i: _mail(i) for i in ids}

    graph.get_typed_records_batch = AsyncMock(side_effect=_records)
    graph.get_user_by_email = AsyncMock(side_effect=lambda email: SimpleNamespace(id=email.split("@")[0]))
    graph.get_user_by_source_id = AsyncMock(return_value=None)
    graph.update_node = AsyncMock(return_value=True)
    graph.get_record_group_organization = AsyncMock(return_value=None)
    graph.stamp_external_org_parents = AsyncMock(return_value=0)
    return graph


def _lines(out: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in out.getvalue().splitlines()]


async def test_a_dry_run_counts_edges_and_writes_nothing() -> None:
    graph, data_store, out = _graph([["m1", "m2"]]), MagicMock(), io.StringIO()
    data_store.execute_idempotent_in_transaction = AsyncMock()
    code = await backfill(graph, data_store, "org-1", apply=False, logger=logging.getLogger("t"), out=out)
    assert code == 0
    assert _lines(out)[-1]["total"] == {"records": 2, "edges": 4, "skipped": 0, "failed_pages": 0}
    data_store.execute_idempotent_in_transaction.assert_not_awaited()
    graph.update_node.assert_not_awaited()
    graph.stamp_external_org_parents.assert_not_awaited()


async def test_apply_writes_each_page_in_one_retried_transaction() -> None:
    graph, out = _graph([["m1"], ["m2"]]), io.StringIO()
    tx_store = MagicMock(
        get_user_by_email=graph.get_user_by_email, get_user_by_source_id=graph.get_user_by_source_id,
        delete_edges_between_collections=AsyncMock(), batch_create_entity_relations=AsyncMock(),
        get_record_group_organization=graph.get_record_group_organization,
    )
    data_store = MagicMock()

    async def _run(fn: object) -> int:
        return await fn(tx_store)

    data_store.execute_idempotent_in_transaction = AsyncMock(side_effect=_run)
    code = await backfill(graph, data_store, "org-1", apply=True, logger=logging.getLogger("t"), out=out)
    assert code == 0
    assert data_store.execute_idempotent_in_transaction.await_count == 2
    assert tx_store.batch_create_entity_relations.await_count == 2
    pages = [c.kwargs["after_key"] for c in graph.page_record_ids_by_type.await_args_list]
    assert pages == [None, "m1", "m2"]
    assert graph.page_record_ids_by_type.await_args.args[1] == LINKED_TYPES


async def test_apply_has_the_entity_index_project_the_orgs_people_again() -> None:
    """The org pass may have run before the backfill; without a re-run the
    people it linked would wait for their records to be re-indexed."""
    from app.modules.indexing.entity_index_rebuild import EntityIndexState

    graph, data_store = _graph([["m1"]]), MagicMock()
    tx_store = MagicMock(
        get_user_by_email=graph.get_user_by_email, get_user_by_source_id=graph.get_user_by_source_id,
        delete_edges_between_collections=AsyncMock(), batch_create_entity_relations=AsyncMock(),
        get_record_group_organization=graph.get_record_group_organization,
    )

    async def _run(fn: object) -> int:
        return await fn(tx_store)

    data_store.execute_idempotent_in_transaction = AsyncMock(side_effect=_run)
    await backfill(graph, data_store, "org-1", apply=True, logger=logging.getLogger("t"), out=io.StringIO())
    graph.update_node.assert_awaited_once_with(
        "org-1", "organizations", {EntityIndexState.STATE: None, EntityIndexState.TARGET: None},
    )

    graph, out = _graph([["m1"]]), io.StringIO()
    graph.update_node = AsyncMock(side_effect=RuntimeError("document not found"))
    code = await backfill(graph, data_store, "org-1", apply=True, logger=logging.getLogger("t"), out=out)
    assert code == 1
    assert _lines(out)[-1]["entity_index_rerun"] is False


async def test_a_failed_page_is_reported_and_the_run_carries_on() -> None:
    graph, out = _graph([["m1"], ["m2"]], failing=frozenset({"m1"})), io.StringIO()
    code = await backfill(graph, MagicMock(), "org-1", apply=False, logger=logging.getLogger("t"), out=out)
    lines = _lines(out)
    assert code == 1
    assert lines[1] == {"after": "m1", "error": "RuntimeError"}
    assert lines[-1]["total"] == {"records": 1, "edges": 2, "skipped": 0, "failed_pages": 1}


def test_apply_is_off_by_default() -> None:
    assert build_parser().parse_args(["backfill", "--org", "o"]).apply is False


def test_every_type_stored_as_a_linked_record_is_backfilled() -> None:
    """Salesforce CASE and TASK are tickets too."""
    assert {"CASE", "TASK", "TICKET", "GROUP_MAIL", "INLINE_COMMENT", "PULL_REQUEST", "DEAL", "PROJECT"} <= set(LINKED_TYPES)


async def test_records_that_could_not_be_read_are_reported() -> None:
    graph, out = _graph([["m1", "m2"]]), io.StringIO()
    graph.get_typed_records_batch = AsyncMock(return_value={"m1": _mail("m1")})
    await backfill(graph, MagicMock(), "org-1", apply=False, logger=logging.getLogger("t"), out=out)
    assert _lines(out)[1]["skipped"] == 1
    assert _lines(out)[-1]["total"]["skipped"] == 1


async def test_apply_stamps_accounts_first_and_links_deals_to_them() -> None:
    from app.models.entities import DealRecord

    graph, out = _graph([["d1"]]), io.StringIO()
    deal = DealRecord(id="d1", external_record_id="d1", record_group_id="rg-1", **{**BASE, "record_type": RecordType.DEAL})
    graph.get_typed_records_batch = AsyncMock(return_value={"d1": deal})
    graph.get_record_group_organization = AsyncMock(return_value="acme")
    graph.stamp_external_org_parents = AsyncMock(return_value=2)
    tx_store = MagicMock(
        get_user_by_email=graph.get_user_by_email, get_user_by_source_id=graph.get_user_by_source_id,
        delete_edges_between_collections=AsyncMock(), batch_create_entity_relations=AsyncMock(),
        get_record_group_organization=graph.get_record_group_organization,
    )
    data_store = MagicMock()

    async def _run(fn: object) -> int:
        return await fn(tx_store)

    data_store.execute_idempotent_in_transaction = AsyncMock(side_effect=_run)
    code = await backfill(graph, data_store, "org-1", apply=True, logger=logging.getLogger("t"), out=out)
    assert code == 0
    graph.stamp_external_org_parents.assert_awaited_once_with("org-1")
    total = _lines(out)[-1]
    assert (total["accounts_stamped"], total["total"]["edges"]) == (2, 1)

    graph.stamp_external_org_parents = AsyncMock(side_effect=RuntimeError("down"))
    graph.page_record_ids_by_type = AsyncMock(side_effect=[["d1"], []])
    out = io.StringIO()
    assert await backfill(graph, data_store, "org-1", apply=True, logger=logging.getLogger("t"), out=out) == 1
    assert _lines(out)[0] == {"stamp_accounts": "failed", "error": "RuntimeError"}
