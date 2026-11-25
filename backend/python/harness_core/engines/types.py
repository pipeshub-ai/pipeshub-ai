"""Engine value types (ARCHITECTURE §4.1). Engine-neutral; adapters translate them."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Literal, cast, get_args

from harness_core.access.types import AccountKind
from harness_core.contracts.generated.enums import ProfileEffort
from harness_core.contracts.generated.policy_data import RELEASE_DEFAULTS
from harness_core.engines.errors import ProfileWidening
from harness_core.interactions.hold import APPROVAL_HOLD_S
from harness_core.sandbox import RuntimeMode
from harness_core.workspace.types import INPUTS_DIR as WORKSPACE_INPUTS_DIR

if TYPE_CHECKING:
    from harness_core.access.types import AccessRouteRef, ModelApi
    from harness_core.artifacts.types import ArtifactVersionRef
    from harness_core.effects.types import ApprovalDecision, EffectKind
    from harness_core.workspace.types import ObjectRef

NativeEngineKind = Literal["opencode", "codex", "jcode", "claude_code"]
ENGINE_KINDS: frozenset[str] = frozenset(get_args(NativeEngineKind))
# A long-tail coding agent driven over the Agent Client Protocol (ACP) is a declarative profile, not code (ACP-driver):
# its kind is ``acp-<agent>``, so each profile is its own engine in the registry, the picker and the journal.
ACP_KIND_PREFIX = "acp-"
_ACP_KIND = re.compile(r"^acp-[a-z][a-z0-9_-]{0,31}$")
# A native kind, or an ACP profile's ``acp-<agent>`` (``is_engine_kind``).
EngineKind = str


def is_acp_kind(kind: str) -> bool:
    return _ACP_KIND.match(kind) is not None


def is_engine_kind(kind: str) -> bool:
    return kind in ENGINE_KINDS or is_acp_kind(kind)

ReasoningEffort = ProfileEffort

JsonValue = None | bool | int | float | str | Sequence["JsonValue"] | Mapping[str, "JsonValue"]

# Approvals are held at most ``APPROVAL_HOLD_S`` before the call is deferred (K-H). Engines
# must wait longer than that for a tool call, or a held approval looks like a failure.
MIN_TOOL_TIMEOUT_S = 150

# A read-only folder in every engine sandbox, root's down to ``/`` and writable by nobody inside, which the adapter
# seeds from outside: configuration an engine obeys above anything a repository in the workspace can set (an agent
# such as Gemini CLI reads its system settings only from such a folder, WP-59).
SEALED_DIR = "/pxh-sealed"

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
# Network beyond the two gateways; release data allows none, so nothing can widen it yet.
EGRESS_NONE = "none"
EFFECT_ORDER: tuple[EffectKind, ...] = ("read", "workspace", "write", "execute")
EFFECT_KINDS: frozenset[EffectKind] = frozenset(EFFECT_ORDER)
# The release profile (policy/release.json): the defaults below that equal a release value are read from it.
_RELEASE = RELEASE_DEFAULTS.engine_profile
# The account kinds an engine run may reach models with when nothing narrows them (A1): the gateway's, and the
# person's own plan sign-in (SIGNIN-5), which only admission's ownPlan ever pins.
GATEWAY_ACCOUNT_KINDS: frozenset[AccountKind] = frozenset(AccountKind(kind.root) for kind in _RELEASE.account_kinds)
# Deployment modes (k8s, managed) run gVisor at least; microVMs isolate more.
_ISOLATION_RANK: dict[RuntimeMode, int] = {
    RuntimeMode.DOCKER_HARDENED: 0,
    RuntimeMode.GVISOR: 1,
    RuntimeMode.K8S: 1,
    RuntimeMode.MANAGED: 1,
    RuntimeMode.MICROVM: 2,
}


def isolation_rank(mode: RuntimeMode) -> int:
    return _ISOLATION_RANK[mode]


@dataclass(frozen=True, slots=True)
class EngineVersion:
    kind: EngineKind
    version: str
    binary_digest: str

    def __post_init__(self) -> None:
        if not is_engine_kind(self.kind):
            raise ValueError(f"Unknown engine kind {self.kind!r}")
        if not self.version:
            raise ValueError("Engine version is empty")
        if not _DIGEST.match(self.binary_digest):
            raise ValueError("Engine binary digest must be sha256:<64 hex>")

    @property
    def ref(self) -> str:
        """``kind@version``: how a broker and Node name the engine (H1), the digest aside."""
        return f"{self.kind}@{self.version}"


class EngineMode(StrEnum):
    PLAN = "plan"
    BUILD = "build"
    REVIEW = "review"




@dataclass(frozen=True, slots=True)
class SubAgentPolicy:
    enabled: bool = _RELEASE.sub_agents.enabled
    max_depth: int = _RELEASE.sub_agents.max_depth
    max_concurrent: int = _RELEASE.sub_agents.max_concurrent

    def __post_init__(self) -> None:
        if self.max_depth < 1 or self.max_concurrent < 1:
            raise ValueError("Sub-agent depth and concurrency must be at least 1")


@dataclass(frozen=True, slots=True)
class RunLimits:
    """Ceilings for one engine run; ``None`` is unlimited. USD only on routes the operator pays."""

    max_steps: int | None = None
    max_wall_s: int | None = None
    max_tokens: int | None = None
    max_usd: float | None = None


@dataclass(frozen=True, slots=True)
class ToolCeiling:
    """The gateway tools an engine run may be granted: how many, and of which effect classes."""

    max_tools: int | None = None
    effects: frozenset[EffectKind] = EFFECT_KINDS

    def __post_init__(self) -> None:
        if self.max_tools is not None and self.max_tools < 1:
            raise ValueError("A tool ceiling is at least 1")
        if not self.effects <= EFFECT_KINDS:
            raise ValueError("Unknown effect class in a tool ceiling")


@dataclass(frozen=True, slots=True)
class ProfileApprovals:
    """The decision per effect class before a gateway tool runs; the default asks before writes (decision 6)."""

    read: ApprovalDecision = _RELEASE.approvals.read
    workspace: ApprovalDecision = _RELEASE.approvals.workspace
    write: ApprovalDecision = _RELEASE.approvals.write
    execute: ApprovalDecision = _RELEASE.approvals.execute

    def decision(self, kind: EffectKind) -> ApprovalDecision:
        return cast("ApprovalDecision", getattr(self, kind))

    def as_mapping(self) -> dict[EffectKind, ApprovalDecision]:
        return {kind: self.decision(kind) for kind in EFFECT_ORDER}


@dataclass(frozen=True, slots=True)
class ProfileConstraint:
    """A requested narrowing. ``None`` fields leave the profile's value unchanged.

    ``engines`` admits engines by ``kind`` or ``kind@version``; ``approvals`` names
    only the effect classes it narrows.
    """

    engines: frozenset[str] | None = None
    mode: EngineMode | None = None
    access: AccessRouteRef | None = None
    reasoning_effort: ReasoningEffort | None = None
    sub_agents: SubAgentPolicy | None = None
    limits: RunLimits | None = None
    isolation_min: RuntimeMode | None = None
    egress: str | None = None
    max_tools: int | None = None
    tool_effects: frozenset[EffectKind] | None = None
    approvals: Mapping[EffectKind, ApprovalDecision] | None = None
    skills: frozenset[str] | None = None
    account_kinds: frozenset[AccountKind] | None = None
    result_schema: Mapping[str, JsonValue] | None = None


@dataclass(frozen=True, slots=True)
class EngineProfile:
    """What an engine run may do, in engine-neutral terms; adapters translate it."""

    engine: EngineVersion
    mode: EngineMode
    access: AccessRouteRef
    reasoning_effort: ReasoningEffort | None = None
    sub_agents: SubAgentPolicy = field(default_factory=SubAgentPolicy)
    limits: RunLimits = field(default_factory=RunLimits)
    isolation_min: RuntimeMode = RuntimeMode.GVISOR
    result_schema: Mapping[str, JsonValue] | None = None
    egress: str = EGRESS_NONE
    tools: ToolCeiling = field(default_factory=ToolCeiling)
    approvals: ProfileApprovals = field(default_factory=ProfileApprovals)
    # The skills the run may mount, by name; ``None``: any the agent pins.
    skills: frozenset[str] | None = None
    # Whose accounts the run may reach models with (A1); a pinned access route's kind must be one of them.
    account_kinds: frozenset[AccountKind] = GATEWAY_ACCOUNT_KINDS

    def narrow(self, c: ProfileConstraint) -> EngineProfile:
        """Apply ``c`` if every requested value is at or below this profile's; else refuse."""
        # The registry lives beside the wire codec; importing it here keeps this module its leaf.
        from harness_core.engines.profiles import narrow_profile

        return narrow_profile(self, c)


