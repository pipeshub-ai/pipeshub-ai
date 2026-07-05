"""Connector-toolset detection helpers, extracted from
`modules/agents/qna/nodes.py` (Phase 0 of the agent-loop migration).

Replaces the original 14 nearly-identical `_has_<connector>_tools(state)`
functions with two generic primitives (`derive_active_connectors`,
`has_connector`) per the migration plan, while keeping the original
function names as thin backward-compatible wrappers so `nodes.py` and any
existing tests importing them by name keep working unchanged.

`has_connector` preserves the original *substring* match semantics (e.g. a
toolset named "jira-cloud" still matches `has_connector(connectors, "jira")`)
rather than switching to exact-name membership, so behavior is identical to
the pre-extraction implementation.
"""

from __future__ import annotations

from typing import Any


def derive_active_connectors(agent_toolsets: list[dict[str, Any]] | None) -> frozenset[str]:
    """Lowercased `name` field of every configured toolset dict.

    Non-dict entries are ignored, matching the original per-connector
    functions' `isinstance(ts, dict)` guard. Deliberately does NOT guard
    against `agent_toolsets` being `None` — the original `_has_*_tools`
    functions iterated it directly and raised `TypeError` in that case, and
    Phase 0 preserves that behavior exactly (no defaulting/broadening).
    """
    return frozenset(
        ts.get("name", "").lower() for ts in agent_toolsets if isinstance(ts, dict)
    )


def has_connector(connectors: frozenset[str], name: str) -> bool:
    """True if `name` is a substring of any active connector's toolset name.

    Substring (not exact-match) semantics on purpose — matches the original
    `"jira" in ts.get("name", "").lower()` checks, so e.g. a toolset named
    "jira-cloud" or "jira_datacenter" still counts as Jira.
    """
    return any(name in connector for connector in connectors)


def _has_connector_from_state(state: Any, name: str) -> bool:
    agent_toolsets = state.get("agent_toolsets", [])
    return has_connector(derive_active_connectors(agent_toolsets), name)


def _has_jira_tools(state: Any) -> bool:
    """Check if Jira tools available"""
    return _has_connector_from_state(state, "jira")


def _has_confluence_tools(state: Any) -> bool:
    """Check if Confluence tools available"""
    return _has_connector_from_state(state, "confluence")


def _has_slack_tools(state: Any) -> bool:
    """Check if Slack tools available"""
    return _has_connector_from_state(state, "slack")


def _has_onedrive_tools(state: Any) -> bool:
    """Check if OneDrive tools available"""
    return _has_connector_from_state(state, "onedrive")


def _has_outlook_tools(state: Any) -> bool:
    """Check if Outlook tools available"""
    return _has_connector_from_state(state, "outlook")


def _has_teams_tools(state: Any) -> bool:
    """Check if Microsoft Teams tools available"""
    return _has_connector_from_state(state, "teams")


def _has_github_tools(state: Any) -> bool:
    """Check if GitHub tools available"""
    return _has_connector_from_state(state, "github")


def _has_mariadb_tools(state: Any) -> bool:
    """Check if MariaDB tools available"""
    return _has_connector_from_state(state, "mariadb")


def _has_zoom_tools(state: Any) -> bool:
    """Check if Zoom tools available"""
    return _has_connector_from_state(state, "zoom")


def _has_salesforce_tools(state: Any) -> bool:
    """Check if Salesforce tools available"""
    return _has_connector_from_state(state, "salesforce")


def _has_clickup_tools(state: Any) -> bool:
    """Check if ClickUp tools available"""
    return _has_connector_from_state(state, "clickup")


def _has_redshift_tools(state: Any) -> bool:
    """Check if Redshift tools available"""
    return _has_connector_from_state(state, "redshift")


def _has_sharepoint_tools(state: Any) -> bool:
    """Check if SharePoint tools available"""
    return _has_connector_from_state(state, "sharepoint")
