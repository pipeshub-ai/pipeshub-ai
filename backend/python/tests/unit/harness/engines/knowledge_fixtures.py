"""A knowledge search tool over fixture docs, labelled by the run's ``CitationRefMapper`` (S1c, E2E3-F3)."""

from __future__ import annotations

from typing import Any, override

from app.utils.chat_helpers import CitationRefMapper, record_to_message_content
from harness_core.tools.base import ParameterType, Tag, Tool, ToolOutput, ToolParameter
from tests.harness_replay.knowledge_base import full_record
from tests.harness_replay.world import Doc

Q3 = Doc(id="q3", title="Q3 report", text="Revenue grew 11% in Q3. Churn fell to 2%. Gross margin was 58%.")


class KnowledgeGraphSearch(Tool):
    """``knowledgegraph__search`` as far as state goes: block 0 of each named doc, labelled by the run's mapper."""

    def __init__(self, state: dict[str, Any], docs: dict[str, Doc]) -> None:
        self._state, self._docs = state, docs

    @property
    @override
    def name(self) -> str:
        return "knowledgegraph__search"

    @property
    @override
    def short_description(self) -> str:
        return self.name

    @property
    @override
    def description(self) -> str:
        return f"The {self.name} tool."

    @property
    @override
    def path(self) -> str:
        return f"/toolsets/test/{self.name}"

    @property
    @override
    def tags(self) -> list[Tag]:
        return [Tag("category", "read")]

    @property
    @override
    def parameters(self) -> list[ToolParameter]:
        return [ToolParameter(name="q", type=ParameterType.STRING, description="query", required=False)]

    @override
    async def execute(self, **kwargs: Any) -> ToolOutput:
        doc = self._docs[str(kwargs.get("q"))]
        record = full_record(doc, snippets=True)
        mapper = self._state.get("citation_ref_mapper") or CitationRefMapper()
        content, mapper = record_to_message_content(record, ref_mapper=mapper, include_blocks={0})
        self._state["citation_ref_mapper"] = mapper
        self._state["virtual_record_id_to_result"] = {**(self._state.get("virtual_record_id_to_result") or {}), doc.id: record}
        return ToolOutput(success=True, data="".join(c["text"] for c in content if c.get("type") == "text"))


LOCKED = "rec-locked-memo"


class Navigate(KnowledgeGraphSearch):
    """``knowledgegraph__navigate`` as far as state goes: a Record ID and a title, no content (``remember_record_ids``)."""

    def __init__(self, state: dict[str, Any]) -> None:
        super().__init__(state, {})

    @property
    @override
    def name(self) -> str:
        return "knowledgegraph__navigate"

    @property
    @override
    def parameters(self) -> list[ToolParameter]:
        return [ToolParameter(name="node_id", type=ParameterType.STRING, description="node", required=False)]

    @override
    async def execute(self, **kwargs: Any) -> ToolOutput:
        from app.modules.agents.qna.chat_state import remember_record_ids

        remember_record_ids(self._state, [LOCKED])
        return ToolOutput(success=True, data=f"Record ID: {LOCKED}\nRecord: Locked memo\nType: FILE")


__all__ = ["LOCKED", "Q3", "KnowledgeGraphSearch", "Navigate"]
