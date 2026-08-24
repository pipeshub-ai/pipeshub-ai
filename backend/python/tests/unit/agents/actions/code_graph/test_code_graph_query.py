"""The generic `query_code_graph` primitive.

One call shape covers callers, file contents, cold-start lookup and the module
graph, so the tests are organised by which of those the arguments select.
"""
import pytest

from app.agents.actions.code_graph.query import query_code_graph_impl

from .conftest import ORG, USER, QueryGraphProvider

pytestmark = pytest.mark.asyncio


def _ctx(graph, user=USER):
    return {"graph_provider": graph, "org_id": ORG, "user_id": user}


def _symbols(result):
    return {n["symbol_id"] for n in result["nodes"]}


class TestSelectorResolution:
    async def test_path_glob(self, query_graph):
        result = await query_code_graph_impl(**_ctx(query_graph), select="web/ui/*")
        assert result["resolved_as"] == "path"
        assert _symbols(result) == {"web_ui_panel_render", "web_ui_panel_imports"}

    async def test_literal_file_path(self, query_graph):
        result = await query_code_graph_impl(**_ctx(query_graph), select="src/b.py")
        assert result["resolved_as"] == "path"
        assert _symbols(result) == {"src_b_target", "src_b_thing", "src_b_thing_run"}

    async def test_glob_does_not_leak_its_prefix_siblings(self, query_graph):
        # The DB-side filter is a prefix, so `web/api/*` must not return web/ui.
        result = await query_code_graph_impl(**_ctx(query_graph), select="web/api/*")
        assert _symbols(result) == {"web_api_client_fetch"}

    async def test_exact_symbol_id(self, query_graph):
        result = await query_code_graph_impl(**_ctx(query_graph), select="src_b_target")
        assert result["resolved_as"] == "symbol_id"
        assert _symbols(result) == {"src_b_target"}

    async def test_free_text_needs_no_prior_knowledge(self, query_graph):
        """The cold-start case: the only selector an agent can use as a first call."""
        result = await query_code_graph_impl(**_ctx(query_graph), select="target")
        assert result["resolved_as"] == "text"
        assert result["nodes"][0]["symbol_id"] == "src_b_target"  # exact beats substring
        assert "web_api_client_fetch" in _symbols(result)  # fetchTarget

    async def test_no_match_is_empty_not_an_error(self, query_graph):
        result = await query_code_graph_impl(**_ctx(query_graph), select="nosuchthing")
        assert result["matches"] == 0
        assert result["nodes"] == [] and "error" not in result


class TestNoise:
    async def test_filler_kinds_are_excluded_from_free_text(self, query_graph):
        # Two blocks are named `run`: a method and an imports span.
        result = await query_code_graph_impl(**_ctx(query_graph), select="run")
        assert _symbols(result) == {"src_b_thing_run"}

    async def test_filler_kinds_stay_reachable_by_path(self, query_graph):
        result = await query_code_graph_impl(**_ctx(query_graph), select="web/ui/panel.ts")
        assert "web_ui_panel_imports" in _symbols(result)


class TestExpansion:
    async def test_depth_zero_returns_no_edges(self, query_graph):
        result = await query_code_graph_impl(
            **_ctx(query_graph), select="src_a_caller", depth=0)
        assert result["edges"] == []

    async def test_inbound_expansion_finds_callers(self, query_graph):
        """Reported the way the edge points: the caller calls the callee.

        Emitting the walk order instead would read as `target` calling its own
        caller, which reverses every dependency an agent draws from it.
        """
        result = await query_code_graph_impl(
            **_ctx(query_graph), select="src_b_target",
            relations=["CALLS"], direction="inbound", depth=1,
        )
        assert result["edges"] == [{
            "from": "src/a.py#src_a_caller",
            "to": "src/b.py#src_b_target",
            "relation": "CALLS",
        }]

    async def test_relations_filter_the_walk(self, query_graph):
        result = await query_code_graph_impl(
            **_ctx(query_graph), select="src_b_target",
            relations=["INHERITS"], direction="outbound", depth=1,
        )
        assert [e["relation"] for e in result["edges"]] == ["INHERITS"]

    async def test_unknown_relation_names_are_dropped(self, query_graph):
        result = await query_code_graph_impl(
            **_ctx(query_graph), select="src_b_target",
            relations=["NOT_A_RELATION"], direction="outbound", depth=1,
        )
        # Falls back to every relation rather than silently matching nothing.
        assert [e["relation"] for e in result["edges"]] == ["INHERITS"]


