"""The Jira issue security worlds of ``test_jira_issue_security_real_backends`` on the PostgreSQL graph provider: the same
connector build, processor writes and verdicts as on Neo4j and ArangoDB. Needs PG_CORE_URL (see conftest.py)."""
from __future__ import annotations

import functools
import inspect
import secrets
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from app.core.org_context import OrgContext, bind
from app.services.graph_db.postgres.kg.drainer import drain_org
from tests.integration.graph_db import test_jira_issue_security_real_backends as it

from .test_kg_read import flip

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]


@pytest.fixture
async def world(pg_provider, pg_db, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict[str, Any]]:
    run, org_id = uuid.uuid4().hex[:10], secrets.token_hex(12)
    copied: list[bool] = []

    def listed_from_kg(read: Callable[..., Any]) -> Callable[..., Any]:
        """The Knowledge Hub lists from the typed copy of the graph: copy the connector once, then bring in what was written since."""
        async def go(*args: Any) -> Any:  # noqa: ANN401
            if copied:
                await drain_org(pg_db, org_id)
            else:
                assert (await flip(pg_db, org_id))[0] == 1, "the connector was not copied into the typed graph"
                copied.append(True)
            with bind(OrgContext(org_id, None, "job", "job:test", "t")):
                return await read(*args)
        return go
    async def access(world: dict[str, Any], name: str, ids: list[str]) -> tuple[set[str], set[str], set[str]]:
        # shortcut: PostgreSQL has no flattened listing yet (get_hidden_app_ids), so what a user may browse to is read by opening
        # every row from the App down; use the flattened listing once PostgreSQL serves it.
        graph, user_id = world["graph"], world["user_ids"][name]
        checked = set(await graph.filter_accessible_record_ids(ids, user_id, org_id))
        searched = await graph.filter_accessible_virtual_record_ids([f"vr-{i}" for i in ids], user_id, org_id)
        listed = {row for _, row, _ in await it._browse(world, name)}
        return checked, set(searched.values()) & set(ids), listed & set(ids)
    monkeypatch.setattr(it, "_listed_under", listed_from_kg(it._listed_under))
    monkeypatch.setattr(it, "_access", listed_from_kg(access))
    with bind(OrgContext(org_id, None, "job", "job:test", "t")):
        yield await it.new_world(pg_provider, run, org_id, f"jira-{run}", [])
    with bind(OrgContext(org_id, None, "job", "job:test", "t")):
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.edges WHERE org_id = $1", org_id), write=True)
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org_id), write=True)


def _in_org(test: Callable[..., Any]) -> Callable[..., Any]:
    """The test runs in its world's org, as a sync does (the fixture's context does not reach the test)."""
    @functools.wraps(test)
    async def run(*args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        with bind(OrgContext(kwargs["world"]["org_id"], None, "job", "job:test", "t")):
            await test(*args, **kwargs)
    return run


for _name, _test in inspect.getmembers(it, inspect.iscoroutinefunction):
    if _name.startswith("test_"):
        globals()[_name] = _in_org(_test)
