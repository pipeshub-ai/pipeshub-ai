"""Projection of a record's property values: what is written, for which records, and what is not."""

from unittest.mock import patch

import pytest

from app.config.constants.arangodb import Connectors
from app.models.entities import RecordType, Status
from app.models.record_metadata import (
    MetadataProvenance,
    MetadataValue,
    PropertyDefinition,
    PropertyType,
    RecordMetadata,
)
from app.modules.record_metadata import projection as proj
from app.modules.record_metadata.builtin import TICKET_PROPERTIES
from app.modules.record_metadata.projection import (
    CURRENT,
    DISABLED,
    FAILED,
    NO_STAMP,
    NO_UNION,
    NOT_PROVIDED,
    WRITTEN,
    ScopeUnionCache,
    encode_value,
    stamp_of,
    union_of,
)
from app.modules.record_metadata.schemas import MetadataSchemaRegistry

from .projection_world import (
    FakeConfig,
    FakeGraph,
    add_ticket,
    defs,
    kb_doc,
    project,
    registry,
    snapshot,
    ticket,
)

pytestmark = pytest.mark.unit

JIRA_UNION_NAMES = sorted(p.name for p in TICKET_PROPERTIES if p.filterable or p.facetable)


def _meta(**values: object) -> RecordMetadata:
    return RecordMetadata(schema_id="org-1:kb-1:1", values={
        name: MetadataValue(value=value, provenance=MetadataProvenance.PROVIDED, source="push")  # type: ignore[arg-type]
        for name, value in values.items()
    })


async def _kb_world(
    *versions: list[PropertyDefinition],
) -> tuple[MetadataSchemaRegistry, FakeConfig, ScopeUnionCache]:
    reg, config = registry()
    for properties in versions:
        await reg.register("org-1", "kb-1", properties)
    return reg, config, ScopeUnionCache()


# ---- T1 ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("style", ["arango", "neo4j"])
async def test_ticket_projects_union_values(style: str) -> None:
    graph = FakeGraph(style)
    t = ticket(labels=["checkout", "web"], due_date=1_790_000_000_000, assignee="Ada")
    add_ticket(graph, t, contentRev="rev-1")
    reg, _ = registry()

    result = await project(graph, reg, ScopeUnionCache(), t.id)

    assert result.status == WRITTEN
    assert graph.props(t.id) == {
        "status": "OPEN", "priority": "HIGH", "labels": ["checkout", "web"], "dueDate": 1_790_000_000_000,
        "assignee": "Ada",
    }
    doc = graph.records[t.id]
    assert doc["propsSet"] == union_of([(1, TICKET_PROPERTIES)]).set_hash and len(doc["propsSet"]) == 16
    assert (doc["propsGen"], doc["propsSrc"], doc["propsVer"]) == (1, 1_000, 1)


async def test_the_source_clock_is_the_stamp_when_the_source_gave_one() -> None:
    graph = FakeGraph()
    t = ticket(updated=5_000, source=2_000)
    add_ticket(graph, t)
    await project(graph, registry()[0], ScopeUnionCache(), t.id)
    assert graph.records[t.id]["propsSrc"] == 2_000


# ---- stamps (T18g) -----------------------------------------------------------------------------------


@pytest.mark.parametrize(("source", "updated", "expected"), [
    (7, 3, 7), (None, 3, 3), (None, None, 0), (0, 3, 0), (-5, 3, 0), (True, 3, 0), (None, True, 0),
    ("9", None, 0), (float("nan"), 3, 0), (7.9, None, 7), (None, 0, 0),
])
def test_stamp_of(source: object, updated: object, expected: int) -> None:
    assert stamp_of(source, updated) == expected


@pytest.mark.parametrize("fields", [
    {"updatedAtTimestamp": None}, {"updatedAtTimestamp": 0}, {"updatedAtTimestamp": -3},
    {"updatedAtTimestamp": 5, "sourceLastModifiedTimestamp": 0},
])
async def test_stamp_zero_is_never_written(fields: dict) -> None:
    graph = FakeGraph()
    t = ticket()
    add_ticket(graph, t, **fields)

    result = await project(graph, registry()[0], ScopeUnionCache(), t.id)

    assert result.status == NO_STAMP
    assert graph.write_calls == 0 and "propsSrc" not in graph.records[t.id]


