"""The knowledge toolset's bulk export for engine runs (G-35, GA-19): ``knowledgegraph__export_records`` and
``knowledgegraph__export_status`` over ``harness_core.knowledge.bulk_export``.

Records come from the same scoped listing as ``knowledgegraph__list_files`` (``plan_listing``): the knowledge
service's own ACL-filtered listing as the run's person, inside the run's pinned scope, narrowed by the export's
filters and, under a bound below app level, kept to the rows the bound admits. Each JSONL line is one record's
metadata; content stays with ``knowledgegraph__fetch_record``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Final, cast

from app.harness.contracts.generated.pipeshub.event_catalog import EVENT_MODELS
from harness_core.contracts import validate_contract
from harness_core.journal.types import NewEvent
from harness_core.knowledge.bulk_export import (
    BulkExports,
    BulkExportTools,
    ExportingCatalog,
    ExportRefusal,
    ExportShelf,
    ExportToolNames,
    RecordPage,
    RecordPages,
    export_tool_effect,
)
from harness_core.provenance.rules import NO_CONTENT_RULE, Fixed, ProvenanceRule
from harness_core.provenance.types import ResultProvenance
from harness_engines.pipeshub import KNOWLEDGE_PATH, KNOWLEDGE_TOOLSET

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, MutableMapping

    from pydantic import BaseModel, JsonValue

    from harness_core.effects.types import ToolEffect
    from harness_core.invoker.ports import ToolCatalog
    from harness_core.logs import HarnessLog

EXPORTED_EVENT: Final = "x.pipeshub.knowledge.exported"
EXPORT_TOOL: Final = f"{KNOWLEDGE_TOOLSET}__export_records"
EXPORT_STATUS_TOOL: Final = f"{KNOWLEDGE_TOOLSET}__export_status"
EXPORT_NAMES: Final = ExportToolNames(start=EXPORT_TOOL, status=EXPORT_STATUS_TOOL, source=KNOWLEDGE_TOOLSET)
# One page of the knowledge service: its own ceiling (the hub pages at most this many).
_SERVICE_PAGE: Final = 100
_SELECTION_REFUSAL: Final = (
    "This conversation is limited to items the user selected, so a bulk export of whole sources is not available "
    "here. List or search the selected items instead."
)


def exported_event(data: dict[str, JsonValue]) -> NewEvent:
    """``x.pipeshub.knowledge.exported``, checked against its contract; non-critical (folds ignore it)."""
    model = EVENT_MODELS[EXPORTED_EVENT]
    data_model = cast("type[BaseModel]", model.model_fields["data"].annotation)
    clean = cast("dict[str, JsonValue]", json.loads(json.dumps(data)))
    _ = validate_contract(data_model, clean)
    return NewEvent(EXPORTED_EVENT, clean, critical=False)


def export_row(item: Any) -> dict[str, JsonValue]:  # noqa: ANN401 - a KnowledgeHub NodeItem
    """One record's metadata, as a line of the export: what the listing shows, never its content."""
    row: dict[str, JsonValue] = {
        "id": item.id,
        "name": item.name,
        "nodeType": getattr(item.nodeType, "value", item.nodeType),
        "origin": getattr(item.origin, "value", item.origin),
        "createdAt": item.createdAt,
        "updatedAt": item.updatedAt,
    }
    for field in ("recordType", "connector", "connectorId", "parentId", "mimeType", "extension", "sizeInBytes", "webUrl"):
        value = getattr(item, field, None)
        if value is not None:
            row[field] = value
    return row


class ListingPages:
    """``RecordPages`` over one ``ScopedListing``: the service's pages in order, under a bound only its admitted
    rows. The cursor is the next service page's number."""

    def __init__(self, listing: Any) -> None:  # noqa: ANN401 - app.agents.actions.knowledge_graph.ops.listing.ScopedListing
        self._listing = listing

    async def __call__(self, cursor: str | None, limit: int) -> RecordPage:
        page = int(cursor) if cursor else 1
        response = await self._listing.fetch(page, _SERVICE_PAGE)
        if not response.success:
            raise RuntimeError(response.error or "the knowledge service could not list records")
        items = [item for item in response.items or [] if getattr(item.nodeType, "value", item.nodeType) == "record"]
        if self._listing.inside is not None and items:
            admitted = await self._listing.inside([item.id for item in items])
            items = [item for item in items if item.id in admitted]
        more = bool(response.pagination and response.pagination.hasNext)
        rows = tuple(export_row(item) for item in items[:limit])
        return RecordPage(rows=rows, next=str(page + 1) if more else None)


