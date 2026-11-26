"""UX-06 (C10.02, WP-18): every typed error a person can meet says what they can do next, in plain language.

The catalogue codes carry it as their ``nextAction`` field (``core/error-catalog.schema.json`` makes it required);
the run endings, error answers and failed tool calls the web app words, which have no catalogue, carry it in
``vectors/error-next-actions.json``. A new code in any of those sources fails here until it has one.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

import pytest

from app.control_plane.contracts.generated.errors import (
    ERROR_CODES as CONTROL_PLANE_CODES,
)
from app.harness.contracts.generated.pipeshub.enums import ANSWER_ERROR_CODES
from app.harness.contracts.generated.pipeshub.errors import ERROR_CODES as HARNESS_CODES
from harness_core.contracts.generated.enums import NEXT_ACTION_KINDS, TOOL_ERROR_CODES
from harness_core.contracts.generated.error_catalog import NextAction
from tests.cohesion.support import REPO_ROOT, with_overlay

if TYPE_CHECKING:
    from collections.abc import Iterator

VECTORS = REPO_ROOT / "contracts" / "harness" / "vectors"
# With an edition's overlay entries (``contracts/harness/ee/vectors/error-next-actions.json``), when present.
NEXT_ACTIONS = with_overlay(
    json.loads((VECTORS / "error-next-actions.json").read_text(encoding="utf-8")), "vectors/error-next-actions.json"
)
RUN_ERROR_CODES = with_overlay(
    json.loads((VECTORS / "run-error-codes.json").read_text(encoding="utf-8")), "vectors/run-error-codes.json"
)["codes"]

# What each kind's words must name, so the field and its sentence cannot disagree.
STEP_WORDS: dict[str, tuple[str, ...]] = {
    "none": ("nothing to do",),
    "retry": ("again",),
    "wait_then_retry": ("wait",),
    "reload": ("reload",),
    "change_request": ("pick", "change", "fix", "shorten", "remove", "rephrase", "lower", "choose", "send a smaller",
                       "check the path", "confirm", "delete", "copy", "turn it on", "ask it a question", "ask without"),
    "new_conversation": ("new chat", "continue privately"),
    "reconnect": ("connect", "add it"),
    "ask_owner": ("owner", "the person who started it"),
    "ask_admin": ("admin",),
    "internal": ("nothing to do",),
}

# Catalogue groups a person reads in the web app (admission refusals, the cards' and settings pages' refusal maps):
# never internal. The worker's and the journal's own groups never reach a person: always internal.
PERSON_GROUPS = frozenset({
    "quota", "turn", "model_access", "model_account", "engine_profile", "run_config", "session_erased",
    "schedule", "publication", "approvals", "decision", "activity", "memory", "budget",
})
SERVICE_GROUPS = frozenset({"attempt_authority", "journal_rejected", "journal_payload"})
# Budget ledger codes the worker meets on its own reservations; the person meets the run's budget stop instead.
SERVICE_BUDGET_CODES = frozenset({"BUDGET_RESERVATION_MISSING", "BUDGET_RESERVATION_SETTLED", "BUDGET_SHARE_EXCEEDS_REMAINING"})
# The agent, not the person, is told these memory refusals and carries on.
SERVICE_MEMORY_CODES = frozenset({"MEMORY_WRITE_NOT_GRANTED", "MEMORY_REPLACE_UNKNOWN"})

CODE_LIKE = re.compile(r"`|_|\(|\)|\{|\}|\b[A-Z]{3,}\b|\b[a-z]+[A-Z]\w*\b")
# Names a person reads as they are written.
PROPER_NAMES = ("PipesHub", "gVisor", "AI")


def _all() -> Iterator[tuple[str, NextAction]]:
    for code, entry in HARNESS_CODES.items():
        yield f"harness:{code}", entry.next_action
    for code, entry in CONTROL_PLANE_CODES.items():
        yield f"control-plane:{code}", entry.next_action
    for section in ("runErrors", "answerErrors", "toolErrors"):
        for code, value in NEXT_ACTIONS[section].items():
            yield f"{section}:{code}", NextAction.model_validate(value)


ALL = dict(_all())


@pytest.mark.parametrize(
    ("section", "source"),
    [
        ("runErrors", RUN_ERROR_CODES),
        ("answerErrors", ANSWER_ERROR_CODES),
        ("toolErrors", TOOL_ERROR_CODES),
    ],
)
def test_ux06_each_section_names_every_code_of_its_source_and_no_other(section: str, source: tuple[str, ...]) -> None:
    assert sorted(NEXT_ACTIONS[section]) == sorted(source)


def test_ux06_every_catalogue_code_has_a_next_action_of_a_known_kind() -> None:
    assert len(HARNESS_CODES) > 100 and len(CONTROL_PLANE_CODES) > 20
    for where, action in ALL.items():
        assert action.kind in NEXT_ACTION_KINDS, where


@pytest.mark.parametrize("where", sorted(ALL))
def test_ux06_the_words_are_plain_and_name_the_step_of_their_kind(where: str) -> None:
    action = ALL[where]
    text = action.text
    assert text.endswith("."), f"{where}: {text!r} is not a sentence"
    assert len(text) <= 160, f"{where}: {len(text)} characters"
    plain = text
    for name in PROPER_NAMES:
        plain = plain.replace(name, "Name")
    found = CODE_LIKE.search(plain)
    assert found is None, f"{where}: {text!r} reads like code at {found.group(0)!r}"
    words = STEP_WORDS[action.kind]
    assert any(w in text.lower() for w in words), f"{where}: {action.kind} but {text!r} names none of {words}"


def test_ux06_codes_a_person_reads_are_never_internal_and_service_codes_always_are() -> None:
    for code, entry in HARNESS_CODES.items():
        kind = entry.next_action.kind
        if entry.group in SERVICE_GROUPS or code in SERVICE_BUDGET_CODES | SERVICE_MEMORY_CODES:
            assert kind == "internal", code
        elif entry.group in PERSON_GROUPS:
            assert kind != "internal", code
    for section in ("runErrors", "answerErrors", "toolErrors"):
        for code, value in NEXT_ACTIONS[section].items():
            assert value["kind"] != "internal", f"{section}:{code}"
