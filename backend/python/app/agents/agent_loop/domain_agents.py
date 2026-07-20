"""Domain-agent catalog: PipesHub's scoped sub-agents, exposed as tools.

Instead of granting the top-level agent every registered tool schema (30+
per turn), each capability domain becomes ONE `AgentTool` — an entire
ReAct `Agent` (same `agent_loop_lib.Agent` loop, just a narrower
`AgentSpec`) callable like any other tool. The top-level agent reasons
over a handful of high-level delegates plus whatever residual tools no
domain claimed; each child reasons over only its own domain's tools.

Design:

- `DomainAgentDefinition` is pure data (which tools a domain claims, how
  the delegate is described to the calling model, which OTHER domain
  agents it may itself call). Adding a domain = adding one entry to
  `DOMAIN_AGENT_DEFINITIONS`; nothing in `plan_domain_agents()`,
  `register_domain_agents()`, or `PipesHubAgentFactory` changes
  (open/closed).
- Claiming tools off the request's already-loaded `ToolRegistry` is split
  from actually building/registering agents (`plan_domain_agents()` vs.
  `register_domain_agents()`) so callers that need to know the FINAL
  top-level tool grant before a runtime/loop exists (the quick-mode
  planner, see `factory.py`) can do so without any registry mutation, and
  callers that already have a runtime (deep mode's spawn machinery, the
  eventual registration step) can build off that same decision instead of
  re-deriving it. Availability falls out naturally either way: a request
  with no web-search provider configured never loads `web_search`, so no
  `web_agent` is planned and the definition is silently skipped — same for
  `internal_exploration_agent` when no knowledge node is attached (no
  `retrieval`/`knowledgehub` tools load, so nothing is claimed). This IS
  the availability contract for both delegates — no separate gating check
  exists or should be added elsewhere; see `test_domain_agents.py`'s
  `TestAvailabilityGating` for the tests that pin it down.
- Children run through `AgentTool.handle()` → `AgentRuntime.run_child()`
  on the SAME runtime — the shared hook kernel (citations, result
  accumulation, SSE status) and event emitter see child tool calls
  exactly as they see top-level ones, and `RunContext.spawn_depth`
  guards recursion (coding_agent → web_agent is depth 2 of 3).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.tools.builtin.coordination.agent_tool import AgentTool
from app.agent_loop_lib.tools.errors import DuplicateToolNameError, DuplicateToolPathError
from app.agents.agent_loop.sub_agent_prompt import build_sub_agent_prompt
from app.modules.agents.capability_summary import (
    build_connector_routing_rules,
    classify_knowledge_sources,
)

if TYPE_CHECKING:
    from app.agent_loop_lib.runtime.runtime import AgentRuntime
    from app.agent_loop_lib.tools.registry import ToolRegistry
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)

# Matches OrchestratorLoop's sub-agent cap: enough turns for a multi-step
# task plus a retry or two, low enough that a flailing child can't dominate
# the request's latency budget.
_DEFAULT_CHILD_MAX_TURNS = 8

# `internal_exploration_agent` needs more room than the other domains: it
# is expected to fan out across several connectors, re-formulate a thin
# query, browse via `knowledgehub`, and fetch full records before
# concluding — a single-digit budget cuts that iteration short.
_EXPLORATION_MAX_TURNS = 12

_EXPLORATION_PLAYBOOK = """\
## Exploration Playbook

You are an ITERATIVE explorer, not a single-shot search box. Keep searching, \
browsing, and fetching until the goal is genuinely satisfied or you have \
exhausted reasonable avenues — do not stop after one empty or thin result.

### Turn 1 — fan out in parallel
- If the goal does not name a specific connector or source, issue ALL of \
these in the SAME turn: one broad search across every source (omit \
`connector_ids`) PLUS one scoped search per source listed below that looks \
relevant to the topic. Independent tool calls issued in one turn run \
concurrently — use that instead of searching sources one at a time across \
turns.
- If the goal DOES name a connector/source, search that one first — but \
still fan out to the others in parallel on a later turn if those results \
are thin.
- If your source list has only ONE connector, also issue a broad search \
(omit `connector_ids` entirely) in that same turn — a single-connector \
inventory does not mean other indexed sources don't exist; the broad call \
catches anything outside that one listed source.

