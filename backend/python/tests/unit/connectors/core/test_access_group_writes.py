"""A record that takes its access from a record group of its own (a Jira issue under its security level):
the edges it gets, the ones it loses, and no state between two writes wider than the old one or the new one."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import (
    AccessRule,
    CollectionNames,
    Connectors,
    OriginTypes,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    AccessGroupMissing,
    DataSourceEntitiesProcessor,
)
from app.models.entities import RecordType, TicketRecord

R, G = CollectionNames.RECORDS.value, CollectionNames.RECORD_GROUPS.value


def _issue(**overrides: object) -> TicketRecord:
    fields: dict[str, object] = {
        "id": "rec-1", "org_id": "org", "record_name": "[P-1] t", "record_type": RecordType.TICKET,
        "external_record_id": "1", "version": 1, "origin": OriginTypes.CONNECTOR, "connector_name": Connectors.JIRA,
        "connector_id": "conn", "external_record_group_id": "p-1", "record_group_id": "proj", "access_rule": AccessRule.STRICT,
        "access_group_external_id": "level:p-1:7",
    }
    fields.update(overrides)
    return TicketRecord(**fields)


class _Store:
    """The calls a write makes, in order, against edges stored as given."""

    def __init__(self, stored: object = None, inherits: list[dict] | None = None, listed_by: list[dict] | None = None,
                 groups: dict[str, str] | None = None) -> None:
        self.log: list[tuple] = []
        self.stored = stored
        self.inherits = inherits or []
        self.listed_by = listed_by or []
        self.groups = groups if groups is not None else {"level:p-1:7": "lvl-7", "p-1": "proj"}

    async def get_record_by_external_id(self, connector_id: str, external_id: str) -> object:
        return self.stored

    async def get_record_group_by_external_id(self, connector_id: str, external_id: str) -> object:
        gid = self.groups.get(external_id)
        return SimpleNamespace(id=gid, external_group_id=external_id) if gid else None

    async def get_record_group_by_id(self, gid: str) -> object:
        ext = next((e for e, i in self.groups.items() if i == gid), None)
        return SimpleNamespace(id=gid, external_group_id=ext) if ext else None

    async def get_edges_from_node(self, node: str, collection: str, *, raise_on_error: bool = False) -> list[dict]:
        return self.inherits

    async def get_edges_to_node(self, node: str, collection: str, *, raise_on_error: bool = False) -> list[dict]:
        return self.listed_by

    def __getattr__(self, name: str) -> AsyncMock:
        async def call(*args: object, **kwargs: object) -> None:
            self.log.append((name, *args, kwargs) if kwargs else (name, *args))
        return call


def _processor() -> DataSourceEntitiesProcessor:
    processor = DataSourceEntitiesProcessor(MagicMock(), MagicMock(), MagicMock())
    processor.org_id = "org"
    return processor


def _rule_writes(store: _Store) -> list[str]:
    return [args[0][0]["accessRule"] for name, *args in store.log if name == "batch_update_nodes"]


@pytest.mark.asyncio
async def test_an_issue_stored_with_a_copy_is_written_closed_until_it_hangs_under_its_level() -> None:
    """Stored RESTRICTED, inheriting its project: written STRICT before the project edge goes, the
    project's readers would read it."""
    processor, store = _processor(), _Store(stored=SimpleNamespace(id="rec-1", access_rule=AccessRule.RESTRICTED))
    seen: list[AccessRule] = []

    async def body(record: TicketRecord, *a: object, **kw: object) -> tuple:
        seen.append(record.access_rule)
        return record, []

    processor._process_record_body = body
    record = _issue()
    await processor._process_record(record, [], store)
    assert seen == [AccessRule.RESTRICTED]
    created = [i for i, (name, *_) in enumerate(store.log) if name == "create_inherit_permissions_relation_record_group"]
    final = [i for i, (name, *args) in enumerate(store.log) if name == "batch_update_nodes"]
    assert created and final and created[0] < final[-1]
    assert _rule_writes(store) == ["STRICT"] and record.access_rule == AccessRule.STRICT


@pytest.mark.asyncio
async def test_an_issue_that_turns_to_a_copy_is_closed_before_its_edges_change() -> None:
    processor, store = _processor(), _Store(stored=SimpleNamespace(id="rec-1", access_rule=AccessRule.STRICT))
    order: list[str] = []

    async def body(record: TicketRecord, *a: object, **kw: object) -> tuple:
        order.append(f"body:{_rule_writes(store)}")
        return record, []

    processor._process_record_body = body
    await processor._process_record(_issue(access_group_external_id="", access_rule=AccessRule.RESTRICTED), [], store)
    assert order == ["body:['RESTRICTED']"]


