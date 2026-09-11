"""CODE_FILE in retrieval unlocks the lazy codegraph toolset."""

from types import SimpleNamespace

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