async def test_a_stamp_lost_between_the_callers_read_and_the_builders_is_not_written() -> None:
    graph = FakeGraph()
    t = ticket()
    add_ticket(graph, t)
    seen_by_caller = dict(graph.records[t.id])
    graph.records[t.id]["updatedAtTimestamp"] = None  # the record the builder reads has none

    result = await project(graph, registry()[0], ScopeUnionCache(), t.id, record_doc=seen_by_caller)

    assert result.status == FAILED and result.reason == "no_stamp" and graph.write_calls == 0


# ---- the union, and which records are touched -------------------------------------------------------


def test_union_is_every_name_any_version_marks_filterable_and_the_highest_version_is_its_generation() -> None:
    v1 = defs(("team", PropertyType.STRING)) + defs(("note", PropertyType.STRING), filterable=False)
    v2 = defs(("tier", PropertyType.ENUM))
    union = union_of([(2, v2), (1, v1)])
    assert dict(union.types) == {"team": PropertyType.STRING, "tier": PropertyType.ENUM}
    assert union.gen == 2


def test_the_current_type_wins_when_a_shipped_version_changed_it() -> None:
    old = defs(("score", PropertyType.STRING), facetable=True)  # accepted before the registration rules
    new = defs(("score", PropertyType.INT))
    assert union_of([(1, old), (2, new)]).types["score"] is PropertyType.INT


def test_a_name_no_version_makes_filterable_is_not_projected() -> None:
    assert union_of([(1, defs(("note", PropertyType.STRING), filterable=False, searchable=True))]).empty


async def test_only_union_values_are_projected_and_a_misfit_is_dropped_with_a_metric() -> None:
    reg, _, cache = await _kb_world(
        defs(("team", PropertyType.STRING)) + defs(("count", PropertyType.INT))
        + defs(("blurb", PropertyType.STRING), filterable=False, searchable=True),
    )
    graph = FakeGraph()
    doc = kb_doc()
    graph.records[doc["_key"]] = doc
    meta = _meta(team="payments", count=2**53, blurb="free text")

    with patch.object(proj, "record_property_value_dropped") as dropped:
        result = await project(graph, reg, cache, doc["_key"], provided=meta)

    assert result.status == WRITTEN
    assert graph.props(doc["_key"]) == {"team": "payments"}
    dropped.assert_called_once()


async def test_records_of_a_scope_with_no_union_are_never_touched() -> None:
    graph = FakeGraph()
    t = ticket(Connectors.GITHUB_TEAMS)
    add_ticket(graph, t)
    other = kb_doc("plain", kb_id="drive-1", connectorName=Connectors.GOOGLE_DRIVE.value)
    graph.records["plain"] = other
    reg, config = registry()

    result = await project(graph, reg, ScopeUnionCache(), "plain")

    assert result.status == NO_UNION
    assert graph.write_calls == 0 and config.reads == []


async def test_a_knowledge_base_without_a_schema_projects_nothing() -> None:
    reg, _, cache = await _kb_world()
    graph = FakeGraph()
    doc = kb_doc()
    graph.records[doc["_key"]] = doc
    assert (await project(graph, reg, cache, doc["_key"], provided=None)).status == NO_UNION


async def test_an_attachment_in_a_ticket_scope_is_stamped_with_no_values() -> None:
    graph = FakeGraph()
    attachment = kb_doc("att-1", kb_id="conn-1", connectorName=Connectors.JIRA.value, updated=4_000)
    graph.records["att-1"] = attachment

    result = await project(graph, registry()[0], ScopeUnionCache(), "att-1")

    assert result.status == WRITTEN
    assert graph.props("att-1") == {} and graph.records["att-1"]["propsSrc"] == 4_000
    assert graph.records["att-1"]["propsSet"] == union_of([(1, TICKET_PROPERTIES)]).set_hash


