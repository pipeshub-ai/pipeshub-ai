from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class BudgetConfig(BaseModel):
    max_tokens: int = 100_000
    max_tool_calls: int | None = None


class RetryHookConfig(BaseModel):
    """Control-plane-level knobs for the builtin RetryHook (see hooks/middleware/builtin/retry.py)."""

    max_retries: int = 3
    initial_delay: float = 1.0
    backoff_factor: float = 2.0
    max_delay: float = 60.0


class ContextEngineConfig(BaseModel):
    """Control-plane-level knobs for the context-shaper pipeline.

    PRE_MODEL shapers run cheapest-first:
        L1  budget_reduction
        L2  artifact_compaction  (turn-aware, replaces offload)
        L3  tool_result_clearing
        L4  loop_compaction      (turn-boundary batch compaction)
        L5  sliding_window
        L6  deterministic_compact (extractive, no LLM call)
        L7  auto_compact         (LLM summariser, last resort)
        L8  synthesis_guard      (hard budget enforcement)

    POST_TOOL_USE:
        artifact_registration   (large results → artifacts)
    """

    enable_budget_reduction: bool = True
    max_result_chars: int = 64_000

    enable_artifact_registration: bool = True
    artifact_threshold_tokens: int = 2_000
    artifact_preview_chars: int = 200

    enable_artifact_compaction: bool = True
    artifact_compaction_trigger_ratio: float = 0.5

    enable_tool_result_clearing: bool = True
    clearing_keep_last_n_turns: int = 3
    clearing_trigger_ratio: float = 0.5

    enable_loop_compaction: bool = True
    loop_compact_every_n_turns: int = 5
    loop_compact_keep_recent: int = 6
    loop_compact_trigger_ratio: float = 0.6

    enable_offload: bool = False
    offload_threshold_tokens: int = 2_000
    offload_preview_lines: int = 10

    enable_sliding_window: bool = True
    sliding_window_pin_first_n: int = 1

    enable_deterministic_compact: bool = True
    deterministic_compact_trigger_ratio: float = 0.85
    deterministic_compact_keep_last_n: int = 6
    deterministic_compact_preview_chars: int = 100

    enable_auto_compact: bool = True
    auto_compact_trigger_ratio: float = 0.85
    auto_compact_max_tail_ratio: float = 0.6

    enable_synthesis_guard: bool = True
    synthesis_guard_keep_last_n: int = 2


class OSSandboxConfig(BaseModel):
    """OS sandbox (Phase 3 taxonomy): the `run_shell` tool. `confine=True`
    applies kernel-level confinement of the whole spawned process tree when
    the OS supports it (Seatbelt on macOS, bubblewrap on Linux — see
    sandbox/confinement.py); it's plain subprocess isolation otherwise."""

    enabled: bool = False
    working_dir: str | None = None
    confine: bool = True
    allow_network: bool = False
    timeout: float = 30.0


class DBSandboxConfig(BaseModel):
    """DB sandbox (Phase 3 taxonomy): the `db_query` tool over
    sandbox/db_sandbox.py's SqliteDBSandbox. "readonly" rejects any
    non-SELECT statement; `table_allowlist`, when set, additionally rejects
    statements referencing tables outside it."""

    enabled: bool = False
    db_path: str = ":memory:"
    mode: str = "readonly"  # "readonly" | "readwrite"
    table_allowlist: list[str] | None = None


class BrowserSandboxConfig(BaseModel):
    """Browser sandbox (Phase 3 taxonomy): navigate/get_text/click/fill/
    screenshot tools over a shared playwright-backed page (see
    sandbox/browser_sandbox.py). Requires the optional `playwright`
    dependency (`pip install agent-loop[browser]`) at first use, not at
    ControlPlane.start() time."""

    enabled: bool = False
    headless: bool = True


class CodingSandboxRlimitsConfig(BaseModel):
    """`setrlimit` values applied to every executed process — see
    `sandbox/coding/executor.py::ExecutionLimits` for the `max_processes`
    (RLIMIT_NPROC is per-UID system-wide on macOS/BSD, not per-sandbox)
    caveat that motivates its generous default."""

    max_memory_bytes: int = 1536 * 1024 * 1024
    max_cpu_seconds: int = 30
    max_file_size_bytes: int = 50 * 1024 * 1024
    max_processes: int = 2048


