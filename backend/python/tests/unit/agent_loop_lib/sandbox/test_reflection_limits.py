"""The process-limit, file-size-limit and out-of-memory messages of
`ReflectionEngine`: each names the limit that was hit instead of a generic
runtime error, and an allocation failure is not called a kill."""

from __future__ import annotations

import pytest

from app.agent_loop_lib.sandbox.coding.base import CodeResult, ErrorCategory
from app.agent_loop_lib.sandbox.coding.reflection import ReflectionEngine

_LOCAL = ReflectionEngine(
    memory_limit_mb=1536, cpu_limit_s=30, process_limit=2048,
    process_limit_shared=True, file_size_limit_mb=50,
)
_DOCKER = ReflectionEngine(memory_limit_mb=512, process_limit=256)


def _result(*, exit_code: int = 1, stderr: str = "", language: str = "python") -> CodeResult:
    return CodeResult(stdout="", stderr=stderr, exit_code=exit_code, language=language, duration_ms=1.0)


_PY_SUBPROCESS_EAGAIN = (
    "Traceback (most recent call last):\n"
    '  File "main.py", line 2, in <module>\n'
    '  File "/usr/lib/python3.12/subprocess.py", line 1955, in _execute_child\n'
    "    self.pid = _fork_exec(\n"
    "BlockingIOError: [Errno 11] Resource temporarily unavailable\n"
)


class TestProcessLimit:
    @pytest.mark.parametrize(
        ("stderr", "language"),
        [
            (_PY_SUBPROCESS_EAGAIN, "python"),
            ("Traceback (most recent call last):\nRuntimeError: can't start new thread\n", "python"),
            ("/bin/sh: fork: retry: Resource temporarily unavailable\n", "python"),
            ("sh: 1: Cannot fork\n", "python"),
            ("Error: spawn /usr/bin/ls EAGAIN\n    at ChildProcess._handle.onexit\n", "typescript"),
            ("Error: spawnSync /bin/sh EAGAIN\n", "typescript"),
            ("  #  Assertion failed: (0) == (uv_thread_create(t.get(), start_thread, this))\n", "typescript"),
            ("runtime: failed to create new OS thread (have 24 already; errno=11)\n", "typescript"),
        ],
    )
    def test_names_the_process_limit(self, stderr: str, language: str) -> None:
        analysis = _LOCAL.analyze(_result(stderr=stderr, language=language))
        assert analysis is not None
        assert analysis.message.startswith(
            "[sandbox] The run hit its process limit (2048 processes) and could not start "
            "another process or thread."
        )
        assert "logic error" not in analysis.suggestion
        assert "fewer processes or threads" in analysis.suggestion

    def test_local_says_the_limit_is_shared_with_the_server(self) -> None:
        analysis = _LOCAL.analyze(_result(stderr=_PY_SUBPROCESS_EAGAIN))
        assert analysis is not None
        assert "counts every process of the server's user" in analysis.message

    def test_docker_names_its_pids_limit_and_no_shared_note(self) -> None:
        analysis = _DOCKER.analyze(_result(stderr=_PY_SUBPROCESS_EAGAIN))
        assert analysis is not None
        assert analysis.message == (
            "[sandbox] The run hit its process limit (256 processes) and could not start "
            "another process or thread."
        )

    def test_unknown_limit_gives_no_number(self) -> None:
        analysis = ReflectionEngine().analyze(_result(stderr=_PY_SUBPROCESS_EAGAIN))
        assert analysis is not None
        assert analysis.message.startswith("[sandbox] The run hit its process limit and could not")

    def test_a_non_blocking_socket_eagain_is_not_a_process_limit(self) -> None:
        stderr = (
            "Traceback (most recent call last):\n"
            '  File "main.py", line 4, in <module>\n'
            "    sock.recv(10)\n"
            "BlockingIOError: [Errno 11] Resource temporarily unavailable\n"
        )
        analysis = _LOCAL.analyze(_result(stderr=stderr))
        assert analysis is not None
        assert "process limit" not in analysis.message


class TestFileSizeLimit:
    def test_python_efbig_names_the_limit(self) -> None:
        stderr = (
            "Traceback (most recent call last):\n"
            '  File "main.py", line 3, in <module>\n'
            "OSError: [Errno 27] File too large\n"
        )
        analysis = _LOCAL.analyze(_result(stderr=stderr))
        assert analysis is not None
        assert analysis.message == (
            "[sandbox] The run hit its file size limit (50 MB per file): a file it wrote grew too large."
        )
        assert "logic error" not in analysis.suggestion
        assert "smaller files" in analysis.suggestion

    def test_node_efbig(self) -> None:
        analysis = _LOCAL.analyze(
            _result(stderr="Error: EFBIG: file too large, write\n", language="typescript")
        )
        assert analysis is not None
        assert analysis.message.startswith("[sandbox] The run hit its file size limit (50 MB per file)")

    @pytest.mark.parametrize("exit_code", [-25, 153])
    def test_sigxfsz_names_the_limit(self, exit_code: int) -> None:
        analysis = _LOCAL.analyze(_result(exit_code=exit_code, language="typescript"))
        assert analysis is not None
        assert analysis.message.startswith("[sandbox] The run hit its file size limit")

    def test_without_a_file_size_limit_153_is_not_a_file_size_kill(self) -> None:
        analysis = _DOCKER.analyze(_result(exit_code=153))
        assert analysis is not None
        assert "file size limit" not in analysis.message


class TestOutOfMemoryWording:
    def test_memory_error_is_not_called_a_kill(self) -> None:
        stderr = 'Traceback (most recent call last):\n  File "main.py", line 1\nMemoryError\n'
        analysis = _LOCAL.analyze(_result(stderr=stderr))
        assert analysis is not None
        assert analysis.category == ErrorCategory.OUT_OF_MEMORY
        assert analysis.message == (
            "Out of memory: the run stopped with a MemoryError because it could not get more "
            "memory (its 1536 MB limit, or the server was short of memory)."
        )

    def test_node_heap_error_is_not_called_a_kill(self) -> None:
        stderr = "FATAL ERROR: Reached heap limit Allocation failed - JavaScript heap out of memory\n"
        analysis = _LOCAL.analyze(_result(exit_code=134, stderr=stderr, language="typescript"))
        assert analysis is not None
        assert "killed" not in analysis.message
        assert "1536 MB" in analysis.message

    def test_memory_error_without_a_known_limit(self) -> None:
        analysis = ReflectionEngine().analyze(_result(stderr="MemoryError\n"))
        assert analysis is not None
        assert analysis.message == (
            "Out of memory: the run stopped with a MemoryError because it could not get more memory."
        )

    def test_a_container_oom_kill_still_says_killed(self) -> None:
        analysis = _DOCKER.out_of_memory("", certain=True)
        assert analysis.message == (
            "Out of memory: the run was killed for using more memory than allowed "
            "(its 512 MB limit, or the server was short of memory)."
        )
