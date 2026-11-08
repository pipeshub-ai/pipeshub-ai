"""Which messages the context shapers must keep: `context/protection.py`."""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.agent_loop_lib.context.protection import (
    compactable_runs,
    current_request,
    current_request_index,
    protected_indices,
)
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    SystemMessage,
    TextPart,
    ToolCall,
    ToolMessage,
    UserMessage,
)

_SETTINGS = settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])


def _chat() -> list:
    """Two earlier turns, then the current question and the run's own work."""
    return [
        UserMessage(content="QUESTION A"),
        AssistantMessage(content=[TextPart(text="answer A")]),
        UserMessage(content="QUESTION B"),
        AssistantMessage(content=[TextPart(text="answer B")]),
        UserMessage(content="QUESTION C (current)"),
        AssistantMessage(tool_calls=[ToolCall(id="c1", name="search", arguments={})]),
        ToolMessage(content="r1", tool_call_id="c1"),
        UserMessage(content="nudge from the loop", injected=True),
        AssistantMessage(tool_calls=[ToolCall(id="c2", name="fetch", arguments={})]),
        ToolMessage(content="r2", tool_call_id="c2"),
    ]


class TestCurrentRequest:
    def test_is_the_last_human_message_not_the_first(self) -> None:
        assert current_request_index(_chat()) == 4

    def test_injected_messages_are_never_the_current_request(self) -> None:
        messages = [UserMessage(content="Q"), UserMessage(content="summary", injected=True)]
        assert current_request_index(messages) == 0

    def test_none_without_a_human_message(self) -> None:
        assert current_request_index([]) is None
        assert current_request([AssistantMessage(content=[TextPart(text="hi")])]) is None

    def test_records_the_tool_calls_made_after_it(self) -> None:
        request = current_request(_chat())
        assert request is not None
        assert request.text == "QUESTION C (current)"
        assert request.later_call_ids == {"c1", "c2"}

    def test_single_question_run_protects_its_goal(self) -> None:
        messages = [UserMessage(content="goal"), AssistantMessage(content=[TextPart(text="done")])]
        assert protected_indices(messages) == {0}


class TestProtectedIndices:
    def test_current_request_pinned_and_system_messages(self) -> None:
        messages = [
            SystemMessage(content="sys"),
            UserMessage(content="old question"),
            UserMessage(content="PLAN", injected=True, pinned=True),
            UserMessage(content="current question"),
            UserMessage(content="nudge", injected=True),
        ]
        assert protected_indices(messages) == {0, 2, 3}

    def test_earlier_questions_are_not_protected(self) -> None:
        assert 0 not in protected_indices(_chat())


class TestCompactableRuns:
    def test_splits_around_protected_messages(self) -> None:
        assert compactable_runs(frozenset({4}), 0, 10) == [range(0, 4), range(5, 10)]

    def test_respects_start_and_stop(self) -> None:
        assert compactable_runs(frozenset({4}), 2, 7) == [range(2, 4), range(5, 7)]

    def test_protected_at_both_ends_and_adjacent(self) -> None:
        assert compactable_runs(frozenset({0, 1, 5}), 0, 6) == [range(2, 5)]

    def test_everything_protected_or_empty(self) -> None:
        assert compactable_runs(frozenset({0, 1}), 0, 2) == []
        assert compactable_runs(frozenset(), 3, 3) == []

    def test_chat_runs_keep_tool_groups_whole(self) -> None:
        messages = _chat()
        for run in compactable_runs(protected_indices(messages), 0, len(messages)):
            first = messages[run.start]
            assert not isinstance(first, ToolMessage), "a run never starts inside a tool group"

    @_SETTINGS
    @given(
        size=st.integers(min_value=0, max_value=60),
        data=st.data(),
    )
    def test_runs_cover_exactly_the_unprotected_span(self, size, data) -> None:
        protected = frozenset(data.draw(st.sets(st.integers(min_value=0, max_value=max(size - 1, 0)))))
        start = data.draw(st.integers(min_value=0, max_value=size))
        stop = data.draw(st.integers(min_value=start, max_value=size))

        runs = compactable_runs(protected, start, stop)

        covered = [i for run in runs for i in run]
        assert covered == [i for i in range(start, stop) if i not in protected]
        assert all(len(run) > 0 for run in runs)
        # Maximal: two runs are always separated by at least one protected index.
        for left, right in zip(runs, runs[1:]):
            assert any(i in protected for i in range(left.stop, right.start))
