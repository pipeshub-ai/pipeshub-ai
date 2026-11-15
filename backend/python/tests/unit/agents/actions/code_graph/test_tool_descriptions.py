"""The tool descriptions, ported back from the pre-refactor dynamic-tools file.

That version's wording is the one the traces liked, so these pin the parts that
carry the behaviour rather than every sentence: `query_code_graph` telling the
model to search first and not glob a type hierarchy, `get_neighbour` describing
itself in terms of something you already hold, `read_code` stating the cost
trade-off between a symbol and a whole file, and every tool naming the neighbour
it hands off to.

Adjusted only where the implementation moved on: `select` no longer takes free
text or a symbol, `find_call_neighbors`' caller/callee became inbound/outbound/
any with `edge_types` and `depth`, and both `get_neighbour` and `read_code` now
accept a bare path.
"""
import re
from pathlib import Path

import pytest

from app.agent_loop_lib.tools.decorators import TOOL_META_ATTR, ToolMeta
from app.agents.actions.code_graph import code_graph as code_graph_module
from app.agents.actions.code_graph.code_graph import CodeGraph
from app.agents.actions.code_graph.neighbours import IMPACT_RELATIONS
from app.agents.actions.code_graph.ops import CODE_RELATIONS, EDGE_LANGUAGES
from app.agents.actions.code_graph.search import _TEXT_SEARCH_FILES, SEARCH_MODES

REGISTERED = sorted(n for n in dir(CodeGraph) if hasattr(getattr(CodeGraph, n), TOOL_META_ATTR))
NUMBER_WORDS = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen".split()
# How a description names each language whose calls resolve through imports; TSX is TypeScript.
LANGUAGE_NAMES = {"python": "Python", "javascript": "JavaScript", "typescript": "TypeScript", "tsx": "TypeScript",
                  "java": "Java", "go": "Go", "csharp": "C#", "php": "PHP", "rust": "Rust", "c": " C ", "cpp": "C++"}
HIDDEN_CALLERS = "ncalled means no indexed call from a file you can read"
# Upper-case words in descriptions that are not relation names.
NOT_RELATIONS = {"FIRST", "NOT", "HTTP", "ID", "PHP"}
MCP_TOOLS = Path(__file__).parents[6] / "nodejs" / "apps" / "src" / "modules" / "mcp" / "code-graph.tools.ts"
OPENAPI_SPEC = MCP_TOOLS.parents[1] / "api-docs" / "pipeshub-openapi.yaml"

HANDOFFS = [
    ("query_code_graph", ["read_code", "get_neighbour", "impact"]),
    ("get_neighbour", ["read_code", "get_neighbour", "impact"]),
    ("read_code", ["get_neighbour"]),
    ("find_symbol_path", ["read_code", "get_neighbour"]),
    ("search_code", ["read_code", "get_neighbour", "impact"]),
    ("search_semantic", ["read_code", "get_neighbour", "impact", "search_code"]),
    ("outline", ["read_code", "impact", "get_neighbour"]),
    ("impact", ["read_code", "get_neighbour"]),
    ("entry_points", ["outline", "read_code", "get_neighbour"]),
    ("impact_of_diff", ["read_code", "impact"]),
    ("history", ["read_code", "impact", "impact_of_diff"]),
]


def _meta(name) -> ToolMeta:
    return getattr(CodeGraph, name)._agent_tool_meta


class TestQueryCodeGraph:
    def test_it_sends_you_to_the_knowledge_search_for_a_connector_id(self) -> None:
        d = _meta("query_code_graph").description
        assert "Search the knowledge base FIRST" in d
        assert "`src/main.py` is ambiguous" in d, "must say WHY the connector matters"

    def test_it_does_not_tell_you_to_glob_a_type_hierarchy(self) -> None:
        d = _meta("query_code_graph").description
        assert "Do not glob a tree to list every implementer" in d
        assert "inbound get_neighbour" in d

    def test_the_dropped_select_shapes_are_not_advertised(self) -> None:
        """Free text and `path#symbol` selects were removed from the impl; a
        description still offering them buys a rejected call per attempt."""
        d = _meta("query_code_graph").description
        assert "free text" in d and "does not take" in d
        assert "group_by" not in d and "relations" not in d

    def test_degree_is_explained_as_a_ranking_to_act_on(self) -> None:
        d = _meta("query_code_graph").description
        assert "ranked by `degree`" in d
        assert "read the top of the list rather than sampling it" in d