async def test_a_ticket_with_every_field_empty_is_stamped_with_no_values() -> None:
    graph = FakeGraph()
    t = ticket(status=None, priority=None, labels=None)
    add_ticket(graph, t)
    result = await project(graph, registry()[0], ScopeUnionCache(), t.id)
    assert result.status == WRITTEN and graph.props(t.id) == {} and graph.records[t.id]["propsSrc"] == 1_000


# ---- T3: flag, +0 writes when current, a name entering the union ------------------------------------


async def test_flag_off_reads_and_writes_nothing() -> None:
    graph = FakeGraph()
    t = ticket()
    add_ticket(graph, t)
    doc = graph.records[t.id]
    result = await proj.project_properties(
        graph=graph, registry=registry()[0], cache=ScopeUnionCache(), record=snapshot(doc), record_doc=doc,
        enabled=lambda: False,
    )
    assert result.status == DISABLED and graph.reads == [] and graph.write_calls == 0


@pytest.mark.parametrize(("value", "on"), [
    (None, False), ("", False), ("false", False), ("0", False), ("true", True), (" ON ", True), ("1", True),
])
def test_the_flag_reads_the_environment(value: str | None, on: bool) -> None:
    assert proj.projection_enabled({} if value is None else {proj.PROJECTION_FLAG: value}) is on


async def test_a_projected_record_costs_no_write_and_no_read() -> None:
    graph = FakeGraph()
    t = ticket()
    add_ticket(graph, t)
    reg, _ = registry()
    cache = ScopeUnionCache()
    await project(graph, reg, cache, t.id)
    graph.reads.clear()

    result = await project(graph, reg, cache, t.id)

    assert result.status == CURRENT
    assert graph.write_calls == 1 and graph.reads == []


async def test_a_name_entering_the_union_is_rewritten_and_the_flag_flip_needs_no_write() -> None:
    reg, _, cache = await _kb_world(defs(("team", PropertyType.STRING)))
    graph = FakeGraph()
    doc = kb_doc()
    graph.records[doc["_key"]] = doc
    meta = _meta(team="payments", tier="a")
    await project(graph, reg, cache, doc["_key"], provided=meta)
    assert graph.props(doc["_key"]) == {"team": "payments"}
    first_set = graph.records[doc["_key"]]["propsSet"]

    await reg.register("org-1", "kb-1", defs(("team", PropertyType.STRING)) + defs(("tier", PropertyType.ENUM)))
    cache.invalidate("org-1", "kb-1")
    result = await project(graph, reg, cache, doc["_key"], provided=meta)

    assert result.status == WRITTEN
    assert graph.props(doc["_key"]) == {"team": "payments", "tier": "a"}
    assert graph.records[doc["_key"]]["propsSet"] != first_set and graph.records[doc["_key"]]["propsGen"] == 2


@pytest.mark.parametrize("style", ["arango", "neo4j"])
async def test_removed_property_is_gone(style: str) -> None:
    reg, _, cache = await _kb_world(defs(("team", PropertyType.STRING)) + defs(("tier", PropertyType.ENUM)))
    graph = FakeGraph(style)
    doc = kb_doc()
    graph.records[doc["_key"]] = doc
    await project(graph, reg, cache, doc["_key"], provided=_meta(team="payments", tier="a"))
    assert graph.props(doc["_key"]) == {"team": "payments", "tier": "a"}

    graph.records[doc["_key"]]["updatedAtTimestamp"] = 2_000
    result = await project(graph, reg, cache, doc["_key"], provided=_meta(team="payments"),
                           record=snapshot(graph.records[doc["_key"]]))

    assert result.status == WRITTEN and graph.props(doc["_key"]) == {"team": "payments"}


# ---- values of a record that is not a ticket --------------------------------------------------------


async def test_a_pushed_records_values_come_from_the_caller_and_none_is_none() -> None:
    reg, _, cache = await _kb_world(defs(("team", PropertyType.STRING)))
    graph = FakeGraph()
    doc = kb_doc()
    graph.records[doc["_key"]] = doc

    assert (await project(graph, reg, cache, doc["_key"], provided=None)).status == WRITTEN
    assert graph.props(doc["_key"]) == {} and graph.records[doc["_key"]]["propsSrc"] == 1_000


