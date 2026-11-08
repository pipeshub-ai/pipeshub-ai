"""The pending registry without a graph store: the stored value, what a transaction collects, what the bump writes, the currency rule, the
start-up check, what a finished back-fill takes off the list, and that a registered root only ever narrows the filter.

The same registry on Neo4j and ArangoDB is in `test_pending_registry_real_graph.py`.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

from app.connectors.core.base.data_store.graph_data_store import StructuralMarks
from app.services.permissions import wiring
from app.services.permissions.currency import app_is_current
from app.services.permissions.errors import ViewerScopesUnavailable
from app.services.permissions.fakes import InMemoryHierarchyView
from app.services.permissions.mode import ENV_SCOPE_FIELDS
from app.services.permissions.pending import (
    MAX_PENDING_ROOTS,
    PendingOverflow,
    PendingUnreadable,
    dump,
    parse,
    require_filter_terms,
    with_roots,
    without_done,
)
from app.services.permissions.scope_fields import (
    SCOPE_FIELDS_VERSION,
    current_scope_generation,
)
from app.services.permissions.structure_generation import (
    GENERATION_KEY,
    PENDING_KEY,
    ScopeGenerationError,
    bump_generation_in,
    clear_pending_covered,
    register_pending_roots,
)
from app.services.vector_db import filters
from app.services.vector_db.scope_backfill import BACKFILL_KEYS as K
from app.services.vector_db.scope_backfill import DONE

from .scope_layer import OracleScopeLayer
from .stamp_world import ORG
from .test_scope_backfill import COLL, WORLD, setup  # noqa: F401
from .test_scopes_reference_filter import WORLDS, path_of, reference_filter


@pytest.fixture(autouse=True)
def flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_SCOPE_FIELDS, "on")


# ---- the stored value ----
def test_the_value_round_trips_and_a_value_that_does_not_parse_is_never_read_as_no_roots() -> None:
    assert parse(dump({"b": 2, "a": 1})) == {"a": 1, "b": 2} and dump({}) is None
    assert parse(None) == {} and parse("") == {}
    for bad in ("not json", "[1]", '{"a": "x"}', '{"a": null}', 5, b"{}"):
        with pytest.raises(PendingUnreadable):
            parse(bad)


def test_a_root_registered_again_takes_the_newer_generation_and_a_pass_removes_only_what_it_covered() -> None:
    held = with_roots(with_roots(None, ["a", "b"], 5), ["b"], 9)
    assert parse(held) == {"a": 5, "b": 9}
    assert parse(without_done(held, {"a": 5, "b": 5})) == {"b": 9}
    assert without_done(held, {"a": 5, "b": 9}) is None
    assert parse(without_done(held, {"c": 99})) == {"a": 5, "b": 9}
    with pytest.raises(PendingUnreadable):
        with_roots("garbage", ["a"], 1)


def test_more_than_the_cap_is_an_overflow_and_the_cap_itself_is_not() -> None:
    with pytest.raises(PendingOverflow):
        with_roots(None, [f"r{i}" for i in range(MAX_PENDING_ROOTS + 1)], 1)
    assert len(parse(with_roots(None, [f"r{i}" for i in range(MAX_PENDING_ROOTS)], 1))) == MAX_PENDING_ROOTS


def test_a_transaction_that_only_changed_places_reports_roots_and_any_other_write_makes_it_a_whole_connector_change() -> None:
    marks = StructuralMarks()
    marks.add("c1", "r1")
    marks.add("c1", "r2")
    marks.add("c2", "r9")
    marks.add("c2")                         # a delete or a group: no node says what it reached
    marks.add("c3", "r3")
    marks.update(["c3"])                    # `set.update` must not bypass the whole-connector rule
    assert marks == {"c1", "c2", "c3"}
    assert marks.subtree_roots("c1") == {"r1", "r2"}
    assert marks.subtree_roots("c2") is None and marks.subtree_roots("c3") is None and marks.subtree_roots("c4") is None
    marks.clear()
    assert not marks and marks.subtree_roots("c1") is None


# ---- the bump ----
class AppGraph:
    """The app document of one connector, with the write lock `increment_node_field` takes and a log of what was written."""

    def __init__(self, app: dict | None) -> None:
        self.app, self.writes, self.txn = app, [], "t1"
        self.graph_provider = self

    def is_write_conflict(self, exc: BaseException) -> bool:
        return False

    async def increment_node_field(self, key: str, collection: str, field: str, transaction: str | None = None) -> int | None:
        if self.app is None:
            return None
        self.app[field] = int(self.app.get(field) or 0) + 1
        return self.app[field]

    async def get_document(self, key: str, collection: str, **kw: object) -> dict | None:
        return None if self.app is None else dict(self.app)

    async def update_node(self, key: str, collection: str, updates: dict, transaction: str | None = None) -> bool:
        self.writes.append(dict(updates))
        self.app.update(updates)
        return True

    async def update_node_fields_if_match(self, key: str, collection: str, updates: dict, expected: dict, transaction: str | None = None) -> bool:
        if any((self.app.get(f) or None) != v for f, v in expected.items()):
            return False
        self.app.update(updates)
        return True


async def test_the_bump_registers_the_roots_beside_the_generation() -> None:
    tx = AppGraph({GENERATION_KEY: 3, PENDING_KEY: dump({"old": 2})})
    g = await bump_generation_in(tx, "c1", ["a"])
    assert g == 4 and parse(tx.app[PENDING_KEY]) == {"old": 2, "a": 4}


@pytest.mark.parametrize("roots", [None, [], [f"r{i}" for i in range(MAX_PENDING_ROOTS + 1)]])
async def test_a_change_with_no_roots_or_too_many_bumps_and_registers_nothing(roots: list[str] | None) -> None:
    tx = AppGraph({GENERATION_KEY: 3, PENDING_KEY: dump({"old": 2})})
    assert await bump_generation_in(tx, "c1", roots) == 4
    assert tx.writes == [] and parse(tx.app[PENDING_KEY]) == {"old": 2}


async def test_a_stored_value_that_does_not_parse_is_left_as_it_is_and_the_connector_stays_not_current() -> None:
    tx = AppGraph({GENERATION_KEY: 3, PENDING_KEY: "garbage"})
    assert await bump_generation_in(tx, "c1", ["a"]) == 4
    assert tx.writes == [] and tx.app[PENDING_KEY] == "garbage"
    assert not app_is_current({**tx.app, **_finished(4)})


async def test_a_connector_that_does_not_exist_is_an_error_not_a_registration() -> None:
    with pytest.raises(ScopeGenerationError):
        await bump_generation_in(AppGraph(None), "gone", ["a"])
    with pytest.raises(ScopeGenerationError):
        await register_pending_roots(AppGraph(None), "gone", ["a"])


async def test_registering_outside_a_transaction_retries_a_lost_compare_and_set_and_loses_no_root() -> None:
    g = AppGraph({GENERATION_KEY: 1})
    real = g.update_node_fields_if_match
    raced = {"n": 0}

    async def other_writer_first(key, collection, updates, expected, transaction=None):
        if not raced["n"]:
            raced["n"] = 1
            g.app[PENDING_KEY] = dump({"theirs": 2})
        return await real(key, collection, updates, expected)

    g.update_node_fields_if_match = other_writer_first
    await register_pending_roots(g, "c1", ["mine"])
    assert set(parse(g.app[PENDING_KEY])) == {"mine", "theirs"}


# ---- currency ----
def _finished(structure: int) -> dict:
    return {K["state"]: DONE, K["version"]: SCOPE_FIELDS_VERSION, K["generation"]: current_scope_generation(), K["structure_generation"]: structure}


def test_a_corrupted_pending_value_reads_as_not_current_and_a_readable_one_does_not_change_currency() -> None:
    app = {GENERATION_KEY: 4, **_finished(4)}
    assert app_is_current(app)
    assert app_is_current({**app, PENDING_KEY: dump({"r": 3})})
    for bad in ("garbage", "[]", '{"r": "x"}', 7):
        assert not app_is_current({**app, PENDING_KEY: bad})


# ---- start-up check ----
def test_the_registry_refuses_to_start_without_the_pending_and_blocked_terms(monkeypatch: pytest.MonkeyPatch) -> None:
    require_filter_terms()
    names = {f.name for f in dataclasses.fields(filters.ScopeFilter)}
    assert {"pending", "blocked"} <= names
    for drop in ("pending", "blocked"):
        fake = dataclasses.make_dataclass("ScopeFilter", [(n, frozenset) for n in names - {drop}], frozen=True)
        monkeypatch.setattr(filters, "ScopeFilter", fake)
        with pytest.raises(RuntimeError, match=drop):
            require_filter_terms()


async def test_a_service_start_raises_when_the_terms_are_missing_and_only_while_the_fields_are_on(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = dataclasses.make_dataclass("ScopeFilter", [("current", frozenset)], frozen=True)
    monkeypatch.setattr(filters, "ScopeFilter", fake)
    with pytest.raises(RuntimeError):
        await wiring.register_scope_generation(SimpleNamespace(), SimpleNamespace())  # type: ignore[arg-type]
    monkeypatch.delenv(ENV_SCOPE_FIELDS)
    monkeypatch.setenv("ACL_FILTER_MODE", "legacy")
    from unittest.mock import AsyncMock, MagicMock
    config = MagicMock(get_config=AsyncMock(return_value=None))
    await wiring.register_scope_generation(config, MagicMock())     # off: no check, nothing raised


# ---- the registry feeds the viewer's pending scopes ----
async def test_the_registered_roots_are_the_pending_scopes_of_the_viewer() -> None:
    from app.services.permissions.fakes import InMemoryPrincipalView
    from app.services.permissions.ports import AppFacts, Principal
    from app.services.permissions.viewer import ViewerScopeResolver

    tx = AppGraph({GENERATION_KEY: 0})
    await bump_generation_in(tx, "c1", ["a", "b"])

    class RegistryView(InMemoryHierarchyView):
        async def pending_scopes(self, org_id: str) -> frozenset[str]:
            return frozenset(parse(tx.app.get(PENDING_KEY)))

    view = RegistryView(ORG, groups_by_app={"c1": frozenset({"g"})}, facts_by_app={"c1": AppFacts(False, "CONFLUENCE")})
    who = {(ORG, "u"): Principal(frozenset(), frozenset({"c1"}))}
    resolver = ViewerScopeResolver(view, InMemoryPrincipalView(who, {(ORG, "u"): frozenset({"g"})}))
    assert (await resolver.resolve(ORG, "u")).pending == {"a", "b"}
    tx.app[PENDING_KEY] = "garbage"
    with pytest.raises(ViewerScopesUnavailable):
        # the real views refuse an unreadable list; the fake mirrors them by raising from parse
        try:
            await resolver.resolve(ORG, "u")
        except PendingUnreadable as exc:
            raise ViewerScopesUnavailable("pending_unreadable") from exc


# ---- what a finished full pass takes off the list (recovery) ----
async def test_a_finished_pass_takes_off_the_roots_it_covered_and_the_connector_is_current_with_nothing_hidden() -> None:
    vdb, loaded, job, app = await setup()
    loaded.graph.app_docs[app] = {"_key": app, GENERATION_KEY: 5, PENDING_KEY: dump({"r1": 2, "r2": 5})}
    assert not app_is_current(loaded.graph.app_docs[app])
    p = await job.run_connector(ORG, app)
    assert p.state == DONE and p.structure_generation == 5
    assert PENDING_KEY not in loaded.graph.app_docs[app] or not loaded.graph.app_docs[app][PENDING_KEY]
    assert app_is_current(loaded.graph.app_docs[app])


async def test_a_root_registered_again_while_the_pass_runs_stays_and_the_connector_is_not_current() -> None:
    vdb, loaded, job, app = await setup(page_size=3)
    loaded.graph.app_docs[app] = {"_key": app, GENERATION_KEY: 5, PENDING_KEY: dump({"r1": 5})}
    real_nodes, once = loaded.graph.stamps.nodes, {"done": False}

    async def register_during_the_pass(org: str, ids: frozenset[str]) -> dict:
        if not once["done"]:
            once["done"] = True
            loaded.graph.app_docs[app] = {**loaded.graph.app_docs[app], GENERATION_KEY: 6, PENDING_KEY: dump({"r1": 5, "r3": 6})}
        return await real_nodes(org, ids)

    loaded.graph.stamps.nodes = register_during_the_pass  # type: ignore[method-assign]
    p = await job.run_connector(ORG, app, max_pages=1000)
    assert p.state == DONE and p.structure_generation == 5
    assert parse(loaded.graph.app_docs[app][PENDING_KEY]) == {"r3": 6}          # r1 was covered, r3 came after the pass read its generation
    assert not app_is_current(loaded.graph.app_docs[app])


async def test_a_pass_that_has_not_finished_takes_nothing_off_the_list() -> None:
    vdb, loaded, job, app = await setup(page_size=3)
    loaded.graph.app_docs[app] = {"_key": app, GENERATION_KEY: 5, PENDING_KEY: dump({"r1": 2})}
    p = await job.run_connector(ORG, app, max_pages=1)
    assert p.state != DONE and parse(loaded.graph.app_docs[app][PENDING_KEY]) == {"r1": 2}


async def test_a_value_that_does_not_parse_is_dropped_by_a_pass_only_while_nothing_can_have_been_registered_since() -> None:
    g = AppGraph({GENERATION_KEY: 5, PENDING_KEY: "garbage"})
    await clear_pending_covered(g, "c1", 4)
    assert g.app[PENDING_KEY] == "garbage"               # the generation moved past the pass: a registration may be in it
    await clear_pending_covered(g, "c1", 5)
    assert not g.app[PENDING_KEY]


async def test_a_pass_with_nothing_registered_writes_nothing() -> None:
    g = AppGraph({GENERATION_KEY: 5})
    await clear_pending_covered(g, "c1", 5)
    assert g.writes == [] and PENDING_KEY not in g.app


# ---- no extra access, no missing access ----
PATHED = [i for i, w in sorted(WORLDS.items()) if any(n.stamp in ("union", "intersection") and n.kind == "record" for n in w.nodes)][:10]


@pytest.mark.parametrize("scenario_id", PATHED)
async def test_a_pending_root_hides_what_is_below_it_and_nothing_else_and_clearing_it_brings_everything_back(scenario_id: str) -> None:
    world, layer = WORLDS[scenario_id], OracleScopeLayer()
    records = [n.id for n in world.nodes if n.kind == "record" and not n.deleted and not n.placeholder and n.stamp in ("union", "intersection")]
    seen = 0
    for viewer in world.users():
        scopes = await layer.viewer_scopes(world, viewer)
        base = {r: reference_filter(world, scopes, r) for r in records}
        for root in records[:5]:
            tx = AppGraph({GENERATION_KEY: 0})
            await bump_generation_in(tx, "c", [root])
            hidden = dataclasses.replace(scopes, pending=frozenset(parse(tx.app[PENDING_KEY])))
            for r in records:
                got = reference_filter(world, hidden, r)
                assert not (got and not base[r]), f"{scenario_id}/{viewer}: {r} admitted with {root} pending, and not without"       # no extra access
                if root not in path_of(world, r):
                    assert got == base[r], f"{scenario_id}/{viewer}: {r} lost with {root} pending, which is not on its path"      # no missing access
                seen += 1
            await clear_pending_covered(tx, "c", 1)
            cleared = dataclasses.replace(scopes, pending=frozenset(parse(tx.app.get(PENDING_KEY))))
            assert {r: reference_filter(world, cleared, r) for r in records} == base                                              # recovery
    assert seen, "the world has no stamped record to hide"


# ---- a refused subtree read never lifts the hide ----
async def test_a_chain_deeper_than_the_subtree_bound_keeps_its_root_pending() -> None:
    from app.services.permissions.fakes import InMemoryStampGraph
    from app.services.permissions.ports import SUBTREE_DEPTH, StampNode

    nodes = {f"n{i}": StampNode(f"n{i}", "record", "c1", parents=(f"n{i - 1}",) if i else (), vrid=f"v{i}") for i in range(SUBTREE_DEPTH + 5)}
    graph = InMemoryStampGraph(ORG, node_map=nodes)
    with pytest.raises(ViewerScopesUnavailable, match="subtree_too_deep"):
        await graph.subtree_vrids(ORG, ["n0"])
    tx = AppGraph({GENERATION_KEY: 0})
    await bump_generation_in(tx, "c1", ["n0"])
    await clear_pending_covered(tx, "c1", 0)               # a pass from before the registration covers nothing registered after it
    assert parse(tx.app[PENDING_KEY]) == {"n0": 1}
