"""A connected app limited to some sources reaches no repository outside them.

The universal agent's sources are every source the user can read, so the
repositories the toolset may open have to be cut to the turn's bounds: a
connected app's limit, a saved agent's or a project's sources.
"""
import pytest

from app.agents.actions.code_graph import code_graph as cg
from app.modules.retrieval.selection_scope import within_connection

from .conftest import ORG, USER, FakeGraphProvider

pytestmark = pytest.mark.asyncio


def _tool(limit: dict) -> cg.CodeGraph:
    return cg.CodeGraph({
        "org_id": ORG, "user_id": USER, "graph_provider": FakeGraphProvider(), "blob_store": object(),
        "apps": ["repo-in", "repo-out"], "kb": [],
        "filters": within_connection({}, {"recordGroups": [], "records": [], **limit}),
    })


async def test_a_repository_outside_the_limit_is_refused() -> None:
    tool = _tool({"apps": ["repo-in"]})

    assert tool._in_scope("repo-in") is None
    assert "error" in (tool._in_scope("repo-out") or {})


async def test_a_limit_with_no_repository_reaches_none() -> None:
    """An empty allow-list means every connector, so a bound that leaves none must refuse."""
    tool = _tool({"apps": ["kb-only"]})

    assert "error" in (tool._in_scope("repo-in") or {})
    assert "error" in (tool._in_scope("repo-out") or {})


async def test_without_a_limit_every_repository_of_the_agent_stays_open() -> None:
    tool = cg.CodeGraph({
        "org_id": ORG, "user_id": USER, "graph_provider": FakeGraphProvider(), "blob_store": object(),
        "apps": ["repo-in", "repo-out"], "kb": [], "filters": {},
    })

    assert tool._in_scope("repo-in") is None and tool._in_scope("repo-out") is None
