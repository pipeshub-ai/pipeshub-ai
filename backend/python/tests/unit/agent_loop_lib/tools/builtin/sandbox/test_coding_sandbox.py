"""Tests for app.agent_loop_lib.tools.builtin.sandbox.coding_sandbox.

Covers both the no-network (default) description/behavior — `run_code`'s
execution container runs with `network_mode="none"` unless explicitly
opted into network access, and the model must be told this explicitly or
it keeps trying (and failing) to call external APIs from inside `run_code`
— and the network-enabled variant, where the description instead steers
the model TOWARD calling live public APIs from `run_code` and `execute()`
threads `allow_network` into the `CodeRequest` the backend receives.
"""

from __future__ import annotations

import uuid

from app.agent_loop_lib.sandbox.coding.base import CodeRequest, CodeResult, InstallResult
from app.agent_loop_lib.sandbox.manager import SandboxManager, SandboxType
from app.agent_loop_lib.tools.builtin.sandbox.coding_sandbox import (
    CodingSandboxTool,
    InstallPackagesTool,
    detect_language_mismatch,
)
from app.agent_loop_lib.tools.builtin.sandbox import input_staging
from app.agent_loop_lib.tools.builtin.sandbox.input_staging import (
    PARENT_RESULTS_INPUT_PATH,
    add_staged_skill_resources,
    stage_input_files,
)


class _CapturingBackend:
    """Fake `CodingSandboxBackend` that records the `CodeRequest` it was
    asked to execute, so tests can assert on `allow_network` without
    spinning up a real subprocess/container."""

    sandbox_id = "fake-sandbox-1"

    def __init__(self) -> None:
        self.last_request: CodeRequest | None = None

    async def provision(self):  # noqa: ANN201 - test double
        return None

    async def execute(self, request: CodeRequest) -> CodeResult:
        self.last_request = request
        return CodeResult(stdout="ok", stderr="", exit_code=0, language=request.language, duration_ms=1.0)


def test_description_discloses_no_network_access() -> None:
    tool = CodingSandboxTool(SandboxManager())
    assert "NO network access" in tool.description


def test_description_directs_model_to_fetch_first() -> None:
    tool = CodingSandboxTool(SandboxManager())
    assert "web_search" in tool.description
    assert "fetch_url" in tool.description


def test_description_warns_against_network_libraries() -> None:
    tool = CodingSandboxTool(SandboxManager())
    assert "requests" in tool.description
    assert "fetches a URL" in tool.description or "fetch a URL" in tool.description


def test_network_enabled_description_advertises_network_access() -> None:
    tool = CodingSandboxTool(SandboxManager(), allow_network=True)
    assert "CAN reach the network" in tool.description
    assert "NO network access" not in tool.description


def test_network_enabled_description_still_forbids_internal_hosts() -> None:
    tool = CodingSandboxTool(SandboxManager(), allow_network=True)
    assert "Internal/private hosts" in tool.description


class TestAdvertiseParentResults:
    """Covers `CodingSandboxTool`'s `advertise_parent_results` flag (see
    `set_advertise_parent_results`'s docstring): `run_code`'s description
    must not promise `PARENT_RESULTS_INPUT_PATH` pre-loading to an agent
    whose call path never actually stages it (e.g. a flat, non-delegated
    grant in quick mode — see `factory.py`)."""

    def test_default_advertises_parent_results_file(self) -> None:
        tool = CodingSandboxTool(SandboxManager())
        assert PARENT_RESULTS_INPUT_PATH in tool.description
        assert "MAY have it pre-loaded" in tool.description
        assert "ALWAYS guard this read" in tool.description

    def test_constructor_flag_can_disable_the_promise_upfront(self) -> None:
        tool = CodingSandboxTool(SandboxManager(), advertise_parent_results=False)
        # The path is still NAMED (so the model knows exactly what to avoid
        # opening), but the "pre-loaded" promise itself must be gone.
        assert "MAY have it pre-loaded" not in tool.description
        assert "never has" in tool.description

    def test_setter_disables_the_promise_after_construction(self) -> None:
        # The real call site (`factory.py`) only knows whether domain-agent
        # composition wrapped this tool in a delegated `coding_agent`
        # AFTER the tool was already registered/constructed — the setter,
        # not the constructor default, is what that call site relies on.
        tool = CodingSandboxTool(SandboxManager())
        tool.set_advertise_parent_results(False)
        assert "MAY have it pre-loaded" not in tool.description
        assert "do NOT write code that opens that path" in tool.description

    def test_setter_can_re_enable_the_promise(self) -> None:
        tool = CodingSandboxTool(SandboxManager(), advertise_parent_results=False)
        tool.set_advertise_parent_results(True)
        assert "MAY have it pre-loaded" in tool.description


