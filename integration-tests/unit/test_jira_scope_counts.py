"""The Jira suite's expected counts follow the connector's filing rules, not the raw ``parent`` field."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from connectors.jira import jira_test_utils as utils

pytestmark = pytest.mark.unit


def _issue(key: str, parent: str | None = None, *, subtask: bool = False, level: int = 0) -> dict:
    return {
        "key": key,
        "id": key,
        "fields": {
            "summary": key,
            "issuetype": {"name": "Sub-task" if subtask else "Story", "subtask": subtask, "hierarchyLevel": -1 if subtask else level},
            **({"parent": {"key": parent, "id": parent}} if parent else {}),
        },
    }


def _counts(monkeypatch: pytest.MonkeyPatch, issues: list[dict]) -> dict[str, int]:
    async def fake_search(*_args: object, **_kwargs: object) -> list[dict]:
        return issues

    monkeypatch.setattr(utils, "search_issues_jql", fake_search)
    monkeypatch.setattr(utils, "is_jira_it_artifact", lambda _issue: False)
    return asyncio.run(utils.derive_jira_scope_counts(SimpleNamespace(), "QE"))


def test_only_a_subtask_of_a_synced_project_counts_as_a_parent_child_edge(monkeypatch: pytest.MonkeyPatch) -> None:
    """A story under an epic hangs off its project, and so does an issue whose parent is in a project that
    does not sync; only a sub-task is filed under its parent."""
    issues = [
        _issue("QE-1", level=1),
        _issue("QE-2", "QE-1"),  # a story under an epic
        _issue("QE-3", "QE-2", subtask=True),
        _issue("QE-4", "QE-2", subtask=True),
        _issue("QE-5", "FINOPS-9", subtask=True),  # its parent never syncs
    ]

    assert _counts(monkeypatch, issues)["parent_child"] == 2
