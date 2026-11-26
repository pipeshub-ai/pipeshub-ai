"""``RunContext``: what one durable run is, typed, with no credential reachable (D-2).

It replaces ``AgentContext.tool_state`` (the 113-key ``ChatState``) as the
thing the durable runtime is keyed by. Everything here comes from the pinned
``RunSpec`` and the admitted run snapshot; secrets (toolset and MCP
credentials, web-search keys, tokens) live only in the trusted tool
environment the assembler builds from it (``tool_environment.py``).
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, cast

from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

from app.agents.run_definition import ASSISTANT_AGENT_KEY
from app.control_plane.contracts.generated.resolved_run_config import ResolvedRunConfig
from app.control_plane.runtime.pinning import pin_digest
from app.harness.assembly.descriptor_pins import DescriptorPin, pinned_descriptors
from app.harness.contracts.generated.pipeshub.run_spec import (
    Audience,
    PipeshubRunSpecExtension,
)
from harness_core.contracts import canonical_digest, dump_contract, validate_contract
from harness_core.driver.refusal import RunRefused
from harness_core.kernel.durable.audience import HistoryAudience, project_turns
from harness_core.workspace.forks import principal_workspace_id

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from pydantic import RootModel

    from app.agents.chat_modes.policy import ChatModePolicy
    from app.harness.contracts.state import RunInputSnapshot
    from app.harness.journal_node import NodeClaim
    from harness_core.contracts.generated.run_spec import RunSpec

PIPESHUB = "x.pipeshub"
# A claim whose pin this worker cannot read is refused: it never runs on what the documents say now instead.
RESOLVED_CONFIG_INVALID = "resolved_config_invalid"
# Tool grants of this source are the web search tools (``tool_environment.WEB_SOURCE``).
WEB_TOOL_SOURCE = "web"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AgentRef(_Frozen):
    agent_key: str
    definition_version: int
    mode: str


class ModelChoice(_Frozen):
    provider: str
    model: str
    model_key: str | None = None
    model_name: str | None = None
    reasoning_effort: str | None = None


class SourceLimit(_Frozen):
    """A saved agent's source limited to some of its record groups and records, as admission pinned it."""

    record_groups: tuple[str, ...] = ()
    records: tuple[str, ...] = ()


class KnowledgeScope(_Frozen):
    """The pinned knowledge: whole apps and collections, the selection the request named below app level (the ids
    as asked, resolved again every attempt), and a saved agent's per-source limits (SYNC4, #4255)."""

    apps: tuple[str, ...] = ()
    kb: tuple[str, ...] = ()
    strict_scope: bool = False
    record_groups: tuple[str, ...] = ()
    records: tuple[str, ...] = ()
    records_exact: tuple[str, ...] = ()
    source_limits: dict[str, SourceLimit] = {}

    @property
    def empty(self) -> bool:
        return not self.apps and not self.kb and not self.has_selection

    @property
    def has_selection(self) -> bool:
        return bool(self.record_groups or self.records or self.records_exact)


class ChatGrant(_Frozen):
    """The built-in assistant's pinned grant (``x.pipeshub.chat``): its mode,
    whether it searches knowledge, and whether it has the web search tools."""

    mode: str
    internal_search: bool
    web_search: bool

    def policy(self) -> ChatModePolicy:
        """The legacy ``ChatModePolicy`` of the mode, narrowed to this grant."""
        from app.agents.chat_modes.policy import resolve_chat_mode_policy

        legacy = resolve_chat_mode_policy(self.mode)
        return dataclasses.replace(
            legacy,
            has_knowledge=legacy.has_knowledge and self.internal_search,
            include_web_search=legacy.include_web_search and self.web_search,
            prefetch_retrieval=legacy.prefetch_retrieval and self.internal_search,
        )


class SubAgentRef(_Frozen):
    """What a child run of deep mode runs as (``x.pipeshub.subAgent``, K-L)."""

    role: str
    tool_names: tuple[str, ...]
    domain_agent: str | None = None


