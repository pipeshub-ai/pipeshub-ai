"""The prompt lists as callable exactly the tools that are bound (A-13)."""
from types import SimpleNamespace

from app.agents.agent_loop.prompt_builder import BOUND_TOOL_NAMES_KEY, _bound_tool_view

CODE = ["query_code_graph", "get_neighbour", "read_code", "find_symbol_path"]


class _Registry:
    def __init__(self) -> None:
        self._groups = [SimpleNamespace(name="codegraph", tool_names=CODE),
                        SimpleNamespace(name="jira", tool_names=["jira_search", "jira_get"])]

    def toolsets(self) -> list:
        return self._groups

    def grouped_tool_names(self) -> set[str]:
        return {n for g in self._groups for n in g.tool_names}


ALL = ["search_knowledge", *CODE, "jira_search", "jira_get"]


def test_a_group_with_some_tools_bound_lists_only_those() -> None:
    state = {BOUND_TOOL_NAMES_KEY: ["get_neighbour", "read_code"]}
    callable_names, groups = _bound_tool_view(ALL, _Registry(), None, state)
    assert callable_names == ["search_knowledge", "get_neighbour", "read_code"]
    assert groups == frozenset({"codegraph"})


def test_a_pinned_group_is_bound_whole() -> None:
    callable_names, _ = _bound_tool_view(ALL, _Registry(), ["jira"], {})
    assert callable_names == ["search_knowledge", "jira_search", "jira_get"]


def test_tools_the_unlock_hook_granted_count_as_bound() -> None:
    state = {"unlocked_codegraph_tools": ["query_code_graph"]}
    callable_names, _ = _bound_tool_view(ALL, _Registry(), None, state)
    assert callable_names == ["search_knowledge", "query_code_graph"]
