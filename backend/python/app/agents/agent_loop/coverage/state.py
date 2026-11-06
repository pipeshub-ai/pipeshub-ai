"""Per-request coverage-loop state, kept in the request's tool state so the
prefetch, the gate and every sub-agent of the request see one copy."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.agents.agent_loop.coverage.facets import FacetPlan, tokens

if TYPE_CHECKING:
    from collections.abc import MutableMapping

COVERAGE_STATE_KEY = "coverage_loop"


def query_key(query: str) -> frozenset[str]:
    return frozenset(tokens(query))


@dataclass
class CoverageState:
    plan: FacetPlan | None = None
    planned: bool = False
    gate_rounds: int = 0
    opening_rewrite_sent: bool = False
    dispute_checked: bool = False
    searched: list[str] = field(default_factory=list[str])
    counters: dict[str, int] = field(default_factory=dict[str, int])

    def bump(self, name: str, by: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + by

    def note_searched(self, query: str) -> None:
        if query and not self.was_searched(query):
            self.searched.append(query)

    def was_searched(self, query: str) -> bool:
        key = query_key(query)
        return bool(key) and any(query_key(done) == key for done in self.searched)


def coverage_state(tool_state: MutableMapping[str, Any]) -> CoverageState:
    """The request's loop state, created on first use."""
    state = tool_state.get(COVERAGE_STATE_KEY)
    if not isinstance(state, CoverageState):
        state = CoverageState()
        tool_state[COVERAGE_STATE_KEY] = state
    return state


__all__ = ["COVERAGE_STATE_KEY", "CoverageState", "coverage_state", "query_key"]
