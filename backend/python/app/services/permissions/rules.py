"""Which combining rule a connector's exception records use. One registry, read by the viewer-scope resolver and by
whatever writes scope fields on records: a connector's rule is declared here and nowhere else.

The rules (see `Rule`):
- Container: the record group is the permission scope; the record carries no scope fields.
- Union: a grant anywhere on the inheritance chain opens the record.
- Override: the nearest ancestor with unique permissions replaces everything above it.
- Intersection: the viewer must pass the container and every restriction on the path.

A record `assert_may_stamp` refuses has **no exact stamp**: it stays on the legacy filter, never Container (its access
differs from its record group's by definition), and its connector is not exact (`connector_is_exact`) for counts and
candidate lists. Only the Intersection rule needs the viewer's whole `unreadable_restricted` set. A connector that is not listed here
may not be stamped with an exception rule at all (`assert_may_stamp`), so a new connector cannot silently get the
fail-open reading of "its restricted scopes are not in the set".
"""

from __future__ import annotations

from collections.abc import Iterable  # noqa: TC003
from dataclasses import dataclass
from enum import Enum


class Rule(str, Enum):
    CONTAINER = "container"
    UNION = "union"
    OVERRIDE = "override"
    INTERSECTION = "intersection"


class RuleNotAllowed(ValueError):
    """The stamp would make the scope filter inexact (or the connector has no declared rule)."""


# Connector type names start with the product ("CONFLUENCE", "CONFLUENCE DATA CENTER", "JIRA PERSONAL", ...).
# A connector that is not listed is Container-only: every record carries its record group's access. The item-level ACL
# connectors are Override: a record's own list replaces everything above it.
RULE_BY_CONNECTOR: dict[str, Rule] = {
    "CONFLUENCE": Rule.INTERSECTION,
    "JIRA": Rule.INTERSECTION,
    "DRIVE WORKSPACE": Rule.UNION,  # the team connector; "DRIVE" (one person's drive) is Container-only
    "SHAREPOINT": Rule.OVERRIDE,
    "ONEDRIVE": Rule.OVERRIDE,
    "BOX": Rule.OVERRIDE,
    "DROPBOX": Rule.OVERRIDE,
    "SALESFORCE": Rule.OVERRIDE,
    "SERVICENOW": Rule.OVERRIDE,
    "ZENDESK": Rule.OVERRIDE,
    "ZAMMAD": Rule.OVERRIDE,
    "ZOOM": Rule.OVERRIDE,
    "DRUPAL WIKI": Rule.OVERRIDE,
    "BOOKSTACK": Rule.OVERRIDE,
    "DEMO": Rule.OVERRIDE,
}
PERSONAL_MARKERS = ("PERSONAL", "INDIVIDUAL")


def rule_for(connector_type: str) -> Rule:
    """The exception rule the registry declares for this connector type; Container when it declares none."""
    upper = connector_type.upper()
    if any(marker in upper for marker in PERSONAL_MARKERS):  # APP_LEVEL: the app opens everything, records carry no rule
        return Rule.CONTAINER
    for prefix, rule in RULE_BY_CONNECTOR.items():
        if upper.startswith(prefix):
            return rule
    return Rule.CONTAINER


def uses_exclusion(connector_type: str) -> bool:
    return rule_for(connector_type) is Rule.INTERSECTION


@dataclass(frozen=True)
class AncestorFact:
    """What the stamper knows about one ancestor of a record, from the graph, independent of any viewer."""

    open_rule: bool  # accessRule absent or OPEN
    directly_granted: bool  # at least one PERMISSION edge into it
    restricted_above: bool  # some ancestor above it is restricted (rule not OPEN/STRICT, deleted, or does not inherit)


def below_seed(ancestors: Iterable[AncestorFact]) -> bool:
    """Whether a record may sit below a *seed* (R9/R10): an OPEN node with a direct grant below a restricted ancestor.
    A viewer granted the seed reads it, and what hangs below it over OPEN nodes, whatever the restricted ancestor says.
    Viewer-independent, so a stamper can compute it; it over-approximates (it ignores who is granted)."""
    return any(a.open_rule and a.directly_granted and a.restricted_above for a in ancestors)


def connector_is_exact(refused_records: int) -> bool:
    """A connector with any record `assert_may_stamp` refuses is **not exact**: those records stay on the legacy filter,
    so counts, facets and candidate lists for the connector cannot come from the scope filter. It is never switched for
    them while `refused_records` is above zero."""
    return refused_records == 0


def assert_may_stamp(
    rule: Rule,
    connector_type: str,
    *,
    equals_record_group: bool = False,
    record_is_open: bool = True,
    record_group_level_on_path: bool = False,
    multi_parent_on_path: bool = False,
    below_seed_on_path: bool = False,
) -> None:
    """The check a stamper calls before it writes scope fields. A record it refuses gets **no exact stamp**: it stays on
    the legacy filter (the post-check) and the connector is not exact (`connector_is_exact`). It must never be stamped
    Container instead: a refused record is by definition one whose access differs from its record group's, so a
    Container stamp makes the filter admit it through its group.

    - Container: the stamper must have computed that the record's effective access equals its record group's
      (`equals_record_group=True`); otherwise refused.
    - any exception rule: the registry must declare it for the connector (a connector cannot pick its own).
    - Intersection is refused on a record with a record-group-level group on its path or in its app, with several
      hierarchy parents on its path, and below a seed (`below_seed_on_path`, see `below_seed`): the path filter is not
      exact there.
    - Union is refused for a record whose rule is not OPEN (`record_is_open=False`) below a seed: the path filter is
      not exact there either.
    """
    if rule is Rule.CONTAINER:
        if not equals_record_group:
            raise RuleNotAllowed(f"{connector_type!r}: Container needs the record's access to equal its record group's")
        return
    declared = rule_for(connector_type)
    if declared is not rule:
        raise RuleNotAllowed(f"{connector_type!r} is registered as {declared.value}, not {rule.value}")
    if rule is Rule.INTERSECTION and record_group_level_on_path:
        raise RuleNotAllowed(f"{connector_type!r}: Intersection is not exact below a record-group-level group")
    if rule is Rule.INTERSECTION and multi_parent_on_path:
        raise RuleNotAllowed(f"{connector_type!r}: Intersection is not exact on a path with several parents")
    if rule is Rule.INTERSECTION and below_seed_on_path:
        raise RuleNotAllowed(f"{connector_type!r}: Intersection is not exact below a seed")
    if rule is Rule.UNION and below_seed_on_path and not record_is_open:
        raise RuleNotAllowed(f"{connector_type!r}: a non-OPEN record below a seed is not exact under Union")
