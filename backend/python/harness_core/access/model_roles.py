"""GA-15 (G-27): which route each engine role of a run calls, folded from its pin's ``models.roles``.

The table Node folds too (``contracts/harness/vectors/model-roles.json``, Node ``domain/model-roles.ts``). Only the
reserved keys name an engine role; other keys are sub-agent roles, a child run's (MA1). A route is
``<provider>:<model>`` split at the first colon. A role without one calls the primary route, and so does every role
of a run whose primary route is pinned to a model account.
"""

from __future__ import annotations

from collections.abc import Mapping

from harness_core.access.types import ENGINE_ROLES, ROLE_MODEL_IDS, ModelRole

MODEL_ROLES_DIMENSION = "models.roles"


def split_route(value: object) -> tuple[str, str] | None:
    if not isinstance(value, str):
        return None
    provider, colon, model = value.partition(":")
    return (provider, model) if colon and provider and model else None


def engine_role_routes(roles: object, *, primary_account: bool) -> dict[ModelRole, tuple[str, str]]:
    """Each mapped engine role's ``(provider, model)``; a role left out calls the primary route."""
    if primary_account or not isinstance(roles, Mapping):
        return {}
    folded: dict[ModelRole, tuple[str, str]] = {}
    for role in ENGINE_ROLES:
        route = split_route(roles.get(role))  # pyright: ignore[reportUnknownMemberType]
        if route is not None:
            folded[role] = route
    return folded


__all__ = ["ENGINE_ROLES", "MODEL_ROLES_DIMENSION", "ROLE_MODEL_IDS", "engine_role_routes", "split_route"]