class LocalBackendConfig(BaseModel):
    """`backend="local"`-only knobs — subprocess + npm/venv execution on
    the host machine (see `sandbox/coding/local.py::LocalCodingSandbox`).
    Split out from `CodingSandboxConfig` (Interface Segregation) since none
    of these apply to a remote backend like E2B."""

    working_dir_root: str | None = None  # None = system temp dir
    typecheck_typescript: bool = True
    rlimits: CodingSandboxRlimitsConfig = Field(default_factory=CodingSandboxRlimitsConfig)


class E2BBackendConfig(BaseModel):
    """`backend="e2b"`-only knobs — cloud micro-VM execution via
    https://e2b.dev (see `sandbox/coding/e2b.py::E2BCodingSandbox`, optional
    dependency `pip install agent-loop[e2b]`). Split out from
    `CodingSandboxConfig` (Interface Segregation) since none of these apply
    to the local backend."""

    api_key: str | None = None  # falls back to the E2B_API_KEY env var
    template: str = "base"
    e2b_timeout: int = 300  # E2B sandbox lifetime in seconds, billed per-second
    allow_internet_access: bool = True


class DockerBackendConfig(BaseModel):
    """`backend="docker"`-only knobs — two-phase container execution (see
    `sandbox/coding/docker.py::DockerCodingSandbox`): an offline run
    container (`network_mode="none"`) plus a separate install container on
    `egress_network` for pip/npm. Split out from `CodingSandboxConfig`
    (Interface Segregation), same as `LocalBackendConfig`/`E2BBackendConfig`.
    Defaults here are deliberately neutral (no `pipeshub`-specific image or
    network names) — callers that need those pass them in explicitly."""

    image: str = "agent-loop-sandbox:latest"
    memory_limit_mb: int = 512
    cpu_limit: float = 0.5
    egress_network: str = "sandbox_egress"
    network_disabled: bool = True
    pip_index_url: str = "https://pypi.org/simple"
    npm_registry: str = "https://registry.npmjs.org"
    working_dir_root: str | None = None  # None = system temp dir


class CodingSandboxConfig(BaseModel):
    """Coding sandbox (Phase 3 taxonomy, `SandboxType.CODING`): the
    `run_code`/`install_packages`/`read_sandbox_file` tools over a
    `CodingSandboxBackend`, provisioned through the generic
    `sandbox/manager.py::SandboxManager`. `backend` is a config switch, not
    a code change (Open/Closed) — "local", "e2b", and "docker" are
    implemented; "daytona"/"aio" are documented future work (see
    `sandbox/coding/base.py::CodingSandboxBackend`). Backend-specific knobs
    live on `local`/`e2b`/`docker` (Interface Segregation) — fields below
    are shared across every backend."""

    enabled: bool = False
    backend: str = "local"  # "local" | "e2b" | "docker" | future: "daytona" | "aio"

    local: LocalBackendConfig = Field(default_factory=LocalBackendConfig)
    e2b: E2BBackendConfig = Field(default_factory=E2BBackendConfig)
    docker: DockerBackendConfig = Field(default_factory=DockerBackendConfig)

    # Environment/execution
    default_timeout: float = 30.0
    allow_network_on_install: bool = True
    package_allowlist: list[str] | None = None
    package_denylist: list[str] = Field(default_factory=list)

    # Manager-enforced lifecycle limits (see sandbox/manager.py::SandboxLimits)
    max_concurrent: int | None = 5
    max_lifetime_s: float | None = 1800.0  # 30 minutes

    # Artifact persistence — when set, files created by `run_code` are
    # automatically downloaded from the sandbox and saved to this directory
    # on the host (organised as `<artifact_output_dir>/<sandbox_id>/...`).
    # None (default) = artifacts stay inside the ephemeral sandbox only.
    artifact_output_dir: str | None = None

    # coding_sandbox_safety PRE_TOOL_USE middleware (defense-in-depth only)
    max_code_size: int = 50_000
    blocked_patterns: list[str] = Field(default_factory=list)
    allow_url_packages: bool = False


class OpikConfig(BaseModel):
    """Control-plane-level knobs for Opik LLM-call tracing (see
    `transport/opik_tracing.py`). `enabled=True` (the default) only takes
    effect when Opik credentials are actually present in the environment
    (`OPIK_API_KEY` for Opik Cloud, or `OPIK_URL_OVERRIDE` for a
    self-hosted instance — see `is_opik_configured()`); with neither set,
    `ControlPlane.start()` silently skips wrapping transports, same
    "configure via env, no-op otherwise" convention the legacy
    `OpikTracer` singletons used. Set `enabled=False` to force tracing off
    even when credentials are present.
    """

    enabled: bool = True
    project_name: str | None = None