class AudienceRef(_Frozen):
    """Whose turns a run in a shared session sees (``x.pipeshub.audience``, SS2, P2.5a)."""

    session_role: str
    owner_ref: str
    own_runs: tuple[str, ...] = ()
    visible_runs: tuple[str, ...] = ()

    def history(self, principal_ref: str) -> HistoryAudience:
        return HistoryAudience(
            principal=principal_ref,
            owner=self.owner_ref,
            own_runs=frozenset(self.own_runs),
            visible_runs=frozenset(self.visible_runs),
        )


class MemoryPinRef(_Frozen):
    """Governed memory the run's admission decided (``x.pipeshub.memory``, P7.3)."""

    recalled: tuple[str, ...] = ()
    write_scopes: tuple[str, ...] = ()


class Attachment(_Frozen):
    record_id: str
    record_name: str | None = None
    mime_type: str | None = None
    extension: str | None = None
    virtual_record_id: str | None = None
    source: str | None = None

    @classmethod
    def of(cls, item: Mapping[str, object]) -> Attachment | None:
        """An admitted attachment's wire shape (the run's input, a steering message's); ``None`` without a record."""
        if not item.get("recordId"):
            return None
        return cls(
            record_id=str(item["recordId"]),
            record_name=_text(item.get("recordName")),
            mime_type=_text(item.get("mimeType")),
            extension=_text(item.get("extension")),
            virtual_record_id=_text(item.get("virtualRecordId")),
            source=_text(item.get("source")),
        )


def _pin(raw: dict[str, JsonValue] | None) -> ResolvedRunConfig | None:
    if raw is None:
        return None
    try:
        pin = validate_contract(ResolvedRunConfig, raw)
    except ValidationError:
        raise RunRefused(RESOLVED_CONFIG_INVALID, "the run's pinned configuration is not one this worker reads") from None
    if pin_digest(raw) != raw.get("digest"):
        raise RunRefused(RESOLVED_CONFIG_INVALID, "the run's pinned configuration does not match its digest")
    return pin


