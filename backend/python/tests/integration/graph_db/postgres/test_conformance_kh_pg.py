"""The Knowledge Hub layer (``tests/permissions/kh_layer.py``) on the PostgreSQL provider: the rows, totals, counts, facets and arrows of every golden, adversarial and generated world
against the access oracle, with the world's connectors served from kg ("current") and with kg not trusted for them ("not current": the listing's access is then the live walk's).
The two states must answer the same rows and arrows; the totals are exact in both for the worlds of these sizes."""
from __future__ import annotations

import logging
import os
import zlib
from typing import TYPE_CHECKING

import asyncpg
import pytest

from app.connectors.sources.localKB.handlers.kh_merge import key_for
from app.core.org_context import OrgContext, bind
from app.exceptions.graph_db_exceptions import KnowledgeHubListingNotReady
from tests.permissions import gen
from tests.permissions.graph import Namespace
from tests.permissions.kh_layer import KhFinding, check_world, listing
from tests.permissions.kh_worlds import KH_WORLDS
from tests.permissions.scenario import load_all

from .test_conformance import PgLayer
from .test_kg_read import KgLayer, _admin, _hex, flip

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(3600)]

CHUNK = 25
GENERATED_WORLDS = 12      # per generator and state on a pull request (the not-current state asks the walk about every row); a nightly run sets PERM_GEN_WORLDS
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)


class _Ns(Namespace):
    """The store's org of a world is the hash of the namespace's (``PgLayer``), and the listing asks for it by that."""

    @property
    def org(self) -> str:      # type: ignore[override]
        return _hex(self._org)

    @org.setter
    def org(self, value: str) -> None:
        self._org = value


# Prefixes put in front of every name of a world: mixed case, accents, a character in U+E000..U+FFFF and one above U+FFFF (a UTF-16 surrogate pair). In UTF-16 code-unit order (what the listing is
# merged and paged in) the last sorts before U+E000..U+FFFF; by code point it sorts after them.
PREFIXES = ["", "É", "é", "Z", "z", "\uE000", "\uFFFD", "\U00010000", "\U0001F600", "a"]


async def _decorate_names(org: str) -> None:
    """Give the names of a stored world those prefixes (chosen by the node's id, so every run of a world is the same), before it is copied into kg."""
    con = await asyncpg.connect(os.environ["PG_ADMIN_URL"])
    try:
        for coll, key in (("records", "recordName"), ("recordGroups", "groupName")):
            rows = await con.fetch("SELECT id, doc->>$3 AS name FROM graph.nodes WHERE org_id = $1 AND coll = $2 AND doc->>$3 IS NOT NULL", org, coll, key)
            ids = [r["id"] for r in rows]
            names = [PREFIXES[zlib.crc32(r["id"].encode()) % len(PREFIXES)] + r["name"] for r in rows]
            await con.execute("UPDATE graph.nodes n SET doc = jsonb_set(n.doc, ARRAY[$3], to_jsonb(x.name)) FROM unnest($4::text[], $5::text[]) AS x(id, name) WHERE n.org_id = $1 AND n.coll = $2 AND n.id = x.id",
                              org, coll, key, ids, names)
    finally:
        await con.close()


class KhPgLayer(KgLayer):
    """``KgLayer`` whose worlds are listed through the product's own listing; ``not_current`` takes kg off every connector of the world after it was copied and verified."""

    def __init__(self, provider, db, *, not_current: bool) -> None:
        super().__init__(provider, db)
        self.not_current = not_current
        self.not_ready = 0
        self.name = f"postgres-kg-{'walk' if not_current else 'kg'}"

    async def preload(self, worlds) -> None:
        new = [w for w in dict.fromkeys(worlds) if w not in self._prefix]
        await PgLayer.preload(self, new)
        for w in new:
            org = _hex(Namespace(self._prefix[w]).org)
            await _decorate_names(org)
            done, total = await flip(self._db, org)
            self.promoted.update(promoted=done, connectors=total)
        if self.not_current:
            for w in new:
                await _admin("UPDATE kg.projection_state SET mode = 'shadow' WHERE org_id = $1 AND app_sk <> 0", _hex(Namespace(self._prefix[w]).org))

    async def _ns(self, world) -> Namespace:
        await super()._ns(world)
        return _Ns(self._prefix[world])


@pytest.fixture(params=[False, True], ids=["current", "not_current"])
async def kh_layer(request, pg_provider, pg_db) -> AsyncIterator[KhPgLayer]:
    lay = KhPgLayer(pg_provider, pg_db, not_current=request.param)
    yield lay
    await lay.release(list(lay._prefix))


