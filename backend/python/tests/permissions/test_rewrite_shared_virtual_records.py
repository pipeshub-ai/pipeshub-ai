"""A virtual record shared by several records keeps every sharer: the points carry the access of all of them, and a rewrite that moves one
sharer neither drops the access of the others nor admits through the one that left. Shared inside one connector, and across two connectors
(the same content synced twice), batched and midflight, on generated worlds.
"""

from __future__ import annotations

import random

import pytest

from app.services.permissions.rules import RULE_BY_CONNECTOR

from . import gen, mutations
from .model import Mutation
from .rewrite_kit import Env, Log, play, random_change, registration
from .test_scope_projector import retyped


@pytest.fixture(autouse=True)
def _writers_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACL_SCOPE_FIELDS", "on")


def sequence(world, rng: random.Random, n: int) -> list:
    out, w = [], world
    for _ in range(n):
        m = random_change(w, rng)
        if m is not None:
            out.append(m)
            w = mutations.apply(w, m)
    return out


@pytest.mark.parametrize("mode", ["batched", "midflight"])
@pytest.mark.parametrize("share", [True, "cross"])
@pytest.mark.parametrize("app_type", sorted(RULE_BY_CONNECTOR)[:6])
async def test_a_shared_virtual_record_keeps_every_sharer(app_type: str, share, mode: str) -> None:
    rng = random.Random(f"shared/{app_type}/{share}/{mode}")
    ran = 0
    for i, base in enumerate(gen.generate("proptest", 11, 5)):
        world = retyped(base, app_type)
        muts = sequence(world, rng, rng.choice([2, 3]))
        if not muts or not registration(world, mutations.apply_all(world, tuple(muts)), tuple(muts))[0]:
            continue
        env, seen = await Env.make(world, share=share, chunks=2), Log()
        if env.unstamped:
            continue
        tag = (app_type, share, mode, i, [(m.kind, m.node, m.ref, m.value) for m in muts])
        await play(env, muts, mode, seen)
        await env.audit(seen)
        assert not seen.leaks and not seen.pending_admits, (tag, seen.leaks[:3], seen.pending_admits[:3])
        assert not await env.problems(), tag      # membership too: recordGroupIds is the union over every sharer
        fresh, base_log = await Env.make(env.world, share=share, chunks=2), Log()
        await fresh.audit(base_log)
        assert set(seen.inexact) <= set(base_log.inexact), (tag, seen.inexact[:3])
        ran += 1
    assert ran, "no world took a structural change"


async def _changes_the_shared_stamp(world, record: str) -> Mutation | None:
    """A change of this record that changes the stamp of the virtual record two connectors share."""
    node = world.node(record)
    candidates = [Mutation("rule_set", record, value=v) for v in ("RESTRICTED", "STRICT")]
    candidates += [Mutation("inherit_set", record, ref=p.id, value="false" if p.id in node.inherits else "true") for p in node.parents]
    for m in candidates:
        env = await Env.make(world, share="cross")
        await env.change((m,))
        if "w:v-cross" in env.changed:
            return m
    return None


async def test_a_cross_connector_virtual_record_is_rewritten_by_each_connector() -> None:
    """The record two connectors share is changed three times in connector A and once in connector B, and B's change is one that changes the
    shared stamp: A's generation (3) is above B's (1), and so is the fence A's rewrite leaves on the shared point. B's rewrite must still end exact."""
    for base in gen.generate("proptest", 11, 40):
        for app_type in ("CONFLUENCE", "DRIVE WORKSPACE", "SHAREPOINT"):
            world = retyped(base, app_type)
            env = await Env.make(world, share="cross", chunks=2)
            shared = sorted(r for r, v in env.vrids().items() if v.endswith("v-cross"))
            if len(shared) < 2 or env.unstamped:
                continue
            a, b = shared[0], shared[1]
            if (m := await _changes_the_shared_stamp(world, b)) is None:
                continue
            for rule in ("RESTRICTED", "", "RESTRICTED"):
                await env.change((Mutation("rule_set", a, value=rule),))
            await env.change((m,))
            assert env.pending(env.world.node(a).connector) and env.pending(env.world.node(b).connector)
            seen = Log()
            await env.audit(seen)
            await env.settle()
            await env.audit(seen)
            assert not seen.leaks and not seen.pending_admits, (seen.leaks[:3], seen.pending_admits[:3])
            assert not await env.problems()
            return
    pytest.skip("no generated world has a record shared by two connectors whose change moves the shared stamp")
