"""Tests for app.agent_loop_lib.sandbox.coding.reflection.ReflectionEngine.

Focused on the `input/`-missing-file safety net: a `FileNotFoundError`
(Python) or `ENOENT` (Node) whose missing path is under `input/` almost
always means the parent-results/artifact handoff file was never staged this
run (see `agent_loop_lib/tools/builtin/sandbox/input_staging.py`) — a normal
condition, not a logic bug — so it must get a targeted, actionable
suggestion instead of the generic RUNTIME one.
"""

from __future__ import annotations

import pytest

from app.agent_loop_lib.sandbox.coding.base import CodeResult, ErrorCategory
from app.agent_loop_lib.sandbox.coding.reflection import ReflectionEngine

_PY_MISSING_INPUT_TRACEBACK = (
    'Traceback (most recent call last):\n'
    '  File "/src/main.py", line 12, in <module>\n'
    "    with open('input/parent_tool_results.json', 'r', encoding='utf-8') as f:\n"
    "         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^\n"
    "FileNotFoundError: [Errno 2] No such file or directory: 'input/parent_tool_results.json'\n"
)


class TestMissingInputFileSuggestion:
    def test_python_missing_parent_results_file_gets_targeted_suggestion(self) -> None:
        result = CodeResult(
            stdout="", stderr=_PY_MISSING_INPUT_TRACEBACK, exit_code=1,
            language="python", duration_ms=1.0,
        )

        analysis = ReflectionEngine().analyze(result)

        assert analysis is not None
        assert analysis.category == ErrorCategory.RUNTIME
        assert "input/parent_tool_results.json" in analysis.suggestion
        assert "normal" in analysis.suggestion

    def test_python_other_file_not_found_keeps_generic_runtime_suggestion(self) -> None:
        """A missing file OUTSIDE `input/` (e.g. a typo'd output path) is a
        genuine logic error — must NOT get the "this is normal" framing."""
        traceback_text = (
            'Traceback (most recent call last):\n'
            '  File "/src/main.py", line 3, in <module>\n'
            "    open('output/report.csv')\n"
            "FileNotFoundError: [Errno 2] No such file or directory: 'output/report.csv'\n"
        )
        result = CodeResult(
            stdout="", stderr=traceback_text, exit_code=1, language="python", duration_ms=1.0,
        )

        analysis = ReflectionEngine().analyze(result)

        assert analysis is not None
        assert analysis.category == ErrorCategory.RUNTIME
        assert analysis.suggestion == "Inspect the stack trace for the failing line and correct the logic error."

    def test_typescript_enoent_on_input_path_gets_targeted_suggestion(self) -> None:
        stderr = (
            "Error: ENOENT: no such file or directory, open 'input/parent_tool_results.json'\n"
            "    at Object.openSync (node:fs:585:3)\n"
            "    at readFileSync (node:fs:453:35)\n"
            "    at Object.<anonymous> (/src/main.ts:3:14)\n"
        )
        result = CodeResult(
            stdout="", stderr=stderr, exit_code=1, language="typescript", duration_ms=1.0,
        )

        analysis = ReflectionEngine().analyze(result)

        assert analysis is not None
        assert "input/parent_tool_results.json" in analysis.suggestion
        assert "normal" in analysis.suggestion

    def test_unrelated_python_error_is_unaffected(self) -> None:
        result = CodeResult(
            stdout="", stderr="ZeroDivisionError: division by zero\n",
            exit_code=1, language="python", duration_ms=1.0,
        )

        analysis = ReflectionEngine().analyze(result)

        assert analysis is not None
        assert analysis.category == ErrorCategory.RUNTIME
        assert analysis.suggestion == "Inspect the stack trace for the failing line and correct the logic error."


def _result(*, exit_code: int, stderr: str = "", stdout: str = "", language: str = "python") -> CodeResult:
    return CodeResult(stdout=stdout, stderr=stderr, exit_code=exit_code, language=language, duration_ms=1.0)


