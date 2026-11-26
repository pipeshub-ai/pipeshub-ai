# pyright: basic
"""Well-known `Tag` values the turn loop, `AgentRuntime`, and builtin
middleware key off of, instead of hardcoded `if call.name == "..."` string
comparisons scattered across `agent/__init__.py`, `runtime.py`, `tool_loop.
py`, and `hooks/middleware/builtin/supervisor_gate.py`.

Each constant here names a CAPABILITY, not a specific tool — "this call
should be pre-launched as an independent spawn task", "this call, if it
succeeds, ends the run", "an identical repeat of this call within one turn
is wasted work", "this call produced a plan a confidence gate should
evaluate". Any `Tool` (builtin or a host application's own) opts into that
capability by including the matching tag in its `tags` property; dispatch
sites then ask "does this tool have tag X", never "is this tool literally
named Y". Adding a new spawn-like or terminal-like tool anywhere in the
codebase needs no edit to `agent/__init__.py` or `tool_loop.py` — the
existing dispatch already recognizes the tag.

Declaring the tag value here rather than as a bare `Tag("spawn", "true")`
literal at each call site is what makes this enforceable in practice: grep
for a `TAG_*` name to find every consumer of a given capability, and a typo
in a tag's key/value is a single point of failure to fix rather than N
independently-drifting literals.
"""

from __future__ import annotations

from harness_core.tools.base import Tag

__all__ = [
    "EFFECT_TAG_KEY",
    "FLOW_TAG_KEY",
    "PROVIDERS",
    "PROVIDER_TAG_KEY",
    "RISK_TAG_KEY",
    "SINK_TAG_KEY",
    "TAG_DEDUP_EXACT",
    "TAG_EFFECT_EXECUTE",
    "TAG_EFFECT_READ",
    "TAG_EFFECT_WRITE",
    "TAG_FLOW_OPENS_ADDRESS",
    "TAG_FLOW_PLATFORM_WEB",
    "TAG_LIFECYCLE_TERMINAL",
    "TAG_OUTPUT_DERIVED",
    "TAG_PLANNING_CREATE_PLAN",
    "TAG_PROVIDER_CONNECTOR",
    "TAG_PROVIDER_PLATFORM",
    "TAG_RISK_DESTRUCTIVE",
    "TAG_SPAWN",
    "TAG_SINK_EXTERNAL",
    "TAG_SINK_NONE",
    "TAG_SINK_SELF",
    "TAG_SPAWN_BATCH",
    "TAG_UI_ONLY",
]

# A tool call that launches one or more child agent runs through
# `AgentRuntime.run_child()` — `run_child()`'s own spawn-depth guard strips
# every tool_names entry carrying this tag once a child is one hop from
# `MAX_SPAWN_DEPTH`, instead of hardcoding the literal names "spawn_agent"
# and "best_of_n". Both `SpawnAgentTool` and `BestOfNTool` carry it; a
# future spawn-like tool needs no edit to the guard itself.
TAG_SPAWN = Tag("spawn", "true")

# Narrower than `TAG_SPAWN`: a call that must be pre-launched as an
# independent `asyncio.Task` via `agent/spawn_scheduler.py::
# schedule_spawn_batch` BEFORE the turn's non-spawn tool calls run, and
# awaited separately afterward — see `Agent.step()`'s `spawn_calls`/
# `parallel_calls` partitioning. Only `SpawnAgentTool` carries this one;
# `BestOfNTool` schedules its own candidates internally via
# `agent/best_of_n.py::run_best_of_n` and must NOT be pulled into that
# batch, so it gets `TAG_SPAWN` without `TAG_SPAWN_BATCH`.
TAG_SPAWN_BATCH = Tag("spawn", "batch")

# A tool call whose SUCCESSFUL result ends the current agent run — the turn
# loop calls the resolved tool's own `extract_outcome()` (see `tools.base.
# TerminalTool`) to get the stop signal and final output, instead of
# hardcoding `TaskCompleteTool.extract_outcome` by name.
TAG_LIFECYCLE_TERMINAL = Tag("lifecycle", "terminal")

# An identical repeat of this call (same name + same arguments) within one
# turn is redundant, side-effect-free work worth short-circuiting — see
# `agent/tool_loop.py::compute_duplicate_flags`.
TAG_DEDUP_EXACT = Tag("dedup", "exact")

# A tool call that produces a `Plan` (structured or free-form) whose
# confidence rating `supervisor_confidence_gate` should evaluate — see
# `hooks/middleware/builtin/supervisor_gate.py`.
TAG_PLANNING_CREATE_PLAN = Tag("planning", "create_plan")

