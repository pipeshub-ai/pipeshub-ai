from __future__ import annotations

import re

from app.agent_loop_lib.sandbox.coding.base import (
    CodeResult,
    ErrorAnalysis,
    ErrorCategory,
)

"""`ReflectionEngine`: turns a failed `CodeResult` into a structured,
retry-friendly `ErrorAnalysis` — this is what makes self-correction
possible. Without it the agent would have to re-parse raw stdout/stderr
text on every retry; with it, the tool result carries a category + concrete
suggestion the model can act on directly.

Deliberately best-effort regex parsing, not a real TS/Python parser: good
enough to categorize the overwhelming majority of LLM-authored mistakes
(typos, missing imports, type errors, syntax errors) without adding a
`typescript`/`ast`-module dependency just for error messages.
"""

__all__ = ["ReflectionEngine"]

# `tsc --noEmit` diagnostic format: `main.ts(3,5): error TS2322: <message>`
_TSC_DIAG_RE = re.compile(r"(?P<file>[^\s()][^()]*)\((?P<line>\d+),(?P<column>\d+)\): error TS\d+: (?P<message>.+)")
# First `SomethingError: message` line in a Node stack trace.
_NODE_ERROR_RE = re.compile(r"^(?P<type>[A-Za-z]+Error): (?P<message>.+)$", re.MULTILINE)
# Node stack frames: `at fn (/path/main.ts:3:14)` or `at /path/main.ts:3:14`.
_NODE_FRAME_RE = re.compile(r"at (?:.*\()?(?P<file>[^\s():]+):(?P<line>\d+):(?P<column>\d+)\)?")
# Python's `ExceptionType: message` (last line of a traceback).
_PY_EXC_RE = re.compile(r"^(?P<type>[\w.]+):\s")
# Python's `File "main.py", line 3, in <module>`.
_PY_FRAME_RE = re.compile(r'File "(?P<file>[^"]+)", line (?P<line>\d+)')

_IMPORT_HINTS = ("cannot find module", "cannot find package", "no module named", "modulenotfounderror")


def _suggestion_for(category: ErrorCategory) -> str:
    return {
        ErrorCategory.IMPORT: "Install the missing package via install_packages, or check the import/require name for typos.",
        ErrorCategory.TYPE: "Fix the type mismatch described in the message before re-running.",
        ErrorCategory.SYNTAX: "Fix the syntax error described in the message before re-running.",
        ErrorCategory.PERMISSION: "The sandbox denied this operation — avoid filesystem/network access outside the sandbox's allowed scope.",
        ErrorCategory.TIMEOUT: "The code took too long to run — optimize it, reduce its scope, or increase the timeout.",
        ErrorCategory.RUNTIME: "Inspect the stack trace for the failing line and correct the logic error.",
        ErrorCategory.UNKNOWN: "Inspect stdout/stderr for details; the failure could not be automatically categorized.",
    }[category]


class ReflectionEngine:
    """Stateless — `analyze()` is a pure function of a `CodeResult`."""

    def analyze(self, result: CodeResult) -> ErrorAnalysis | None:
        if result.success:
            return None
        if result.exit_code == -1 and "timed out" in result.stderr.lower():
            return ErrorAnalysis(
                category=ErrorCategory.TIMEOUT,
                message=result.stderr.strip() or "Execution timed out",
                suggestion=_suggestion_for(ErrorCategory.TIMEOUT),
                is_retryable=True,
            )
        if result.language == "typescript":
            return self._analyze_typescript(result)
        return self._analyze_python(result)

    def _analyze_typescript(self, result: CodeResult) -> ErrorAnalysis:
        text = "\n".join(part for part in (result.stdout, result.stderr) if part)

        tsc_match = _TSC_DIAG_RE.search(text)
        if tsc_match:
            return ErrorAnalysis(
                category=ErrorCategory.TYPE,
                message=tsc_match.group("message").strip(),
                file=tsc_match.group("file").strip(),
                line=int(tsc_match.group("line")),
                column=int(tsc_match.group("column")),
                suggestion=_suggestion_for(ErrorCategory.TYPE),
                stack_trace=text,
                is_retryable=True,
            )

        # Import-hint check runs against the full text FIRST, regardless of
        # whether _NODE_ERROR_RE matched — Node's `require()`/ESM resolver
        # throws a plain `Error: Cannot find module '...'` (not a named
        # subclass like `TypeError`), so gating this check on a successful
        # node_match would miss the single most common import failure.
        lowered = text.lower()
        node_match = _NODE_ERROR_RE.search(text)
        if node_match:
            error_type = node_match.group("type")
            message = f"{error_type}: {node_match.group('message').strip()}"
        else:
            error_type = None
            message = _last_nonempty_line(text) or "Unknown TypeScript execution error"

        if error_type == "SyntaxError":
            category = ErrorCategory.SYNTAX
        elif any(hint in lowered for hint in _IMPORT_HINTS):
            category = ErrorCategory.IMPORT
        elif error_type is not None:
            category = ErrorCategory.RUNTIME
        else:
            category = ErrorCategory.UNKNOWN

        frame_match = _NODE_FRAME_RE.search(text)
        return ErrorAnalysis(
            category=category,
            message=message,
            file=frame_match.group("file") if frame_match else None,
            line=int(frame_match.group("line")) if frame_match else None,
            column=int(frame_match.group("column")) if frame_match else None,
            suggestion=_suggestion_for(category),
            stack_trace=text,
            is_retryable=True,
        )

    def _analyze_python(self, result: CodeResult) -> ErrorAnalysis:
        text = "\n".join(part for part in (result.stdout, result.stderr) if part)
        lines = [line for line in text.strip().splitlines() if line.strip()]
        last_line = lines[-1] if lines else "Unknown Python execution error"

        exc_match = _PY_EXC_RE.match(last_line)
        exc_type = exc_match.group("type") if exc_match else None
        if exc_type in ("SyntaxError", "IndentationError", "TabError"):
            category = ErrorCategory.SYNTAX
        elif exc_type in ("ImportError", "ModuleNotFoundError"):
            category = ErrorCategory.IMPORT
        elif exc_type == "PermissionError":
            category = ErrorCategory.PERMISSION
        elif exc_type is not None:
            category = ErrorCategory.RUNTIME
        else:
            category = ErrorCategory.UNKNOWN

        frame_match = None
        for line in reversed(lines):
            match = _PY_FRAME_RE.search(line)
            if match:
                frame_match = match
                break

        return ErrorAnalysis(
            category=category,
            message=last_line,
            file=frame_match.group("file") if frame_match else None,
            line=int(frame_match.group("line")) if frame_match else None,
            suggestion=_suggestion_for(category),
            stack_trace=text,
            is_retryable=True,
        )


def _last_nonempty_line(text: str) -> str | None:
    for line in reversed(text.strip().splitlines()):
        if line.strip():
            return line.strip()
    return None
