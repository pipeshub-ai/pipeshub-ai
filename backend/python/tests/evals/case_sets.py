"""Which golden cases run nightly, and which only weekly.

The nightly set is the cheap tripwire: the cases whose failure means the agent
picks the wrong tool or writes without being asked, which is worth learning the
next morning rather than the next week. The full set runs weekly and costs
proportionally more.

Membership is listed by case id rather than tagged on ``GoldenCase`` so that
adding a case forces a decision here: :func:`select_cases` refuses to run when
a case in ``GOLDEN_CASES`` is in no set, instead of silently leaving it out of
both schedules.
"""

from __future__ import annotations

from tests.evals.live_harness import GOLDEN_CASES, GoldenCase

# The subset a nightly run executes.
NIGHTLY_CASE_IDS: tuple[str, ...] = (
    "C-01-single-lookup",
    "C-02-no-write-without-intent",
)

SET_NAMES: tuple[str, ...] = ("nightly", "full")


class UnknownCaseSet(ValueError):
    """Asked for a set that does not exist."""


class CaseSetError(ValueError):
    """The sets and the cases disagree — running would measure the wrong thing."""


def _by_id(cases: list[GoldenCase]) -> dict[str, GoldenCase]:
    return {case.id: case for case in cases}


def check_sets(cases: list[GoldenCase] | None = None) -> None:
    """Raise when a case is in no set, or a set names a case that is gone."""
    known = _by_id(cases if cases is not None else GOLDEN_CASES)
    missing = [case_id for case_id in NIGHTLY_CASE_IDS if case_id not in known]
    if missing:
        raise CaseSetError(
            "NIGHTLY_CASE_IDS names cases that no longer exist: "
            f"{', '.join(sorted(missing))}. Remove them from case_sets.py, or "
            "restore them in live_harness.py."
        )
    # Every case is in the full set by construction, so "in no set" can only
    # mean a case nobody decided about when it was added.
    if not known:
        raise CaseSetError(
            "There are no golden cases to run. Add cases to GOLDEN_CASES in "
            "tests/evals/live_harness.py."
        )


def select_cases(set_name: str, cases: list[GoldenCase] | None = None) -> list[GoldenCase]:
    """The cases belonging to ``set_name``.

    Raises rather than returning an empty list: a run with nothing in it would
    report a pass rate over zero cases, which reads as success.
    """
    all_cases = list(cases if cases is not None else GOLDEN_CASES)
    check_sets(all_cases)
    if set_name == "full":
        return all_cases
    if set_name == "nightly":
        known = _by_id(all_cases)
        selected = [known[case_id] for case_id in NIGHTLY_CASE_IDS]
        if not selected:
            raise CaseSetError(
                "The nightly set is empty. Add case ids to NIGHTLY_CASE_IDS in "
                "tests/evals/case_sets.py."
            )
        return selected
    raise UnknownCaseSet(
        f"'{set_name}' is not a case set. Choose one of: {', '.join(SET_NAMES)}."
    )


__all__ = [
    "NIGHTLY_CASE_IDS",
    "SET_NAMES",
    "CaseSetError",
    "UnknownCaseSet",
    "check_sets",
    "select_cases",
]
