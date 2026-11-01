"""Switches for the coverage loop: every part the question asks for must have
supporting evidence before an answer is final.

``COVERAGE_LOOP`` is off unless set "true"; ``HIGH_ACCURACY_MODE`` does not
turn it on. Each part below is on while the loop is on unless its own switch
is "false", "0", "no" or "off", so one part can be tried or turned off by
itself.

- ``COVERAGE_LOOP_FACETS``: plan the question's required parts beside the
  prefetch and search in code for the parts the prefetch does not cover.
- ``COVERAGE_LOOP_GATE``: check a text answer against the plan and the
  evidence read before it is final; search for the gaps and ask for a revision.
- ``COVERAGE_LOOP_ADJUDICATION``: in the gate, compare records that give
  different values for one part and decide by scope and named document type.
- ``COVERAGE_LOOP_PROMPT``: the answer rules stop weighing records of a
  different scope as rival versions.

``ANSWER_OPENING_CHECK`` is off unless set "true": in the gate, a cited draft
that opens with what the assistant cannot do or with the status of what was
asked for goes back once to lead with the substance. It runs inside the gate,
so it needs the loop too.

Lives under `app/utils` because the chat bridge and the agent factory both
read it, and neither may import the other's modules for a switch.
"""

from __future__ import annotations

from app.utils.env_utils import enabled_unless_off, env_bool, env_int

COVERAGE_LOOP_ENV_VAR = "COVERAGE_LOOP"
COVERAGE_LOOP_FACETS_ENV_VAR = "COVERAGE_LOOP_FACETS"
COVERAGE_LOOP_GATE_ENV_VAR = "COVERAGE_LOOP_GATE"
COVERAGE_LOOP_ADJUDICATION_ENV_VAR = "COVERAGE_LOOP_ADJUDICATION"
COVERAGE_LOOP_PROMPT_ENV_VAR = "COVERAGE_LOOP_PROMPT"
ANSWER_OPENING_CHECK_ENV_VAR = "ANSWER_OPENING_CHECK"

COVERAGE_LOOP_FACET_SEARCHES_ENV_VAR = "COVERAGE_LOOP_FACET_SEARCHES"
COVERAGE_LOOP_GATE_ROUNDS_ENV_VAR = "COVERAGE_LOOP_GATE_ROUNDS"
COVERAGE_LOOP_GATE_SEARCHES_ENV_VAR = "COVERAGE_LOOP_GATE_SEARCHES"
COVERAGE_LOOP_GATE_SEARCH_TIMEOUT_ENV_VAR = "COVERAGE_LOOP_GATE_SEARCH_TIMEOUT_S"

_DEFAULT_FACET_SEARCHES = 1
_DEFAULT_GATE_ROUNDS = 1
_DEFAULT_GATE_SEARCHES = 2
_DEFAULT_GATE_SEARCH_TIMEOUT_S = 20


def coverage_loop_enabled() -> bool:
    return env_bool(COVERAGE_LOOP_ENV_VAR, False)


def _part_enabled(name: str) -> bool:
    return coverage_loop_enabled() and enabled_unless_off(name)


def coverage_facets_enabled() -> bool:
    return _part_enabled(COVERAGE_LOOP_FACETS_ENV_VAR)


def coverage_gate_enabled() -> bool:
    return _part_enabled(COVERAGE_LOOP_GATE_ENV_VAR)


def coverage_adjudication_enabled() -> bool:
    return coverage_gate_enabled() and enabled_unless_off(COVERAGE_LOOP_ADJUDICATION_ENV_VAR)


def coverage_prompt_enabled() -> bool:
    return _part_enabled(COVERAGE_LOOP_PROMPT_ENV_VAR)


def answer_opening_check_enabled() -> bool:
    return env_bool(ANSWER_OPENING_CHECK_ENV_VAR, False)


def facet_search_budget() -> int:
    """Searches run in code for parts the prefetch does not cover."""
    return env_int(COVERAGE_LOOP_FACET_SEARCHES_ENV_VAR, _DEFAULT_FACET_SEARCHES, lo=0, hi=4) or 0


def gate_round_budget() -> int:
    """Times the gate may send a draft back for revision."""
    return env_int(COVERAGE_LOOP_GATE_ROUNDS_ENV_VAR, _DEFAULT_GATE_ROUNDS, lo=0, hi=3) or 0


def gate_search_budget() -> int:
    """Searches the gate runs in code per round."""
    return env_int(COVERAGE_LOOP_GATE_SEARCHES_ENV_VAR, _DEFAULT_GATE_SEARCHES, lo=0, hi=5) or 0


def gate_search_timeout_seconds() -> float:
    """Bound on each search the gate runs; a slower one counts as finding nothing."""
    return float(
        env_int(COVERAGE_LOOP_GATE_SEARCH_TIMEOUT_ENV_VAR, _DEFAULT_GATE_SEARCH_TIMEOUT_S, lo=3, hi=120)
        or _DEFAULT_GATE_SEARCH_TIMEOUT_S
    )


__all__ = [
    "ANSWER_OPENING_CHECK_ENV_VAR",
    "COVERAGE_LOOP_ENV_VAR",
    "answer_opening_check_enabled",
    "coverage_adjudication_enabled",
    "coverage_facets_enabled",
    "coverage_gate_enabled",
    "coverage_loop_enabled",
    "coverage_prompt_enabled",
    "facet_search_budget",
    "gate_round_budget",
    "gate_search_budget",
    "gate_search_timeout_seconds",
]