### Iterate when results are thin
- An empty or thin result is a signal to try again, not to conclude "not \
found": reformulate the query (synonyms, broader/narrower terms, different \
phrasing), try `knowledgehub.list_files` to browse by structure when a \
keyword search comes up empty, and widen scope to sources you did not \
search on turn 1.

### Full-Record Fetch Policy (MANDATORY — do not skip this)
Once you have called a search tool at least once and it returned results, \
a tool named `dynamic_fetch_full_record` becomes available to you (it does \
NOT exist before that — do not treat its absence on turn 1 as an error). \
**You MUST call it before answering whenever ANY of these are true:**
1. The blocks you retrieved are only partial — there are gaps, truncation, \
or missing sections.
2. The goal asks for comprehensive, full, or complete details about a \
topic or document, not just a snippet.
3. You are not confident you can give a thorough answer from the blocks \
alone.
4. The goal is about one specific document/record and you only have a \
few blocks from it.
5. The record is a ticket/issue (e.g. Jira) and the goal needs live \
fields — status, sprint, assignee, story points — that may be stale in \
the indexed snippet.
6. **DEFAULT: when in doubt about a record that is clearly relevant to \
the goal, call it.** An incomplete answer on a relevant record is worse \
than one extra tool call.

**Relevance gate — only fetch what is actually relevant to the goal.** \
Search commonly returns documents that are topically adjacent but not what \
the goal is asking about. If search returned many documents but only a \
handful are clearly about the goal's topic, fetch full records for ONLY \
those — do not fetch every result from a broad search just because it was \
returned.

Only skip fetching entirely when the retrieved blocks already fully and \
directly answer the goal.

**How to call it:** pass the exact `Record ID :` value(s) shown in each \
record's context metadata as `record_ids` — a list, ALL the IDs you need \
in ONE call. Never invent, guess, or reuse an example ID.

### Reporting Record IDs for follow-up
When you return your final answer, if there is a specific record that is \
clearly central to the goal but you did NOT fetch its full content (or \
the calling agent may reasonably want more than your summary), mention \
its exact `Record ID :` value inline next to that finding — e.g. "(Record \
ID: 6f3a1c2e-...)". The calling agent may have direct access to \
`dynamic_fetch_full_record` itself once you have searched, and can use \
the ID you report to fetch more detail without asking you again.

