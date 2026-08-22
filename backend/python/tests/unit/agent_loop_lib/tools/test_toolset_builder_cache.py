"""Regression coverage for the reduce-request-hotpath todo: `ToolsetBuilder`
must cache the `@tool`-decorated-attribute reflection scan per connector
CLASS (`toolset.py::_tool_attrs_for_class`), not redo the full `dir()` walk
for every per-request instance — while still binding every collected
`BoundMethodTool` to the CURRENT instance's own methods, never a stale
instance's, since two connector instances built from the same class in two
different requests carry two different (freshly authenticated) clients.
"""

from __future__ import annotations

from app.agent_loop_lib.tools.base import Tag
from app.agent_loop_lib.tools.decorators import tool
from app.agent_loop_lib.tools.toolset import (
    _TOOL_ATTR_CACHE,
    ToolsetBuilder,
    _tool_attrs_for_class,
)


class _FakeConnector:
    @tool(
        path="/tools/fakeconnector/search",
        short_description="Search",
        description="Search things",
        parameters=[],
        tags=[Tag(key="category", value="read")],
    )
    async def search(self, query: str = "") -> tuple[bool, str]:
        return True, f"results for {query}"

    @tool(
        path="/tools/fakeconnector/get",
        short_description="Get",
        description="Get one thing",
        parameters=[],
    )
    async def get(self, item_id: str = "") -> tuple[bool, str]:
        return True, item_id


class _OtherFakeConnector:
    @tool(
        path="/tools/otherfakeconnector/ping",
        short_description="Ping",
        description="Ping the service",
        parameters=[],
    )
    async def ping(self) -> tuple[bool, str]:
        return True, "pong"


def _build(instance) -> ToolsetBuilder:
    return ToolsetBuilder(
        instance, name="fake", description="Fake connector for tests",
        path_prefix="/tools/fakeconnector",
    )


class TestToolAttrsForClassCache:
    def setup_method(self) -> None:
        _TOOL_ATTR_CACHE.pop(_FakeConnector, None)
        _TOOL_ATTR_CACHE.pop(_OtherFakeConnector, None)

    def test_caches_result_per_class(self) -> None:
        first = _tool_attrs_for_class(_FakeConnector)
        assert _FakeConnector in _TOOL_ATTR_CACHE
        second = _tool_attrs_for_class(_FakeConnector)
        assert second is first  # same cached list object, not recomputed

    def test_finds_every_tool_decorated_method(self) -> None:
        attrs = _tool_attrs_for_class(_FakeConnector)
        names = {name for name, _meta in attrs}
        assert names == {"search", "get"}

    def test_different_classes_get_independent_cache_entries(self) -> None:
        fake_attrs = _tool_attrs_for_class(_FakeConnector)
        other_attrs = _tool_attrs_for_class(_OtherFakeConnector)
        assert {n for n, _ in fake_attrs} == {"search", "get"}
        assert {n for n, _ in other_attrs} == {"ping"}


class TestToolsetBuilderUsesCacheButBindsFreshInstance:
    def setup_method(self) -> None:
        _TOOL_ATTR_CACHE.pop(_FakeConnector, None)

    async def test_two_instances_of_same_class_get_correctly_bound_tools(self) -> None:
        """The metadata scan is cached per class, but each `ToolsetBuilder`
        must still bind its OWN instance's methods — a cache hit must never
        leak instance A's bound method into instance B's tool list."""
        instance_a = _FakeConnector()
        instance_b = _FakeConnector()

        toolset_a = _build(instance_a)  # first call: cache miss, populates it
        toolset_b = _build(instance_b)  # second call: cache hit

        search_a = next(t for t in toolset_a.tools if t.name == "fakeconnector__search")
        search_b = next(t for t in toolset_b.tools if t.name == "fakeconnector__search")

        assert search_a._bound_method.__self__ is instance_a
        assert search_b._bound_method.__self__ is instance_b

        result_a = await search_a.execute(query="a")
        result_b = await search_b.execute(query="b")
        assert result_a.data == "results for a"
        assert result_b.data == "results for b"

    def test_collected_tool_names_and_metadata_unaffected_by_cache(self) -> None:
        toolset = _build(_FakeConnector())
        names = {t.name for t in toolset.tools}
        assert names == {"fakeconnector__search", "fakeconnector__get"}

    def test_cache_hit_still_produces_correct_tool_count_for_second_instance(self) -> None:
        _build(_FakeConnector())  # populate cache
        toolset = _build(_FakeConnector())  # cache hit
        assert len(toolset.tools) == 2
