"""sdk_reference(symbol): Tier 2 progressive-disclosure tool for the
Workflow Builder Agent. Returns one symbol's full signature, docstring,
and example — so the model can look up exactly what it needs without the
full stub bundle polluting the context window.

Mirrors FetchToolsTool's role for tools: depth on demand.
"""
from __future__ import annotations

import logging
import textwrap
from typing import Any

from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter

__all__ = ["SdkReferenceTool"]

logger = logging.getLogger(__name__)

_SDK_SYMBOLS: dict[str, str] = {
    "workflow": textwrap.dedent("""
        @workflow(name=None, inputs=None, outputs=None, on_event=None, triggers=None)

        Marks an async function as a workflow entry point. Exactly one per file.

        Parameters:
            name: str — workflow name (defaults to function name)
            inputs: Pydantic BaseModel — input schema
            outputs: Pydantic BaseModel — output schema
            on_event: internal event-binding metadata — do not set this
                directly; use the `triggers=[on_event(...)]` form instead.
            triggers: list[TriggerSpec] — cron()/interval()/once_at()/on_event()
                schedules, read at generation time and registered with the
                scheduler. See sdk_reference("triggers").

        The entry point receives the payload of whatever triggered the run as
        its second argument ({} for a manual run).

        Example:
            from app.services.workflows.sdk import workflow, Ctx, cron

            @workflow(name="daily_report", triggers=[cron("0 9 * * 1-5", tz="UTC")])
            async def daily_report(ctx: Ctx, inp: dict) -> dict:
                data = await fetch_data(ctx, inp.get("date"))
                return {"report": data}
    """).strip(),
    "triggers": textwrap.dedent("""
        from app.services.workflows.sdk import cron, interval, once_at, on_event
        from app.services.workflows.sdk import slack, jira, github, confluence

        cron(expression: str, *, tz: str = "UTC") -> TriggerSpec
            Fire on a 5-field cron schedule in the given IANA timezone.

        interval(seconds: int) -> TriggerSpec
            Fire every `seconds` seconds.

        once_at(when: str) -> TriggerSpec
            Fire once at an ISO-8601 instant. Must be in the future.

        on_event(event_type, **filters) -> TriggerSpec
            Fire when a matching app event arrives. `filters` are equality
            matches against the event payload.

        Example:
            @workflow(
                name="alert_watcher",
                triggers=[
                    cron("*/15 * * * *", tz="America/New_York"),
                    on_event(slack.MessagePosted, channel="C0123"),
                ],
            )
            async def alert_watcher(ctx: Ctx, event: dict) -> None:
                ctx.log("triggered", payload=event)
    """).strip(),
    "step": textwrap.dedent("""
        @step(retries=0, timeout_s=None, side_effect=SideEffect.NONE)

        Marks an async function as a journaled workflow step.

        Parameters:
            retries: int — automatic retry count on failure (default 0)
            timeout_s: float | None — step timeout in seconds
            side_effect: SideEffect — READ, WRITE, or NONE
                READ: safe to re-run on replay
                WRITE: do NOT re-run; raises ReplayDivergence if journal miss
                NONE: no external side effects

        Example:
            @step(retries=3, timeout_s=30, side_effect=SideEffect.READ)
            async def fetch_tickets(ctx: Ctx, project: str) -> list[dict]:
                return await ctx.tool("jira__search_issues", jql=f"project={project}")
    """).strip(),
    "ctx.tool": textwrap.dedent("""
        await ctx.tool(tool_name: str, **kwargs) -> Any

        Call a registered PipesHub tool. Journaled — replays return cached result.
        Only tools granted to this workflow are callable; anything else is
        rejected at generation time and again at run time.

        Parameters:
            tool_name: str — Tool.name form, "<app>__<action>"
                (e.g. "jira__search_issues", "slack__send_message")
            **kwargs — tool-specific arguments

        Example:
            issues = await ctx.tool("jira__search_issues", jql="project=MYPROJ AND status=Open")
            await ctx.tool("slack__send_message", channel="C0123", text="Done!")
    """).strip(),
    "ctx.agent": textwrap.dedent("""
        handle = await ctx.agent(agent_id: str)
        result = await handle.run(goal: str, **kwargs) -> str

        Two-step: ctx.agent(...) returns a handle, .run(...) executes it.
        Journaled — replays return the cached result.

        IMPORTANT: .run() returns a plain TEXT STRING — the agent's natural-language
        answer. It is NOT a dict, list, or structured object. Do not index it, parse
        it as JSON, or call .get() on it. Use the string directly.

        Parameters:
            agent_id: str — id of an existing agent
            goal: str — the agent's task description
            **kwargs — additional context passed to the agent

        Example:
            summarizer = await ctx.agent("agent-uuid")
            summary = await summarizer.run(goal=f"Summarize these {len(issues)} issues")
            await ctx.emit(summary)
    """).strip(),
    "ctx.create_agent": textwrap.dedent("""
        handle = await ctx.create_agent(
            name: str, *, instructions: str = "", tools: list[str] | None = None,
            knowledge: list[str] | None = None, skills: list[str] | None = None,
            mcps: list[str] | None = None, model: str | None = None,
            persist: bool = False,
        )

        Provision a new agent and return a handle you can .run(goal=...) on.
        Requires the workflow's grant to allow agent creation.

        IMPORTANT: .run() returns a plain TEXT STRING — the agent's natural-language
        answer. It is NOT a dict, list, or structured object. Do not index it, parse
        it as JSON, or call .get() on it. Use the string directly.

        Parameters:
            tools: list[str] — Tool names to grant. You only need to list
                one or two representative tools per app (e.g. ["jira__search_issues"]);
                the agent automatically gets access to ALL tools from each
                listed app (read and write), so it can discover projects,
                list resources, etc. as needed to accomplish its goal.
            mcps: list[str] — MCP server IDs. Validated now; has no effect
                until the MCP registry exists (the agent is created without
                MCP toolsets in the meantime).

        Example:
            agent = await ctx.create_agent(
                "jira-reporter",
                instructions="Search Jira and produce a markdown summary.",
                tools=["jira__search_issues"],
            )
            report = await agent.run(goal="List all open P1 bugs in project LAUNCH")
            await ctx.emit(report)
    """).strip(),
    "ctx.search": textwrap.dedent("""
        await ctx.search(query: str, *, collections: list[str] | None = None, limit: int = 10) -> list[dict]

        Search the knowledge base. Journaled. Scoped to the collections this
        workflow was granted.

        Example:
            docs = await ctx.search("refund policy", limit=5)
    """).strip(),
    "ctx.state": textwrap.dedent("""
        await ctx.state.get(key: str) -> Any
        await ctx.state.set(key: str, value: Any) -> None

        Durable key/value state scoped to this workflow across all of its runs.
        Use it for cursors and watermarks so a scheduled workflow can pick up
        where the previous run stopped.

        Example:
            last_seen = await ctx.state.get("last_issue_key")
            ...
            await ctx.state.set("last_issue_key", issues[-1]["key"])
    """).strip(),
    "ctx.emit": textwrap.dedent("""
        await ctx.emit(message: str, *, kind: str = "text") -> None

        Post a message back into the conversation the workflow was created
        from. kind: "text" (default), "code", "error", "card".

        Example:
            await ctx.emit(f"Triaged {len(issues)} issues.")
    """).strip(),
    "ctx.map": textwrap.dedent("""
        await ctx.map(fn, items: list, *, concurrency: int | None = None) -> list

        Run fn over items with bounded concurrency. Deterministic fan-out.

        Parameters:
            fn: async function (ctx, item) -> result
            items: list — items to process
            concurrency: int | None — max parallel executions; None uses this
                workflow's configured default concurrency

        Example:
            results = await ctx.map(classify_issue, issues, concurrency=8)
    """).strip(),
    "ctx.now": textwrap.dedent("""
        await ctx.now() -> datetime

        Journaled current UTC time. Async — must be awaited. Never use
        datetime.now()/date.today(): they are non-deterministic and would
        make a replayed run diverge from the original.

        Example:
            run_date = (await ctx.now()).date()
    """).strip(),
    "ctx.random": textwrap.dedent("""
        await ctx.random() -> float

        Journaled random float in [0, 1). Async — must be awaited. Never use
        random.random() (non-deterministic on replay).

        Example:
            jitter_seconds = (await ctx.random()) * 5
    """).strip(),
    "ctx.uuid": textwrap.dedent("""
        await ctx.uuid() -> str

        Journaled UUID4 string. Async — must be awaited. Never use
        uuid.uuid4() (non-deterministic on replay).

        Example:
            idempotency_key = await ctx.uuid()
    """).strip(),
    "ctx.sleep": textwrap.dedent("""
        await ctx.sleep(seconds: float) -> None

        Journaled short pause; a no-op on replay. Async — must be awaited.
        For waits longer than a short pause, use a cron/interval trigger or
        ctx.wait_for_event() instead — both park the run rather than holding
        a sandbox slot.

        Example:
            await ctx.sleep(2)
    """).strip(),
    "ctx.wait_for_event": textwrap.dedent("""
        await ctx.wait_for_event(event_type: str, *, timeout_s: float | None = None) -> dict

        Suspend the workflow until a matching event arrives (forever-running pattern).
        Returns the event payload as a dict.

        Example:
            event = await ctx.wait_for_event("slack.message.posted", timeout_s=3600)
            channel_id = event["channel"]["id"]
    """).strip(),
    "ctx.request_approval": textwrap.dedent("""
        await ctx.request_approval(label: str, *, payload: Any = None) -> bool

        Suspend and request human approval. Returns True if approved, False if denied.

        Example:
            approved = await ctx.request_approval("Deploy to production", payload={"version": "v1.2"})
            if not approved:
                ctx.log("Deployment denied")
                return
    """).strip(),
    "events/slack.message.posted": textwrap.dedent("""
        Event type: slack.message.posted
        Source app: slack
        Provider event: message.channels

        Payload schema:
            channel: {id: str, name: str}
            user: {id: str, name: str}
            text: str
            ts: str
            thread_ts: str | None

        Filterable fields:
            channel.id  [eq]     options_tool: slack__list_channels
            user.id     [eq]     options_tool: slack__list_users

        Name resolution: call ctx.tool("slack__list_channels") to map "#eng-alerts" → "C0123"

        Example subscription:
            @workflow(name="alert_watcher", triggers=[on_event("slack.message.posted", channel="C0123")])
            async def handle_alert(ctx: Ctx, event: dict) -> None:
                ctx.log("Message received", text=event.get("text", "")[:100])
    """).strip(),
    "events/jira.issue.created": textwrap.dedent("""
        Event type: jira.issue.created
        Source app: jira
        Provider event: jira:issue_created

        Payload schema:
            issue: {key: str, id: str, summary: str, status: str, priority: str, assignee: str}
            project_key: str

        Filterable fields:
            project_key    [eq]    options_tool: jira__list_projects
            issue.type     [eq]
            issue.status   [eq]
            issue.priority [eq]

        Example subscription:
            @workflow(name="bug_triage", triggers=[on_event("jira.issue.created", project_key="MYPROJ")])
            async def triage_bug(ctx: Ctx, event: dict) -> None:
                issue = event.get("issue", {})
                if issue.get("priority") == "Critical":
                    await ctx.tool("slack__send_message", channel="C0BUG", text=f"Critical bug: {issue['key']}")
    """).strip(),
}


