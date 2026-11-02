"""The projection's conditional write and strict schemas, against real Neo4j and ArangoDB.

The unit tests run the write against an in-memory graph that evaluates the conditions in Python. This module
proves the two engines' statements agree with that reference, hold under concurrent writers, and that the
strict record schemas of both stores accept what is written (and, for Arango, that a populated collection
migrates).
"""

import asyncio
import copy
import logging

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
from app.models.entities import Priority, RecordType, Status, TicketRecord
from app.modules.record_metadata import projection as proj
from app.modules.record_metadata.builtin import TICKET_PROPERTIES
from app.modules.record_metadata.projection import ScopeUnionCache, union_of
from app.schema.arango.documents import record_schema
from app.services.graph_db.conditions import Cond, holds

from .write_path_support import _suffix

pytestmark = pytest.mark.integration

RECORDS = CollectionNames.RECORDS.value
TICKETS = CollectionNames.TICKETS.value
ORG = "org-it"


@pytest.fixture(params=["arango", "neo4j"])
def provider(request, arango_provider, neo4j_provider):  # noqa: ANN201 - the provider under test
    return arango_provider if request.param == "arango" else neo4j_provider


def _style(provider) -> str:
    return "neo4j" if type(provider).__name__.startswith("Neo4j") else "arango"


def _ticket(**fields) -> TicketRecord:
    return TicketRecord(
        id=f"it-{_suffix()}", record_name="Login fails", record_type=RecordType.TICKET, external_record_id="EXT-1",
        version=1, origin="CONNECTOR", connector_name=Connectors.JIRA, connector_id="conn-it", org_id=ORG,
        virtual_record_id="vr-it", mime_type="text/blocks", updated_at=fields.pop("updated", 1_000),
        **({"status": Status.OPEN, "priority": Priority.HIGH, "labels": ["a", "b"]} | fields),
    )


async def _seed(provider, t: TicketRecord, **extra) -> str:
    assert await provider.batch_upsert_nodes([{**t.to_arango_base_record(), **extra}], RECORDS)
    assert await provider.batch_upsert_nodes([t.to_arango_record()], TICKETS)
    return t.id


def _props(provider, doc: dict) -> dict:
    if _style(provider) == "arango":
        return dict(doc.get("recordProps") or {})
    return {k[2:]: v for k, v in doc.items() if k.startswith("p_")}


async def _doc(provider, key: str) -> dict:
    doc = await provider.get_document(key, RECORDS, raise_on_error=True)
    assert doc is not None
    return doc


# ---- the conditions agree with the reference ----------------------------------------------------------

_STORED = [None, 3, 5, 7]
_CONDS = [Cond("propsSrc", "is_null")] + [
    Cond("propsSrc", op, 5) for op in ("eq", "null_or_eq", "null_or_le")
]


@pytest.mark.parametrize("stored", _STORED)
@pytest.mark.parametrize("cond", _CONDS, ids=lambda c: f"{c.op}")
async def test_each_condition_means_on_the_engine_what_it_means_in_the_reference(provider, stored, cond) -> None:
    t = _ticket()
    extra = {} if stored is None else {"propsSrc": stored}
    key = await _seed(provider, t, **extra)
    expected = holds(cond, {"propsSrc": stored})

    applied = await provider.update_node_fields_if(key, RECORDS, {"propsGen": 9}, [cond])

    assert applied is expected
    assert (await _doc(provider, key)).get("propsGen") == (9 if expected else None)


async def test_all_conditions_must_hold_and_none_is_unset(provider) -> None:
    key = await _seed(provider, _ticket(), propsSrc=5, propsVer=2)
    assert not await provider.update_node_fields_if(key, RECORDS, {"propsGen": 1}, [
        Cond("propsVer", "eq", 2), Cond("propsSrc", "null_or_le", 4)])
    assert await provider.update_node_fields_if(key, RECORDS, {"propsGen": 1}, [
        Cond("propsVer", "eq", 2), Cond("propsSrc", "null_or_le", 5)])
    # None in updates removes the field.
    assert await provider.update_node_fields_if(key, RECORDS, {"propsGen": None}, [Cond("propsVer", "eq", 2)])
    assert (await _doc(provider, key)).get("propsGen") is None


