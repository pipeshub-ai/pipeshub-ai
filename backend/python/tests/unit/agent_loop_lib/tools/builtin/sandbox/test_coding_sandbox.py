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

import asyncio
import uuid

from app.agent_loop_lib.core.types import ToolResult
from app.agent_loop_lib.sandbox.coding.base import (
    CodeRequest,
    CodeResult,
    InstallResult,
)
from app.agent_loop_lib.sandbox.manager import SandboxManager, SandboxType
from app.agent_loop_lib.tools.builtin.sandbox import input_staging
from app.agent_loop_lib.tools.builtin.sandbox.coding_sandbox import (
    CodingSandboxTool,
    InstallPackagesTool,
    detect_language_mismatch,
)
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


class TestWebToolNames:
    """`set_web_tool_names()` — the description must never name a web tool
    that was not actually granted this turn (see `factory.py`'s call site),
    and must fall back to a self-sufficiency note instead of a dead end."""

    def test_default_names_flat_web_search_and_fetch_url(self) -> None:
        tool = CodingSandboxTool(SandboxManager())
        assert "web_search" in tool.description
        assert "fetch_url" in tool.description

    def test_empty_names_drops_the_fetch_first_escape_hatch(self) -> None:
        tool = CodingSandboxTool(SandboxManager())
        tool.set_web_tool_names(())
        assert "web_search" not in tool.description
        assert "fetch_url" not in tool.description
        assert "cannot retrieve external data itself" in tool.description

    def test_composed_name_replaces_flat_pair(self) -> None:
        tool = CodingSandboxTool(SandboxManager())
        tool.set_web_tool_names(("web_agent",))
        assert "web_agent" in tool.description
        assert "web_search" not in tool.description

    def test_networked_description_drops_prefer_clause_when_no_web_tool(self) -> None:
        tool = CodingSandboxTool(SandboxManager(), allow_network=True)
        tool.set_web_tool_names(())
        assert "PREFER this over" not in tool.description
        assert "CAN reach the network" in tool.description

    def test_networked_description_names_granted_tool_in_prefer_clause(self) -> None:
        tool = CodingSandboxTool(SandboxManager(), allow_network=True)
        tool.set_web_tool_names(("web_agent",))
        assert "PREFER this over `web_agent`" in tool.description


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


class TestBlankSandboxId:
    """Regression: models fill an optional string parameter with `""`
    rather than omitting it. A blank `sandbox_id` has to mean "create a
    fresh sandbox", not "look up the sandbox named blank" — the latter
    made a first-ever `run_code` fail with `UnknownSandboxError`."""

    async def test_run_code_treats_blank_sandbox_id_as_fresh(self) -> None:
        manager = SandboxManager()
        manager.register_backend_factory(SandboxType.CODING, _UploadCapturingBackend)
        tool = CodingSandboxTool(manager)

        result = await tool.execute(code="print(1)", language="python", sandbox_id="")

        assert result.success, result.error
        assert result.data["sandbox_id"]

    async def test_run_code_still_uploads_staged_inputs_for_blank_sandbox_id(self) -> None:
        """A blank id must take the fresh-creation path, staged upload
        included — normalizing it only at the manager would resolve the
        sandbox but leave `is_fresh_sandbox` False."""
        manager = SandboxManager()
        manager.register_backend_factory(SandboxType.CODING, _UploadCapturingBackend)
        tool = InstallPackagesTool(manager)

        with stage_input_files({PARENT_RESULTS_INPUT_PATH: b'{"a": 1}'}):
            result = await tool.execute(packages=["reportlab"], language="python", sandbox_id="   ")

        assert result.success, result.error
        assert result.data["input_files"] == [PARENT_RESULTS_INPUT_PATH]

    async def test_unknown_sandbox_id_still_fails(self) -> None:
        manager = SandboxManager()
        manager.register_backend_factory(SandboxType.CODING, _UploadCapturingBackend)
        tool = CodingSandboxTool(manager)

        result = await tool.execute(code="print(1)", language="python", sandbox_id="nope")

        assert not result.success
        assert "nope" in result.error


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


def _ran(content: object) -> ToolResult:
    return ToolResult(tool_call_id="call-1", name="run_code", content=content)


