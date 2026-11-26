"""``RunModelGrants`` over Node: the run's pinned model grant, read with the run's own gateway token.

``GET /api/v1/agent-harness/internal/runs/{runId}/model-grant``: Node verifies
the token, checks the attempt still holds the lease and answers the pin's model
policy, effort and budgets (E3 NG-2). The model gateway therefore holds no worker
credential. A grant is kept per (run, attempt, fence, routes digest) for a
minute or until the token expires, so a stream of calls asks Node once.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Final, cast, override
from urllib.parse import quote

from app.harness.journal_node.client import INTERNAL_PREFIX, error_code
from app.harness.journal_node.http import NodeServiceClient
from harness_core.access.errors import GrantUnavailable
from harness_core.access.types import (
    ENGINE_ROLES,
    AccountKind,
    AccountRef,
    PinnedRoute,
    RunBudget,
    RunModelGrant,
    neutral_models,
)
from harness_core.gateway.types import RunAttemptBinding
from harness_core.models.policy import TRIGGERS
from harness_core.priority import parse_class

if TYPE_CHECKING:
    from collections.abc import Callable

    import httpx

    from harness_core.gateway.types import GatewayClaims

CACHE_TTL_S = 60.0
_REFUSED = {401: 401, 404: 403, 409: 403}


def _malformed() -> GrantUnavailable:
    return GrantUnavailable("the model grant is malformed", detail="malformed_grant", http_status=503)


def _account(raw: object) -> AccountRef | None:
    """A pinned model account (A1): a user's own key or an org cloud account, never anything else."""
    if raw is None:
        return None
    account = cast("dict[str, object]", raw) if isinstance(raw, dict) else {}
    kind, account_id = account.get("kind"), account.get("accountId")
    if kind not in (AccountKind.USER_API_KEY.value, AccountKind.ORG_CLOUD.value) or not isinstance(account_id, str):
        raise _malformed()
    return AccountRef(AccountKind(kind), account_id)


def _route(raw: object, index: int) -> PinnedRoute:
    route = cast("dict[str, object]", raw) if isinstance(raw, dict) else {}
    provider, model = route.get("provider"), route.get("model")
    key, name = route.get("modelKey"), route.get("modelName")
    if not isinstance(provider, str) or not isinstance(model, str):
        raise _malformed()
    on_raw = route.get("on")
    on = (
        frozenset(t for t in cast("list[object]", on_raw) if isinstance(t, str) and t in TRIGGERS)
        if isinstance(on_raw, list) and index > 0
        else frozenset[str]()
    )
    account = _account(route.get("account"))
    # G-48 (WP-100): an alternate names its model as a role's route does, served by the configured model that lists it.
    by_name = bool(on) and account is None and not isinstance(key, str)
    return PinnedRoute(
        provider,
        model,
        config_key=key if isinstance(key, str) else None,
        config_model=name if isinstance(name, str) else (model if by_name else None),
        account=account,
        by_name=by_name,
        on=on,
    )


def _role_routes(raw: object) -> dict[str, PinnedRoute]:
    """GA-15: the engine roles' routes Node folded from the pin; a key that names no engine role is ignored."""
    body = cast("dict[str, object]", raw) if isinstance(raw, dict) else {}
    routes: dict[str, PinnedRoute] = {}
    for role in ENGINE_ROLES:
        route = cast("dict[str, object]", body[role]) if isinstance(body.get(role), dict) else None
        if route is None:
            continue
        provider, model = route.get("provider"), route.get("model")
        if not isinstance(provider, str) or not isinstance(model, str) or not provider or not model:
            raise _malformed()
        routes[role] = PinnedRoute(provider, model, config_model=model, by_name=True)
    return routes


# The grant's fields this build reads; any other string field is carried for an edition's ports (``carried``).
_READ: Final = frozenset(
    {"runId", "attemptId", "routesDigest", "routes", "roleRoutes", "reasoningEffort", "budgets", "actorId", "engine",
     "priorityClass"}
)


