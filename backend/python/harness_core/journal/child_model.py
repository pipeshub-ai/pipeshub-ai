"""The model a child run calls (MA1): its role's, else its parent's.

A parent's definition may name a model per sub-agent role. The admission authority (Node) looks a spawn up under
its role keys and, when one has a model, pins that model in the child's spec and names it in ``child.spawned``.
The same rule lives here for the in-process reference journal; both run the one table
``contracts/harness/vectors/child-model.json``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

DEFAULT_ROLE = "default"
ROLE_KEY_MAX = 64
# The dimension a refused role model is named under (design §3 C3).
ROLE_MODELS_DIMENSION = "models.roles"

_OUTSIDE = re.compile(r"[^A-Za-z0-9_.-]+")
_LEADING = re.compile(r"^[^a-z0-9]+")
# ASCII only: a letter outside it is already a ``-``, and ``str.lower`` would fold some of them into ASCII.
_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


@dataclass(frozen=True)
class RoleModelChoice:
    """What ``child.spawned.data.model`` says: the model and the role key that chose it."""

    provider: str
    model: str
    role: str

    @property
    def route(self) -> str:
        return f"{self.provider}:{self.model}"

    def wire(self) -> dict[str, JsonValue]:
        return {"provider": self.provider, "model": self.model, "role": self.role}


@dataclass(frozen=True)
class ChildSpecModel:
    """The model a role-assigned child's spec pins: its one route and the configured entry that serves it."""

    provider: str
    model: str
    model_key: str


def role_key(name: str) -> str:
    """``name`` as a role key; empty when nothing of it can be one."""
    return _LEADING.sub("", _OUTSIDE.sub("-", name).translate(_LOWER))[:ROLE_KEY_MAX]


def role_keys(sub_agent: Mapping[str, JsonValue] | None) -> tuple[str, ...]:
    """The keys a spawn is looked up under, nearest first: its domain agent, its role, then ``default``."""
    definition = sub_agent or {}
    keys: list[str] = []
    for name in (definition.get("domainAgent"), definition.get("role"), DEFAULT_ROLE):
        key = role_key(name) if isinstance(name, str) else ""
        if key and key not in keys:
            keys.append(key)
    return tuple(keys)


def child_model_of(role_routes: Mapping[str, str], sub_agent: Mapping[str, JsonValue] | None) -> RoleModelChoice | None:
    """The model the first role key with a route names (``<provider>:<model>``); ``None``: the child inherits."""
    for key in role_keys(sub_agent):
        route = role_routes.get(key)
        if route is None:
            continue
        provider, _, model = route.partition(":")
        return RoleModelChoice(provider, model, key)
    return None


__all__ = [
    "DEFAULT_ROLE",
    "ROLE_KEY_MAX",
    "ROLE_MODELS_DIMENSION",
    "RoleModelChoice",
    "ChildSpecModel",
    "child_model_of",
    "role_key",
    "role_keys",
]
