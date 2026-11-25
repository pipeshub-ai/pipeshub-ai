"""QA14-1: the canary scan finds every planted value and every vector shape, wherever a surface nests it."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import BaseModel

from harness_core.journal.inline import inline_ref
from harness_core.security.token_patterns import SECRET_MASK, redact_tokens
from harness_core.testing.canary import (
    Canaries,
    Surface,
    assert_no_canary,
    private_canary,
    scan,
    secret_canary,
)
from harness_core.testing.ids import SeededIds
from tests.support.token_vector import EXAMPLES, VectorEntry

IDS = SeededIds(14)
SECRET = secret_canary(IDS.uuid4)
PRIVATE = private_canary(IDS.uuid4)
CANARIES = Canaries(secrets=(SECRET,), private=(PRIVATE,))


@dataclass(frozen=True, slots=True)
class _Slotted:
    inner: object


class _Model(BaseModel):
    text: str


class _Plain:
    def __init__(self, value: object) -> None:
        self.value = value


class _SlotsOnly:
    __slots__ = ("held",)

    def __init__(self, held: object) -> None:
        self.held = held


def _nestings(text: str) -> list[object]:
    return [
        text,
        text.encode(),
        {"k": [{"deep": (text,)}]},
        {text: 1},
        _Slotted(_Model(text=text)),
        _Plain({"x": _SlotsOnly(text)}),
    ]


@pytest.mark.parametrize("index", range(6))
def test_qa14_1_the_secret_canary_is_found_wherever_it_nests(index: int) -> None:
    leaks = scan(Surface("s", _nestings(f"before {SECRET} after")[index]), CANARIES)
    assert [leak.kind for leak in leaks] == ["secret"]
    assert SECRET not in str(leaks[0]), "a report never prints the whole secret"


@pytest.mark.parametrize(("name", "example", "entry"), EXAMPLES, ids=[e[0] for e in EXAMPLES])
def test_qa14_1_every_vector_example_is_found_and_its_mask_is_not(name: str, example: str, entry: VectorEntry) -> None:
    del name
    found = scan(Surface("logs", {"line": f"auth failed: {example}"}), Canaries(secrets=()))
    assert entry.kind in {leak.kind for leak in found}
    assert scan(Surface("logs", redact_tokens(f"auth failed: {example}")), Canaries(secrets=())) == []
    assert SECRET_MASK in redact_tokens(example)


def test_qa14_1_private_text_is_a_leak_only_off_the_owners_view() -> None:
    value = {"message": f"summarise {PRIVATE}"}
    assert [leak.kind for leak in scan(Surface("logs", value), CANARIES)] == ["private"]
    assert scan(Surface("journal content", value, owner_view=True), CANARIES) == []
    owner_secret = scan(Surface("journal content", {"m": SECRET}, owner_view=True), CANARIES)
    assert [leak.kind for leak in owner_secret] == ["secret"], "a secret is a leak on the owner's view too"


def test_qa14_1_a_clean_run_passes_and_a_dirty_one_names_every_surface() -> None:
    clean = [Surface("journal", {"a": "ok"}), Surface("logs", ["fine", b"bytes"])]
    assert_no_canary(clean, CANARIES)
    with pytest.raises(AssertionError) as failed:
        assert_no_canary([*clean, Surface("spans", [SECRET]), Surface("metrics", PRIVATE)], CANARIES)
    message = str(failed.value)
    assert "spans: secret" in message and "metrics: private" in message
    assert SECRET not in message and PRIVATE not in message


def test_qa14_1_cycles_end() -> None:
    loop: dict[str, object] = {"text": "x"}
    loop["self"] = loop
    assert scan(Surface("s", loop), CANARIES) == []


def test_qa14_1_a_payload_carried_in_its_inline_ref_is_scanned() -> None:
    ref = inline_ref(f'{{"content": "{SECRET}"}}'.encode())
    assert [leak.kind for leak in scan(Surface("journal", {"resultRef": ref.model_dump()}), CANARIES)] == ["secret"]


def test_qa14_1_any_container_is_walked() -> None:
    from collections import OrderedDict, deque

    values: list[object] = [deque([{"e": SECRET}]), OrderedDict(k=SECRET), {"k": SECRET}.values(), {SECRET}]
    for value in values:
        assert [leak.kind for leak in scan(Surface("s", value), CANARIES)] == ["secret"], type(value)


def test_qa14_1_every_model_in_a_list_is_walked() -> None:
    """Found while building: each model's dump is a temporary; a walker keyed on ``id`` alone skipped later ones."""
    values = [_Model(text=f"clean {i}") for i in range(200)] + [_Model(text=SECRET)]
    assert [leak.kind for leak in scan(Surface("journal", values), CANARIES)] == ["secret"]