async def test_execute_sets_allow_network_true_on_code_request() -> None:
    manager = SandboxManager()
    manager.register_backend_factory(SandboxType.CODING, _CapturingBackend)
    tool = CodingSandboxTool(manager, allow_network=True)

    result = await tool.execute(code="print(1)", language="python")

    assert result.success
    backend = manager.get(SandboxType.CODING, result.data["sandbox_id"])
    assert backend.last_request is not None
    assert backend.last_request.allow_network is True


async def test_execute_sets_allow_network_false_by_default() -> None:
    manager = SandboxManager()
    manager.register_backend_factory(SandboxType.CODING, _CapturingBackend)
    tool = CodingSandboxTool(manager)

    result = await tool.execute(code="print(1)", language="python")

    assert result.success
    backend = manager.get(SandboxType.CODING, result.data["sandbox_id"])
    assert backend.last_request.allow_network is False


class TestDetectLanguageMismatch:
    def test_python_code_declared_typescript_is_corrected(self) -> None:
        code = "import reportlab\n\ndef build():\n    print('hi')\n"
        assert detect_language_mismatch(code, "typescript") == "python"

    def test_typescript_code_declared_python_is_corrected(self) -> None:
        code = "import { foo } from 'bar';\n\nconst x: number = 1;\nconsole.log(x);\n"
        assert detect_language_mismatch(code, "python") == "typescript"

    def test_matching_language_is_not_corrected(self) -> None:
        code = "import reportlab\n\ndef build():\n    print('hi')\n"
        assert detect_language_mismatch(code, "python") is None

    def test_ambiguous_single_signal_is_not_corrected(self) -> None:
        code = "print('just one signal')\n"
        assert detect_language_mismatch(code, "typescript") is None

    def test_empty_code_is_not_corrected(self) -> None:
        assert detect_language_mismatch("", "typescript") is None


async def test_execute_corrects_python_code_declared_as_typescript() -> None:
    manager = SandboxManager()
    manager.register_backend_factory(SandboxType.CODING, _CapturingBackend)
    tool = CodingSandboxTool(manager)
    code = "import reportlab\n\ndef build():\n    print('hi')\n"

    result = await tool.execute(code=code, language="typescript")

    assert result.success
    backend = manager.get(SandboxType.CODING, result.data["sandbox_id"])
    assert backend.last_request.language == "python"
    assert "language_correction" in result.data
    assert "python" in result.data["language_correction"]


async def test_execute_leaves_matching_language_untouched() -> None:
    manager = SandboxManager()
    manager.register_backend_factory(SandboxType.CODING, _CapturingBackend)
    tool = CodingSandboxTool(manager)

    result = await tool.execute(code="print(1)", language="python")

    assert result.success
    backend = manager.get(SandboxType.CODING, result.data["sandbox_id"])
    assert backend.last_request.language == "python"
    assert "language_correction" not in result.data


class _UploadCapturingBackend(_CapturingBackend):
    """Extends `_CapturingBackend` with `upload_file` tracking and a
    unique per-instance `sandbox_id` (the base class's is a fixed class
    attribute, which would make every fresh creation in a test collide
    on the same manager slot)."""

    def __init__(self) -> None:
        super().__init__()
        self.sandbox_id = f"fake-sandbox-{uuid.uuid4()}"
        self.uploaded: dict[str, bytes] = {}

    async def upload_file(self, path: str, content: bytes) -> None:
        self.uploaded[path] = content

    async def install_packages(self, packages: list[str], language: str) -> InstallResult:
        return InstallResult(success=True, installed=list(packages))


