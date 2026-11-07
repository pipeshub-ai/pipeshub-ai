"""Record -> CRM account edges (KG-13 slice 3a): a deal, case or task links
to its account's organisation, INFERRED, replacing only account edges."""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import Connectors, OriginTypes
from app.connectors.core.base.data_processor.record_organizations import (
    link_record_organization,
)
from app.models.entities import DealRecord, FileRecord, RecordType, TicketRecord

BASE = {
    "id": "rec-1", "org_id": "org-1", "external_record_id": "ext-1", "record_name": "r",
    "origin": OriginTypes.CONNECTOR.value, "connector_name": Connectors.SALESFORCE, "connector_id": "conn-1",
    "version": 1, "record_group_id": "rg-acme",
}


def _store(organization: str | None = "acme") -> MagicMock:
    store = MagicMock()
    store.get_record_group_organization = AsyncMock(return_value=organization)
    store.replace_record_entity_relations = AsyncMock()
    return store


def _deal(**kw: object) -> DealRecord:
    return DealRecord(record_type=RecordType.DEAL, **{**BASE, **kw})


async def test_a_deal_links_to_its_account_as_inferred() -> None:
    store = _store()
    assert await link_record_organization(_deal(), store, logging.getLogger("t")) == 1
    store.get_record_group_organization.assert_awaited_once_with("rg-acme", "org-1")
    # Only the connector's own links: organisations its content names stay.
    record_id, collections, origin, (edge,) = store.replace_record_entity_relations.await_args.args
    assert (record_id, collections, origin) == ("rec-1", ["organizations"], "INFERRED")
    assert (edge["_from"], edge["_to"], edge["edgeType"]) == ("records/rec-1", "organizations/acme", "FOR_ACCOUNT")
    assert (edge["origin"], edge["source"]) == ("INFERRED", "conn-1")


async def test_a_case_whose_group_has_no_account_loses_its_old_edge() -> None:
    store = _store(organization=None)
    case = TicketRecord(record_type=RecordType.CASE, **BASE)
    assert await link_record_organization(case, store, logging.getLogger("t")) == 0
    assert store.replace_record_entity_relations.await_args.args[3] == []


async def test_other_record_types_are_left_alone() -> None:
    store = _store()
    file_record = FileRecord(record_type=RecordType.FILE, is_file=True, extension="txt", **BASE)
    assert await link_record_organization(file_record, store, logging.getLogger("t")) == 0
    assert await link_record_organization(_deal(record_group_id=None), store, logging.getLogger("t")) == 0
    store.get_record_group_organization.assert_not_awaited()
    store.replace_record_entity_relations.assert_not_awaited()


async def test_a_failed_lookup_keeps_the_existing_edge() -> None:
    store = _store()
    store.get_record_group_organization.side_effect = RuntimeError("db")
    assert await link_record_organization(_deal(), store, logging.getLogger("t")) == 0
    store.replace_record_entity_relations.assert_not_awaited()


async def test_a_rejected_write_does_not_fail_the_record() -> None:
    """FOR_ACCOUNT is a new edge type an older pod's schema can reject."""
    store = _store()
    store.replace_record_entity_relations.side_effect = RuntimeError("Document does not match the entity relations schema")
    assert await link_record_organization(_deal(), store, logging.getLogger("t")) == 0


@pytest.mark.parametrize("failing", [
    "get_record_group_organization", "replace_record_entity_relations",
])
async def test_a_failure_the_caller_must_see_is_raised(failing: str) -> None:
    """A conflict the caller retries, or a failure that ended its transaction,
    is not logged and passed over."""
    store = _store()
    getattr(store, failing).side_effect = RuntimeError("deadlock")
    with pytest.raises(RuntimeError, match="deadlock"):
        await link_record_organization(_deal(), store, logging.getLogger("t"), lambda exc: True)


@pytest.mark.parametrize("error", [TypeError("unexpected keyword argument"), AttributeError("x")])
@pytest.mark.parametrize("failing", [
    "get_record_group_organization", "replace_record_entity_relations",
])
async def test_a_programming_error_is_raised_not_logged(failing: str, error: Exception) -> None:
    """A store call that does not fit is a bug, not an account the graph did not answer for."""
    store = _store()
    getattr(store, failing).side_effect = error
    with pytest.raises(type(error)):
        await link_record_organization(_deal(), store, logging.getLogger("t"))


class _LinkStore:
    """Entity relations held as an atomic replace keeps them: a write that fails leaves them as they were."""

    def __init__(self, edges: list[dict], fail: bool = False) -> None:
        self.edges, self.fail = edges, fail

    async def get_record_group_organization(self, *_a: object) -> str:
        return "acme"

    async def replace_record_entity_relations(self, record_id, collections, origin, edges) -> None:
        if self.fail:
            raise RuntimeError("write failed")
        keep = [
            e for e in self.edges
            if not (e["_from"] == f"records/{record_id}" and e["_to"].split("/")[0] in collections
                    and e.get("origin", "INFERRED") == origin)
        ]
        self.edges = [*keep, *edges]


OLD_ACCOUNT = {"_from": "records/rec-1", "_to": "organizations/old", "edgeType": "FOR_ACCOUNT", "origin": "INFERRED"}
MENTION = {"_from": "records/rec-1", "_to": "organizations/named", "edgeType": "MENTIONS", "origin": "EXTRACTED"}


async def test_a_replace_leaves_exactly_the_new_account_and_the_extracted_links() -> None:
    store = _LinkStore([dict(OLD_ACCOUNT), dict(MENTION)])
    assert await link_record_organization(_deal(), store, logging.getLogger("t")) == 1
    assert sorted(e["_to"] for e in store.edges) == ["organizations/acme", "organizations/named"]


async def test_a_failed_write_keeps_the_old_account_edge() -> None:
    store = _LinkStore([dict(OLD_ACCOUNT), dict(MENTION)], fail=True)
    assert await link_record_organization(_deal(), store, logging.getLogger("t")) == 0
    assert store.edges == [OLD_ACCOUNT, MENTION]
