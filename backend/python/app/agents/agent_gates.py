"""An agent's own run gates (WP-31 D): what the agents API stores as ``gates``.

The agent's builder sets "Safety and checks": plan approval (G-04) and the checks a run's result must pass (G-05).
Every run of the agent is held to them: the run definition reports them, Node merges them with a turn's own at
admission, and a gate only narrows, so a turn can add one but never drop the agent's. A dry run (G-03) is a
per-turn try-out and never an agent default. The shape is the RunSpec's ``gates`` without ``dryRun``; it is
versioned with the agent (OWD-14) like any other field.
"""

from __future__ import annotations

from typing import Any, Final

from pydantic import JsonValue, ValidationError

from app.harness.contracts.generated.pipeshub.errors import ERROR_CODES
from harness_core.contracts.generated.run_spec import RunGates

GATES_FIELD: Final = "gates"
# What a chat that would run a gated agent on today's loop answers: the loop has no gates, so it never runs one.
# WP-64: the error catalogue's `GATES_NEED_RUNTIME` words, which Node answers for the same refusal.
GATED_AGENT_NEEDS_HARNESS: Final = ERROR_CODES["GATES_NEED_RUNTIME"].next_action.text


class AgentGatesRefused(ValueError):
    """A ``gates`` the agents API will not store; ``code`` is the reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _first_problem(error: ValidationError) -> str:
    issue = error.errors()[0] if error.errors() else None
    if issue is None:
        return "gates is not valid"
    where = ".".join(str(part) for part in issue.get("loc", ()))
    return f"gates{'.' + where if where else ''}: {issue.get('msg', 'invalid')}"


def parse_agent_gates(raw: object) -> dict[str, JsonValue]:
    """``raw`` as the stored gates (``{}``: none), or :class:`AgentGatesRefused`."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise AgentGatesRefused("GATES_INVALID", "gates must be an object")
    if "dryRun" in raw or "dry_run" in raw:
        raise AgentGatesRefused(
            "GATES_DRY_RUN", "gates.dryRun: a dry run is chosen for one message, never set on an agent"
        )
    try:
        gates = RunGates.model_validate(raw)
    except ValidationError as error:
        raise AgentGatesRefused("GATES_INVALID", _first_problem(error)) from None
    stored: dict[str, JsonValue] = gates.model_dump(by_alias=True, exclude_none=True, mode="json")
    return stored


def agent_gates(body: dict[str, Any]) -> dict[str, JsonValue] | None:
    """The gates this write stores; ``None``: the write does not name them; ``{}``: it clears them."""
    if GATES_FIELD not in body:
        return None
    return parse_agent_gates(body[GATES_FIELD])


def stored_agent_gates(value: object) -> dict[str, JsonValue] | None:
    """An agent document's ``gates`` as its run definition reports them; unset when it sets none.

    A stored value that does not read as gates is reported as one Node refuses, so the agent is refused rather than
    run without the gates its owner set."""
    if value is None or value == {}:
        return None
    try:
        return parse_agent_gates(value) or None
    except AgentGatesRefused:
        return {"invalid": True}


__all__ = ["GATED_AGENT_NEEDS_HARNESS", "GATES_FIELD", "AgentGatesRefused", "agent_gates", "parse_agent_gates", "stored_agent_gates"]