def _strings(value: JsonValue, cap: int) -> list[str] | None:
    if not isinstance(value, list):
        return None
    return [item for item in value if isinstance(item, str)][:cap] or None


def listing_pages_for(
    state: MutableMapping[str, Any],
    log: HarnessLog,
) -> Callable[[Mapping[str, JsonValue]], Awaitable[RecordPages | ExportRefusal]]:
    """The pages of one export's arguments, as the run's person in the run's scope (``state``: the run's tool
    state, the same one ``knowledgegraph__list_files`` reads)."""

    async def pages(arguments: Mapping[str, JsonValue]) -> RecordPages | ExportRefusal:
        from app.agents.actions.knowledge_graph.ops.listing import plan_listing

        query = arguments.get("query")
        try:
            planned = await plan_listing(
                state,  # type: ignore[arg-type]  # a ChatState-shaped mapping
                query=query if isinstance(query, str) else None,
                source_ids=_strings(arguments.get("source_ids"), 50),
                node_types=["record"],
                record_types=_strings(arguments.get("record_types"), 20),
                sort_by="updatedAt",
                sort_order="desc",
                flatten=True,
            )
        except Exception as error:  # the export cannot start; the model is told, nothing was read
            log.warning("bulk_export.unplanned", errorType=type(error).__name__)
            return ExportRefusal("The knowledge service could not be read just now, so no export was started. Try again.")
        if isinstance(planned, tuple):
            ok, text = planned
            return ExportRefusal(_SELECTION_REFUSAL if ok else text)
        return ListingPages(planned)

    return pages


def export_catalog(
    catalog: ToolCatalog,
    *,
    state: MutableMapping[str, Any],
    shelf: ExportShelf,
    allowance: Callable[[], int],
    log: HarnessLog,
) -> tuple[ExportingCatalog, BulkExports]:
    """``catalog`` with the export tools beside it, and the attempt's exports (closed when the attempt ends)."""
    exports = BulkExports(shelf, log=log)
    plan = listing_pages_for(state, log)
    tools = BulkExportTools(
        names=EXPORT_NAMES,
        exports=exports,
        pages_for=plan,
        allowance=allowance,
        event=exported_event,
        shelf_path=KNOWLEDGE_PATH,
    )
    return ExportingCatalog(catalog, tools), exports


def export_effects() -> dict[str, ToolEffect]:
    """The export tools' effects, by name, as the catalog describes them."""
    effects: dict[str, ToolEffect] = {}
    for name in (EXPORT_TOOL, EXPORT_STATUS_TOOL):
        effect = export_tool_effect(EXPORT_NAMES, name)
        if effect is not None:
            effects[name] = effect
    return effects


# Records written in bulk to the sandbox's shelf, read there by the engine's own shell: their sources are the export's
# own record, not a viewer's label, so the result counts as content nobody can name (fail closed).
EXPORT_RULE: Final = Fixed("export", ResultProvenance("unknown", "first_party", ()))


def export_provenance() -> dict[str, ProvenanceRule]:
    """The export tools' provenance rules, by name (ADR-RUN-01 D3)."""
    return {EXPORT_TOOL: EXPORT_RULE, EXPORT_STATUS_TOOL: NO_CONTENT_RULE}


def is_export(name: str) -> bool:
    return name == EXPORT_TOOL


def is_export_status(name: str) -> bool:
    return name == EXPORT_STATUS_TOOL


__all__ = [
    "EXPORTED_EVENT",
    "EXPORT_NAMES",
    "EXPORT_STATUS_TOOL",
    "EXPORT_TOOL",
    "ListingPages",
    "export_catalog",
    "export_effects",
    "export_provenance",
    "export_row",
    "exported_event",
    "is_export",
    "is_export_status",
    "listing_pages_for",
]
