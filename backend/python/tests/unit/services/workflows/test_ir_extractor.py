"""The IR is what the graph view renders and what makes a graph node clickable
back to a line of code, so two properties matter: `source_start`/`source_end`
must both be LINE numbers (a column number there scrolls the editor to a
nonsense position), and two calls to the same tool must not collapse into one
node just because they share a name.
"""
from __future__ import annotations

from app.services.workflows.domain.models import IRNodeKind
from app.services.workflows.ir.extractor import extract_ir, extract_trigger_specs

_SOURCE = '''
from app.services.workflows.sdk import Ctx, step, workflow

@step
async def fetch_tickets(ctx: Ctx) -> list:
    return await ctx.tool("jira__search_issues", jql="project = OPS")

@workflow
async def daily_digest(ctx: Ctx) -> str:
    tickets = await fetch_tickets(ctx)
    await ctx.tool("slack__post_message", channel="#ops", text="first")
    await ctx.tool("slack__post_message", channel="#eng", text="second")
    return "ok"
'''


def _ir():
    return extract_ir(_SOURCE)


class TestLineMapping:
    def test_every_node_maps_to_a_real_line_range(self) -> None:
        source_lines = len(_SOURCE.splitlines())
        for node in _ir().nodes:
            assert 1 <= node.source_start <= source_lines, node.label
            assert node.source_end >= node.source_start, node.label
            assert node.source_end <= source_lines, node.label

    def test_the_entry_point_starts_at_its_own_definition_line(self) -> None:
        ir = _ir()
        entry = next(n for n in ir.nodes if n.node_id == ir.entry_node_id)
        assert _SOURCE.splitlines()[entry.source_start - 1].startswith("async def daily_digest")

    def test_a_tool_call_points_at_the_line_that_calls_it(self) -> None:
        ir = _ir()
        call = next(
            n for n in ir.nodes
            if n.kind == IRNodeKind.TOOL_CALL and n.metadata.get("tool_path") == "jira__search_issues"
        )
        assert "jira__search_issues" in _SOURCE.splitlines()[call.source_start - 1]


class TestNodeIdentity:
    def test_repeated_calls_to_the_same_tool_stay_separate_nodes(self) -> None:
        """Collapsing them would hide a step from the graph and make the
        remaining node's click-through jump to the wrong line."""
        posts = [
            n for n in _ir().nodes
            if n.metadata.get("tool_path") == "slack__post_message"
        ]
        assert len(posts) == 2
        assert posts[0].node_id != posts[1].node_id
        assert posts[0].source_start != posts[1].source_start

    def test_node_ids_are_unique(self) -> None:
        ids = [n.node_id for n in _ir().nodes]
        assert len(ids) == len(set(ids))

    def test_extraction_is_deterministic(self) -> None:
        first, second = extract_ir(_SOURCE), extract_ir(_SOURCE)
        assert first.model_dump() == second.model_dump()


class TestStructure:
    def test_the_entry_point_is_the_workflow_function(self) -> None:
        ir = _ir()
        entry = next(n for n in ir.nodes if n.node_id == ir.entry_node_id)
        assert entry.kind == IRNodeKind.WORKFLOW
        assert entry.label == "daily_digest"

    def test_the_workflow_is_edged_to_the_step_it_calls(self) -> None:
        ir = _ir()
        step_node = next(n for n in ir.nodes if n.kind == IRNodeKind.STEP)
        assert any(
            e.from_node == ir.entry_node_id and e.to_node == step_node.node_id
            for e in ir.edges
        )

    def test_unparseable_source_yields_an_empty_ir_rather_than_raising(self) -> None:
        """A half-written edit in the editor must not blank the whole page."""
        ir = extract_ir("async def broken(:")
        assert ir.nodes == []
        assert ir.entry_node_id is None


class TestDecoratorForms:
    """Generated code reaches the SDK either by importing the names or through
    a module handle. Recognising only the first form is silent: the workflow
    still runs, but the detail view draws an empty graph and every declarative
    trigger is dropped."""

    _QUALIFIED = '''
@sdk.workflow(name="daily", triggers=[sdk.cron("0 9 * * *")])
async def daily(ctx) -> str:
    await ctx.tool("slack__post_message", channel="#ops", text="hi")
    return "ok"
'''

    def test_a_module_qualified_workflow_decorator_is_recognised(self) -> None:
        ir = extract_ir(self._QUALIFIED)
        entry = next(n for n in ir.nodes if n.node_id == ir.entry_node_id)
        assert entry.kind == IRNodeKind.WORKFLOW
        assert any(n.kind == IRNodeKind.TOOL_CALL for n in ir.nodes)

    def test_module_qualified_trigger_helpers_are_extracted(self) -> None:
        specs = extract_trigger_specs(self._QUALIFIED)
        assert [s["kind"] for s in specs] == ["cron"]


class TestTriggerExtraction:
    def test_declared_cron_trigger_is_extracted_as_a_spec(self) -> None:
        source = '''
from app.services.workflows.sdk import Ctx, cron, workflow

@workflow(triggers=[cron("0 9 * * *")])
async def daily(ctx: Ctx) -> str:
    return "ok"
'''
        specs = extract_trigger_specs(source)
        assert len(specs) == 1
        assert specs[0]["kind"] == "cron"
        assert specs[0]["cron_expression"] == "0 9 * * *"

    def test_a_workflow_without_triggers_yields_none(self) -> None:
        assert extract_trigger_specs(_SOURCE) == []
