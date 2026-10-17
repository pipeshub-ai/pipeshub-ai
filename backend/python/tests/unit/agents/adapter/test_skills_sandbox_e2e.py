"""End-to-end (no Docker, no mocks) proof that `load_skill` -> `run_code`
actually works against a REAL builtin skill's bundled scripts, using
`LocalCodingSandbox` like the sandbox contract suite
(`tests/unit/agent_loop_lib/sandbox/contract/`).

This is the scenario the whole `bundle.py`/`SkillBundleResolver` design in
the plan exists for: `office-utils`'s SKILL.md instructs the model to run
`python skills/office-utils/scripts/unpack.py` — this test proves that path
is real inside a freshly-created sandbox after one `load_skill` call, and
that the resulting artifact round-trips through `pack.py` back into
`$OUTPUT_DIR`."""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.core.exceptions import RegistryError
from app.agent_loop_lib.modules.providers.skills.filesystem_store import (
    FilesystemSkillStore,
)
from app.agent_loop_lib.sandbox.coding.local import LocalCodingSandbox
from app.agent_loop_lib.sandbox.manager import SandboxManager, SandboxType
from app.agent_loop_lib.tools.builtin.data.skills import LoadSkillTool
from app.agent_loop_lib.tools.builtin.sandbox import input_staging
from app.agent_loop_lib.tools.builtin.sandbox.coding_sandbox import CodingSandboxTool
from app.agent_loop_lib.tools.builtin.sandbox.input_staging import (
    peek_staged_skill_resources,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.agent_loop_lib.modules.providers.skills.base import Skill

pytestmark = pytest.mark.timeout(120, method="thread")

_BUILTIN_PACKS_ROOT = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..",
    "app", "agents", "agent_loop", "skills", "builtin_packs",
)


@pytest.fixture(autouse=True)
def _reset_staged_skill_resources() -> Iterator[None]:
    token = input_staging._staged_skill_resources.set(None)
    yield
    input_staging._staged_skill_resources.reset(token)


@pytest.fixture
def short_tmp_dir() -> Iterator[str]:
    """See `sandbox/contract/conftest.py`'s fixture of the same name: a
    short path is required because the local backend's IPC socket path is
    capped by `sockaddr_un`, and pytest's own `tmp_path` is already too
    long on macOS."""
    path = tempfile.mkdtemp(prefix="alcs-skills-")
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


class _FilesystemBackedSkillManager:
    """The minimal surface `LoadSkillTool`/`SkillBundleResolver` need
    (`activate_skill`, `get_resources`) implemented directly over a REAL
    `FilesystemSkillStore` pointed at the actual in-repo `builtin_packs/`
    directory — this test exercises the real `office-utils` SKILL.md and
    scripts, not a synthetic fixture, without pulling in the rest of
    `SkillManager` (index/tracker/governor) that this test has no use for."""

    def __init__(self, store: FilesystemSkillStore) -> None:
        self._store = store

    async def activate_skill(self, name: str, session_id: str | None = None) -> Skill:
        skill = await self._store.get_skill(name)
        if skill is None:
            raise RegistryError(f"Skill {name!r} not found")
        return skill

    async def get_resources(self, name: str) -> dict[str, str]:
        return await self._store.get_resources(name)


@pytest.fixture
def office_utils_manager() -> _FilesystemBackedSkillManager:
    store = FilesystemSkillStore(_BUILTIN_PACKS_ROOT)
    assert "office-utils" in set(store._locations), (
        "builtin_packs/office-utils fixture directory not found — check _BUILTIN_PACKS_ROOT"
    )
    return _FilesystemBackedSkillManager(store)


@pytest.fixture
async def sandbox_manager(short_tmp_dir: str) -> Iterator[SandboxManager]:
    if shutil.which("node") is None:
        pytest.skip("node not on PATH")
    manager = SandboxManager()
    manager.register_backend_factory(
        SandboxType.CODING,
        lambda: LocalCodingSandbox(working_dir=os.path.join(short_tmp_dir, "sandbox")),
    )
    yield manager


class TestLoadSkillStagesRealOfficeUtilsScripts:
    async def test_load_skill_stages_skill_md_and_both_scripts(
        self, office_utils_manager: _FilesystemBackedSkillManager,
    ) -> None:
        tool = LoadSkillTool(office_utils_manager)

        result = await tool.execute(name="office-utils")

        assert result.success
        assert result.data["sandbox_root"] == "skills/office-utils"
        staged = peek_staged_skill_resources()
        assert staged is not None
        assert "skills/office-utils/SKILL.md" in staged
        assert "skills/office-utils/scripts/unpack.py" in staged
        assert "skills/office-utils/scripts/pack.py" in staged


class TestRunCodeExecutesAStagedBuiltinScript:
    async def test_unpack_then_pack_round_trip_produces_an_artifact(
        self, office_utils_manager: _FilesystemBackedSkillManager, sandbox_manager: SandboxManager,
    ) -> None:
        load_tool = LoadSkillTool(office_utils_manager)
        load_result = await load_tool.execute(name="office-utils")
        assert load_result.success

        run_tool = CodingSandboxTool(sandbox_manager)
        script = """
import os
import subprocess
import sys
import zipfile

fixture = "fixture.docx"
with zipfile.ZipFile(fixture, "w") as zf:
    zf.writestr("[Content_Types].xml", "<Types/>")
    zf.writestr(
        "word/document.xml",
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body/></w:document>",
    )

subprocess.run(
    [sys.executable, "skills/office-utils/scripts/unpack.py", fixture, "unpacked"],
    check=True,
)
assert os.path.isfile("unpacked/_unpack_manifest.json")
assert os.path.isfile("unpacked/word/document.xml")

out_path = os.path.join(os.environ["OUTPUT_DIR"], "roundtrip.docx")
subprocess.run(
    [sys.executable, "skills/office-utils/scripts/pack.py", "unpacked", out_path],
    check=True,
)
print("round-trip complete")
"""

        result = await run_tool.execute(code=script, language="python")

        assert result.success, result.data
        assert result.data["exit_code"] == 0, result.data.get("stderr")
        assert "round-trip complete" in result.data["stdout"]
        assert any(a.endswith("roundtrip.docx") for a in result.data["artifacts"])
        assert "skills/office-utils/scripts/unpack.py" in result.data["input_files"]

    async def test_a_fresh_sandbox_with_no_load_skill_call_has_no_staged_files(
        self, sandbox_manager: SandboxManager,
    ) -> None:
        """No leak between test runs / agent turns: a context that never
        called `load_skill` must see nothing staged."""
        assert peek_staged_skill_resources() is None

        run_tool = CodingSandboxTool(sandbox_manager)
        result = await run_tool.execute(
            code="import os; assert not os.path.isdir('skills'); print('clean')",
            language="python",
        )

        assert result.success
        assert result.data["exit_code"] == 0
        assert "clean" in result.data["stdout"]
