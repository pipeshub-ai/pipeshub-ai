"""Schemas connectors ship with, and the values a record of theirs provides.

Only tickets have one so far: every connector type that syncs ``TicketRecord``
gets the ticket schema.
"""

from collections.abc import Callable
from enum import Enum

from app.config.constants.arangodb import Connectors
from app.models.entities import (
    DeliveryStatus,
    ItemType,
    Priority,
    Record,
    Status,
    TicketRecord,
)
from app.models.record_metadata import (
    MAX_LIST_ITEMS,
    MetadataProvenance,
    MetadataSchema,
    MetadataValidationError,
    MetadataValue,
    PropertyDefinition,
    PropertyType,
    RecordMetadata,
    coerce_value,
    string_fits,
)

TICKET_SCHEMA_ID = "builtin:ticket:1"


def _enum_values(enum: type[Enum]) -> tuple[str, ...]:
    # UNKNOWN is what a connector writes for a value it could not map: no value.
    return tuple(member.value for member in enum if member.value != "UNKNOWN")


def _enum(name: str, label: str, enum: type[Enum], **flags: object) -> PropertyDefinition:
    return PropertyDefinition(
        name=name, type=PropertyType.ENUM, display_label=label, facetable=True,
        enum_values=_enum_values(enum), **flags,
    )


def _text(name: str, label: str, **flags: object) -> PropertyDefinition:
    return PropertyDefinition(name=name, type=PropertyType.STRING, display_label=label, filterable=True, **flags)


def _time(name: str, label: str) -> PropertyDefinition:
    return PropertyDefinition(name=name, type=PropertyType.DATETIME, display_label=label, filterable=True)


TICKET_PROPERTIES: tuple[PropertyDefinition, ...] = (
    _enum("status", "Status", Status, semantic_label="status"),
    _text("sourceStatus", "Source status", searchable=True),
    _enum("priority", "Priority", Priority),
    _enum("issueType", "Type", ItemType),
    _enum("deliveryStatus", "Delivery status", DeliveryStatus),
    _text("assignee", "Assignee", searchable=True, semantic_label="owner"),
    _text("assigneeEmail", "Assignee email"),
    _text("reporterName", "Reporter", searchable=True, semantic_label="author"),
    _text("reporterEmail", "Reporter email"),
    _text("creatorName", "Creator", searchable=True),
    _text("creatorEmail", "Creator email"),
    PropertyDefinition(
        name="labels", type=PropertyType.STRING_LIST, display_label="Labels", facetable=True, searchable=True,
    ),
    _time("resolvedAt", "Resolved"),
    _time("dueDate", "Due"),
)

TICKET_SCHEMA = MetadataSchema(schema_id=TICKET_SCHEMA_ID, properties=TICKET_PROPERTIES)

_TICKET_CONNECTORS = frozenset({
    Connectors.JIRA, Connectors.JIRA_PERSONAL, Connectors.JIRA_CLOUD_PERSONAL,
    Connectors.JIRA_DATA_CENTER, Connectors.JIRA_DATA_CENTER_PERSONAL,
    Connectors.LINEAR, Connectors.ZENDESK, Connectors.ZAMMAD, Connectors.SALESFORCE,
    Connectors.GITHUB_TEAMS, Connectors.GITLAB, Connectors.GITLAB_PERSONAL,
})

#: Connector type to the properties of its built-in schema.
BUILTIN_SCHEMAS: dict[Connectors, Callable[[], tuple[PropertyDefinition, ...]]] = {
    connector: (lambda: TICKET_PROPERTIES) for connector in _TICKET_CONNECTORS
}


def builtin_properties(connector: Connectors | str) -> tuple[PropertyDefinition, ...] | None:
    """The properties a connector type ships with, or None."""
    try:
        connector = Connectors(connector)
    except ValueError:
        return None
    factory = BUILTIN_SCHEMAS.get(connector)
    return factory() if factory else None


def connector_source(connector: Connectors | str) -> str:
    """``connector:<type>``: the ``source`` of the values a connector provides."""
    name = connector.value if isinstance(connector, Enum) else str(connector)
    return "connector:" + name.lower().replace(" ", "_")


def _member(value: object, enum: type[Enum]) -> object:
    """``value`` as its member's value, matched without case as
    ``TicketRecord._safe_enum_parse`` does; anything else unchanged.

    Some connectors pass the source's own spelling through (a Salesforce
    picklist's "High"), and the same ticket re-read from the graph comes back
    upper-cased; both must provide the same value.
    """
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, str):
        wanted = value.upper()
        for member in enum:
            if member.value.upper() == wanted:
                return member.value
    return value


def ticket_metadata(record: Record) -> RecordMetadata | None:
    """The values a ticket provides under ``TICKET_SCHEMA``; None for any other record or none set.

    A value that does not fit its property (a status the connector did not map,
    an over-long name) is left out rather than failing the record, and so is a
    label that does not fit; labels past ``MAX_LIST_ITEMS`` are cut.
    """
    if not isinstance(record, TicketRecord):
        return None
    # With emails hidden, the email fields hold the source's own user ids.
    emails = not record.is_email_hidden
    raw: dict[str, object] = {
        "status": _member(record.status, Status),
        "sourceStatus": record.source_status,
        "priority": _member(record.priority, Priority),
        "issueType": _member(record.type, ItemType),
        "deliveryStatus": _member(record.delivery_status, DeliveryStatus),
        "assignee": record.assignee,
        "assigneeEmail": record.assignee_email if emails else None,
        "reporterName": record.reporter_name,
        "reporterEmail": record.reporter_email if emails else None,
        "creatorName": record.creator_name,
        "creatorEmail": record.creator_email if emails else None,
        "labels": [label for label in record.labels or () if string_fits(label)][:MAX_LIST_ITEMS] or None,
        "resolvedAt": record.resolved_at,
        "dueDate": record.due_date,
    }
    source = connector_source(record.connector_name)
    values: dict[str, MetadataValue] = {}
    for prop in TICKET_SCHEMA.properties:
        value = raw.get(prop.name)
        if value is None or value == "":
            continue
        try:
            coerced = coerce_value(prop, value)
        except MetadataValidationError:
            continue
        values[prop.name] = MetadataValue(value=coerced, provenance=MetadataProvenance.PROVIDED, source=source)
    if not values:
        return None
    return RecordMetadata(schema_id=TICKET_SCHEMA_ID, values=values)
