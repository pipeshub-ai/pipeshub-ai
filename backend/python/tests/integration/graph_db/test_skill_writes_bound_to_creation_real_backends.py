"""A skill write that passed its ownership check lands only on the skill that was checked.

Each case holds one caller right after `GraphSkillStore` has read the skill and decided the
caller may change it (and, again, at its first graph write); `SkillManager.delete` is also held
right after its first read (the binding) and after the usage check that follows it. While it
waits, the skill is deleted and the name is created again by another user. The held write then
resumes. It must leave the new skill's document and agent edges exactly as they were and must
not report success; a delete must not refuse on, or report, the new skill's usage. Every case runs twice: with the clock as it is, and with the new skill
stamped in the same millisecond as the doc that was read, so a write matched on the
timestamp alone would land.

Runs on Neo4j and ArangoDB (backend-matrix). Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD,
ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from typing import TYPE_CHECKING, Any

import pytest

from app.agent_loop_lib.modules.providers.skills.base import (
    SkillConflictError,
    SkillStatus,
)
from app.agent_loop_lib.modules.providers.skills.manager import SkillManager
from app.agents.agent_loop.skills import graph_store as graph_store_module
from app.config.constants.arangodb import CollectionNames
from app.config.constants.neo4j import collection_to_label
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)
from tests.support.skill_stores import skill_store

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from app.agents.agent_loop.skills.graph_store import GraphSkillStore
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "skill_writes_bound_it"
SKILLS = CollectionNames.AGENT_SKILLS.value
VERSIONS = CollectionNames.AGENT_SKILL_VERSIONS.value
AGENTS = CollectionNames.AGENT_INSTANCES.value
AGENT_HAS_SKILL = CollectionNames.AGENT_HAS_SKILL.value
NAME = "deploy"
logger = logging.getLogger("skill-writes-bound-it")

# Every provider write the store can issue after its ownership read.
_WRITES = frozenset({
    "update_node", "update_node_if_match", "update_node_fields_if_match", "batch_upsert_nodes",
    "delete_nodes_and_edges", "delete_node_if_match", "batch_delete_edges", "delete_edges_from",
    "batch_create_edges",
})


class _Held:
    """The real provider, holding one call until `resume` is set: the first write
    (`at="write"`), or the return of the first read of the skill doc (`at="read"`), which
    is the ownership read."""

    def __init__(self, inner: IGraphDBProvider, at: str) -> None:
        self._inner = inner
        self._at = at
        self.reached = asyncio.Event()
        self.resume = asyncio.Event()
        self._held = False

    async def _hold(self) -> None:
        if not self._held:
            self._held = True
            self.reached.set()
            await self.resume.wait()

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        attr = getattr(self._inner, name)
        if self._at == "write" and name in _WRITES:
            async def write(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
                await self._hold()
                return await attr(*args, **kwargs)
            return write
        if self._at == "read" and name == "get_document":
            async def read(key: str, collection: str, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
                doc = await attr(key, collection, *args, **kwargs)
                if collection == SKILLS:
                    await self._hold()
                return doc
            return read
        return attr


def _md(marker: str) -> str:
    return f"---\nname: {NAME}\ndescription: Use when doing {marker}\n---\n\nBody written by {marker}.\n"


@pytest.fixture(params=["neo4j", "arango"])
async def graph(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[IGraphDBProvider, str]]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            provider = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
                else connect_arango(logger, ARANGO_DB)
            )
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(provider, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)
        org = f"it-org-{uuid.uuid4().hex[:10]}"
        yield provider, org
        if isinstance(provider, Neo4jProvider):
            for collection in (SKILLS, VERSIONS):
                await provider.client.execute_query(
                    f"MATCH (n:{collection_to_label(collection)}) WHERE n.orgId = $o DETACH DELETE n",
                    parameters={"o": org},
                )
            await provider.client.execute_query(
                f"MATCH (n:{collection_to_label(AGENTS)}) WHERE n.id STARTS WITH $o DETACH DELETE n",
                parameters={"o": org},
            )
        else:
            await provider.delete_nodes_and_edges(
                [d["_key"] for d in await provider.get_nodes_by_filters(SKILLS, {"orgId": org})], SKILLS,
            )
            await provider.http_client.execute_aql(
                f"FOR n IN {VERSIONS} FILTER n.orgId == @o REMOVE n IN {VERSIONS}", {"o": org},
            )
            await provider.delete_nodes_and_edges([f"{org}-agent-a", f"{org}-agent-b"], AGENTS)


def _store(provider: Any, org: str, user: str, *, admin: bool = False) -> GraphSkillStore:  # noqa: ANN401
    return skill_store(provider, org, user, visibility_scope=None if admin else user, is_admin=admin)


async def _add_agent(provider: IGraphDBProvider, org: str, agent: str, user: str) -> None:
    now = 1
    assert await provider.batch_upsert_nodes([{
        "_key": f"{org}-{agent}", "name": agent, "description": "d", "startMessage": "s", "systemPrompt": "p",
        "models": [], "createdBy": user, "createdAtTimestamp": now, "updatedAtTimestamp": now, "isDeleted": False,
    }], AGENTS)
    assert await provider.batch_create_edges([{
        "from_id": f"{org}-{agent}", "from_collection": AGENTS,
        "to_id": f"{org}_{NAME}", "to_collection": SKILLS, "createdAtTimestamp": now,
    }], AGENT_HAS_SKILL)


async def _agent_edges(provider: IGraphDBProvider, org: str) -> list[str]:
    edges = await provider.get_edges_to_node(f"{SKILLS}/{org}_{NAME}", AGENT_HAS_SKILL)
    return sorted(graph_store_module.edge_source_key(e) for e in edges)


async def _skill_state(provider: IGraphDBProvider, org: str) -> dict:
    return {
        "doc": await provider.get_document(f"{org}_{NAME}", SKILLS),
        "agents": await _agent_edges(provider, org),
    }


async def _race(
    provider: IGraphDBProvider,
    org: str,
    monkeypatch: pytest.MonkeyPatch,
    held_write: Callable[[GraphSkillStore], Awaitable[Any]],
    *,
    actor: str = "user-a",
    admin: bool = False,
    same_millisecond: bool,
    hold_at: str,
    expected: tuple[type[BaseException], ...] | bool = (SkillConflictError,),
) -> None:
    """A creates the skill (with an agent and a revision), `held_write` runs as `actor` and is
    held after its ownership read; A deletes the skill, B creates the name and attaches it to
    B's agent; the held write resumes. B's skill must be untouched."""
    owner = _store(provider, org, "user-a")
    await owner.create_skill(NAME, _md("owner-a"), resources={"a.md": "from a"})
    await owner.update_skill(NAME, _md("owner-a-v2"))
    await _add_agent(provider, org, "agent-a", "user-a")
    read = await provider.get_document(f"{org}_{NAME}", SKILLS)

    held = _Held(provider, hold_at)
    task = asyncio.create_task(held_write(_store(held, org, actor, admin=admin)))
    await asyncio.wait_for(held.reached.wait(), timeout=30)

    assert await _store(provider, org, "user-a").delete_skill(NAME)
    assert await provider.get_document(f"{org}_{NAME}", SKILLS) is None
    with monkeypatch.context() as clock:
        if same_millisecond:
            clock.setattr(graph_store_module, "get_epoch_timestamp_in_ms", lambda: read["updatedAtTimestamp"])
        await _store(provider, org, "user-b").create_skill(NAME, _md("owner-b"), resources={"b.md": "from b"})
    await _add_agent(provider, org, "agent-b", "user-b")
    before = await _skill_state(provider, org)
    assert before["doc"]["createdBy"] == "user-b" and before["agents"] == [f"{org}-agent-b"]
    if same_millisecond:
        assert before["doc"]["updatedAtTimestamp"] == read["updatedAtTimestamp"]

    held.resume.set()
    outcome = (await asyncio.gather(task, return_exceptions=True))[0]

    assert await _skill_state(provider, org) == before
    if isinstance(expected, bool):
        assert outcome is expected, outcome
    else:
        assert isinstance(outcome, expected), outcome


async def _detach_then_delete(store: GraphSkillStore) -> bool:
    """`SkillManager.delete(detach=True)`: detach, then delete. An admin may moderate the new
    skill too, so only the binding to the doc that was read keeps it."""
    await store.detach_from_agents(NAME)
    return await store.delete_skill(NAME)


# id -> (the held write, actor, admin, outcome). A lost compare-and-swap reports False.
CASES: dict[str, tuple[Callable[[GraphSkillStore], Awaitable[object]], str, bool, tuple[type[BaseException], ...] | bool]] = {
    "write-resource": (lambda s: s.write_resource(NAME, "notes.md", "from a"), "user-a", False, (SkillConflictError,)),
    "remove-resource": (lambda s: s.remove_resource(NAME, "a.md"), "user-a", False, (SkillConflictError,)),
    "overlapping-delete": (lambda s: s.delete_skill(NAME), "user-a", False, (SkillConflictError,)),
    "admin-detach-and-delete": (_detach_then_delete, "admin-c", True, (SkillConflictError,)),
    "admin-status": (lambda s: s.set_skill_status(NAME, SkillStatus.DISABLED), "admin-c", True, (SkillConflictError,)),
    "admin-disable": (
        lambda s: s.set_skill_status(NAME, SkillStatus.DISABLED, from_status=SkillStatus.ACTIVE), "admin-c", True, False,
    ),
    "update": (lambda s: s.update_skill(NAME, _md("owner-a-v3")), "user-a", False, (SkillConflictError,)),
    "rollback": (lambda s: s.rollback(NAME, "1.0.0"), "user-a", False, (SkillConflictError,)),
}


def race_cases(test: Callable) -> Callable:
    """Every case, held after the ownership read and at the first write, with the clock as it
    is and with the new skill stamped in the same millisecond as the doc that was read."""
    test = pytest.mark.parametrize("case", list(CASES))(test)
    test = pytest.mark.parametrize("same_millisecond", [False, True], ids=["clock", "same-ms"])(test)
    return pytest.mark.parametrize("hold_at", ["read", "write"], ids=["after-read", "at-write"])(test)


async def run_case(
    provider: IGraphDBProvider, org: str, monkeypatch: pytest.MonkeyPatch, case: str, *, same_millisecond: bool, hold_at: str,
) -> None:
    write, actor, admin, outcome = CASES[case]
    await _race(provider, org, monkeypatch, write, actor=actor, admin=admin,
                same_millisecond=same_millisecond, hold_at=hold_at, expected=outcome)


@race_cases
async def test_a_held_write_leaves_the_skill_created_again_alone(graph, monkeypatch, case, same_millisecond, hold_at) -> None:
    provider, org = graph
    await run_case(provider, org, monkeypatch, case, same_millisecond=same_millisecond, hold_at=hold_at)


class _HeldManagerStore:
    """The caller's store, holding `SkillManager.delete` right after its first read returns:
    the binding (`at="binding"`), or the usage check that follows it (`at="usage"`)."""

    def __init__(self, inner: GraphSkillStore, at: str) -> None:
        self._inner = inner
        self._at = at
        self._held = False
        self.reached = asyncio.Event()
        self.resume = asyncio.Event()

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        return getattr(self._inner, name)

    async def _hold(self, step: str) -> None:
        if step == self._at and not self._held:
            self._held = True
            self.reached.set()
            await self.resume.wait()

    async def deletion_binding(self, name: str) -> Any:  # noqa: ANN401
        binding = await self._inner.deletion_binding(name)
        await self._hold("binding")
        return binding

    async def get_referential_usage(self, name: str) -> Any:  # noqa: ANN401
        usage = await self._inner.get_referential_usage(name)
        await self._hold("usage")
        return usage


class _NoIndex:
    async def remove_entry(self, name: str) -> None:
        return None


def manager_delete_cases(test: Callable) -> Callable:
    """The owner and an admin, with and without `detach`, held after the binding or after the usage check."""
    test = pytest.mark.parametrize("detach", [False, True], ids=["delete", "delete-detach"])(test)
    test = pytest.mark.parametrize("admin", [False, True], ids=["owner", "admin"])(test)
    return pytest.mark.parametrize("hold_at", ["binding", "usage"], ids=["after-binding", "after-usage"])(test)


async def run_manager_delete(
    provider: IGraphDBProvider, org: str, *, admin: bool, detach: bool, hold_at: str = "usage",
) -> None:
    """`SkillManager.delete` is held after its binding or its usage check; A deletes the skill
    and B creates the name and attaches its agent; the delete resumes. It is a conflict, never
    a refusal that reports B's agents, and B's skill is untouched."""
    owner = _store(provider, org, "user-a")
    await owner.create_skill(NAME, _md("owner-a"), resources={"a.md": "from a"})
    if detach:
        await _add_agent(provider, org, "agent-a", "user-a")
    held = _HeldManagerStore(_store(provider, org, "admin-c" if admin else "user-a", admin=admin), hold_at)
    manager = SkillManager(store=held, index=_NoIndex(), tracker=None)  # type: ignore[arg-type]
    task = asyncio.create_task(manager.delete(NAME, detach=detach))
    await asyncio.wait_for(held.reached.wait(), timeout=30)

    assert await _store(provider, org, "user-a").delete_skill(NAME)
    await _store(provider, org, "user-b").create_skill(NAME, _md("owner-b"), resources={"b.md": "from b"})
    await _add_agent(provider, org, "agent-b", "user-b")
    before = await _skill_state(provider, org)
    assert before["doc"]["createdBy"] == "user-b" and before["agents"] == [f"{org}-agent-b"]

    held.resume.set()
    outcome = (await asyncio.gather(task, return_exceptions=True))[0]
    assert await _skill_state(provider, org) == before
    assert isinstance(outcome, SkillConflictError), outcome


@manager_delete_cases
async def test_a_manager_delete_binds_to_the_read_before_its_usage_check(graph, admin, detach, hold_at) -> None:
    provider, org = graph
    await run_manager_delete(provider, org, admin=admin, detach=detach, hold_at=hold_at)


async def run_stale_binding_delete(provider: IGraphDBProvider, org: str, *, admin: bool) -> None:
    """The store compares its own read with the binding it is given, whoever calls it:
    a binding taken before the name was deleted and created again is a conflict."""
    await _store(provider, org, "user-a").create_skill(NAME, _md("owner-a"))
    caller = _store(provider, org, "admin-c" if admin else "user-a", admin=admin)
    binding = await caller.deletion_binding(NAME)
    assert await _store(provider, org, "user-a").delete_skill(NAME)
    await _store(provider, org, "user-b").create_skill(NAME, _md("owner-b"))
    await _add_agent(provider, org, "agent-b", "user-b")
    before = await _skill_state(provider, org)
    with pytest.raises(SkillConflictError) as exc:
        await caller.delete_skill(NAME, expected=binding)
    assert exc.value.current_version == "" and exc.value.current_updated_at == 0
    assert await _skill_state(provider, org) == before


@pytest.mark.parametrize("admin", [False, True], ids=["owner", "admin"])
async def test_a_store_delete_with_a_stale_binding_is_a_conflict(graph, admin) -> None:
    provider, org = graph
    await run_stale_binding_delete(provider, org, admin=admin)
