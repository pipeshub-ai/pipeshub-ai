"""An agent's environment (C20.18 / G-17, WP-100): what the agents API stores as ``environment``.

The agent builder's "Environment" section sets what its engine runs' sandboxes need before the engine starts: a setup
script, non-secret variables, the folders outside the workspace the setup fills (kept as the post-setup snapshot);
or a repository's ``devcontainer.json``, imported into the agent by its editor (the
worker reads its subset, never one fetched at run time: the setup is a trusted job that reads no repository file). It
is versioned with the agent (OWD-14), reported in the run definition, and pinned by Node as
``x.pipeshub.environment`` on each engine run. The shape is that pin's; every rule the worker holds it to is checked
here first, so an editor learns at save what the worker would refuse.
"""

from __future__ import annotations

from typing import Any, Final

from pydantic import JsonValue, ValidationError

from app.harness.contracts.generated.pipeshub.run_spec import Environment
from harness_core.engines.environments import EnvironmentInvalid, read_environment

ENVIRONMENT_FIELD: Final = "environment"


class AgentEnvironmentRefused(ValueError):
    """An ``environment`` the agents API will not store; ``code`` is the reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _first_problem(error: ValidationError) -> str:
    issue = error.errors()[0] if error.errors() else None
    if issue is None:
        return "environment is not valid"
    where = ".".join(str(part) for part in issue.get("loc", ()))
    return f"environment{'.' + where if where else ''}: {issue.get('msg', 'invalid')}"


def parse_agent_environment(raw: object) -> dict[str, JsonValue]:
    """``raw`` as the stored environment (``{}``: none), or :class:`AgentEnvironmentRefused`."""
    if raw is None or raw == {}:
        return {}
    if not isinstance(raw, dict):
        raise AgentEnvironmentRefused("ENVIRONMENT_INVALID", "environment must be an object")
    try:
        model = Environment.model_validate(raw)
    except ValidationError as error:
        raise AgentEnvironmentRefused("ENVIRONMENT_INVALID", _first_problem(error)) from None
    stored: dict[str, JsonValue] = model.model_dump(by_alias=True, exclude_none=True, mode="json")
    try:
        _ = read_environment(stored)
    except EnvironmentInvalid as invalid:
        raise AgentEnvironmentRefused(invalid.reason.upper(), str(invalid)) from None
    return stored


def agent_environment(body: dict[str, Any]) -> dict[str, JsonValue] | None:
    """The environment this write stores; ``None``: the write does not name it; ``{}``: it clears it."""
    if ENVIRONMENT_FIELD not in body:
        return None
    return parse_agent_environment(body[ENVIRONMENT_FIELD])


def stored_agent_environment(value: object) -> dict[str, JsonValue] | None:
    """An agent document's ``environment`` as its run definition reports it; unset when it sets none.

    A stored value that no longer reads as one is reported as one Node refuses (``invalid``), so the agent's engine
    run is refused rather than run without the toolchain its editor set up."""
    if value is None or value == {}:
        return None
    try:
        return parse_agent_environment(value) or None
    except AgentEnvironmentRefused:
        return {"invalid": True}


__all__ = [
    "ENVIRONMENT_FIELD",
    "AgentEnvironmentRefused",
    "agent_environment",
    "parse_agent_environment",
    "stored_agent_environment",
]
