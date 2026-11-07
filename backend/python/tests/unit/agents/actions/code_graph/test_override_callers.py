"""Callers and impact of an override: what reaches the methods it overrides reaches it too."""
import pytest

from app.agents.actions.code_graph import code_graph as cg

from .conftest import CONN, ORG, USER, FakeGraphProvider

pytestmark = pytest.mark.asyncio


async def test_an_overrides_callers_include_the_callers_of_the_method_it_overrides(monkeypatch) -> None:
    """`self.speak()` inside Animal, or on a value typed as Animal, runs Dog.speak for a Dog; the
    graph links that call to Animal.speak alone, so Dog.speak's caller list left it out."""
    asked = []

    async def neighbours(**kwargs) -> dict:
        asked.append((kwargs["qualified_name"], kwargs["direction"], tuple(kwargs["edge_types"])))
        if kwargs["edge_types"] == ["OVERRIDES"]:
            return {"neighbors": [{"file_path": "src/animal.py", "qualified_name": "method:Animal.speak",
                                   "relation": "OVERRIDES"}]}
        return {"neighbors": [{"file_path": "src/zoo.py", "qualified_name": "function:feed", "line": 9}]}

    monkeypatch.setattr(cg, "get_neighbour_impl", neighbours)
    tool = cg.CodeGraph({"org_id": ORG, "user_id": USER,
                         "graph_provider": FakeGraphProvider(), "blob_store": object()})
    out = await tool._with_dispatch({"neighbors": []}, CONN, "src/dog.py", None, "method:Dog.speak", None,
                                    include_tests=False)
    assert asked == [("method:Dog.speak", "outbound", ("OVERRIDES",)),
                     ("method:Animal.speak", "inbound", ("CALLS",))]
    assert out["through_overridden_methods"]["by_base"] == {
        "src/animal.py::method:Animal.speak": [{"file_path": "src/zoo.py", "qualified_name": "function:feed",
                                                "line": 9}]}


async def test_an_overrides_impact_includes_the_impact_of_the_method_it_overrides(monkeypatch) -> None:
    async def neighbours(**_kwargs) -> dict:
        return {"neighbors": [{"file_path": "src/animal.py", "qualified_name": "method:Animal.speak",
                               "relation": "OVERRIDES"}]}

    async def impact(**kwargs) -> dict:
        assert kwargs["qualified_name"] == "method:Animal.speak" and kwargs["depth"] == 3
        return {"impacted_files": [{"file_path": "src/zoo.py", "symbols": ["function:feed"]}]}

    monkeypatch.setattr(cg, "get_neighbour_impl", neighbours)
    monkeypatch.setattr(cg, "impact_impl", impact)
    tool = cg.CodeGraph({"org_id": ORG, "user_id": USER,
                         "graph_provider": FakeGraphProvider(), "blob_store": object()})
    out = await tool._with_dispatch({"impacted_files": []}, CONN, "src/dog.py", None, "method:Dog.speak", 3,
                                    include_tests=False, edge_types=["CALLS"])
    assert out["through_overridden_methods"]["by_base"]["src/animal.py::method:Animal.speak"] == [
        {"file_path": "src/zoo.py", "symbols": ["function:feed"]}]


async def test_a_method_that_overrides_nothing_gets_no_section(monkeypatch) -> None:
    async def neighbours(**_kwargs) -> dict:
        return {"neighbors": []}

    monkeypatch.setattr(cg, "get_neighbour_impl", neighbours)
    tool = cg.CodeGraph({"org_id": ORG, "user_id": USER,
                         "graph_provider": FakeGraphProvider(), "blob_store": object()})
    out = await tool._with_dispatch({"neighbors": []}, CONN, "src/dog.py", None, "method:Dog.bark", None,
                                    include_tests=False)
    assert out == {"neighbors": []}


async def test_the_answer_stands_when_the_overridden_methods_cannot_be_walked(monkeypatch) -> None:
    async def neighbours(**_kwargs) -> dict:
        raise RuntimeError("graph down")

    monkeypatch.setattr(cg, "get_neighbour_impl", neighbours)
    tool = cg.CodeGraph({"org_id": ORG, "user_id": USER,
                         "graph_provider": FakeGraphProvider(), "blob_store": object()})
    out = await tool._with_dispatch({"neighbors": []}, CONN, "src/dog.py", None, "method:Dog.speak", None,
                                    include_tests=False)
    assert out == {"neighbors": []}


async def test_a_base_whose_walk_fails_is_left_out_not_reported_empty(monkeypatch) -> None:
    async def neighbours(**kwargs) -> dict:
        if kwargs["edge_types"] == ["OVERRIDES"]:
            return {"neighbors": [{"file_path": "src/animal.py", "qualified_name": "method:Animal.speak",
                                   "relation": "OVERRIDES"},
                                  {"file_path": "src/pet.py", "qualified_name": "method:Pet.speak",
                                   "relation": "OVERRIDES"}]}
        if kwargs["qualified_name"] == "method:Animal.speak":
            return {"error": "walk failed"}
        return {"neighbors": [{"file_path": "src/zoo.py", "qualified_name": "function:feed", "line": 9}]}

    monkeypatch.setattr(cg, "get_neighbour_impl", neighbours)
    tool = cg.CodeGraph({"org_id": ORG, "user_id": USER,
                         "graph_provider": FakeGraphProvider(), "blob_store": object()})
    out = await tool._with_dispatch({"neighbors": []}, CONN, "src/dog.py", None, "method:Dog.speak", None,
                                    include_tests=False)
    assert list(out["through_overridden_methods"]["by_base"]) == ["src/pet.py::method:Pet.speak"]


async def test_the_answer_stands_when_the_override_walk_returns_an_error(monkeypatch) -> None:
    async def neighbours(**kwargs) -> dict:
        assert kwargs["edge_types"] == ["OVERRIDES"], "no base walk after the override walk failed"
        return {"error": "walk failed"}

    monkeypatch.setattr(cg, "get_neighbour_impl", neighbours)
    tool = cg.CodeGraph({"org_id": ORG, "user_id": USER,
                         "graph_provider": FakeGraphProvider(), "blob_store": object()})
    out = await tool._with_dispatch({"neighbors": []}, CONN, "src/dog.py", None, "method:Dog.speak", None,
                                    include_tests=False)
    assert out == {"neighbors": []}