### Stop condition
- Stop as soon as the goal is genuinely satisfied — do not keep searching \
once you have enough to answer confidently.
- Only report "not found" after you have fanned out across every listed \
source AND tried at least one reformulated query.
"""


def _internal_exploration_instructions(context: "AgentContext | None") -> str | None:
    """Per-request connector inventory + the fan-out/iteration playbook
    above, appended to `internal_exploration_agent`'s child prompt via
    `DomainAgentDefinition.instructions_factory`. Built from
    `context.agent_knowledge`/`context.connector_configs` — the SAME
    source `capability_summary.py`'s parent-facing routing rules use
    (`classify_knowledge_sources`/`build_connector_routing_rules`) — so
    the child sees the identical per-request source list (labels,
    `connector_ids`) the parent prompt does, instead of a generic "you
    have search tools" placeholder it would otherwise have to guess
    connector identifiers for.

    Always returns AT LEAST the generic playbook — a request with no
    resolved connector inventory (empty `agent_knowledge`, or
    `build_connector_routing_rules()` returning nothing) still gets the
    fan-out/iteration/fetch-policy guidance; only the per-request routing
    rules on top of it are conditional. Returning `None` here would
    silently drop ALL exploration guidance, not just the connector list."""
    if context is None or not context.agent_knowledge:
        return _EXPLORATION_PLAYBOOK
    sources = classify_knowledge_sources(
        context.agent_knowledge, connector_configs=context.connector_configs,
    )
    routing_rules = build_connector_routing_rules(sources, call_format="planner")
    if not routing_rules:
        return _EXPLORATION_PLAYBOOK
    return f"{_EXPLORATION_PLAYBOOK}\n{routing_rules}"


@dataclass(frozen=True)
class DomainAgentDefinition:
    """Declarative description of one domain agent. Pure data — claiming
    lives in `plan_domain_agents()`; spec construction and registration
    live in `register_domain_agents()`."""

    name: str
    """LLM-facing tool name of the delegate, e.g. ``"coding_agent"``."""

    domain: str
    """Human label used in the child's system prompt ("web", "coding", ...)."""

    description: str
    """Tool description shown to the CALLING agent — this is the only thing
    the parent model sees, so it must say what to delegate and what comes back."""

    app_names: frozenset[str] = frozenset()
    """Claim every registered tool whose adapter `app_name` is in this set."""

    tool_names: frozenset[str] = frozenset()
    """Claim these exact registered tool names (for tools whose `app_name`
    is shared with unrelated tools, e.g. the synthetic ``"dynamic"`` bucket)."""

    delegate_agents: tuple[str, ...] = ()
    """Names of OTHER domain agents this agent may call as tools — wired
    only when the referenced agent was actually built for this request."""

    extra_instructions: str | None = None
    """Static text appended to the child's system prompt, same for every
    request. For per-request text (e.g. a connector inventory that
    differs by org/agent), use `instructions_factory` instead — the two
    compose (both are appended) rather than one overriding the other."""

    instructions_factory: Callable[["AgentContext | None"], str | None] | None = None
    """Optional per-request instructions builder, called with this
    request's `AgentContext` at registration time (see
    `register_domain_agents()`). Returns `None` to add nothing — lets a
    domain (e.g. `internal_exploration_agent`) see request-scoped data
    (connector inventory, knowledge sources) that a static
    `extra_instructions` string cannot, without `plan_domain_agents()`/
    `register_domain_agents()` themselves needing to know anything
    domain-specific (open/closed: adding this to a new domain later means
    adding one function, not touching this module's control flow)."""

    max_turns: int = _DEFAULT_CHILD_MAX_TURNS

    model: str | None = None
    """Optional per-domain model tier override — `None` (default) inherits
    the request's own `model_name` (`register_domain_agents(model_name=...)`),
    same as every domain got before this field existed. Set this for a
    domain whose work reliably needs LESS capability than the rest of the
    request (e.g. `calculator_agent`'s single arithmetic call) or MORE
    (a domain doing multi-step synthesis) — the override applies to the
    domain's OWN `AgentSpec`, so it takes effect whether that domain is
    reached via static composition, `spawn_agent`'s direct-AgentTool
    dispatch, or the generic spawn wrapper calling it as a tool (all three
    ultimately run THIS spec — see `AgentTool.handle()`/`run_spawned_child`).
    Keeps `provider` fixed to the request's own provider: this is a tier
    swap within one provider's model family, not a cross-provider switch."""

    share_parent_results: bool = False
    """When True, `register_domain_agents()` builds this domain's
    `AgentTool` with `share_parent_results=True` (see that class's
    docstring): the calling agent's own tool results from its current leg
    of work are deterministically appended to the child's goal and staged
    as a file for its sandbox, instead of relying on the calling model to
    paste data into the goal text itself. Only meaningful for a domain
    whose children can consume arbitrary prior tool output as data —
    `coding_agent` is the only current user."""

    result_note: str | None = None
    """Passed to `AgentTool(result_note=...)`: appended verbatim to the
    child's successful output before the calling agent sees it. Use for
    presentation rules that MUST survive a very large parent context —
    a rule in the parent's system prompt ("don't summarize the delegate's
    answer") sits hundreds of thousands of tokens away from the result it
    governs and gets ignored; the same rule inline at the end of the tool
    result does not (see `AgentTool`'s class docstring)."""


