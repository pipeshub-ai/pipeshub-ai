"""Generated worlds through the rewrite job, for every connector type of the matrix.

A connector's stamps depend on its combining rule only (`RULE_BY_CONNECTOR`), so each distinct app type the registry gives a connector is run.
Every world gets a random single change (a move, an inheritance break or restore, a new access rule); each is held to the gate of the corpus:
0 admits of the subtree meanwhile, no leak, the stored fields the projection of the new graph and no worse than a fresh stamping after, and
writes only inside the subtree. PERM_GEN_SEED picks the seed (default 7), PERM_GEN_WORLDS the worlds per generator.
"""

from __future__ import annotations

import os
import random

import pytest

from app.services.permissions.rules import RULE_BY_CONNECTOR, rule_for

from . import gen, matrix, mutations
from .rewrite_kit import Env, Log, random_change, registration
from .test_scope_projector import retyped

SEED = int(os.environ.get("PERM_GEN_SEED", "7"))
WORLDS = int(os.environ.get("PERM_GEN_WORLDS", "3"))


def app_type(c: matrix.Connector) -> str:
    """The connector type string the writers register the app with, as far as the rule registry can tell."""
    path = c.path.lower()
    if "personal" in path or "individual" in path:
        return path.replace("/", " ").replace("_", " ").upper()
    if path == "google/drive/team":
        return "DRIVE WORKSPACE"
    for key in RULE_BY_CONNECTOR:
        if key.lower().replace(" ", "_") in path or (key == "CONFLUENCE" and "confluence" in path) or (key == "JIRA" and "jira" in path):
            return key
    return path.replace("/", " ").replace("_", " ").upper()


TYPES = sorted({app_type(c) for c in matrix.CONNECTORS})


@pytest.fixture(autouse=True)
def _writers_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACL_SCOPE_FIELDS", "on")


def test_every_connector_of_the_matrix_has_the_rule_the_matrix_says() -> None:
    wrong = [(c.path, c.rule, rule_for(app_type(c)).value) for c in matrix.CONNECTORS if rule_for(app_type(c)).value != c.rule]
    assert not wrong, wrong


@pytest.mark.parametrize("kind", sorted(gen.GENERATORS))
@pytest.mark.parametrize("connector_type", TYPES)
async def test_random_structural_changes_are_hidden_meanwhile_and_exact_after(connector_type: str, kind: str) -> None:
    rng = random.Random(f"{SEED}/{connector_type}/{kind}")
    ran = 0
    for i, base in enumerate(gen.generate(kind, SEED, WORLDS)):
        world = retyped(base, connector_type)
        change = random_change(world, rng)
        if change is None or not registration(world, mutations.apply_all(world, (change,)), (change,))[0]:
            continue
        tag = (connector_type, kind, SEED, i, change)
        env, seen, after = await Env.make(world), Log(), Log()
        await env.change((change,))
        await env.audit(seen)
        await env.settle()
        await env.audit(after)
        assert not seen.pending_admits and not seen.leaks and not after.leaks, (tag, seen.pending_admits[:3], seen.leaks[:3], after.leaks[:3])
        assert not await env.problems(), tag
        fresh, base_log = await Env.make(env.world), Log()
        await fresh.audit(base_log)
        assert set(seen.inexact) <= set(base_log.inexact), (tag, seen.inexact[:3])  # (a world the projector gets wrong from scratch stays as it was)
        assert after.inexact == base_log.inexact, (tag, after.inexact[:3], base_log.inexact[:3])
        ran += 1
    assert ran, "no world of this seed took a structural change"
