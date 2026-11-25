"""WID-1..WID-9 (OWD-18, G-42, WP-23): write identity and AI disclosure on an agent's external writes.

On the real tool catalog (P2.1's enumerators: every tool a durable run can reach), every external write is either
disclosed, with the argument people read, or exempt with its reason (WID-1). Per connector, the disclosure lands in
that argument in the connector's markup, once (WID-2). Creates and updates differ (WID-4), markup switches follow the
call (WID-5), and the rule runs in the executor the loop and the engines' Tool Gateway share (WID-6). The run reads
its snapshot TTL from its pin (WID-8); who a write is from beyond the user is the write identity port's
(``tests/unit/harness/test_wp132_write_identity_port.py``).
"""

from __future__ import annotations

import inspect
from collections import Counter
from typing import TYPE_CHECKING, Any

import pytest

from app.harness.run_policy.resolved import resolved_snapshot_ttl_days
from app.harness.write_identity.disclosure import (
    WriteIdentitySettings,
    disclosed,
    render,
    write_identity_middleware,
)
from app.harness.write_identity.settings import use_write_identity
from app.harness.write_identity.targets import (
    DISCLOSED,
    EXEMPT,
    EXEMPT_PREFIXES,
    DisclosureTarget,
    target_of,
)
from harness_core.effects.flow import external_write
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable.effects import classify_tool_effect
from harness_core.kernel.hooks.middleware.context import ToolCallContext
from harness_core.kernel.hooks.middleware.decisions import PreDecision
from harness_core.kernel.hooks.registry import HookRegistry
from harness_core.tools.base import ParameterType, ToolOutput, ToolParameter
from harness_core.tools.decorators import tool
from harness_core.tools.executor import ToolExecutor
from harness_core.tools.registry import ToolRegistry
from tests.unit.harness.test_effect_tags_complete import ENUMERATORS

if TYPE_CHECKING:
    from harness_core.tools.base import Tool

APP = "https://pipeshub.example.com"
LINK = f"{APP}/chat?conversationId=ses-1"


def settings() -> WriteIdentitySettings:
    return WriteIdentitySettings(session_id="ses-1", agent_name="Sales <Helper>", app_url=APP)


def _key(path: str) -> tuple[str, str] | None:
    parts = path.rstrip("/").split("/")
    return (parts[-2], parts[-1]) if len(parts) >= 2 else None


@pytest.fixture(scope="module")
def external_writes() -> dict[str, Tool]:
    found: dict[str, Tool] = {}
    for enumerate_ in ENUMERATORS.values():
        for t in enumerate_():
            if external_write(classify_tool_effect(t.name, t.tags)):
                found.setdefault(getattr(t, "path", t.name), t)
    return found


def test_wid1_every_external_write_is_disclosed_or_exempt_with_a_reason(external_writes: dict[str, Tool]) -> None:
    unplaced = [
        path
        for path in external_writes
        if not any(path.startswith(prefix) for prefix in EXEMPT_PREFIXES)
        and _key(path) not in DISCLOSED
        and _key(path) not in EXEMPT
    ]
    assert unplaced == [], "place each new connector write in DISCLOSED or EXEMPT (app/harness/write_identity/targets.py)"
    assert not set(DISCLOSED) & set(EXEMPT)
    assert all(reason.strip() for reason in EXEMPT.values())


def test_wid1_every_listed_write_is_a_real_tool(external_writes: dict[str, Tool]) -> None:
    keys = {_key(path) for path in external_writes}
    assert sorted(set(DISCLOSED) - keys) == []
    assert sorted(set(EXEMPT) - keys) == []


def test_wid1_every_action_type_has_a_connector_and_every_connector_with_messages_is_covered() -> None:
    actions = Counter(target.action for target in DISCLOSED.values())
    assert set(actions) == {"email", "message", "ticket", "document", "event", "commit"}
    connectors = {toolset for toolset, _ in DISCLOSED}
    assert {"slack", "teams", "gmail", "outlook", "jira", "github", "confluence", "clickup", "salesforce"} <= connectors


@pytest.mark.parametrize(("key", "target"), sorted(DISCLOSED.items()), ids=lambda v: "/".join(v) if isinstance(v, tuple) else "")
def test_wid2_the_disclosure_lands_in_the_argument_people_read_in_the_connectors_markup(
    key: tuple[str, str], target: DisclosureTarget, external_writes: dict[str, Tool]
) -> None:
    [t] = [t for path, t in external_writes.items() if _key(path) == key]
    assert target.field in {p.name for p in t.parameters}, f"{key}: {target.field} is not an argument of the tool"
    if target.markup_switch is not None:
        assert target.markup_switch[0] in {p.name for p in t.parameters}
    s = settings()
    arguments: dict[str, object] = {target.field: "Hello team"}
    assert disclosed(s, target, arguments)
    text = str(arguments[target.field])
    assert text.startswith("Hello team")
    assert text.endswith(render(s, target.markup, target.action))
    assert "ses-1" in text
    again = dict(arguments)
    assert not disclosed(s, target, again)
    assert again == arguments


def test_wid2_each_markup_names_the_agent_and_links_the_session() -> None:
    s = settings()
    assert render(s, "text") == f"Sent via PipesHub agent Sales <Helper> · View the session: {LINK}"
    assert render(s, "markdown") == f"_Sent via PipesHub agent Sales <Helper>_ · [View the session]({LINK})"
    assert render(s, "slack") == f"_Sent via PipesHub agent Sales <Helper>_ · <{LINK}|View the session>"
    assert render(s, "html") == (
        f'<p><em>Sent via PipesHub agent Sales &lt;Helper&gt;</em> · <a href="{LINK}">View the session</a></p>'
    )
    unlinked = WriteIdentitySettings(session_id="ses-9", agent_name="A")
    assert render(unlinked, "text") == "Sent via PipesHub agent A · session ses-9"