# Appended to every successful `internal_exploration_agent` result (see
# `DomainAgentDefinition.result_note`). Lives here, adjacent to the data,
# because the equivalent rule in the parent's system prompt was measurably
# ignored on large-context requests: the parent condensed a multi-page
# evidence pack into one paragraph despite "do NOT condense" in its prompt.
#
# Deliberately does NOT forbid summarizing: when the user explicitly asks
# for a "quick"/"concise" answer, an absolute "never condense" rule loses
# to the user's own request and the model then discards the whole note —
# including the parts about concrete facts and citations. Requiring
# concreteness and citations AT ANY LENGTH is the invariant that holds
# whether the user wanted a one-paragraph summary or the full detail.
#
# Wording is intentionally descriptive, never imperative: this block ships
# INSIDE a tool result, which is exactly where provider prompt-injection
# shields look for instruction-like text. The original "[SYSTEM NOTE ...]"
# header tripped Azure OpenAI's jailbreak filter immediately; the V2
# "Guidance for using the findings above ... Rules: - **Match...**"
# rewrite still contained imperative bullets and "Do NOT" directives that
# triggered the same filter in `planExecute` mode, where this tool result
# sits in the top-level LLM context alongside injected phase-instruction
# user messages (a combination dense enough to cross the filter threshold,
# even though deep mode — where the result stays in a child agent's leaner
# context — was fine). This V3 text conveys the same semantics as a
# description of what the answer should look like, avoiding patterns the
# filter watches for: "Rules:", bold imperatives, "Do NOT", and anything
# that reads like system instructions overriding the model's behavior.
_EXPLORATION_RESULT_NOTE = """\

About these findings:
These findings are the evidence base for the final answer. The answer \
should match the depth the user asked for — comprehensive when they want \
full detail, concise when they asked for brevity. At any length, it \
should carry the specific facts from these findings (figures, metrics, \
names, dates, direct quotes) rather than vague paraphrases. For example, \
keep "seats grew ~9x year-over-year" rather than replacing it with \
"strong growth in seats." Citation markers and record links from these \
findings belong in the answer next to the facts they support, exactly as \
given. All findings relevant to the user's question should be included; \
only clearly irrelevant material should be left out."""


