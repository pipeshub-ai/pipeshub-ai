from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent_loop_lib.control_plane.config import ControlPlaneConfig

if TYPE_CHECKING:
    from app.agent_loop_lib.agent.spec import AgentSpec
    from app.agent_loop_lib.runtime.runtime import AgentRuntime

# Tools the execute_code RPC bridge (sandbox/rpc.py) refuses to dispatch —
# each needs full turn-loop context (spawn_agent's async task lifecycle,
# clarify's HIL block/resume, write_todos/fetch_tools/list_toolsets' agent-
# level visibility/state mutations) that a bare ToolExecutor.execute() call
# can't provide. See ControlPlane._execute_code_dispatch.
_EXECUTE_CODE_BLOCKED_TOOLS = frozenset({
    "execute_code", "spawn_agent", "best_of_n", "clarify", "write_todos", "fetch_tools", "list_toolsets", "search_tools", "replan", "handoff",
    # coding sandbox (see tools/builtin/sandbox/coding_sandbox.py): blocked
    # so a script running inside `execute_code`'s own sandboxed subprocess
    # can't recursively spawn a second coding sandbox from within it.
    "run_code", "install_packages",
})




def _make_llm_summarizer(transport_registry, provider: str, model: str):
    """Build an `AutoCompactHook` summarizer that calls the real LLM via the
    already-lazy `TransportRegistry` — resolving (and thus instantiating)
    the transport only the first time compaction actually triggers, not at
    ControlPlane.start() time."""
    from app.agent_loop_lib.core.tokens import extract_text
    from app.agent_loop_lib.core.types import Message, UserMessage

    async def _summarize(messages: list["Message"]) -> str:
        transport = transport_registry.resolve(provider)
        joined = "\n".join(
            f"[{m.role.value}] {extract_text(m)}" for m in messages
        )
        response = await transport.complete(
            messages=[UserMessage(
                content=(
                    "Summarize the following conversation history concisely, "
                    f"preserving all facts, decisions, and open questions:\n\n{joined}"
                ),
            )],
            system="You are a context compaction summarizer. Be concise but lossless on facts.",
            model=model,
        )
        text = response.message.text
        return text if text else joined[:2_000]

    return _summarize


