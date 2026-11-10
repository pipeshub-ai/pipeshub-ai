"""The rule registry (`app.services.permissions.rules`) is the one place a connector's combining rule is declared; the
coverage matrix classifies connectors independently, and the two must agree where they overlap."""

from __future__ import annotations

from app.services.permissions.rules import RULE_BY_CONNECTOR, Rule, rule_for

from . import matrix

BY_RULE = {matrix.INTERSECTION: Rule.INTERSECTION, matrix.UNION: Rule.UNION, matrix.OVERRIDE: Rule.OVERRIDE,
           matrix.CONTAINER: Rule.CONTAINER}


def _type_of(path: str) -> str:
    """'atlassian/jira_cloud' -> 'JIRA CLOUD'; 'google/drive/team' -> 'DRIVE WORKSPACE'; 'microsoft/sharepoint_online' -> 'SHAREPOINT ONLINE'."""
    parts = path.split("/")
    if parts[0] == "google" and parts[1] == "drive":
        return "DRIVE WORKSPACE" if parts[2] == "team" else "DRIVE"
    name = parts[1] if parts[0] in ("atlassian", "microsoft", "google") else parts[0]
    return name.replace("_", " ").upper()


def test_the_registry_agrees_with_the_matrix_on_every_connector() -> None:
    """Every row, not only the exception-rule groups: a connector the matrix calls Override (the item-level ACL ones
    included) must not be Container-only in the registry, or a stamper would write Container stamps on records whose
    own list differs from their group's."""
    assert matrix.CONNECTORS
    assert [(c.path, rule_for(_type_of(c.path)).value, c.rule) for c in matrix.CONNECTORS
            if rule_for(_type_of(c.path)) is not BY_RULE[c.rule]] == []


def test_every_registered_connector_has_a_matrix_row() -> None:
    types = {_type_of(c.path) for c in matrix.CONNECTORS}
    assert [t for t in RULE_BY_CONNECTOR if not any(x.startswith(t) or t.startswith(x) for x in types)] == []