class RunContext(_Frozen):
    run_id: str
    org_id: str
    session_id: str
    actor_id: str
    principal_ref: str
    spec_digest: str
    via: str
    agent: AgentRef | None
    model: ModelChoice
    # The admitted ``chatMode`` (the model's config reads it); the loop runs
    # ``loop_mode``.
    chat_mode: str
    tool_ids: tuple[str, ...]
    mcp_instances: tuple[str, ...]
    # D-LAZY-2: the servers granted whole (``tools: at_first_use``): the tools their first listing in the run offers.
    mcp_at_first_use: tuple[str, ...] = ()
    # D-LAZY-5 (R31HOT2): the first-use pins a child's parent carried, per instance: each tool by the server's own name
    # at the digest the parent's listing served. Such a server is the run's at that pin from the start.
    mcp_carried_pins: dict[str, dict[str, str]] = {}
    # The version-2 pins a claim verifies before it runs (CPS PN-5/PN-6).
    descriptor_pins: tuple[DescriptorPin, ...] = ()
    skills: tuple[str, ...]
    # Each pinned skill's bundle digest (``x.pipeshub.skills[].bundleDigest``): the content a run may load (SKL-G6).
    skill_digests: dict[str, str] = {}
    sandbox_profile: str
    knowledge: KnowledgeScope
    capabilities: dict[str, JsonValue]
    query: str
    record_ids: tuple[str, ...] = ()
    attachments: tuple[Attachment, ...] = ()
    timezone: str | None = None
    current_time: str | None = None
    # G-52: the person's locale (``input.locale``), which sets the answer's language; ``None``: not sent.
    locale: str | None = None
    # When Node admitted the run (epoch ms): fixed for the run and every attempt of it.
    admitted_at: int | None = None
    previous_conversations: tuple[dict[str, JsonValue], ...] = ()
    chat: ChatGrant | None = None
    caller_email: str | None = None
    caller_display_name: str | None = None
    sub_agent: SubAgentRef | None = None
    max_children: int = 0
    audience: AudienceRef | None = None
    memory: MemoryPinRef | None = None
    # The claim's attempt (sandboxes are labelled with it so the sweeper reaps what a dead attempt left).
    attempt_id: str | None = None
    # ``grants.egress``: ``none`` keeps code the run executes off the network.
    egress: str = "none"
    # The run's pin (``x.pipeshub.resolved``): what its policy is read from; ``None``: admitted without one.
    resolved: ResolvedRunConfig | None = None
    # CTL-6: the run pins an engine authority (``x.pipeshub.codeTask``): its loop may hand off through code_task.
    code_task: bool = False
    # DEEPROUTE: the org lets a turn that may hand off do so on its own (``x.pipeshub.codeTask.autoRoute``).
    code_task_auto_route: bool = True
    # The chat's repository's GitHub toolset instance (``x.pipeshub.repository.toolsetInstanceId``), checked by Node
    # as one the person may use; ``None``: no repository, or a public one.
    repository_toolset: str | None = None
    # The turn asked for today's loop's measurement frames (``includeRetrievalContext``, SYNC2-F1).
    include_retrieval_context: bool = False
    # The turn's ``keywordSearchBudget`` (``short`` caps each search's wait for its keyword matches); ``None``: full.
    keyword_search_budget: str | None = None
    # The turn asked for deep research (``agentCapabilities.deepSearch``); applies only with web search on.
    deep_research: bool = False
    # Where the run was started (``x.pipeshub.surface``); ``None``: not pinned (an older Node, or a child run).
    surface: str | None = None
    # A schedule's trigger started the run (TR-03, ``claim.trigger_context``): nobody waits in a chat to continue it.
    unattended: bool = False
    # A person's ``/run`` (SLASH3, ``input.sandboxCommand``): its timeout; ``None``: a turn for the model.
    sandbox_command_timeout_s: int | None = None

    @property
    def web_app(self) -> bool:
        """Started in the web app, the only client that draws main's MCP sign-in card: an API token's run is on
        ``web`` too, but comes ``via`` a token, as main's auth rewrites such a caller's client name to ``api``."""
        return self.surface == "web" and self.via == "session"

    @property
    def prompt_time(self) -> str | None:
        """The current time the prompt shows: the client's, else, when the client gave a time
        zone, the run's admission time. Never the wall clock: a time read per turn makes
        every system prompt of the run different and the provider's prompt cache miss (EV4)."""
        if self.current_time or not self.timezone or self.admitted_at is None:
            return self.current_time
        admitted = datetime.fromtimestamp(self.admitted_at / 1000, tz=UTC)
        return admitted.isoformat(timespec="seconds").replace("+00:00", "Z")

    @property
    def history_audience(self) -> HistoryAudience | None:
        """Set in a shared session: other participants' turns reach the model as a stub."""
        return self.audience.history(self.principal_ref) if self.audience is not None else None

    @property
    def workspace_id(self) -> str:
        """The workspace this run's principal owns in its session (OWD-16): a collaborator's run never the owner's."""
        collaborator = self.audience is not None and self.audience.session_role == "collaborator"
        return principal_workspace_id(self.session_id, self.actor_id, collaborator=collaborator)

    @property
    def deep(self) -> bool:
        """A root run of deep mode (its sub-agents are child runs, K-L). Deep mode is planning work and handing it to
        sub-agents, so a deep-mode turn of an agent pinned no children (its definition's ``maxChildren: 0``) is not
        one: it runs as the agent's own loop, which downgrades the orchestrator to react, rather than plan and see
        every spawn refused (QAFINAL-FU-PW113-2, ADR-RUN-04 D4)."""
        return (
            self.sub_agent is None
            and self.agent is not None
            and self.chat_mode.strip().lower() == "deep"
            and self.max_children > 0
        )

    @property
    def coordinates(self) -> bool:
        """Plans and hands work to sub-agents: the one run offered deep mode's coordination tools (``create_plan``,
        ``critique_plan``, ``verify_result``, ``spawn_agent``). A sub-agent is the tree's bound: its pin grants it no
        children (``child_budget_share``), so it is offered none of them (QAFINAL-FU-PW113, ADR-RUN-04), and neither
        is a root that may start none (``deep``)."""
        return self.deep

    @property
    def web_scope(self) -> Literal["answer", "run"]:
        """Whose web allowance this loop run's web tools draw on: a deep root run and its children do the long,
        many-source work an engine run does, so they draw on the run's (its USD budget limits them, WP-62); so does
        a scheduled or triggered run, whatever its mode, which has nobody to say "continue" when an answer's count
        runs out (WP56-WEB-4); any other loop run is one answer and keeps an answer's
        count (WP-56)."""
        return "run" if self.deep or self.sub_agent is not None or self.unattended else "answer"

    @property
    def loop_mode(self) -> str:
        """The agent loop mode: the built-in assistant always runs ``quick``."""
        if self.chat is not None:
            return self.chat.policy().loop_chat_mode
        return self.chat_mode

    @classmethod
    def from_claim(cls, claim: NodeClaim) -> RunContext:
        spec = claim.spec
        raw = (spec.extensions or {}).get(PIPESHUB)
        if raw is None:
            raise ValueError("the pinned spec has no x.pipeshub extension")
        ext = validate_contract(PipeshubRunSpecExtension, raw)
        run = claim.run
        snapshot = run.input if run is not None else None
        route = spec.model.policy[0]
        chosen = ext.model
        return cls(
            run_id=claim.run_id,
            org_id=claim.org_id,
            session_id=claim.session_id,
            actor_id=run.actor_id if run is not None else spec.principal_ref.removeprefix("p_"),
            principal_ref=spec.principal_ref,
            spec_digest=canonical_digest(dump_contract(spec)),
            via=ext.via,
            surface=ext.surface,
            unattended=claim.trigger_context is not None,
            agent=(
                AgentRef(
                    agent_key=ext.agent.agent_key,
                    definition_version=ext.agent.definition_version,
                    mode=ext.agent.mode,
                )
                if ext.agent is not None
                else None
            ),
            model=ModelChoice(
                provider=route.provider,
                model=route.model,
                model_key=chosen.model_key if chosen is not None else None,
                model_name=chosen.model_name if chosen is not None else None,
                reasoning_effort=spec.model.reasoning_effort,
            ),
            chat_mode=_chat_mode(ext, snapshot),
            tool_ids=tuple(grant.tool_id for grant in spec.grants.tools),
            mcp_instances=tuple(server.instance_id for server in ext.mcp),
            mcp_at_first_use=tuple(server.instance_id for server in ext.mcp if server.tools == "at_first_use"),
            mcp_carried_pins={
                server.instance_id: {tool: digest.root for tool, digest in server.pins.items()}
                for server in ext.mcp
                if server.pins
            },
            descriptor_pins=pinned_descriptors(spec.grants.tools, ext.mcp),
            skills=tuple(skill.name for skill in ext.skills),
            skill_digests={skill.name: skill.bundle_digest for skill in ext.skills},
            sandbox_profile=spec.grants.sandbox_profile,
            egress=spec.grants.egress,
            attempt_id=claim.attempt_id,
            resolved=_pin(ext.resolved),
            code_task=ext.code_task is not None,
            code_task_auto_route=ext.code_task is None or ext.code_task.auto_route is not False,
            repository_toolset=ext.repository.toolset_instance_id if ext.repository is not None else None,
            knowledge=KnowledgeScope(
                apps=tuple(app.root for app in ext.knowledge.apps),
                kb=tuple(kb.root for kb in ext.knowledge.kb),
                strict_scope=ext.knowledge.strict_scope,
                record_groups=_ids(ext.knowledge.record_groups),
                records=_ids(ext.knowledge.records),
                records_exact=_ids(ext.knowledge.records_exact),
                source_limits={
                    limit.connector_id: SourceLimit(
                        record_groups=_ids(limit.record_groups), records=_ids(limit.records)
                    )
                    for limit in ext.knowledge.source_limits or ()
                },
            ),
            capabilities=dict(claim.capabilities),
            query=claim.input_text,
            record_ids=tuple(snapshot.record_ids) if snapshot is not None else (),
            attachments=tuple(
                attachment
                for item in (snapshot.attachments if snapshot is not None else ())
                if (attachment := Attachment.of(item)) is not None
            ),
            timezone=snapshot.timezone if snapshot is not None else None,
            current_time=snapshot.current_time if snapshot is not None else None,
            locale=snapshot.locale if snapshot is not None else None,
            include_retrieval_context=snapshot is not None and snapshot.include_retrieval_context,
            keyword_search_budget=snapshot.keyword_search_budget if snapshot is not None else None,
            sandbox_command_timeout_s=(
                snapshot.sandbox_command.timeout_s
                if snapshot is not None and snapshot.sandbox_command is not None
                else None
            ),
            deep_research=bool(snapshot is not None and (snapshot.agent_capabilities or {}).get("deepSearch")),
            admitted_at=run.created_at if run is not None else None,
            previous_conversations=_visible_history(
                snapshot.previous_conversations if snapshot is not None else (),
                ext.audience,
                spec.principal_ref,
            ),
            caller_email=snapshot.caller.email if snapshot is not None and snapshot.caller else None,
            caller_display_name=(
                snapshot.caller.display_name if snapshot is not None and snapshot.caller else None
            ),
            chat=(
                ChatGrant(
                    mode=ext.chat.mode,
                    internal_search=ext.chat.internal_search,
                    web_search=any(
                        grant.tool_id.startswith(f"{WEB_TOOL_SOURCE}@") for grant in spec.grants.tools
                    ),
                )
                if ext.agent is None and ext.chat is not None
                else None
            ),
            sub_agent=(
                SubAgentRef(
                    role=ext.sub_agent.role,
                    tool_names=tuple(name.root for name in ext.sub_agent.tool_names),
                    domain_agent=ext.sub_agent.domain_agent,
                )
                if ext.sub_agent is not None
                else None
            ),
            max_children=spec.budgets.max_children,
            audience=(
                AudienceRef(
                    session_role=ext.audience.session_role,
                    owner_ref=ext.audience.owner_ref,
                    own_runs=tuple(run.root for run in ext.audience.own_runs),
                    visible_runs=tuple(run.root for run in ext.audience.visible_runs or ()),
                )
                if ext.audience is not None
                else None
            ),
            memory=(
                MemoryPinRef(
                    recalled=tuple(memory_id.root for memory_id in ext.memory.recalled),
                    write_scopes=tuple(ext.memory.write_scopes),
                )
                if ext.memory is not None
                else None
            ),
        )


