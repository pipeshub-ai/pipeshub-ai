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

from app.agent_loop_lib.sandbox.coding.base import CodeRequest, CodeResult
from app.agent_loop_lib.sandbox.manager import SandboxManager, SandboxType
from app.agent_loop_lib.tools.builtin.sandbox.coding_sandbox import (
    CodingSandboxTool,
    detect_language_mismatch,
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