class LazyToolsetsConfig(BaseModel):
    """Control-plane-level knobs for Phase 1 progressive tool disclosure
    (see tools/registry.py's Toolset grouping + tools/builtin/lazy_toolsets.py).

    When enabled, builtin tools other than the always-essential ones
    (spawn_agent, clarify, task_complete, and the two meta-tools themselves)
    are grouped into toolsets and their schemas are hidden from the model
    until it calls `fetch_tools(toolset)` — cutting upfront schema tokens
    on roles that only end up using a fraction of their available tools.
    """

    enabled: bool = True
    pinned_toolsets: list[str] = Field(default_factory=list)


class SkillManagerConfig(BaseModel):
    """Control-plane-level knobs for the Skills Manager (see
    modules/providers/skills/manager.py::SkillManagerConfig, which this
    mirrors field-for-field — kept separate so ControlPlaneConfig has no
    import-time dependency on the skills package). `skills_dir` is the one
    writable root (owns `_meta/`, where the index and pending learning-loop
    candidates live); `extra_skills_dirs` are read-only (npm-installed skill
    packs, shared team directories, ...) and shadowed by `skills_dir` on a
    name collision.

    `skills_dir=None` (the default) means the skills feature is entirely
    disabled — `ControlPlane.start()` skips wiring a `SkillManager` (and
    therefore never touches the filesystem) unless a path is explicitly
    configured, mirroring the old empty-`skills_dirs`-list default.

    The learning loop (extraction -> evaluation -> governance) only runs
    when `learning_enabled` AND "skill_learning" is listed in
    `ControlPlaneConfig.hooks` — these knobs are otherwise inert.
    """

    skills_dir: str | None = None
    extra_skills_dirs: list[str] = Field(default_factory=list)

    auto_approve: bool = False       # auto-approve learning-loop candidates (skips human review)
    write_approval: bool = False     # require governance approval for every write, not just learning-loop ones
    learning_enabled: bool = True
    max_candidates: int = 50
    catalog_render_limit: int = 40   # above this many skills, the prompt shows categories + a skill_search hint instead of the full list

    # Skill promotion gate (grading): when True, a candidate must earn a
    # passing RubricGrader score (see eval/rubric.py) before it's accepted
    # at all — checked inside SkillEvaluator.evaluate_candidate.
    require_passing_grade: bool = False
    pass_threshold: float = 0.7


