"""`tools/special_route.py` must import `AgentHandle` from `core/interfaces.py`
rather than redefining its own copy of the Protocol — `core/interfaces.py`'s
own docstring claims this re-export already happens; this guards against
the two silently drifting apart again (e.g. one gaining a method the other
lacks, so an `isinstance(x, AgentHandle)` check passes against one import
path and fails against the other for the exact same object)."""

from __future__ import annotations

from app.agent_loop_lib.core.interfaces import AgentHandle as CoreAgentHandle
from app.agent_loop_lib.tools.special_route import AgentHandle as SpecialRouteAgentHandle


class TestAgentHandleIsNotDuplicated:
    def test_special_route_reexports_the_same_protocol_object(self) -> None:
        assert SpecialRouteAgentHandle is CoreAgentHandle
