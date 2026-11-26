"""R17-LINK: a human-test stack refresh repoints its package links when they name a removed worktree.

The stack's checkout links ``node_modules`` to a worktree's packages. When that worktree is removed, the link
dangles: ``[[ -e ]]`` called it absent and ``ln -s`` then failed on the existing link, so ``refresh`` stopped before
rebuilding. ``checkout`` now repoints any link to the main worktree's packages and leaves a real directory alone.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest

from tests.cohesion.support import REPO_ROOT

if TYPE_CHECKING:
    from pathlib import Path

HUMAN_SH = REPO_ROOT / "scripts" / "human-test-stack.sh"
DIRS = ("backend/nodejs/apps", "frontend")


def _function(path: Path, name: str) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.index(f"\n{name}() {{") + 1
    return text[start : text.index("\n}\n", start) + 3]


def _git(*args: str, cwd: Path) -> None:
    _ = subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.invalid",
            "-c",
            "init.defaultBranch=main",
            *args,
        ],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _lock(versions: dict[str, str]) -> str:
    packages: dict[str, dict[str, object]] = {"": {"name": "app"}}
    for name, version in versions.items():
        packages[f"node_modules/{name}"] = {"version": version}
    packages["node_modules/fsevents"] = {"version": "2.3.3", "optional": True}
    return json.dumps({"lockfileVersion": 3, "packages": packages})


def _repo(tmp_path: Path, lock: str | None = None) -> tuple[Path, Path, str]:
    main = tmp_path / "main"
    (main / "deployment" / "e2e").mkdir(parents=True)
    _ = (main / "deployment" / "e2e" / "run.sh").write_text(
        "case x in\n  human-up) ;;\nesac\n", encoding="utf-8"
    )
    for d in DIRS:
        (main / d).mkdir(parents=True, exist_ok=True)
        _ = (main / d / ".keep").write_text("", encoding="utf-8")
        if lock is not None:
            _ = (main / d / "package-lock.json").write_text(lock, encoding="utf-8")
    _git("init", "-q", cwd=main)
    _git("add", "-A", cwd=main)
    _git("commit", "-q", "-m", "init", cwd=main)
    for d in DIRS:
        (main / d / "node_modules").mkdir()
    home = tmp_path / "stack"
    src = home / "src"
    home.mkdir()
    _git("worktree", "add", "-q", "--detach", str(src), "HEAD", cwd=main)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=main,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return main, src, sha


def _checkout(main: Path, src: Path, sha: str) -> subprocess.CompletedProcess[str]:
    script = "\n".join(
        (
            "set -euo pipefail",
            f"MAIN_REPO={main} SRC={src} HT1_HOME={src.parent}",
            'die() { echo "$*" >&2; exit 1; }',
            _function(HUMAN_SH, "packages_current"),
            _function(HUMAN_SH, "checkout"),
            f"checkout {sha}",
        )
    )
    return subprocess.run(
        ["bash", "-c", script],
        env={"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")},
        capture_output=True,
        text=True,
        check=False,
    )


def test_r17_link_1_a_link_to_a_removed_worktree_is_repointed(
    tmp_path: Path,
) -> None:
    main, src, sha = _repo(tmp_path)
    for d in DIRS:
        (src / d / "node_modules").symlink_to(tmp_path / "gone" / d / "node_modules")

    done = _checkout(main, src, sha)

    assert done.returncode == 0, done.stderr
    for d in DIRS:
        link = src / d / "node_modules"
        assert link.is_symlink()
        assert os.readlink(link) == str(main / d / "node_modules")


def test_r17_link_2_a_missing_link_is_made_and_a_real_directory_is_kept(
    tmp_path: Path,
) -> None:
    main, src, sha = _repo(tmp_path)
    real = src / "frontend" / "node_modules"
    real.mkdir()
    _ = (real / "own").write_text("", encoding="utf-8")

    done = _checkout(main, src, sha)

    assert done.returncode == 0, done.stderr
    assert os.readlink(src / "backend/nodejs/apps" / "node_modules") == str(
        main / "backend/nodejs/apps" / "node_modules"
    )
    assert not real.is_symlink() and (real / "own").exists()


needs_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="needs Node on PATH"
)


def _installed(main: Path, versions: dict[str, str]) -> None:
    """What ``npm ci`` records in ``node_modules/.package-lock.json``; an optional package may be absent."""
    for d in DIRS:
        packages = {f"node_modules/{n}": {"version": v} for n, v in versions.items()}
        _ = (main / d / "node_modules" / ".package-lock.json").write_text(
            json.dumps({"lockfileVersion": 3, "packages": packages}), encoding="utf-8"
        )


@needs_node
def test_r17_link_3_packages_installed_from_an_older_lockfile_stop_the_refresh(
    tmp_path: Path,
) -> None:
    main, src, sha = _repo(
        tmp_path, _lock({"left-pad": "1.3.0", "@aws-sdk/client-s3": "3.9.0"})
    )
    _installed(main, {"left-pad": "1.2.0"})

    done = _checkout(main, src, sha)

    assert done.returncode != 0
    assert "npm ci" in done.stderr and str(main / "backend/nodejs/apps") in done.stderr
    assert "@aws-sdk/client-s3" in done.stderr or "left-pad" in done.stderr


@needs_node
def test_r17_link_4_packages_installed_from_this_lockfile_pass(
    tmp_path: Path,
) -> None:
    versions = {"left-pad": "1.3.0", "@aws-sdk/client-s3": "3.9.0"}
    main, src, sha = _repo(tmp_path, _lock(versions))
    _installed(main, versions)

    done = _checkout(main, src, sha)

    assert done.returncode == 0, done.stderr


@needs_node
def test_r17_link_5_packages_with_no_install_record_stop_the_refresh(
    tmp_path: Path,
) -> None:
    main, src, sha = _repo(tmp_path, _lock({"left-pad": "1.3.0"}))

    done = _checkout(main, src, sha)

    assert done.returncode != 0
    assert "npm ci" in done.stderr