DOMAIN_AGENT_DEFINITIONS: tuple[DomainAgentDefinition, ...] = (
    DomainAgentDefinition(
        name="web_agent",
        domain="web research",
        description=(
            "Delegate public-web research to a focused sub-agent that can search "
            "the web and fetch/read specific URLs. Give it ONE self-contained "
            "research question (include all context it needs — it cannot see this "
            "conversation). Returns a synthesized answer with source links."
        ),
        tool_names=frozenset({"dynamic__web_search", "dynamic__fetch_url"}),
    ),
    DomainAgentDefinition(
        name="coding_agent",
        domain="coding",
        description=(
            "Delegate a coding task to a focused sub-agent that writes and runs "
            "code in a sandbox, installs packages, and reads files it produced. "
            "Use for computation, data analysis/transformation, chart or file "
            "generation. It can also generate AI images (via its own "
            "image-generation tool) and combine them into documents/decks. Give "
            "it ONE self-contained goal stating exactly what to "
            "compute or produce and what the expected output looks like — tool "
            "results YOU already gathered this turn are shared with it "
            "automatically (inline and as a file), so you do NOT need to paste "
            "large data into the goal yourself. It has NO credentials and cannot "
            "reach any connected or authenticated system (Jira, Slack, "
            "Confluence, Google, internal knowledge, databases, ...) — always "
            "fetch that data with the real tools FIRST, then delegate only the "
            "computation/file-generation step to this agent; never delegate data "
            "RETRIEVAL from a connected system to it. Returns the final result "
            "and paths of files it generated."
        ),
        tool_names=frozenset({
            "run_code", "install_packages", "read_sandbox_file",
            # Real AI image generation belongs with the domain that owns
            # file/asset generation: a goal like "generate 7 images then
            # build a deck from them" must not be answerable only by
            # hand-drawing PIL shapes because the child can't see the
            # image_generator tool the top level kept.
            "image_generator__generate_image",
        }),
        delegate_agents=("web_agent",),
        share_parent_results=True,
        extra_instructions=(
            "For pictures/illustrations (photos, scenes, objects, people, "
            "places), ALWAYS use the image_generator__generate_image tool — do "
            "NOT draw them programmatically with PIL/matplotlib shapes. "
            "Generated images are saved as artifacts automatically; pass their "
            "file names into run_code's input_artifacts to embed them in a "
            "document/presentation. Draw with code only for charts/graphs of "
            "data. Any file a run_code call writes is attached to the "
            "response automatically as a downloadable artifact — never re-run "
            "code to \"attach\" or \"provide\" files that were already produced."
        ),
    ),
    DomainAgentDefinition(
        name="internal_exploration_agent",
        domain="internal knowledge exploration",
        description=(
            "Delegate exploration of the organization's internal knowledge to a "
            "sub-agent that iteratively searches, browses, and fetches records "
            "until it has actually satisfied the goal — not a single lookup. It "
            "decides for itself whether to search one connector, fan out across "
            "several in parallel, or search everything, and keeps refining its "
            "queries when results are thin instead of giving up early. Give it "
            "ONE self-contained goal (include names, dates, and identifiers it "
            "needs; name a specific connector/source only if you already know "
            "it's the right one — otherwise let it decide). Returns DETAILED, "
            "COMPREHENSIVE findings — full document text, complete ticket "
            "bodies, all fields, with citations. Build your answer from those "
            "findings at the depth the user asked for, always keeping their "
            "specific facts, figures, and citations — never reduce them to a "
            "vague, uncited paraphrase."
        ),
        app_names=frozenset({"retrieval", "knowledgehub"}),
        tool_names=frozenset({"dynamic_fetch_full_record"}),
        instructions_factory=_internal_exploration_instructions,
        max_turns=_EXPLORATION_MAX_TURNS,
        result_note=_EXPLORATION_RESULT_NOTE,
    ),
    DomainAgentDefinition(
        name="calculator_agent",
        domain="calculation",
        description=(
            "Delegate arithmetic, unit, and date calculations to a focused "
            "sub-agent. Give it ONE self-contained calculation request with all "
            "numbers/dates inline. Returns the computed result."
        ),
        app_names=frozenset({"calculator", "date_calculator"}),
        max_turns=5,
    ),
    DomainAgentDefinition(
        name="calendar_agent",
        domain="calendar",
        description=(
            "Delegate calendar work to a focused sub-agent that can list, "
            "create, and update events and check availability on the user's "
            "connected calendars. Give it ONE self-contained request with "
            "explicit dates/times and attendees. Returns the outcome with event "
            "links."
        ),
        app_names=frozenset({"calendar", "google_calendar"}),
    ),
)


@dataclass(frozen=True)
class DomainAgentPlan:
    """The pure OUTCOME of claiming tools off a `ToolRegistry`: which
    domain agents WOULD be built and what the resulting top-level tool
    grant would be, computed with zero side effects — no `AgentSpec`, no
    `AgentTool`, no registry mutation. Callers that need to know the final
    top-level names before a runtime/loop exists (`factory.py`'s
    quick-mode planner, which must be steered with the SAME names the
    executing agent will end up with) can use `top_level_names` directly;
    `register_domain_agents()` later replays `claims`/`agent_names`
    unchanged to actually build things."""

    definitions: tuple[DomainAgentDefinition, ...]
    registered_names: tuple[str, ...]
    """Every tool name registered on the `ToolRegistry` this plan was
    computed from, at planning time — the residual grant is derived from
    this snapshot, not a live registry read, so it stays valid even after
    `register_domain_agents()` has added `AgentTool`s to that same registry."""
    claims: dict[str, list[str]] = field(default_factory=dict)
    """`{definition.name: [claimed tool names]}` for domains that claimed
    at least one tool — catalog order, first definition wins a tool."""

    @property
    def agent_names(self) -> list[str]:
        """Names of every domain agent that WOULD be built, catalog order."""
        return [d.name for d in self.definitions if d.name in self.claims]

    @property
    def top_level_names(self) -> list[str]:
        """The tool grant a top-level agent composed from this plan would
        end up with: domain-agent names + every tool no domain claimed."""
        claimed = {name for names in self.claims.values() for name in names}
        residual = [n for n in self.registered_names if n not in claimed]
        return [*self.agent_names, *residual]


