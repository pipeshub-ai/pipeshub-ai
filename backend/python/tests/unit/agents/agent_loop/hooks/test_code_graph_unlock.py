"""CODE_FILE in retrieval unlocks the lazy codegraph toolset."""

from types import SimpleNamespace

import pytest

from app.agents.agent_loop.hooks.code_graph_unlock import (
    CODE_GRAPH_TOOLSET,
    unlock_code_graph_tools,
    virtual_records_include_code,
)


class TestVirtualRecordsIncludeCode:
    def test_true_when_record_type_is_code_file(self) -> None:
        assert virtual_records_include_code({
            "vr-1": {"record_type": "CODE_FILE", "record_name": "a.py"},
        })

    def test_true_from_context_metadata_type_line(self) -> None:
        assert virtual_records_include_code({
            "vr-1": {"context_metadata": "Record ID: x\nType: CODE_FILE\n"},
        })

    def test_false_for_tickets_and_empty(self) -> None:
        assert not virtual_records_include_code({})
        assert not virtual_records_include_code({
            "vr-1": {"record_type": "TICKET"},
        })


class TestUnlockCodeGraphTools:
    def test_grows_visible_tools_with_granted_codegraph_names(self) -> None:
        registry = SimpleNamespace(
            tools_in_toolset=lambda name: [
                "codegraph__query_code_graph",
                "codegraph__get_neighbour",
                "codegraph__read_code",
                "codegraph__find_symbol_path",
            ] if name == CODE_GRAPH_TOOLSET else [],
            names=lambda: [],
            has_toolsets=lambda: True,
        )
        spec = SimpleNamespace(tool_names=[
            "codegraph__query_code_graph",
            "codegraph__get_neighbour",
            "knowledgegraph__search",
        ])
        runtime = SimpleNamespace(tool_registry=registry)
        run_scope = SimpleNamespace(
            spec=spec, runtime=runtime, visible_tools={"knowledgegraph__search"},
        )

        unlocked = unlock_code_graph_tools(run_scope, registry)

        assert set(unlocked) == {
            "codegraph__query_code_graph",
            "codegraph__get_neighbour",
        }
        assert "codegraph__query_code_graph" in run_scope.visible_tools
        assert "codegraph__get_neighbour" in run_scope.visible_tools
        assert "codegraph__read_code" not in run_scope.visible_tools

    def test_noop_when_toolset_not_loaded(self) -> None:
        registry = SimpleNamespace(tools_in_toolset=lambda name: [])
        run_scope = SimpleNamespace(
            spec=SimpleNamespace(tool_names=None),
            runtime=SimpleNamespace(tool_registry=registry),
            visible_tools=set(),
        )
        assert unlock_code_graph_tools(run_scope, registry) == []
        assert run_scope.visible_tools == set()


class TestCodeIntent:
    """A turn whose retrieval brushed one code file is not a code question (A-10)."""

    CODE = {"record_type": "CODE_FILE"}
    DOC = {"record_type": "FILE"}

    def test_a_policy_question_with_one_stray_code_hit_does_not_unlock(self) -> None:
        from app.agents.agent_loop.hooks.code_graph_unlock import has_code_intent
        records = {"a": self.DOC, "b": self.DOC, "c": self.CODE}
        assert not has_code_intent("what happens when I submit an expense report", records)

    @pytest.mark.parametrize("query", [
        "who calls the payment handler",
        "what does build_code_graph_edges do",
        "where is getUserById defined",
        "explain app/events/events.py",
        "how does `RecordAccess` decide",
    ])
    def test_a_code_question_unlocks(self, query: str) -> None:
        from app.agents.agent_loop.hooks.code_graph_unlock import has_code_intent
        assert has_code_intent(query, {"c": self.CODE, "d": self.DOC, "e": self.DOC})

    def test_mostly_code_results_unlock_whatever_the_wording(self) -> None:
        from app.agents.agent_loop.hooks.code_graph_unlock import has_code_intent
        assert has_code_intent("what happens at checkout", {"a": self.CODE, "b": self.CODE,
                                                            "c": self.DOC})


@pytest.mark.asyncio
async def test_tool_search_ranks_code_tools_for_code_questions_only() -> None:
    """The tags held "what happens when"; the tokenizer keeps every word (A-10)."""
    from app.agent_loop_lib.core.text_scoring import tokenize
    from app.agent_loop_lib.tools.base import ToolSummary
    from app.agent_loop_lib.tools.index import _score
    from app.agents.actions.code_graph import code_graph as cg

    summary = ToolSummary(name="get_neighbour", short_description="Walk code edges",
                          path="/tools/codegraph/get_neighbour", tags=tuple(cg._NEIGHBOUR_TAGS))
    assert _score(tokenize("what happens when I submit an expense report"), summary) == 0
    assert _score(tokenize("who calls the payment handler function"), summary) > 0