class ControlPlane:
    """
    Composition root for the agent-loop framework.

    Reads ControlPlaneConfig, provisions all modules (transport, memory,
    knowledge, tools, hooks, roles, budget, stores) into one shared
    `AgentRuntime`, and exposes `make_spec()` (via `AgentFactory`) to turn a
    role name into a fully-wired `AgentSpec` — `Agent(spec, cp.runtime)` is
    then a plain, cheap construction.
    """

    def __init__(self, config: ControlPlaneConfig) -> None:
        self._config = config
        self._transport_registry = None
        self._tool_registry = None
        self._role_registry = None
        self._kernel = None
        self._memory_provider = None
        self._knowledge_provider = None
        self._workspace = None
        self._skills = None
        self._commands = None
        self._os_sandbox = None
        self._db_sandbox = None
        self._browser_sandbox = None
        self._sandbox_manager = None
        self._budget_manager = None
        self._approval_store = None
        self._hil_store = None
        self._state_store = None
        self._timeline_store = None
        self._session_store = None
        self._checkpoint_store = None
        self._runtime: "AgentRuntime | None" = None
        self._factory = None
        self._started = False

    async def start(self) -> None:
        from app.agent_loop_lib.hooks.events import HookEvent
        from app.agent_loop_lib.hooks.middleware.builtin.auto_compact import (
            shape_auto_compact,
        )
        from app.agent_loop_lib.hooks.middleware.builtin.budget_guard import (
            require_budget,
        )
        from app.agent_loop_lib.hooks.middleware.builtin.budget_reduction import (
            shape_budget_reduction,
        )
        from app.agent_loop_lib.hooks.middleware.builtin.coding_sandbox_safety import (
            coding_sandbox_safety,
        )
        from app.agent_loop_lib.hooks.middleware.builtin.e2b_sandbox_guard import (
            e2b_sandbox_guard,
        )
        from app.agent_loop_lib.hooks.middleware.builtin.logging import (
            audit_log_post_agent,
            audit_log_post_tool,
            audit_log_post_turn,
            audit_log_pre_tool,
        )
        from app.agent_loop_lib.hooks.middleware.builtin.mode import enforce_mode
        from app.agent_loop_lib.hooks.middleware.builtin.offload import shape_offload
        from app.agent_loop_lib.hooks.middleware.builtin.permission import (
            require_permission,
        )
        from app.agent_loop_lib.hooks.middleware.builtin.retry import retry_model_call
        from app.agent_loop_lib.hooks.middleware.builtin.sliding_window import (
            shape_sliding_window,
        )
        from app.agent_loop_lib.hooks.middleware.builtin.turn_guards import (
            install_stall_detection,
            install_supervisor_confidence_gate,
        )
        from app.agent_loop_lib.hooks.middleware.builtin.tool_result_clearing import (
            shape_tool_result_clearing,
        )
        from app.agent_loop_lib.hooks.middleware.builtin.tool_safety import (
            enforce_tool_safety,
        )
        from app.agent_loop_lib.hooks.registry import HookRegistry
        from app.agent_loop_lib.modules.providers.budget.tracker import BudgetTracker
        from app.agent_loop_lib.modules.providers.knowledge.in_memory import (
            InMemoryKnowledge,
        )
        from app.agent_loop_lib.modules.providers.memory.in_memory import (
            InMemoryProvider,
        )
        from app.agent_loop_lib.modules.providers.memory.sqlite import (
            SQLiteMemoryProvider,
        )
        from app.agent_loop_lib.modules.providers.workspace.in_memory import (
            InMemoryWorkspaceBackend,
        )
        from app.agent_loop_lib.modules.providers.workspace.local import (
            LocalWorkspaceBackend,
        )
        from app.agent_loop_lib.modules.stores.approval.in_memory import (
            InMemoryApprovalStore,
        )
        from app.agent_loop_lib.modules.stores.checkpoint.in_memory import (
            InMemoryCheckpointStore,
        )
        from app.agent_loop_lib.modules.stores.hil.in_memory import InMemoryHILStore
        from app.agent_loop_lib.modules.stores.session.in_memory import (
            InMemorySessionStore,
        )
        from app.agent_loop_lib.modules.stores.state.in_memory import (
            InMemoryStateStore,
        )
        from app.agent_loop_lib.modules.stores.timeline.in_memory import (
            InMemoryTimelineStore,
        )
        from app.agent_loop_lib.roles.registry import default_registry
        from app.agent_loop_lib.tools.builtin import (
            BestOfNTool,
            BrowserClickTool,
            BrowserFillTool,
            BrowserGetTextTool,
            BrowserNavigateTool,
            BrowserScreenshotTool,
            ClarifyTool,
            CodingSandboxTool,
            CreatePlanTool,
            CritiquePlanTool,
            DBQueryTool,
            EditFileTool,
            ExecuteCodeTool,
            FetchToolsTool,
            GlobTool,
            GrepTool,
            HandoffTool,
            InstallPackagesTool,
            KnowledgeQueryTool,
            ListToolsetsTool,
            LsTool,
            MemoryConsolidateTool,
            MemoryReadTool,
            MemorySearchTool,
            MemoryWriteTool,
            ParseIntentTool,
            ReadFileTool,
            ReadSandboxFileTool,
            ReplanTool,
            RequestReviewTool,
            RouteTaskTool,
            RunShellTool,
            SearchToolsTool,
            SpawnAgentTool,
            TaskCompleteTool,
            VerifyResultTool,
            WebScrapeTool,
            WebSearchTool,
            WriteFileTool,
            WriteTodosTool,
        )
        from app.agent_loop_lib.tools.registry import ToolRegistry
        from app.agent_loop_lib.transport.anthropic import AnthropicTransport
        from app.agent_loop_lib.transport.base import RetryConfig
        from app.agent_loop_lib.transport.ollama import OllamaTransport
        from app.agent_loop_lib.transport.openai import OpenAITransport
        from app.agent_loop_lib.transport.registry import LazyTransport, TransportRegistry

        cfg = self._config

        # 1. Transport → TransportRegistry (lazy: factory only, no instantiation)
        transport_registry = TransportRegistry()
        api_key = cfg.api_key
        model = cfg.model
        max_tokens = cfg.max_tokens
        base_url = cfg.base_url

        # Opik LLM-call tracing (see transport/opik_tracing.py) — wraps
        # whichever transport gets registered below so every LLM call this
        # ControlPlane's agents make is traced identically, regardless of
        # provider. `opik_active` also drives `AgentRuntime.opik_enabled`
        # further down, so `Agent.run()` knows to open a per-run trace.
        from app.agent_loop_lib.transport.opik_tracing import resolve_opik_gate, traced_transport_factory

        opik_active = resolve_opik_gate(cfg.opik.enabled)

        def _traced(factory):
            return traced_transport_factory(
                factory, opik_active=opik_active, project_name=cfg.opik.project_name
            )

        if cfg.transport == "anthropic":
            transport_registry.register(
                "anthropic",
                _traced(lambda: AnthropicTransport(api_key=api_key, model=model, max_tokens=max_tokens)),
            )
        elif cfg.transport == "openai":
            transport_registry.register(
                "openai",
                _traced(lambda: OpenAITransport(api_key=api_key or "", model=model, base_url=base_url)),
            )
        elif cfg.transport == "ollama":
            transport_registry.register(
                "ollama",
                _traced(lambda: OllamaTransport(base_url=base_url or OllamaTransport.DEFAULT_BASE_URL, model=model)),
            )
        else:
            raise ValueError(
                f"Unknown transport backend: {cfg.transport!r}. Supported: 'anthropic', 'openai', 'ollama'"
            )
        self._transport_registry = transport_registry
        self._opik_enabled = opik_active

        # 2. Memory provider
        if cfg.memory == "in_memory":
            self._memory_provider = InMemoryProvider()
        elif cfg.memory == "sqlite":
            self._memory_provider = SQLiteMemoryProvider(cfg.memory_path)
        else:
            raise ValueError(f"Unknown memory backend: {cfg.memory!r}. Supported: 'in_memory', 'sqlite'")

        # 3. Knowledge provider
        if cfg.knowledge == "in_memory":
            self._knowledge_provider = InMemoryKnowledge()
        else:
            raise ValueError(f"Unknown knowledge backend: {cfg.knowledge!r}. Supported: 'in_memory'")

        # 3b. Workspace backend (Phase 3) — backs the filesystem tools below
        if cfg.workspace == "in_memory":
            self._workspace = InMemoryWorkspaceBackend()
        elif cfg.workspace == "local":
            self._workspace = LocalWorkspaceBackend(cfg.workspace_root)
        else:
            raise ValueError(f"Unknown workspace backend: {cfg.workspace!r}. Supported: 'in_memory', 'local'")

        # 4. Budget manager
        if cfg.budget is not None:
            self._budget_manager = BudgetTracker(
                max_input_tokens=cfg.budget.max_tokens,
                max_tool_calls=cfg.budget.max_tool_calls,
                model=model,
            )

        # 5. Builtin tools → ToolRegistry
        tool_registry = ToolRegistry()
        memory = self._memory_provider
        knowledge = self._knowledge_provider
        workspace = self._workspace
        BUILTIN_TOOLS: dict[str, object] = {
            "spawn_agent":     lambda: SpawnAgentTool(),
            "best_of_n":       lambda: BestOfNTool(),
            "memory_read":     lambda: MemoryReadTool(memory),
            "memory_write":    lambda: MemoryWriteTool(memory),
            "memory_search":   lambda: MemorySearchTool(memory),
            "memory_consolidate": lambda: MemoryConsolidateTool(memory),
            "parse_intent":    lambda: ParseIntentTool(transport_registry, cfg.transport),
            "knowledge_query": lambda: KnowledgeQueryTool(knowledge),
            "clarify":         lambda: ClarifyTool(),
            "task_complete":   lambda: TaskCompleteTool(),
            "web_search":      lambda: WebSearchTool(),
            "web_scrape":      lambda: WebScrapeTool(),
            "write_todos":     lambda: WriteTodosTool(),
            "replan":          lambda: ReplanTool(),
            "handoff":         lambda: HandoffTool(),
            "route_task":      lambda: RouteTaskTool(),
            "create_plan":     lambda: CreatePlanTool(),
            "critique_plan":   lambda: CritiquePlanTool(),
            "verify_result":   lambda: VerifyResultTool(),
            "request_review":  lambda: RequestReviewTool(),
            "ls":              lambda: LsTool(workspace),
            "read_file":       lambda: ReadFileTool(workspace),
            "write_file":      lambda: WriteFileTool(workspace),
            "edit_file":       lambda: EditFileTool(workspace),
            "glob":            lambda: GlobTool(workspace),
            "grep":            lambda: GrepTool(workspace),
            "execute_code":    lambda: ExecuteCodeTool(
                self._execute_code_dispatch,
                timeout=cfg.execute_code_timeout,
            ),
        }
        tools_to_register = (
            list(BUILTIN_TOOLS.keys()) if "all" in cfg.tools else cfg.tools
        )
        for tool_name in tools_to_register:
            if tool_name in BUILTIN_TOOLS:
                tool_registry.register_tool(BUILTIN_TOOLS[tool_name]())

        # 5b. Lazy toolsets (Phase 1 progressive disclosure): meta-tools +
        # grouping of the non-essential builtins registered above. spawn_agent,
        # clarify, and task_complete stay essential (always visible) — everything
        # else ships as an overview until fetch_tools loads its real schemas.
        if cfg.lazy_toolsets.enabled:
            tool_registry.register_tool(ListToolsetsTool(tool_registry))
            tool_registry.register_tool(FetchToolsTool(tool_registry))
            tool_registry.register_tool(SearchToolsTool(tool_registry))
            _TOOLSET_DEFS: dict[str, tuple[str, list[str]]] = {
                "memory": ("Read, write, search, and consolidate agent memory.", ["memory_read", "memory_write", "memory_search", "memory_consolidate"]),
                "knowledge": ("Query the knowledge base.", ["knowledge_query"]),
                "web_search": ("Search the web and scrape pages.", ["web_search", "web_scrape"]),
                "filesystem": ("Read, write, edit, and search files in the workspace.", ["ls", "read_file", "write_file", "edit_file", "glob", "grep"]),
                # Category 2 tools (see .claude/rules/principles.md's "everything
                # via tool calls" gap map) — route/plan/critique/verify/escalate,
                # exposed purely as agent-callable tools (never a hardwired
                # pre-loop step). Still opt-in overall: this only groups them
                # for lazy disclosure WHEN they're already present in
                # `cfg.tools`, same as every other toolset here — it does not
                # add them to `cfg.tools` itself.
                "planning": (
                    "Route, plan, critique, verify, and escalate tasks mid-loop.",
                    ["route_task", "create_plan", "critique_plan", "verify_result", "request_review"],
                ),
            }
            for toolset_name, (description, members) in _TOOLSET_DEFS.items():
                present = [m for m in members if tool_registry.has(m)]
                if present:
                    tool_registry.register_toolset(toolset_name, description, present)

        # 5c. Commands (Phase 3) — client-side markdown slash-commands
        # (see commands/loader.py); not agent-facing, so unlike skills there
        # is no tool/prompt-section wiring here — only the CLI (cli.py)
        # consumes `self._commands` directly, via `/name args` dispatch.
        from app.agent_loop_lib.commands.registry import CommandRegistry
        command_registry = CommandRegistry()
        for commands_dir in cfg.commands_dirs:
            command_registry.load_dir(commands_dir)
        self._commands = command_registry

        # 5d. Sandbox taxonomy (Phase 3): one SandboxProvider interface, typed
        # sandboxes on top. Code sandbox = execute_code above (its own
        # subprocess+RPC bridge, sandbox/rpc.py); these three are opt-in.
        if cfg.os_sandbox.enabled:
            from app.agent_loop_lib.sandbox.local import LocalSandbox
            from app.agent_loop_lib.sandbox.os_sandbox import ConfinedLocalSandbox
            osc = cfg.os_sandbox
            self._os_sandbox = (
                ConfinedLocalSandbox(working_dir=osc.working_dir, allow_network=osc.allow_network)
                if osc.confine else LocalSandbox(working_dir=osc.working_dir)
            )
            tool_registry.register_tool(RunShellTool(self._os_sandbox, timeout=osc.timeout))

        if cfg.db_sandbox.enabled:
            from app.agent_loop_lib.sandbox.db_sandbox import SqliteDBSandbox
            dbc = cfg.db_sandbox
            self._db_sandbox = SqliteDBSandbox(dbc.db_path, dbc.mode, dbc.table_allowlist)
            tool_registry.register_tool(DBQueryTool(self._db_sandbox))

        if cfg.browser_sandbox.enabled:
            from app.agent_loop_lib.sandbox.browser_sandbox import (
                PlaywrightBrowserSandbox,
            )
            self._browser_sandbox = PlaywrightBrowserSandbox(headless=cfg.browser_sandbox.headless)
            browser = self._browser_sandbox
            tool_registry.register_tool(BrowserNavigateTool(browser))
            tool_registry.register_tool(BrowserGetTextTool(browser))
            tool_registry.register_tool(BrowserClickTool(browser))
            tool_registry.register_tool(BrowserFillTool(browser))
            tool_registry.register_tool(BrowserScreenshotTool(browser))

        if cfg.coding_sandbox.enabled:
            from app.agent_loop_lib.sandbox.manager import (
                SandboxLimits,
                SandboxManager,
                SandboxType,
            )

            csc = cfg.coding_sandbox
            self._sandbox_manager = SandboxManager()

            if csc.backend == "local":
                import os
                import uuid

                from app.agent_loop_lib.sandbox.coding.executor import ExecutionLimits
                from app.agent_loop_lib.sandbox.coding.local import LocalCodingSandbox

                lbc = csc.local
                root = lbc.working_dir_root

                def _make_local_coding_sandbox() -> LocalCodingSandbox:
                    working_dir = (
                        os.path.join(root, f"alcs-{uuid.uuid4().hex[:10]}")
                        if root is not None else None
                    )
                    return LocalCodingSandbox(
                        working_dir=working_dir,
                        allow_network_on_install=csc.allow_network_on_install,
                        typecheck_typescript=lbc.typecheck_typescript,
                        package_allowlist=csc.package_allowlist,
                        package_denylist=csc.package_denylist,
                        limits=ExecutionLimits(
                            max_memory_bytes=lbc.rlimits.max_memory_bytes,
                            max_cpu_seconds=lbc.rlimits.max_cpu_seconds,
                            max_file_size_bytes=lbc.rlimits.max_file_size_bytes,
                            max_processes=lbc.rlimits.max_processes,
                        ),
                    )

                self._sandbox_manager.register_backend_factory(
                    SandboxType.CODING,
                    _make_local_coding_sandbox,
                    limits=SandboxLimits(max_concurrent=csc.max_concurrent, max_lifetime_s=csc.max_lifetime_s),
                )
            elif csc.backend == "e2b":
                from app.agent_loop_lib.sandbox.coding.e2b import E2BCodingSandbox

                ebc = csc.e2b

                def _make_e2b_coding_sandbox() -> E2BCodingSandbox:
                    return E2BCodingSandbox(
                        api_key=ebc.api_key,
                        template=ebc.template,
                        e2b_timeout=ebc.e2b_timeout,
                        allow_internet_access=ebc.allow_internet_access,
                        package_allowlist=csc.package_allowlist,
                        package_denylist=csc.package_denylist,
                    )

                self._sandbox_manager.register_backend_factory(
                    SandboxType.CODING,
                    _make_e2b_coding_sandbox,
                    limits=SandboxLimits(max_concurrent=csc.max_concurrent, max_lifetime_s=csc.max_lifetime_s),
                )
            elif csc.backend == "docker":
                import os
                import uuid

                from app.agent_loop_lib.sandbox.coding.docker import DockerCodingSandbox

                dbc = csc.docker
                root = dbc.working_dir_root

                def _make_docker_coding_sandbox() -> DockerCodingSandbox:
                    working_dir = (
                        os.path.join(root, f"alcs-docker-{uuid.uuid4().hex[:10]}")
                        if root is not None else None
                    )
                    return DockerCodingSandbox(
                        image=dbc.image,
                        working_dir=working_dir,
                        memory_limit_mb=dbc.memory_limit_mb,
                        cpu_limit=dbc.cpu_limit,
                        egress_network=dbc.egress_network,
                        network_disabled=dbc.network_disabled,
                        pip_index_url=dbc.pip_index_url,
                        npm_registry=dbc.npm_registry,
                        package_allowlist=csc.package_allowlist,
                        package_denylist=csc.package_denylist,
                    )

                self._sandbox_manager.register_backend_factory(
                    SandboxType.CODING,
                    _make_docker_coding_sandbox,
                    limits=SandboxLimits(max_concurrent=csc.max_concurrent, max_lifetime_s=csc.max_lifetime_s),
                )
            else:
                raise ValueError(
                    f"Unknown coding_sandbox backend: {csc.backend!r}. Supported backends are 'local', "
                    "'e2b', and 'docker' — other remote backends (daytona/aio) are documented future work."
                )

            tool_registry.register_tool(CodingSandboxTool(
                self._sandbox_manager,
                default_timeout=csc.default_timeout,
                artifact_output_dir=csc.artifact_output_dir,
            ))
            tool_registry.register_tool(InstallPackagesTool(self._sandbox_manager))
            tool_registry.register_tool(ReadSandboxFileTool(self._sandbox_manager))

        if cfg.lazy_toolsets.enabled:
            _SANDBOX_TOOLSET_DEFS: dict[str, tuple[str, list[str]]] = {
                "os_sandbox": ("Execute shell commands in a sandboxed OS environment.", ["run_shell"]),
                "db_sandbox": ("Run scoped SQL queries against a sandboxed database.", ["db_query"]),
                "browser_sandbox": (
                    "Navigate and interact with web pages via a sandboxed browser.",
                    ["browser_navigate", "browser_get_text", "browser_click", "browser_fill", "browser_screenshot"],
                ),
                "coding_sandbox": (
                    "Write and run standalone TypeScript/Python programs in an isolated, kernel-confined sandbox.",
                    ["run_code", "install_packages", "read_sandbox_file"],
                ),
            }
            for toolset_name, (description, members) in _SANDBOX_TOOLSET_DEFS.items():
                present = [m for m in members if tool_registry.has(m)]
                if present:
                    tool_registry.register_toolset(toolset_name, description, present)

        self._tool_registry = tool_registry

        # 6. HookRegistry (kernel) — one Pipeline/Wrapper per HookEvent,
        # populated with middleware instead of a fixed-shape HookChain.
        kernel = HookRegistry()
        for hook_name in cfg.hooks:
            if hook_name == "logging":
                kernel.on(HookEvent.PRE_TOOL_USE).use(audit_log_pre_tool())
                kernel.on(HookEvent.POST_TOOL_USE).use(audit_log_post_tool())
                kernel.on(HookEvent.POST_TURN).use(audit_log_post_turn())
                kernel.on(HookEvent.POST_AGENT).use(audit_log_post_agent())
            elif hook_name == "budget_guard" and self._budget_manager is not None:
                kernel.on(HookEvent.PRE_TOOL_USE).use(require_budget(self._budget_manager))
            elif hook_name == "permission":
                kernel.on(HookEvent.PRE_TOOL_USE).use(
                    require_permission(allowlist=cfg.allowlist, denylist=cfg.denylist or None)
                )
            elif hook_name == "retry":
                kernel.wrapper(HookEvent.PRE_MODEL_CALL).use(retry_model_call(RetryConfig(
                    max_retries=cfg.retry_config.max_retries,
                    initial_delay=cfg.retry_config.initial_delay,
                    backoff_factor=cfg.retry_config.backoff_factor,
                    max_delay=cfg.retry_config.max_delay,
                )))
            elif hook_name == "tool_safety":
                kernel.on(HookEvent.PRE_TOOL_USE).use(enforce_tool_safety())
            elif hook_name == "coding_sandbox_safety":
                csc = cfg.coding_sandbox
                kernel.on(HookEvent.PRE_TOOL_USE).use(
                    "/toolsets/coding_sandbox/**",
                    coding_sandbox_safety(
                        max_code_size=csc.max_code_size,
                        blocked_patterns=csc.blocked_patterns,
                        allow_url_packages=csc.allow_url_packages,
                    ),
                )
            elif hook_name == "e2b_sandbox_guard":
                ebc = cfg.coding_sandbox.e2b
                kernel.on(HookEvent.PRE_TOOL_USE).use(
                    "/toolsets/coding_sandbox/**",
                    e2b_sandbox_guard(max_timeout=float(ebc.e2b_timeout)),
                )
            elif hook_name == "context_engine":
                ce = cfg.context_engine
                # Registered cheapest-first (PRE_MODEL runs shapers in
                # registration order): budget reduction -> tool-result
                # clearing -> offload -> sliding window -> auto-compact.
                if ce.enable_budget_reduction:
                    kernel.on(HookEvent.PRE_MODEL).use(shape_budget_reduction(max_result_chars=ce.max_result_chars))
                if ce.enable_tool_result_clearing:
                    kernel.on(HookEvent.PRE_MODEL).use(shape_tool_result_clearing(
                        keep_last_n_turns=ce.clearing_keep_last_n_turns,
                        trigger_ratio=ce.clearing_trigger_ratio,
                    ))
                if ce.enable_offload:
                    kernel.on(HookEvent.PRE_MODEL).use(shape_offload(
                        threshold_tokens=ce.offload_threshold_tokens,
                        preview_lines=ce.offload_preview_lines,
                    ))
                if ce.enable_sliding_window:
                    kernel.on(HookEvent.PRE_MODEL).use(shape_sliding_window(pin_first_n=ce.sliding_window_pin_first_n))
                if ce.enable_auto_compact:
                    kernel.on(HookEvent.PRE_MODEL).use(shape_auto_compact(
                        summarizer=_make_llm_summarizer(transport_registry, cfg.transport, model),
                        trigger_ratio=ce.auto_compact_trigger_ratio,
                        keep_last_n_messages=ce.auto_compact_keep_last_n_messages,
                    ))
            elif hook_name == "skill_learning":
                # Deferred: needs the SkillManager + timeline store, both
                # built below at step 8b/8 — wired onto this same `kernel`
                # instance right after those exist, same pattern the
                # "approval" hook (step 9) already uses for a post-stores
                # dependency.
                pass
            elif hook_name == "supervisor_confidence_gate":
                install_supervisor_confidence_gate(kernel)
            elif hook_name == "stall_detection":
                install_stall_detection(kernel)

        # Auto-add budget guard when budget configured but not in hooks list
        if self._budget_manager is not None and "budget_guard" not in cfg.hooks:
            kernel.on(HookEvent.PRE_TOOL_USE).use(require_budget(self._budget_manager))
        # Auto-add permission when allowlist/denylist set but not in hooks list
        if (cfg.allowlist is not None or cfg.denylist) and "permission" not in cfg.hooks:
            kernel.on(HookEvent.PRE_TOOL_USE).use(
                require_permission(allowlist=cfg.allowlist, denylist=cfg.denylist or None)
            )
        # Auto-add mode enforcement when a non-default mode is configured —
        # pairs AgentSpec.mode with real PRE_TOOL_USE blocking (see hooks/middleware/builtin/mode.py).
        if cfg.mode != "act":
            kernel.on(HookEvent.PRE_TOOL_USE).use(enforce_mode(cfg.mode))
        # Auto-add coding sandbox safety (defense-in-depth static checks,
        # scoped to just its own toolset subtree) whenever the sandbox
        # itself is enabled but not in hooks list.
        if cfg.coding_sandbox.enabled and "coding_sandbox_safety" not in cfg.hooks:
            csc = cfg.coding_sandbox
            kernel.on(HookEvent.PRE_TOOL_USE).use(
                "/toolsets/coding_sandbox/**",
                coding_sandbox_safety(
                    max_code_size=csc.max_code_size,
                    blocked_patterns=csc.blocked_patterns,
                    allow_url_packages=csc.allow_url_packages,
                ),
            )
        # Auto-add the E2B billing/timeout guard whenever backend="e2b" but
        # not already explicitly listed in hooks — local users pay zero
        # overhead since this middleware is scoped to backend="e2b" only.
        if (
            cfg.coding_sandbox.enabled
            and cfg.coding_sandbox.backend == "e2b"
            and "e2b_sandbox_guard" not in cfg.hooks
        ):
            ebc = cfg.coding_sandbox.e2b
            kernel.on(HookEvent.PRE_TOOL_USE).use(
                "/toolsets/coding_sandbox/**",
                e2b_sandbox_guard(max_timeout=float(ebc.e2b_timeout)),
            )
        self._kernel = kernel

        # 7. RoleRegistry (builtin roles: assistant, planner, researcher, critic, verifier, writer,
        # web_search, coder, skill_writer)
        self._role_registry = default_registry()

        # 8. Stores — checkpoint, hil, approval, and the opt-in
        # state/timeline/session stores. All process-local (in-memory);
        # PipesHub's own durable stores (Mongo/Arango/Redis) back
        # everything that actually needs to survive a restart, so no
        # separate persistence layer is wired here.
        #
        # HIL, approval, and checkpoint stores are always created — resume()
        # and the approval hook depend on them regardless of the opt-in
        # observability flags below.
        self._hil_store = InMemoryHILStore()
        self._approval_store = InMemoryApprovalStore()
        self._checkpoint_store = InMemoryCheckpointStore()
        if cfg.enable_state_tracking:
            self._state_store = InMemoryStateStore()
        if cfg.enable_timeline:
            self._timeline_store = InMemoryTimelineStore()
        if cfg.enable_session:
            self._session_store = InMemorySessionStore()

        # 8b. Skills manager — deliberately wired here, AFTER the stores
        # above (not back at step 5c with everything else), to mirror that
        # dependency ordering even though the tracker itself is now
        # in-memory. Entirely skipped (no filesystem touched at all) when
        # `skill_manager.skills_dir` is left unset — the feature is opt-in.
        smc = cfg.skill_manager
        if smc.skills_dir is not None:
            from app.agent_loop_lib.modules.providers.skills.evaluator import (
                RubricSkillEvaluator,
            )
            from app.agent_loop_lib.modules.providers.skills.extractor import (
                LLMSkillExtractor,
            )
            from app.agent_loop_lib.modules.providers.skills.filesystem_index import (
                FilesystemSkillIndex,
            )
            from app.agent_loop_lib.modules.providers.skills.filesystem_store import (
                FilesystemSkillStore,
            )
            from app.agent_loop_lib.modules.providers.skills.in_memory_tracker import (
                InMemoryUsageTracker,
            )
            from app.agent_loop_lib.modules.providers.skills.manager import (
                SkillManager,
                SkillManagerConfig,
            )
            from app.agent_loop_lib.modules.providers.skills.validator import (
                SkillValidator,
            )
            from app.agent_loop_lib.tools.builtin.data.skills import (
                LoadSkillResourceTool,
                LoadSkillTool,
                SkillManageTool,
                SkillSearchTool,
                SkillsListTool,
            )

            skill_validator = SkillValidator()
            skill_store = FilesystemSkillStore(smc.skills_dir, smc.extra_skills_dirs, skill_validator)
            skill_index = FilesystemSkillIndex(smc.skills_dir)
            skill_tracker = InMemoryUsageTracker()

            skill_grader = None
            if smc.require_passing_grade:
                from app.agent_loop_lib.eval.rubric import RubricGrader
                skill_grader = RubricGrader(
                    LazyTransport(transport_registry, cfg.transport), pass_threshold=smc.pass_threshold,
                )
            skill_evaluator = RubricSkillEvaluator(grader=skill_grader, index=skill_index)
            skill_extractor = (
                LLMSkillExtractor(LazyTransport(transport_registry, cfg.transport))
                if "skill_learning" in cfg.hooks else None
            )

            skill_manager = SkillManager(
                store=skill_store, index=skill_index, tracker=skill_tracker, validator=skill_validator,
                extractor=skill_extractor, evaluator=skill_evaluator,
                config=SkillManagerConfig(
                    skills_dir=smc.skills_dir,
                    extra_skills_dirs=smc.extra_skills_dirs,
                    auto_approve=smc.auto_approve,
                    write_approval=smc.write_approval,
                    learning_enabled=smc.learning_enabled,
                    max_candidates=smc.max_candidates,
                    catalog_render_limit=smc.catalog_render_limit,
                ),
            )
            await skill_manager.start()
            self._skills = skill_manager

            # Read tools register whenever the feature is enabled at all
            # (fixes the old conditional-registration bug where a skill
            # created at runtime into an initially-empty library was never
            # loadable — an empty catalog just renders nothing into the
            # prompt); `skill_manage` registers alongside them since any
            # role (skill_writer, or an interactive admin role) may need to
            # author/edit/deprecate skills regardless of whether the
            # automatic learning-loop hook itself is enabled.
            tool_registry.register_tool(SkillsListTool(skill_manager))
            tool_registry.register_tool(LoadSkillTool(skill_manager))
            tool_registry.register_tool(LoadSkillResourceTool(skill_manager))
            tool_registry.register_tool(SkillSearchTool(skill_manager))
            tool_registry.register_tool(SkillManageTool(skill_manager))
            if cfg.lazy_toolsets.enabled:
                tool_registry.register_toolset(
                    "skills",
                    "Search, load, and manage the skill library.",
                    ["skills_list", "load_skill", "load_skill_resource", "skill_search", "skill_manage"],
                )
                # Pinned (visible at turn 0), not left to a `fetch_tools`
                # round-trip like other lazy toolsets — a skill needs to be
                # checked BEFORE the model improvises its first move, not
                # discovered after it's already committed to an approach.
                # Mutates the same `LazyToolsetsConfig` instance `make_spec()`
                # reads `pinned_toolsets` from below, so every role built
                # from this ControlPlane gets it, even one that overrides
                # `cfg.lazy_toolsets.pinned_toolsets` to a different list.
                if "skills" not in cfg.lazy_toolsets.pinned_toolsets:
                    cfg.lazy_toolsets.pinned_toolsets.append("skills")

            if "skill_learning" in cfg.hooks:
                from app.agent_loop_lib.hooks.middleware.builtin.skill_learning import (
                    SkillLearning,
                )
                kernel.on(HookEvent.POST_AGENT).use(SkillLearning(
                    manager=skill_manager,
                    spawn_skill_writer=self._spawn_skill_writer,
                    timeline_store=self._timeline_store,
                ))

        # 9. Post-wire approval middleware (stores must be ready first) —
        # bridges the new PRE_TOOL_USE pipeline to the existing rich
        # RiskLevel/ApprovalPolicy/ApprovalStore/HIL system, replacing the
        # old ApprovalHookAdapter.
        if "approval" in cfg.hooks:
            from app.agent_loop_lib.modules.stores.approval.hook import ApprovalHook
            from app.agent_loop_lib.tools.approval import (
                StorageBackedApprovalHandler,
                require_approval,
            )
            approval_hook = ApprovalHook(
                store=self._approval_store,
                hil_store=self._hil_store,
                tool_registry=self._tool_registry,
            )
            kernel.on(HookEvent.PRE_TOOL_USE).use(
                require_approval(StorageBackedApprovalHandler(approval_hook))
            )

        # 10. AgentRuntime + AgentFactory — the shared Layer 2 services
        # object every Agent this ControlPlane constructs (directly or via
        # spawn_agent/best_of_n/AgentTool's `run_child()`) draws on, and the
        # Layer 4 factory that turns a role NAME into a full `AgentSpec`.
        from app.agent_loop_lib.runtime.factory import AgentFactory
        from app.agent_loop_lib.runtime.runtime import AgentRuntime

        self._runtime = AgentRuntime(
            transport_registry=self._transport_registry,
            tool_registry=self._tool_registry,
            hooks=kernel,
            budget=self._budget_manager,
            memory=self._memory_provider,
            knowledge=self._knowledge_provider,
            skills=self._skills,
            checkpoint_store=self._checkpoint_store,
            session_store=self._session_store,
            state_store=self._state_store,
            hil_store=self._hil_store,
            approval_store=self._approval_store,
            timeline_store=self._timeline_store,
            role_registry=self._role_registry,
        )
        self._factory = AgentFactory(
            self._runtime, self._role_registry,
            default_provider=cfg.transport, default_model=model,
        )
        self._runtime.spec_factory = self._factory.from_role

        self._started = True

    async def stop(self) -> None:
        if self._memory_provider is not None and hasattr(self._memory_provider, "close"):
            await self._memory_provider.close()
        if self._browser_sandbox is not None:
            await self._browser_sandbox.close()
        if self._sandbox_manager is not None:
            await self._sandbox_manager.destroy_all()
        self._started = False

    async def _execute_code_dispatch(self, name: str, args: dict) -> object:
        """RPC bridge dispatch for `execute_code` (sandbox/rpc.py): routes a
        `tool(name, **kwargs)` call made from INSIDE sandboxed code through
        the exact same `ToolExecutor.call_tool()` — PreToolUse -> execute ->
        PostToolUse — as a normal top-level tool call, so permission/
        approval/mode enforcement is never bypassed just because the call
        originated from code instead of the model directly."""
        import uuid as _uuid

        from app.agent_loop_lib.core.types import ToolCall
        from app.agent_loop_lib.tools.executor import ToolExecutor

        if name in _EXECUTE_CODE_BLOCKED_TOOLS:
            raise ValueError(f"Tool '{name}' cannot be called from execute_code — call it directly instead.")
        if self._tool_registry is None or not self._tool_registry.has(name):
            raise ValueError(f"Unknown tool: {name!r}")

        call = ToolCall(id=str(_uuid.uuid4()), name=name, arguments=args)
        result = await ToolExecutor(self._tool_registry, self._kernel).call_tool(call)
        if result.is_error:
            raise RuntimeError(str(result.content))
        return result.content

    async def _spawn_skill_writer(self, goal_description: str) -> object:
        """Programmatic sub-agent spawn (skill self-creation loop) — used as
        `SkillCreationHook`'s `spawn_skill_writer` callback. Unlike the
        `spawn_agent` tool, this isn't triggered by the model; it's the hook
        layer reacting to a detected pattern, so it goes through the same
        `AgentRuntime.run_child()` every other non-LLM-triggered spawn
        (AgentTool) uses, rather than constructing an `Agent`
        by hand."""
        from app.agent_loop_lib.core.types import Goal

        spec = self.make_spec("skill_writer")
        return await self._runtime.run_child(spec, Goal(description=goal_description), parent_run_ctx=None)

    def make_spec(self, role_name: str = "assistant", **overrides) -> "AgentSpec":
        """Resolve a role NAME to a fully-wired `AgentSpec` via
        `AgentFactory.from_role()`. `**overrides` are `AgentSpec` field
        overrides (`tool_names`, `model`, `max_turns`, `loop`, ...) — see
        `runtime/factory.py::AgentFactory.from_role_obj`."""
        if not self._started:
            raise RuntimeError(
                "ControlPlane must be started before calling make_spec(). "
                "Call `await cp.start()` or use `async with ControlPlane(...) as cp:`."
            )
        try:
            role = self._role_registry.resolve(role_name)
        except Exception:
            raise ValueError(
                f"Unknown role: {role_name!r}. Available: {self._role_registry.names()}"
            )
        # The global ControlPlaneConfig.mode is only a fallback default —
        # an explicit per-call override or the role's own `mode` both win.
        if role.mode is None and "mode" not in overrides:
            overrides.setdefault("mode", self._config.mode)
        overrides.setdefault("pinned_toolsets", list(self._config.lazy_toolsets.pinned_toolsets))
        return self._factory.from_role_obj(role, **overrides)

    def create_agent(self, role_name: str = "assistant") -> object:
        from app.agent_loop_lib.agent import Agent
        return Agent(self.make_spec(role_name), self._runtime)

    @property
    def runtime(self) -> "AgentRuntime":
        return self._runtime

    @property
    def factory(self):
        return self._factory

    @property
    def tool_registry(self):
        return self._tool_registry

    @property
    def role_registry(self):
        return self._role_registry

    @property
    def transport_registry(self):
        return self._transport_registry

    @property
    def memory_provider(self):
        return self._memory_provider

    @property
    def workspace(self):
        return self._workspace

    @property
    def skills(self):
        return self._skills

    @property
    def commands(self):
        return self._commands

    @property
    def os_sandbox(self):
        return self._os_sandbox

    @property
    def db_sandbox(self):
        return self._db_sandbox

    @property
    def browser_sandbox(self):
        return self._browser_sandbox

    @property
    def sandbox_manager(self):
        return self._sandbox_manager

    @property
    def checkpoint_store(self):
        return self._checkpoint_store

    async def __aenter__(self) -> "ControlPlane":
        await self.start()
        return self

    async def __aexit__(self, *args) -> None:
        await self.stop()
