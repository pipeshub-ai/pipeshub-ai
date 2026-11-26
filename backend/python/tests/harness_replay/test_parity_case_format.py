"""The parity case format: every committed case loads and is decided from its recording alone, and a case carrying a
field the format does not have is refused, so a suite that adds fields does so in its own subclass."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
import yaml
from pydantic import ValidationError

from tests.support.parity_cases.harness_cases import (
    CASE_DIR,
    CaseSetError,
    Grader,
    ParityCase,
    load_cases,
)

if TYPE_CHECKING:
    from pathlib import Path

QUICK = CASE_DIR / "quick" / "quick_01_kb_lookup.yaml"


def _doc(path: Path = QUICK) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_every_committed_case_loads_with_unique_ids() -> None:
    cases = load_cases()
    assert cases and len({c.id for c in cases}) == len(cases)
    assert {c.category for c in cases} >= {"quick", "react", "injection"}


def test_a_suite_loads_the_committed_cases_as_its_own_subclass() -> None:
    class Tagged(ParityCase):
        tags: list[str] = []

    cases = load_cases(CASE_DIR, Tagged)
    assert cases and all(isinstance(c, Tagged) for c in cases)


@pytest.mark.parametrize(
    "extra",
    [{"sets": ["all"]}, {"k": 3}, {"live_task": "ask again"}],
    ids=["sets", "repeat count", "alternate task"],
)
def test_a_field_the_format_does_not_have_is_refused(extra: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _ = ParityCase.model_validate({**_doc(), **extra})


def test_a_grader_the_recording_cannot_decide_is_refused() -> None:
    with pytest.raises(ValidationError):
        _ = Grader.model_validate({"llm_judge": "the answer is right"})


def test_an_injection_block_belongs_to_injection_cases_only() -> None:
    doc = _doc()
    injection = {"vector": "kb_doc", "channel": "slack_post", "canary": "c", "goal": ["g"]}
    with pytest.raises(ValidationError, match="injection"):
        _ = ParityCase.model_validate({**doc, "injection": injection})
    with pytest.raises(ValidationError, match="injection"):
        _ = ParityCase.model_validate({**doc, "category": "injection"})


def test_a_case_without_graders_is_refused() -> None:
    with pytest.raises(ValidationError, match="grader"):
        _ = ParityCase.model_validate({**_doc(), "grader": []})


def test_an_empty_directory_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CaseSetError, match="no cases"):
        _ = load_cases(tmp_path)


def test_a_repeated_id_is_refused(tmp_path: Path) -> None:
    for name in ("a.yaml", "b.yaml"):
        _ = (tmp_path / name).write_text(QUICK.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(CaseSetError, match="duplicate"):
        _ = load_cases(tmp_path)