async def test_a_missing_node_is_not_applied(provider) -> None:
    assert not await provider.update_node_fields_if("no-such", RECORDS, {"propsGen": 1}, [Cond("propsVer", "is_null")])


async def test_no_condition_is_refused(provider) -> None:
    with pytest.raises(ValueError):
        await provider.update_node_fields_if("x", RECORDS, {"propsGen": 1}, [])


# ---- T17: the strict schemas accept the projection ------------------------------------------------------

ALL_TYPES = {
    "aString": "x", "anEnum": "OPEN", "anInt": 2**53 - 1, "aFloat": 1.5, "aBool": True,
    "aDate": 1_790_000_000_000, "aList": ["l1", "l2"],
}
CONTROLS = {"propsSet": "0123456789abcdef", "propsGen": 3, "propsSrc": 1_790_000_000_000, "propsVer": 1}


async def test_strict_schemas_accept_projection(provider) -> None:
    key = await _seed(provider, _ticket())
    ok = await provider.set_record_properties(
        key, values=ALL_TYPES, previous_names=(), controls=CONTROLS, conditions=[Cond("propsVer", "is_null")],
    )
    assert ok
    doc = await _doc(provider, key)
    assert _props(provider, doc) == ALL_TYPES
    assert {k: doc[k] for k in CONTROLS} == CONTROLS

    # A full upsert of the record read back from the graph passes the strict validators (the Neo4j node
    # schema is closed; Arango's server-side schema is).
    readback = {k: v for k, v in doc.items() if k not in ("_id", "_rev")}
    assert await provider.batch_upsert_nodes([readback], RECORDS)
    assert _props(provider, await _doc(provider, key)) == ALL_TYPES


async def test_removed_property_is_gone(provider) -> None:
    key = await _seed(provider, _ticket())
    cond = [Cond("propsVer", "is_null")]
    assert await provider.set_record_properties(
        key, values={"a": "1", "b": "2"}, previous_names=(), controls={**CONTROLS, "propsVer": 1}, conditions=cond)
    assert await provider.set_record_properties(
        key, values={"a": "1"}, previous_names={"a", "b"}, controls={**CONTROLS, "propsVer": 2},
        conditions=[Cond("propsVer", "eq", 1)])
    doc = await _doc(provider, key)
    assert _props(provider, doc) == {"a": "1"}
    assert "p_b" not in doc and "b" not in (doc.get("recordProps") or {})
    # Removing the last one leaves none, not a stale remainder.
    assert await provider.set_record_properties(
        key, values={}, previous_names={"a"}, controls={**CONTROLS, "propsVer": 3}, conditions=[Cond("propsVer", "eq", 2)])
    assert _props(provider, await _doc(provider, key)) == {}


async def test_a_refused_write_changes_nothing(provider) -> None:
    key = await _seed(provider, _ticket(), propsSrc=9, propsVer=1)
    await provider.set_record_properties(
        key, values={"a": "1"}, previous_names=(), controls={**CONTROLS, "propsSrc": 9, "propsVer": 2},
        conditions=[Cond("propsVer", "eq", 1)])
    before = await _doc(provider, key)
    refused = await provider.set_record_properties(
        key, values={"a": "stale"}, previous_names={"a"}, controls={**CONTROLS, "propsSrc": 4, "propsVer": 3},
        conditions=[Cond("propsVer", "eq", 2), Cond("propsSrc", "null_or_le", 4)])
    assert not refused
    assert await _doc(provider, key) == before


async def test_two_concurrent_writers_with_one_counter_exactly_one_applies(provider) -> None:
    for round_ in range(5):
        key = await _seed(provider, _ticket())
        results = await asyncio.gather(*(
            provider.set_record_properties(
                key, values={"who": str(i)}, previous_names=(), controls={**CONTROLS, "propsVer": 1},
                conditions=[Cond("propsVer", "is_null")])
            for i in range(6)
        ))
        assert results.count(True) == 1, (round_, results)
        assert len(_props(provider, await _doc(provider, key))) == 1


