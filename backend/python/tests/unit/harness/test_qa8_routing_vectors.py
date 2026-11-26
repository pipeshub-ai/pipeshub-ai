"""QA-8 RTE-21: the routing table (contracts/harness/vectors/routing.json) read from Python.

Node holds every RoutingDecider adapter to the table; here the table itself is held to be total and
self-consistent, to the product's mode catalog, and read the same way the e2e oracle reads it
(scripts/routing_table.py).
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from hypothesis import given, settings
from hypothesis import strategies as st

if TYPE_CHECKING:
    from types import ModuleType

ROOT = Path(__file__).resolve().parents[5]


def _load_routing_table() -> ModuleType:
    spec = importlib.util.spec_from_file_location("routing_table", ROOT / "scripts" / "routing_table.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("routing_table", module)
    spec.loader.exec_module(module)
    return module


rt = _load_routing_table()
FACTORS = rt.factors()

valid_inputs = st.fixed_dictionaries({f: st.sampled_from(levels) for f, levels in FACTORS.items()}).filter(rt.is_valid)


def test_qa8_1_every_named_vector_is_valid_and_its_expected_answer_is_the_tables() -> None:
    vectors = rt.table()["vectors"]
    assert len({v["id"] for v in vectors}) == len(vectors)
    for vector in vectors:
        inp = rt.input_of(vector["input"])
        assert rt.is_valid(inp), vector["id"]
        found = rt.answer(inp)
        assert found is not None and found[1] == vector["expected"], vector["id"]


def test_qa8_1_every_rule_is_some_named_vectors_answer() -> None:
    used = {rt.answer(rt.input_of(v["input"]))[0] for v in rt.table()["vectors"]}
    assert [r["id"] for r in rt.table()["rules"] if r["id"] not in used] == []


@settings(max_examples=500, deadline=None)
@given(valid_inputs)
def test_qa8_4_every_valid_input_has_one_answer_and_the_same_one_twice(inp: dict[str, str]) -> None:
    first = rt.answer(inp)
    assert first is not None, inp
    assert rt.answer(dict(reversed(list(inp.items())))) == first
    assert first[1]["route"] in {"durable", "engine", "legacy", "refused"}
    assert ("reason" in first[1]) == (first[1]["route"] == "legacy")
    assert ("code" in first[1]) == (first[1]["route"] == "refused")


def _level_classes() -> dict[str, list[list[str]]]:
    """Each factor's levels, grouped where no selector tells them apart.

    is_valid and answer read a level only as `level in selector[factor]`, so levels that every constraint and rule
    selector puts on the same side give every input the same validity and the same answer.
    """
    table = rt.table()
    selectors = [
        *(c["if"] for c in table["constraints"]),
        *(c["then"] for c in table["constraints"]),
        *(r["when"] for r in table["rules"]),
    ]
    classes: dict[str, list[list[str]]] = {}
    for factor, levels in FACTORS.items():
        by_side: dict[tuple[bool, ...], list[str]] = {}
        for level in levels:
            side = tuple(level in s[factor] for s in selectors if factor in s)
            by_side.setdefault(side, []).append(level)
        classes[factor] = list(by_side.values())
    return classes


LEVEL_CLASSES = _level_classes()


@settings(max_examples=300, deadline=None)
@given(valid_inputs, st.data())
def test_qa8_4_levels_of_one_class_answer_alike(inp: dict[str, str], data: st.DataObject) -> None:
    """What lets the totality check below walk one level per class instead of the full product."""
    factor = data.draw(st.sampled_from(list(FACTORS)))
    peers = next(c for c in LEVEL_CLASSES[factor] if inp[factor] in c)
    swapped = {**inp, factor: data.draw(st.sampled_from(peers))}
    assert rt.is_valid(swapped)
    assert rt.answer(swapped) == rt.answer(inp)


def test_qa8_4_the_table_is_total_over_every_valid_input_and_every_rule_answers_one() -> None:
    # Every valid input, by class: the full product (~4M inputs) took ~17 s, past the timeout on a loaded gate.
    names = list(FACTORS)
    answered: set[str] = set()
    for combo in itertools.product(*LEVEL_CLASSES.values()):
        inp = {name: levels[0] for name, levels in zip(names, combo)}
        if not rt.is_valid(inp):
            continue
        found = rt.answer(inp)
        assert found is not None, inp
        answered.add(found[0])
    assert answered == {r["id"] for r in rt.table()["rules"]}


def test_qa8_5_the_tables_durable_modes_are_the_products() -> None:
    """durable-chat-modes.json is what test_durable_chat_modes derives from MODE_CATALOG and the chat mode policies."""
    product = json.loads((ROOT / "contracts" / "harness" / "vectors" / "durable-chat-modes.json").read_text())

    def durable(kind: str) -> set[str]:
        return {
            mode for mode in FACTORS["chatMode"]
            if rt.answer(rt.input_of({"kind": kind, "chatMode": mode}))[1]["route"] == "durable"
        }

    assert durable("agent") == set(product["durableChatModes"])
    assert durable("assistant") == set(product["assistantChatModes"])


def test_qa8_6_the_e2e_mapping_names_only_factor_levels() -> None:
    e2e = rt.table()["e2e"]
    for part in ("modes", "paths", "engines", "triggers"):
        for partial in e2e[part].values():
            for factor, level in partial.items():
                assert level in FACTORS[factor], (part, factor, level)
