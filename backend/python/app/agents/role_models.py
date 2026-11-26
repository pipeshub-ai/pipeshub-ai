"""An agent's model per sub-agent role (MA1): what the agents API stores as ``roleModels``.

``roleModels`` maps a role key (``harness_core.journal.child_model.role_keys``; ``default`` is every sub-agent
without its own entry) to one of the deployment's configured LLMs. It is checked when the agent is saved; which of
those models a run may use is decided at admission and at each spawn, by Node, from the run's pin.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Final, TypedDict, cast

from app.harness.access.configured import machine_name, models_of
from harness_core.journal.child_model import ROLE_KEY_MAX

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

ROLE_MODELS_FIELD: Final = "roleModels"
MAX_ROLE_MODELS: Final = 64
ROLE_KEY: Final = re.compile(rf"^[a-z0-9][a-z0-9_.-]{{0,{ROLE_KEY_MAX - 1}}}$")

_log = logging.getLogger(__name__)


class RoleModel(TypedDict):
    modelKey: str
    modelName: str
    provider: str


class RoleModelsRefused(ValueError):
    """A ``roleModels`` the agents API will not store; ``code`` is the reason, ``role`` the entry at fault."""

    def __init__(self, code: str, message: str, *, role: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.role = role


def _members(value: object) -> Mapping[str, object] | None:
    return cast("Mapping[str, object]", value) if isinstance(value, dict) else None


def _entry(role: str, value: object, configured: list[dict[str, object]]) -> RoleModel:
    raw = _members(value)
    model_key = raw.get("modelKey") if raw is not None else None
    if raw is None or not isinstance(model_key, str) or not model_key:
        raise RoleModelsRefused(
            "ROLE_MODEL_INVALID", f"roleModels.{role} must be an object with a modelKey", role=role
        )
    entry = next((e for e in configured if e.get("modelKey") == model_key), None)
    if entry is None:
        raise RoleModelsRefused(
            "ROLE_MODEL_NOT_CONFIGURED",
            f"roleModels.{role}: model '{model_key}' is not a configured AI model",
            role=role,
        )
    served = models_of(entry)
    asked = raw.get("modelName")
    names_one = asked not in (None, "")
    model_name = asked if isinstance(asked, str) and asked in served else ("" if names_one or not served else served[0])
    if not model_name:
        raise RoleModelsRefused(
            "ROLE_MODEL_NOT_CONFIGURED",
            f"roleModels.{role}: '{model_key}' does not serve the model '{asked or ''}'",
            role=role,
        )
    provider = str(entry.get("provider") or "")
    named = raw.get("provider")
    if named not in (None, "") and (not isinstance(named, str) or machine_name(named) != machine_name(provider)):
        raise RoleModelsRefused(
            "ROLE_MODEL_NOT_CONFIGURED",
            f"roleModels.{role}: '{model_key}' is not a model of the provider '{named}'",
            role=role,
        )
    return RoleModel(modelKey=model_key, modelName=model_name, provider=provider)


async def agent_role_models(
    agent_key: str,
    body: Mapping[str, object],
    configured: Callable[[], Awaitable[list[dict[str, object]]]],
) -> dict[str, RoleModel] | None:
    """The role models to store for ``body``: ``None`` when it does not name the field (the stored value stays),
    an empty map when it clears it. The configuration is read only when there is an entry to check."""
    if ROLE_MODELS_FIELD not in body:
        return None
    value = body[ROLE_MODELS_FIELD]
    try:
        if value is None:
            return {}
        raw = cast("Mapping[object, object] | None", value if isinstance(value, dict) else None)
        if raw is None:
            raise RoleModelsRefused("ROLE_MODELS_INVALID", "roleModels must be an object of role to model")
        if len(raw) > MAX_ROLE_MODELS:
            raise RoleModelsRefused(
                "ROLE_MODELS_TOO_MANY", f"roleModels holds {len(raw)} roles; at most {MAX_ROLE_MODELS} are allowed"
            )
        for role in raw:
            if not isinstance(role, str) or not ROLE_KEY.match(role):
                raise RoleModelsRefused(
                    "ROLE_KEY_INVALID",
                    f"roleModels: '{role}' is not a role key (lower-case letters, digits, '_', '.', '-'; "
                    f"at most {ROLE_KEY_MAX} characters, starting with a letter or digit)",
                    role=str(role),
                )
        if not raw:
            return {}
        entries = await configured()
        return {str(role): _entry(str(role), entry, entries) for role, entry in raw.items()}
    except RoleModelsRefused as refused:
        _log.warning(
            "agent.role_models.refused",
            extra={"agentKey": agent_key, "reasonCode": refused.code, "role": refused.role},
        )
        raise


def stored_role_models(value: object) -> dict[str, RoleModel]:
    """An agent document's ``roleModels`` as a run definition carries it. An entry that is not one (a document
    edited by hand) is left out: its role then has no model of its own and inherits, as a role never set does."""
    out: dict[str, RoleModel] = {}
    for role, stored in (_members(value) or {}).items():
        entry = _members(stored)
        if entry is None or not ROLE_KEY.match(role):
            continue
        model_key, model_name, provider = entry.get("modelKey"), entry.get("modelName"), entry.get("provider")
        if isinstance(model_key, str) and isinstance(model_name, str) and isinstance(provider, str) and model_key and model_name:
            out[role] = RoleModel(modelKey=model_key, modelName=model_name, provider=provider)
    return out


__all__ = [
    "MAX_ROLE_MODELS",
    "ROLE_KEY",
    "ROLE_MODELS_FIELD",
    "RoleModel",
    "RoleModelsRefused",
    "agent_role_models",
    "stored_role_models",
]
