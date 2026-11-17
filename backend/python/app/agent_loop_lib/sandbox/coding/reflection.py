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

# Node aborts with one of these when V8 cannot grow its heap. It sizes the
# heap from the cgroup limit, so in a container it often dies this way
# before the kernel's OOM killer gets to it.
_NODE_OOM_MARKERS = (
    "javascript heap out of memory",
    "fatal process out of memory",
    "reached heap limit",
    "allocation failed - process out of memory",
)
# Exit statuses of a SIGKILL: 137 from a container or a shell (128 + 9),
# -9 from a subprocess the host waited on directly.
_SIGKILL_EXIT_CODES = (137, -9)
# SIGXCPU (RLIMIT_CPU's soft limit): -24 from a direct subprocess, 152 when a
# wrapper such as bwrap reports it as 128 + 24.
_CPU_LIMIT_EXIT_CODES = (-24, 152)
# SIGXFSZ (RLIMIT_FSIZE): -25 from a direct subprocess, 153 through a wrapper.
# Python ignores SIGXFSZ, so a Python run gets EFBIG ("File too large") instead.
_FILE_SIZE_EXIT_CODES = (-25, 153)
_FILE_SIZE_MARKERS = ("[errno 27] file too large", "efbig")
# A process or thread could not be started because the process limit
# (RLIMIT_NPROC, or a container's pids limit) was reached: Python's thread
# error, a shell's fork error, Node's spawn EAGAIN, and Node or a Go tool
# (esbuild) aborting when it cannot create one of its own threads.
_PROCESS_LIMIT_RE = re.compile(
    r"can't start new thread"
    r"|\bfork: (?:retry: )?resource temporarily unavailable"
    r"|\bcannot fork\b"
    r"|\bspawn(?:sync)?\b[^\n]*\beagain\b"
    r"|assertion failed: \(0\) == \(uv_thread_create"
    r"|failed to create new os thread",
)
# Python's fork/spawn failure is a plain `BlockingIOError: [Errno 11]`, which a
# non-blocking socket can also raise; only count it when the traceback shows a
# process being started.
_PY_FORK_FRAMES = ("_execute_child", "_fork_exec", "fork_exec", "os.fork", "posix_spawn", "popen_fork")

_OUT_OF_MEMORY_SUGGESTION = (
    "Reduce peak memory and run again: process the data in chunks or stream it instead "
    "of loading it all at once (for example pandas read_csv(chunksize=...)), use smaller "
    "dtypes, avoid keeping several copies of large data, and delete large objects once "
    "you are done with them. Running the same code again will fail the same way."
)

# A `FileNotFoundError` (Python) / `ENOENT` (Node) whose missing path is
# under `input/` almost always means "no data was staged into this sandbox
# this run" (see `agent_loop_lib/tools/builtin/sandbox/input_staging.py`) —
# a normal, expected condition, not a logic bug. `run_code`'s own
# description already says to guard this read (see `coding_sandbox.py`'s
# `_PARENT_RESULTS_NOTE`/`_NO_PARENT_RESULTS_NOTE`), but a model that
# skipped the guard anyway still deserves an actionable suggestion on retry
# rather than the generic "inspect the stack trace" — this is that safety
# net, independent of which mode/description the model happened to see.
_MISSING_INPUT_FILE_RE = re.compile(
    r"(?:no such file or directory|enoent)[^'\"\n]*['\"](?P<path>input/[^'\"\n]+)['\"]",
    re.IGNORECASE,
)


def _missing_input_file_suggestion(text: str) -> str | None:
    match = _MISSING_INPUT_FILE_RE.search(text)
    if match is None:
        return None
    return (
        f"`{match.group('path')}` does not exist in this sandbox — that is normal, not a bug: "
        "files under `input/` are only present when the calling agent explicitly shared or "
        "staged that data for this run. Guard the read (check existence first, or catch the "
        "not-found error) instead of assuming the file is always there; if you genuinely need "
        "that data, obtain it explicitly (a tool call, or a literal embedded directly in your "
        "code) rather than relying on an unconditional read of a fixed path."
    )


