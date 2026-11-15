"""Operator scripts on PostgreSQL: a script starts with no org bound, and the graph runs every statement under one org. The code edge
build script binds the org it is given, and with none lists every org's repos, each read under its own org.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import asyncio
import contextvars
import uuid
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames
from app.core.org_context import OrgContext, bind
from app.modules.code_graph.build_state import BuildStateStore
from app.scripts import build_code_graph_edges as script

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Coroutine

pytestmark = pytest.mark.integration


async def _detached(coro: Coroutine) -> int:
    """As the script's ``asyncio.run`` starts it: an empty context, no org bound."""
    return await asyncio.get_running_loop().create_task(coro, context=contextvars.Context())


@pytest.fixture
async def repos(pg_provider, org_ctx) -> AsyncIterator[tuple[str, str, str]]:
    """Two orgs, each with one repo owed a code edge build."""
    tag = uuid.uuid4().hex[:8]
    a, b = uuid.uuid4().hex[:24], uuid.uuid4().hex[:24]
    for org in (a, b):
        with org_ctx(org):
            await pg_provider.batch_upsert_nodes([{"_key": org, "name": "org", "isActive": True}], CollectionNames.ORGS.value)
            await BuildStateStore(pg_provider).mark_requested(org, f"conn-{org}", f"repo-{org}-{tag}", now_ms=0)
    yield a, b, tag
    for org in (a, b):
        with bind(OrgContext(org, None, "job", "job:test", "t")):
            await pg_provider._db.run(lambda tx, org=org: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org), write=True)


async def test_list_without_an_org_shows_every_orgs_repos(pg_provider, repos, capsys) -> None:
    a, b, tag = repos
    assert await _detached(script._list(pg_provider, None)) == 0
    out = capsys.readouterr().out
    assert f"repo-{a}-{tag}" in out and f"repo-{b}-{tag}" in out


async def test_list_for_one_org_shows_only_its_repos(pg_provider, repos, capsys) -> None:
    a, b, tag = repos
    assert await _detached(script._list(pg_provider, a)) == 0
    out = capsys.readouterr().out
    assert f"repo-{a}-{tag}" in out and f"repo-{b}-{tag}" not in out


async def test_explain_reads_the_repo_under_its_org(pg_provider, repos, capsys) -> None:
    a, _, tag = repos
    assert await _detached(script._explain(pg_provider, a, f"repo-{a}-{tag}")) == 0
    out = capsys.readouterr().out
    assert "No build was ever asked" not in out and "A build now would" in out
