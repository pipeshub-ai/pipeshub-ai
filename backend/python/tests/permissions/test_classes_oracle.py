"""The gate of the access classes (``app.services.permissions.classes``): every viewer x node pair of the permission corpus, of generated worlds with fresh seeds and of the targeted
worlds, under five connector typings, agrees with the oracle: 0 over-admits, 0 under-admits, 0 non-SELF literals on a leaf record. And the gate fails when a rule is wrong: five
controls, each breaking one arm, must be noticed (see ``classes_gate``)."""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

import pytest

from app.services.permissions import classes
from tests.permissions import gen
from tests.permissions.scenario import load_all

from .classes_gate import RETYPE, run, targeted

if TYPE_CHECKING:
    from tests.permissions.model import World

sys.setrecursionlimit(10000)
SEEDS = [int(x) for x in os.environ.get("KG_CLASS_SEEDS", "7,77,11,2026,424242").split(",")]
WORLDS = int(os.environ.get("KG_CLASS_WORLDS", "20"))


def _generated(seed: int) -> list[tuple[str, World]]:
    return [(f"{kind}/{seed}/{i}", w) for kind in sorted(gen.GENERATORS) for i, w in enumerate(gen.generate(kind, seed, WORLDS))]


def _corpus() -> list[tuple[str, World]]:
    return [(s.id, s.final_world()) for s in load_all()]


def _clean(tally, examples, *, min_pairs: int) -> None:
    assert tally["pairs"] >= min_pairs, dict(tally)
    assert (tally["over"], tally["under"], tally["leaf_literal_violation"]) == (0, 0, 0), (dict(tally), dict(examples))


async def test_the_targeted_worlds_agree_with_the_oracle_under_every_typing() -> None:
    tally, examples = await run(targeted())
    _clean(tally, examples, min_pairs=300)
    assert tally["refusal:deep_below_seed"] >= 1           # T1: the chain near the hop cap behind a cycle is refused, not guessed


async def test_a_record_below_a_seed_of_its_own_group_has_a_class_and_one_below_a_seed_of_another_group_is_refused() -> None:
    """The cell's shape (records under a granted record, all in one record-group-level group) was refused as a whole; only the record whose seed has a group it does not share is."""
    tally, examples = await run([w for w in targeted() if w[0] == "RGL1"], typings=("as_written",))
    _clean(tally, examples, min_pairs=20)
    assert tally["refusal:rgl_below_seed"] == 1, dict(tally)


@pytest.mark.parametrize("typing", list(RETYPE))
async def test_the_corpus_agrees_with_the_oracle(typing: str) -> None:
    tally, examples = await run(_corpus(), typings=(typing,))
    _clean(tally, examples, min_pairs=4000)
    assert tally["pairs_legacy"] <= 0.02 * tally["pairs"]  # the realistic legacy share is below one in a hundred pairs


@pytest.mark.parametrize("seed", SEEDS)
async def test_generated_worlds_agree_with_the_oracle(seed: int) -> None:
    tally, examples = await run(_generated(seed), typings=("as_written", "container", "override"))
    _clean(tally, examples, min_pairs=1000)
    assert tally["apps_shallow"] > 0 and tally["apps_per_record_memo"] > 0       # both memo modes are exercised


# ---- the controls: a wrong rule must be noticed ---------------------------------------------------------------------------------------------

def _break_restricted_no_inherit(monkeypatch: pytest.MonkeyPatch) -> None:
    orig = classes.Compiler.hop

    def hop(self, c, p, t):  # noqa: ANN202
        cn = self.n[c]
        if (cn.rule or "OPEN") == "RESTRICTED" and p not in cn.inherits and not cn.deleted and not self.opens:
            lit = self._lit(c, t)                  # a grant passes a hop that can never pass
            return frozenset({lit}) if lit else None
        return orig(self, c, p, t)
    monkeypatch.setattr(classes.Compiler, "hop", hop)


def _break_no_r10(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(classes.Compiler, "below_seed", lambda self, t: set())


def _break_no_self(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(classes.Klass, "admits", lambda self, granted, gated, *, self_granted: self.app in gated and any(
        all((False if lit == classes.SELF else lit in granted) for lit in c) for c in self.clauses))


def _break_shared_memo(monkeypatch: pytest.MonkeyPatch) -> None:
    def _get(self):  # noqa: ANN202
        return self.__dict__["_wk"]

    def _set(self, value) -> None:
        if "_wk" not in self.__dict__:             # the per-record reset in compile() is ignored: the memo is shared by the whole app
            self.__dict__["_wk"] = value
    monkeypatch.setattr(classes.Compiler, "_walk", property(_get, _set), raising=False)


def _break_rgl_seeds_always_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(classes.Compiler, "seeds_exact", lambda self, t, others: True)


def _break_seeds_exact(monkeypatch: pytest.MonkeyPatch, was: str, now: str) -> None:
    import inspect
    import textwrap
    src = inspect.getsource(classes.Compiler.seeds_exact)
    assert was in src
    ns: dict = {}
    exec(textwrap.dedent(src).replace(was, now), vars(classes), ns)   # noqa: S102 - the control rewrites one condition of the real rule
    monkeypatch.setattr(classes.Compiler, "seeds_exact", ns["seeds_exact"])


def _break_rgl_seeds_ignore_crossing(monkeypatch: pytest.MonkeyPatch) -> None:
    _break_seeds_exact(monkeypatch, "crossed or self.is_rgl(p)", "crossed")


def _break_rgl_seeds_any_shared_group(monkeypatch: pytest.MonkeyPatch) -> None:
    _break_seeds_exact(monkeypatch, "not self.declared_groups(p) <= mine", "not (self.declared_groups(p) & mine or not self.declared_groups(p))")


CONTROLS = {
    "rgl-seeds-always-exact": (_break_rgl_seeds_always_exact, "over", "targeted"),
    "rgl-seeds-ignore-crossing": (_break_rgl_seeds_ignore_crossing, "over", "targeted"),
    "rgl-seeds-any-shared-group": (_break_rgl_seeds_any_shared_group, "over", "targeted"),
    "restricted-no-inherit": (_break_restricted_no_inherit, "over", "corpus"),
    "no-r10": (_break_no_r10, "under", "corpus"),
    "no-self": (_break_no_self, "under", "corpus"),
    "shared-memo": (_break_shared_memo, "over", "targeted"),
}


@pytest.mark.parametrize("name", list(CONTROLS))
async def test_a_wrong_rule_is_noticed_by_the_gate(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    breaker, kind, worlds = CONTROLS[name]
    breaker(monkeypatch)
    tally, _ = await run(targeted() if worlds == "targeted" else _corpus() + targeted(), typings=("as_written",))
    assert tally[kind] > 0, (name, dict(tally))


async def test_record_groups_without_children_treated_as_leaves_are_noticed_by_the_gate() -> None:
    """Round-trip of the materialization rule: a record group is never a leaf (it carries record-group-level literals even with members and no children)."""
    tally, _ = await run(targeted(), typings=("as_written",), leaf_groups=True)
    assert tally["under"] > 0 or tally["leaf_literal_violation"] > 0, dict(tally)
