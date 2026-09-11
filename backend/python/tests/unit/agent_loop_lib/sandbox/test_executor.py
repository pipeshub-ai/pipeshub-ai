"""Tests for app.agent_loop_lib.sandbox.coding.executor.CodeExecutor.

Focused on the artifact-detection diff — a regression here silently turns
every code run's own entry script into a spurious "artifact" that gets
uploaded to blob storage and shown to the user as a download card.
"""

from __future__ import annotations


import pytest

from app.agent_loop_lib.sandbox.coding.base import CodeRequest
from app.agent_loop_lib.sandbox.coding.environment import EnvironmentManager
from app.agent_loop_lib.sandbox.coding.executor import CodeExecutor, ExecutionLimits


@pytest.fixture
def executor(tmp_path) -> CodeExecutor:
    working_dir = str(tmp_path)
    env = EnvironmentManager(working_dir)
    return CodeExecutor(working_dir, env)


class TestArtifactDetection:
    async def test_entry_file_itself_is_not_reported_as_an_artifact(self, executor: CodeExecutor) -> None:
        """The entry file (main.py) is written to disk on every run, between
        the before/after mtime snapshots, so a naive diff always flags it as
        "new" — it must be excluded, or every code execution would report
        its own script as a downloadable artifact."""
        result = await executor.execute(
            CodeRequest(code="print('hello')", language="python", timeout=15.0)
        )
        assert result.success
        assert "main.py" not in result.artifacts

    async def test_real_output_file_is_still_reported_as_an_artifact(self, executor: CodeExecutor) -> None:
        result = await executor.execute(
            CodeRequest(
                code="open('report.txt', 'w').write('hi')",
                language="python",
                timeout=15.0,
            )
        )
        assert result.success
        assert result.artifacts == ["report.txt"]

    async def test_custom_entry_file_is_also_excluded(self, executor: CodeExecutor) -> None:
        result = await executor.execute(
            CodeRequest(
                code="open('data.csv', 'w').write('a,b\\n1,2')",
                language="python",
                timeout=15.0,
                entry_file="custom_script.py",
            )
        )
        assert result.success
        assert "custom_script.py" not in result.artifacts
        assert "data.csv" in result.artifacts


class TestMemoryLimitDoesNotBreakJitRuntimes:
    """`RLIMIT_AS` caps VIRTUAL address space, which a JIT reserves far more
    of than it ever commits. V8 sizes that reservation from the limit it
    observes, so the outcome is non-monotonic — measured in the app image with
    tsx, 1.5 GB and 6 GB both start fine while 3 GB dies with
    "Fatal process out of memory: Failed to reserve virtual memory for
    CodeRange" (exit 133). There is no safe value to pick: it depends on the
    limit, the host's physical memory and the Node build.

    `RLIMIT_DATA` bounds the data segment and private anonymous mappings
    instead, which is what actually correlates with memory the sandbox uses.
    Verified in the same image: a hello-world runs under it at every limit
    tried, while a runaway 4 GB allocation is still killed (and Python gets
    `MemoryError` rather than completing).
    """

    def _applied_limits(self, limits: ExecutionLimits) -> dict:
        """The `(resource, value)` pairs the preexec fn would set."""
        import resource
        from unittest.mock import patch

        from app.agent_loop_lib.sandbox.coding.executor import _rlimit_preexec_fn

        captured: dict = {}
        with patch.object(
            resource, "setrlimit",
            side_effect=lambda res, val: captured.__setitem__(res, val),
        ):
            _rlimit_preexec_fn(limits)()
        return captured

    def test_address_space_is_not_capped(self) -> None:
        import resource

        applied = self._applied_limits(ExecutionLimits())
        assert resource.RLIMIT_AS not in applied, (
            "capping RLIMIT_AS makes V8 fail to reserve its CodeRange"
        )

    def test_memory_is_still_capped(self) -> None:
        """Dropping RLIMIT_AS must not mean dropping the cap entirely."""
        import resource

        applied = self._applied_limits(ExecutionLimits(max_memory_bytes=1234))
        assert applied.get(resource.RLIMIT_DATA) == (1234, 1234)

    def test_the_other_limits_are_unchanged(self) -> None:
        import resource

        applied = self._applied_limits(
            ExecutionLimits(max_cpu_seconds=7, max_file_size_bytes=99, max_processes=5)
        )
        assert applied.get(resource.RLIMIT_CPU) == (7, 7)
        assert applied.get(resource.RLIMIT_FSIZE) == (99, 99)
        assert applied.get(resource.RLIMIT_NPROC) == (5, 5)