class TestGetNeighbour:
    def test_it_is_framed_around_something_you_already_hold(self) -> None:
        d = _meta("get_neighbour").description
        assert "Give it something you already hold" in d
        assert "`file_path` on its own" in d, "the bare-path mode is the post-search move"

    def test_the_directions_are_the_current_ones(self) -> None:
        d = _meta("get_neighbour").description
        for direction in ("'inbound'", "'outbound'", "'any'"):
            assert direction in d
        assert "caller" not in d.replace("callers", ""), "caller/callee were removed"

    def test_inbound_is_marked_as_having_no_substitute(self) -> None:
        assert "no substitute" in _meta("get_neighbour").description

    def test_it_still_pairs_line_with_read_code(self) -> None:
        assert "read_code(lines=...)" in _meta("get_neighbour").description

    def test_it_forbids_calls_only_as_the_first_walk(self) -> None:
        d = _meta("get_neighbour").description
        assert "Do not open with `edge_types=['CALLS']`" in d
        assert "`chain`" in d or "METHOD" in d

    def test_a_set_question_starts_from_the_factory_or_base(self) -> None:
        d = _meta("get_neighbour").description
        assert "factory, registry, or base type" in d
        assert "inbound heritage" in d


class TestReadCode:
    def test_the_cost_tradeoff_is_stated(self) -> None:
        """The paragraph that tells the model when a whole file is worth it."""
        d = _meta("read_code").description
        assert "Cost trade-offs" in d
        assert "fills context fast" in d

    def test_a_bare_path_is_enough(self) -> None:
        assert "never need to list a file before reading it" in _meta("read_code").description

    def test_it_disclaims_resolving_calls(self) -> None:
        d = _meta("read_code").description
        assert "does not resolve where calls go" in d
        assert "instead of guessing filenames" in d


class TestFindSymbolPath:
    def test_it_warns_that_cross_language_pairs_have_no_path(self) -> None:
        d = _meta("find_symbol_path").description
        assert "one language and one repository" in d
        assert "Read the route handler instead" in d


class TestHandoffs:
    @pytest.mark.parametrize("name,neighbours", HANDOFFS)
    def test_each_tool_names_where_its_output_goes(self, name, neighbours) -> None:
        d = _meta(name).description
        for other in neighbours:
            assert other in d, f"{name} never mentions {other}"

    @pytest.mark.parametrize("name", REGISTERED)
    def test_descriptions_stay_short(self, name) -> None:
        """Every one of these is bound on every turn."""
        d = _meta(name).description
        assert len(d) < 1100, f"{name} description is {len(d)} chars"

    def test_every_tool_is_in_the_handoff_table(self) -> None:
        assert sorted(name for name, _ in HANDOFFS) == REGISTERED

    @pytest.mark.parametrize("name", REGISTERED)
    def test_each_says_when_to_use_it_what_it_returns_and_what_comes_next(self, name) -> None:
        """The three things an agent choosing a tool has to know, in that order."""
        d = _meta(name).description
        assert d.startswith("Use "), name
        assert d.index("Use ") < d.index("Returns") < d.index("Next:"), name

    def test_impact_asks_to_be_called_first(self) -> None:
        assert _meta("impact").description.startswith("Use first")