class TestGrouping:
    async def test_directory_rollup_weights_the_underlying_edges(self, query_graph):
        """An import edge points at a whole record, a call edge at a block.

        `web/ui` reaches `web/api` twice, once each way, and both have to land
        on the same module pair or the module graph undercounts.
        """
        result = await query_code_graph_impl(
            **_ctx(query_graph), select="web/**",
            relations=["CALLS", "IMPORTS_FROM"], direction="outbound",
            group_by="directory",
        )
        assert result["grouped_by"] == "directory"
        assert result["edges"] == [{
            "from": "web/ui", "to": "web/api", "weight": 2,
            "relations": ["IMPORTS_FROM", "CALLS"],
        }]
        assert result["groups"] == [
            {"group": "web/api", "files": 1},
            {"group": "web/ui", "files": 1},
        ]

    async def test_depth_follows_the_selector(self, query_graph):
        """`web/**` groups at `web/ui`; `web/ui/**` would group a level deeper.

        A fixed depth cannot serve both — it either merges every module of a
        deep tree into one bucket or splits a shallow one into leaves.
        """
        shallow = await query_code_graph_impl(
            **_ctx(query_graph), select="web/**",
            relations=["CALLS"], direction="outbound", group_by="directory")
        assert [g["group"] for g in shallow["groups"]] == ["web/api", "web/ui"]
        assert shallow["scope"] == "web/"

    async def test_file_rollup_keeps_full_paths(self, query_graph):
        result = await query_code_graph_impl(
            **_ctx(query_graph), select="web/**",
            relations=["CALLS", "IMPORTS_FROM"], direction="outbound",
            group_by="file",
        )
        assert {g["group"] for g in result["groups"]} == {
            "web/ui/panel.ts", "web/api/client.ts"}
        assert result["edges"][0]["from"] == "web/ui/panel.ts"

    async def test_intra_group_edges_are_dropped(self, query_graph):
        # src/a.py and src/b.py both roll up to `src`, so the CALLS edge between
        # them is a self-loop at this level and carries no information.
        result = await query_code_graph_impl(
            **_ctx(query_graph), select="src/**",
            relations=["CALLS"], direction="outbound", group_by="directory",
        )
        assert result["edges"] == []

    async def test_inbound_reverses_the_reported_edge(self, query_graph):
        """Asked who depends on `web/api`, the edge still reads api <- ui."""
        result = await query_code_graph_impl(
            **_ctx(query_graph), select="web/api/**",
            relations=["CALLS"], direction="inbound", group_by="directory",
        )
        assert result["edges"] == [{
            "from": "web/ui", "to": "web/api", "weight": 1, "relations": ["CALLS"],
        }]


class TestTruncation:
    async def test_the_true_total_is_reported(self, query_graph):
        result = await query_code_graph_impl(
            **_ctx(query_graph), select="src/b.py", limit=1)
        assert result["truncated"] is True
        assert result["matches"] == 3
        assert len(result["nodes"]) == 1

    async def test_a_complete_result_says_so(self, query_graph):
        result = await query_code_graph_impl(
            **_ctx(query_graph), select="src/b.py", limit=50)
        assert result["truncated"] is False


class TestArgumentValidation:
    @pytest.mark.parametrize("kwargs", [
        {"select": "   "},
        {"select": "src/b.py", "direction": "sideways"},
        {"select": "src/b.py", "group_by": "planet"},
    ])
    async def test_rejected(self, query_graph, kwargs):
        result = await query_code_graph_impl(**_ctx(query_graph), **kwargs)
        assert "error" in result

    async def test_depth_is_clamped_not_rejected(self, query_graph):
        result = await query_code_graph_impl(
            **_ctx(query_graph), select="src_a_caller", depth=99)
        assert "error" not in result


class TestAccessControl:
    """A denial and a miss must be indistinguishable.

    Returning `matches: 3, nodes: []` would tell the agent that the file exists
    and how much is in it, which is the leak the empty result exists to prevent.
    """

    async def test_denied_user_sees_a_plain_miss(self, query_graph):
        denied = await query_code_graph_impl(
            **_ctx(query_graph, user="intruder"), select="src/b.py")
        missing = await query_code_graph_impl(
            **_ctx(query_graph), select="src/nosuchfile.py")
        assert denied["matches"] == 0 and "error" not in denied
        assert {k: v for k, v in denied.items() if k != "select"} == \
               {k: v for k, v in missing.items() if k != "select"}

    async def test_unreadable_neighbour_is_dropped_from_the_walk(self):
        graph = QueryGraphProvider(deny_records={"rec-b"})
        result = await query_code_graph_impl(
            **_ctx(graph), select="src_a_caller",
            relations=["CALLS"], direction="outbound", depth=1,
        )
        assert result["edges"] == []
        assert _symbols(result) == {"src_a_caller"}

    async def test_grouped_import_target_is_gated(self):
        """An import edge names a record the caller may not read.

        Without the record-level gate the module graph would still print
        `web/api/client.ts` as a dependency, disclosing a file by its path.
        """
        graph = QueryGraphProvider(deny_records={"rec-d"})
        result = await query_code_graph_impl(
            **_ctx(graph), select="web/ui/",
            relations=["IMPORTS_FROM"], direction="outbound", group_by="directory",
        )
        assert result["edges"] == []
        # The module the caller *can* read still appears — redacting the
        # dependency must not silently redact the dependent.
        assert [g["group"] for g in result["groups"]] == ["web/ui"]

    async def test_denied_user_gets_no_module_graph(self):
        graph = QueryGraphProvider()
        result = await query_code_graph_impl(
            **_ctx(graph, user="intruder"), select="web/**",
            relations=["CALLS"], direction="outbound", group_by="directory",
        )
        assert result["groups"] == [] and result["edges"] == []
        assert "error" not in result

    async def test_a_failed_permission_lookup_does_not_fail_open(self):
        class Broken(QueryGraphProvider):
            async def get_accessible_virtual_record_ids(self, *a, **k):
                raise RuntimeError("neo4j is down")

        result = await query_code_graph_impl(
            **_ctx(Broken()), select="web/**", group_by="directory")
        # Not an empty graph: "no dependencies" would be stated as fact.
        assert "error" in result
        assert result.get("edges") is None
