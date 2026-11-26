"""IN-2, IN-4 (SK1): the skills a user installed are the pinned ones, and none of them is in this repository.

IN-2 runs when ``SK1_SKILLS_DIR`` (or ``SK1_SKILLS_DIR_<ENGINE>``) names the skills manager's output; IN-4 always
runs: the skills' licence forbids copies, so no tracked file may carry their licence text or the bytes of any
file the manifest lists.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.unit.harness.engines.skill_packs import (
    LICENCE_FIRST_LINE,
    SKILL_NAMES,
    installed_dir,
    manifest,
    tree_mismatch,
)

REPO = Path(__file__).resolve().parents[5]
# Short files (an empty __init__.py) say nothing about where their bytes came from.
MIN_TELLING_SIZE = 64


@pytest.mark.parametrize("engine", ["codex", "opencode"])
def test_in2_the_installed_tree_is_the_pinned_one(engine: str) -> None:
    root = installed_dir(engine)
    if root is None:
        pytest.skip("SK1_SKILLS_DIR names no installed skills (the skills are not in this repository)")
    assert tree_mismatch(root) == []
    assert sorted({p.split("/", 1)[0] for p in manifest()["files"]}) == list(SKILL_NAMES)


def test_in2_a_changed_or_extra_file_is_refused(tmp_path: Path) -> None:
    root = installed_dir("codex") or installed_dir("opencode")
    if root is None:
        pytest.skip("SK1_SKILLS_DIR names no installed skills")
    copy = tmp_path / "skills"
    shutil.copytree(root, copy)
    with (copy / "pdf" / "SKILL.md").open("ab") as handle:
        handle.write(b"\n")
    (copy / "pptx" / "extra.md").write_text("not installed by the skills manager")
    assert tree_mismatch(copy) == ["pdf/SKILL.md", "pptx/extra.md"]


def test_in4_no_skill_file_or_licence_text_is_in_the_repository() -> None:
    tracked = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z"], capture_output=True, check=True, timeout=60
    ).stdout.decode().split("\0")
    pinned = {e["sha256"] for e in manifest()["files"].values() if e["size"] >= MIN_TELLING_SIZE}
    copies: list[str] = []
    licence: list[str] = []
    for name in filter(None, tracked):
        path = REPO / name
        if not path.is_file() or path.is_symlink():
            continue
        data = path.read_bytes()
        if len(data) >= MIN_TELLING_SIZE and hashlib.sha256(data).hexdigest() in pinned:
            copies.append(name)
        if LICENCE_FIRST_LINE.encode() in data and not name.endswith(("SK1-skill-packs.md", "skill_packs.py")):
            licence.append(name)
    assert (copies, licence) == ([], [])
