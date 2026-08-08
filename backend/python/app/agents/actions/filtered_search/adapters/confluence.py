"""`ConfluenceFilterAdapter`: validates, identity-substitutes, and executes
model-authored CQL via `ConfluenceDataSource.search_by_cql` — filters only.
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

# CQL's free-text operator `~`/`!~` is only meaningful on text-searchable
# fields (`siteSearch`, `text`, `title`, `content`, `comment`, `body`).
# Restricting the check to these field names — rather than "any bare `~`"
# — is what lets Confluence's personal-space token (`space = ~<accountId>`,
# itself UNQUOTED, visible in `list_filter_values` output) pass validation:
# it is a value after `space =`/`space IN`, never after one of these field
# names, so it can never match this pattern. Quoted literals are stripped
# first so a label/title value containing "title ~" as text is never
# mistaken for the operator.
_TEXT_FIELDS = frozenset({"sitesearch", "text", "title", "content", "comment", "body"})
_TEXT_OPERATOR_RE = re.compile(r"\b(\w+)\s*(!?~)(?!=)", re.IGNORECASE)

_CURRENT_USER_RE = re.compile(r"currentUser\s*\(\s*\)", re.IGNORECASE)


class ConfluenceFilterAdapter(NativeFilterAdapter):
    @classmethod
    def capabilities(cls) -> FilterCapabilityDescriptor:
        return FilterCapabilityDescriptor(
            connector_type="CONFLUENCE",
            record_group_noun="space",
            container_group_types=[RecordGroupType.CONFLUENCE_SPACES],
            group_reference=GroupReference.SHORT_NAME,
            supports_custom_fields=True,  # labels catalog, discovered via discover_custom_fields
            people_coverage_note=(
                "Roles are not tracked for Confluence Cloud; use record_groups/people only."
            ),
        )

    @classmethod
    def validate_query(cls, query: str) -> str | None:
        if not query or not query.strip():
            return "cql must not be empty."
        stripped = strip_quoted_literals(query)
        for match in _TEXT_OPERATOR_RE.finditer(stripped):
            field = match.group(1).lower()
            if field in _TEXT_FIELDS:
                return (
                    f"CQL text-match operator {match.group(2)!r} on field {field!r} is not allowed here — "
                    "search_confluence_content is filters only. Move that term into `content_query` instead."
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
        response = await client.search_by_cql(cql=query, limit=page.limit, cursor=page.cursor)
        if response.status != 200:
            raise RuntimeError(f"Confluence filter search failed with status {response.status}")

        data = response.json()
        results = data.get("results", [])
        records: list[FilteredRecord] = []
        for item in results:
            content = item.get("content") or {}
            content_id = content.get("id")
            if not content_id:
                continue
            records.append(
                FilteredRecord(
                    external_id=str(content_id),
                    name=content.get("title", ""),
                    web_url=None,
                )
            )
        return FilteredSearchUniverse(
            connector_type="CONFLUENCE",
            records=records,
            native_query=query,
            total_available=data.get("totalSize"),
            truncated=len(records) < (data.get("totalSize") or 0),
        )

    async def discover_custom_fields(self, client: Any) -> list[CustomFieldDef]:  # noqa: ANN401
        """Confluence has no custom-field concept — the closest analog is
        the org-wide label catalog (`GET /labels`), exposed here so
        `describe_filter_schema` gives the agent real, currently-used label
        values instead of nothing."""
        response = await client.get_labels(limit=100)
        if response.status != 200:
            return []
        try:
            data = response.json()
        except Exception:
            return []
        labels = sorted({
            item.get("name") for item in data.get("results", []) if item.get("name")
        })
        return [CustomFieldDef(field_id="label", name="Label", field_type="label", allowed_values=labels)]


    @classmethod
    async def build_datasource(cls, config_service, connector_id, logger):  # noqa: ANN001,ANN206
        from app.sources.client.confluence.confluence import ConfluenceClient
        from app.sources.external.confluence.confluence import ConfluenceDataSource

        client = await ConfluenceClient.build_from_services(
            logger=logger, config_service=config_service,
            connector_instance_id=connector_id,
        )
        return ConfluenceDataSource(client)


__all__ = ["ConfluenceFilterAdapter"]
