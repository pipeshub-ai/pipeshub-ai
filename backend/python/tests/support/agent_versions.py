"""Agent versions (OWD-14) for tests whose graph provider is a mock.

Those tests answer ``get_agent`` and friends from mocks and know nothing of the ``agentVersions``
collection. ``versions_beside`` serves ``AgentVersionStore`` from a collection of its own instead, so
the real store runs (an agent from before versions gets version 1 on its first read, a save
publishes) whatever the mock answers for other documents.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.agents.versions import AgentVersionStore
from tests.support.agent_routes import InMemoryGraph

if TYPE_CHECKING:
    import pytest


def versions_beside(monkeypatch: pytest.MonkeyPatch, *modules: str) -> InMemoryGraph:
    """Each of ``modules`` builds its ``AgentVersionStore`` over one shared in-memory collection."""
    graph = InMemoryGraph()
    for module in modules:
        monkeypatch.setattr(f"{module}.AgentVersionStore", lambda _graph: AgentVersionStore(graph))
    return graph