@pytest.mark.asyncio
async def test_the_level_group_replaces_what_the_issue_inherited_and_its_old_level() -> None:
    """Neo4j hands an edge's ends over as ids with their collection, ArangoDB as "collection/id": both are read."""
    store = _Store(
        inherits=[{"to_id": "lvl-old", "to_collection": G}, {"_to": f"{G}/proj"}, {"_to": f"{R}/epic"}],
        listed_by=[{"from_id": "lvl-old", "from_collection": G}, {"_from": f"{G}/proj"}, {"_from": f"{R}/epic"}],
    )
    await _processor()._apply_access_group(_issue(), store)
    assert [(name, *args) for name, *args in store.log if name != "batch_create_edges"] == [
        ("delete_edge", "lvl-old", G, "rec-1", R, CollectionNames.NODE_RELATIONS.value),
        ("delete_inherit_permissions_relation_record_group", "rec-1", "lvl-old"),
        ("delete_inherit_permissions_relation_record_group", "rec-1", "proj"),
        ("create_inherit_permissions_relation_record_group", "rec-1", "lvl-7"),
    ]
    (edges,) = [args[0] for name, *args in store.log if name == "batch_create_edges"]
    assert [(e["from_id"], e["to_id"]) for e in edges] == [("lvl-7", "rec-1")]  # listed under it, and still where it is filed


@pytest.mark.asyncio
async def test_an_issue_that_names_no_group_loses_its_level_and_keeps_its_project() -> None:
    store = _Store(inherits=[{"_to": f"{G}/lvl-7"}, {"_to": f"{G}/proj"}], listed_by=[{"_from": f"{G}/lvl-7"}, {"_from": f"{G}/proj"}])
    await _processor()._apply_access_group(_issue(access_group_external_id=""), store)
    assert store.log == [
        ("delete_edge", "lvl-7", G, "rec-1", R, CollectionNames.NODE_RELATIONS.value),
        ("delete_inherit_permissions_relation_record_group", "rec-1", "lvl-7"),
    ]


@pytest.mark.asyncio
async def test_a_level_group_that_is_not_there_is_an_error() -> None:
    with pytest.raises(AccessGroupMissing):
        await _processor()._apply_access_group(_issue(), _Store(groups={}))


@pytest.mark.asyncio
async def test_a_write_that_names_no_group_keeps_the_stored_level_and_rule() -> None:
    """A level read that will be retried keeps the issue's stored grants; it keeps its level and STRICT rule too."""
    stored = SimpleNamespace(id="rec-1", record_group_id="proj", access_rule=AccessRule.STRICT)
    record = _issue(access_group_external_id=None, access_rule=AccessRule.RESTRICTED, rewrite_permissions=False)
    await _processor()._keep_access_group(record, _Store(stored=stored, inherits=[{"_to": f"{G}/lvl-7"}]))
    assert (record.access_group_external_id, record.access_rule) == ("level:p-1:7", AccessRule.STRICT)

    rewritten = _issue(access_group_external_id=None, access_rule=AccessRule.RESTRICTED, rewrite_permissions=True)
    await _processor()._keep_access_group(rewritten, _Store(stored=stored, inherits=[{"_to": f"{G}/lvl-7"}]))
    assert (rewritten.access_group_external_id, rewritten.access_rule) == ("level:p-1:7", AccessRule.RESTRICTED)

    moved = _issue(access_group_external_id=None, external_record_group_id="p-2")
    await _processor()._keep_access_group(
        moved, _Store(stored=stored, inherits=[{"_to": f"{G}/lvl-7"}], groups={"level:p-1:7": "lvl-7", "p-2": "proj-2"}))
    assert moved.access_group_external_id == ""  # filed in another project: the old project's level goes


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [None, SimpleNamespace(id="rec-1", record_group_id="proj", is_placeholder=False)])
async def test_an_issue_under_its_level_never_inherits_its_project_on_the_way(existing: object) -> None:
    """Its record edges are written before its level's: an edge to the project, even for a moment, opens it to the project."""
    store = _Store()
    await _processor()._link_record_to_group(_issue(), "proj", store, existing)
    (link,) = [entry for entry in store.log if entry[0] == "link_record_to_group"]
    assert link[2] == "proj" and link[-1]["inherit"] is not True  # still placed in its project
    plain = _Store()
    await _processor()._link_record_to_group(_issue(access_group_external_id=""), "proj", plain, existing)
    (link,) = [entry for entry in plain.log if entry[0] == "link_record_to_group"]
    assert link[-1]["inherit"] is True
