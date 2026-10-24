"""Which API toolset operations an agent may call.

An agent stores them as one list of `"<instanceId>:<toolName>"` strings (`apiToolsetTools`).
A flat list of strings is a property both graph backends store as is; ids are hex and tool
names are `[A-Za-z0-9_-]`, so the `:` is unambiguous.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.agents.openapi.models import AuthType
from app.agents.openapi.service import ApiToolsetError

if TYPE_CHECKING:
    from app.agents.openapi.store import ApiToolsetStore

AGENT_FIELD = "apiToolsetTools"
MAX_ATTACHED_TOOLS = 500
_ENTRY = re.compile(r"^([0-9a-f]{32}):([A-Za-z0-9_-]{1,64})$")


def by_instance(entries: list[str]) -> dict[str, list[str]]:
    """Instance id -> its attached tool names, in the order given. Malformed entries are skipped."""
    grouped: dict[str, list[str]] = {}
    for entry in entries:
        found = _ENTRY.match(entry) if isinstance(entry, str) else None
        if found:
            grouped.setdefault(found.group(1), []).append(found.group(2))
    return grouped


async def checked_attachments(
    store: ApiToolsetStore, org_id: str, raw: object, *, service_account: bool,
) -> list[str]:
    """The attachments to store. Raises `ApiToolsetError` naming the first problem.

    A service-account agent runs as nobody in particular, so it can only use toolsets
    that need no credential or share one.
    """
    if not isinstance(raw, list):
        raise ApiToolsetError(f"{AGENT_FIELD} must be a list.")
    entries = list(dict.fromkeys(raw))
    if len(entries) > MAX_ATTACHED_TOOLS:
        raise ApiToolsetError(f"An agent can use at most {MAX_ATTACHED_TOOLS} API action operations.")
    for entry in entries:
        if not isinstance(entry, str) or not _ENTRY.match(entry):
            raise ApiToolsetError(f"{entry!r} is not an API action operation (expected '<actionId>:<operation>').")
    for instance_id, tool_names in by_instance(entries).items():
        instance = await store.get_instance(org_id, instance_id)
        if instance is None:
            raise ApiToolsetError(f"API action {instance_id} doesn't exist.")
        offered = {op.tool_name for op in instance.operations}
        missing = [name for name in tool_names if name not in offered]
        if missing:
            raise ApiToolsetError(f"{instance.name} has no operation {missing[0]}.")
        if service_account and not instance.use_shared_credential and instance.auth_scheme.type is not AuthType.NONE:
            raise ApiToolsetError(
                f"A service-account agent can only use API actions with a shared credential, and {instance.name} "
                "has one credential per member."
            )
    return entries
