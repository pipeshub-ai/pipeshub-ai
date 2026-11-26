"""A small world for the projection tests: an in-memory graph that evaluates the real ``Cond`` operators,
a config store behind the real schema registry, and ticket / knowledge-base records."""

import copy
import logging
from collections.abc import Awaitable, Callable, Collection, Sequence
from typing import Any

from app.config.constants.arangodb import CollectionNames, Connectors
from app.events.processor import convert_record_dict_to_record
from app.models.entities import (
    Priority,
    Record,
    RecordType,
    Status,
    TicketRecord,
)
from app.models.record_metadata import PropertyDefinition, PropertyType
from app.modules.record_metadata import projection as projection_module
from app.modules.record_metadata.projection import ScopeUnionCache
from app.modules.record_metadata.schemas import MetadataSchemaRegistry
from app.services.graph_db.conditions import Cond, check_conditions, holds

ORG = "org-1"
RECORDS = CollectionNames.RECORDS.value
TICKETS = CollectionNames.TICKETS.value


class FakeConfig:
    """Just the config-store calls the schema registry makes, counting reads."""

    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.reads: list[str] = []
        self.fail_reads = False
        self.logger = logging.getLogger("fake-config")

    async def get_config(self, key: str, use_cache: bool = True, raise_on_error: bool = False, **_: Any) -> Any:  # noqa: ANN401
        self.reads.append(key)
        if self.fail_reads:
            raise RuntimeError("config store down")
        return copy.deepcopy(self.store.get(key))

    async def create_config_if_absent(self, key: str, value: Any) -> bool:  # noqa: ANN401
        if key in self.store:
            return False
        self.store[key] = copy.deepcopy(value)
        return True

    async def set_config(self, key: str, value: Any) -> bool:  # noqa: ANN401
        self.store[key] = copy.deepcopy(value)
        return True


class FakeGraph:
    """Records and tickets documents; ``set_record_properties`` is the engines' statement, in Python.

    ``style``: ``arango`` keeps values in ``recordProps`` (replaced whole), ``neo4j`` flat as ``p_<name>``.
    ``before_write(n)`` runs just before the n-th write (1-based) so a test can interleave another writer.
    """

    def __init__(self, style: str = "arango") -> None:
        self.style = style
        self.records: dict[str, dict[str, Any]] = {}
        self.tickets: dict[str, dict[str, Any]] = {}
        self.writes: list[dict[str, Any]] = []  # applied
        self.refused: list[dict[str, Any]] = []
        self.write_calls = 0
        self.reads: list[tuple[str, str]] = []
        self.read_errors: dict[tuple[str, str], Exception] = {}
        self.before_write: Callable[[int], Awaitable[None]] | None = None

    async def get_document(
        self, key: str, collection: str, transaction: str | None = None, *, raise_on_error: bool = False,
    ) -> dict[str, Any] | None:
        self.reads.append((collection, key))
        if (collection, key) in self.read_errors:
            raise self.read_errors[(collection, key)]
        table = self.records if collection == RECORDS else self.tickets
        doc = table.get(key)
        return copy.deepcopy(doc) if doc is not None else None

    async def set_record_properties(
        self, key: str, *, values: dict[str, Any], previous_names: Collection[str], controls: dict[str, Any],
        conditions: Sequence[Cond], transaction: str | None = None,
    ) -> bool:
        check_conditions(conditions)
        self.write_calls += 1
        if self.before_write is not None:
            await self.before_write(self.write_calls)
        doc = self.records[key]
        call = {"key": key, "values": dict(values), "controls": dict(controls), "conditions": list(conditions)}
        if not all(holds(c, doc) for c in conditions):
            self.refused.append(call)
            return False
        if self.style == "arango":
            doc["recordProps"] = dict(values)
        else:
            for name in previous_names:
                doc.pop(f"p_{name}", None)
            for name, value in values.items():
                doc[f"p_{name}"] = value
        doc.update(controls)
        self.writes.append(call)
        return True

    def props(self, key: str) -> dict[str, Any]:
        doc = self.records[key]
        if self.style == "arango":
            return dict(doc.get("recordProps") or {})
        return {k[2:]: v for k, v in doc.items() if k.startswith("p_")}


def ticket(connector: Connectors = Connectors.JIRA, *, record_id: str = "t-1", updated: int = 1_000, source: int | None = None,
           **fields: Any) -> TicketRecord:  # noqa: ANN401
    return TicketRecord(
        id=record_id, record_name="Login fails", record_type=RecordType.TICKET, external_record_id="EXT-1", version=1,
        origin="CONNECTOR", connector_name=connector, connector_id="conn-1", org_id=ORG, virtual_record_id="vr-1",
        mime_type="text/blocks", updated_at=updated, source_updated_at=source,
        **({"status": Status.OPEN, "priority": Priority.HIGH, "labels": ["a", "b"]} | fields),
    )


def add_ticket(graph: FakeGraph, t: TicketRecord, **doc_fields: Any) -> dict[str, Any]:  # noqa: ANN401
    doc = {**t.to_arango_base_record(), **doc_fields}
    graph.records[t.id] = doc
    graph.tickets[t.id] = t.to_arango_record()
    return doc


def set_ticket_status(graph: FakeGraph, record_id: str, status: Status, *, stamp: int) -> None:
    """The connector changes a ticket at the source: tickets document and the record's stamp move together."""
    graph.tickets[record_id]["status"] = status.value
    graph.records[record_id]["updatedAtTimestamp"] = stamp


def kb_doc(record_id: str = "kb-rec-1", *, kb_id: str = "kb-1", updated: int = 1_000, **fields: Any) -> dict[str, Any]:  # noqa: ANN401
    return {
        "_key": record_id, "orgId": ORG, "connectorId": kb_id, "recordName": "doc.md", "externalRecordId": record_id,
        "recordType": RecordType.FILE.value, "origin": "UPLOAD", "createdAtTimestamp": updated,
        "updatedAtTimestamp": updated, "sourceLastModifiedTimestamp": None, "mimeType": "text/markdown",
        "virtualRecordId": "vr-" + record_id, "version": 1, **fields,
    }


def snapshot(doc: dict[str, Any]) -> Record:
    """The in-memory ``ctx.record`` an event carries: built from the record document as it was read."""
    return convert_record_dict_to_record(doc)


def defs(*specs: tuple[str, PropertyType], **flags: Any) -> list[PropertyDefinition]:  # noqa: ANN401
    out = []
    for name, ptype in specs:
        extra: dict[str, Any] = {"filterable": True, **flags}
        if ptype is PropertyType.ENUM:
            extra["enum_values"] = ("a", "b")
        out.append(PropertyDefinition(name=name, type=ptype, display_label=name.title(), **extra))
    return out


def registry(config: FakeConfig | None = None) -> tuple[MetadataSchemaRegistry, FakeConfig]:
    config = config or FakeConfig()
    return MetadataSchemaRegistry(config), config  # type: ignore[arg-type]


async def project(graph: FakeGraph, reg: MetadataSchemaRegistry, cache: ScopeUnionCache, record_id: str, *,
                   record_doc: dict[str, Any] | None = None, record: Record | None = None,
                   provided: object = projection_module.NOT_PROVIDED) -> projection_module.Projection:
    doc = record_doc if record_doc is not None else copy.deepcopy(graph.records[record_id])
    return await projection_module.project_properties(
        graph=graph, registry=reg, cache=cache, record=record or snapshot(doc), record_doc=doc,
        provided=provided, logger=logging.getLogger("t"), enabled=lambda: True,
    )