def test_run_code_reports_a_non_zero_exit_as_failed() -> None:
    tool = CodingSandboxTool(SandboxManager())
    failed = CodeResult(stdout="", stderr="Traceback ...", exit_code=1, language="python", duration_ms=1.0)
    passed = CodeResult(stdout="ok", stderr="", exit_code=0, language="python", duration_ms=1.0)

    assert tool.result_failed({}, _ran(failed.model_dump())) is True
    assert tool.result_failed({}, _ran(passed.model_dump())) is False
    assert tool.result_failed({}, _ran("Unknown sandbox_id")) is False


def test_install_packages_reports_a_failed_install_as_failed() -> None:
    tool = InstallPackagesTool(SandboxManager())

    assert tool.result_failed({}, _ran(InstallResult(success=False, stderr="no such package").model_dump())) is True
    assert tool.result_failed({}, _ran(InstallResult(success=True, installed=["numpy"]).model_dump())) is False


class _TimeoutBackend(_CapturingBackend):
    """Records the request and reports a wall-clock timeout."""

    async def execute(self, request: CodeRequest) -> CodeResult:
        from app.agent_loop_lib.sandbox.coding.reflection import ReflectionEngine

        self.last_request = request
        result = CodeResult(
            stdout="", stderr=f"Execution timed out after {request.timeout}s", exit_code=-1,
            language=request.language, duration_ms=1.0,
        )
        return result.model_copy(update={"error_analysis": ReflectionEngine().analyze(result)})


def _capped_tool(
    backend_cls: type = _CapturingBackend, cap: float | None = 300.0,
) -> tuple[CodingSandboxTool, _CapturingBackend]:
    from app.agent_loop_lib.sandbox.manager import SandboxLimits

    backend = backend_cls()
    manager = SandboxManager()
    manager.register_backend_factory(
        SandboxType.CODING, lambda: backend, limits=SandboxLimits(max_run_timeout_s=cap),
    )
    return CodingSandboxTool(manager), backend


class TestRunTimeoutCap:
    async def test_timeout_above_the_cap_is_lowered_with_a_note(self) -> None:
        tool, backend = _capped_tool()
        result = await tool.execute(code="print(1)", language="python", timeout=900)
        assert backend.last_request.timeout == 300
        assert result.data["timeout_note"] == (
            "Requested timeout of 900s is above this deployment's maximum of 300s; "
            "the run was given 300s."
        )

    async def test_timeout_below_the_cap_is_kept(self) -> None:
        tool, backend = _capped_tool()
        result = await tool.execute(code="print(1)", language="python", timeout=60)
        assert backend.last_request.timeout == 60
        assert "timeout_note" not in result.data

    async def test_omitted_timeout_uses_the_default(self) -> None:
        tool, backend = _capped_tool()
        await tool.execute(code="print(1)", language="python")
        assert backend.last_request.timeout == 30

    async def test_zero_and_negative_become_one_second(self) -> None:
        for value in (0, -5):
            tool, backend = _capped_tool()
            await tool.execute(code="print(1)", language="python", timeout=value)
            assert backend.last_request.timeout == 1

    async def test_nan_uses_the_default_and_inf_the_cap(self) -> None:
        tool, backend = _capped_tool()
        await tool.execute(code="print(1)", language="python", timeout=float("nan"))
        assert backend.last_request.timeout == 30
        tool, backend = _capped_tool()
        result = await tool.execute(code="print(1)", language="python", timeout=float("inf"))
        assert backend.last_request.timeout == 300
        assert "timeout_note" in result.data

    async def test_timeout_at_the_cap_replaces_the_suggestion(self) -> None:
        tool, _backend = _capped_tool(_TimeoutBackend)
        result = await tool.execute(code="while True: pass", language="python", timeout=900)
        suggestion = result.data["error_analysis"]["suggestion"]
        assert suggestion.startswith("The run hit the maximum run time of 300s.")

    async def test_timeout_below_the_cap_keeps_the_suggestion(self) -> None:
        tool, _backend = _capped_tool(_TimeoutBackend)
        result = await tool.execute(code="while True: pass", language="python", timeout=60)
        assert "increase the timeout" in result.data["error_analysis"]["suggestion"]

    async def test_no_cap_leaves_the_timeout_alone(self) -> None:
        tool, backend = _capped_tool(cap=None)
        result = await tool.execute(code="print(1)", language="python", timeout=900)
        assert backend.last_request.timeout == 900
        assert "timeout_note" not in result.data

    def test_parameter_description_states_the_cap(self) -> None:
        tool, _backend = _capped_tool()
        timeout = next(p for p in tool.parameters if p.name == "timeout")
        assert "at most 300" in timeout.description

    async def test_finished_run_logs_its_outcome(self, caplog) -> None:
        import logging

        tool, _backend = _capped_tool(_TimeoutBackend)
        with caplog.at_level(logging.INFO):
            await tool.execute(code="while True: pass", language="python", timeout=900)
        assert "run_code finished: outcome=timeout exit_code=-1" in caplog.text
        assert "timeout_s=300 timeout_capped=True" in caplog.text