def plan_domain_agents(
    tool_registry: "ToolRegistry",
    definitions: tuple[DomainAgentDefinition, ...] = DOMAIN_AGENT_DEFINITIONS,
) -> DomainAgentPlan:
    """Pure claim computation: assigns each registered tool to at most one
    domain (first definition wins, catalog order). No registration, no
    `AgentSpec` construction — safe to call before a runtime/loop exists
    (see `DomainAgentPlan`'s docstring) or purely to preview what
    `register_domain_agents()` would do."""
    claims: dict[str, list[str]] = {}
    claimed: set[str] = set()
    registered = tool_registry.names()
    for definition in definitions:
        names: list[str] = []
        for name in registered:
            if name in claimed:
                continue
            tool = tool_registry.resolve_by_name(name)
            app_name = getattr(tool, "app_name", None)
            if name in definition.tool_names or (app_name and app_name in definition.app_names):
                names.append(name)
                claimed.add(name)
        if names:
            claims[definition.name] = names
    return DomainAgentPlan(definitions=definitions, registered_names=tuple(registered), claims=claims)


def _combine_instructions(static: str | None, from_factory: str | None) -> str | None:
    """Both `extra_instructions` and `instructions_factory` are additive,
    not mutually exclusive — a domain can have static rules AND a
    per-request block computed from `AgentContext`."""
    parts = [p for p in (static, from_factory) if p]
    return "\n\n".join(parts) if parts else None


