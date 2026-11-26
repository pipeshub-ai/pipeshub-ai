"""``X-PipesHub-Resolved``: a compact JWS Node signs with the gateway-token keyring under its own audience.

The payload is a projection of the turn's pin (``contracts/control-plane/resolved-turn.schema.json``), read
strictly: a claim this version does not know, or one it needs and does not find, is malformed. A token is bound
to the org, user, agent and conversation it was signed for, so one copied from another turn serves nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, cast, get_args

from app.harness.legacy.errors import ResolvedTurnRefusalReason, ResolvedTurnRefused
from harness_core.effects.flow import FlowLevel, FlowRule
from harness_core.effects.policy import Threshold
from harness_core.gateway.token import RETIRED_KEY_RETENTION_MS
from harness_core.jws import JwsFailure, JwsProfile, JwsVerifier

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.clock import Clock
    from harness_core.gateway.ports import GatewayKeySource

RESOLVED_TURN_HEADER: Final = "x-pipeshub-resolved"
RESOLVED_TURN_ISSUER: Final = "pipeshub-node/agent-harness"
RESOLVED_TURN_AUDIENCE: Final = "pipeshub-query/resolved-turn"
RESOLVED_TURN_VERSION: Final = 1
RESOLVED_TURN_TTL_S: Final = 60
# Node replicas' clocks may disagree a little; a longer-lived token was not minted by the issuer.
MAX_RESOLVED_TURN_LIFETIME_S: Final = 120
MAX_RESOLVED_TURN_LENGTH: Final = 16 * 1024

_REQUIRED: Final = frozenset(
    {"iss", "aud", "iat", "exp", "ver", "org", "sub", "agent", "cid", "pin", "stages", "flow"}
)
_OPTIONAL: Final = frozenset({"access", "bindings"})
_ID_MAX: Final = 256
_PATHS: Final = ("legacy", "durable")
_OWNER_KINDS: Final = ("user", "agent")

_REASONS: Final[dict[JwsFailure, ResolvedTurnRefusalReason]] = {
    "missing": "resolved_malformed",
    "malformed": "resolved_malformed",
    "algorithm": "resolved_algorithm",
    "header": "resolved_malformed",
    "unknown_key": "resolved_unknown_key",
    "key_type": "resolved_unknown_key",
    "signature": "resolved_signature",
    "issuer": "resolved_issuer",
    "audience": "resolved_audience",
    "claims": "resolved_malformed",
    "lifetime": "resolved_malformed",
    "expired": "resolved_expired",
}


@dataclass(frozen=True, slots=True)
class ResolvedRouting:
    path: str
    reason: str | None


@dataclass(frozen=True, slots=True)
class ResolvedScope:
    agent_key: str | None
    project_id: str | None
    agent_owner: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedPin:
    """What the turn was resolved from, for the log line; the pin itself does not fit a header."""

    digest: str
    head: int
    routing: ResolvedRouting
    scope: ResolvedScope


@dataclass(frozen=True, slots=True)
class ResolvedBinding:
    """An installation the turn holds ready with a credential binding; ``owner`` is ``user:<id>`` or ``agent:<key>``."""

    installation: str
    owner: str
    ref: str


@dataclass(frozen=True, slots=True)
class ResolvedTurn:
    org: str
    sub: str
    agent: str | None
    cid: str
    pin: ResolvedPin
    stages: Mapping[str, bool]
    flow: tuple[FlowRule, ...]
    access_run: bool
    bindings: tuple[ResolvedBinding, ...]

    def bound_installations(self, owner: str) -> frozenset[str]:
        return frozenset(binding.installation for binding in self.bindings if binding.owner == owner)


def binding_owner(owner_id: str, *, agent: bool) -> str:
    """The owner a binding names for the id the route keeps a credential under (a service-account agent's key,
    else the executing user)."""
    return f"{'agent' if agent else 'user'}:{owner_id}"


def _malformed() -> ResolvedTurnRefused:
    return ResolvedTurnRefused("resolved_malformed")


def _object(value: object, required: frozenset[str], optional: frozenset[str] = frozenset()) -> dict[str, object]:
    if not isinstance(value, dict):
        raise _malformed()
    found = cast("dict[str, object]", value)
    if not required <= frozenset(found) <= required | optional:
        raise _malformed()
    return found


def _text(value: object, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value or len(value) > _ID_MAX:
        raise _malformed()
    return value


def _required_text(value: object) -> str:
    text = _text(value)
    assert text is not None
    return text


def _items(value: object) -> list[object]:
    if not isinstance(value, list):
        raise _malformed()
    return cast("list[object]", value)


def _pin(value: object) -> ResolvedPin:
    pin = _object(value, frozenset({"digest", "head", "routing", "scope"}))
    head = pin["head"]
    if isinstance(head, bool) or not isinstance(head, int) or head < 0:
        raise _malformed()
    routing = _object(pin["routing"], frozenset({"path", "reason"}))
    if routing["path"] not in _PATHS:
        raise _malformed()
    scope = _object(pin["scope"], frozenset({"agentKey", "projectId"}), frozenset({"agentOwner"}))
    return ResolvedPin(
        _required_text(pin["digest"]),
        head,
        ResolvedRouting(cast("str", routing["path"]), _text(routing["reason"], nullable=True)),
        ResolvedScope(
            _text(scope["agentKey"], nullable=True),
            _text(scope["projectId"], nullable=True),
            _text(scope["agentOwner"]) if "agentOwner" in scope else None,
        ),
    )


def _stages(value: object) -> Mapping[str, bool]:
    if not isinstance(value, dict):
        raise _malformed()
    stages = cast("dict[str, object]", value)
    if any(not name or not isinstance(on, bool) for name, on in stages.items()):
        raise _malformed()
    return MappingProxyType(cast("dict[str, bool]", dict(stages)))


def _flow(value: object) -> tuple[FlowRule, ...]:
    rules: list[FlowRule] = []
    for item in _items(value):
        rule = _object(item, frozenset({"level", "above"}))
        if rule["level"] not in get_args(FlowLevel) or rule["above"] not in get_args(Threshold):
            raise _malformed()
        rules.append(FlowRule(cast("FlowLevel", rule["level"]), cast("Threshold", rule["above"])))
    return tuple(rules)


def _access_run(payload: dict[str, object]) -> bool:
    if "access" not in payload:
        return False
    run = _object(payload["access"], frozenset({"run"}))["run"]
    if not isinstance(run, bool):
        raise _malformed()
    return run


def _bindings(payload: dict[str, object]) -> tuple[ResolvedBinding, ...]:
    bindings: list[ResolvedBinding] = []
    for item in _items(payload.get("bindings", [])):
        binding = _object(item, frozenset({"installation", "owner", "ref"}))
        owner = _required_text(binding["owner"])
        kind, _, owner_id = owner.partition(":")
        if kind not in _OWNER_KINDS or not owner_id:
            raise _malformed()
        bindings.append(ResolvedBinding(_required_text(binding["installation"]), owner, _required_text(binding["ref"])))
    return tuple(bindings)


def resolved_turn_of(payload: dict[str, object]) -> ResolvedTurn:
    """The turn a verified payload names. The version is read first: another version's claims are not this one's
    to judge."""
    if "ver" not in payload:
        raise _malformed()
    if type(payload["ver"]) is not int or payload["ver"] != RESOLVED_TURN_VERSION:
        raise ResolvedTurnRefused("resolved_version")
    claims = _object(payload, _REQUIRED, _OPTIONAL)
    return ResolvedTurn(
        org=_required_text(claims["org"]),
        sub=_required_text(claims["sub"]),
        agent=_text(claims["agent"], nullable=True),
        cid=_required_text(claims["cid"]),
        pin=_pin(claims["pin"]),
        stages=_stages(claims["stages"]),
        flow=_flow(claims["flow"]),
        access_run=_access_run(claims),
        bindings=_bindings(claims),
    )


def _refusal(failure: JwsFailure) -> ResolvedTurnRefused:
    return ResolvedTurnRefused(_REASONS[failure])


class ResolvedTurnVerifier:
    """One per process: the keys are the gateway keyring's published document, cached by ``keys``."""

    def __init__(self, keys: GatewayKeySource, clock: Clock) -> None:
        profile = JwsProfile(
            RESOLVED_TURN_ISSUER,
            RESOLVED_TURN_AUDIENCE,
            MAX_RESOLVED_TURN_LIFETIME_S,
            MAX_RESOLVED_TURN_LENGTH,
            RETIRED_KEY_RETENTION_MS,
        )
        self._jws = JwsVerifier(keys, clock, profile, _refusal)

    async def verify(
        self, token: str, *, org_id: str, user_id: str, agent_key: str | None, conversation_id: str | None
    ) -> ResolvedTurn:
        """The turn ``token`` carries, when it was signed for this org, user, agent and conversation and names
        the legacy path; ``ResolvedTurnRefused`` otherwise. The identity given is the request's own verified one."""
        turn = (await self._jws.verify(token, resolved_turn_of)).claims
        if turn.pin.routing.path != "legacy":
            raise ResolvedTurnRefused("resolved_routing")
        if turn.org != org_id or turn.sub != user_id:
            raise ResolvedTurnRefused("resolved_subject_mismatch")
        if turn.agent != agent_key:
            raise ResolvedTurnRefused("resolved_agent_mismatch")
        if conversation_id is None or turn.cid != conversation_id:
            raise ResolvedTurnRefused("resolved_conversation_mismatch")
        return turn


__all__ = [
    "MAX_RESOLVED_TURN_LENGTH",
    "MAX_RESOLVED_TURN_LIFETIME_S",
    "RESOLVED_TURN_AUDIENCE",
    "RESOLVED_TURN_HEADER",
    "RESOLVED_TURN_ISSUER",
    "RESOLVED_TURN_TTL_S",
    "RESOLVED_TURN_VERSION",
    "ResolvedBinding",
    "ResolvedPin",
    "ResolvedRouting",
    "ResolvedScope",
    "ResolvedTurn",
    "ResolvedTurnVerifier",
    "binding_owner",
    "resolved_turn_of",
]