def test_manager_reports_the_registered_run_timeout_cap() -> None:
    from app.agent_loop_lib.sandbox.manager import SandboxLimits

    manager = SandboxManager()
    assert manager.run_timeout_cap(SandboxType.CODING) is None
    manager.register_backend_factory(
        SandboxType.CODING, _CapturingBackend, limits=SandboxLimits(max_run_timeout_s=120),
    )
    assert manager.run_timeout_cap(SandboxType.CODING) == 120


class _BlockingBackend(_UploadCapturingBackend):
    """Not metered (like Docker). `execute` and `install_packages` block
    until cancelled, then take a moment to clean up, as 1.4's container
    removal does."""

    class capabilities:  # stands in for SandboxCapabilities
        is_metered = False

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.saw_cancel = False
        self.cleaned_up = False

    async def _block(self) -> None:
        self.started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            self.saw_cancel = True
            await asyncio.sleep(0.05)
            self.cleaned_up = True
            raise

    async def execute(self, request: CodeRequest) -> CodeResult:
        self.last_request = request
        await self._block()
        raise AssertionError("unreachable")

    async def install_packages(self, packages: list[str], language: str) -> InstallResult:
        await self._block()
        raise AssertionError("unreachable")

    async def destroy(self) -> None:
        return None


def _stoppable(backend_cls: type = _BlockingBackend, **manager_kwargs: object) -> tuple:
    from app.agent_loop_lib.core.context import CancellationToken
    from app.agent_loop_lib.sandbox.governor import (
        GovernorLimits,
        SandboxResourceGovernor,
    )

    token = CancellationToken()
    gov = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=5))
    backends: list = []

    def make() -> object:
        backends.append(backend_cls())
        return backends[-1]

    manager = SandboxManager(governor=gov, stop_token=token, slot_wait_s=5, **manager_kwargs)
    manager.register_backend_factory(SandboxType.CODING, make)
    return manager, token, gov, backends


async def _stop_when_started(token: object, backends: list, count: int = 1) -> None:
    for _ in range(200):
        if len(backends) >= count and all(b.started.is_set() for b in backends[:count]):
            break
        await asyncio.sleep(0.01)
    token.cancel()