class TestTextMatchesTheCode:
    """Tool names, relations, caps and language lists written into prose drift from the code."""

    def test_the_module_lists_every_registered_tool_and_counts_them(self) -> None:
        doc = code_graph_module.__doc__
        listed = re.findall(r"^  (\w+)\s+—", doc, re.MULTILINE)
        assert sorted(listed) == REGISTERED
        count = re.search(r"^(\w+) tools over", doc, re.MULTILINE).group(1)
        assert NUMBER_WORDS.index(count.lower()) == len(REGISTERED)

    @pytest.mark.parametrize("name", REGISTERED)
    def test_every_relation_a_description_names_is_one_the_graph_has(self, name) -> None:
        named = set(re.findall(r"\b[A-Z]{2,}(?:_[A-Z]+)*\b", _meta(name).description)) - NOT_RELATIONS
        assert named <= set(CODE_RELATIONS), named - set(CODE_RELATIONS)

    def test_search_code_names_the_file_cap_it_applies(self) -> None:
        assert f"stop after {_TEXT_SEARCH_FILES} files" in _meta("search_code").description

    def test_search_code_describes_every_mode_it_takes(self) -> None:
        d = _meta("search_code").description
        for mode in SEARCH_MODES:
            assert f"'{mode}'" in d, mode

    def test_entry_points_names_every_language_whose_calls_resolve_through_imports(self) -> None:
        d = _meta("entry_points").description
        for language in EDGE_LANGUAGES:
            assert LANGUAGE_NAMES[language] in d, language

    def test_entry_points_says_only_readable_callers_count(self) -> None:
        """A caller in a file the user cannot read does not count, so its callee can still be listed."""
        assert HIDDEN_CALLERS in _meta("entry_points").description


@pytest.mark.skipif(not MCP_TOOLS.exists(), reason="the Node app is not in this checkout")
class TestMcpTextMatchesTheCode:
    """The MCP server describes the same tools in TypeScript, from the same code."""

    @pytest.fixture(scope="class")
    def ts(self) -> str:
        return MCP_TOOLS.read_text()

    def test_every_tool_is_renamed_for_mcp(self, ts) -> None:
        mcp_names = dict(re.findall(r"^  (\w+): '(pipeshub_code_\w+)',$", ts, re.MULTILINE))
        assert set(mcp_names) == set(REGISTERED)

    def test_every_tool_the_text_names_is_registered(self, ts) -> None:
        registered = set(re.findall(r"^  '(pipeshub_code_\w+)',$", ts, re.MULTILINE))
        assert registered, "CODE_GRAPH_TOOL_NAMES not found"
        named = set(re.findall(r"\bpipeshub_code_[a-z_]+\b", ts)) - {"pipeshub_code_"}
        assert named <= registered, named - registered

    def test_search_names_the_file_cap_it_applies(self, ts) -> None:
        assert f"at most {_TEXT_SEARCH_FILES} files" in ts

    def test_search_takes_and_describes_the_modes_search_code_takes(self, ts) -> None:
        search = ts.rsplit("'pipeshub_code_search',", 1)[1].split("server.registerTool(", 1)[0]
        enum = re.search(r"mode: z\s*\.enum\(\[(.*?)\]", search, re.S).group(1)
        assert tuple(re.findall(r"'(\w+)'", enum)) == SEARCH_MODES
        for mode in SEARCH_MODES:
            assert f'"{mode}"' in search, mode

    def test_entry_points_names_the_same_languages(self, ts) -> None:
        entry_points = ts.rsplit("'pipeshub_code_entry_points'", 1)[1]  # its registration, the last mention
        for language in EDGE_LANGUAGES:
            assert LANGUAGE_NAMES[language] in entry_points, language
        assert HIDDEN_CALLERS in entry_points

    def test_edge_types_lists_every_relation(self, ts) -> None:
        listed = re.search(r"const edgeTypes = .*?\.describe\(\s*(.*?)\);", ts, re.S).group(1)
        assert set(re.findall(r"\b[A-Z]{2,}(?:_[A-Z]+)*\b", listed)) == set(CODE_RELATIONS)

    def test_mcp_and_the_api_spec_take_the_edge_types_impact_follows(self, ts) -> None:
        enum = re.search(r"const impactEdgeTypes = z\s*\.array\(\s*z\.enum\(\[(.*?)\]", ts, re.S)
        assert set(re.findall(r"'(\w+)'", enum.group(1))) == set(IMPACT_RELATIONS)
        listed = re.search(r"`edge_types`\s+takes\s+any\s+of\s+(.*?);\s+another\s+value\s+is\s+refused",
                           OPENAPI_SPEC.read_text(), re.S)
        assert set(re.findall(r"`(\w+)`", listed.group(1))) == set(IMPACT_RELATIONS)

    def test_the_api_spec_names_every_mcp_tool(self, ts) -> None:
        spec = " ".join(OPENAPI_SPEC.read_text().split())
        for name in re.findall(r"^  '(pipeshub_code_\w+)',$", ts, re.MULTILINE):
            assert f"`{name}`" in spec, name
