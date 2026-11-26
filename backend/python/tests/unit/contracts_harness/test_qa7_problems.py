"""QA7-30 (FR-38, Python half): the Problem vector, read by ``app.control_plane.problems``.

Node reads the same file in ``tests/modules/control-plane/qa7-problems.test.ts`` and the frontend in
``lib/contracts/__tests__/qa7-problems.test.ts``; the three readers agree entry by entry.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.control_plane.contracts.generated.errors import ERROR_CODES
from app.control_plane.problems import ProblemRefused, read_problem

from .support import CONTROL_PLANE

VECTOR: dict[str, Any] = json.loads((CONTROL_PLANE / "vectors" / "problems.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("body", VECTOR["accepted"], ids=lambda b: b["code"])
def test_qa7_30_an_accepted_problem_is_read(body: dict[str, Any]) -> None:
    assert read_problem(body).code == body["code"]


@pytest.mark.parametrize("case", VECTOR["refused"], ids=lambda c: c["description"])
def test_qa7_30_a_refused_problem_names_the_first_rule_it_breaks(case: dict[str, Any]) -> None:
    with pytest.raises(ProblemRefused) as refused:
        read_problem(case["problem"])
    assert refused.value.reason == case["reason"]


def test_qa7_30_every_catalogue_code_has_an_accepted_problem() -> None:
    assert {body["code"] for body in VECTOR["accepted"]} == set(ERROR_CODES)


def test_qa7_30_every_refusal_reason_is_exercised() -> None:
    assert {case["reason"] for case in VECTOR["refused"]} == {"schema", "unknown_code", "status", "retryable", "remediation"}