# ---- the migration of a populated Arango collection ------------------------------------------------------


async def test_arango_migrates_a_populated_collection(arango_provider) -> None:
    """A deployment that already holds records has the closed schema without the projection fields; boot
    re-applies the current schema (``update_collection_schema``) and projections then land."""
    client = arango_provider.http_client
    old = copy.deepcopy(record_schema)
    for name in ("recordProps", "propsSet", "propsGen", "propsSrc", "propsVer"):
        old["rule"]["properties"].pop(name)
    assert await client.update_collection_schema(RECORDS, old)
    key = await _seed(arango_provider, _ticket())
    args = {"values": {"a": "1"}, "previous_names": (), "controls": CONTROLS, "conditions": [Cond("propsVer", "is_null")]}
    with pytest.raises(Exception):  # noqa: B017 - the closed schema refuses fields it does not name
        await arango_provider.set_record_properties(key, **args)

    assert await client.update_collection_schema(RECORDS, record_schema)

    assert await arango_provider.set_record_properties(key, **args)
    assert _props(arango_provider, await _doc(arango_provider, key)) == {"a": "1"}
    # The records that existed before still read.
    assert (await _doc(arango_provider, key))["recordName"] == "Login fails"


# ---- the whole projection on the real providers ------------------------------------------------------------


class _Registry:
    async def get(self, *_a, **_k) -> None:  # pragma: no cover - built-in scopes read no registry
        raise AssertionError("a built-in scope reads no registry")


async def _project(provider, key: str) -> proj.Projection:
    doc = await _doc(provider, key)
    from app.events.processor import convert_record_dict_to_record

    return await proj.project_properties(
        graph=provider, registry=_Registry(), cache=ScopeUnionCache(), record=convert_record_dict_to_record(doc),
        record_doc=doc, logger=logging.getLogger("it"), enabled=lambda: True,
    )


async def test_a_ticket_is_projected_once_and_then_costs_no_write(provider) -> None:
    key = await _seed(provider, _ticket(labels=["checkout", "web"], due_date=1_790_000_000_000), contentRev="rev-1")

    first = await _project(provider, key)
    doc = await _doc(provider, key)

    assert first.status == proj.WRITTEN
    assert _props(provider, doc) == {"status": "OPEN", "priority": "HIGH", "labels": ["checkout", "web"],
                                     "dueDate": 1_790_000_000_000}
    assert doc["propsSet"] == union_of([(1, TICKET_PROPERTIES)]).set_hash
    assert (doc["propsGen"], doc["propsSrc"], doc["propsVer"]) == (1, 1_000, 1)

    second = await _project(provider, key)
    assert second.status == proj.CURRENT and (await _doc(provider, key))["propsVer"] == 1


async def test_a_record_that_changed_after_the_read_is_not_overwritten_with_older_values(provider) -> None:
    key = await _seed(provider, _ticket(), contentRev="rev-1")
    stale_doc = await _doc(provider, key)
    # Run B wrote DONE at t2 (the stamp is newer than the stale reader's).
    assert await provider.set_record_properties(
        key, values={"status": "DONE"}, previous_names={"status"}, controls={**CONTROLS, "propsGen": 1, "propsSrc": 2_000, "propsVer": 1},
        conditions=[Cond("propsVer", "is_null")])

    from app.events.processor import convert_record_dict_to_record

    result = await proj.project_properties(
        graph=provider, registry=_Registry(), cache=ScopeUnionCache(), record=convert_record_dict_to_record(stale_doc),
        record_doc=stale_doc, logger=logging.getLogger("it"), enabled=lambda: True,
    )

    assert result.status == proj.SUPERSEDED
    assert _props(provider, await _doc(provider, key)) == {"status": "DONE"}

