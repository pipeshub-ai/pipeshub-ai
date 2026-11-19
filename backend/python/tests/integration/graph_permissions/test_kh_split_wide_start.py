"""A split browse of a start with more children than it reads (`Neo4jProvider._KH_SPLIT_MAX_CHILDREN`) is answered by legacy.

The split browse checks every child of the start it lists. Past the bound it hands the request to the legacy statement, whose
rules decide in the database and whose check is windowed with a budget and a resume cursor, so a very wide folder is listed
exactly as before (docs/knowledge-hub-browse.md).
"""

from __future__ import annotations

import pytest

from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

from .test_provider_v3_arrow_matches_expansion import (  # noqa: F401
    _browse,
    _mode,
    arrows_graph,
    provider,
)

pytestmark = pytest.mark.integration


# The split browse is Neo4j's, so only that store runs: a skipped case fails the conformance shard.
@pytest.mark.parametrize("provider", ["neo4j"], indirect=True)
async def test_a_start_wider_than_the_bound_is_listed_by_legacy(arrows_graph, provider, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setenv("KH_BROWSE_MODE", "legacy")
    legacy = await _browse(provider, "ae-open", "ae-og", "recordGroup")
    assert len(legacy) == 2      # the premise: the start has two children

    monkeypatch.setenv("KH_BROWSE_MODE", "split")
    monkeypatch.setattr(Neo4jProvider, "_KH_SPLIT_MAX_CHILDREN", 1)
    assert await _browse(provider, "ae-open", "ae-og", "recordGroup") == legacy

    # Within the bound the split browse answers itself: its arrow follows what opening the row lists (see that module).
    monkeypatch.setattr(Neo4jProvider, "_KH_SPLIT_MAX_CHILDREN", 5_000)
    split = {r["id"]: r["hasChildren"] for r in await _browse(provider, "ae-open", "ae-og", "recordGroup")}
    assert split == {"ae-of1": False, "ae-of2": False}