JIRA_ARGS = frozenset({"project_key", "summary", "description", "issue_key", "status"})


async def _pre(
    s: WriteIdentitySettings, path: str, arguments: dict[str, Any], accepts: frozenset[str] | None = None
) -> ToolCallContext:
    ctx = ToolCallContext(tool_path=path, tool_input=arguments)
    reached: list[bool] = []

    async def next_fn() -> None:
        reached.append(True)

    await write_identity_middleware(s, (lambda _path: accepts) if accepts is not None else None)(ctx, next_fn)
    if ctx.decision == PreDecision.ALLOW:
        assert reached == [True]
    return ctx


async def test_wid4_a_create_without_text_gets_the_disclosure_alone_an_update_that_leaves_it_alone_stays() -> None:
    created = await _pre(settings(), "/tools/jira/create_issue", {"summary": "Bug"}, JIRA_ARGS)
    assert created.tool_input["description"] == render(settings(), "text", "ticket")
    updated = await _pre(settings(), "/tools/jira/update_issue", {"issue_key": "P-1", "status": "Done"}, JIRA_ARGS)
    assert "description" not in updated.tool_input
    # A tool that does not say it takes the argument is never handed one it may refuse.
    unknown = await _pre(settings(), "/tools/jira/create_issue", {"summary": "Bug"})
    assert unknown.tool_input == {"summary": "Bug"}
    undeclared = await _pre(settings(), "/tools/jira/create_issue", {"summary": "Bug"}, frozenset({"summary"}))
    assert undeclared.tool_input == {"summary": "Bug"}
    structured = await _pre(settings(), "/tools/jira/add_comment", {"comment": {"type": "doc"}})
    assert structured.tool_input["comment"] == {"type": "doc"}
    other = await _pre(settings(), "/tools/slack/add_reaction", {"name": "tada"})
    assert other.tool_input == {"name": "tada"}
    assert target_of("/mcp/inst-1/delete_page") is None


async def test_wid5_the_calls_own_markup_switch_decides_the_disclosures_markup() -> None:
    html_mail = await _pre(settings(), "/tools/outlook/send_email", {"body": "<p>Hi</p>", "body_type": "HTML"})
    assert str(html_mail.tool_input["body"]).endswith(render(settings(), "html"))
    text_mail = await _pre(settings(), "/tools/outlook/send_email", {"body": "Hi", "body_type": "Text"})
    assert str(text_mail.tool_input["body"]).endswith(render(settings(), "text"))
    rich = await _pre(settings(), "/tools/salesforce/post_chatter_to_record", {"text": "Hi", "is_rich_text": True})
    assert str(rich.tool_input["text"]).endswith(render(settings(), "markdown"))


def _slack_registry(sent: list[str]) -> ToolRegistry:
    @tool(
        path="/tools/slack/send_message",
        short_description="send",
        description="Send a Slack message.",
        parameters=[
            ToolParameter(name="channel", type=ParameterType.STRING, description="channel", required=True),
            ToolParameter(name="message", type=ParameterType.STRING, description="message", required=True),
        ],
    )
    async def send_message(channel: str, message: str) -> ToolOutput:
        sent.append(message)
        return ToolOutput(success=True, data={"ok": True, "channel": channel})

    registry = ToolRegistry()
    registry.register_tool(send_message)
    return registry


async def test_wid6_the_executor_the_loop_and_the_tool_gateway_share_sends_the_disclosed_text() -> None:
    sent: list[str] = []
    registry = _slack_registry(sent)
    executor = ToolExecutor(registry, use_write_identity(HookRegistry(), settings()))
    name = registry.names()[0]
    result = await executor.call_tool(ToolCall(id="c1", name=name, arguments={"channel": "C1", "message": "Shipped"}))
    assert not result.is_error
    assert sent == [f"Shipped\n\n{render(settings(), 'slack')}"]


def test_wid8_the_run_reads_its_snapshot_ttl_from_its_pin() -> None:
    def entry(value: object) -> dict[str, object]:
        return {"value": value, "setBy": "org", "ceilings": []}

    assert resolved_snapshot_ttl_days({}) == 7
    assert resolved_snapshot_ttl_days({"snapshots.ttlDays": entry(2)}) == 2
    assert resolved_snapshot_ttl_days({"snapshots.ttlDays": entry(True)}) == 7


def test_wid9_the_middleware_is_applied_where_the_loop_and_the_engines_build_their_call_hooks() -> None:
    from app.harness.assembly import assembler
    from app.harness.engines import durable

    assert "use_write_identity(sandbox.hooks.registry, write_identity, registry)" in inspect.getsource(assembler)
    assert "use_write_identity(HookRegistry(), write_identity, registry)" in inspect.getsource(durable)


@pytest.mark.parametrize("markup", ["text", "markdown", "slack", "html"])
def test_wid10_the_parity_lanes_leave_out_exactly_the_disclosure_and_nothing_else(markup: str) -> None:
    from tests.harness_replay.wire import without_disclosure

    target = DisclosureTarget("message", "body", markup)  # type: ignore[arg-type]
    for s in (settings(), WriteIdentitySettings(session_id="ses-eval", agent_name="Eval")):
        arguments: dict[str, object] = {"body": "Shipped · see notes", "n": 1}
        assert disclosed(s, target, arguments)
        assert without_disclosure(arguments) == {"body": "Shipped · see notes", "n": 1}
    assert without_disclosure({"body": "Sent via PipesHub agent X"}) == {"body": "Sent via PipesHub agent X"}
