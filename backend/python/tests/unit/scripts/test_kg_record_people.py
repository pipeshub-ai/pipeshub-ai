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
    return graph


def _lines(out: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in out.getvalue().splitlines()]


async def test_a_dry_run_counts_edges_and_writes_nothing() -> None:
    graph, data_store, out = _graph([["m1", "m2"]]), MagicMock(), io.StringIO()
    data_store.execute_idempotent_in_transaction = AsyncMock()
    code = await backfill(graph, data_store, "org-1", apply=False, logger=logging.getLogger("t"), out=out)
    assert code == 0
    assert _lines(out)[-1]["total"] == {"records": 2, "edges": 4, "failed_pages": 0}
    data_store.execute_idempotent_in_transaction.assert_not_awaited()


async def test_apply_writes_each_page_in_one_retried_transaction() -> None:
    graph, out = _graph([["m1"], ["m2"]]), io.StringIO()
    tx_store = MagicMock(
        get_user_by_email=graph.get_user_by_email, get_user_by_source_id=graph.get_user_by_source_id,
        delete_edges_from=AsyncMock(), batch_create_entity_relations=AsyncMock(),
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


async def test_a_failed_page_is_reported_and_the_run_carries_on() -> None:
    graph, out = _graph([["m1"], ["m2"]], failing=frozenset({"m1"})), io.StringIO()
    code = await backfill(graph, MagicMock(), "org-1", apply=False, logger=logging.getLogger("t"), out=out)
    lines = _lines(out)
    assert code == 1
    assert lines[0] == {"after": "m1", "error": "RuntimeError"}
    assert lines[-1]["total"] == {"records": 1, "edges": 2, "failed_pages": 1}


def test_apply_is_off_by_default() -> None:
    assert build_parser().parse_args(["backfill", "--org", "o"]).apply is False
