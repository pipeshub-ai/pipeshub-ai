"""Golden-query behavioral regression checks (Safe Prompt Bloat Reduction
plan, Phase 0 / "Tests to Add").

Not a full eval framework — a lightweight proxy for a handful of golden
scenarios, each of which pins the specific tool-selection/citation/
escalation/code-execution behavior the real prompt is responsible for
eliciting. Every scenario builds the real prompt (via the same
`_build`/`_runtime_for` helpers `test_prompt_builder.py` uses — mocking
only the content-bearing helpers unrelated to the assertion, never the
gating logic under test) and asserts on structural properties that proxy
for correct model behavior.

These exist so a future prompt trim that removes the wrong sentence fails
here, in under a second, instead of showing up as a silent accuracy
regression in production weeks later.
"""

from __future__ import annotations

from tests.unit.agents.adapter.conftest import make_context
from tests.unit.agents.adapter.test_prompt_builder import FakeTool, _build, _runtime_for


class TestTopicQueryTriggersParallelTools:
    """A query naming a topic that could live in either the KB or a
    connected service must get BOTH the retrieval tool and the matching
    service tool surfaced, with parallel calling instructed — losing this
    is a silent accuracy regression (half the sources never searched)."""

    def test_both_tools_surfaced_and_parallel_instructed(self) -> None:
        context = make_context(has_knowledge=True)
        context.tool_state["agent_toolsets"] = [{"name": "jira-cloud"}]
        result = _build(
            context,
            tool_names=["knowledgegraph__search", "jira_search_issues"],
        )
        assert "## Finding Information" in result
        section = result.split("## Finding Information", 1)[1]
        assert "IN PARALLEL" in section
        assert "knowledgegraph__search" in section


class TestCitationFormatInstructionPresent:
    """Every prompt that can produce citable knowledge must teach the
    `[source](<Citation ID>)` markdown form — without it the model invents
    URLs or drops citations, and there is no other place this is taught."""

    def test_present_when_knowledge_configured(self) -> None:
        context = make_context(has_knowledge=True)
        result = _build(context)
        assert "## Citation Rules" in result
        assert "[source](ref5)" in result

    def test_present_for_web_only_run(self) -> None:
        context = make_context()
        context.tool_state["web_search_config"] = {"provider": "duckduckgo"}
        result = _build(context)
        assert "## Citation Rules" in result
        assert "url/Citation ID:" in result


class TestFullRecordEscalationRulePresent:
    """Once `dynamic_fetch_full_record` is granted, the model must be told
    that in-context blocks are a ranked sample and IDs must be batched into
    ONE call — the WHEN-to-escalate test itself now lives solely in the
    tool's own function-calling schema (`_FETCH_FULL_RECORD_DESCRIPTION`,
    see `hooks/citations.py`), so it is asserted there, not here. Must NOT
    appear before the tool exists (turn 1), or it invites a tool-not-found
    call."""

    def test_escalation_rule_present_and_names_the_tool(self) -> None:
        context = make_context()
        result = _build(
            context,
            tool_names=["knowledgegraph__search", "knowledgegraph__fetch_record"],
        )
        assert "## Reading Records in Full" in result
        assert "knowledgegraph__fetch_record" in result

    def test_no_escalation_rule_before_tool_is_granted(self) -> None:
        context = make_context()
        result = _build(context, tool_names=["knowledgegraph__search"])
        assert "## Reading Records in Full" not in result

    def test_whole_document_test_lives_in_the_tool_schema(self) -> None:
        from app.agents.agent_loop.hooks.citations import _FETCH_FULL_RECORD_DESCRIPTION

        for shape in ("summary", "risks", "review", "comparison"):
            assert shape in _FETCH_FULL_RECORD_DESCRIPTION.lower()



class TestCodeExecutionMandatoryTrigger:
    """`run_code`/`coding_agent` being grantable must always come with the
    MANDATORY file-generation trigger — otherwise the model answers file
    requests in prose instead of producing a downloadable artifact."""

    def test_flat_run_code_triggers_mandatory_language(self) -> None:
        context = make_context()
        result = _build(context, tool_names=["run_code"])
        assert "## Code Execution (MANDATORY" in result

    def test_composed_coding_agent_triggers_mandatory_language(self) -> None:
        context = make_context()
        tool = FakeTool("coding_agent", description="Delegate a coding task.")
        result = _build(
            context, tool_names=["coding_agent"], runtime=_runtime_for([tool]),
        )
        assert "## Code Execution (MANDATORY" in result
        assert "delegate to `coding_agent`" in result

    def test_no_trigger_without_code_tool(self) -> None:
        context = make_context()
        result = _build(context, tool_names=["web_search"])
        assert "## Code Execution (MANDATORY" not in result


class TestRoutingTablesStayRemoved:
    """Regression guard for the routing-table removal: the ASCII
    "Tool Routing" block and the "Dual-Source Apps" decision table must not
    come back — that routing now lives in each tool's own `description`
    (sent via the function-calling schema), not restated in the system
    prompt. A plain example attribution phrase (e.g. "From Confluence...")
    is fine; a routing table naming which apps have dual sources is not."""

    def test_no_routing_table_or_dual_source_table(self) -> None:
        context = make_context(has_knowledge=True)
        context.tool_state["agent_toolsets"] = [{"name": "jira-cloud"}]
        result = _build(
            context,
            tool_names=["retrieval__search_internal_knowledge", "jira_search_issues"],
        )
        section = result.split("## Finding Information", 1)[1].split("\n## ", 1)[0]
        assert "Tool Routing" not in section
        assert "Dual-Source Apps" not in section
        assert "SERVICE NOUN" not in section
        assert "When to use ONLY" not in section
