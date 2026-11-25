"""SV-1..SV-4 (P2.2): saving an agent keeps two instances of one toolset type apart (flag on)."""

from __future__ import annotations

from typing import Any

import pytest

from app.api.routes.agent import (
    InvalidRequestError,
    _parse_mcp_servers,
    _parse_toolsets,
)


def _jira(instance: str | None, *tools: str) -> dict[str, Any]:
    entry: dict[str, Any] = {"name": "Jira", "displayName": "Jira", "tools": [{"name": t} for t in tools]}
    if instance is not None:
        entry |= {"instanceId": instance, "instanceName": f"Jira {instance}"}
    return entry


def _github(instance: str) -> dict[str, Any]:
    return {"instanceId": instance, "name": f"gh-{instance}", "typeId": "github", "tools": [{"name": "create_issue"}]}


@pytest.fixture
def flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INSTANCE_TOOL_IDS", "true")


@pytest.mark.usefixtures("flag_on")
def test_sv1_two_instances_are_two_toolsets() -> None:
    parsed = _parse_toolsets([_jira("a", "search_issues"), _jira("b", "create_issue")])
    entries = sorted(parsed.values(), key=lambda e: e["instanceId"])
    assert [(e["name"], e["instanceId"], [t["name"] for t in e["tools"]]) for e in entries] == [
        ("jira", "a", ["search_issues"]),
        ("jira", "b", ["create_issue"]),
    ]
    assert [e["instanceName"] for e in entries] == ["Jira a", "Jira b"]


@pytest.mark.usefixtures("flag_on")
def test_sv2_one_instance_listed_twice_merges_and_an_unbound_toolset_is_keyed_by_type() -> None:
    parsed = _parse_toolsets([_jira("a", "search_issues"), _jira("a", "create_issue"), _jira(None, "x"), _jira(None, "y")])
    assert len(parsed) == 2
    bound = next(e for e in parsed.values() if e["instanceId"] == "a")
    unbound = parsed["jira"]
    assert [t["name"] for t in bound["tools"]] == ["search_issues", "create_issue"]
    assert (unbound["instanceId"], [t["name"] for t in unbound["tools"]]) == (None, ["x", "y"])


@pytest.mark.usefixtures("flag_on")
def test_sv3_two_mcp_instances_of_one_type_are_kept() -> None:
    parsed = _parse_mcp_servers([_github("m1"), _github("m2")])
    assert sorted(parsed) == ["m1", "m2"]


def test_sv4_flag_off_collapses_and_refuses_as_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INSTANCE_TOOL_IDS", raising=False)
    parsed = _parse_toolsets([_jira("a", "search_issues"), _jira("b", "create_issue")])
    assert list(parsed) == ["jira"]
    assert parsed["jira"]["instanceId"] == "a"
    assert [t["name"] for t in parsed["jira"]["tools"]] == ["search_issues", "create_issue"]
    with pytest.raises(InvalidRequestError):
        _parse_mcp_servers([_github("m1"), _github("m2")])
