"""COVERAGE_LOOP is off unless set; each part can be turned off alone."""

from __future__ import annotations

import pytest

from app.utils import coverage_switches as switches

_PARTS = ("COVERAGE_LOOP_FACETS", "COVERAGE_LOOP_GATE", "COVERAGE_LOOP_ADJUDICATION", "COVERAGE_LOOP_PROMPT")


@pytest.fixture(autouse=True)
def _clean(monkeypatch) -> None:
    for name in ("HIGH_ACCURACY_MODE", "COVERAGE_LOOP", *_PARTS, "COVERAGE_LOOP_FACET_SEARCHES",
                 "COVERAGE_LOOP_GATE_ROUNDS", "COVERAGE_LOOP_GATE_SEARCHES", "COVERAGE_LOOP_GATE_SEARCH_TIMEOUT_S"):
        monkeypatch.delenv(name, raising=False)


def _all() -> list[bool]:
    return [switches.coverage_facets_enabled(), switches.coverage_gate_enabled(),
            switches.coverage_adjudication_enabled(), switches.coverage_prompt_enabled()]


def test_off_by_default() -> None:
    assert not switches.coverage_loop_enabled()
    assert _all() == [False] * 4


@pytest.mark.parametrize("value", ["", "false", "1"])
def test_high_accuracy_mode_does_not_turn_it_on(monkeypatch, value: str) -> None:
    monkeypatch.setenv("HIGH_ACCURACY_MODE", "true")
    monkeypatch.setenv("COVERAGE_LOOP", value)
    assert _all() == [False] * 4


def test_every_part_is_on_with_the_loop(monkeypatch) -> None:
    monkeypatch.setenv("COVERAGE_LOOP", "true")
    assert _all() == [True] * 4


def test_each_part_turns_off_alone(monkeypatch) -> None:
    monkeypatch.setenv("COVERAGE_LOOP", "true")
    monkeypatch.setenv("COVERAGE_LOOP_GATE", "off")
    assert _all() == [True, False, False, True]  # adjudication lives in the gate
    monkeypatch.setenv("COVERAGE_LOOP_PROMPT", "")  # blank is unset
    assert switches.coverage_prompt_enabled()


def test_budgets_are_tight_by_default_and_clamped(monkeypatch) -> None:
    assert (switches.facet_search_budget(), switches.gate_round_budget(), switches.gate_search_budget()) == (1, 1, 2)
    monkeypatch.setenv("COVERAGE_LOOP_GATE_ROUNDS", "9")
    monkeypatch.setenv("COVERAGE_LOOP_FACET_SEARCHES", "x")
    assert switches.gate_round_budget() == 3 and switches.facet_search_budget() == 1


def test_the_gate_search_timeout_is_short_by_default_and_clamped(monkeypatch) -> None:
    assert switches.gate_search_timeout_seconds() == 20.0
    monkeypatch.setenv("COVERAGE_LOOP_GATE_SEARCH_TIMEOUT_S", "1")
    assert switches.gate_search_timeout_seconds() == 3.0
    monkeypatch.setenv("COVERAGE_LOOP_GATE_SEARCH_TIMEOUT_S", "60")
    assert switches.gate_search_timeout_seconds() == 60.0
