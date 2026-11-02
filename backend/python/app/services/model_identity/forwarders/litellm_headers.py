"""Trusted headers for a LiteLLM proxy.

* ``x-litellm-end-user-id`` drives per-user budgets, rate limits and model
  allow-lists (LiteLLM "customers").
* ``x-litellm-tags`` carries one ``group:<id>`` tag per group, so per-tag budgets
  and limits apply to a group. Tag budgets need a LiteLLM release with
  BerriAI/litellm#27573.

The trust boundary in this mode is the virtual key: anyone holding it can claim
any identity, so restrict the key's routes and network access.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.model_identity.forwarders.base import HeaderSet
from app.services.model_identity.sanitize import (
    clean_header_value,
    clean_tag_value,
    join_capped,
)

if TYPE_CHECKING:
    from app.services.model_identity.projector import ForwardedIdentity
    from app.services.model_identity.settings import ConnectionForwardingSettings

END_USER_HEADER = "x-litellm-end-user-id"
TAGS_HEADER = "x-litellm-tags"
WORKLOAD_HEADER = "x-pipeshub-workload"
PURPOSE_HEADER = "x-pipeshub-purpose"
REQUEST_ID_HEADER = "x-pipeshub-request-id"
CONVERSATION_ID_HEADER = "x-pipeshub-conversation-id"
GROUPS_HEADER = "x-pipeshub-user-groups"
GROUPS_INCOMPLETE_HEADER = "x-pipeshub-groups-incomplete"
AUTHORIZATION_HEADER = "authorization"


def service_key_headers(
    identity: ForwardedIdentity, settings: ConnectionForwardingSettings
) -> list[tuple[str, str]]:
    """Authorization for background traffic that uses its own virtual key.

    A separate key lets LiteLLM give indexing its own team, budget and model
    list, independent of any user.
    """
    if identity.is_user or not settings.service_api_key or settings.service_auth != "apiKey":
        return []
    return [(AUTHORIZATION_HEADER, f"Bearer {clean_header_value(settings.service_api_key)}")]


class LiteLLMHeadersForwarder:
    name = "litellm-headers"

    def requires_groups(self, settings: ConnectionForwardingSettings) -> bool:
        return True

    def render(
        self, identity: ForwardedIdentity, settings: ConnectionForwardingSettings
    ) -> HeaderSet:
        headers: list[tuple[str, str]] = [
            (END_USER_HEADER, clean_header_value(identity.subject)),
            (TAGS_HEADER, self._tags(identity)),
            (WORKLOAD_HEADER, identity.workload),
        ]
        if identity.purpose and identity.purpose != "main":
            headers.append((PURPOSE_HEADER, identity.purpose))
        if identity.request_id:
            headers.append((REQUEST_ID_HEADER, identity.request_id))
        if identity.conversation_id:
            headers.append((CONVERSATION_ID_HEADER, identity.conversation_id))
        if identity.group_ids:
            headers.append(
                (GROUPS_HEADER, join_capped(clean_tag_value(g) for g in identity.group_ids))
            )
        if identity.is_user and not identity.groups_complete:
            headers.append((GROUPS_INCOMPLETE_HEADER, "true"))

        auth = service_key_headers(identity, settings)
        return HeaderSet(set=tuple(headers + auth))

    @staticmethod
    def _tags(identity: ForwardedIdentity) -> str:
        # Workload and org first: when many groups fill the header cap it is the
        # group tags at the end that are dropped, not the ones every request needs.
        tags = [f"workload:{clean_tag_value(identity.workload)}"]
        if identity.org_id:
            tags.append(f"org:{clean_tag_value(identity.org_id)}")
        tags.extend(f"group:{clean_tag_value(g)}" for g in identity.group_ids)
        return join_capped(tags)