def _visible_history(
    turns: Iterable[dict[str, JsonValue]],
    audience: Audience | None,
    principal_ref: str,
) -> tuple[dict[str, JsonValue], ...]:
    """The conversation history the run's own actor may give the model (SS2); Node
    projected it already, and projecting again changes nothing but drops ``sender``."""
    if audience is None:
        return tuple(turns)
    projected = project_turns(
        turns,
        HistoryAudience(principal=principal_ref, owner=audience.owner_ref, own_runs=frozenset()),
    )
    return tuple(cast("dict[str, JsonValue]", turn) for turn in projected)


def _chat_mode(ext: PipeshubRunSpecExtension, snapshot: RunInputSnapshot | None) -> str:
    """The mode the run's loop runs in. The system assistant definition's input keeps the client's ``agent``;
    Node pinned the loop mode legacy runs it in (ASSIST1)."""
    if ext.agent is not None and (snapshot is None or ext.agent.agent_key == ASSISTANT_AGENT_KEY):
        return ext.agent.mode
    return snapshot.model.chat_mode if snapshot is not None else "quick"


def pinned_parent_asks(spec: RunSpec) -> bool:
    """Whether a child's parent can put a question to a person, as Node pinned it at the child's admission
    (``x.pipeshub.parentAsks``, ANSWER-CONTRACT-FU-3). A child pinned without it is not offered ``ask_user``: its
    parent may be a schedule's run, which nobody watches."""
    extensions: dict[str, dict[str, object]] = spec.extensions or {}
    return extensions.get(PIPESHUB, {}).get("parentAsks") is True


def _ids(ids: Iterable[RootModel[str]] | None) -> tuple[str, ...]:
    return tuple(i.root for i in ids or ())


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


__all__ = [
    "AgentRef",
    "Attachment",
    "AudienceRef",
    "ChatGrant",
    "KnowledgeScope",
    "MemoryPinRef",
    "ModelChoice",
    "RunContext",
    "SourceLimit",
    "SubAgentRef",
    "pinned_parent_asks",
]
