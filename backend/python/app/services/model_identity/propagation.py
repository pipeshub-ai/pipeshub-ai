"""Carries the call context across a service hop inside the service token.

Extraction and parsing run in their own processes, so the ContextVar the caller
bound does not reach them. The caller adds a small ``pcp`` claim to the service
token it already mints; the callee rebuilds the context from the *verified*
claims. Only ids travel (no email, no name, no groups): the callee looks groups
up itself, the same way the caller would.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from app.services.model_identity import metrics
from app.services.model_identity.call_context import ModelCallContext, peek_call_context
from app.services.model_identity.principal import (
    Principal,
    PrincipalKind,
    service_account_principal,
    service_principal,
    user_principal,
)
from app.services.model_identity.runtime import call_scope
from app.services.model_identity.workload import Workload, workload_class

logger = logging.getLogger(__name__)

PCP_CLAIM = "pcp"
PCP_VERSION = 1
_MAX_ID_LENGTH = 256

_KINDS = {kind.value: kind for kind in PrincipalKind}


def encode_pcp(ctx: ModelCallContext | None = None) -> dict[str, Any] | None:
    """The claim for the context bound now, or ``None`` when nothing is bound."""
    ctx = ctx or peek_call_context()
    if ctx is None:
        return None
    principal = ctx.principal
    claim: dict[str, Any] = {
        "v": PCP_VERSION,
        "kind": principal.kind.value,
        "sub": principal.subject_id,
        "org": principal.org_id,
        "wl": ctx.workload.value,
    }
    optional = {
        "rid": ctx.request_id,
        "rec": ctx.record_id,
        "con": ctx.connector_id,
        "agt": ctx.agent_id,
    }
    claim.update({key: value for key, value in optional.items() if value})
    return claim


class PropagatedContext:
    """What a callee learned from a verified claim."""

    __slots__ = ("principal", "origin_workload", "request_id", "record_id", "connector_id", "agent_id")

    def __init__(
        self,
        principal: Principal,
        origin_workload: Workload,
        request_id: str | None,
        record_id: str | None,
        connector_id: str | None,
        agent_id: str | None,
    ) -> None:
        self.principal = principal
        self.origin_workload = origin_workload
        self.request_id = request_id
        self.record_id = record_id
        self.connector_id = connector_id
        self.agent_id = agent_id


def decode_pcp(claims: Mapping[str, Any]) -> PropagatedContext | None:
    """Parse the claim from *already verified* token claims.

    Returns ``None`` for anything that is not a well-formed claim of a known
    version for the same org as the token, so a bad or stale claim degrades to
    the callee's own service identity and never to a guessed one.
    """
    raw = claims.get(PCP_CLAIM)
    if raw is None:
        return None
    if not isinstance(raw, Mapping) or raw.get("v") != PCP_VERSION:
        return _reject("version")
    kind = _KINDS.get(str(raw.get("kind")))
    subject = _identifier(raw.get("sub"))
    org = _identifier(raw.get("org"))
    if kind is None or subject is None:
        return _reject("shape")
    token_org = claims.get("orgId")
    if token_org and org and org != str(token_org):
        # A token minted for one org must not name a person of another.
        return _reject("org_mismatch")
    if kind is not PrincipalKind.SERVICE and not org:
        return _reject("shape")
    if kind is not PrincipalKind.SERVICE and not token_org:
        # A person or agent is only believed inside the org the token was minted for.
        return _reject("org_mismatch")
    try:
        workload = Workload(str(raw.get("wl")))
    except ValueError:
        return _reject("workload")
    if kind is PrincipalKind.USER:
        principal = user_principal({"userId": subject}, org)
    elif kind is PrincipalKind.SERVICE_ACCOUNT:
        principal = service_account_principal(subject, org or "")
    else:
        principal = service_principal(subject, org or "")
    return PropagatedContext(
        principal=principal,
        origin_workload=workload,
        request_id=_identifier(raw.get("rid")),
        record_id=_identifier(raw.get("rec")),
        connector_id=_identifier(raw.get("con")),
        agent_id=_identifier(raw.get("agt")),
    )


def scope_from_claims(
    claims: Mapping[str, Any],
    workload: Workload,
    *,
    fallback_service: str,
    config_service: Any = None,  # noqa: ANN401 - an untyped dependency-injection object
    container: Any = None,  # noqa: ANN401 - an untyped dependency-injection object
) -> call_scope:
    """``call_scope`` for a service route, from its verified token claims.

    The route's own ``workload`` is used unless the caller's class differs (an
    interactive upload being parsed stays interactive), in which case the
    caller's workload is kept so the gateway still sees the right class.
    """
    propagated = decode_pcp(claims)
    if propagated is None:
        org = str(claims.get("orgId") or "")
        return call_scope(
            service_principal(fallback_service, org),
            workload,
            config_service=config_service,
            container=container,
        )
    if workload_class(propagated.origin_workload) is not workload_class(workload):
        workload = propagated.origin_workload
    return call_scope(
        propagated.principal,
        workload,
        config_service=config_service,
        container=container,
        request_id=propagated.request_id,
        record_id=propagated.record_id,
        connector_id=propagated.connector_id,
        agent_id=propagated.agent_id,
    )


def _identifier(value: Any) -> str | None:  # noqa: ANN401 - a value of unknown shape (JSON or user input)
    if value is None or isinstance(value, (bool, int, float, list, dict)):
        return None
    text = str(value).strip()
    return text if text and len(text) <= _MAX_ID_LENGTH else None


def _reject(reason: str) -> None:
    metrics.inc(metrics.IDENTITY_MISSING, f"bad_pcp_{reason}")
    logger.warning("ignoring an unusable identity claim (%s)", reason)
