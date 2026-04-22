"""Regression tests for code-execution tool gating.

These are focused, dependency-light tests that check:

1. ``_code_execution_enabled`` defaults to False.
2. It honours ``state["enable_code_execution"]`` as a per-request override.
3. It respects the ``PIPESHUB_ENABLE_CODE_EXECUTION`` env var.

The full ``_load_all_tools`` path pulls in heavy transitive imports that may
not be available in unit-test environments (etcd3, google api client, ...),
so we deliberately exercise only the gating helper here.
"""

from __future__ import annotations

import pytest


def _get_helper():
    """Import without triggering tool_system's heavy side-effects.

    We read the source of ``tool_system.py`` and eval just the two gating
    constructs. This way the test doesn't require the full package tree.
    """
    from pathlib import Path
    src = (
        Path(__file__).resolve().parents[5]
        / "app"
        / "modules"
        / "agents"
        / "qna"
        / "tool_system.py"
    )
    text = src.read_text()

    # Extract the two blocks by crude anchoring so the test stays resilient
    # to nearby edits.
    const_marker = "_CODE_EXECUTION_APPS: frozenset[str] = frozenset({"
    func_marker = "def _code_execution_enabled(state"

    i_const = text.index(const_marker)
    i_func = text.index(func_marker)

    # Slice from the constant definition through the end of the function.
    # End: find the first blank line at col 0 after func_marker.
    tail = text[i_func:]
    # Grab until the next top-level def/class or a comment section.
    for sep in ["\n\ndef ", "\n\nclass ", "\n\n# "]:
        idx = tail.find(sep, 1)
        if idx > 0:
            tail = tail[:idx]
            break

    snippet = text[i_const:i_func] + tail

    ns: dict = {}
    exec(snippet, ns)
    return ns["_code_execution_enabled"], ns["_CODE_EXECUTION_APPS"]


@pytest.fixture(scope="module")
def helper():
    return _get_helper()


class TestCodeExecutionEnabled:
    def test_default_disabled(self, helper, monkeypatch):
        code_exec_enabled, _apps = helper
        monkeypatch.delenv("PIPESHUB_ENABLE_CODE_EXECUTION", raising=False)
        assert code_exec_enabled({}) is False

    def test_state_override_true(self, helper, monkeypatch):
        code_exec_enabled, _apps = helper
        monkeypatch.delenv("PIPESHUB_ENABLE_CODE_EXECUTION", raising=False)
        assert code_exec_enabled({"enable_code_execution": True}) is True

    def test_state_override_false_wins_over_env(self, helper, monkeypatch):
        """Per-request explicit False must NOT be silently upgraded by env."""
        code_exec_enabled, _apps = helper
        monkeypatch.setenv("PIPESHUB_ENABLE_CODE_EXECUTION", "true")
        assert code_exec_enabled({"enable_code_execution": False}) is False

    @pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on"])
    def test_env_truthy_values(self, helper, monkeypatch, raw):
        code_exec_enabled, _apps = helper
        monkeypatch.setenv("PIPESHUB_ENABLE_CODE_EXECUTION", raw)
        assert code_exec_enabled({}) is True

    @pytest.mark.parametrize("raw", ["0", "false", "no", "", "   ", "maybe"])
    def test_env_falsy_values(self, helper, monkeypatch, raw):
        code_exec_enabled, _apps = helper
        monkeypatch.setenv("PIPESHUB_ENABLE_CODE_EXECUTION", raw)
        assert code_exec_enabled({}) is False

    def test_apps_list_contains_both_sandbox_apps(self, helper):
        _fn, apps = helper
        assert "coding_sandbox" in apps
        assert "database_sandbox" in apps
        # image_generator is intentionally NOT in this list — it's a
        # data-generation action, not arbitrary code execution.
        assert "image_generator" not in apps
