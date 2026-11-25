"""An engine agent's settings on its document (AG1), checked when they are written.

``engineProfile`` is the agent's layer of E4a's profile narrowing: present, the agent's durable runs
use an engine; absent, the loop. ``modelAccountId`` pins the org cloud account (``cloud/<id>``) its
runs reach the model with; a person's own key is never pinned on an agent, admission picks the
actor's own key per run. A write is refused, never trimmed, when the narrowing is invalid or asks
for more than the org ceiling allows; admission checks again at every run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from app.services.graph_db.common.utils import (
    AGENT_ENGINE_PROFILE_FIELD,
    AGENT_MODEL_ACCOUNT_FIELD,
)
from harness_core.access.errors import AccountInvalid
from harness_core.access.types import (
    AccessRouteRef,
    AccountKind,
    AccountRef,
    account_owner,
)
from harness_core.engines.errors import (
    ProfileCeilingUnavailable,
    ProfileInvalid,
    ProfileWidening,
)
from harness_core.engines.profiles import constraint_from_wire, constraint_json

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from pydantic import JsonValue

    from harness_core.engines.types import EngineProfile, ProfileConstraint

_log = logging.getLogger(__name__)

# The resolution only answers whether the narrowing fits under the ceiling; no route is pinned here.
_WRITE_CHECK_ACCESS = AccessRouteRef("agent-definition", "write-check")


class CeilingCheck(Protocol):
    async def resolve_engine_profile(
        self,
        org_id: str,
        agent: ProfileConstraint | None,
        request: ProfileConstraint | None,
        *,
        access: AccessRouteRef,
    ) -> EngineProfile: ...


class EngineSettingsRefused(Exception):
    """A refused write: ``code`` is the reason (``ENGINE_PROFILE_INVALID``, ``ENGINE_PROFILE_WIDENING``,
    ``MODEL_ACCOUNT_INVALID``, ``ENGINE_PROFILE_UNAVAILABLE``, ``ENGINE_AGENT_ADMIN_REQUIRED``), ``status`` the
    HTTP answer."""

    def __init__(
        self, code: str, message: str, *, status: int = 400, dimension: str | None = None, layer: str | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.dimension = dimension
        self.layer = layer


@dataclass(frozen=True, slots=True)
class AgentEngineSettings:
    """What the agent document stores: the canonical narrowing (``None``: the loop) and the cloud pin."""

    engine_profile: dict[str, JsonValue] | None
    model_account_id: str | None

    def document_fields(self) -> dict[str, object]:
        return {AGENT_ENGINE_PROFILE_FIELD: self.engine_profile, AGENT_MODEL_ACCOUNT_FIELD: self.model_account_id}


def _cloud_account(value: object) -> str:
    if not isinstance(value, str):
        raise EngineSettingsRefused("MODEL_ACCOUNT_INVALID", "modelAccountId must be an org cloud account id")
    try:
        _ = account_owner(AccountRef(AccountKind.ORG_CLOUD, value))
    except AccountInvalid:
        raise EngineSettingsRefused(
            "MODEL_ACCOUNT_INVALID",
            "modelAccountId must name an organization cloud account (cloud/<id>); an agent never pins a person's key",
        ) from None
    return value


async def agent_engine_settings(
    org_id: str,
    agent_key: str,
    body: Mapping[str, object],
    stored: Mapping[str, object] | None,
    ceilings: Callable[[], CeilingCheck],
    *,
    caller_is_admin: bool,
) -> AgentEngineSettings | None:
    """The engine settings to write for ``body``, or ``None`` when it names neither field (a loop agent's
    write builds no ceiling source and reads no configuration). A field the body does not name is taken
    from ``stored``.

    Turning a loop agent into an engine agent is an org admin's decision (P2.11e): the agent then runs
    code in a sandbox on the org's model accounts. Once it is one, its editors may change its narrowing
    within the org ceiling, or turn it back into a loop agent."""
    names_profile = AGENT_ENGINE_PROFILE_FIELD in body
    names_account = AGENT_MODEL_ACCOUNT_FIELD in body
    if not names_profile and not names_account:
        return None
    previous = stored or {}
    raw_profile = body[AGENT_ENGINE_PROFILE_FIELD] if names_profile else previous.get(AGENT_ENGINE_PROFILE_FIELD)
    raw_account = body[AGENT_MODEL_ACCOUNT_FIELD] if names_account else previous.get(AGENT_MODEL_ACCOUNT_FIELD)
    try:
        if raw_profile is not None and previous.get(AGENT_ENGINE_PROFILE_FIELD) is None and not caller_is_admin:
            raise EngineSettingsRefused(
                "ENGINE_AGENT_ADMIN_REQUIRED",
                "Only an organization admin can make an agent run on an engine",
                status=403,
            )
        if raw_profile is None:
            if names_account and raw_account is not None:
                raise EngineSettingsRefused(
                    "MODEL_ACCOUNT_INVALID", "modelAccountId is for an agent that runs on an engine (engineProfile)"
                )
            return AgentEngineSettings(None, None)
        try:
            constraint = constraint_from_wire(raw_profile)
        except ProfileInvalid as invalid:
            raise EngineSettingsRefused("ENGINE_PROFILE_INVALID", f"engineProfile is not valid: {invalid}") from None
        try:
            _ = await ceilings().resolve_engine_profile(org_id, constraint, None, access=_WRITE_CHECK_ACCESS)
        except ProfileWidening as widening:
            raise EngineSettingsRefused(
                "ENGINE_PROFILE_WIDENING",
                f"engineProfile asks for more than the organization allows ({widening.field})",
                dimension=widening.field,
                layer=widening.layer,
            ) from None
        except (ProfileInvalid, ProfileCeilingUnavailable):
            raise EngineSettingsRefused(
                "ENGINE_PROFILE_UNAVAILABLE",
                "The organization's engine limits could not be read; try again",
                status=503,
            ) from None
        account = None if raw_account is None else _cloud_account(raw_account)
        admits_cloud = constraint.account_kinds is None or AccountKind.ORG_CLOUD in constraint.account_kinds
        if account is not None and not admits_cloud:
            if names_account:
                raise EngineSettingsRefused(
                    "MODEL_ACCOUNT_INVALID",
                    "modelAccountId needs an engineProfile whose accountKinds include org_cloud",
                    dimension="accountKinds",
                )
            # The editor chose kinds without org_cloud: a kept pin would refuse every later run.
            account = None
        return AgentEngineSettings(constraint_json(constraint), account)
    except EngineSettingsRefused as refused:
        _log.warning(
            "agent.engine_profile.refused",
            extra={
                "orgId": org_id,
                "agentKey": agent_key,
                "reasonCode": refused.code,
                "dimension": refused.dimension,
                "layer": refused.layer,
            },
        )
        raise


__all__ = ["AgentEngineSettings", "CeilingCheck", "EngineSettingsRefused", "agent_engine_settings"]
