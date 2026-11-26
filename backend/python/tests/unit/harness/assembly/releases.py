"""Releases of the assembly world's toolsets: the same toolset as another release's code declares it.

A worker of a newer release answers a claim with whatever its own code declares; these stand in for that code.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from harness_core.tools.tags import TAG_EFFECT_READ, TAG_SINK_SELF

from . import world as w

if TYPE_CHECKING:
    import pytest

SPRINT: dict[str, Any] = {"type": "object", "properties": {"id": {"type": "integer"}}}


def jira(
    *,
    description: str = "Search Jira issues.",
    statuses: tuple[str, ...] = ("open", "closed"),
    items: dict[str, Any] | None = None,
    sprint: dict[str, Any] | None = None,
    required: tuple[str, ...] = ("assignee",),
    tags: tuple[Tag, ...] = (TAG_EFFECT_READ, TAG_SINK_SELF),
    create_description: str = "Create a Jira issue.",
) -> type:
    """The Jira toolset as one release declares it; a call with other arguments is another release."""

    class Jira:
        def __init__(self, client: object) -> None:
            self.client = client

        @tool(
            path="/tools/jira/search_issues",
            short_description="Search issues",
            description=description,
            parameters=[
                ToolParameter(name="status", type=ParameterType.STRING, description="Status", enum=list(statuses)),
                ToolParameter(name="labels", type=ParameterType.ARRAY, description="Labels", items=items or {"type": "string"}),
                ToolParameter(
                    name="filter",
                    type=ParameterType.OBJECT,
                    description="Filter",
                    properties={"assignee": {"type": "string"}, "sprint": sprint or SPRINT},
                    required_properties=list(required),
                ),
            ],
            tags=list(tags),
        )
        async def search_issues(self, status: str, labels: list[str], filter: dict[str, Any]) -> str:  # noqa: A002
            return '{"issues": []}'

        @tool(
            path="/tools/jira/create_issue",
            short_description="Create an issue",
            description=create_description,
            parameters=[ToolParameter(name="title", type=ParameterType.STRING, description="Title", required=True)],
        )
        async def create_issue(self, title: str) -> str:
            return '{"id": "J-1"}'

    return Jira


def knowledge(description: str) -> type:
    """The platform's knowledge toolset with another description of its search."""

    class Knowledge:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        @tool(
            path="/tools/knowledgegraph/search",
            short_description="Search knowledge",
            description=description,
            parameters=[ToolParameter(name="query", type=ParameterType.STRING, description="Query", required=True)],
            tags=[Tag("category", "read")],
        )
        async def search(self, query: str) -> str:
            return '{"hits": []}'

    return Knowledge


def deploy(monkeypatch: pytest.MonkeyPatch, **toolsets: type) -> None:
    """The deployment's workers now run a release that declares these toolsets so."""
    catalog = dict(w.ToolsetCatalog().get_all_toolsets())
    for name, declared in toolsets.items():
        catalog[name] = {**catalog[name], "class": declared}
    monkeypatch.setattr(w.ToolsetCatalog, "get_all_toolsets", lambda self: catalog)


__all__ = ["SPRINT", "deploy", "jira", "knowledge"]
