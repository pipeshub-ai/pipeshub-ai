"""A split browse of a start with more children than it holds whole (`Neo4jProvider._KH_SPLIT_START_MAX`) reads them in windows.

The split browse decides a start's children before its page. Past the bound it reads them with the page instead, in the page's order from
its boundary a window at a time, each window asked of the check (`_kh_v3_browse_undecided`), so a very wide folder pages in bounded time
and memory and lists exactly what the legacy statement lists (docs/knowledge-hub-browse.md).
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
async def test_a_start_wider_than_the_bound_is_listed_by_split_in_windows(arrows_graph, provider, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setenv("KH_BROWSE_MODE", "split")
    # Held whole, the split browse's arrow follows what opening the row lists (see that module), where legacy's does not.
    whole = await _browse(provider, "ae-open", "ae-og", "recordGroup")
    assert {r["id"]: r["hasChildren"] for r in whole} == {"ae-of1": False, "ae-of2": False}

    async def no_legacy(*_a: object, **_k: object) -> None:
        raise AssertionError("a wide start was handed to legacy")

    monkeypatch.setattr(Neo4jProvider, "_KH_SPLIT_START_MAX", 1)
    monkeypatch.setattr(provider, "_kh_split_answered_by_legacy", no_legacy)
    assert await _browse(provider, "ae-open", "ae-og", "recordGroup") == whole