class TestOutOfMemory:
    def test_python_memory_error_names_the_limit_and_both_causes(self) -> None:
        stderr = (
            'Traceback (most recent call last):\n'
            '  File "main.py", line 1, in <module>\n'
            "MemoryError\n"
        )
        analysis = ReflectionEngine(memory_limit_mb=512).analyze(_result(exit_code=1, stderr=stderr))
        assert analysis is not None
        assert analysis.category == ErrorCategory.OUT_OF_MEMORY
        assert "512 MB" in analysis.message
        assert "the server was short of memory" in analysis.message
        assert "chunks" in analysis.suggestion

    def test_numpy_array_memory_error(self) -> None:
        stderr = (
            "numpy.core._exceptions._ArrayMemoryError: Unable to allocate 74.5 GiB "
            "for an array with shape (100000, 100000) and data type float64\n"
        )
        analysis = ReflectionEngine(memory_limit_mb=512).analyze(_result(exit_code=1, stderr=stderr))
        assert analysis is not None
        assert analysis.category == ErrorCategory.OUT_OF_MEMORY

    def test_node_heap_out_of_memory(self) -> None:
        stderr = (
            "<--- Last few GCs --->\n"
            "FATAL ERROR: Reached heap limit Allocation failed - JavaScript heap out of memory\n"
        )
        analysis = ReflectionEngine(memory_limit_mb=512).analyze(
            _result(exit_code=134, stderr=stderr, language="typescript")
        )
        assert analysis is not None
        assert analysis.category == ErrorCategory.OUT_OF_MEMORY
        assert "512 MB" in analysis.message

    @pytest.mark.parametrize("exit_code", [137, -9])
    def test_sigkill_with_no_output_is_a_hedged_out_of_memory(self, exit_code: int) -> None:
        analysis = ReflectionEngine(memory_limit_mb=512).analyze(_result(exit_code=exit_code))
        assert analysis is not None
        assert analysis.category == ErrorCategory.OUT_OF_MEMORY
        assert analysis.message.startswith("The run was killed (SIGKILL)")
        assert "most likely" in analysis.message

    def test_unknown_limit_gives_the_hedged_message(self) -> None:
        analysis = ReflectionEngine().out_of_memory("", certain=True)
        assert analysis.category == ErrorCategory.OUT_OF_MEMORY
        assert "MB" not in analysis.message

    def test_timeout_wins_over_memory(self) -> None:
        analysis = ReflectionEngine(memory_limit_mb=512).analyze(
            _result(exit_code=-1, stderr="Execution timed out after 30s\nMemoryError")
        )
        assert analysis is not None
        assert analysis.category == ErrorCategory.TIMEOUT

    def test_value_error_is_still_runtime(self) -> None:
        analysis = ReflectionEngine(memory_limit_mb=512).analyze(
            _result(exit_code=1, stderr="ValueError: boom\n")
        )
        assert analysis is not None
        assert analysis.category == ErrorCategory.RUNTIME


class TestCpuTimeLimit:
    @pytest.mark.parametrize("exit_code", [-24, 152])
    def test_sigxcpu_is_a_timeout_with_the_cpu_note(self, exit_code: int) -> None:
        analysis = ReflectionEngine(memory_limit_mb=1536, cpu_limit_s=30).analyze(_result(exit_code=exit_code))
        assert analysis is not None
        assert analysis.category == ErrorCategory.TIMEOUT
        assert analysis.message == (
            "[sandbox] The program used up its CPU time limit (30 s of CPU). "
            "Make it faster, or split the work into smaller runs."
        )

    def test_sigkill_still_goes_to_the_memory_rule(self) -> None:
        analysis = ReflectionEngine(memory_limit_mb=1536, cpu_limit_s=30).analyze(_result(exit_code=-9))
        assert analysis is not None
        assert analysis.category == ErrorCategory.OUT_OF_MEMORY

    def test_without_a_cpu_limit_152_is_not_a_cpu_kill(self) -> None:
        analysis = ReflectionEngine().analyze(_result(exit_code=152))
        assert analysis is not None
        assert analysis.category != ErrorCategory.TIMEOUT
