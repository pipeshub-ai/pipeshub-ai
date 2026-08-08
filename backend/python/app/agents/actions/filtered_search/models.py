"""Data models for native-app filter search.

There is no normalized filter language here — each connector's tool
(`filtered_search/tools.py`) takes that connector's own native query
(JQL, CQL, Slack search operators) verbatim; see the design doc's "Why the
fix is a language change, not a new field" for why a hand-maintained
`FilterSpec` superset was abandoned. What remains is the vocabulary-mapping
declaration (`FilterCapabilityDescriptor`) that `list_filter_values` and
`people_search` render from, and the plain result models the adapters and
`FilteredRetrievalBridge` pass between each other.

Nothing here talks to a database or an HTTP client — these are plain
Pydantic models, unit-testable in isolation.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

# Real (not TYPE_CHECKING-only) import: `RecordGroupType` is used as an
# actual Pydantic field type below (`container_group_types`,
# `excluded_group_types`), so Pydantic must be able to resolve it at class
# body evaluation time — deferring it behind `TYPE_CHECKING` breaks model
# construction with "class not fully defined" at runtime.
from app.models.entities import RecordGroupType  # noqa: TC001


class GroupReference(str, Enum):
    """Which `RecordGroup` field is the native query's filter token.

    Jira/Confluence: the project/space KEY lives in `short_name`.
    Slack: channels are referenced by ID in search operators, but DM/MPIM
    groups have no usable name — see `SlackFilterAdapter` for the fallback.
    """

    SHORT_NAME = "SHORT_NAME"
    EXTERNAL_ID = "EXTERNAL_ID"


class FilterCapabilityDescriptor(BaseModel):
    """Machine-readable declaration of how one connector's PipesHub-graph
    vocabulary (record groups, people) maps onto its native query language.

    Deliberately does NOT declare "supported filter fields" or "entity
    types" — those are properties of the connector's own query language
    (JQL/CQL/Slack operators), which the model already knows and the tool
    description documents with examples, not something this subsystem
    re-declares and risks drifting from reality.
    """

    connector_type: str = Field(description="Matches the connector/app registration name, e.g. 'JIRA'.")
    record_group_noun: str = Field(description="Human noun for this connector's container, e.g. 'project'.")
    container_group_types: list[RecordGroupType] = Field(
        description="RecordGroupType values that are top-level filter containers for this connector."
    )
    excluded_group_types: list[RecordGroupType] = Field(
        default_factory=list,
        description="RecordGroupType values to exclude from vocabulary listings (high-cardinality/no key).",
    )
    group_reference: GroupReference = Field(
        description="Which RecordGroup field holds the native query token for this connector."
    )
    supports_custom_fields: bool = Field(default=False)
    people_coverage_note: str | None = Field(
        default=None,
        description="Caveat surfaced to the agent, e.g. Slack Individual-scope U-ID coverage gap.",
    )


class FilteredRecord(BaseModel):
    """One hit from a native filter-only search — never carries content."""

    external_id: str
    name: str
    web_url: str | None = None
    external_group_id: str | None = None


class FilteredSearchUniverse(BaseModel):
    """The result of a filter-only native search: a candidate set of
    external IDs, not yet resolved to PipesHub records or permission-gated.

    `FilteredRetrievalBridge` consumes this to produce the final answer.
    """

    connector_type: str
    records: list[FilteredRecord] = Field(default_factory=list)
    native_query: str = Field(description="The JQL/CQL/Slack-operator string actually executed, for transparency.")
    total_available: int | None = Field(
        default=None, description="Total matches at the source, when the API reports it (may exceed len(records))."
    )
    truncated: bool = Field(default=False)


class CustomFieldDef(BaseModel):
    """One custom/connector-specific filterable field, discovered via
    `describe_filter_schema` rather than known statically."""

    field_id: str
    name: str
    field_type: str = ""
    allowed_values: list[str] | None = None
    clause_name: str | None = Field(
        default=None,
        description="The literal token to type in the native query (e.g. Jira JQL clauseNames), "
        "when it differs from field_id.",
    )


__all__ = [
    "GroupReference",
    "FilterCapabilityDescriptor",
    "FilteredRecord",
    "FilteredSearchUniverse",
    "CustomFieldDef",
]
