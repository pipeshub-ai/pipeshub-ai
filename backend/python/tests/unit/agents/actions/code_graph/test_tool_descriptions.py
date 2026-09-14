"""The tool descriptions, ported back from the pre-refactor dynamic-tools file.

That version's wording is the one the traces liked, so these pin the parts that
carry the behaviour rather than every sentence: `query_code_graph` telling the
model to search first and call again as it narrows, `get_neighbour` describing
itself in terms of something you already hold, `read_code` stating the cost
trade-off between a symbol and a whole file, and every tool naming the neighbour
it hands off to.

Adjusted only where the implementation moved on: `select` no longer takes free
text or a symbol, `find_call_neighbors`' caller/callee became inbound/outbound/
any with `edge_types` and `depth`, and both `get_neighbour` and `read_code` now
accept a bare path.
"""
import pytest

from app.agents.actions.code_graph.code_graph import CodeGraph

HANDOFFS = [
    ("query_code_graph", ["read_code", "get_neighbour"]),
    ("get_neighbour", ["read_code", "get_neighbour"]),
    ("read_code", ["get_neighbour"]),
    ("find_symbol_path", []),
]


def _meta(name):
    return getattr(CodeGraph, name)._agent_tool_meta


class TestQueryCodeGraph:
    def test_it_sends_you_to_the_knowledge_search_for_a_connector_id(self) -> None:
        d = _meta("query_code_graph").description
        assert "Search the knowledge base FIRST" in d
        assert "`src/main.py` is ambiguous" in d, "must say WHY the connector matters"

    def test_it_expects_to_be_called_more_than_once(self) -> None:
        assert "one call rarely answers a broad question" in _meta("query_code_graph").description

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

    @pytest.mark.parametrize("name", [n for n, _ in HANDOFFS])
    def test_descriptions_stay_short(self, name) -> None:
        """Every one of these is bound on every turn."""
        d = _meta(name).description
        assert len(d) < 2100, f"{name} description is {len(d)} chars"