class TestStagedInputFileUpload:
    """Covers the parent -> child data-handoff wiring on the sandbox side
    (see `input_staging.py`): a FRESH sandbox picks up whatever is
    currently staged, and — unlike `InstallPackagesTool`, which only
    creates sandboxes and so only ever needs the fresh-creation path — a
    REUSED `CodingSandboxTool.execute()` call re-uploads too. That's
    required for PipesHub's `input_artifacts` (`sandbox_bridge.py`),
    resolved fresh per call via `set_staged_input_files_for_task` and
    staged even when the model passes an explicit `sandbox_id`; re-
    uploading the SAME parent-handoff bytes on every call is an
    idempotent no-op cost, not a correctness issue."""

    async def test_uploads_staged_files_on_fresh_sandbox(self) -> None:
        manager = SandboxManager()
        manager.register_backend_factory(SandboxType.CODING, _UploadCapturingBackend)
        tool = CodingSandboxTool(manager)

        with stage_input_files({"input/parent_tool_results.json": b'{"a": 1}'}):
            result = await tool.execute(code="print(1)", language="python")

        assert result.success
        backend = manager.get(SandboxType.CODING, result.data["sandbox_id"])
        assert backend.uploaded == {"input/parent_tool_results.json": b'{"a": 1}'}
        assert result.data["input_files"] == ["input/parent_tool_results.json"]

    async def test_no_upload_and_no_input_files_key_when_nothing_staged(self) -> None:
        manager = SandboxManager()
        manager.register_backend_factory(SandboxType.CODING, _UploadCapturingBackend)
        tool = CodingSandboxTool(manager)

        result = await tool.execute(code="print(1)", language="python")

        assert result.success
        backend = manager.get(SandboxType.CODING, result.data["sandbox_id"])
        assert backend.uploaded == {}
        assert "input_files" not in result.data

    async def test_reuploads_staged_files_when_reusing_an_existing_sandbox(self) -> None:
        """Regression for the PRE_TOOL_USE staging bug: a model calling
        `run_code` with BOTH `sandbox_id` (reuse) AND freshly resolved
        `input_artifacts` (staged per-call by `sandbox_bridge.py`'s PRE
        hook via `set_staged_input_files_for_task`) must still get those
        bytes uploaded — gating the upload on `is_fresh_sandbox` silently
        dropped them on every reused-sandbox call."""
        manager = SandboxManager()
        manager.register_backend_factory(SandboxType.CODING, _UploadCapturingBackend)
        tool = CodingSandboxTool(manager)

        with stage_input_files({"input/parent_tool_results.json": b"data"}):
            first = await tool.execute(code="print(1)", language="python")
        sandbox_id = first.data["sandbox_id"]

        with stage_input_files({"input/parent_tool_results.json": b"data"}):
            second = await tool.execute(code="print(2)", language="python", sandbox_id=sandbox_id)

        assert second.success
        assert second.data["input_files"] == ["input/parent_tool_results.json"]
        backend = manager.get(SandboxType.CODING, sandbox_id)
        assert backend.uploaded == {"input/parent_tool_results.json": b"data"}

    async def test_reused_sandbox_upload_uses_task_local_staging(self) -> None:
        """End-to-end shape of the actual bug: PRE_TOOL_USE middleware
        calls `set_staged_input_files_for_task` (never `stage_input_files`'s
        `with` block — see that function's docstring for why), then
        `next_fn()` returns, THEN `execute()` runs — and a reused sandbox
        with freshly staged `input_artifacts` still gets them uploaded."""
        manager = SandboxManager()
        manager.register_backend_factory(SandboxType.CODING, _UploadCapturingBackend)
        tool = CodingSandboxTool(manager)

        first = await tool.execute(code="print(1)", language="python")
        sandbox_id = first.data["sandbox_id"]

        # `set_staged_input_files_for_task` has no `with`-block reset (see
        # its docstring) — manually reset the token afterward so this
        # test can't leak staged files into a later test on this task.
        token = input_staging._staged_input_files.set(None)
        try:
            # Simulates the PRE hook: set-then-dispatch-returns, exactly
            # as `ToolExecutor.call_tool()` runs PRE_TOOL_USE to
            # completion BEFORE calling `tool.execute()`.
            input_staging.set_staged_input_files_for_task(
                {"input/artifacts/chart.png": b"pngbytes"},
            )
            second = await tool.execute(code="print(2)", language="python", sandbox_id=sandbox_id)
        finally:
            input_staging._staged_input_files.reset(token)

        assert second.success
        assert second.data["input_files"] == ["input/artifacts/chart.png"]
        backend = manager.get(SandboxType.CODING, sandbox_id)
        assert backend.uploaded == {"input/artifacts/chart.png": b"pngbytes"}

    async def test_install_packages_uploads_staged_files_on_fresh_sandbox(self) -> None:
        """Regression: a child that pre-warms with `install_packages` FIRST
        creates its sandbox there, and every later `run_code` call reuses
        it (non-fresh, upload skipped) — if install_packages doesn't do
        the staged upload itself, `input/parent_tool_results.json` never
        reaches that sandbox at all, despite the child's goal promising
        it ("pre-loaded as soon as you create a sandbox")."""
        manager = SandboxManager()
        manager.register_backend_factory(SandboxType.CODING, _UploadCapturingBackend)
        install_tool = InstallPackagesTool(manager)
        run_tool = CodingSandboxTool(manager)

        with stage_input_files({"input/parent_tool_results.json": b'{"a": 1}'}):
            installed = await install_tool.execute(packages=["reportlab"], language="python")
            assert installed.success
            sandbox_id = installed.data["sandbox_id"]
            ran = await run_tool.execute(code="print(1)", language="python", sandbox_id=sandbox_id)

        assert ran.success
        assert installed.data["input_files"] == ["input/parent_tool_results.json"]
        backend = manager.get(SandboxType.CODING, sandbox_id)
        assert backend.uploaded == {"input/parent_tool_results.json": b'{"a": 1}'}

    async def test_install_packages_does_not_upload_into_a_reused_sandbox(self) -> None:
        manager = SandboxManager()
        manager.register_backend_factory(SandboxType.CODING, _UploadCapturingBackend)
        install_tool = InstallPackagesTool(manager)

        first = await install_tool.execute(packages=["a"], language="python")
        with stage_input_files({"input/parent_tool_results.json": b"data"}):
            second = await install_tool.execute(
                packages=["b"], language="python", sandbox_id=first.data["sandbox_id"],
            )

        assert second.success
        assert "input_files" not in second.data


