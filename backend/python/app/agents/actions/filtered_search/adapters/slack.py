"""`SlackFilterAdapter`: validates, identity-substitutes, and executes
model-authored Slack search-operator queries — filters only.

Documents the ID-reference operator syntax (`in:<#C0123>`, `from:<@U0123>`)
in the tool description rather than the human-typed name/handle forms
(`in:channel-name`, `from:@handle`), since `RecordGroup.short_name` for
Slack is a *display* string ("DM: Alice") that is not a valid channel
token at all (see `FilterCapabilityDescriptor.group_reference =
EXTERNAL_ID` below) — `list_filter_values`/`people_search` return the
IDs the model needs.
"""

from __future__ import annotations

import re
from typing import Any

from app.agents.actions.filtered_search.adapter import NativeFilterAdapter, Pagination
from app.agents.actions.filtered_search.models import (
    CustomFieldDef,
    FilterCapabilityDescriptor,
    FilteredRecord,
    FilteredSearchUniverse,
    GroupReference,
)
from app.models.entities import RecordGroupType

# A filter-only Slack search query is made ENTIRELY of `operator:value`
# modifiers (optionally negated with a leading `-`) — any other token is a
# free-text/content search term, including a quoted phrase, which is
# exactly what this subsystem routes through PipesHub's own retrieval
# instead (`content_query`).
_OPERATOR_TOKEN_RE = re.compile(
    r"^-?(?:in|from|to|before|after|on|during|has|is):\S+$", re.IGNORECASE
)

_SELF_TOKEN_RE = re.compile(r"\b(from|to):@?me\b", re.IGNORECASE)


class SlackFilterAdapter(NativeFilterAdapter):
    @classmethod
    def capabilities(cls) -> FilterCapabilityDescriptor:
        return FilterCapabilityDescriptor(
            connector_type="SLACK",
            record_group_noun="channel",
            container_group_types=[RecordGroupType.SLACK_CHANNEL],
            excluded_group_types=[RecordGroupType.SLACK_THREAD],
            group_reference=GroupReference.EXTERNAL_ID,
            supports_custom_fields=False,
            people_coverage_note=(
                "On Slack Individual-scope connectors only the authenticated user has a "
                "resolvable sourceUserId; other workspace members may not appear in people_search."
            ),
        )

    @classmethod
    def validate_query(cls, query: str) -> str | None:
        if not query or not query.strip():
            return "query must not be empty."
        for token in query.split():
            if not _OPERATOR_TOKEN_RE.match(token):
                return (
                    f"{token!r} is not a Slack operator:value filter (in:/from:/to:/before:/after:/"
                    "on:/during:/has:/is:) — search_slack_messages is filters only. Move free text "
                    "into `content_query` instead."
                )
        return None

    @classmethod
    def has_self_reference(cls, query: str) -> bool:
        return bool(_SELF_TOKEN_RE.search(query))

    @classmethod
    def substitute_identity(cls, query: str, source_user_id: str) -> str:
        return _SELF_TOKEN_RE.sub(lambda m: f"{m.group(1).lower()}:<@{source_user_id}>", query)

    async def execute(
        self, query: str, client: Any, page: Pagination  # noqa: ANN401
    ) -> FilteredSearchUniverse:
        response = await client.search_messages(query=query, count=page.limit)
        if not response.success or not isinstance(response.data, dict):
            raise RuntimeError(f"Slack filter search failed: {response.error or 'unknown error'}")
        response_dict = response.data
        messages = ((response_dict.get("messages") or {}).get("matches")) or []
        records = [
            FilteredRecord(
                # Regular (non-burst) Slack messages store their raw `ts` as
                # `Record.external_record_id`; messages folded into a burst
                # record at index time will not join here — a known,
                # documented partial-coverage case, not a bug (the bridge
                # treats unmatched external ids as silently omitted).
                external_id=msg.get("ts"),
                name=(msg.get("text") or "")[:200],
                web_url=msg.get("permalink"),
                external_group_id=(msg.get("channel") or {}).get("id"),
            )
            for msg in messages
            if msg.get("ts")
        ]
        total = ((response_dict.get("messages") or {}).get("total"))
        return FilteredSearchUniverse(
            connector_type="SLACK",
            records=records,
            native_query=query,
            total_available=total,
            truncated=bool(total) and len(records) < total,
        )

    async def discover_custom_fields(self, client: Any) -> list[CustomFieldDef]:  # noqa: ANN401
        raise NotImplementedError("SlackFilterAdapter does not support custom field discovery")


    @classmethod
    async def build_datasource(cls, config_service, connector_id, logger):  # noqa: ANN001,ANN206
        from app.sources.client.slack.slack import SlackClient
        from app.sources.external.slack.slack import SlackDataSource

        client = await SlackClient.build_from_services(
            logger=logger, config_service=config_service,
            connector_instance_id=connector_id,
        )
        return SlackDataSource(client)


__all__ = ["SlackFilterAdapter"]