async def test_without_values_from_the_caller_a_knowledge_base_record_is_left_alone() -> None:
    """Whether the record has no values or the caller could not say is unknowable here: fail closed."""
    reg, _, cache = await _kb_world(defs(("team", PropertyType.STRING)))
    graph = FakeGraph()
    doc = kb_doc(recordProps={"team": "kept"}, propsSrc=500, propsVer=3, propsSet="x", propsGen=1)
    graph.records[doc["_key"]] = doc

    result = await project(graph, reg, cache, doc["_key"], provided=NOT_PROVIDED)

    assert result.status == FAILED and result.reason == "values_unavailable"
    assert graph.write_calls == 0 and graph.records[doc["_key"]]["recordProps"] == {"team": "kept"}


# ---- T33: an error is not "no metadata" -------------------------------------------------------------


async def _projected_ticket() -> tuple[FakeGraph, str]:
    graph = FakeGraph()
    t = ticket(labels=["keep"])
    add_ticket(graph, t)
    await project(graph, registry()[0], ScopeUnionCache(), t.id)
    assert graph.props(t.id)["labels"] == ["keep"]
    graph.records[t.id]["updatedAtTimestamp"] = 2_000  # stale again: it will be projected anew
    graph.write_calls = 0
    return graph, t.id


async def test_tickets_read_error_keeps_existing_values() -> None:
    graph, rid = await _projected_ticket()
    graph.read_errors[("tickets", rid)] = RuntimeError("tickets read timed out")

    result = await project(graph, registry()[0], ScopeUnionCache(), rid)

    assert result.status == FAILED and result.reason.startswith("read_error")
    assert graph.write_calls == 0
    assert graph.props(rid)["labels"] == ["keep"] and graph.records[rid]["propsSrc"] == 1_000


async def test_a_ticket_record_whose_tickets_document_is_missing_is_failed_not_absent() -> None:
    graph, rid = await _projected_ticket()
    del graph.tickets[rid]

    result = await project(graph, registry()[0], ScopeUnionCache(), rid)

    assert result.status == FAILED and result.reason == "tickets_document_missing"
    assert graph.write_calls == 0 and graph.props(rid)["labels"] == ["keep"]


async def test_a_record_read_error_is_failed_too() -> None:
    graph, rid = await _projected_ticket()
    graph.read_errors[("records", rid)] = RuntimeError("down")
    # The caller's own copy is what the cheap check reads; the builder's fresh read is what fails.
    doc = {**graph.records[rid]}
    result = await project(graph, registry()[0], ScopeUnionCache(), rid, record_doc=doc)
    assert result.status == FAILED and graph.write_calls == 0


async def test_a_deleted_record_is_not_projected() -> None:
    graph, rid = await _projected_ticket()
    graph.records[rid]["isDeleted"] = True
    result = await project(graph, registry()[0], ScopeUnionCache(), rid, record_doc={**graph.records[rid], "isDeleted": False})
    assert result.status == FAILED and result.reason == "record_gone" and graph.write_calls == 0


async def test_an_unreadable_union_writes_nothing() -> None:
    reg, config, cache = await _kb_world(defs(("team", PropertyType.STRING)))
    cache = ScopeUnionCache()  # nothing cached
    config.fail_reads = True
    graph = FakeGraph()
    doc = kb_doc()
    graph.records[doc["_key"]] = doc
    result = await project(graph, reg, cache, doc["_key"], provided=None)
    assert result.status == FAILED and result.reason == "union_unavailable" and graph.write_calls == 0


# ---- encodings (OW-2) --------------------------------------------------------------------------------