def _suggestion_for(category: ErrorCategory) -> str:
    return {
        ErrorCategory.IMPORT: "Install the missing package via install_packages, or check the import/require name for typos.",
        ErrorCategory.TYPE: "Fix the type mismatch described in the message before re-running.",
        ErrorCategory.SYNTAX: "Fix the syntax error described in the message before re-running.",
        ErrorCategory.PERMISSION: "The sandbox denied this operation — avoid filesystem/network access outside the sandbox's allowed scope.",
        ErrorCategory.TIMEOUT: "The code took too long to run — optimize it, reduce its scope, or increase the timeout.",
        ErrorCategory.OUT_OF_MEMORY: _OUT_OF_MEMORY_SUGGESTION,
        ErrorCategory.RUNTIME: "Inspect the stack trace for the failing line and correct the logic error.",
        ErrorCategory.UNKNOWN: "Inspect stdout/stderr for details; the failure could not be automatically categorized.",
    }[category]


class ReflectionEngine:
    """Stateless — `analyze()` is a pure function of a `CodeResult`.

    `memory_limit_mb`, `cpu_limit_s`, `process_limit` and
    `file_size_limit_mb` are the backend's per-run limits, named in the
    matching messages. `None` means the backend does not know them (E2B),
    and the messages say less. `process_limit_shared` is True when the
    process limit counts every process of the server's user (RLIMIT_NPROC),
    not only this run's.
    """

    def __init__(
        self,
        memory_limit_mb: int | None = None,
        cpu_limit_s: float | None = None,
        *,
        process_limit: int | None = None,
        process_limit_shared: bool = False,
        file_size_limit_mb: float | None = None,
    ) -> None:
        self._memory_limit_mb = memory_limit_mb
        self._cpu_limit_s = cpu_limit_s
        self._process_limit = process_limit
        self._process_limit_shared = process_limit_shared
        self._file_size_limit_mb = file_size_limit_mb

    def out_of_memory(self, text: str, *, certain: bool, error: str | None = None) -> ErrorAnalysis:
        """The run ran out of memory. `certain` is False when all that is
        known is that it was SIGKILLed: memory is then only the likely cause.
        `error` names what the run itself reported (a `MemoryError`, a Node
        heap error) when it was not killed but failed an allocation.

        The certain message names both causes, because the kernel reports a
        kill the same way whether the run went over its own limit or the
        whole server was short of memory.
        """
        causes = (
            f" (its {self._memory_limit_mb} MB limit, or the server was short of memory)"
            if self._memory_limit_mb is not None else ""
        )
        if certain and error is not None:
            message = f"Out of memory: the run stopped with {error} because it could not get more memory{causes}."
        elif certain and self._memory_limit_mb is not None:
            message = f"Out of memory: the run was killed for using more memory than allowed{causes}."
        else:
            message = "The run was killed (SIGKILL) before it finished, most likely because it ran out of memory."
        return ErrorAnalysis(
            category=ErrorCategory.OUT_OF_MEMORY,
            message=message,
            suggestion=_OUT_OF_MEMORY_SUGGESTION,
            stack_trace=text or None,
            # The same code runs out of memory again; only changed code helps.
            is_retryable=False,
        )

    def cpu_time_exceeded(self, text: str) -> ErrorAnalysis:
        """The run used up its CPU-time rlimit (local backend), which is
        separate from, and often shorter than, its wall-clock timeout."""
        limit = f"{self._cpu_limit_s:g} s of CPU" if self._cpu_limit_s is not None else "CPU time"
        return ErrorAnalysis(
            category=ErrorCategory.TIMEOUT,
            message=(
                f"[sandbox] The program used up its CPU time limit ({limit}). "
                "Make it faster, or split the work into smaller runs."
            ),
            suggestion=(
                "Make the code faster, or split the work into smaller runs. A larger "
                "timeout does not help: the limit counts CPU time, not wall-clock time."
            ),
            stack_trace=text or None,
            is_retryable=True,
        )

    def process_limit_hit(self, text: str) -> ErrorAnalysis:
        """The run could not start another process or thread: it reached its
        process limit (RLIMIT_NPROC locally, the pids limit in a container)."""
        limit = f" ({self._process_limit} processes)" if self._process_limit is not None else ""
        shared = (
            " The limit counts every process of the server's user, not only this run's."
            if self._process_limit_shared else ""
        )
        return ErrorAnalysis(
            category=ErrorCategory.RUNTIME,
            message=f"[sandbox] The run hit its process limit{limit} and could not start another process or thread.{shared}",
            suggestion=(
                "Start fewer processes or threads (for example a smaller worker pool), "
                "or do the work in one process."
            ),
            stack_trace=text or None,
            is_retryable=True,
        )

    def file_size_limit_hit(self, text: str) -> ErrorAnalysis:
        """The run wrote a file past its per-file size limit (RLIMIT_FSIZE)."""
        limit = f" ({self._file_size_limit_mb:g} MB per file)" if self._file_size_limit_mb is not None else ""
        return ErrorAnalysis(
            category=ErrorCategory.RUNTIME,
            message=f"[sandbox] The run hit its file size limit{limit}: a file it wrote grew too large.",
            suggestion=(
                "Write smaller files: split the output into several files, compress it, "
                "or keep only the part you need."
            ),
            stack_trace=text or None,
            is_retryable=True,
        )

    def disk_quota_hit(self, text: str, limit: str) -> ErrorAnalysis:
        """The run was stopped for writing more than its disk quota."""
        return ErrorAnalysis(
            category=ErrorCategory.RUNTIME,
            message=f"[sandbox] The run was stopped because it wrote more than {limit} to its disk.",
            suggestion=(
                "Write less data: process it in chunks, stream it instead of saving it, "
                "or keep only the part you need."
            ),
            stack_trace=text or None,
            # The same code writes the same amount again.
            is_retryable=False,
        )

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
        text = "\n".join(part for part in (result.stdout, result.stderr) if part)
        if self._cpu_limit_s is not None and result.exit_code in _CPU_LIMIT_EXIT_CODES:
            return self.cpu_time_exceeded(text)
        if self._file_size_limit_mb is not None and result.exit_code in _FILE_SIZE_EXIT_CODES:
            return self.file_size_limit_hit(text)
        lowered = text.lower()
        if any(marker in lowered for marker in _NODE_OOM_MARKERS):
            return self.out_of_memory(text, certain=True, error="a JavaScript heap out-of-memory error")
        if result.language != "typescript" and _python_memory_error(text):
            return self.out_of_memory(text, certain=True, error="a MemoryError")
        if _process_limit_reached(lowered):
            return self.process_limit_hit(text)
        if any(marker in lowered for marker in _FILE_SIZE_MARKERS):
            return self.file_size_limit_hit(text)
        if result.exit_code in _SIGKILL_EXIT_CODES:
            return self.out_of_memory(text, certain=False)
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
            suggestion=_missing_input_file_suggestion(text) or _suggestion_for(category),
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
            suggestion=_missing_input_file_suggestion(text) or _suggestion_for(category),
            stack_trace=text,
            is_retryable=True,
        )


def _process_limit_reached(lowered: str) -> bool:
    if _PROCESS_LIMIT_RE.search(lowered):
        return True
    return "[errno 11]" in lowered and any(frame in lowered for frame in _PY_FORK_FRAMES)


def _python_memory_error(text: str) -> bool:
    """The traceback's last line is a `MemoryError`, including subclasses
    such as numpy's `_ArrayMemoryError`. A bare `MemoryError` has no
    message, so it has no `: ` for `_PY_EXC_RE` to match."""
    last = _last_nonempty_line(text)
    if last is None:
        return False
    exc_type = last.split(":", 1)[0].strip()
    return bool(re.fullmatch(r"[\w.]*MemoryError", exc_type))


def _last_nonempty_line(text: str) -> str | None:
    for line in reversed(text.strip().splitlines()):
        if line.strip():
            return line.strip()
    return None