class TestStagedSkillResourceUpload:
    """A skill's bundled resources, staged by `load_skill` via
    `add_staged_skill_resources` (see `input_staging.py`), upload into the
    next fresh sandbox exactly like a parent-tool-result handoff does."""

    async def _reset(self):
        token = input_staging._staged_skill_resources.set(None)
        return token

    async def test_uploads_staged_skill_resources_on_fresh_sandbox(self) -> None:
        token = await self._reset()
        try:
            manager = SandboxManager()
            manager.register_backend_factory(SandboxType.CODING, _UploadCapturingBackend)
            tool = CodingSandboxTool(manager)

            add_staged_skill_resources({"skills/office-utils/scripts/unpack.py": b"print(1)"})
            result = await tool.execute(code="print(1)", language="python")

            assert result.success
            backend = manager.get(SandboxType.CODING, result.data["sandbox_id"])
            assert backend.uploaded == {"skills/office-utils/scripts/unpack.py": b"print(1)"}
            assert result.data["input_files"] == ["skills/office-utils/scripts/unpack.py"]
        finally:
            input_staging._staged_skill_resources.reset(token)

    async def test_merges_with_stage_input_files(self) -> None:
        token = await self._reset()
        try:
            manager = SandboxManager()
            manager.register_backend_factory(SandboxType.CODING, _UploadCapturingBackend)
            tool = CodingSandboxTool(manager)

            add_staged_skill_resources({"skills/office-utils/scripts/unpack.py": b"code"})
            with stage_input_files({"input/parent_tool_results.json": b"data"}):
                result = await tool.execute(code="print(1)", language="python")

            assert result.success
            backend = manager.get(SandboxType.CODING, result.data["sandbox_id"])
            assert backend.uploaded == {
                "skills/office-utils/scripts/unpack.py": b"code",
                "input/parent_tool_results.json": b"data",
            }
        finally:
            input_staging._staged_skill_resources.reset(token)
