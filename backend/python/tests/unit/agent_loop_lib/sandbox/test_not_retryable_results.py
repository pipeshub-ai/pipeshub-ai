"""Results the same call cannot fix are marked ``is_retryable: False``.

An out-of-memory kill and a timeout at the deployment's maximum run time say
"running the same code again will fail the same way" and "a larger timeout
will not be accepted"; the flag must agree with the words.
"""

from __future__ import annotations

import pytest

from app.agent_loop_lib.sandbox.coding.base import CodeResult, ErrorCategory
from app.agent_loop_lib.sandbox.coding.reflection import ReflectionEngine
from tests.unit.agent_loop_lib.tools.builtin.sandbox.test_coding_sandbox import (
    _capped_tool,
    _TimeoutBackend,
)


def _result(**kwargs: object) -> CodeResult:
    base = {"stdout": "", "stderr": "", "exit_code": 1, "language": "python", "duration_ms": 1.0}
    return CodeResult(**{**base, **kwargs})  # type: ignore[arg-type]


class TestOutOfMemory:
    @pytest.mark.parametrize("result", [
        _result(exit_code=137),
        _result(stderr="Traceback (most recent call last):\n  File \"main.py\", line 1\nMemoryError\n"),
        _result(language="typescript", stderr="FATAL ERROR: Reached heap limit Allocation failed - "
                "JavaScript heap out of memory"),
    ])
    def test_out_of_memory_is_not_retryable(self, result: CodeResult) -> None:
        analysis = ReflectionEngine(memory_limit_mb=512).analyze(result)
        assert analysis is not None and analysis.category == ErrorCategory.OUT_OF_MEMORY
        assert analysis.is_retryable is False

    def test_certain_oom_kill_is_not_retryable(self) -> None:
        analysis = ReflectionEngine(memory_limit_mb=512).out_of_memory("", certain=True)
        assert analysis.is_retryable is False

    def test_an_ordinary_error_stays_retryable(self) -> None:
        analysis = ReflectionEngine().analyze(_result(stderr="ValueError: bad\n"))
        assert analysis is not None and analysis.is_retryable is True


class TestTimeoutAtTheCap:
    async def test_timeout_at_the_cap_is_not_retryable(self) -> None:
        tool, _backend = _capped_tool(_TimeoutBackend)
        result = await tool.execute(code="while True: pass", language="python", timeout=900)
        assert result.data["error_analysis"]["is_retryable"] is False

    async def test_timeout_below_the_cap_stays_retryable(self) -> None:
        tool, _backend = _capped_tool(_TimeoutBackend)
        result = await tool.execute(code="while True: pass", language="python", timeout=60)
        assert result.data["error_analysis"]["is_retryable"] is True
