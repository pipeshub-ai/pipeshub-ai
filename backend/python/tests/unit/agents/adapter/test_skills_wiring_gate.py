"""`skills_enabled()` (`app/agents/agent_loop/skills_wiring.py`) — regression
for the default-drift fix: the function's actual default (`os.getenv(...,
"true")`) must match its own module docstring, which used to claim "default
OFF" while the code was actually "default ON". Same deployment-level
opt-out convention as `PIPESHUB_USE_AGENT_LOOP`/`PIPESHUB_USE_COMPOSED_
AGENTS` (both also default `"true"`) — fixed to match the DOCSTRING to the
CODE (not the other way around), since flipping the actual default would
be a behavior change existing deployments/tests already rely on.
"""

from __future__ import annotations

import pytest

from app.agents.agent_loop.skills_wiring import skills_enabled


class TestSkillsEnabledDefault:
    def test_defaults_to_enabled_when_env_var_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PIPESHUB_ENABLE_SKILLS", raising=False)
        assert skills_enabled() is True

    def test_explicit_false_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "false")
        assert skills_enabled() is False

    def test_explicit_true_enables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "true")
        assert skills_enabled() is True
