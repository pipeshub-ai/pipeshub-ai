"""`reasoning_persistence.py` — the `PIPESHUB_PERSIST_REASONING` env-var gate
and truncation policy shared by `build_reasoning_payload` (legacy
`completion_data["reasoning"]`) and `filter_reasoning_parts` (the
parts-transcript equivalent, applied once in `respond.py`'s
`AnswerFinalizer._attach_parts`).
"""

from __future__ import annotations

import importlib

import pytest

from app.agents.agent_loop import reasoning_persistence as rp


@pytest.fixture(autouse=True)
def _reload_module_after_each_test():
    """Env-var reads happen at call time (not import time), so no reload is
    actually required for correctness — but this keeps `monkeypatch.delenv`
    calls in one test from leaking into the next via any future caching."""
    yield
    importlib.reload(rp)


class TestReasoningPersistenceEnabled:
    def test_defaults_to_enabled_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PIPESHUB_PERSIST_REASONING", raising=False)

        assert rp.reasoning_persistence_enabled() is True

    def test_explicit_false_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PIPESHUB_PERSIST_REASONING", "false")

        assert rp.reasoning_persistence_enabled() is False

    def test_explicit_false_is_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PIPESHUB_PERSIST_REASONING", "FALSE")

        assert rp.reasoning_persistence_enabled() is False

    def test_any_other_value_stays_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PIPESHUB_PERSIST_REASONING", "true")

        assert rp.reasoning_persistence_enabled() is True


class TestBuildReasoningPayload:
    def test_returns_none_for_empty_turns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PIPESHUB_PERSIST_REASONING", raising=False)

        assert rp.build_reasoning_payload([]) is None

    def test_returns_none_when_disabled_even_with_turns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PIPESHUB_PERSIST_REASONING", "false")

        assert rp.build_reasoning_payload([{"content": "thinking"}]) is None

    def test_truncates_content_to_max_chars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PIPESHUB_PERSIST_REASONING", raising=False)

        payload = rp.build_reasoning_payload([{"content": "x" * 5000, "turnIndex": 0}])

        assert payload is not None
        assert len(payload[0]["content"]) == 4000
        assert payload[0]["turnIndex"] == 0


class TestFilterReasoningParts:
    def test_drops_reasoning_parts_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PIPESHUB_PERSIST_REASONING", "false")
        parts = [
            {"type": "reasoning", "content": "secret thoughts"},
            {"type": "text", "content": "the answer"},
        ]

        result = rp.filter_reasoning_parts(parts)

        assert result == [{"type": "text", "content": "the answer"}]

    def test_keeps_and_truncates_reasoning_parts_when_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PIPESHUB_PERSIST_REASONING", raising=False)
        parts = [{"type": "reasoning", "content": "y" * 5000}]

        result = rp.filter_reasoning_parts(parts)

        assert len(result) == 1
        assert len(result[0]["content"]) == 4000

    def test_recurses_into_sub_agent_parts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PIPESHUB_PERSIST_REASONING", "false")
        parts = [
            {
                "type": "sub_agent",
                "runId": "child-1",
                "roleName": "explorer",
                "parts": [
                    {"type": "reasoning", "content": "child thinking"},
                    {"type": "text", "content": "child answer"},
                ],
            }
        ]

        result = rp.filter_reasoning_parts(parts)

        assert result[0]["parts"] == [{"type": "text", "content": "child answer"}]

    def test_non_reasoning_non_sub_agent_parts_pass_through_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PIPESHUB_PERSIST_REASONING", raising=False)
        tool_part = {"type": "tool_call", "toolCallId": "call-1", "status": "completed"}

        result = rp.filter_reasoning_parts([tool_part])

        assert result == [tool_part]
