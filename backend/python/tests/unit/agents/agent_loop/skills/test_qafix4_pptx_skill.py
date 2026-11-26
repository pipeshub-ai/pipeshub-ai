"""QAFIX4 B3: the pptx skill's skeleton guards slide text against a literal backslash-n (QA-FP12 task 3 put
"MAKE THE SAFE\\nCHOICE THE EASY\\nCHOICE." on slide 3), shows how to break lines, and writes the deck to $OUTPUT_DIR.

The guard is the skeleton's own TypeScript, run under Node with its types stripped, so what the model copies is what
is tested."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SKILL = Path(__file__).parents[5] / "app/agents/agent_loop/skills/builtin_packs/pptx/SKILL.md"


def _skeleton() -> str:
    match = re.search(r"```typescript\n(.*?)```", SKILL.read_text(encoding="utf-8"), re.DOTALL)
    assert match is not None, "the skill has a TypeScript skeleton"
    return match.group(1)


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        if os.environ.get("PXH_REQUIRE_NODE") == "1":
            pytest.fail("PXH_REQUIRE_NODE=1 but node is not on PATH")
        pytest.skip("node is not on PATH")
    return node


def test_the_skeleton_breaks_lines_with_runs_and_writes_to_the_output_dir() -> None:
    skeleton = _skeleton()
    assert "breakLine: true" in skeleton
    assert "process.env.OUTPUT_DIR" in skeleton
    assert 'addText(text("' in skeleton and '{ text: text("' in skeleton
    assert 'addText("' not in skeleton and '{ text: "' not in skeleton, "every slide string goes through the guard"


@pytest.mark.parametrize(
    ("value", "refused"),
    [("MAKE THE SAFE\\nCHOICE THE EASY\\nCHOICE.", True), ("MAKE THE SAFE", False), ("line\nbreak", False)],
)
def test_the_guard_refuses_a_literal_backslash_n(value: str, refused: bool, tmp_path: Path) -> None:
    match = re.search(r"^function text\(.*?^}\n", _skeleton(), re.DOTALL | re.MULTILINE)
    assert match is not None, "the skeleton defines text()"
    program = tmp_path / "guard.ts"
    program.write_text(f"{match.group(0)}\nconsole.log(text({json.dumps(value)}));\n", encoding="utf-8")
    run = subprocess.run(  # a fixed node binary on a file this test wrote
        [_node(), "--experimental-strip-types", "--no-warnings", str(program)],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert (run.returncode != 0) is refused, run.stderr
    if refused:
        assert "literal backslash-n" in run.stderr