@pytest.mark.parametrize(("ptype", "value", "expected"), [
    (PropertyType.STRING, "Payments", "Payments"),
    (PropertyType.STRING, "x" * 1025, None),
    (PropertyType.ENUM, "a", "a"),
    (PropertyType.INT, 2**53 - 1, 2**53 - 1),
    (PropertyType.INT, -(2**53 - 1), -(2**53 - 1)),
    (PropertyType.INT, 2**53, None),
    (PropertyType.INT, True, None),
    (PropertyType.INT, 1.5, None),
    (PropertyType.FLOAT, 2, 2.0),
    (PropertyType.FLOAT, float("inf"), None),
    (PropertyType.BOOL, True, True),
    (PropertyType.BOOL, 1, None),
    (PropertyType.DATETIME, 1_790_000_000_000, 1_790_000_000_000),
    (PropertyType.DATETIME, "2026-01-01T00:00:00Z", None),
    (PropertyType.STRING_LIST, ["a", "b"], ["a", "b"]),
    (PropertyType.STRING_LIST, [f"l{i}" for i in range(65)], None),
    (PropertyType.STRING_LIST, ["a", 1], None),
    (PropertyType.STRING, ["a"], None),
])
def test_encode_value(ptype: PropertyType, value: object, expected: object) -> None:
    assert encode_value(ptype, value) == expected


async def test_names_are_case_sensitive_and_values_are_not_normalised() -> None:
    reg, _, cache = await _kb_world(defs(("team", PropertyType.STRING)))
    graph = FakeGraph()
    doc = kb_doc()
    graph.records[doc["_key"]] = doc
    await project(graph, reg, cache, doc["_key"], provided=_meta(team="  Payments "))
    assert graph.props(doc["_key"]) == {"team": "  Payments "}


def test_pipeline_record_types_are_unchanged_by_the_scope_rules() -> None:
    assert RecordType.TICKET.value == "TICKET" and Status.OPEN.value == "OPEN"


def test_property_definition_flags_drive_the_union() -> None:
    assert PropertyDefinition(name="a", type=PropertyType.BOOL, display_label="A", facetable=True).filterable


async def test_a_version_that_adds_no_name_still_advances_the_generation_of_a_current_record() -> None:
    """v2 only edits a label: the names are the same (same hash), but the generation is 2, and a record at
    generation 1 is not current -- else a pod holding the older union could still write over it."""
    v1 = defs(("team", PropertyType.STRING))
    reg, _, cache = await _kb_world(v1)
    graph = FakeGraph()
    doc = kb_doc()
    graph.records[doc["_key"]] = doc
    meta = _meta(team="payments")
    await project(graph, reg, cache, doc["_key"], provided=meta)
    assert graph.records[doc["_key"]]["propsGen"] == 1

    relabelled = [PropertyDefinition(name="team", type=PropertyType.STRING, display_label="Squad", filterable=True)]
    await reg.register("org-1", "kb-1", relabelled)
    cache.invalidate("org-1", "kb-1")
    result = await project(graph, reg, cache, doc["_key"], provided=meta)

    assert result.status == WRITTEN
    assert graph.records[doc["_key"]]["propsGen"] == 2 and graph.props(doc["_key"]) == {"team": "payments"}


def test_the_generation_is_the_highest_version_not_the_highest_one_that_adds_a_name() -> None:
    adds = defs(("a", PropertyType.STRING))
    union = union_of([(1, adds), (2, adds), (3, defs(("a", PropertyType.STRING), filterable=False))])
    assert union.gen == 3
    assert union_of([(1, adds), (2, defs(("b", PropertyType.STRING), filterable=False))]).gen == 2


async def test_a_source_stamp_that_goes_backwards_is_not_reprojected_by_the_index_path() -> None:
    """Documented bound (module docstring): current means propsSrc >= stamp. The sweep converges it."""
    graph = FakeGraph()
    t = ticket(source=9_000_000_000_000)
    add_ticket(graph, t)
    reg, cache = registry()[0], ScopeUnionCache()
    assert (await project(graph, reg, cache, t.id)).status == WRITTEN
    graph.tickets[t.id]["status"] = Status.DONE.value
    graph.records[t.id]["sourceLastModifiedTimestamp"] = 2_000_000_000_000

    result = await project(graph, reg, cache, t.id)

    assert result.status == CURRENT and graph.props(t.id)["status"] == "OPEN"