def register_domain_agents(
    plan: DomainAgentPlan,
    tool_registry: "ToolRegistry",
    runtime: "AgentRuntime",
    context: "AgentContext | None" = None,
    *,
    provider: str,
    model_name: str,
    lazy_tools: "Callable[[ToolRegistry, list[str]], tuple[list[str], str]] | None" = None,
    shared_tool_names: frozenset[str] = frozenset(),
) -> list[str]:
    """Materializes `plan`: builds one ReAct child `AgentSpec` per claimed
    domain and registers each as an `AgentTool` on `tool_registry`. Returns
    the tool names the TOP-LEVEL agent should actually be granted —
    ordinarily identical to `plan.top_level_names`, except a domain whose
    name collides with an already-registered tool is skipped (logged), and
    its claimed tools fall back to the residual grant instead of being
    silently dropped.

    `lazy_tools`, when given, is called once per domain with `(tool_registry,
    [*claimed_names, *delegate_names])` and must return `(tool_names,
    tool_disclosure)` for that child's `AgentSpec` — `tool_names` unchanged
    and `tool_disclosure="eager"` for a no-op decision, or `tool_names`
    augmented with meta-tool names (`list_toolsets`/...) and
    `tool_disclosure="lazy"` if it decided to group this domain's tools
    into lazily-disclosed toolsets. Keeps this module ignorant of env
    flags/thresholds/app_name grouping — see `lazy_tools_wiring.py::
    make_lazy_tools_decider`, the only real implementation of this
    callback today. Defaults to a no-op (every child stays eager, exactly
    today's behavior) when `None`.

    `shared_tool_names`, when given, is granted to EVERY built domain in
    ADDITION to its own claimed tools — for meta-capabilities that no
    single domain "owns" but every domain should still be able to reach
    (today: the read-only skill tools `load_skill`/`skill_search`, passed
    by `factory.py` when the skills subsystem is on). These are NOT
    marked as claimed by anyone: the top level keeps them too (they were
    already in its residual grant before this call, since no
    `DomainAgentDefinition` lists them in `tool_names`/`app_names` — see
    `skills_wiring.py`'s "residual grant" comment), and a name not
    actually registered on `tool_registry` this request (skills
    disabled, or any other tool from a future caller) is silently
    skipped rather than granting a nonexistent tool. Without this, a
    domain agent's own `skill_preloading` PRE_AGENT pass (see that
    middleware's docstring) can only ever inject a skill's full body —
    never a "call load_skill if relevant" pointer, since the domain
    agent would have no `load_skill` to call.
    """
    claims = plan.claims
    registered = set(plan.registered_names)
    built: list[str] = []
    for definition in plan.definitions:
        claimed_names = claims.get(definition.name)
        if not claimed_names:
            continue
        delegate_names = [d for d in definition.delegate_agents if d in claims]
        shared_names = [n for n in shared_tool_names if n in registered]
        extra_instructions = _combine_instructions(
            definition.extra_instructions,
            definition.instructions_factory(context) if definition.instructions_factory else None,
        )
        child_tool_names, tool_disclosure = (
            lazy_tools(tool_registry, [*claimed_names, *delegate_names, *shared_names])
            if lazy_tools is not None
            else ([*claimed_names, *delegate_names, *shared_names], "eager")
        )
        spec = AgentSpec(
            name=definition.name,
            description=definition.description,
            system_prompt=build_sub_agent_prompt(
                definition.domain, context,
                extra_instructions=extra_instructions,
            ),
            tool_names=child_tool_names,
            tool_disclosure=tool_disclosure,
            model=ModelSpec(provider=provider, model=definition.model or model_name),
            loop=ReActLoop(),
            max_turns=definition.max_turns,
        )
        try:
            tool_registry.register_tool(
                AgentTool(
                    spec, runtime, name=definition.name, description=definition.description,
                    share_parent_results=definition.share_parent_results,
                    result_note=definition.result_note,
                )
            )
        except (DuplicateToolNameError, DuplicateToolPathError):
            logger.warning(
                "register_domain_agents: name %r already registered — skipping this domain agent",
                definition.name,
            )
            continue
        built.append(definition.name)
        logger.info(
            "register_domain_agents: built %s with %d tool(s) %s + delegates %s",
            definition.name, len(claimed_names), claimed_names, delegate_names,
        )

    # Only tools claimed by an agent that actually got built leave the
    # top level — a skipped registration must not orphan its tools. Uses
    # `plan.registered_names` (the pre-registration snapshot), not a fresh
    # `tool_registry.names()` read, so the newly-added AgentTool names
    # themselves can never leak into the residual list.
    all_claimed = {name for agent_name in built for name in claims[agent_name]}
    residual = [n for n in plan.registered_names if n not in all_claimed]
    return [*built, *residual]


def compose_domain_agents(
    tool_registry: "ToolRegistry",
    runtime: "AgentRuntime",
    context: "AgentContext | None" = None,
    *,
    provider: str,
    model_name: str,
    definitions: tuple[DomainAgentDefinition, ...] = DOMAIN_AGENT_DEFINITIONS,
    lazy_tools: "Callable[[ToolRegistry, list[str]], tuple[list[str], str]] | None" = None,
    shared_tool_names: frozenset[str] = frozenset(),
) -> list[str]:
    """Convenience: plan + register in one call, for callers that don't
    need the plan/register split (e.g. tests, or a caller with no need to
    steer anything on the plan before registration happens)."""
    plan = plan_domain_agents(tool_registry, definitions)
    return register_domain_agents(
        plan, tool_registry, runtime, context,
        provider=provider, model_name=model_name, lazy_tools=lazy_tools,
        shared_tool_names=shared_tool_names,
    )


__all__ = [
    "DOMAIN_AGENT_DEFINITIONS",
    "DomainAgentDefinition",
    "DomainAgentPlan",
    "compose_domain_agents",
    "plan_domain_agents",
    "register_domain_agents",
]