@dataclass(frozen=True, slots=True)
class EngineCapabilities:
    can_steer: bool
    native_skills: bool
    sub_agents: bool
    model_apis: frozenset[ModelApi]
    max_tools: int | None
    drive: Literal["stdio", "port"]
    # The longest gateway tool name the engine can carry to the model; ``None``: no limit.
    max_tool_name: int | None = None
    # The session compacts its own context on request (``EngineSession.compact``, SLASH2 C2).
    can_compact: bool = False
    # Executables (``/proc`` comm, at most 15 characters) whose children started during a turn are the shell tool's
    # commands (LCFIX): Codex runs each ``exec_command`` as its app-server's child. Empty: only commands a shell runs
    # are attributed to the turn.
    command_parents: frozenset[str] = frozenset()
    # Bare tool names the engine drops from every MCP server's list (Gemini CLI excludes its own ``ask_user`` in ACP
    # mode and matches an MCP tool by its unprefixed name too), so the gateway lists its own tools under other names.
    reserved_tool_names: frozenset[str] = frozenset()
    # The gateway lists the run's tools under OWD-7's spec names (``pipeshub_<toolset>_<tool>``) and still answers
    # today's names. Off for every engine until the live engine runs that pin tool names are repeated on it (WP-22).
    spec_tool_names: bool = False


