"""C-14: a test that swaps a tool's body must not change the catalog other tests see."""

from __future__ import annotations

import pytest

from harness_core.tools import toolset
from harness_core.tools.toolset import ToolsetBuilder

from . import world as w


def _names() -> set[str]:
    builder = ToolsetBuilder(w.FakeJira(None), name="jira", description="Jira", path_prefix="/tools/jira")
    return {tool.name for tool in builder.tools}


async def test_c14_a_replaced_tool_body_leaves_the_class_catalog_whole(monkeypatch: pytest.MonkeyPatch) -> None:
    """The first scan of a class is cached for the process; a replacement seen
    by a cold cache used to drop the tool for every later test on that worker."""
    monkeypatch.delitem(toolset._TOOL_ATTR_CACHE, w.FakeJira, raising=False)

    async def broken(self: object, jql: str) -> str:
        raise RuntimeError("jira is down")

    with pytest.MonkeyPatch.context() as patched:
        w.replace_tool(patched, w.FakeJira, "search_issues", broken)
        assert _names() == {"jira__search_issues", "jira__create_issue"}
    assert _names() == {"jira__search_issues", "jira__create_issue"}