class ControlPlaneConfig(BaseModel):
    # Transport — one of "anthropic" | "openai" | "ollama". `base_url`
    # applies to openai (any OpenAI-compatible endpoint: vLLM, LiteLLM
    # proxy, Azure's OpenAI-compat surface) and ollama (defaults to
    # OllamaTransport.DEFAULT_BASE_URL when left None).
    transport: str = "anthropic"
    api_key: str | None = None
    model: str = "claude-sonnet-4-6"
    # Keep in sync with AnthropicTransport.DEFAULT_MAX_TOKENS — 8k proved
    # too small for report-synthesis outputs and caused silent truncation
    # of task_complete arguments.
    max_tokens: int = 20_000
    base_url: str | None = None

    # Prompting layer default (see AgentSpec.mode / hooks/middleware/builtin/mode.py).
    # Fallback for every AgentSpec make_spec() produces (a role's own `mode`
    # or an explicit make_spec() override both win — see
    # ControlPlane.make_spec()) AND, when not "act", wires the enforcing
    # ModeHook at start() time. Overriding mode per make_spec() call changes
    # the prompt section but NOT this hook's enforcement boundary, since the
    # hook is built once at start() — for per-agent mode enforcement,
    # construct a separate ControlPlane (or add your own ModeHook instance)
    # per mode.
    mode: str = "act"

    # Memory — "in_memory" (dev/test, substring search, non-persistent) or
    # "sqlite" (Phase 5: durable, real FTS5 full-text ranking; see
    # modules/providers/memory/sqlite.py). `memory_path` is only used by "sqlite".
    memory: str = "in_memory"
    memory_path: str = ":memory:"

    # Knowledge
    knowledge: str = "in_memory"

    # Workspace (Phase 3): backs the filesystem tools (ls/read_file/write_file/
    # edit_file/glob/grep — see tools/builtin/filesystem/filesystem.py). "in_memory" is a
    # per-run virtual filesystem; "local" persists to `workspace_root` on disk.
    workspace: str = "in_memory"          # "in_memory" | "local"
    workspace_root: str = "./workspace"   # only used when workspace="local"

    # Skills: agentskills.io / anthropic/skills-format SKILL.md library, with
    # full lifecycle management (search, categories, usage tracking, a
    # self-learning loop) — see modules/providers/skills/manager.py. A
    # name+description overview is always rendered into the system prompt
    # (or a category-tree summary above `catalog_render_limit`); the read
    # tools (`skills_list`/`load_skill`/`load_skill_resource`/`skill_search`)
    # are auto-registered whenever this is set (no need to list them in
    # `tools` below).
    skill_manager: SkillManagerConfig = Field(default_factory=SkillManagerConfig)

    # Commands (Phase 3): directories to scan for user-defined markdown
    # slash-commands (see commands/loader.py) — client-side prompt templates
    # expanded by the CLI's `/name args` dispatch (cli.py), not agent-facing.
    commands_dirs: list[str] = Field(default_factory=list)

    # execute_code (Phase 3): programmatic tool calling via the RPC bridge
    # (see sandbox/rpc.py, tools/builtin/filesystem/execute_code.py). Opt-in — not in
    # the default `tools` list below — since it's arbitrary code execution
    # (RiskLevel.HIGH) with no OS-level sandboxing yet (LocalSandbox-grade
    # subprocess isolation only; see sandbox/local.py's caveat).
    execute_code_timeout: float = 30.0
    execute_code_max_tool_calls: int = 50

    # Sandbox taxonomy (Phase 3): one SandboxProvider interface (sandbox/base.py),
    # four typed sandboxes each shipping its own toolset. Code sandbox = the
    # execute_code RPC bridge above; these three are the rest, each opt-in.
    os_sandbox: OSSandboxConfig = Field(default_factory=OSSandboxConfig)
    db_sandbox: DBSandboxConfig = Field(default_factory=DBSandboxConfig)
    browser_sandbox: BrowserSandboxConfig = Field(default_factory=BrowserSandboxConfig)
    coding_sandbox: CodingSandboxConfig = Field(default_factory=CodingSandboxConfig)

    # Tools to register (builtin names or "all")
    tools: list[str] = Field(default_factory=lambda: [
        "spawn_agent", "memory_read", "memory_write", "memory_search", "memory_consolidate",
        "knowledge_query", "clarify", "task_complete", "write_todos", "replan", "handoff",
        "parse_intent",
    ])

    # Hooks to enable (builtin names). "retry" wires RetryHook with
    # retry_config (or its defaults) as a wrap_model_call middleware.
    # "context_engine" wires the five Phase 1 pre_model shapers.
    # "supervisor_confidence_gate"/"stall_detection" are deliberately NOT
    # in the default list — both change model-visible behavior (blocking a
    # tool result; injecting warning/directive messages), so they're opt-in
    # here for every agent this ControlPlane builds, same as "approval"/
    # "permission"/"tool_safety" below. Prefer `AgentSpec.middleware`
    # instead (see `hooks/middleware/builtin/turn_guards.py`) when only
    # SOME roles on this ControlPlane should get them.
    hooks: list[str] = Field(default_factory=lambda: ["logging", "retry", "context_engine"])
    retry_config: RetryHookConfig = Field(default_factory=RetryHookConfig)
    context_engine: ContextEngineConfig = Field(default_factory=ContextEngineConfig)
    lazy_toolsets: LazyToolsetsConfig = Field(default_factory=LazyToolsetsConfig)

    # Opik LLM-call tracing (see transport/opik_tracing.py) — applies to
    # every transport this ControlPlane registers, not gated by `hooks`
    # since it wraps the transport layer itself rather than the hook kernel.
    opik: OpikConfig = Field(default_factory=OpikConfig)

    # Budget
    budget: BudgetConfig | None = None

    # Permissions
    allowlist: list[str] | None = None
    denylist: list[str] = Field(default_factory=list)

    # Optional stores
    enable_state_tracking: bool = False
    enable_timeline: bool = False
    enable_session: bool = False

    metadata: dict[str, Any] = Field(default_factory=dict)