def parse_grant(document: object, *, tenant: str) -> RunModelGrant:
    body = cast("dict[str, object]", document) if isinstance(document, dict) else {}
    run_id, routes_raw, budgets = body.get("runId"), body.get("routes"), body.get("budgets")
    if not isinstance(run_id, str) or not isinstance(routes_raw, list) or not isinstance(budgets, dict):
        raise GrantUnavailable("the model grant is malformed", detail="malformed_grant", http_status=503)
    routes = tuple(_route(r, i) for i, r in enumerate(cast("list[object]", routes_raw)))
    limits = cast("dict[str, object]", budgets)
    cost, tokens = limits.get("maxCostUsd"), limits.get("maxTokens")
    effort, actor, engine = body.get("reasoningEffort"), body.get("actorId"), body.get("engine")
    return RunModelGrant(
        tenant=tenant,
        run_id=run_id,
        routes=routes,
        models=neutral_models(routes, _role_routes(body.get("roleRoutes"))),
        budget=RunBudget(
            max_cost_usd=float(cost) if isinstance(cost, int | float) and not isinstance(cost, bool) else None,
            max_tokens=tokens if isinstance(tokens, int) and not isinstance(tokens, bool) else None,
        ),
        reasoning_effort=effort if isinstance(effort, str) else None,
        actor=actor if isinstance(actor, str) and actor else None,
        engine=engine if isinstance(engine, str) and engine else None,
        carried=tuple(sorted((k, v) for k, v in body.items() if k not in _READ and isinstance(v, str) and v)),
        priority_class=parse_class(body.get("priorityClass")),
    )


class NodeRunModelGrants:
    def __init__(
        self,
        *,
        base_url: str,
        http_client: httpx.AsyncClient | None = None,
        timeout_s: float = 10.0,
        ttl_s: float = CACHE_TTL_S,
        monotonic: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self._node = NodeServiceClient(
            base_url=base_url, timeout_s=timeout_s, http_client=http_client, service_name="node.grants"
        )
        self._ttl_s = ttl_s
        self._monotonic = monotonic
        self._wall = wall
        self._cache: dict[tuple[str, str, str, int, str], tuple[float, RunModelGrant]] = {}

    @override
    def __repr__(self) -> str:
        return f"NodeRunModelGrants(cached={len(self._cache)})"

    async def grant(self, claims: GatewayClaims, token: str, *, fresh: bool = False) -> RunModelGrant:
        binding = claims.binding
        if not isinstance(binding, RunAttemptBinding):
            raise GrantUnavailable("model grants exist for durable runs only", http_status=401)
        key = (binding.tenant, binding.run_id, binding.attempt_id, binding.fence, binding.routes_digest)
        now = self._monotonic()
        cached = self._cache.get(key)
        if cached is not None and cached[0] > now and not fresh:
            return cached[1]
        grant = await self._fetch(binding, token)
        lifetime = min(self._ttl_s, max(0.0, claims.expires_at - self._wall()))
        self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
        self._cache[key] = (now + lifetime, grant)
        return grant

    async def _fetch(self, binding: RunAttemptBinding, token: str) -> RunModelGrant:
        path = f"{INTERNAL_PREFIX}/runs/{quote(binding.run_id, safe='')}/model-grant"
        response = await self._node.send(
            "GET",
            path,
            bearer=token,
            unreachable=lambda _: GrantUnavailable("Node is unreachable", detail="node_unreachable", http_status=503),
        )
        if response.status_code != 200:
            status = _REFUSED.get(response.status_code, 503)
            raise GrantUnavailable(
                "Node refused the model grant",
                detail=error_code(response).lower() or f"status_{response.status_code}",
                http_status=status,
            )
        try:
            document: object = response.json()
        except ValueError as exc:
            raise GrantUnavailable("the model grant is malformed", detail="malformed_grant", http_status=503) from exc
        return parse_grant(document, tenant=binding.tenant)

    async def aclose(self) -> None:
        await self._node.aclose()


__all__ = ["NodeRunModelGrants", "parse_grant"]
