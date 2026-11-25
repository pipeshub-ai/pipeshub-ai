"""FR-8 (S1c): ``ToolRegistry.restricted``, the registry a narrower grant (stage 1's engine) executes against."""

from __future__ import annotations

from typing import Any, override

import pytest

from harness_core.tools.base import ParameterType, Tag, Tool, ToolOutput, ToolParameter
from harness_core.tools.errors import ToolNotFoundError
from harness_core.tools.executor import ToolExecutor
from harness_core.tools.registry import ToolRegistry


class _Named(Tool):
    def __init__(self, name: str) -> None:
        super().__init__()
        self._name = name

    @property
    @override
    def name(self) -> str:
        return self._name

    @property
    @override
    def short_description(self) -> str:
        return self._name

    @property
    @override
    def description(self) -> str:
        return self._name

    @property
    @override
    def path(self) -> str:
        return f"/toolsets/mail/{self._name}"

    @property
    @override
    def tags(self) -> list[Tag]:
        return [Tag("category", "read")]

    @property
    @override
    def parameters(self) -> list[ToolParameter]:
        return [ToolParameter(name="q", type=ParameterType.STRING, description="q", required=False)]

    @override
    async def execute(self, **kwargs: Any) -> ToolOutput:
        return ToolOutput(success=True, data=self._name)


def _mail_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_tool(_Named("mail__read_inbox"), extra_tags=(Tag("toolset", "mail"),))
    registry.register_tool(_Named("mail__send_email"), extra_tags=(Tag("toolset", "mail"),))
    registry.register_toolset("mail", "Mail", ["mail__read_inbox", "mail__send_email"])
    return registry


def test_fr_8_a_restricted_registry_holds_only_the_named_tools_with_their_toolset_tags() -> None:
    full = _mail_registry()
    narrowed = full.restricted(["mail__read_inbox"])
    assert narrowed.names() == ["mail__read_inbox"]
    assert narrowed.tags_for_name("mail__read_inbox") == full.tags_for_name("mail__read_inbox")
    assert narrowed.resolve_by_name("mail__read_inbox") is full.resolve_by_name("mail__read_inbox")
    with pytest.raises(ToolNotFoundError):
        full.restricted(["mail__nothing"])


def test_fr_8_name_repair_on_a_restricted_registry_resolves_only_to_granted_tools() -> None:
    full = _mail_registry()
    narrowed = ToolExecutor(full.restricted(["mail__read_inbox"]))
    assert narrowed.resolve_tool_name("mail__send_email") == "mail__send_email"
    assert not full.restricted(["mail__read_inbox"]).has("mail__send_email")
    assert narrowed.resolve_tool_name("mail__read_inboxmail__read_inbox") == "mail__read_inbox"


def test_wp79_fu5_a_withdrawn_tool_is_neither_listed_nor_callable() -> None:
    registry = _mail_registry()

    assert registry.withdraw("mail__send_email") is True
    assert registry.withdraw("mail__send_email") is False

    assert not registry.has("mail__send_email")
    assert [s.name for s in registry.schemas(["mail"])] == ["mail__read_inbox"]
    assert registry.tools_in_toolset("mail") == ["mail__read_inbox"]
    with pytest.raises(ToolNotFoundError):
        registry.resolve_by_name("mail__send_email")
