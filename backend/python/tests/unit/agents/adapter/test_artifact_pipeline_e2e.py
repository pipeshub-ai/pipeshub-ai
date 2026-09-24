"""End-to-end artifact pipeline over a real sandbox.

The unit tests in `test_sandbox_bridge.py` drive the artifact hooks with
fake backends, which cannot catch a break in the parts that only exist for
real: `$OUTPUT_DIR` resolving to a real directory, files surviving the
container being removed, and staged bytes actually landing in a fresh
sandbox's filesystem.

Runs against `local` always and against a real Docker daemon when one is
available — the results must be identical, because swapping the backend is
a config change.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.agent_loop_lib.sandbox.governor import reset_default_governor
from app.agent_loop_lib.sandbox.manager import SandboxType
from app.agent_loop_lib.tools.builtin.sandbox.input_staging import (
    peek_staged_input_files,
    stage_input_files,
)
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.sandbox_bridge import (
    _partition_sandbox_outputs,
    build_coding_sandbox_manager,
    register_coding_sandbox_tools,
)

from ...agent_loop_lib.sandbox.contract.conftest import (
    DOCKER_TEST_IMAGE,
    docker_available,
)

pytestmark = pytest.mark.timeout(300, method="thread")

_GENERATE = """
import os
out = os.environ["OUTPUT_DIR"]
open(os.path.join(out, "report.csv"), "w").write("name,value\\nalpha,1\\nbeta,2\\n")
open("scratch_note.txt", "w").write("intermediate")
print("generated")
"""

_CONSUME = """
import os
p = "input/report.csv"
print("staged_exists:" + str(os.path.exists(p)))
print("staged_content:" + open(p).read().strip().replace(chr(10), "|"))
"""


def _context() -> AgentContext:
    return AgentContext(
        org_id="org-e2e",
        user_id="user-e2e",
        user_email="e2e@example.com",
        conversation_id="conv-e2e",
        logger=MagicMock(),
        retrieval_service=MagicMock(config_service=MagicMock()),
    )


@pytest.fixture(params=["local", "docker"], autouse=False)
def sandbox_mode(request, monkeypatch):
    if request.param == "docker":
        available, reason = docker_available()
        if not available:
            pytest.skip(reason)
        monkeypatch.setenv("SANDBOX_MODE", "docker")
        monkeypatch.setenv("SANDBOX_DOCKER_IMAGE", DOCKER_TEST_IMAGE)
    else:
        monkeypatch.delenv("SANDBOX_MODE", raising=False)
    reset_default_governor()
    yield request.param
    reset_default_governor()


class TestArtifactPipeline:
    async def test_generate_partition_download_and_restage(
        self, sandbox_mode: str,
    ) -> None:
        manager = await build_coding_sandbox_manager(
            ctx=_context(), allow_network=False,
        )
        tools = ToolRegistry()
        register_coding_sandbox_tools(tools, manager, allow_network=False)
        run_code = tools.resolve_by_name("run_code")
        read_file = tools.resolve_by_name("read_sandbox_file")

        try:
            # 1. A run produces a deliverable and a scratch file.
            produced = await run_code.execute(
                code=_GENERATE, language="python", timeout=120,
            )
            assert produced.success, produced.error
            artifacts = produced.data["artifacts"]
            assert any("report.csv" in a for a in artifacts), artifacts

            # 2. The split the POST hook uses to decide what to register.
            deliverables, scratch = _partition_sandbox_outputs(artifacts)
            assert any("report.csv" in d for d in deliverables)
            assert any("scratch_note.txt" in s for s in scratch)

            # 3. Bytes fetched back out — the container is gone by now, so
            #    this is what makes a deliverable survive at all.
            sandbox_id = produced.data["sandbox_id"]
            _, backend = await manager.get_or_create(SandboxType.CODING, sandbox_id)
            raw = await backend.download_file(deliverables[0])
            assert raw == b"name,value\nalpha,1\nbeta,2\n"

            # 4. The model spells paths as `$OUTPUT_DIR/...`; nothing expands
            #    a tool argument, so the backend has to normalise it.
            read_out = await read_file.execute(
                path="$OUTPUT_DIR/report.csv", sandbox_id=sandbox_id,
            )
            assert read_out.success, read_out.error

            # 5. That artifact staged back into a FRESH sandbox — reused
            #    sandboxes are deliberately left alone, so no sandbox_id here.
            with stage_input_files({"input/report.csv": raw}):
                assert peek_staged_input_files() == {"input/report.csv": raw}
                consumed = await run_code.execute(
                    code=_CONSUME, language="python", timeout=120,
                )
            assert consumed.success, consumed.error
            stdout = consumed.data["stdout"]
            assert "staged_exists:True" in stdout, stdout
            assert "alpha,1" in stdout, stdout
        finally:
            await manager.destroy_all()

        assert manager.active_count(SandboxType.CODING) == 0
        assert manager._governor.snapshot()["total"] == 0

    async def test_prior_deliverable_not_re_reported_on_the_next_call(
        self, sandbox_mode: str,
    ) -> None:
        """`output/` persists across calls on one sandbox, so a naive listing
        would re-report the first run's file every time and the bridge would
        register the same artifact again on each turn."""
        manager = await build_coding_sandbox_manager(
            ctx=_context(), allow_network=False,
        )
        tools = ToolRegistry()
        register_coding_sandbox_tools(tools, manager, allow_network=False)
        run_code = tools.resolve_by_name("run_code")

        try:
            first = await run_code.execute(
                code=_GENERATE, language="python", timeout=120,
            )
            assert first.success, first.error
            sandbox_id = first.data["sandbox_id"]

            second = await run_code.execute(
                code='print("nothing new")',
                language="python",
                timeout=120,
                sandbox_id=sandbox_id,
            )
            assert second.success, second.error
            assert not any(
                "report.csv" in a for a in second.data["artifacts"]
            ), second.data["artifacts"]
        finally:
            await manager.destroy_all()
