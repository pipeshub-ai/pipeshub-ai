"""The Jira suite's expected counts follow the connector's filing rules, not the raw ``parent`` field."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from connectors.jira import jira_test_utils as utils

pytestmark = pytest.mark.unit


_NO_FIELD = object()


def _issue(
    key: str, parent: str | None = None, *, subtask: bool = False, level: int = 0, security: object = _NO_FIELD
) -> dict:
    return {
        "key": key,
        "id": key,
        "fields": {
            "summary": key,
            "issuetype": {"name": "Sub-task" if subtask else "Story", "subtask": subtask, "hierarchyLevel": -1 if subtask else level},
            **({"parent": {"key": parent, "id": parent}} if parent else {}),
            **({} if security is _NO_FIELD else {"security": security}),
        },
    }


def _search_returns(monkeypatch: pytest.MonkeyPatch, issues: list[dict]) -> None:
    async def fake_search(*_args: object, **_kwargs: object) -> list[dict]:
        return issues

    monkeypatch.setattr(utils, "search_issues_jql", fake_search)
    monkeypatch.setattr(utils, "is_jira_it_artifact", lambda _issue: False)


def _counts(monkeypatch: pytest.MonkeyPatch, issues: list[dict]) -> dict[str, int]:
    _search_returns(monkeypatch, issues)
    return asyncio.run(utils.derive_jira_scope_counts(SimpleNamespace(), "QE"))


_LEVEL = {"id": "10100", "name": "Staff"}


def test_a_subtask_counts_when_its_parent_is_in_the_synced_project(monkeypatch: pytest.MonkeyPatch) -> None:
    issues = [
        _issue("QE-2", security=None),
        _issue("QE-3", "QE-2", subtask=True),
        _issue("QE-4", "QE-2", subtask=True),
        _issue("QE-5", "FINOPS-9", subtask=True),  # its parent never syncs
    ]

    assert _counts(monkeypatch, issues)["parent_child"] == 2


def test_a_story_counts_under_an_epic_with_no_level(monkeypatch: pytest.MonkeyPatch) -> None:
    """Everyone who reads the story reads an epic with no level, whatever the story's own level."""
    issues = [
        _issue("QE-1", level=1, security=None),
        _issue("QE-2", "QE-1", security=None),
        _issue("QE-3", "QE-1", security=_LEVEL),
    ]

    assert _counts(monkeypatch, issues) == {"ticket": 3, "file": 0, "parent_child": 2, "parent_child_by_level": 0}


@pytest.mark.parametrize(
    "epic_security",
    [_NO_FIELD, {"name": "no id"}],
    ids=["field missing", "level without an id"],
)
def test_a_story_does_not_count_when_the_epics_level_is_unknown(
    monkeypatch: pytest.MonkeyPatch, epic_security: object
) -> None:
    issues = [_issue("QE-1", level=1, security=epic_security), _issue("QE-2", "QE-1", security=None)]

    assert _counts(monkeypatch, issues)["parent_child"] == 0


def test_a_story_does_not_count_under_an_epic_with_a_level_it_does_not_share(monkeypatch: pytest.MonkeyPatch) -> None:
    issues = [
        _issue("QE-1", level=1, security=_LEVEL),
        _issue("QE-2", "QE-1", security=None),
        _issue("QE-3", "QE-1", security={"id": "10200"}),
    ]

    counts = _counts(monkeypatch, issues)
    assert (counts["parent_child"], counts["parent_child_by_level"]) == (0, 0)


def test_a_story_sharing_its_epics_level_is_counted_apart(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whether it is filed depends on the level's members, which the live count does not read."""
    issues = [_issue("QE-1", level=1, security=_LEVEL), _issue("QE-2", "QE-1", security=_LEVEL)]

    counts = _counts(monkeypatch, issues)
    assert (counts["parent_child"], counts["parent_child_by_level"]) == (0, 1)


def _epic_pair(monkeypatch: pytest.MonkeyPatch, issues: list[dict]) -> tuple | None:
    _search_returns(monkeypatch, issues)
    return asyncio.run(utils.discover_epic_and_child(SimpleNamespace(), "QE"))


def test_the_epic_pair_prefers_a_child_filed_under_its_epic(monkeypatch: pytest.MonkeyPatch) -> None:
    issues = [
        _issue("QE-1", level=1, security=_LEVEL),
        _issue("QE-2", "QE-1", security=None),
        _issue("QE-3", level=1, security=None),
        _issue("QE-4", "QE-3", security=None),
    ]

    assert _epic_pair(monkeypatch, issues) == ("QE-3", "QE-3", "QE-4", "QE-4", True)


def test_the_epic_pair_passes_over_a_shared_level(monkeypatch: pytest.MonkeyPatch) -> None:
    issues = [
        _issue("QE-1", level=1, security=_LEVEL),
        _issue("QE-2", "QE-1", security=_LEVEL),
        _issue("QE-3", "QE-1", security=None),
    ]

    assert _epic_pair(monkeypatch, issues) == ("QE-1", "QE-1", "QE-3", "QE-3", False)
