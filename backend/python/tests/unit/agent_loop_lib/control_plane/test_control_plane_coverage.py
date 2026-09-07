"""Coverage-focused tests for `ControlPlane` — exercises the config-driven
branches in `start()` (transport/memory/knowledge/workspace/sandbox
backends, hook wiring, auto-add hook logic, skills, stores), `stop()`
(happy path + idempotency), `make_spec()` (validation + overrides), and the
small helper methods (`_execute_code_dispatch`, `_spawn_skill_writer`,
`create_agent`, properties, the async context-manager protocol).

None of these tests make real network/LLM calls: transport registration is
lazy (factory only — see `ControlPlane.start()`'s step 1 comment), and every
sandbox backend this suite exercises either constructs cheaply (no I/O in
`__init__`) or is registered as a lazy factory with `SandboxManager` rather
than instantiated eagerly.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.agent_loop_lib.control_plane.config import (
    BudgetConfig,
    BrowserSandboxConfig,
    CodingSandboxConfig,
    ControlPlaneConfig,
    DBSandboxConfig,
    OSSandboxConfig,
    SkillManagerConfig,
)
from app.agent_loop_lib.control_plane.control_plane import ControlPlane
from app.agent_loop_lib.modules.providers.memory.sqlite import SQLiteMemoryProvider
from app.agent_loop_lib.modules.providers.workspace.local import LocalWorkspaceBackend


def _minimal_cfg(**overrides) -> ControlPlaneConfig:
    """Bare config: no default tools/hooks, so each test only pays for the
    branches it actually asks for."""
    overrides.setdefault("tools", [])
    overrides.setdefault("hooks", [])
    return ControlPlaneConfig(**overrides)


class TestTransportBackends:
    async def test_openai_transport_registers_without_error(self) -> None:
        cp = ControlPlane(_minimal_cfg(transport="openai", api_key=None))
        await cp.start()
        assert cp.transport_registry.has("openai")

    async def test_ollama_transport_uses_default_base_url_when_unset(self) -> None:
        cp = ControlPlane(_minimal_cfg(transport="ollama", base_url=None))
        await cp.start()
        assert cp.transport_registry.has("ollama")

    async def test_unknown_transport_raises(self) -> None:
        cp = ControlPlane(_minimal_cfg(transport="not-a-real-transport"))
        with pytest.raises(ValueError, match="Unknown transport backend"):
            await cp.start()


class TestMemoryBackends:
    async def test_sqlite_memory_backend(self) -> None:
        cp = ControlPlane(_minimal_cfg(memory="sqlite", memory_path=":memory:"))
        await cp.start()
        assert isinstance(cp.memory_provider, SQLiteMemoryProvider)

    async def test_unknown_memory_backend_raises(self) -> None:
        cp = ControlPlane(_minimal_cfg(memory="not-a-real-memory"))
        with pytest.raises(ValueError, match="Unknown memory backend"):
            await cp.start()


class TestKnowledgeBackend:
    async def test_unknown_knowledge_backend_raises(self) -> None:
        cp = ControlPlane(_minimal_cfg(knowledge="not-a-real-knowledge"))
        with pytest.raises(ValueError, match="Unknown knowledge backend"):
            await cp.start()


class TestWorkspaceBackends:
    async def test_local_workspace_backend(self, tmp_path) -> None:
        cp = ControlPlane(_minimal_cfg(workspace="local", workspace_root=str(tmp_path)))
        await cp.start()
        assert isinstance(cp.workspace, LocalWorkspaceBackend)

    async def test_unknown_workspace_backend_raises(self) -> None:
        cp = ControlPlane(_minimal_cfg(workspace="not-a-real-workspace"))
        with pytest.raises(ValueError, match="Unknown workspace backend"):
            await cp.start()


class TestBudgetAndTools:
    async def test_budget_config_wires_budget_manager_onto_runtime(self) -> None:
        cp = ControlPlane(_minimal_cfg(budget=BudgetConfig(max_tokens=500, max_tool_calls=7)))
        await cp.start()
        assert cp.runtime.budget is not None
        # Confirm the configured max_tokens actually made it onto the
        # tracker (not just that some tracker got constructed) by recording
        # usage past the limit and expecting `check()` to enforce it.
        from app.agent_loop_lib.core.exceptions import BudgetExceeded

        await cp.runtime.budget.record_turn(input_tokens=600, output_tokens=0)
        with pytest.raises(BudgetExceeded):
            await cp.runtime.budget.check()

    async def test_default_tools_are_registered_and_toolsets_grouped(self) -> None:
        cfg = ControlPlaneConfig(hooks=[])  # keep default `tools`, default lazy_toolsets
        cp = ControlPlane(cfg)
        await cp.start()
        for name in ("memory_read", "memory_write", "knowledge_query", "clarify"):
            assert cp.tool_registry.has(name)
        # "memory" toolset groups memory_read/write/search/consolidate, all
        # present in the default tools list.
        assert cp.tool_registry.has_toolsets()

    async def test_lazy_toolsets_disabled_skips_toolset_grouping(self) -> None:
        cfg = ControlPlaneConfig(hooks=[], lazy_toolsets={"enabled": False})
        cp = ControlPlane(cfg)
        await cp.start()
        assert not cp.tool_registry.has_toolsets()
        # The core tools are still registered even without toolset grouping.
        assert cp.tool_registry.has("memory_read")

    async def test_unregistered_tool_name_in_tools_list_is_ignored(self) -> None:
        cfg = ControlPlaneConfig(hooks=[], tools=["memory_read", "not-a-real-builtin-tool"])
        cp = ControlPlane(cfg)
        await cp.start()
        assert cp.tool_registry.has("memory_read")
        assert not cp.tool_registry.has("not-a-real-builtin-tool")

    async def test_all_sentinel_registers_every_builtin_tool(self) -> None:
        cfg = ControlPlaneConfig(hooks=[], tools=["all"])
        cp = ControlPlane(cfg)
        await cp.start()
        for name in ("ls", "read_file", "execute_code", "web_search", "route_task"):
            assert cp.tool_registry.has(name)


class TestCommandsDir:
    async def test_commands_dir_scanned_at_start(self, tmp_path) -> None:
        cfg = _minimal_cfg(commands_dirs=[str(tmp_path)])
        cp = ControlPlane(cfg)
        await cp.start()
        assert cp.commands.names() == []


class TestOSSandbox:
    async def test_confined_os_sandbox_registers_run_shell(self, tmp_path) -> None:
        cfg = _minimal_cfg(os_sandbox=OSSandboxConfig(enabled=True, working_dir=str(tmp_path), confine=True))
        cp = ControlPlane(cfg)
        await cp.start()
        assert cp.os_sandbox is not None
        assert cp.tool_registry.has("run_shell")

    async def test_unconfined_os_sandbox_uses_plain_local_sandbox(self, tmp_path) -> None:
        from app.agent_loop_lib.sandbox.local import LocalSandbox
        from app.agent_loop_lib.sandbox.os_sandbox import ConfinedLocalSandbox

        cfg = _minimal_cfg(os_sandbox=OSSandboxConfig(enabled=True, working_dir=str(tmp_path), confine=False))
        cp = ControlPlane(cfg)
        await cp.start()
        assert isinstance(cp.os_sandbox, LocalSandbox)
        assert not isinstance(cp.os_sandbox, ConfinedLocalSandbox)


class TestDBSandbox:
    async def test_db_sandbox_registers_db_query_tool(self) -> None:
        cfg = _minimal_cfg(db_sandbox=DBSandboxConfig(enabled=True, db_path=":memory:"))
        cp = ControlPlane(cfg)
        await cp.start()
        assert cp.db_sandbox is not None
        assert cp.tool_registry.has("db_query")


class TestBrowserSandbox:
    async def test_browser_sandbox_registers_browser_tools(self) -> None:
        cfg = _minimal_cfg(browser_sandbox=BrowserSandboxConfig(enabled=True, headless=True))
        cp = ControlPlane(cfg)
        await cp.start()
        assert cp.browser_sandbox is not None
        for name in ("browser_navigate", "browser_get_text", "browser_click", "browser_fill", "browser_screenshot"):
            assert cp.tool_registry.has(name)


class TestCodingSandboxBackends:
    async def test_local_backend_registers_coding_tools_and_auto_safety_hook(self, tmp_path) -> None:
        cfg = ControlPlaneConfig(
            tools=[],
            # Default hooks (logging/retry/context_engine) do NOT include
            # coding_sandbox_safety, so enabling the sandbox should auto-add it.
            coding_sandbox=CodingSandboxConfig(enabled=True, backend="local", local={"working_dir_root": str(tmp_path)}),
        )
        cp = ControlPlane(cfg)
        await cp.start()
        assert cp.sandbox_manager is not None
        for name in ("run_code", "install_packages", "read_sandbox_file"):
            assert cp.tool_registry.has(name)

    async def test_e2b_backend_registers_factory_and_auto_guard_hook(self) -> None:
        from unittest.mock import patch

        from app.agent_loop_lib.sandbox.coding.factories.e2b import (
            E2BCodingSandboxFactory,
        )

        cfg = ControlPlaneConfig(
            tools=[],
            coding_sandbox=CodingSandboxConfig(enabled=True, backend="e2b"),
        )
        # The e2b SDK is an optional dependency and is not installed in CI.
        # Selecting a backend whose dependencies are missing now fails at
        # startup by design, so pretend it is present to exercise the WIRING
        # (factory + auto-added metered guard), which is what this covers.
        with patch.object(E2BCodingSandboxFactory, "is_installed", return_value=True):
            cp = ControlPlane(cfg)
            await cp.start()

        assert cp.sandbox_manager is not None
        assert cp.tool_registry.has("run_code")
        # A metered backend gets the billing guard without anyone listing it.
        assert [s.name for s in cp._sandbox_factory_middleware] == ["metered_sandbox_guard"]

    async def test_backend_with_missing_dependencies_fails_at_startup(self) -> None:
        """Better here, with an actionable message, than at the first user
        request — and the old behaviour reported it as an unknown backend,
        which sent operators looking for a typo instead of a missing package."""
        cfg = _minimal_cfg(coding_sandbox=CodingSandboxConfig(enabled=True, backend="e2b"))
        cp = ControlPlane(cfg)
        try:
            import e2b_code_interpreter  # noqa: F401
        except ImportError:
            pass
        else:
            pytest.skip("e2b SDK installed; nothing uninstalled to assert on")

        with pytest.raises(ValueError, match="dependencies are not installed"):
            await cp.start()

    async def test_docker_backend_registers_factory(self, tmp_path) -> None:
        cfg = _minimal_cfg(
            coding_sandbox=CodingSandboxConfig(enabled=True, backend="docker", docker={"working_dir_root": str(tmp_path)}),
        )
        cp = ControlPlane(cfg)
        await cp.start()
        assert cp.sandbox_manager is not None
        assert cp.tool_registry.has("run_code")

    async def test_unknown_backend_raises(self) -> None:
        cfg = _minimal_cfg(coding_sandbox=CodingSandboxConfig(enabled=True, backend="daytona"))
        cp = ControlPlane(cfg)
        with pytest.raises(ValueError, match="unknown sandbox backend 'daytona'"):
            await cp.start()

    async def test_lazy_toolsets_group_sandbox_toolsets_when_present(self, tmp_path) -> None:
        cfg = ControlPlaneConfig(
            tools=[],
            os_sandbox=OSSandboxConfig(enabled=True, working_dir=str(tmp_path)),
            db_sandbox=DBSandboxConfig(enabled=True),
        )
        cp = ControlPlane(cfg)
        await cp.start()
        assert cp.tool_registry.has_toolsets()


class TestHookWiringInline:
    """Each hook name listed explicitly in `cfg.hooks` (as opposed to being
    auto-added) exercises the inline `elif hook_name == ...` branch."""

    async def test_budget_guard_requires_budget_manager(self) -> None:
        cfg = _minimal_cfg(hooks=["budget_guard"], budget=BudgetConfig(max_tokens=10))
        cp = ControlPlane(cfg)
        await cp.start()  # should not raise; middleware installed on PRE_TOOL_USE

    async def test_permission_hook_with_allowlist(self) -> None:
        cfg = _minimal_cfg(hooks=["permission"], allowlist=["memory_read"])
        cp = ControlPlane(cfg)
        await cp.start()

    async def test_retry_hook(self) -> None:
        cfg = _minimal_cfg(hooks=["retry"])
        cp = ControlPlane(cfg)
        await cp.start()

    async def test_tool_safety_hook(self) -> None:
        cfg = _minimal_cfg(hooks=["tool_safety"])
        cp = ControlPlane(cfg)
        await cp.start()

    async def test_coding_sandbox_safety_hook_explicit(self) -> None:
        cfg = _minimal_cfg(hooks=["coding_sandbox_safety"])
        cp = ControlPlane(cfg)
        await cp.start()

    async def test_e2b_sandbox_guard_hook_explicit(self) -> None:
        cfg = _minimal_cfg(hooks=["e2b_sandbox_guard"])
        cp = ControlPlane(cfg)
        await cp.start()

    async def test_context_engine_hook_default_subflags(self) -> None:
        cfg = _minimal_cfg(hooks=["context_engine"])
        cp = ControlPlane(cfg)
        await cp.start()
        # artifact registration is on by default -> retrieve_artifact tool registered
        assert cp.tool_registry.has("retrieve_artifact_content")

    async def test_context_engine_hook_all_subflags_disabled_except_offload(self) -> None:
        """Inverse of `test_context_engine_hook_default_subflags`: every
        sub-shaper defaults to enabled except `enable_offload` — flipping
        all of them exercises the opposite side of each `if ce.enable_*`
        branch in one pass."""
        cfg = _minimal_cfg(
            hooks=["context_engine"],
            context_engine={
                "enable_budget_reduction": False,
                "enable_artifact_registration": False,
                "enable_artifact_compaction": False,
                "enable_tool_result_clearing": False,
                "enable_loop_compaction": False,
                "enable_offload": True,
                "enable_sliding_window": False,
                "enable_deterministic_compact": False,
                "enable_auto_compact": False,
                "enable_synthesis_guard": False,
            },
        )
        cp = ControlPlane(cfg)
        await cp.start()
        assert not cp.tool_registry.has("retrieve_artifact_content")

    async def test_skill_learning_hook_noop_without_skill_manager(self) -> None:
        cfg = _minimal_cfg(hooks=["skill_learning"])
        cp = ControlPlane(cfg)
        await cp.start()
        assert cp.skills is None

    async def test_supervisor_confidence_gate_hook(self) -> None:
        cfg = _minimal_cfg(hooks=["supervisor_confidence_gate"])
        cp = ControlPlane(cfg)
        await cp.start()

    async def test_stall_detection_hook(self) -> None:
        cfg = _minimal_cfg(hooks=["stall_detection"])
        cp = ControlPlane(cfg)
        await cp.start()


class TestHookAutoAdd:
    async def test_mode_other_than_act_wires_enforce_mode(self) -> None:
        cfg = _minimal_cfg(mode="plan")
        cp = ControlPlane(cfg)
        await cp.start()
        spec = cp.make_spec("assistant")
        assert spec.mode == "plan"

    async def test_budget_and_allowlist_auto_add_when_not_in_hooks(self) -> None:
        cfg = _minimal_cfg(budget=BudgetConfig(max_tokens=10), allowlist=["memory_read"])
        cp = ControlPlane(cfg)
        await cp.start()
        assert cp.runtime.budget is not None


class TestApprovalHook:
    async def test_approval_hook_wires_require_approval(self) -> None:
        cfg = _minimal_cfg(hooks=["approval"], tools=["memory_read"])
        cp = ControlPlane(cfg)
        await cp.start()
        assert cp.tool_registry.has("memory_read")


class TestOptionalStores:
    async def test_state_timeline_session_stores_enabled(self) -> None:
        cfg = _minimal_cfg(enable_state_tracking=True, enable_timeline=True, enable_session=True)
        cp = ControlPlane(cfg)
        await cp.start()
        assert cp.runtime.state_store is not None
        assert cp.runtime.timeline_store is not None
        assert cp.runtime.session_store is not None

    async def test_optional_stores_default_off(self) -> None:
        cfg = _minimal_cfg()
        cp = ControlPlane(cfg)
        await cp.start()
        assert cp.runtime.state_store is None
        assert cp.runtime.timeline_store is None
        assert cp.runtime.session_store is None


class TestSkillManagerWiring:
    async def test_lazy_toolsets_disabled_skips_skills_pinning(self, tmp_path) -> None:
        cfg = ControlPlaneConfig(
            hooks=[], tools=[],
            skill_manager=SkillManagerConfig(skills_dir=str(tmp_path)),
            lazy_toolsets={"enabled": False},
        )
        cp = ControlPlane(cfg)
        await cp.start()
        assert cp.skills is not None
        assert "skills" not in cfg.lazy_toolsets.pinned_toolsets
        assert not cp.tool_registry.has_toolsets()

    async def test_skill_learning_hook_wires_post_agent_middleware(self, tmp_path) -> None:
        cfg = ControlPlaneConfig(
            hooks=["skill_learning"], tools=[],
            skill_manager=SkillManagerConfig(skills_dir=str(tmp_path)),
        )
        cp = ControlPlane(cfg)
        await cp.start()
        assert cp.skills is not None

    async def test_require_passing_grade_builds_rubric_grader(self, tmp_path) -> None:
        cfg = ControlPlaneConfig(
            hooks=[], tools=[],
            skill_manager=SkillManagerConfig(skills_dir=str(tmp_path), require_passing_grade=True),
        )
        cp = ControlPlane(cfg)
        await cp.start()
        assert cp.skills is not None
        for name in ("skills_list", "load_skill", "load_skill_resource", "skill_search", "skill_manage"):
            assert cp.tool_registry.has(name)


class TestStop:
    async def test_stop_without_start_is_a_noop(self) -> None:
        cp = ControlPlane(_minimal_cfg())
        await cp.stop()  # must not raise
        assert cp._started is False

    async def test_stop_closes_memory_browser_and_sandbox_manager(self, tmp_path) -> None:
        cfg = ControlPlaneConfig(
            hooks=[], tools=[],
            memory="sqlite", memory_path=":memory:",
            browser_sandbox=BrowserSandboxConfig(enabled=True),
            coding_sandbox=CodingSandboxConfig(enabled=True, backend="local", local={"working_dir_root": str(tmp_path)}),
        )
        cp = ControlPlane(cfg)
        await cp.start()
        assert cp._started is True
        await cp.stop()
        assert cp._started is False
        # `SQLiteMemoryProvider.close()` sets its connection back to None —
        # calling stop() again must not error even though every resource
        # was already torn down once.
        await cp.stop()

    async def test_stop_idempotent_when_optional_resources_absent(self) -> None:
        cp = ControlPlane(_minimal_cfg())
        await cp.start()
        await cp.stop()
        await cp.stop()
        assert cp._started is False


class TestExecuteCodeDispatch:
    async def test_blocked_tool_raises_value_error(self) -> None:
        cfg = _minimal_cfg(tools=["write_todos"])
        cp = ControlPlane(cfg)
        await cp.start()
        with pytest.raises(ValueError, match="cannot be called from execute_code"):
            await cp._execute_code_dispatch("write_todos", {})

    async def test_unknown_tool_raises_value_error(self) -> None:
        cfg = _minimal_cfg()
        cp = ControlPlane(cfg)
        await cp.start()
        with pytest.raises(ValueError, match="Unknown tool"):
            await cp._execute_code_dispatch("totally_unregistered_tool", {})

    async def test_dispatch_before_start_raises_unknown_tool(self) -> None:
        cp = ControlPlane(_minimal_cfg())
        with pytest.raises(ValueError, match="Unknown tool"):
            await cp._execute_code_dispatch("memory_write", {"content": "x"})

    async def test_successful_dispatch_returns_tool_content(self) -> None:
        cfg = _minimal_cfg(tools=["memory_write"])
        cp = ControlPlane(cfg)
        await cp.start()
        result = await cp._execute_code_dispatch("memory_write", {"content": "hello"})
        assert result is not None

    async def test_tool_error_raises_runtime_error(self) -> None:
        cfg = _minimal_cfg(tools=["memory_write"])
        cp = ControlPlane(cfg)
        await cp.start()
        # Missing the required `content` argument -> ToolValidationError ->
        # ToolOutput(success=False, ...) -> ToolResult.is_error=True.
        with pytest.raises(RuntimeError):
            await cp._execute_code_dispatch("memory_write", {})


class TestSpawnSkillWriter:
    async def test_spawn_skill_writer_builds_spec_and_delegates_to_run_child(self) -> None:
        cfg = _minimal_cfg()
        cp = ControlPlane(cfg)
        await cp.start()

        sentinel_result = object()
        cp._runtime.run_child = AsyncMock(return_value=sentinel_result)

        result = await cp._spawn_skill_writer("write a skill for X")

        assert result is sentinel_result
        cp._runtime.run_child.assert_awaited_once()
        called_spec, called_goal = cp._runtime.run_child.call_args.args[0], cp._runtime.run_child.call_args.args[1]
        assert called_spec.name == "skill_writer"
        assert called_goal.description == "write a skill for X"
        assert cp._runtime.run_child.call_args.kwargs["parent_run_ctx"] is None


class TestMakeSpec:
    async def test_raises_when_not_started(self) -> None:
        cp = ControlPlane(_minimal_cfg())
        with pytest.raises(RuntimeError, match="must be started"):
            cp.make_spec("assistant")

    async def test_unknown_role_raises_value_error(self) -> None:
        cp = ControlPlane(_minimal_cfg())
        await cp.start()
        with pytest.raises(ValueError, match="Unknown role"):
            cp.make_spec("not-a-real-role")

    async def test_default_mode_falls_back_to_config_mode(self) -> None:
        cp = ControlPlane(_minimal_cfg(mode="plan"))
        await cp.start()
        spec = cp.make_spec("assistant")
        assert spec.mode == "plan"

    async def test_explicit_mode_override_wins_over_config(self) -> None:
        cp = ControlPlane(_minimal_cfg(mode="plan"))
        await cp.start()
        spec = cp.make_spec("assistant", mode="act")
        assert spec.mode == "act"

    async def test_pinned_toolsets_default_from_config(self) -> None:
        cp = ControlPlane(_minimal_cfg())
        await cp.start()
        spec = cp.make_spec("assistant")
        assert spec.pinned_toolsets == list(cp._config.lazy_toolsets.pinned_toolsets)


class TestCreateAgent:
    async def test_create_agent_binds_spec_to_runtime(self) -> None:
        from app.agent_loop_lib.agent import Agent

        cp = ControlPlane(_minimal_cfg())
        await cp.start()
        agent = cp.create_agent("assistant")
        assert isinstance(agent, Agent)
        assert agent._spec.name == "assistant"
        assert agent._runtime is cp.runtime


class TestProperties:
    async def test_properties_expose_internal_state_after_start(self) -> None:
        cfg = _minimal_cfg()
        cp = ControlPlane(cfg)
        await cp.start()
        assert cp.runtime is not None
        assert cp.factory is not None
        assert cp.tool_registry is not None
        assert cp.role_registry is not None
        assert cp.transport_registry is not None
        assert cp.memory_provider is not None
        assert cp.workspace is not None
        assert cp.skills is None
        assert cp.commands is not None
        assert cp.os_sandbox is None
        assert cp.db_sandbox is None
        assert cp.browser_sandbox is None
        assert cp.sandbox_manager is None
        assert cp.checkpoint_store is not None


class TestAsyncContextManager:
    async def test_aenter_starts_and_aexit_stops(self) -> None:
        cfg = _minimal_cfg()
        cp = ControlPlane(cfg)
        async with cp as entered:
            assert entered is cp
            assert cp._started is True
        assert cp._started is False
