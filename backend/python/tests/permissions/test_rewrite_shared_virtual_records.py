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
from .rewrite_kit import PREFIX, Env, Log, play, random_change, registration
from .stamp_world import ORG
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


@pytest.mark.parametrize("order", ["both_changed_b_rewritten_first", "a_rewritten_then_b_changes"])
async def test_a_cross_connector_virtual_record_is_rewritten_by_each_connector(order: str) -> None:
    """The record two connectors share is changed three times in connector A and once in connector B, and B's change is one that changes the
    shared stamp. A's generation (3) is above B's (1). A fence that was a generation would then refuse B's write for ever when A's rewrite came
    first (`a_rewritten_then_b_changes`); the fence is a time, which both connectors compare."""
    for base in gen.generate("proptest", 11, 40):
        for app_type in ("CONFLUENCE", "DRIVE WORKSPACE", "SHAREPOINT"):
            world = retyped(base, app_type)
            env = await Env.make(world, share="cross", chunks=2)
            shared = sorted(r for r, v in env.vrids().items() if v.endswith("v-cross"))
            if len(shared) < 2:
                continue
            a, b = shared[0], shared[1]
            if (m := await _changes_the_shared_stamp(world, b)) is None:
                continue
            ca, cb = (PREFIX + env.world.node(x).connector for x in (a, b))
            ca_name = env.world.node(a).connector
            others = [n.id for n in env.world.nodes if n.kind == "record" and n.connector == ca_name and n.id not in shared and not n.deleted and not n.placeholder]
            if len(others) < 2:
                continue
            # three different roots, so that each bump is listed and the rewrite of A is a rewrite (the same root changed three times is not releasable)
            for node in (a, *others[:2]):
                await env.change((Mutation("rule_set", node, value="RESTRICTED"),))
            seen = Log()
            if order == "a_rewritten_then_b_changes":
                first = await env.job.run_connector(ORG, ca)      # A is rewritten and has left its fence on the shared point
                assert first.state == "done" and first.points_touched + first.points_written > 0, first
                await env.change((m,))
            else:
                await env.change((m,))
                await env.job.run_connector(ORG, cb)      # B first (generation 1), then A (generation 3)
            assert env.pending(env.world.node(b).connector) or order == "both_changed_b_rewritten_first"
            await env.audit(seen)
            await env.settle()
            await env.audit(seen)
            assert not seen.leaks and not seen.pending_admits, (seen.leaks[:3], seen.pending_admits[:3])
            assert not await env.problems()
            return
    pytest.fail("no generated world has a record shared by two connectors whose change moves the shared stamp")