# `browse_gap`: the layer fails a folder the browse does not open or leaves nodes out of. This provider lists a whole connector only (browsing a node is the next step of the series), so every browse is a
# gap here; the flat listing is held to the oracle in full (over, under, total, counts, facets, order). Take `browse_gap` out of this set when the browse is served.
GAPS = {"gap", "browse_gap"}


async def check(layer: KhPgLayer, world, scenario: str) -> list[KhFinding]:
    """``check_world`` as the product runs a request: under the world's own org."""
    with bind(OrgContext((await layer._ns(world)).org, None, "job", "job:test", "t")):
        try:
            found = await check_world(layer, world, scenario)
            for viewer in world.users():      # the order of the whole listing is the merge's (a string by UTF-16 code unit): what pages and cursors are cut by
                keys = [key_for(r, descending=False) for r in (await listing(layer, world, viewer)).rows]
                if keys != sorted(keys):
                    found.append(KhFinding(scenario, viewer, "order", f"rows out of the merge's order: {[k.value for k in keys][:8]}"))
            return found
        except KnowledgeHubListingNotReady:      # a world the projection refuses (a value the typed graph has no column for) is never copied: the listing says so, and the layer counts it
            layer.not_ready += 1
            return []


def _describe(found: list[KhFinding]) -> str:
    return "\n".join(f"{f.kind} {f.scenario} {f.viewer}: {f.detail}" for f in found[:40])


def _bad(found: list[KhFinding]) -> list[KhFinding]:
    return [f for f in found if f.kind not in GAPS]


async def test_golden_and_adversarial_worlds_list_what_the_viewer_may_read(kh_layer) -> None:
    worlds = [*dict.fromkeys(s.final_world() for s in load_all()), *KH_WORLDS]
    bad: list[KhFinding] = []
    for start in range(0, len(worlds), CHUNK):
        chunk = worlds[start:start + CHUNK]
        await kh_layer.preload(chunk)
        for offset, world in enumerate(chunk):
            bad += _bad(await check(kh_layer, world, f"golden-{start + offset}"))
        await kh_layer.release(chunk)
    assert not bad, f"{kh_layer.name}\n{_describe(bad)}"
    print(f"{kh_layer.name}: {len(worlds)} golden worlds, {kh_layer.not_ready} refused as not copied, promoted {dict(kh_layer.promoted)}")
    assert kh_layer.not_ready * 10 <= len(worlds), f"{kh_layer.not_ready} of {len(worlds)} worlds were refused as not copied"


@pytest.mark.parametrize("kind", sorted(gen.GENERATORS))
async def test_generated_worlds_list_what_the_viewer_may_read(kh_layer, kind: str, monkeypatch) -> None:
    # The typed graph takes the three access rules a connector writes; the adversarial generator also writes INHERITED, which no connector does and which the projection refuses (the connector is then never
    # copied and the listing refuses it). The worlds are generated without it, so that every adversarial shape is listed.
    from tests.permissions.gen import adversarial
    monkeypatch.setattr(adversarial, "RULES", [r for r in adversarial.RULES if r != "INHERITED"])
    for seed in gen.seeds():
        worlds = gen.generate(kind, seed, int(os.environ.get("PERM_GEN_WORLDS", GENERATED_WORLDS)))
        bad: list[KhFinding] = []
        for start in range(0, len(worlds), CHUNK):
            chunk = worlds[start:start + CHUNK]
            await kh_layer.preload(chunk)
            for offset, world in enumerate(chunk):
                bad += _bad(await check(kh_layer, world, f"{kind}-{seed}-{start + offset}"))
            await kh_layer.release(chunk)
        print(f"{kh_layer.name} {kind} seed {seed}: {len(worlds)} worlds, {kh_layer.not_ready} refused as not copied so far")
        assert not bad, f"{kh_layer.name} {kind} seed {seed}\n{_describe(bad)}"
        assert kh_layer.not_ready * 10 <= len(worlds), f"{kh_layer.not_ready} of {len(worlds)} {kind} worlds were refused as not copied"


async def test_a_connector_with_no_copy_is_refused_not_listed_as_empty(pg_provider, pg_db) -> None:
    """The listing of a connector the typed graph has never copied raises the refusal the service answers with a 503 (retry in a moment)."""
    from app.connectors.sources.localKB.handlers.kh_search import search_page
    lay = KhPgLayer(pg_provider, pg_db, not_current=False)
    world = KH_WORLDS[0]
    try:
        await PgLayer.preload(lay, [world])      # stored, never copied
        ns = await lay._ns(world)
        viewer = world.users()[0]
        with bind(OrgContext(ns.org, None, "job", "job:test", "t")), pytest.raises(KnowledgeHubListingNotReady):
            await search_page(pg_provider, user_key=ns.id(viewer), user_id=ns.id(viewer), org_id=ns.org, limit=5, secret=None)
    finally:
        await lay.release(list(lay._prefix))
