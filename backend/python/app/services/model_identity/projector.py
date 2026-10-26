"""Applies the org's governance policy to a call context.

``IdentityProjector.project`` is the only place the policy is applied. A
forwarder receives the resulting ``ForwardedIdentity`` and never sees the raw
principal, so a new wire format cannot leak a field the admin turned off.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.services.model_identity import metrics
from app.services.model_identity.principal import Principal, PrincipalKind
from app.services.model_identity.sanitize import clean_text, optional_clean
from app.services.model_identity.workload import Workload, WorkloadClass, workload_class

if TYPE_CHECKING:
    from app.services.model_identity.call_context import ModelCallContext
    from app.services.model_identity.groups import GroupMembership
    from app.services.model_identity.settings import IdentityGovernancePolicy

_PSEUDONYM_DOMAIN = b"pipeshub-identity-pseudonym-v1"
_PSEUDONYM_LENGTH = 32


@dataclass(frozen=True, slots=True)
class ForwardedIdentity:
    subject: str
    subject_kind: str
    org_id: str
    workload: str
    workload_class: str
    purpose: str
    email: str | None = None
    display_name: str | None = None
    group_ids: tuple[str, ...] = ()
    groups_complete: bool = True
    groups_source: str = "none"
    request_id: str | None = None
    conversation_id: str | None = None
    agent_id: str | None = None

    @property
    def is_user(self) -> bool:
        return self.subject_kind == PrincipalKind.USER.value


def pseudonymize(value: str, key: bytes) -> str:
    """Stable, non-reversible stand-in for ``value`` under a deployment secret."""
    subkey = hmac.new(key, _PSEUDONYM_DOMAIN, hashlib.sha256).digest()
    digest = hmac.new(subkey, value.encode(), hashlib.sha256).hexdigest()
    return f"u_{digest[:_PSEUDONYM_LENGTH]}"


class IdentityProjector:
    def __init__(self, *, pseudonym_key: bytes | None = None) -> None:
        self._pseudonym_key = pseudonym_key

    def project(
        self,
        ctx: ModelCallContext,
        governance: IdentityGovernancePolicy,
        *,
        groups: GroupMembership | None = None,
        purpose: str = "main",
    ) -> ForwardedIdentity:
        principal = ctx.principal
        workload = ctx.workload

        if self._acts_as_service(principal, workload, governance):
            return self._service_identity(ctx, governance, principal, purpose)

        if principal.kind is PrincipalKind.SERVICE_ACCOUNT:
            return self._base(
                ctx,
                purpose,
                subject=f"{governance.service_identity_prefix}:agent:{clean_text(principal.subject_id)}",
                kind=PrincipalKind.SERVICE_ACCOUNT,
            )

        subject = self._user_subject(principal, governance)
        if governance.pseudonymize:
            key = ctx.pseudonym_key or self._pseudonym_key
            if key is None:
                # Never fall back to the raw id when the admin asked for a pseudonym.
                metrics.inc(metrics.IDENTITY_MISSING, "no_pseudonym_key")
                return self._service_identity(ctx, governance, principal, purpose)
            subject = pseudonymize(subject, key)

        group_ids, complete, source = self._groups(groups, governance)
        return self._base(
            ctx,
            purpose,
            subject=clean_text(subject),
            kind=PrincipalKind.USER,
            email=optional_clean(principal.email) if governance.include_email else None,
            display_name=(
                optional_clean(principal.display_name) if governance.include_display_name else None
            ),
            group_ids=group_ids,
            groups_complete=complete,
            groups_source=source,
        )

    @staticmethod
    def _acts_as_service(
        principal: Principal, workload: Workload, governance: IdentityGovernancePolicy
    ) -> bool:
        if principal.kind is PrincipalKind.SERVICE:
            return True
        if principal.kind is not PrincipalKind.USER:
            return False
        # Only interactive calls carry a person. A probe an admin starts or work a
        # user's upload triggers must not spend that person's gateway budget, and a
        # refusal for them must not fail the probe.
        if workload_class(workload) is not WorkloadClass.INTERACTIVE:
            return True
        return (
            workload is Workload.QUERY_EMBEDDING
            and governance.query_embedding_identity == "service"
        )

    def _service_identity(
        self,
        ctx: ModelCallContext,
        governance: IdentityGovernancePolicy,
        principal: Principal,
        purpose: str,
    ) -> ForwardedIdentity:
        if principal.kind is PrincipalKind.SERVICE and principal.service_name:
            name = principal.service_name
        else:
            name = ctx.workload.value
        return self._base(
            ctx,
            purpose,
            subject=f"{governance.service_identity_prefix}:{clean_text(name)}",
            kind=PrincipalKind.SERVICE,
        )

    @staticmethod
    def _user_subject(principal: Principal, governance: IdentityGovernancePolicy) -> str:
        if governance.subject_source == "email" and principal.email:
            return principal.email
        if governance.subject_source == "idpObjectId" and principal.idp_object_id:
            return principal.idp_object_id
        return principal.subject_id

    @staticmethod
    def _groups(
        groups: GroupMembership | None, governance: IdentityGovernancePolicy
    ) -> tuple[tuple[str, ...], bool, str]:
        if not governance.groups_enabled:
            return (), True, "disabled"
        if groups is None:
            return (), False, "pending"
        ids = sorted({clean_text(g) for g in groups.ids if clean_text(g)})
        if governance.groups_allow_list:
            ids = [g for g in ids if g in governance.groups_allow_list]
        truncated = len(ids) > governance.max_forwarded_groups
        ids = ids[: governance.max_forwarded_groups]
        return tuple(ids), groups.complete and not truncated, groups.source

    @staticmethod
    def _base(
        ctx: ModelCallContext,
        purpose: str,
        *,
        subject: str,
        kind: PrincipalKind,
        email: str | None = None,
        display_name: str | None = None,
        group_ids: tuple[str, ...] = (),
        groups_complete: bool = True,
        groups_source: str = "none",
    ) -> ForwardedIdentity:
        return ForwardedIdentity(
            subject=subject,
            subject_kind=kind.value,
            org_id=clean_text(ctx.principal.org_id),
            workload=ctx.workload.value,
            workload_class=ctx.workload_class.value,
            purpose=clean_text(purpose),
            email=email,
            display_name=display_name,
            group_ids=group_ids,
            groups_complete=groups_complete,
            groups_source=groups_source,
            request_id=optional_clean(ctx.request_id),
            conversation_id=optional_clean(ctx.conversation_id),
            agent_id=optional_clean(ctx.agent_id),
        )