@dataclass(frozen=True, slots=True)
class GatewayEndpoints:
    """The two gateways as the sandbox sees them. Secrets are placeholders the proxy swaps."""

    model_base_url: str
    model_api: ModelApi
    tools_mcp_url: str
    token_placeholder: str
    tool_timeout_s: int = MIN_TOOL_TIMEOUT_S
    # The egress proxy holds one placeholder per destination (P5.4); ``None``: the model's.
    tools_token_placeholder: str | None = None
    # The managed policy hook's program in the sandbox (G-25): read-only, seeded by the controller with the turn's
    # tool folder. An engine that runs hooks calls it before each of its own tool calls; ``None``: no hook.
    policy_hook: str | None = None

    @property
    def tools_placeholder(self) -> str:
        return self.tools_token_placeholder or self.token_placeholder

    @property
    def model_root_url(self) -> str:
        """The model gateway without its ``/v1``, for a client that appends its own API path (Gemini's
        ``/v1beta/models/...``)."""
        return self.model_base_url.rstrip("/").removesuffix("/v1")

    def __post_init__(self) -> None:
        if self.tool_timeout_s < MIN_TOOL_TIMEOUT_S:
            raise ValueError(
                f"Tool timeout {self.tool_timeout_s}s is below {MIN_TOOL_TIMEOUT_S}s; "
                "held approvals would time out in the engine"
            )
        if not self.token_placeholder:
            raise ValueError("Token placeholder is empty")


@dataclass(frozen=True, slots=True)
class EngineLaunch:
    """How to start an engine: pure output of ``EngineAdapter.render_launch``.

    ``engine`` is the pinned version the launch was rendered for; ``open`` checks
    restored state and the running binary against it.
    """

    engine: EngineVersion
    argv: tuple[str, ...]
    env: Mapping[str, str]
    files: Mapping[str, bytes] = field(default_factory=dict[str, bytes])
    workdir: str = "/workspace"
    port: int | None = None


@dataclass(frozen=True, slots=True)
class EngineTask:
    turn_id: str
    prompt: str
    instructions: str = ""


@dataclass(frozen=True, slots=True)
class FollowUp:
    turn_id: str
    text: str


# Where a task's input files are placed in its workspace: the workspace's own constant, the sync moves them in.
INPUTS_DIR = WORKSPACE_INPUTS_DIR


@dataclass(frozen=True, slots=True)
class EngineInput:
    """A file a turn starts with under ``INPUTS_DIR``: its bytes already in the session's object scope, and
    the artifact version they are."""

    name: str
    content: ObjectRef
    mime_type: str
    source: ArtifactVersionRef

    @property
    def path(self) -> str:
        return f"{INPUTS_DIR}/{self.name}"


@dataclass(frozen=True, slots=True)
class SessionHealth:
    alive: bool
    engine_version: str | None = None
    detail: str = ""


__all__ = [
    "APPROVAL_HOLD_S",
    "ACP_KIND_PREFIX",
    "ENGINE_KINDS",
    "MIN_TOOL_TIMEOUT_S",
    "SEALED_DIR",
    "INPUTS_DIR",
    "EngineCapabilities",
    "EngineInput",
    "EngineKind",
    "EngineLaunch",
    "EngineMode",
    "EngineProfile",
    "EngineTask",
    "EFFECT_KINDS",
    "EFFECT_ORDER",
    "EGRESS_NONE",
    "EngineVersion",
    "FollowUp",
    "GatewayEndpoints",
    "JsonValue",
    "ProfileApprovals",
    "ProfileConstraint",
    "ProfileWidening",
    "ReasoningEffort",
    "RunLimits",
    "SessionHealth",
    "SubAgentPolicy",
    "ToolCeiling",
    "NativeEngineKind",
    "is_acp_kind",
    "is_engine_kind",
    "isolation_rank",
]
