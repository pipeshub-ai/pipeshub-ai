"""Tests for `app.agents.agent_loop.sandbox_bridge` — the composition layer
wiring `agent_loop_lib`'s generic coding sandbox to PipesHub's artifact
pipeline, package allowlist, and host-path redaction."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from app.agent_loop_lib.core.scope import RunScope, ToolScope, TurnScope
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.hooks.middleware.context import (
    AgentLifecycleContext,
    ToolCallContext,
    ToolResultContext,
)
from app.agent_loop_lib.hooks.middleware.decisions import PostDecision, PreDecision
from app.agent_loop_lib.hooks.middleware.routing import path_match
from app.agent_loop_lib.hooks.registry import HookRegistry
from app.agent_loop_lib.sandbox.coding.docker import DockerCodingSandbox
from app.agent_loop_lib.sandbox.coding.local import LocalCodingSandbox
from app.agent_loop_lib.sandbox.coding.base import SandboxContext
from app.agent_loop_lib.sandbox.governor import reset_default_governor
from app.agent_loop_lib.sandbox.manager import SandboxManager, SandboxType, UnknownSandboxError
from app.agent_loop_lib.tools.base import ToolOutput
from app.agent_loop_lib.tools.builtin.sandbox import input_staging
from app.agent_loop_lib.tools.builtin.sandbox.coding_sandbox import CodingSandboxTool
from app.agent_loop_lib.tools.builtin.sandbox.input_staging import peek_staged_input_files
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.sandbox_bridge import (
    CODING_SANDBOX_PATH_PATTERN,
    _capture_code_artifact,
    _code_artifact_token,
    _partition_sandbox_outputs,
    build_coding_sandbox_manager,
    coding_sandbox_artifact_bridge,
    coding_sandbox_artifact_staging,
    coding_sandbox_package_policy,
    coding_sandbox_result_propagation,
    register_coding_sandbox_hooks,
    register_coding_sandbox_tools,
    sandbox_network_enabled,
)
from app.models.entities import ArtifactType, LifecycleStatus
from app.services.artifact_registry import ArtifactMetadata
from app.services.artifact_registry.access import ArtifactNotFoundError


def _make_metadata(**overrides: Any) -> ArtifactMetadata:
    defaults: dict[str, Any] = {
        "artifact_id": "rec-1",
        "org_id": "org-1",
        "conversation_id": "conv-1",
        "name": "report.pdf",
        "logical_name": "report.pdf",
        "artifact_type": ArtifactType.OTHER,
        "mime_type": "application/octet-stream",
        "lifecycle_status": LifecycleStatus.PUBLISHED,
        "version": 1,
        "size_bytes": 9,
        "document_id": "doc-1",
    }
    defaults.update(overrides)
    return ArtifactMetadata(**defaults)


def _make_mock_registry(**method_overrides: Any) -> MagicMock:
    """A stand-in for `ArtifactRegistryService` exposing exactly the async
    surface `_register_run_code_artifacts` calls. Bound onto `context` via
    the `AgentContext.artifact_registry` class property (monkeypatched per
    test) rather than instance assignment — it's a read-only `@property`
    on a Pydantic model, so plain attribute assignment would fail."""
    registry = MagicMock()
    registry.register_output = AsyncMock(
        return_value=(method_overrides.pop("metadata", _make_metadata()), 1)
    )
    registry.get_download_url = AsyncMock(return_value=method_overrides.pop("download_url", "https://blob.example/report.pdf"))
    registry.record_derivation = AsyncMock()
    for name, value in method_overrides.items():
        setattr(registry, name, value)
    return registry


async def _noop_next() -> None:
    return None


def _make_context(**overrides: Any) -> AgentContext:
    defaults: dict[str, Any] = {
        "org_id": "org-1",
        "user_id": "user-1",
        "user_email": "u@example.com",
        "logger": MagicMock(),
        "retrieval_service": MagicMock(config_service=MagicMock()),
        "conversation_id": "conv-1",
    }
    defaults.update(overrides)
    return AgentContext(**defaults)


class TestSandboxNetworkEnabled:
    def test_defaults_to_enabled_when_unset(self, monkeypatch) -> None:
        monkeypatch.delenv("SANDBOX_ALLOW_NETWORK", raising=False)
        assert sandbox_network_enabled() is True

    @pytest.mark.parametrize("value", ["false", "False", "0", "no", "off"])
    def test_falsy_values_disable_network(self, monkeypatch, value: str) -> None:
        monkeypatch.setenv("SANDBOX_ALLOW_NETWORK", value)
        assert sandbox_network_enabled() is False

    @pytest.mark.parametrize("value", ["true", "1", "yes", "on"])
    def test_truthy_values_enable_network(self, monkeypatch, value: str) -> None:
        monkeypatch.setenv("SANDBOX_ALLOW_NETWORK", value)
        assert sandbox_network_enabled() is True


class TestBuildCodingSandboxManager:
    """`build_coding_sandbox_manager` goes through the settings loader and
    backend registry, so these assert on the backend the factory actually
    builds rather than on a patched constructor — a mock would pass even if
    the config never reached the real backend, which is precisely the bug
    this path had."""

    @pytest.fixture(autouse=True)
    def _isolated_governor(self):
        reset_default_governor()
        yield
        reset_default_governor()

    async def _backend(self, **kwargs):
        manager = await build_coding_sandbox_manager(**kwargs)
        entry = manager._factories[SandboxType.CODING]
        return manager, entry.backend_factory.create(SandboxContext())

    async def test_local_mode_registers_local_backend_with_curated_allowlist(
        self, monkeypatch,
    ) -> None:
        monkeypatch.delenv("SANDBOX_MODE", raising=False)
        _, backend = await self._backend()
        assert isinstance(backend, LocalCodingSandbox)
        # The local backend delegates package policy to its EnvironmentManager.
        assert "pandas" in backend._environment._allowlist
        assert "sharp" in backend._environment._allowlist

    async def test_docker_mode_registers_docker_backend_with_env_config(
        self, monkeypatch,
    ) -> None:
        monkeypatch.setenv("SANDBOX_MODE", "docker")
        monkeypatch.setenv("SANDBOX_DOCKER_IMAGE", "custom/sandbox:v2")
        monkeypatch.setenv("SANDBOX_EGRESS_NETWORK", "custom_egress")
        monkeypatch.setenv("SANDBOX_PIP_INDEX_URL", "https://pip.example.com/simple")
        monkeypatch.setenv("SANDBOX_NPM_REGISTRY", "https://npm.example.com")

        _, backend = await self._backend()
        assert isinstance(backend, DockerCodingSandbox)
        assert backend._image == "custom/sandbox:v2"
        assert backend._egress_network == "custom_egress"
        assert backend._pip_index_url == "https://pip.example.com/simple"
        assert backend._npm_registry == "https://npm.example.com"
        assert "pandas" in backend._allowlist

    async def test_docker_mode_uses_pipeshub_defaults(self, monkeypatch) -> None:
        monkeypatch.setenv("SANDBOX_MODE", "docker")
        for var in (
            "SANDBOX_DOCKER_IMAGE", "SANDBOX_EGRESS_NETWORK",
            "SANDBOX_PIP_INDEX_URL", "SANDBOX_NPM_REGISTRY",
        ):
            monkeypatch.delenv(var, raising=False)

        _, backend = await self._backend()
        assert backend._image == "pipeshub/sandbox:latest"
        assert backend._egress_network == "pipeshub_sandbox_egress"
        # Baked into the image — without it every sandbox re-installs the
        # common packages.
        assert backend._image_node_modules == "/home/sandbox/node_modules"

    async def test_limits_are_applied_to_registered_factory(self, monkeypatch) -> None:
        monkeypatch.delenv("SANDBOX_MODE", raising=False)
        manager = await build_coding_sandbox_manager(max_concurrent=3, max_lifetime_s=60.0)
        entry = manager._factories[SandboxType.CODING]
        assert entry.limits.max_concurrent == 3
        assert entry.limits.max_lifetime_s == 60.0

    async def test_local_factory_produces_real_local_sandbox_instance(
        self, monkeypatch,
    ) -> None:
        monkeypatch.delenv("SANDBOX_MODE", raising=False)
        manager = await build_coding_sandbox_manager()
        _, backend = await manager.get_or_create(SandboxType.CODING)
        assert isinstance(backend, LocalCodingSandbox)
        await manager.destroy_all()

    @pytest.mark.parametrize("allow_network", [True, False])
    async def test_docker_backend_receives_resolved_allow_network_flag(
        self, monkeypatch, allow_network: bool,
    ) -> None:
        monkeypatch.setenv("SANDBOX_MODE", "docker")
        _, backend = await self._backend(allow_network=allow_network)
        assert backend._allow_network is allow_network

    async def test_docker_backend_falls_back_to_env_flag_when_omitted(
        self, monkeypatch,
    ) -> None:
        monkeypatch.setenv("SANDBOX_MODE", "docker")
        monkeypatch.setenv("SANDBOX_ALLOW_NETWORK", "false")
        _, backend = await self._backend()
        assert backend._allow_network is False

    async def test_agent_context_ids_reach_the_backend(self, monkeypatch) -> None:
        """The governor's per-org cap and remote provider tagging both
        depend on the org reaching the sandbox — the tools call
        `get_or_create` with no context of their own."""
        monkeypatch.delenv("SANDBOX_MODE", raising=False)
        manager = await build_coding_sandbox_manager(ctx=_make_context())
        entry = manager._factories[SandboxType.CODING]
        assert entry.default_ctx.org_id == "org-1"
        assert entry.default_ctx.user_id == "user-1"
        assert entry.default_ctx.conversation_id == "conv-1"

    async def test_managers_from_separate_requests_share_one_governor(
        self, monkeypatch,
    ) -> None:
        """Per-request managers, one process-wide ceiling — otherwise N
        concurrent chats each get their own quota and nothing is capped."""
        monkeypatch.delenv("SANDBOX_MODE", raising=False)
        first = await build_coding_sandbox_manager()
        second = await build_coding_sandbox_manager()
        assert first._governor is second._governor

    async def test_unknown_backend_raises_instead_of_falling_back(
        self, monkeypatch,
    ) -> None:
        """A backend that can't be resolved must fail loudly at wiring
        time; silently substituting an ungoverned manager would defer the
        failure into the middle of a conversation."""
        monkeypatch.delenv("SANDBOX_MODE", raising=False)
        with patch(
            "app.agents.agent_loop.sandbox_bridge.build_default_registry"
        ) as mock_registry:
            mock_registry.return_value.get.side_effect = ValueError(
                "unknown sandbox backend 'nope'"
            )
            with pytest.raises(ValueError, match="unknown sandbox backend"):
                await build_coding_sandbox_manager()


class TestRegisterCodingSandboxTools:
    def test_registers_all_three_tools(self) -> None:
        registry = ToolRegistry()
        manager = SandboxManager()

        register_coding_sandbox_tools(registry, manager)

        assert set(registry.names()) == {"run_code", "install_packages", "read_sandbox_file"}
        assert registry.has_path("/toolsets/coding_sandbox/run_code")
        assert registry.has_path("/toolsets/coding_sandbox/install_packages")
        assert registry.has_path("/toolsets/coding_sandbox/read_sandbox_file")

    def test_run_code_tool_receives_resolved_allow_network_flag(self) -> None:
        registry = ToolRegistry()
        manager = SandboxManager()

        register_coding_sandbox_tools(registry, manager, allow_network=True)

        run_code_tool = registry.resolve("/toolsets/coding_sandbox/run_code")
        assert isinstance(run_code_tool, CodingSandboxTool)
        assert run_code_tool._allow_network is True

    def test_run_code_tool_falls_back_to_env_flag_when_allow_network_omitted(self, monkeypatch) -> None:
        monkeypatch.setenv("SANDBOX_ALLOW_NETWORK", "false")
        registry = ToolRegistry()
        manager = SandboxManager()

        register_coding_sandbox_tools(registry, manager)

        run_code_tool = registry.resolve("/toolsets/coding_sandbox/run_code")
        assert run_code_tool._allow_network is False


class TestCodingSandboxPathPattern:
    @pytest.mark.parametrize(
        "path",
        [
            "/toolsets/coding_sandbox/run_code",
            "/toolsets/coding_sandbox/install_packages",
            "/toolsets/coding_sandbox/read_sandbox_file",
        ],
    )
    def test_pattern_matches_coding_sandbox_tools(self, path: str) -> None:
        assert path_match(path, CODING_SANDBOX_PATH_PATTERN)

    @pytest.mark.parametrize(
        "path",
        ["/toolsets/jira/create_issue", "/toolsets/database_sandbox/run_query"],
    )
    def test_pattern_does_not_match_other_toolsets(self, path: str) -> None:
        assert not path_match(path, CODING_SANDBOX_PATH_PATTERN)


class TestCodingSandboxPackagePolicy:
    async def test_no_packages_passes_through(self) -> None:
        ctx = ToolCallContext(tool_path="/toolsets/coding_sandbox/run_code", tool_input={"code": "1"})
        await coding_sandbox_package_policy()(ctx, _noop_next)
        assert ctx.decision == PreDecision.ALLOW

    async def test_allowed_package_passes_through(self) -> None:
        ctx = ToolCallContext(
            tool_path="/toolsets/coding_sandbox/install_packages",
            tool_input={"packages": ["pandas"], "language": "python"},
        )
        await coding_sandbox_package_policy()(ctx, _noop_next)
        assert ctx.decision == PreDecision.ALLOW

    async def test_disallowed_package_denied_with_structured_reason(self) -> None:
        ctx = ToolCallContext(
            tool_path="/toolsets/coding_sandbox/install_packages",
            tool_input={"packages": ["some-random-unlisted-pkg"], "language": "python"},
        )
        await coding_sandbox_package_policy()(ctx, _noop_next)

        assert ctx.decision == PreDecision.DENY
        assert "some-random-unlisted-pkg" in ctx.decision_reason
        assert ctx.metadata["rejected_package"] == "some-random-unlisted-pkg"
        assert "pandas" in ctx.metadata["allowed_packages"]

    async def test_denial_reason_clarifies_no_package_grants_network_access(self) -> None:
        """Without this, the model reads a plain "package not allowed" denial
        and reasonably tries a DIFFERENT network library next — every retry
        is doomed since the sandbox has no network at all, regardless of
        package (see `docker.py`'s `network_mode="none"`)."""
        ctx = ToolCallContext(
            tool_path="/toolsets/coding_sandbox/run_code",
            tool_input={"packages": ["requests"], "language": "python"},
        )
        await coding_sandbox_package_policy()(ctx, _noop_next)

        assert ctx.decision == PreDecision.DENY
        assert "no package can give this sandbox network access" in ctx.decision_reason
        assert "web_search" in ctx.decision_reason

    async def test_denial_reason_notes_network_access_when_allowed(self) -> None:
        """Once the sandbox has network access, "no package can give this
        sandbox network access" would be actively wrong — the deny message
        must reflect that network is on but the allowlist still applies."""
        ctx = ToolCallContext(
            tool_path="/toolsets/coding_sandbox/run_code",
            tool_input={"packages": ["requests"], "language": "python"},
        )
        await coding_sandbox_package_policy(allow_network=True)(ctx, _noop_next)

        assert ctx.decision == PreDecision.DENY
        assert "no package can give this sandbox network access" not in ctx.decision_reason
        assert "this sandbox has network access" in ctx.decision_reason

    async def test_unmapped_language_skips_enforcement(self) -> None:
        ctx = ToolCallContext(
            tool_path="/toolsets/database_sandbox/run_query",
            tool_input={"packages": ["whatever"], "language": "sqlite"},
        )
        await coding_sandbox_package_policy()(ctx, _noop_next)
        assert ctx.decision == PreDecision.ALLOW

    async def test_defaults_to_typescript_when_language_missing(self) -> None:
        ctx = ToolCallContext(
            tool_path="/toolsets/coding_sandbox/install_packages",
            tool_input={"packages": ["left-pad"]},
        )
        await coding_sandbox_package_policy()(ctx, _noop_next)
        assert ctx.decision == PreDecision.DENY


class TestCodingSandboxArtifactStaging:
    """Pins the fix for the PRE_TOOL_USE contextvar bug (see
    `set_staged_input_files_for_task`'s docstring): `ToolExecutor.call_tool()`
    runs the WHOLE PRE_TOOL_USE pipeline to completion before ever calling
    `tool.execute()`, so staged `input_artifacts` bytes must still be
    visible via `peek_staged_input_files()` AFTER this middleware's
    `_middleware(ctx, next_fn)` returns — not just while `next_fn()` is
    still running."""

    @pytest.fixture(autouse=True)
    def _reset_staged_input_files(self):
        token = input_staging._staged_input_files.set(None)
        yield
        input_staging._staged_input_files.reset(token)

    def _make_registry(self, files: dict[str, bytes]) -> MagicMock:
        """Async `resolve`/`get_content` stand-in keyed by logical name —
        `files` maps a ref (as the model would pass it) to its content."""
        registry = MagicMock()

        async def _resolve(*, actor: Any, ref: str, conversation_id: str | None) -> ArtifactMetadata:
            if ref not in files:
                raise ArtifactNotFoundError(f"No artifact named {ref!r} in this conversation")
            return _make_metadata(artifact_id=f"art-{ref}", name=ref, logical_name=ref)

        async def _get_content(*, actor: Any, artifact_id: str) -> bytes:
            return files[artifact_id[len("art-"):]]

        registry.resolve = AsyncMock(side_effect=_resolve)
        registry.get_content = AsyncMock(side_effect=_get_content)
        return registry

    async def test_staged_files_survive_past_middleware_return(self, monkeypatch) -> None:
        context = _make_context()
        registry = self._make_registry({"chart.png": b"pngbytes"})
        monkeypatch.setattr(AgentContext, "artifact_registry", property(lambda self: registry))
        ctx = ToolCallContext(
            tool_path="/toolsets/coding_sandbox/run_code",
            tool_input={"code": "", "input_artifacts": ["chart.png"]},
        )

        await coding_sandbox_artifact_staging(context)(ctx, _noop_next)

        # The regression: a `with stage_input_files(...): await next_fn()`
        # implementation would have already reset this back to `None` by
        # the time this assertion runs, exactly where `tool.execute()`
        # would read it in the real `ToolExecutor.call_tool()` sequencing.
        assert peek_staged_input_files() == {"input/artifacts/chart.png": b"pngbytes"}

    async def test_missing_ref_reported_and_nothing_staged(self, monkeypatch) -> None:
        context = _make_context()
        registry = self._make_registry({})
        monkeypatch.setattr(AgentContext, "artifact_registry", property(lambda self: registry))
        ctx = ToolCallContext(
            tool_path="/toolsets/coding_sandbox/run_code",
            tool_input={"code": "", "input_artifacts": ["does_not_exist.png"]},
        )

        await coding_sandbox_artifact_staging(context)(ctx, _noop_next)

        assert ctx.metadata["input_artifacts_not_found"] == ["does_not_exist.png"]
        assert "staged_input_artifacts" not in ctx.metadata
        assert not peek_staged_input_files()

    async def test_no_input_artifacts_leaves_nothing_staged(self, monkeypatch) -> None:
        context = _make_context()
        registry = self._make_registry({})
        monkeypatch.setattr(AgentContext, "artifact_registry", property(lambda self: registry))
        ctx = ToolCallContext(
            tool_path="/toolsets/coding_sandbox/run_code",
            tool_input={"code": "print(1)"},
        )

        await coding_sandbox_artifact_staging(context)(ctx, _noop_next)

        assert peek_staged_input_files() is None

    async def test_parallel_tool_calls_do_not_see_each_others_staged_files(self, monkeypatch) -> None:
        """Mirrors the real `asyncio.gather`-per-tool-call isolation the
        turn loop relies on (see `set_staged_input_files_for_task`'s
        docstring): two calls staging different refs, each in its OWN
        task, must never observe the other's files."""
        context = _make_context()
        registry = self._make_registry({"a.png": b"a-bytes", "b.png": b"b-bytes"})
        monkeypatch.setattr(AgentContext, "artifact_registry", property(lambda self: registry))

        seen: dict[str, dict[str, bytes] | None] = {}

        async def _run_call(ref: str) -> None:
            ctx = ToolCallContext(
                tool_path="/toolsets/coding_sandbox/run_code",
                tool_input={"code": "", "input_artifacts": [ref]},
            )
            await coding_sandbox_artifact_staging(context)(ctx, _noop_next)
            seen[ref] = peek_staged_input_files()

        await asyncio.gather(_run_call("a.png"), _run_call("b.png"))

        assert seen["a.png"] == {"input/artifacts/a.png": b"a-bytes"}
        assert seen["b.png"] == {"input/artifacts/b.png": b"b-bytes"}


class TestCodingSandboxArtifactBridge:
    async def test_redacts_stdout_stderr_and_error_analysis(self) -> None:
        context = _make_context()
        manager = SandboxManager()
        ctx = ToolResultContext(
            tool_path="/toolsets/coding_sandbox/run_code",
            tool_use_id=__import__("uuid").uuid4(),
            tool_response=ToolOutput(
                success=True,
                data={
                    "stdout": "Saved at: /tmp/pipeshub_sandbox/abc-1/output/report.pdf",
                    "stderr": "warn: /tmp/pipeshub_sandbox/abc-1/script.py",
                    "error_analysis": {
                        "message": "at /tmp/pipeshub_sandbox/abc-1/output/x.pdf",
                        "stack_trace": None,
                        "suggestion": None,
                    },
                },
            ),
        )

        await coding_sandbox_artifact_bridge(context, manager)(ctx, _noop_next)

        assert ctx.tool_response.data["stdout"] == "Saved at: <output>/report.pdf"
        assert ctx.tool_response.data["stderr"] == "warn: <workdir>/script.py"
        assert ctx.tool_response.data["error_analysis"]["message"] == "at <output>/x.pdf"

    async def test_no_artifacts_key_skips_upload_entirely(self) -> None:
        context = _make_context()
        manager = SandboxManager()
        ctx = ToolResultContext(
            tool_path="/toolsets/coding_sandbox/run_code",
            tool_use_id=__import__("uuid").uuid4(),
            tool_response=ToolOutput(success=True, data={"stdout": "hi"}),
        )
        with patch("app.agents.agent_loop.sandbox_bridge.register_task") as mock_register:
            await coding_sandbox_artifact_bridge(context, manager)(ctx, _noop_next)
        mock_register.assert_not_called()

    async def test_missing_blob_store_skips_upload(self) -> None:
        context = _make_context(blob_store=None)
        manager = SandboxManager()
        manager.register_backend_factory(SandboxType.CODING, lambda: _FakeBackend({}))
        sandbox_id, _ = await manager.get_or_create(SandboxType.CODING)

        ctx = ToolResultContext(
            tool_path="/toolsets/coding_sandbox/run_code",
            tool_use_id=__import__("uuid").uuid4(),
            tool_response=ToolOutput(
                success=True,
                data={"artifacts": ["output/x.pdf"], "sandbox_id": sandbox_id},
            ),
        )
        with patch("app.agents.agent_loop.sandbox_bridge.register_task") as mock_register:
            await coding_sandbox_artifact_bridge(context, manager)(ctx, _noop_next)
        mock_register.assert_not_called()

    async def test_unknown_sandbox_id_skips_upload_without_raising(self) -> None:
        context = _make_context(blob_store=AsyncMock())
        manager = SandboxManager()
        ctx = ToolResultContext(
            tool_path="/toolsets/coding_sandbox/run_code",
            tool_use_id=__import__("uuid").uuid4(),
            tool_response=ToolOutput(
                success=True,
                data={"artifacts": ["output/x.pdf"], "sandbox_id": "does-not-exist"},
            ),
        )
        with patch("app.agents.agent_loop.sandbox_bridge.register_task") as mock_register:
            await coding_sandbox_artifact_bridge(context, manager)(ctx, _noop_next)
        mock_register.assert_not_called()

    async def test_fetches_bytes_inline_and_registers_synchronously(self, monkeypatch) -> None:
        """Registration through `ArtifactRegistryService` must complete
        BEFORE this hook returns — `artifact_id`/`version` land in the
        tool response the model sees THIS turn (the point of the
        synchronous-registration rewrite), not in a later background task."""
        blob_store = AsyncMock()
        graph_provider = MagicMock()
        context = _make_context(blob_store=blob_store, graph_provider=graph_provider)
        registry = _make_mock_registry()
        monkeypatch.setattr(AgentContext, "artifact_registry", property(lambda self: registry))
        manager = SandboxManager()
        manager.register_backend_factory(
            SandboxType.CODING,
            lambda: _FakeBackend({"output/report.pdf": b"pdf-bytes"}),
        )
        sandbox_id, _ = await manager.get_or_create(SandboxType.CODING)

        ctx = ToolResultContext(
            tool_path="/toolsets/coding_sandbox/run_code",
            tool_use_id=__import__("uuid").uuid4(),
            tool_response=ToolOutput(
                success=True,
                data={"artifacts": ["output/report.pdf"], "sandbox_id": sandbox_id},
            ),
        )

        scheduled: list[asyncio.Task] = []
        with patch(
            "app.agents.agent_loop.sandbox_bridge.register_task",
            side_effect=lambda cid, task: scheduled.append(task),
        ):
            await coding_sandbox_artifact_bridge(context, manager)(ctx, _noop_next)

        registry.register_output.assert_awaited_once()
        _, kwargs = registry.register_output.call_args
        assert kwargs["name"] == "report.pdf"
        assert kwargs["content"] == b"pdf-bytes"
        assert kwargs["conversation_id"] == "conv-1"

        # Model-visible compact block — already present synchronously,
        # not deferred to the background task below.
        assert ctx.tool_response.data["artifacts"] == [
            {
                "artifact_id": "rec-1",
                "name": "report.pdf",
                "version": 1,
                "mime_type": "application/octet-stream",
                "size_bytes": 9,
                "artifact_type": "OTHER",
                "visibility": "VISIBLE",
            }
        ]
        assert context.artifacts_registered_this_run == [
            _make_metadata().model_dump()
        ]

        # `::artifact` marker delivery still runs off the (now trivially
        # resolved) background-task pipeline for backward compatibility.
        assert len(scheduled) == 1
        task_result = await scheduled[0]
        assert task_result == {
            "type": "artifacts",
            "artifacts": [{
                "documentId": "doc-1",
                "fileName": "report.pdf",
                "mimeType": "application/octet-stream",
                "sizeBytes": 9,
                "recordId": "rec-1",
                "downloadUrl": "https://blob.example/report.pdf",
                "artifactType": "OTHER",
                "version": 1,
            }],
        }

    async def test_rerun_of_same_artifact_version_is_not_redelivered(self, monkeypatch) -> None:
        """A model that re-runs the same code re-registers byte-identical
        content — content-hash dedup keeps (artifact_id, version) the same,
        and the delivery pipeline must NOT attach a second download card
        (SSE event / `::artifact` marker) for it. The model still sees the
        artifact in its tool response, flagged `already_delivered`."""
        blob_store = AsyncMock()
        event_sink = AsyncMock()
        context = _make_context(blob_store=blob_store, event_sink=event_sink)
        registry = _make_mock_registry()
        monkeypatch.setattr(AgentContext, "artifact_registry", property(lambda self: registry))
        manager = SandboxManager()
        manager.register_backend_factory(
            SandboxType.CODING,
            lambda: _FakeBackend({"output/report.pdf": b"pdf-bytes"}),
        )
        sandbox_id, _ = await manager.get_or_create(SandboxType.CODING)

        def _make_ctx() -> ToolResultContext:
            return ToolResultContext(
                tool_path="/toolsets/coding_sandbox/run_code",
                tool_use_id=__import__("uuid").uuid4(),
                tool_response=ToolOutput(
                    success=True,
                    data={"artifacts": ["output/report.pdf"], "sandbox_id": sandbox_id},
                ),
            )

        scheduled: list[asyncio.Task] = []
        with patch(
            "app.agents.agent_loop.sandbox_bridge.register_task",
            side_effect=lambda cid, task: scheduled.append(task),
        ):
            first = _make_ctx()
            await coding_sandbox_artifact_bridge(context, manager)(first, _noop_next)
            second = _make_ctx()
            await coding_sandbox_artifact_bridge(context, manager)(second, _noop_next)
            for task in scheduled:
                await task

        # One SSE event, one legacy marker task, one registered entry —
        # not two of each.
        event_sink.write.assert_awaited_once()
        assert len(scheduled) == 1
        assert len(context.artifacts_registered_this_run) == 1

        # First delivery: plain compact block. Re-run: flagged so the model
        # knows the file is already attached and must not regenerate it.
        assert "already_delivered" not in first.tool_response.data["artifacts"][0]
        assert second.tool_response.data["artifacts"][0]["already_delivered"] is True
        assert second.tool_response.data["artifacts"][0]["artifact_id"] == "rec-1"

    async def test_no_task_scheduled_when_every_registration_fails(self, monkeypatch) -> None:
        """When every artifact fails to register, nothing gets appended to
        `data["artifacts"]` and no legacy marker task is ever created —
        there is nothing for it to report."""
        blob_store = AsyncMock()
        context = _make_context(blob_store=blob_store)
        registry = _make_mock_registry()
        registry.register_output = AsyncMock(side_effect=RuntimeError("permission denied"))
        monkeypatch.setattr(AgentContext, "artifact_registry", property(lambda self: registry))
        manager = SandboxManager()
        manager.register_backend_factory(
            SandboxType.CODING,
            lambda: _FakeBackend({"output/report.pdf": b"pdf-bytes"}),
        )
        sandbox_id, _ = await manager.get_or_create(SandboxType.CODING)

        ctx = ToolResultContext(
            tool_path="/toolsets/coding_sandbox/run_code",
            tool_use_id=__import__("uuid").uuid4(),
            tool_response=ToolOutput(
                success=True,
                data={"artifacts": ["output/report.pdf"], "sandbox_id": sandbox_id},
            ),
        )

        with patch("app.agents.agent_loop.sandbox_bridge.register_task") as mock_register:
            await coding_sandbox_artifact_bridge(context, manager)(ctx, _noop_next)

        mock_register.assert_not_called()
        assert context.artifacts_registered_this_run == []
        # The model must be able to tell "the save failed" from "no file was
        # produced" — otherwise it announces a download that does not exist.
        assert ctx.tool_response.data["artifacts"] == []
        assert ctx.tool_response.data["artifact_errors"] == [
            {"file": "report.pdf", "error": "RuntimeError: permission denied"}
        ]
        assert "artifact_errors_note" in ctx.tool_response.data

    async def test_emits_live_sse_artifact_event_when_event_sink_present(self, monkeypatch) -> None:
        blob_store = AsyncMock()
        event_sink = AsyncMock()
        context = _make_context(blob_store=blob_store, event_sink=event_sink)
        metadata = _make_metadata(
            artifact_id="rec-1", name="chart.png", mime_type="image/png",
            artifact_type=ArtifactType.IMAGE, size_bytes=9, document_id="doc-1",
        )
        registry = _make_mock_registry(metadata=metadata, download_url="https://blob.example/chart.png")
        monkeypatch.setattr(AgentContext, "artifact_registry", property(lambda self: registry))
        manager = SandboxManager()
        manager.register_backend_factory(
            SandboxType.CODING,
            lambda: _FakeBackend({"output/chart.png": b"png-bytes"}),
        )
        sandbox_id, _ = await manager.get_or_create(SandboxType.CODING)

        ctx = ToolResultContext(
            tool_path="/toolsets/coding_sandbox/run_code",
            tool_use_id=__import__("uuid").uuid4(),
            tool_response=ToolOutput(
                success=True,
                data={"artifacts": ["output/chart.png"], "sandbox_id": sandbox_id},
            ),
        )

        scheduled: list[asyncio.Task] = []
        with patch(
            "app.agents.agent_loop.sandbox_bridge.register_task",
            side_effect=lambda cid, task: scheduled.append(task),
        ):
            await coding_sandbox_artifact_bridge(context, manager)(ctx, _noop_next)
            await scheduled[0]

        event_sink.write.assert_awaited_once()
        (event,), _ = event_sink.write.call_args
        assert event["event"] == "artifact"
        assert event["data"]["fileName"] == "chart.png"
        assert event["data"]["downloadUrl"] == "https://blob.example/chart.png"
        assert event["data"]["mimeType"] == "image/png"
        assert event["data"]["sizeBytes"] == 9
        assert event["data"]["recordId"] == "rec-1"
        assert event["data"]["artifactType"] == "IMAGE"

    async def test_no_sse_event_emitted_without_event_sink(self, monkeypatch) -> None:
        blob_store = AsyncMock()
        context = _make_context(blob_store=blob_store)  # event_sink defaults to None
        registry = _make_mock_registry()
        monkeypatch.setattr(AgentContext, "artifact_registry", property(lambda self: registry))
        manager = SandboxManager()
        manager.register_backend_factory(
            SandboxType.CODING,
            lambda: _FakeBackend({"output/report.pdf": b"pdf-bytes"}),
        )
        sandbox_id, _ = await manager.get_or_create(SandboxType.CODING)

        ctx = ToolResultContext(
            tool_path="/toolsets/coding_sandbox/run_code",
            tool_use_id=__import__("uuid").uuid4(),
            tool_response=ToolOutput(
                success=True,
                data={"artifacts": ["output/report.pdf"], "sandbox_id": sandbox_id},
            ),
        )

        scheduled: list[asyncio.Task] = []
        with patch(
            "app.agents.agent_loop.sandbox_bridge.register_task",
            side_effect=lambda cid, task: scheduled.append(task),
        ):
            # Must not raise even though context.event_sink is None.
            await coding_sandbox_artifact_bridge(context, manager)(ctx, _noop_next)
            task_result = await scheduled[0]

        assert task_result == {
            "type": "artifacts",
            "artifacts": [{
                "documentId": "doc-1",
                "fileName": "report.pdf",
                "mimeType": "application/octet-stream",
                "sizeBytes": 9,
                "recordId": "rec-1",
                "downloadUrl": "https://blob.example/report.pdf",
                "artifactType": "OTHER",
                "version": 1,
            }],
        }

    async def test_download_failure_for_one_artifact_does_not_block_others(self, monkeypatch) -> None:
        blob_store = AsyncMock()
        context = _make_context(blob_store=blob_store)
        good_metadata = _make_metadata(artifact_id="rec-good", name="good.pdf")
        registry = _make_mock_registry(metadata=good_metadata)
        monkeypatch.setattr(AgentContext, "artifact_registry", property(lambda self: registry))
        manager = SandboxManager()

        class _PartiallyBrokenBackend(_FakeBackend):
            def __init__(self) -> None:
                super().__init__({})

            async def download_file(self, path: str) -> bytes:
                if path == "output/bad.pdf":
                    raise OSError("disk read error")
                return b"good-bytes"

        manager.register_backend_factory(SandboxType.CODING, _PartiallyBrokenBackend)
        sandbox_id, _ = await manager.get_or_create(SandboxType.CODING)

        ctx = ToolResultContext(
            tool_path="/toolsets/coding_sandbox/run_code",
            tool_use_id=__import__("uuid").uuid4(),
            tool_response=ToolOutput(
                success=True,
                data={
                    "artifacts": ["output/bad.pdf", "output/good.pdf"],
                    "sandbox_id": sandbox_id,
                },
            ),
        )

        scheduled: list[asyncio.Task] = []
        with patch(
            "app.agents.agent_loop.sandbox_bridge.register_task",
            side_effect=lambda cid, task: scheduled.append(task),
        ):
            await coding_sandbox_artifact_bridge(context, manager)(ctx, _noop_next)
            await scheduled[0]

        registry.register_output.assert_awaited_once()
        _, kwargs = registry.register_output.call_args
        assert kwargs["name"] == "good.pdf"
        assert kwargs["content"] == b"good-bytes"
        assert ctx.tool_response.data["artifacts"] == [good_metadata.to_tool_response()]


class TestCodeArtifactNaming:
    """The captured program's name must not read as a live sandbox handle.

    A sandbox only exists for the turn that created it, so a name built from
    `sandbox_id` works if the model tries it inside that turn and dies in
    every later one — which is how a model ends up calling read_sandbox_file
    with an id it read off a filename.
    """

    def _capture(self, *, sandbox_id: str | None, language: str = "python") -> tuple[str, MagicMock]:
        registry = MagicMock()
        registry.register_output = AsyncMock(return_value=(_make_metadata(), None))
        tool_input: dict[str, Any] = {"code": "print(1)", "language": language}
        if sandbox_id is not None:
            tool_input["sandbox_id"] = sandbox_id
        ctx = ToolCallContext(
            tool_path="/toolsets/coding_sandbox/run_code", tool_input=tool_input,
        )
        asyncio.run(_capture_code_artifact(_make_context(), registry, ctx))
        return registry.register_output.call_args.kwargs["name"], registry

    def test_name_never_reuses_the_sandbox_id(self) -> None:
        sandbox_id = "f0077f48-032c-4c93-a10b-c746dbacbd17"

        name, _ = self._capture(sandbox_id=sandbox_id)

        assert sandbox_id not in name
        assert sandbox_id.replace("-", "") not in name

    @pytest.mark.parametrize("sandbox_id", [None, "f0077f48-032c-4c93-a10b-c746dbacbd17"])
    def test_name_is_not_uuid_shaped(self, sandbox_id) -> None:
        name, _ = self._capture(sandbox_id=sandbox_id)

        token = name.removeprefix("code_").removesuffix(".py")
        assert "-" not in token
        assert len(token) == 12
        with pytest.raises(ValueError):
            UUID(token)

    def test_each_call_gets_its_own_artifact_rather_than_a_version_bump(self) -> None:
        """`register_output` matches on logical name, so two runs sharing a
        name would bump one artifact instead of recording two programs."""
        first, _ = self._capture(sandbox_id=None)
        second, _ = self._capture(sandbox_id=None)

        assert first != second

    @pytest.mark.parametrize(("language", "ext"), [("python", "py"), ("typescript", "ts")])
    def test_extension_still_follows_the_language(self, language: str, ext: str) -> None:
        name, _ = self._capture(sandbox_id=None, language=language)

        assert name.endswith(f".{ext}")

    def test_token_falls_back_when_the_tool_use_id_carries_no_usable_characters(self) -> None:
        assert len(_code_artifact_token("---")) == 12


class TestPartitionSandboxOutputs:
    """`_partition_sandbox_outputs` — the $OUTPUT_DIR contract's single
    source of truth for what is a deliverable vs. scratch."""

    def test_output_dir_path_is_deliverable(self) -> None:
        deliverables, scratch = _partition_sandbox_outputs(["output/report.pdf"])
        assert deliverables == ["output/report.pdf"]
        assert scratch == []

    def test_non_output_path_is_scratch(self) -> None:
        deliverables, scratch = _partition_sandbox_outputs(["page-1.png"])
        assert deliverables == []
        assert scratch == ["page-1.png"]

    def test_dotfile_cache_directory_excluded_from_both(self) -> None:
        deliverables, scratch = _partition_sandbox_outputs([".matplotlib/fontlist-v390.json"])
        assert deliverables == []
        assert scratch == []

    def test_staged_input_artifacts_excluded_from_both(self) -> None:
        deliverables, scratch = _partition_sandbox_outputs(["input/artifacts/x.png"])
        assert deliverables == []
        assert scratch == []

    def test_duplicate_paths_deduped(self) -> None:
        deliverables, _ = _partition_sandbox_outputs(["output/report.pdf", "output/report.pdf"])
        assert deliverables == ["output/report.pdf"]

    def test_screenshot_regression_shape_partitions_correctly(self) -> None:
        """The exact shape from the reported bug: a PDF plus its two
        intermediate page renders."""
        deliverables, scratch = _partition_sandbox_outputs(
            ["output/summary.pdf", "page-1.png", "page-2.png"]
        )
        assert deliverables == ["output/summary.pdf"]
        assert scratch == ["page-1.png", "page-2.png"]

    def test_leading_slash_normalized(self) -> None:
        deliverables, _ = _partition_sandbox_outputs(["/output/report.pdf"])
        assert deliverables == ["/output/report.pdf"]

    def test_backslash_path_normalized(self) -> None:
        deliverables, _ = _partition_sandbox_outputs(["output\\report.pdf"])
        assert deliverables == ["output\\report.pdf"]

    def test_nested_output_subdir_is_deliverable(self) -> None:
        deliverables, _ = _partition_sandbox_outputs(["output/charts/rev.png"])
        assert deliverables == ["output/charts/rev.png"]


class TestOutputDirScreenshotRegression:
    """End-to-end regression test for the reported bug: a PDF plus its
    intermediate page renders must produce exactly one artifact/SSE
    event/marker, never three."""

    async def test_pdf_and_page_renders_registers_only_the_pdf(self, monkeypatch) -> None:
        blob_store = AsyncMock()
        event_sink = AsyncMock()
        context = _make_context(blob_store=blob_store, event_sink=event_sink)
        registry = _make_mock_registry()
        monkeypatch.setattr(AgentContext, "artifact_registry", property(lambda self: registry))
        manager = SandboxManager()
        download_calls: list[str] = []

        class _TrackingBackend(_FakeBackend):
            def __init__(self) -> None:
                super().__init__({})

            async def download_file(self, path: str) -> bytes:
                download_calls.append(path)
                return b"pdf-bytes"

        manager.register_backend_factory(SandboxType.CODING, _TrackingBackend)
        sandbox_id, _ = await manager.get_or_create(SandboxType.CODING)

        ctx = ToolResultContext(
            tool_path="/toolsets/coding_sandbox/run_code",
            tool_use_id=__import__("uuid").uuid4(),
            tool_response=ToolOutput(
                success=True,
                data={
                    "artifacts": ["output/summary.pdf", "page-1.png", "page-2.png"],
                    "sandbox_id": sandbox_id,
                },
            ),
        )

        scheduled: list[asyncio.Task] = []
        with patch(
            "app.agents.agent_loop.sandbox_bridge.register_task",
            side_effect=lambda cid, task: scheduled.append(task),
        ):
            await coding_sandbox_artifact_bridge(context, manager)(ctx, _noop_next)
            for task in scheduled:
                await task

        assert download_calls == ["output/summary.pdf"]
        assert len(ctx.tool_response.data["artifacts"]) == 1
        assert ctx.tool_response.data["scratch_files"] == ["page-1.png", "page-2.png"]
        event_sink.write.assert_awaited_once()
        assert len(scheduled) == 1


class TestBasenameCollisionDisambiguation:
    async def test_colliding_basenames_get_distinct_registered_names(self, monkeypatch) -> None:
        blob_store = AsyncMock()
        context = _make_context(blob_store=blob_store)
        registry = _make_mock_registry()
        monkeypatch.setattr(AgentContext, "artifact_registry", property(lambda self: registry))
        manager = SandboxManager()
        manager.register_backend_factory(
            SandboxType.CODING,
            lambda: _FakeBackend({
                "output/charts/rev.png": b"chart-bytes",
                "output/tables/rev.png": b"table-bytes",
            }),
        )
        sandbox_id, _ = await manager.get_or_create(SandboxType.CODING)

        ctx = ToolResultContext(
            tool_path="/toolsets/coding_sandbox/run_code",
            tool_use_id=__import__("uuid").uuid4(),
            tool_response=ToolOutput(
                success=True,
                data={
                    "artifacts": ["output/charts/rev.png", "output/tables/rev.png"],
                    "sandbox_id": sandbox_id,
                },
            ),
        )

        with patch("app.agents.agent_loop.sandbox_bridge.register_task"):
            await coding_sandbox_artifact_bridge(context, manager)(ctx, _noop_next)

        names = [kw["name"] for _, kw in registry.register_output.call_args_list]
        assert names == ["charts_rev.png", "tables_rev.png"]


class TestScratchOnlyRun:
    async def test_scratch_only_run_leaves_empty_artifacts_and_gate_false(self, monkeypatch) -> None:
        """Regression for the raw-path leak: a scratch-only run must leave
        `data["artifacts"] == []` — never the raw sandbox-relative paths —
        and must NOT satisfy `completion_gate`."""
        blob_store = AsyncMock()
        context = _make_context(blob_store=blob_store)
        registry = _make_mock_registry()
        monkeypatch.setattr(AgentContext, "artifact_registry", property(lambda self: registry))
        manager = SandboxManager()
        manager.register_backend_factory(SandboxType.CODING, lambda: _FakeBackend({}))
        sandbox_id, _ = await manager.get_or_create(SandboxType.CODING)

        ctx = ToolResultContext(
            tool_path="/toolsets/coding_sandbox/run_code",
            tool_use_id=__import__("uuid").uuid4(),
            tool_response=ToolOutput(
                success=True,
                data={"artifacts": ["scratch.tmp"], "sandbox_id": sandbox_id},
            ),
        )

        with patch("app.agents.agent_loop.sandbox_bridge.register_task") as mock_register:
            await coding_sandbox_artifact_bridge(context, manager)(ctx, _noop_next)

        assert ctx.tool_response.data["artifacts"] == []
        assert ctx.tool_response.data["scratch_files"] == ["scratch.tmp"]
        assert context.artifacts_produced_this_run is False
        mock_register.assert_not_called()

    async def test_dotfile_cache_never_reported_as_scratch_or_rescued(self, monkeypatch) -> None:
        blob_store = AsyncMock()
        context = _make_context(blob_store=blob_store)
        registry = _make_mock_registry()
        monkeypatch.setattr(AgentContext, "artifact_registry", property(lambda self: registry))
        manager = SandboxManager()
        manager.register_backend_factory(SandboxType.CODING, lambda: _FakeBackend({}))
        sandbox_id, _ = await manager.get_or_create(SandboxType.CODING)
        tool_scope, run_scope = _make_tool_scope()

        ctx = ToolResultContext(
            tool_path="/toolsets/coding_sandbox/run_code",
            tool_use_id=__import__("uuid").uuid4(),
            tool_response=ToolOutput(
                success=True,
                data={"artifacts": [".matplotlib/fontlist-v390.json"], "sandbox_id": sandbox_id},
            ),
            scope=tool_scope,
        )

        with patch("app.agents.agent_loop.sandbox_bridge.register_task"):
            await coding_sandbox_artifact_bridge(context, manager)(ctx, _noop_next)

        assert "scratch_files" not in ctx.tool_response.data

        from app.agent_loop_lib.core.types import AgentResult, Goal

        result = AgentResult(goal=Goal(description="test"))
        lifecycle_ctx = AgentLifecycleContext(result=result, scope=run_scope)
        await coding_sandbox_result_propagation(context, manager)(lifecycle_ctx, _noop_next)

        assert result.artifacts == []
        registry.register_output.assert_not_awaited()


class TestDeferredScratchFallback:
    """`coding_sandbox_result_propagation`'s POST_AGENT rescue — fires only
    when the whole run ends with zero real $OUTPUT_DIR deliverables."""

    async def test_scratch_only_call_is_rescued_at_post_agent(self, monkeypatch) -> None:
        blob_store = AsyncMock()
        context = _make_context(blob_store=blob_store)
        registry = _make_mock_registry(metadata=_make_metadata(name="chart.png"))
        monkeypatch.setattr(AgentContext, "artifact_registry", property(lambda self: registry))
        manager = SandboxManager()
        manager.register_backend_factory(
            SandboxType.CODING, lambda: _FakeBackend({"chart.png": b"png-bytes"}),
        )
        sandbox_id, _ = await manager.get_or_create(SandboxType.CODING)

        tool_scope, run_scope = _make_tool_scope()
        ctx = ToolResultContext(
            tool_path="/toolsets/coding_sandbox/run_code",
            tool_use_id=__import__("uuid").uuid4(),
            tool_response=ToolOutput(
                success=True,
                data={"artifacts": ["chart.png"], "sandbox_id": sandbox_id},
            ),
            scope=tool_scope,
        )

        with patch("app.agents.agent_loop.sandbox_bridge.register_task"):
            await coding_sandbox_artifact_bridge(context, manager)(ctx, _noop_next)

        # Nothing delivered during the call itself.
        assert ctx.tool_response.data["artifacts"] == []
        assert context.artifacts_registered_this_run == []
        assert context.artifacts_produced_this_run is False

        from app.agent_loop_lib.core.types import AgentResult, Goal

        result = AgentResult(goal=Goal(description="test"))
        lifecycle_ctx = AgentLifecycleContext(result=result, scope=run_scope)

        with patch("app.agents.agent_loop.sandbox_bridge.register_task"):
            await coding_sandbox_result_propagation(context, manager)(lifecycle_ctx, _noop_next)

        assert len(result.artifacts) == 1
        assert result.artifacts[0].name == "chart.png"
        assert context.artifacts_produced_this_run is True

    async def test_run_with_later_output_dir_delivery_never_rescues(self, monkeypatch) -> None:
        """Two calls in the SAME run: call 1 writes an intermediate to cwd
        (scratch), call 2 writes the real deliverable to $OUTPUT_DIR. Must
        deliver exactly the one real deliverable — never also rescue call
        1's intermediate."""
        blob_store = AsyncMock()
        context = _make_context(blob_store=blob_store)
        registry = _make_mock_registry()
        monkeypatch.setattr(AgentContext, "artifact_registry", property(lambda self: registry))
        manager = SandboxManager()
        manager.register_backend_factory(
            SandboxType.CODING,
            lambda: _FakeBackend({"data.csv": b"csv-bytes", "output/report.pdf": b"pdf-bytes"}),
        )
        sandbox_id, _ = await manager.get_or_create(SandboxType.CODING)
        tool_scope, run_scope = _make_tool_scope()

        first_ctx = ToolResultContext(
            tool_path="/toolsets/coding_sandbox/run_code",
            tool_use_id=__import__("uuid").uuid4(),
            tool_response=ToolOutput(
                success=True, data={"artifacts": ["data.csv"], "sandbox_id": sandbox_id},
            ),
            scope=tool_scope,
        )
        second_ctx = ToolResultContext(
            tool_path="/toolsets/coding_sandbox/run_code",
            tool_use_id=__import__("uuid").uuid4(),
            tool_response=ToolOutput(
                success=True, data={"artifacts": ["output/report.pdf"], "sandbox_id": sandbox_id},
            ),
            scope=tool_scope,
        )

        scheduled: list[asyncio.Task] = []
        with patch(
            "app.agents.agent_loop.sandbox_bridge.register_task",
            side_effect=lambda cid, task: scheduled.append(task),
        ):
            await coding_sandbox_artifact_bridge(context, manager)(first_ctx, _noop_next)
            await coding_sandbox_artifact_bridge(context, manager)(second_ctx, _noop_next)
            for task in scheduled:
                await task

        assert registry.register_output.await_count == 1

        from app.agent_loop_lib.core.types import AgentResult, Goal

        result = AgentResult(goal=Goal(description="test"))
        lifecycle_ctx = AgentLifecycleContext(result=result, scope=run_scope)

        with patch("app.agents.agent_loop.sandbox_bridge.register_task"):
            await coding_sandbox_result_propagation(context, manager)(lifecycle_ctx, _noop_next)

        assert len(result.artifacts) == 1
        # Rescue must not have fired a second registration for call 1's scratch.
        assert registry.register_output.await_count == 1

    async def test_unknown_sandbox_id_rescue_does_not_raise(self, monkeypatch) -> None:
        """Sandbox already torn down by the time POST_AGENT runs — the
        rescue must degrade gracefully, not raise."""
        context = _make_context(blob_store=AsyncMock())
        registry = _make_mock_registry()
        monkeypatch.setattr(AgentContext, "artifact_registry", property(lambda self: registry))
        manager = SandboxManager()
        manager.register_backend_factory(SandboxType.CODING, lambda: _FakeBackend({"chart.png": b"x"}))
        sandbox_id, _ = await manager.get_or_create(SandboxType.CODING)
        tool_scope, run_scope = _make_tool_scope()

        ctx = ToolResultContext(
            tool_path="/toolsets/coding_sandbox/run_code",
            tool_use_id=__import__("uuid").uuid4(),
            tool_response=ToolOutput(
                success=True, data={"artifacts": ["chart.png"], "sandbox_id": sandbox_id},
            ),
            scope=tool_scope,
        )
        with patch("app.agents.agent_loop.sandbox_bridge.register_task"):
            await coding_sandbox_artifact_bridge(context, manager)(ctx, _noop_next)

        await manager.destroy_all()

        from app.agent_loop_lib.core.types import AgentResult, Goal

        result = AgentResult(goal=Goal(description="test"))
        lifecycle_ctx = AgentLifecycleContext(result=result, scope=run_scope)
        await coding_sandbox_result_propagation(context, manager)(lifecycle_ctx, _noop_next)

        assert result.artifacts == []


class _FakeBackend:
    """Download stub for the artifact-bridge hooks.

    Implements the full `SandboxLifecycle` contract (`sandbox_id`,
    `provision`, `destroy`) because it is registered into a real
    `SandboxManager` — the manager requires it of every backend rather
    than probing with `getattr`, so a backend that forgets one of these
    fails at registration instead of at teardown.
    """

    def __init__(self, files: dict[str, bytes]) -> None:
        self._files = files
        self.sandbox_id = f"fake-{uuid4().hex[:8]}"

    async def provision(self) -> None:
        return None

    async def destroy(self) -> None:
        return None

    async def download_file(self, path: str) -> bytes:
        return self._files[path]


def _make_tool_scope() -> tuple[ToolScope, RunScope]:
    run_scope = RunScope(identity=MagicMock(), spec=MagicMock(), runtime=MagicMock(), goal=MagicMock())
    turn_scope = TurnScope(run=run_scope, turn_index=0)
    tool_scope = ToolScope(turn=turn_scope, call=MagicMock(), tool_path="/toolsets/coding_sandbox/run_code")
    return tool_scope, run_scope


class TestCodingSandboxResultPropagation:
    """`coding_sandbox_result_propagation` — the POST_AGENT hook that
    copies THIS run's registered artifacts onto `AgentResult.artifacts`
    for a parent/orchestrator to see, scoped per-`RunScope` so concurrent
    sibling `coding_agent` spawns never see each other's artifacts."""

    async def test_registered_artifact_is_copied_onto_agent_result(self, monkeypatch) -> None:
        blob_store = AsyncMock()
        context = _make_context(blob_store=blob_store)
        metadata = _make_metadata(artifact_id="rec-1", name="chart.png", mime_type="image/png")
        registry = _make_mock_registry(metadata=metadata)
        monkeypatch.setattr(AgentContext, "artifact_registry", property(lambda self: registry))
        manager = SandboxManager()
        manager.register_backend_factory(
            SandboxType.CODING, lambda: _FakeBackend({"output/chart.png": b"png-bytes"}),
        )
        sandbox_id, _ = await manager.get_or_create(SandboxType.CODING)

        tool_scope, run_scope = _make_tool_scope()
        ctx = ToolResultContext(
            tool_path="/toolsets/coding_sandbox/run_code",
            tool_use_id=__import__("uuid").uuid4(),
            tool_response=ToolOutput(
                success=True,
                data={"artifacts": ["output/chart.png"], "sandbox_id": sandbox_id},
            ),
            scope=tool_scope,
        )

        scheduled: list[asyncio.Task] = []
        with patch(
            "app.agents.agent_loop.sandbox_bridge.register_task",
            side_effect=lambda cid, task: scheduled.append(task),
        ):
            await coding_sandbox_artifact_bridge(context, manager)(ctx, _noop_next)
            await scheduled[0]

        from app.agent_loop_lib.core.types import AgentResult, Goal

        result = AgentResult(goal=Goal(description="test"))
        lifecycle_ctx = AgentLifecycleContext(result=result, scope=run_scope)

        await coding_sandbox_result_propagation(context, manager)(lifecycle_ctx, _noop_next)

        assert len(result.artifacts) == 1
        assert result.artifacts[0].name == "chart.png"
        assert result.artifacts[0].content["artifact_id"] == "rec-1"

    async def test_no_artifacts_registered_leaves_result_untouched(self) -> None:
        _, run_scope = _make_tool_scope()
        from app.agent_loop_lib.core.types import AgentResult, Goal

        result = AgentResult(goal=Goal(description="test"))
        lifecycle_ctx = AgentLifecycleContext(result=result, scope=run_scope)

        await coding_sandbox_result_propagation(_make_context(), SandboxManager())(lifecycle_ctx, _noop_next)

        assert result.artifacts == []

    async def test_no_scope_or_result_does_not_raise(self) -> None:
        lifecycle_ctx = AgentLifecycleContext(result=None, scope=None)
        await coding_sandbox_result_propagation(_make_context(), SandboxManager())(lifecycle_ctx, _noop_next)


class TestRegisterCodingSandboxHooksIntegration:
    """Confirms the PRE_TOOL_USE/POST_TOOL_USE wiring is scoped to
    `/toolsets/coding_sandbox/**` only — a sibling toolset's calls must be
    completely unaffected by the coding-sandbox safety/policy middleware."""

    def _build(self, *, allow_network: bool | None = None) -> tuple[HookRegistry, AgentContext, SandboxManager]:
        context = _make_context()
        manager = SandboxManager()
        hooks = HookRegistry()
        register_coding_sandbox_hooks(hooks, context, manager, allow_network=allow_network)
        return hooks, context, manager

    async def test_dangerous_code_denied_within_coding_sandbox_scope(self) -> None:
        hooks, _, _ = self._build()
        ctx = ToolCallContext(
            tool_path="/toolsets/coding_sandbox/run_code",
            tool_input={"code": "import shutil\nshutil.rmtree('/')"},
        )
        result = await hooks.on(HookEvent.PRE_TOOL_USE).dispatch(ctx)
        assert result.decision == PreDecision.DENY

    async def test_dangerous_code_not_denied_outside_coding_sandbox_scope(self) -> None:
        hooks, _, _ = self._build()
        ctx = ToolCallContext(
            tool_path="/toolsets/jira/create_issue",
            tool_input={"code": "import shutil\nshutil.rmtree('/')"},
        )
        result = await hooks.on(HookEvent.PRE_TOOL_USE).dispatch(ctx)
        assert result.decision == PreDecision.ALLOW

    async def test_disallowed_package_denied_within_scope(self) -> None:
        hooks, _, _ = self._build()
        ctx = ToolCallContext(
            tool_path="/toolsets/coding_sandbox/install_packages",
            tool_input={"packages": ["not-on-allowlist"], "language": "typescript"},
        )
        result = await hooks.on(HookEvent.PRE_TOOL_USE).dispatch(ctx)
        assert result.decision == PreDecision.DENY
        assert "not-on-allowlist" in result.decision_reason

    async def test_package_policy_deny_message_reflects_allow_network_flag(self) -> None:
        hooks, _, _ = self._build(allow_network=True)
        ctx = ToolCallContext(
            tool_path="/toolsets/coding_sandbox/install_packages",
            tool_input={"packages": ["not-on-allowlist"], "language": "typescript"},
        )
        result = await hooks.on(HookEvent.PRE_TOOL_USE).dispatch(ctx)
        assert result.decision == PreDecision.DENY
        assert "this sandbox has network access" in result.decision_reason

    async def test_post_tool_use_redacts_within_scope(self) -> None:
        hooks, _, _ = self._build()
        ctx = ToolResultContext(
            tool_path="/toolsets/coding_sandbox/run_code",
            tool_use_id=__import__("uuid").uuid4(),
            tool_response=ToolOutput(
                success=True, data={"stdout": "Saved at: /tmp/pipeshub_sandbox/x/output/a.pdf"},
            ),
        )
        result = await hooks.on(HookEvent.POST_TOOL_USE).dispatch(ctx)
        assert result.decision == PostDecision.CONTINUE
        assert result.tool_response.data["stdout"] == "Saved at: <output>/a.pdf"

    async def test_post_tool_use_does_not_redact_outside_scope(self) -> None:
        hooks, _, _ = self._build()
        ctx = ToolResultContext(
            tool_path="/toolsets/jira/create_issue",
            tool_use_id=__import__("uuid").uuid4(),
            tool_response=ToolOutput(
                success=True, data={"stdout": "Saved at: /tmp/pipeshub_sandbox/x/output/a.pdf"},
            ),
        )
        result = await hooks.on(HookEvent.POST_TOOL_USE).dispatch(ctx)
        assert result.tool_response.data["stdout"] == "Saved at: /tmp/pipeshub_sandbox/x/output/a.pdf"
