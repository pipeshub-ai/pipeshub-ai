"""For tests that drive the agent loop below its HTTP route.

The loop's entry refuses unless the route guard verified the caller for the
current request (`app.api.middlewares.identified_caller`). A test that calls an
entry point, or a route handler, directly has skipped that route guard on
purpose, so this fixture admits the check for it. The check itself is tested
through real routes in `tests/unit/api/test_agent_loop_sink_guard.py`.
"""

from __future__ import annotations

import pytest

LOOP_ENTRY_MODULES = (
    "app.agents.agent_loop.stream_bridge",
)


def admit_loop_callers(monkeypatch: pytest.MonkeyPatch) -> None:
    def admitted(_user_info: object) -> None:
        return None

    for module in LOOP_ENTRY_MODULES:
        monkeypatch.setattr(f"{module}.loop_caller_refusal", admitted)


@pytest.fixture
def loop_caller_verified(monkeypatch: pytest.MonkeyPatch) -> None:
    admit_loop_callers(monkeypatch)
