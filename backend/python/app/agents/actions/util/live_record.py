"""The temporary record of a file a tool read straight from a connector."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import Connectors, OriginTypes
from app.core.org_context import bound_org_id
from app.harness.tool_identity.settings import instance_tool_ids_enabled
from app.models.citation_source import live_record_id
from app.models.entities import FileRecord, LlmTextContent, RecordType

if TYPE_CHECKING:
    from collections.abc import Mapping, MutableMapping

logger = logging.getLogger(__name__)


_TOOLSET_INSTANCE = "_pipeshub_toolset_instance_id"


def bind_toolset_instance(action: object, instance_id: str | None) -> None:
    """Records on an action object the toolset instance whose credentials built it (``ToolInstanceCreator``)."""
    if instance_id:
        setattr(action, _TOOLSET_INSTANCE, instance_id)


def toolset_instance_of(action: object) -> str | None:
    instance = getattr(action, _TOOLSET_INSTANCE, None)
    return instance if isinstance(instance, str) and instance else None


def live_connector_id(state: Mapping[str, Any] | None, tool: str, instance_id: str | None = None) -> str:
    """The connector instance a live record names. ``tool_to_toolset_map`` is keyed by type, so with two
    instances of one type (``INSTANCE_TOOL_IDS``, P2.2) only the action's own instance is right; without the
    flag an agent has one instance per type and the map's answer is today's."""
    if instance_id and instance_tool_ids_enabled():
        return instance_id
    toolsets = (state or {}).get("tool_to_toolset_map") or {}
    return str(toolsets.get(tool) or "")


def live_file_record(
    state: Mapping[str, Any] | None,
    *,
    tool: str,
    connector: Connectors,
    external_record_id: str,
    record_name: str,
    mime_type: str | None,
    extension: str,
    instance_id: str | None = None,
) -> FileRecord:
    """A never-saved `FileRecord` marked live (P1b C-18): a live id, the run's
    org, the connector kind, the toolset instance that fetched it (``tool`` is
    its full name in ``tool_to_toolset_map``; ``instance_id`` the action's own,
    see ``live_connector_id``) and the item's external id. No record page exists
    for it, so it is not preview-renderable."""
    state = state or {}
    return FileRecord(
        id=live_record_id(),
        org_id=bound_org_id() or "",
        record_name=record_name,
        record_type=RecordType.FILE,
        external_record_id=external_record_id,
        version=1,
        origin=OriginTypes.CONNECTOR,
        connector_name=connector,
        connector_id=live_connector_id(state, tool, instance_id),
        mime_type=mime_type or "application/octet-stream",
        extension=extension,
        is_file=True,
        preview_renderable=False,
    )


def _tool_record(record: FileRecord) -> dict[str, Any]:
    """The record in the shape tools put in ``tool_records`` (a stored record's)."""
    # When a never-saved record was built is not when the file changed.
    tool_record = record.model_dump(mode="json", exclude_none=True, exclude={"created_at", "updated_at"})
    blocks = (tool_record.get("block_containers") or {}).get("blocks") or []
    # Citations address a block by its position; a parser may leave indexes unset.
    for position, block in enumerate(blocks):
        if block.get("index") is None:
            block["index"] = position
    tool_record["context_metadata"] = record.to_llm_context()
    tool_record["frontend_url"] = ""
    return tool_record


class LiveCitations:
    """Makes a live record citable in the run, as a fetched indexed record is
    (P1b C-19): its blocks are rendered with reference numbers from the run's
    ``CitationRefMapper`` and the record joins ``tool_records``, which the
    answer's citations are resolved against (and a durable run journals)."""

    def __init__(self, state: MutableMapping[str, Any] | None) -> None:
        self._state: MutableMapping[str, Any] = state if state is not None else {}
        self._rendered: dict[str, dict[str, Any]] = {}

    def render(self, record: FileRecord) -> list[LlmTextContent]:
        from app.utils.chat_helpers import (
            CITE_BLOCK_REFS_HINT,
            CitationRefMapper,
            record_to_message_content,
        )

        mapper = self._state.get("citation_ref_mapper") or CitationRefMapper()
        self._state["citation_ref_mapper"] = mapper
        tool_record = self._rendered[record.id] = _tool_record(record)
        content, _ = record_to_message_content(tool_record, ref_mapper=mapper)
        texts = [
            LlmTextContent(type="text", text=item["text"])
            for item in content
            if item.get("type") == "text" and item.get("text")
        ]
        texts.append(LlmTextContent(type="text", text=CITE_BLOCK_REFS_HINT))
        return texts

    def register(self, record: FileRecord) -> None:
        """Called once the model is given the content: a record it never saw is no source."""
        existing = self._state.get("tool_records")
        records = existing if isinstance(existing, list) else []
        if any(isinstance(r, dict) and r.get("id") == record.id for r in records):
            return
        tool_record = self._rendered.get(record.id) or _tool_record(record)
        self._state["tool_records"] = [*records, tool_record]
        logger.info(
            "citations.live_registered connector=%s blocks=%d toolRecords=%d",
            record.connector_name.value,
            len(record.block_containers.blocks),
            len(self._state["tool_records"]),
        )


__all__ = [
    "LiveCitations",
    "bind_toolset_instance",
    "live_connector_id",
    "live_file_record",
    "toolset_instance_of",
]