class TestStopInterruptsSandboxCalls:
    async def test_run_code_stopped_mid_run(self, caplog) -> None:
        import logging
        from unittest.mock import patch

        manager, token, gov, backends = _stoppable()
        tool = CodingSandboxTool(manager, artifact_output_dir="/tmp/unused")
        with patch(
            "app.agent_loop_lib.tools.builtin.sandbox.coding_sandbox._save_artifacts",
        ) as save, caplog.at_level(logging.INFO):
            stopper = asyncio.ensure_future(_stop_when_started(token, backends))
            result = await tool.execute(code="while True: pass", language="python", timeout=60)
            await stopper

        assert result.success is False
        assert result.error.startswith("Stopped by the user")
        assert dict(result.end_fields) == {"reason": "stopped", "error_class": "cancelled"}
        assert backends[0].cleaned_up
        save.assert_not_called()
        assert gov.snapshot()["total"] == 0
        assert "run_code finished: outcome=stopped" in caplog.text

    async def test_install_packages_stopped(self) -> None:
        manager, token, gov, backends = _stoppable()
        tool = InstallPackagesTool(manager)
        stopper = asyncio.ensure_future(_stop_when_started(token, backends))
        result = await tool.execute(packages=["pandas"], language="python")
        await stopper

        assert result.success is False
        assert result.error.startswith("Stopped by the user")
        assert backends[0].saw_cancel
        assert gov.snapshot()["total"] == 0

    async def test_stop_while_the_sandbox_is_being_created(self) -> None:
        provisioning = asyncio.Event()

        class _SlowProvision(_BlockingBackend):
            async def provision(self) -> None:
                provisioning.set()
                await asyncio.sleep(60)

        manager, token, _gov, backends = _stoppable(_SlowProvision)
        tool = CodingSandboxTool(manager)
        task = asyncio.ensure_future(tool.execute(code="print(1)", language="python"))
        await provisioning.wait()
        token.cancel()
        result = await task

        assert result.error.startswith("Stopped by the user")
        assert backends[0].last_request is None

    async def test_stop_before_the_call_takes_no_slot_and_uploads_nothing(self) -> None:
        manager, token, gov, backends = _stoppable()
        token.cancel()
        tool = CodingSandboxTool(manager)
        with stage_input_files({PARENT_RESULTS_INPUT_PATH: b"{}"}):
            result = await tool.execute(code="print(1)", language="python")

        assert result.error.startswith("Stopped by the user")
        assert backends == []
        assert gov.snapshot()["total"] == 0

    async def test_parallel_calls_all_stop_and_free_their_slots(self) -> None:
        manager, token, gov, backends = _stoppable()
        tool = CodingSandboxTool(manager)
        stopper = asyncio.ensure_future(_stop_when_started(token, backends, count=3))
        results = await asyncio.gather(*(
            tool.execute(code="while True: pass", language="python") for _ in range(3)
        ))
        await stopper

        assert all(r.error.startswith("Stopped by the user") for r in results)
        assert all(b.cleaned_up for b in backends)
        assert gov.snapshot()["total"] == 0


class TestDeniedSlotDropsAFreshSandbox:
    """A sandbox a call created but could not run on (no free slot) is torn
    down: its id is never returned, so nothing could reuse it."""

    @staticmethod
    def _full_manager() -> tuple:
        from app.agent_loop_lib.sandbox.governor import (
            GovernorLimits,
            SandboxResourceGovernor,
        )

        gov = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=1))
        backends: list = []

        def make() -> object:
            backends.append(_BlockingBackend())
            return backends[-1]

        manager = SandboxManager(governor=gov, slot_wait_s=0)
        manager.register_backend_factory(SandboxType.CODING, make)
        return manager, gov, backends

    async def _hold_the_only_slot(self, manager: SandboxManager, backends: list) -> asyncio.Future:
        """A fresh sandbox running until cancelled: it holds the governor's one slot."""
        before = len(backends)
        busy = asyncio.ensure_future(CodingSandboxTool(manager).execute(code="while True: pass", language="python"))
        for _ in range(200):
            if len(backends) > before and backends[before].started.is_set():
                break
            await asyncio.sleep(0.01)
        return busy

    async def test_run_code(self) -> None:
        manager, gov, backends = self._full_manager()
        busy = await self._hold_the_only_slot(manager, backends)

        result = await CodingSandboxTool(manager).execute(code="print(1)", language="python")

        assert result.success is False
        assert manager.active_count(SandboxType.CODING) == 1
        assert backends[1].last_request is None
        busy.cancel()
        await asyncio.gather(busy, return_exceptions=True)
        assert gov.snapshot()["total"] == 0

    async def test_install_packages(self) -> None:
        manager, gov, backends = self._full_manager()
        busy = await self._hold_the_only_slot(manager, backends)

        result = await InstallPackagesTool(manager).execute(packages=["pandas"], language="python")

        assert result.success is False
        assert manager.active_count(SandboxType.CODING) == 1
        busy.cancel()
        await asyncio.gather(busy, return_exceptions=True)

    async def test_a_reused_sandbox_is_kept(self) -> None:
        manager, gov, backends = self._full_manager()
        tool = CodingSandboxTool(manager)
        sandbox_id, _ = await manager.get_or_create(SandboxType.CODING, None)
        busy = await self._hold_the_only_slot(manager, backends)

        result = await tool.execute(code="print(1)", language="python", sandbox_id=sandbox_id)

        assert result.success is False
        assert manager.get(SandboxType.CODING, sandbox_id) is not None
        busy.cancel()
        await asyncio.gather(busy, return_exceptions=True)
