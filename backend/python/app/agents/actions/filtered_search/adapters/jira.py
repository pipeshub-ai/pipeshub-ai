"""`JiraFilterAdapter`: validates, identity-substitutes, and executes
model-authored JQL — filters only, never a text term.

Reuses the same authenticated `JiraDataSource` the existing `Jira` toolset
uses — this adapter adds no new auth surface, it is handed the client the
`search_jira_issues` tool resolved via `connector_context.py`.
"""

from __future__ import annotations

import re
from typing import Any

from app.agents.actions.filtered_search.adapter import NativeFilterAdapter, Pagination
from app.agents.actions.filtered_search.adapters._query_text import strip_quoted_literals
from app.agents.actions.filtered_search.models import (
    CustomFieldDef,
    FilterCapabilityDescriptor,
    FilteredRecord,
    FilteredSearchUniverse,
    GroupReference,
)
from app.models.entities import RecordGroupType

# JQL restricts `~`/`!~` (fuzzy/text matching) to text-searchable fields —
# `priority ~ "x"` is not valid JQL, so matching only these field names
# cannot false-positive on a real filter clause. Quoted literals are
# stripped first so a label/value containing "summary ~" as text is never
# mistaken for the operator.
_TEXT_FIELDS = frozenset({"text", "summary", "description", "comment", "environment"})
_TEXT_OPERATOR_RE = re.compile(r"\b(\w+)\s*(!?~)(?!=)")

_CURRENT_USER_RE = re.compile(r"currentUser\s*\(\s*\)", re.IGNORECASE)


class JiraFilterAdapter(NativeFilterAdapter):
    @classmethod
    def capabilities(cls) -> FilterCapabilityDescriptor:
        return FilterCapabilityDescriptor(
            connector_type="JIRA",
            record_group_noun="project",
            container_group_types=[RecordGroupType.PROJECT],
            group_reference=GroupReference.SHORT_NAME,
            supports_custom_fields=True,
        )

    @classmethod
    def validate_query(cls, query: str) -> str | None:
        if not query or not query.strip():
            return "jql must not be empty."
        stripped = strip_quoted_literals(query)
        for match in _TEXT_OPERATOR_RE.finditer(stripped):
            field = match.group(1).lower()
            if field in _TEXT_FIELDS:
                return (
                    f"JQL text-match operator {match.group(2)!r} on field {field!r} is not allowed here — "
                    "search_jira_issues is filters only. Move that term into `content_query` instead."
                )
        return None

    @classmethod
    def has_self_reference(cls, query: str) -> bool:
        return bool(_CURRENT_USER_RE.search(query))

    @classmethod
    def substitute_identity(cls, query: str, source_user_id: str) -> str:
        return _CURRENT_USER_RE.sub(f'"{source_user_id}"', query)

    async def execute(
        self, query: str, client: Any, page: Pagination  # noqa: ANN401
    ) -> FilteredSearchUniverse:
        response = await client.search_and_reconsile_issues_using_jql_post(
            jql=query,
            maxResults=page.limit,
            fields=["summary", "key"],
        )
        if response.status != 200:
            raise RuntimeError(f"Jira filter search failed with status {response.status}")

        data = response.json()
        issues = data.get("issues", [])
        records = [
            FilteredRecord(
                # Jira connectors store the numeric issue `id` (not the
                # human `key`) as `Record.external_record_id` — see
                # `jira_cloud/connector.py::_extract_issue_data`. The
                # bridge joins on this, so it must match exactly.
                external_id=str(issue.get("id")),
                name=f"{issue.get('key', '')}: {(issue.get('fields') or {}).get('summary', '')}".strip(": "),
                web_url=None,  # resolved from the graph by the bridge, not guessed here
            )
            for issue in issues
            if issue.get("id")
        ]
        return FilteredSearchUniverse(
            connector_type="JIRA",
            records=records,
            native_query=query,
            total_available=data.get("total"),
            truncated=len(records) < (data.get("total") or 0),
        )

    async def discover_custom_fields(self, client: Any) -> list[CustomFieldDef]:  # noqa: ANN401
        response = await client.get_fields()
        if response.status != 200:
            raise RuntimeError(f"Failed to fetch Jira fields: status {response.status}")
        fields = response.json() or []
        return [
            CustomFieldDef(
                field_id=f.get("id", ""),
                name=f.get("name", ""),
                field_type=(f.get("schema") or {}).get("type", ""),
                # `clauseNames` is what a JQL author actually types (usually
                # `cf[12345]` for custom fields, sometimes an alias) — the
                # `id` alone is not always valid JQL syntax.
                clause_name=((f.get("clauseNames") or [None])[0]),
            )
            for f in fields
            if isinstance(f, dict) and str(f.get("id", "")).startswith("customfield_")
        ]


    @classmethod
    async def build_datasource(cls, config_service, connector_id, logger):  # noqa: ANN001,ANN206
        from app.sources.client.jira.jira import JiraClient
        from app.sources.external.jira.jira import JiraDataSource

        client = await JiraClient.build_from_services(
            logger=logger, config_service=config_service,
            connector_instance_id=connector_id,
        )
        return JiraDataSource(client)


__all__ = ["JiraFilterAdapter"]
