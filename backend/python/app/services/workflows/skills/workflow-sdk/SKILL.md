# PipesHub Workflow SDK

Write workflows as async Python functions decorated with `@workflow` and `@step`.
Import: `from app.services.workflows.sdk import workflow, step, Ctx, SideEffect`

## Core decorators

```python
@workflow(name="my_workflow", inputs=InputModel, outputs=OutputModel)
async def my_workflow(ctx: Ctx, inp: InputModel) -> OutputModel:
    ...

@step(retries=3, timeout_s=60, side_effect=SideEffect.READ)
async def my_step(ctx: Ctx, arg: str) -> list[dict]:
    ...
```

## Ctx surface (ALWAYS use these — never raw datetime/random/uuid/network)

| Method | Returns | Notes |
|--------|---------|-------|
| `await ctx.tool("jira__create_issue", **kwargs)` | Any | Call any registered tool (use Tool.name form) |
| `handle = await ctx.agent("agent-uuid")` → `await handle.run(goal="...")` | Any | Run an existing Agent Builder agent |
| `handle = await ctx.create_agent("name", instructions="...", tools=[...])` → `await handle.run(goal="...")` | Any | Create + run an ephemeral agent |
| `await ctx.search("query", collections=[...], limit=10)` | list[dict] | Knowledge base search |
| `await ctx.state.get("key")` | Any | Read durable per-workflow state |
| `await ctx.state.set("key", value)` | None | Write durable state (WRITE step) |
| `await ctx.emit("msg", kind="text")` | None | Post to originating chat |
| `await ctx.map(fn, items, concurrency=8)` | list | Parallel fan-out |
| `await ctx.now()` | datetime | Journaled current time |
| `await ctx.random()` | float | Journaled random [0,1) |
| `await ctx.uuid()` | str | Journaled UUID4 |
| `await ctx.sleep(seconds)` | None | Journaled pause, 60s max — longer waits belong on a trigger |
| `await ctx.wait_for_event("type")` | dict | Suspend until event |
| `await ctx.request_approval("label")` | bool | Durable HIL pause |
| `ctx.log("msg", **data)` | None | Structured log (not journaled) |

## Tool naming convention

Use `Tool.name` form (`jira__create_issue`) in `ctx.tool()` — this is what the LLM sees in tool schemas. URL-path form (`/tools/jira/create_issue`) and dot form (`jira.create_issue`) are also accepted and normalized at the broker boundary.

## Hard rules (violations → typecheck failure)
- NEVER use `datetime.now()`, `random.random()`, `uuid.uuid4()` — use ctx.now/random/uuid
- NEVER import `os`, `subprocess`, `socket`, `requests`, `httpx` directly
- NEVER use `open()` for file I/O
- All workflow functions must be `async def`
- Steps must call `ctx.tool` for external I/O, not import SDK clients
- Annotate write steps: `@step(side_effect=SideEffect.WRITE)` — these are never re-run on replay

## Example: Jira issue triage

```python
from app.services.workflows.sdk import workflow, step, Ctx, SideEffect
from pydantic import BaseModel

class IssueIn(BaseModel):
    project_key: str

class TriageOut(BaseModel):
    critical_count: int

@step(retries=2, side_effect=SideEffect.READ)
async def fetch_issues(ctx: Ctx, project_key: str) -> list[dict]:
    return await ctx.tool("jira/search_issues", jql=f"project={project_key} AND priority=Critical")

@workflow(name="triage_issues", inputs=IssueIn, outputs=TriageOut)
async def triage(ctx: Ctx, inp: IssueIn) -> TriageOut:
    issues = await fetch_issues(ctx, inp.project_key)
    if len(issues) > 10:
        summary = await ctx.agent("summarizer", goal=f"Summarize {len(issues)} critical issues")
    return TriageOut(critical_count=len(issues))
```

## Event subscriptions

```python
# Subscribe to Slack messages in a specific channel
@workflow(name="slack_listener", on_event=slack.message_posted(channel_id=In(["C0123"])))
async def handle_message(ctx: Ctx, event: SlackMessagePosted) -> None:
    await ctx.tool("slack/reply", channel=event.channel.id, text="Received!")
```

Use `sdk_reference("events/slack.message.posted")` for full event schemas.
