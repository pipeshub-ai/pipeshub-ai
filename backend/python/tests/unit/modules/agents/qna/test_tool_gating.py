"""Regression tests for `code_execution_enabled` (`app.modules.agents.qna.
tool_system`) — the deployment-level gate for sandbox tools.

Covers:

1. Defaults to True (fail-open) when no state/env override applies.
2. Honours ``state["enable_code_execution"]`` as a per-request override.
3. Respects explicit truthy/falsy ``PIPESHUB_ENABLE_CODE_EXECUTION`` env var
   values for deployment-level overrides.

No feature flag is involved — resolution is env-only, so unrecognised/
absent env values fall straight through to the True default.
"""

from __future__ import annotations

import pytest

from app.modules.agents.qna.tool_system import code_execution_enabled


class TestCodeExecutionEnabled:
    def test_default_enabled(self, monkeypatch):
        """No state/env override ⇒ enabled by default."""
        monkeypatch.delenv("PIPESHUB_ENABLE_CODE_EXECUTION", raising=False)
        assert code_execution_enabled({}) is True

    def test_state_override_true(self, monkeypatch):
        monkeypatch.delenv("PIPESHUB_ENABLE_CODE_EXECUTION", raising=False)
        assert code_execution_enabled({"enable_code_execution": True}) is True

    def test_state_override_false_wins_over_env(self, monkeypatch):
        """Per-request explicit False must NOT be silently upgraded by env."""
        monkeypatch.setenv("PIPESHUB_ENABLE_CODE_EXECUTION", "true")
        assert code_execution_enabled({"enable_code_execution": False}) is False

    @pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on"])
    def test_env_truthy_values(self, monkeypatch, raw):
        monkeypatch.setenv("PIPESHUB_ENABLE_CODE_EXECUTION", raw)
        assert code_execution_enabled({}) is True

    @pytest.mark.parametrize("raw", ["0", "false", "no", "off"])
    def test_env_falsy_values_disable(self, monkeypatch, raw):
        """Explicit deploy-level opt-out via env."""
        monkeypatch.setenv("PIPESHUB_ENABLE_CODE_EXECUTION", raw)
        assert code_execution_enabled({}) is False

    @pytest.mark.parametrize("raw", ["", "   ", "maybe"])
    def test_env_unrecognised_values_fall_through(self, monkeypatch, raw):
        """Unrecognised env values fall through to the True default."""
        monkeypatch.setenv("PIPESHUB_ENABLE_CODE_EXECUTION", raw)
        assert code_execution_enabled({}) is True
