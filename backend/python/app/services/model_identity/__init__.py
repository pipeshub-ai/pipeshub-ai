"""Which identity a model call is made under, and how a gateway is told.

Layers, each with one job:

* ``principal`` / ``workload`` / ``call_context``: who and what for, bound at a boundary.
* ``groups``: lazy group membership for that person.
* ``settings`` / ``policy_store``: org governance and per-connection forwarding config.
* ``projector``: applies governance, producing the minimal ``ForwardedIdentity``.
* ``forwarders``: render a ``ForwardedIdentity`` as headers for one kind of gateway.
* ``transport``: installs the render step on every model HTTP client.

Design and data flows: ``docs/llm-gateway-identity.md``.
"""

from app.services.model_identity.call_context import (
    ModelCallContext,
    bind_call_context,
    current_call_context,
    peek_call_context,
    workload_scope,
)
from app.services.model_identity.groups import (
    GroupMembership,
    GroupMembershipResolver,
    GroupsHandle,
    NoopGroupResolver,
)
from app.services.model_identity.principal import (
    Principal,
    PrincipalKind,
    service_account_principal,
    service_principal,
    user_principal,
)
from app.services.model_identity.workload import Workload, WorkloadClass

__all__ = [
    "GroupMembership",
    "GroupMembershipResolver",
    "GroupsHandle",
    "ModelCallContext",
    "NoopGroupResolver",
    "Principal",
    "PrincipalKind",
    "Workload",
    "WorkloadClass",
    "bind_call_context",
    "current_call_context",
    "peek_call_context",
    "service_account_principal",
    "service_principal",
    "user_principal",
    "workload_scope",
]