class SdkReferenceTool(Tool):
    """Look up a specific Workflow SDK symbol, event type, or tool path.

    Use this when you need the exact signature/schema for a specific item.
    Faster than searching staged files. Returns machine-actionable reference text.
    """

    @property
    def name(self) -> str:
        return "sdk_reference"

    @property
    def short_description(self) -> str:
        return "Look up a Workflow SDK symbol, event type, or tool schema."

    @property
    def description(self) -> str:
        return (
            "Look up a Workflow SDK symbol, event type, or tool. "
            "symbol examples: 'workflow', 'step', 'triggers', 'ctx.tool', 'ctx.agent', "
            "'ctx.create_agent', 'ctx.search', 'ctx.state', 'ctx.emit', 'ctx.map', "
            "'ctx.now', 'ctx.random', 'ctx.uuid', 'ctx.sleep', "
            "'ctx.wait_for_event', 'ctx.request_approval', "
            "'events/slack.message.posted', 'events/jira.issue.created'. "
            "Returns signature, docstring, and example."
        )

    @property
    def path(self) -> str:
        return "/toolsets/tasks/sdk_reference"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="symbol",
                type=ParameterType.STRING,
                description=(
                    "SDK symbol or event type to look up "
                    "(e.g. 'ctx.tool', 'events/slack.message.posted')."
                ),
                required=True,
            )
        ]

    async def execute(self, **kwargs: Any) -> ToolOutput:
        symbol = str(kwargs.get("symbol", "")).strip()
        if not symbol:
            available = ", ".join(sorted(_SDK_SYMBOLS.keys()))
            return ToolOutput(success=False, data=f"symbol is required. Available: {available}")

        reference = _SDK_SYMBOLS.get(symbol)
        if reference is None:
            matches = [k for k in _SDK_SYMBOLS if symbol.lower() in k.lower()]
            if matches:
                return ToolOutput(success=True, data={
                    "symbol": symbol,
                    "found": False,
                    "suggestions": matches,
                    "message": f"Symbol '{symbol}' not found. Did you mean: {', '.join(matches)}?",
                })
            available = ", ".join(sorted(_SDK_SYMBOLS.keys()))
            return ToolOutput(
                success=False,
                data=f"Symbol '{symbol}' not found. Available: {available}",
            )

        return ToolOutput(success=True, data={
            "symbol": symbol,
            "reference": reference,
        })
