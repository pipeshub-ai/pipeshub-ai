"""Tests for app.agent_loop_lib.sandbox.coding.executor.CodeExecutor.

Focused on the artifact-detection diff — a regression here silently turns
every code run's own entry script into a spurious "artifact" that gets
uploaded to blob storage and shown to the user as a download card.
"""

from __future__ import annotations

import os

import pytest

from app.agent_loop_lib.sandbox.coding.base import CodeRequest
from app.agent_loop_lib.sandbox.coding.environment import EnvironmentManager
from app.agent_loop_lib.sandbox.coding.executor import CodeExecutor


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
