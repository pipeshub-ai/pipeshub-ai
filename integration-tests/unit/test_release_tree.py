"""WP88-FU-2: what a release tree's own virtualenv installs (``harness/fault/release_tree.py``), without Docker.

A release from before the locked requirements declares its dependencies in ``pyproject.toml`` only. Its virtualenv
holds what it declares and this tree's virtualenv no longer has, from a lock in which every file has a sha256.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "harness" / "fault"))

import release_tree  # noqa: E402

REPO = Path(__file__).resolve().parents[2]

PYPROJECT = """
[project]
name = "release"
dependencies = [
    "python-jose==3.4.0",
    "Fast_MCP==3.2.0",
    "present==1.0",
    "etcd3 @ git+https://github.com/kragniz/python-etcd3.git@e58a899579ba416449c4e225b61f039457c8072a",
]
"""


def _site(tmp_path: Path, *names: str) -> Path:
    for name in names:
        info = tmp_path / f"{name}-1.0.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n")
    return tmp_path


def test_a_release_gets_what_it_declares_and_this_tree_dropped(tmp_path: Path) -> None:
    base = _site(tmp_path, "present", "etcd3")
    assert release_tree.dropped_requirements(PYPROJECT, base) == ["python-jose==3.4.0", "Fast_MCP==3.2.0"]


def test_names_compare_canonically_and_a_vcs_pin_is_left_to_this_tree(tmp_path: Path) -> None:
    base = _site(tmp_path, "present", "fast-mcp", "python_jose")
    assert release_tree.dropped_requirements(PYPROJECT, base) == []


def _install(name: str, version: str, sha: str | None) -> dict[str, object]:
    archive = {"hashes": {"sha256": sha}} if sha is not None else {}
    return {"metadata": {"name": name, "version": version}, "download_info": {"archive_info": archive}}


def test_the_lock_pins_every_file_by_its_sha256_sorted() -> None:
    report = {"install": [_install("mcp", "1.30.0", "bb"), _install("Python_Jose", "3.4.0", "aa")]}
    assert release_tree.lock_lines(report) == [
        "mcp==1.30.0 --hash=sha256:bb",
        "python-jose==3.4.0 --hash=sha256:aa",
    ]


def test_a_file_without_a_sha256_is_refused() -> None:
    with pytest.raises(RuntimeError, match="no sha256"):
        release_tree.lock_lines({"install": [_install("mcp", "1.30.0", None)]})


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=False)


def test_fuperf_fu1_the_previous_release_postdates_every_feature_its_rollbacks_exercise() -> None:
    """The N-1 tree holds CPD-10 and PERF-B, and this build descends from it: a rollback onto it is a rollback the
    skew window promises, never one onto a tree from before the feature."""
    previous = release_tree.previous_release()
    ref = str(previous["gitRef"])
    postdates = dict(previous["postdates"])  # type: ignore[call-overload]
    assert set(postdates) == {"CPD-10", "PERF-B"}
    if _git("cat-file", "-e", f"{ref}^{{commit}}").returncode != 0:
        pytest.skip("a shallow clone without the recorded release's commit")
    for feature, before in postdates.items():
        assert _git("merge-base", "--is-ancestor", str(before), ref).returncode == 0, f"{ref} predates {feature}"
        assert str(before) != ref
    assert _git("merge-base", "--is-ancestor", ref, "HEAD").returncode == 0, "this build does not descend from N-1"

