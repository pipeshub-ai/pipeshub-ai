"""Tests for app.agent_loop_lib.sandbox.coding.reflection.ReflectionEngine.

Focused on the `input/`-missing-file safety net: a `FileNotFoundError`
(Python) or `ENOENT` (Node) whose missing path is under `input/` almost
always means the parent-results/artifact handoff file was never staged this
run (see `agent_loop_lib/tools/builtin/sandbox/input_staging.py`) — a normal
condition, not a logic bug — so it must get a targeted, actionable
suggestion instead of the generic RUNTIME one.
"""

from __future__ import annotations

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
