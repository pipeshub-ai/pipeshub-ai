"""What a connection would send for a given person, for the admin to check before saving.

Runs the real projector and forwarder, so the preview cannot drift from what the
hook sends. Credentials are redacted; nothing here makes a model call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.services.model_identity.call_context import ModelCallContext
from app.services.model_identity.forwarders.registry import get_forwarder_registry
from app.services.model_identity.projector import IdentityProjector
from app.services.model_identity.sanitize import is_forbidden_header_name
from app.services.model_identity.workload import Workload, workload_class

if TYPE_CHECKING:
    from app.services.model_identity.groups import GroupMembership
    from app.services.model_identity.principal import Principal
    from app.services.model_identity.settings import (
        ConnectionForwardingSettings,
        IdentityGovernancePolicy,
    )

REDACTED = "[redacted]"
_SECRET_HEADER_HINTS = ("key", "token", "secret", "password", "auth")


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Hide the value of any header that looks like a credential, by name.

    Placeholders cannot render a secret (only identity fields exist), so a header
    carries one only if an admin typed it into a template. That is caught by the
    name hints below, not guaranteed: a credential in a header named outside them
    would show in the preview an admin sees.
    """
    return {
        name: REDACTED
        if is_forbidden_header_name(name) or any(hint in name.lower() for hint in _SECRET_HEADER_HINTS)
        else value
        for name, value in headers.items()
    }


def build_preview(
    *,
    principal: Principal,
    workload: Workload,
    settings: ConnectionForwardingSettings,
    governance: IdentityGovernancePolicy,
    pseudonym_key: bytes | None,
    groups: GroupMembership | None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    ctx = ModelCallContext(
        principal=principal,
        workload=workload,
        conversation_id=conversation_id,
        governance=governance,
        pseudonym_key=pseudonym_key,
    )
    identity = IdentityProjector(pseudonym_key=pseudonym_key).project(ctx, governance, groups=groups)
    notes = []
    result: dict[str, Any] = {
        "forwarding": settings.to_dict(redact=True),
        "principal": {"kind": principal.kind.value, "orgId": principal.org_id},
        "workload": {"name": workload.value, "class": workload_class(workload).value},
        "identity": {
            "subject": identity.subject,
            "subjectKind": identity.subject_kind,
            "email": identity.email,
            "displayName": identity.display_name,
            "groups": list(identity.group_ids),
            "groupsSource": identity.groups_source,
            "groupsComplete": identity.groups_complete,
        },
        "headers": None,
        "notes": notes,
    }
    if not settings.enabled:
        notes.append("Identity forwarding is off for this connection, so nothing is sent.")
        return result
    forwarder = get_forwarder_registry().get(settings)
    if forwarder is None:
        notes.append(f"The '{settings.mode}' format is not available in this edition, so nothing is sent.")
        return result
    rendered = forwarder.render(identity, settings)
    result["headers"] = redact_headers(rendered.as_dict())
    result["removedHeaders"] = list(rendered.remove)
    if identity.is_user and not identity.groups_complete:
        notes.append("Groups are incomplete: they were truncated or not all sources answered.")
    if identity.is_user and governance.groups_enabled and not identity.group_ids:
        notes.append(
            "No groups were resolved for this person: there is no group source in this edition, "
            "or none has been synced for them."
        )
    return result
