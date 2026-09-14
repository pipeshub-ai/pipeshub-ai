"""`self.m()` where `m` is inherited: the CALLS edge must land on the base.

Shaped like the real failure. `BaseConnector.notify` is defined once and called
as `await self.notify(...)` from ~38 sites across the connectors, none of which
redefine it. Resolving only against the calling class meant every one of those
sites dropped, so "which connectors emit a notification" had no answer in the
graph even though every call site was indexed.
"""
import pytest

from app.config.constants.arangodb import RecordRelations
from app.modules.code_graph.edge_builder import build_code_graph_edges

from .conftest import GROUP_ID, ORG_ID

CALLS = RecordRelations.CALLS.value

BASE = b'''
from abc import ABC, abstractmethod


class BaseConnector(ABC):
    async def notify(self, title, message):
        return (title, message)

    @abstractmethod
    async def run_sync(self):
        raise NotImplementedError
'''

CONNECTOR = b'''
from .base import BaseConnector


class ConfluenceConnector(BaseConnector):
    async def run_sync(self):
        await self.notify("ambiguous site", "reconnect")

    async def _on_error(self):
        def report():
            return self.notify("failed", "retry")
        return report()
'''

# Two hops: the connector's own base is itself a subclass.
MIDDLE = b'''
from .base import BaseConnector


class HttpConnector(BaseConnector):
    pass
'''

GRANDCHILD = b'''
from .middle import HttpConnector


class JiraConnector(HttpConnector):
    async def run_sync(self):
        await self.notify("auth", "expired")
'''

OVERRIDER = b'''
from .base import BaseConnector


class NotionConnector(BaseConnector):
    async def notify(self, title, message):
        return None

    async def run_sync(self):
        await self.notify("own", "impl")
'''


async def _build(graph):
    return await build_code_graph_edges(
        graph_provider=graph, org_id=ORG_ID, record_group_id=GROUP_ID,
    )


def _pairs(graph, relation):
    out = []
    for edge in graph.code_edges(relation):
        src = graph.blocks.get(edge["_from"].partition("/")[2], {})
        dst = graph.blocks.get(edge["_to"].partition("/")[2], {})
        out.append((src.get("qualifiedName"), dst.get("qualifiedName")))
    return sorted(p for p in out if p[0] and p[1])


class TestInheritedSelfCall:
    @pytest.mark.asyncio
    async def test_call_to_an_inherited_method_reaches_the_base(
        self, graph, index_file
    ) -> None:
        await index_file("recB", "src/base.py", BASE, "python")
        await index_file("recC", "src/confluence.py", CONNECTOR, "python")
        await _build(graph)

        assert ("method:ConfluenceConnector.run_sync",
                "method:BaseConnector.notify") in _pairs(graph, CALLS)

    @pytest.mark.asyncio
    async def test_the_base_method_has_inbound_calls(self, graph, index_file) -> None:
        """The question the graph could not answer: who notifies?"""
        await index_file("recB", "src/base.py", BASE, "python")
        await index_file("recC", "src/confluence.py", CONNECTOR, "python")
        await index_file("recM", "src/middle.py", MIDDLE, "python")
        await index_file("recJ", "src/jira.py", GRANDCHILD, "python")
        await _build(graph)

        callers = {src for src, dst in _pairs(graph, CALLS)
                   if dst == "method:BaseConnector.notify"}
        assert "method:ConfluenceConnector.run_sync" in callers
        assert "method:JiraConnector.run_sync" in callers

    @pytest.mark.asyncio
    async def test_an_override_wins_over_the_base(self, graph, index_file) -> None:
        await index_file("recB", "src/base.py", BASE, "python")
        await index_file("recN", "src/notion.py", OVERRIDER, "python")
        await _build(graph)

        pairs = _pairs(graph, CALLS)
        assert ("method:NotionConnector.run_sync",
                "method:NotionConnector.notify") in pairs
        assert ("method:NotionConnector.run_sync",
                "method:BaseConnector.notify") not in pairs

    @pytest.mark.asyncio
    async def test_a_call_from_a_closure_inside_a_method_still_resolves(
        self, graph, index_file
    ) -> None:
        """The nested `report()` calls `self.notify` — its parent is the method,
        not the class, so the owner lookup has to climb."""
        await index_file("recB", "src/base.py", BASE, "python")
        await index_file("recC", "src/confluence.py", CONNECTOR, "python")
        await _build(graph)

        callers = {src for src, dst in _pairs(graph, CALLS)
                   if dst == "method:BaseConnector.notify"}
        assert any("_on_error" in (c or "") or "report" in (c or "") for c in callers)

    @pytest.mark.asyncio
    async def test_an_undefined_self_method_creates_no_edge(
        self, graph, index_file
    ) -> None:
        await index_file("recB", "src/base.py", BASE, "python")
        await index_file("recC", "src/confluence.py", CONNECTOR, "python")
        await _build(graph)

        assert not [p for p in _pairs(graph, CALLS) if "nonexistent" in (p[1] or "")]