# A tool that talks directly to the top-level HUMAN user through a UI
# surface the model doesn't otherwise render (e.g. a tappable-option
# question card) rather than returning data for the calling agent to
# reason over. Meaningful only for the agent the human is actually
# watching — `AgentRuntime.run_child()` strips every `TAG_UI_ONLY` tool
# from a spawned child's grant unconditionally (regardless of spawn
# depth), the same way it strips `TAG_SPAWN` tools one hop from
# `MAX_SPAWN_DEPTH`: a child's "question" has nowhere to go (there is no
# UI surface watching its own turns) and no way back into the parent's
# turn loop, so granting it one just gives the model a way to stall a
# whole spawn tree waiting on an answer nobody will ever provide.
TAG_UI_ONLY = Tag("interaction", "ui_only")

# What a call does outside the run, from a review of the tool's code and the
# API it calls (PA-5): `read` changes nothing anywhere, so a durable run may
# re-execute it after a crash; `write` (and no tag) is treated as an effect;
# `execute` runs model-written code. Every tool a durable run can reach carries
# one (P2.1, tests/unit/harness/test_effect_tags_complete.py); see
# docs/agent-harness-v2/impl/tool-effects.md.
EFFECT_TAG_KEY = "effect"
TAG_EFFECT_READ = Tag(EFFECT_TAG_KEY, "read")
TAG_EFFECT_WRITE = Tag(EFFECT_TAG_KEY, "write")
TAG_EFFECT_EXECUTE = Tag(EFFECT_TAG_KEY, "execute")

# Where a tool's arguments go (the sink's `egress`, UX1). An untagged write is `external`: every
# write may leave the deployment unless the tool says otherwise. `none`: the deployment's own
# storage (a conversation's artifacts, the org's skill library, a sandbox without network);
# `self`: the acting user's own space (a new file in their own drive). An untagged read is `self`:
# it asks the system it reads, with the user's own access; a read whose request carries what the
# model chose to a host outside the organization (a URL to fetch, a web search) is `external`
# (FG2). The flow rule holds an `external` call, read or write, once the run has read private data.
SINK_TAG_KEY = "sink"
TAG_SINK_NONE = Tag(SINK_TAG_KEY, "none")
TAG_SINK_SELF = Tag(SINK_TAG_KEY, "self")
TAG_SINK_EXTERNAL = Tag(SINK_TAG_KEY, "external")

# The platform's own public-web read (web search, page fetch): its request does reach an outside host (`sink=external`),
# but through code that redacts secrets, refuses private addresses and internal URLs and labels what comes back as
# outsider-written, so the flow rule does not hold it (``harness_core.effects.flow.flow_holds``). An approval layer's
# own `tools` rule still asks about it. Honoured only on a read whose provider is `platform`, so a connector or MCP
# tool that carried it would still be held.
FLOW_TAG_KEY = "flow"
TAG_FLOW_PLATFORM_WEB = Tag(FLOW_TAG_KEY, "platform_web")
# Of those, the read that opens an address its call names (page fetch), where the address itself can carry what the
# run read: after a private read it runs only for an address the run was given (a search result, the person's own
# message or file, a record's link), and is refused otherwise (``harness_core.effects.flow.unvouched_address``).
TAG_FLOW_OPENS_ADDRESS = Tag(FLOW_TAG_KEY, "opens_address")

# A tool that cannot be undone (`destructive`: a delete, a send) or that moves money (`financial`), as its source
# declares it: the risk class's mark (OWD-7, `harness_core.effects.risk`). Metadata for policy and audit; which calls
# ask stays the approval rules' decision.
RISK_TAG_KEY = "risk"
TAG_RISK_DESTRUCTIVE = Tag(RISK_TAG_KEY, "destructive")

# What a tool's result shows the model is computed only from the call's own arguments by the platform's code (a
# calculator, the image generator's acknowledgement): it brings nothing the run had not read already, so the taint
# ledger counts it as no new content instead of unknown (FLOWTRUST D3). Reviewed per tool; a
# connector, MCP or web tool never carries it, and the ledger honours it only on a platform tool.
TAG_OUTPUT_DERIVED = Tag("output", "derived")

# Who provides a tool, as its registration declares it (never read from its name or path, KN2): `platform`, the
# host's own tool, allowed by the run's other fields; `connector`, a configured integration's, granted by its pinned
# instance. A tool without one is granted to no run.
PROVIDER_TAG_KEY = "provider"
TAG_PROVIDER_PLATFORM = Tag(PROVIDER_TAG_KEY, "platform")
TAG_PROVIDER_CONNECTOR = Tag(PROVIDER_TAG_KEY, "connector")
PROVIDERS = frozenset({TAG_PROVIDER_PLATFORM.value, TAG_PROVIDER_CONNECTOR.value})
