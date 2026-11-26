"""Which knowledge sources outsiders can write: the ``untrusted_content_read`` taint (C3.23, KN-08).

A record from an email, a chat, a ticket queue, a code host or the web may hold text written by someone outside the
organization, and so instructions the person never gave. The evidence is what a call journals
(``x.pipeshub.citations.recorded``): the records its result drew from, each with its connector. Set by the gateway
from that evidence, never by the model, so a resumed attempt reads the same answer from the same journal.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, Literal, cast

from app.config.constants.arangodb import Connectors
from app.harness.citations import EVENT as CITATIONS_EVENT

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from pydantic import JsonValue

    from harness_core.journal.types import NewEvent

WEB: Final = "the web"
# Connector -> how a person names it. Design-plan §4.9: Slack, email, web, Confluence; and the other sources where
# people outside the organization write (tickets, code hosts, meeting invites and transcripts). A connector that
# names nothing known is untrusted too (fail closed).
UNTRUSTED_CONNECTORS: Final[Mapping[str, str]] = {
    Connectors.SLACK.value: "Slack",
    Connectors.SLACK_WORKSPACE.value: "Slack",
    Connectors.MICROSOFT_TEAMS.value: "Microsoft Teams",
    Connectors.GOOGLE_MAIL.value: "Gmail",
    Connectors.GOOGLE_MAIL_WORKSPACE.value: "Gmail",
    Connectors.OUTLOOK.value: "Outlook",
    Connectors.OUTLOOK_INDIVIDUAL.value: "Outlook",
    Connectors.OUTLOOK_CALENDAR.value: "Outlook Calendar",
    Connectors.GOOGLE_CALENDAR.value: "Google Calendar",
    Connectors.CONFLUENCE.value: "Confluence",
    Connectors.CONFLUENCE_DATA_CENTER.value: "Confluence",
    Connectors.CONFLUENCE_DATA_CENTER_PERSONAL.value: "Confluence",
    Connectors.WEB.value: WEB,
    Connectors.RSS.value: "RSS feeds",
    Connectors.GITHUB.value: "GitHub",
    Connectors.GITHUB_TEAMS.value: "GitHub",
    Connectors.GITLAB.value: "GitLab",
    Connectors.GITLAB_PERSONAL.value: "GitLab",
    Connectors.ZENDESK.value: "Zendesk",
    Connectors.ZAMMAD.value: "Zammad",
    Connectors.SERVICENOW.value: "ServiceNow",
    Connectors.ZOOM.value: "Zoom",
    Connectors.UNKNOWN.value: "an unknown source",
}
_KNOWN: Final = frozenset(c.value for c in Connectors)
_CONNECTOR_KEYS: Final = ("connector", "connector_name", "connectorName")


def _connector(record: Mapping[str, object]) -> str | None:
    for key in _CONNECTOR_KEYS:
        value = record.get(key)
        if isinstance(value, str) and value:
            # A record copied from an enum reads ``Connectors.SLACK``.
            return value.removeprefix("Connectors.")
    return None


def source_label(record: Mapping[str, object]) -> str | None:
    """How a person names the outsider-written source of ``record`` (citation metadata, snake or camel case); None
    when the organization's own people write it."""
    if record.get("sourceKind") == "web" or record.get("origin") == "WEB_SEARCH":
        return WEB
    connector = _connector(record)
    if connector is None:
        return None
    if connector not in _KNOWN:
        return UNTRUSTED_CONNECTORS[Connectors.UNKNOWN.value]
    return UNTRUSTED_CONNECTORS.get(connector)


def untrusted_in(data: Mapping[str, JsonValue]) -> tuple[str, ...]:
    """The outsider-written sources one ``x.pipeshub.citations.recorded`` names, in the order it names them."""
    items: list[object] = []
    records = data.get("records")
    if isinstance(records, dict):
        items.extend(records.values())
    for field in ("toolRecords", "finalResults"):
        value = data.get(field)
        if isinstance(value, list):
            items.extend(value)
    found: dict[str, None] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        entry = cast("dict[str, object]", item)
        metadata = entry.get("metadata")
        label = source_label(cast("dict[str, object]", metadata) if isinstance(metadata, dict) else entry)
        if label is None and isinstance(metadata, dict):
            label = source_label(entry)
        if label is not None:
            found[label] = None
    web = data.get("webRecords")
    if isinstance(web, list) and web:
        found[WEB] = None
    return tuple(found)


def untrusted_sources(events: Iterable[NewEvent]) -> tuple[str, ...]:
    """The outsider-written sources a call's journaled events name (``NamesUntrusted``)."""
    found: dict[str, None] = {}
    for event in events:
        if event.type == CITATIONS_EVENT:
            found.update(dict.fromkeys(untrusted_in(event.data)))
    return tuple(found)


def untrusted_integrity(events: tuple[NewEvent, ...]) -> Literal["external"] | None:
    """A call's integrity from its journaled events (``RegistryToolPort``'s ``rate``): ``external`` when any of its
    sources is outsider-written, else no opinion."""
    return "external" if untrusted_sources(events) else None


__all__ = [
    "UNTRUSTED_CONNECTORS",
    "WEB",
    "source_label",
    "untrusted_in",
    "untrusted_integrity",
    "untrusted_sources",
]
