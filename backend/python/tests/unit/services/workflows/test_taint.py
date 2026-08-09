"""Taint tracking decides which calls a workflow may make after it has read
untrusted external content. The gate has to be narrow in both directions: too
loose and injected text in a Jira ticket can drive a deletion, too tight and
the read-then-post shape most workflows have parks a scheduled run on an
approval nobody is awake to give.

Classification is the broker's job (`test_broker_guardrails.py` covers that
end); these tests pin the policy itself.
"""
from __future__ import annotations

from app.services.workflows.security.taint import TaintState


def _after_reading_jira() -> TaintState:
    state = TaintState()
    state.after_tool_result("jira__search_issues", is_taint_source=True)
    return state


class TestBlocking:
    def test_a_destructive_call_after_an_external_read_is_blocked(self) -> None:
        error = _after_reading_jira().check_tool_call("jira__delete_issue", is_destructive=True)

        assert error is not None
        assert error["code"] == "TAINT_BLOCKED"
        assert error["taint_sources"] == ["jira__search_issues"]
        assert "ctx.request_approval" in error["fix_hint"]

    def test_every_source_read_so_far_is_named_in_the_error(self) -> None:
        """The hint is what the user sees, so it has to say where the
        untrusted content came from."""
        state = _after_reading_jira()
        state.after_tool_result("slack__list_messages", is_taint_source=True)

        error = state.check_tool_call("jira__delete_issue", is_destructive=True)

        assert error is not None
        assert error["taint_sources"] == ["jira__search_issues", "slack__list_messages"]

    def test_a_repeated_source_is_not_listed_twice(self) -> None:
        state = _after_reading_jira()
        state.after_tool_result("jira__search_issues", is_taint_source=True)

        error = state.check_tool_call("jira__delete_issue", is_destructive=True)

        assert error is not None
        assert error["taint_sources"] == ["jira__search_issues"]


class TestAllowing:
    def test_an_ordinary_write_after_a_read_still_runs(self) -> None:
        """A workflow is reviewed, committed code: the call sequence is fixed
        before the run, so injected content cannot redirect it."""
        assert _after_reading_jira().check_tool_call("slack__post_message", is_destructive=False) is None

    def test_a_read_after_a_read_is_never_blocked(self) -> None:
        assert _after_reading_jira().check_tool_call("jira__get_issue", is_destructive=False) is None

    def test_a_destructive_call_before_any_external_read_is_allowed(self) -> None:
        state = TaintState()
        state.after_tool_result("slack__list_channels", is_taint_source=False)

        assert state.check_tool_call("slack__delete_message", is_destructive=True) is None
        assert state.is_tainted is False
