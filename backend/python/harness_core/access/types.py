"""Model access types shared by engines and gateways (ARCHITECTURE §4.6.1).

E3 brings the gateway route with an org API key: ``AccessRoute`` and its account
and route kinds, the run's pinned model grant (``RunModelGrant``), and the spend
value the gateway charges. P2.4b adds model accounts; A1 the vendor rules
(``harness_core.access.vendor_rules``) and what a call needs checked against them
(``ModelNeed``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from harness_core.access.errors import AccountInvalid
from harness_core.budget.run_budget import RunBudget
from harness_core.budget.types import MICRO_USD
from harness_core.contracts import canonical_digest

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from harness_core.contracts.generated.run_spec import ModelRoute
    from harness_core.priority import PriorityClass

# The id an engine names for the run's model (E5 X-7): neutral, so no engine
# switches its tools or prompts on a vendor's model name.
ENGINE_MODEL_ID = "engine-model"

# GA-15: the roles a run calls models in. Each has its own neutral id; an engine names only these.
ModelRole = Literal["primary", "fast", "subagent", "compaction"]
PRIMARY_ROLE: ModelRole = "primary"
ENGINE_ROLES: tuple[ModelRole, ...] = ("fast", "subagent", "compaction")
ROLE_MODEL_IDS: dict[ModelRole, str] = {
    "primary": ENGINE_MODEL_ID,
    "fast": "engine-model-fast",
    "subagent": "engine-model-subagent",
    "compaction": "engine-model-compaction",
}

Payer = Literal["org", "user", "vendor_plan"]


class ModelApi(StrEnum):
    CHAT_COMPLETIONS = "chat_completions"
    RESPONSES = "responses"
    ANTHROPIC_MESSAGES = "anthropic_messages"
    GEMINI_GENERATE_CONTENT = "gemini_generate_content"


class AccountKind(StrEnum):
    ORG_API_KEY = "org_api_key"
    ORG_CLOUD = "org_cloud"
    USER_API_KEY = "user_api_key"
    USER_PLAN_INJECTED = "user_plan_injected"
    USER_PLAN_SANDBOX_LOGIN = "user_plan_sandbox_login"


class RouteKind(StrEnum):
    GATEWAY = "gateway"
    VENDOR_INJECTED = "vendor_injected"
    VENDOR_SANDBOX_LOGIN = "vendor_sandbox_login"


@dataclass(frozen=True, slots=True)
class AccessRouteRef:
    """A pinned access route, by id and content digest; resolved by the model gateway."""

    route_id: str
    digest: str

    def __post_init__(self) -> None:
        if not self.route_id or not self.digest:
            raise ValueError("Access route reference is incomplete")


@dataclass(frozen=True, slots=True)
class AccountRef:
    kind: AccountKind
    account_id: str


@dataclass(frozen=True, slots=True)
class PinnedRoute:
    """One route of a run's pinned model policy. ``config_key`` is the host's opaque
    name for the configured model (PipesHub: the ``aiModels`` entry's ``modelKey``);
    ``None`` means the host's default for this route. ``account`` pins a model
    account (a user's own key, an org cloud account) instead of the configured
    model's key; who may pin which account is the admitting host's rule (A1)."""

    provider: str
    model: str
    config_key: str | None = None
    config_model: str | None = None
    account: AccountRef | None = None
    # GA-15: a role's route names its model by provider and name, not by key: the host serves it from the first
    # configured model that lists it, as a fallback route is found (``configured_model_key``).
    by_name: bool = False
    # P4.7: the failures that move a run's call here from the route before it (``ModelRoute.on``); empty on the first
    # route and on a route nothing falls back to. Not part of the route's id.
    on: frozenset[str] = frozenset()

    @property
    def route_id(self) -> str:
        """The id in a gateway token's routes digest (Node ``modelRouteId``); a pinned account is part of it."""
        base = f"{self.provider}:{self.model}"
        return base if self.account is None else f"{base}@{self.account.kind.value}:{self.account.account_id}"


def spec_route_id(route: ModelRoute) -> str:
    """A RunSpec model route's id (Node ``modelRouteId``): the item a child's ``access`` grant names."""
    access = route.access
    account = (
        None
        if access is None or access.account_id is None
        else AccountRef(AccountKind(access.account_kind), access.account_id)
    )
    return PinnedRoute(route.provider, route.model, account=account).route_id


def route_digest(routes: tuple[PinnedRoute, ...]) -> str:
    """The digest a gateway token carries for its run's model routes (Node: ``gatewayGrantDigests``)."""
    return canonical_digest(sorted({route.route_id for route in routes}))


@dataclass(frozen=True, slots=True)
class AccessRoute:
    kind: RouteKind
    api: ModelApi
    provider: str
    model: str
    account: AccountRef | None
    pays: Payer
    # Where the route's calls are served, from its configuration (MG-15); ``None``: unknown.
    region: str | None = None

    @property
    def charges_usd(self) -> bool:
        """Budgets are in USD only when the org or the user pays through us (§4.6.2)."""
        return self.pays in ("org", "user")


@dataclass(frozen=True, slots=True)
class GrantedModel:
    """A neutral model id an engine may name, and the pinned route it stands for."""

    model_id: str
    route: PinnedRoute
    # What the call is for (GA-15): every charge of it names this role.
    role: ModelRole = PRIMARY_ROLE


@dataclass(frozen=True, slots=True)
class ModelNeed:
    """What one model call needs checked beyond its route (§4.6.1): the acting user, the
    calling engine and the API the call came in on. ``None``: the grant did not say."""

    actor: str | None
    engine: str | None
    api: ModelApi


@dataclass(frozen=True, slots=True)
class RunModelGrant:
    """What one run may call, as pinned: every pinned route (the token's digest covers
    them all), the neutral ids an engine may name, the run's budget and effort, and
    who acts (``actor``, a user id) from which engine (``engine``, an engine kind or
    ``loop``): the access guard checks both before any route resolves."""

    tenant: str
    run_id: str
    routes: tuple[PinnedRoute, ...]
    models: tuple[GrantedModel, ...]
    budget: RunBudget
    reasoning_effort: str | None = None
    actor: str | None = None
    engine: str | None = None
    # The fields the grant carries beyond those this build reads: the run fields an edition records (Node's
    # ``RecordedRunField``), by name, read by the edition's ports (``carried_value``). Empty: none.
    carried: tuple[tuple[str, str], ...] = ()
    # Which runs' calls go first for a provider's limited slots (G-45); ``None``: a Node that names none.
    priority_class: PriorityClass | None = None

    def need(self, api: ModelApi) -> ModelNeed:
        return ModelNeed(actor=self.actor, engine=self.engine, api=api)

    def model(self, model_id: str) -> GrantedModel | None:
        return next((m for m in self.models if m.model_id == model_id), None)

    def carried_value(self, name: str) -> str | None:
        """The carried field ``name``; ``None``: the grant carries none."""
        return next((value for key, value in self.carried if key == name), None)


def neutral_models(
    routes: tuple[PinnedRoute, ...], role_routes: Mapping[str, PinnedRoute] | None = None
) -> tuple[GrantedModel, ...]:
    """The ids an engine sees: one per role (GA-15). ``engine-model`` is the first route; each engine role's id is
    its own route, or the first route when the role has none or when that route is pinned to a model account (A1: a
    person's own key pays for nothing but the model it was pinned for). Fallback routes get no id: the engine never names
    one; the gateway moves the engine's own model's call there when its provider is down, by the run's pinned policy
    (G-48, ``ModelGateway(fallback=...)``), and the move is journaled and shown."""
    if not routes:
        return ()
    primary = routes[0]
    roles = role_routes if role_routes is not None and primary.account is None else {}
    return (
        GrantedModel(ENGINE_MODEL_ID, primary),
        *(GrantedModel(ROLE_MODEL_IDS[role], roles.get(role, primary), role) for role in ENGINE_ROLES),
    )




ORG_OWNER = "org"
ValidationState = Literal["unvalidated", "validated"]
_SEGMENT = re.compile(r"^[A-Za-z0-9._:@-]{1,128}$")


def _segment(value: str) -> str:
    if not _SEGMENT.match(value) or value in (".", ".."):
        raise AccountInvalid("account owners, providers and ids are path-safe segments")
    return value


def user_account_ref(user_id: str, provider: str) -> AccountRef:
    """A user's own API key for one provider: one per user and provider."""
    return AccountRef(AccountKind.USER_API_KEY, f"users/{_segment(user_id)}/{_segment(provider)}")


def org_account_ref(account_id: str) -> AccountRef:
    """An org cloud account (an Azure OpenAI deployment, Bedrock, Vertex)."""
    return AccountRef(AccountKind.ORG_CLOUD, f"cloud/{_segment(account_id)}")


def account_owner(ref: AccountRef) -> str:
    """The user id of a user's account, ``ORG_OWNER`` for an org account."""
    parts = ref.account_id.split("/")
    if ref.kind is AccountKind.USER_API_KEY and len(parts) == 3 and parts[0] == "users":
        return _segment(parts[1])
    if ref.kind is AccountKind.ORG_CLOUD and len(parts) == 2 and parts[0] == "cloud":
        _ = _segment(parts[1])
        return ORG_OWNER
    raise AccountInvalid("not a model account reference")


def last_four(secret: str) -> str:
    """What a read may show of a key: its last four characters, never more than a quarter of it."""
    return secret[-4:] if len(secret) >= 16 else ""


@dataclass(frozen=True, slots=True)
class ModelAccount:
    """A model account as reads see it: metadata only, never the secret."""

    ref: AccountRef
    owner: str
    provider: str
    label: str | None
    last4: str
    version: str
    created_at: datetime
    rotated_at: datetime
    validation: ValidationState = "unvalidated"


@dataclass(frozen=True, slots=True)
class Spend:
    """A run's spend: money in integer micro-USD (no float drift across charges), tokens."""

    usd_micros: int = 0
    tokens: int = 0
    stopped: bool = False

    @property
    def usd(self) -> float:
        return self.usd_micros / MICRO_USD


__all__ = [
    "ENGINE_MODEL_ID",
    "ENGINE_ROLES",
    "PRIMARY_ROLE",
    "ROLE_MODEL_IDS",
    "ModelRole",
    "MICRO_USD",
    "AccessRoute",
    "AccessRouteRef",
    "AccountKind",
    "AccountRef",
    "ModelAccount",
    "ORG_OWNER",
    "ValidationState",
    "GrantedModel",
    "ModelApi",
    "ModelNeed",
    "PinnedRoute",
    "Payer",
    "RouteKind",
    "RunBudget",
    "RunModelGrant",
    "Spend",
    "account_owner",
    "last_four",
    "neutral_models",
    "org_account_ref",
    "route_digest",
    "spec_route_id",
    "user_account_ref",
]
