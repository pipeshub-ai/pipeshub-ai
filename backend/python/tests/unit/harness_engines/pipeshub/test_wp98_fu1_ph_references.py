"""WP98-FU-1 (ADR-RUN-02): ``ph git clone`` checks out on the references mount and links the folder asked for.

The engine's paths stay the same (``repos/onyx`` works as before, through the link); the saved workspace holds only the
link. A clone of the same folder again replaces its checkout; without the mount (an older controller) ``ph`` checks out
in place. ``ph git adopt`` moves a checkout an older ``ph`` left inside the workspace onto the mount.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import TYPE_CHECKING, Any

import pytest

from harness_engines.pipeshub import ph

if TYPE_CHECKING:
    from pathlib import Path

URL = "https://github.com/onyx-dot-app/onyx"


def _git(*args: str, cwd: Path | None = None) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True, timeout=60)
    return done.stdout.strip()


@pytest.fixture
def bare(tmp_path: Path) -> tuple[Path, str]:
    seed = tmp_path / "seed"
    seed.mkdir()
    _git("init", "-q", "-b", "main", cwd=seed)
    (seed / "README.md").write_text("hello\n")
    _git("-c", "user.name=t", "-c", "user.email=t@e", "add", "README.md", cwd=seed)
    _git("-c", "user.name=t", "-c", "user.email=t@e", "commit", "-q", "-m", "one", cwd=seed)
    copy = tmp_path / "handed" / "onyx.git"
    _git("clone", "-q", "--bare", str(seed), str(copy))
    return copy, _git("rev-parse", "HEAD", cwd=seed)


class _Gateway:
    def __init__(self, path: Path, commit: str) -> None:
        self.answer = {"path": str(path), "url": URL, "label": "onyx-dot-app/onyx", "bytes": 1024, "commit": commit}
        self.calls = 0

    def tools(self) -> list[dict[str, Any]]:
        return [{"name": ph.REPOSITORY_FETCH, "description": "", "inputSchema": {}}]

    def call(self, name: str, arguments: dict[str, Any], **_: Any) -> dict[str, Any]:  # noqa: ANN401
        self.calls += 1
        return {"content": [{"type": "text", "text": json.dumps(self.answer)}], "isError": False}


def test_wp98_fu1_ph_1_a_clone_checks_out_on_the_mount_and_links_the_folder(
    bare: tuple[Path, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, commit = bare
    workspace, mount = tmp_path / "workspace", tmp_path / "references"
    workspace.mkdir()
    mount.mkdir()
    monkeypatch.chdir(workspace)
    gw = _Gateway(path, commit)
    done = ph.git_clone(gw, URL, "repos/onyx", None, full=False, references=str(mount))  # type: ignore[arg-type]
    assert done.code == ph.EX_OK, done.message
    link = workspace / "repos" / "onyx"
    assert link.is_symlink() and (link / "README.md").read_text() == "hello\n"
    checkout = os.readlink(link)
    assert checkout.startswith(str(mount) + "/") and done.data["checkout"] == checkout
    assert _git("config", "pipeshub.commit", cwd=link) == commit
    assert "is a link to the checkout" in done.text and "find -L" in done.text
    # The same folder again: its checkout is replaced, never a second copy beside it.
    again = ph.git_clone(gw, URL, "repos/onyx", None, full=False, references=str(mount))  # type: ignore[arg-type]
    assert again.code == ph.EX_OK and os.readlink(link) == checkout and len(os.listdir(mount)) == 1
    # A link left by an earlier sandbox whose checkout is gone is replaced too.
    os.unlink(link)
    os.symlink(str(mount / "gone-0000000000"), link)
    assert ph.git_clone(gw, URL, "repos/onyx", None, full=False, references=str(mount)).code == ph.EX_OK  # type: ignore[arg-type]
    assert (link / "README.md").is_file()


def test_wp98_fu1_ph_2_without_the_mount_the_checkout_stays_in_place_and_a_full_folder_is_refused(
    bare: tuple[Path, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, commit = bare
    monkeypatch.chdir(tmp_path)
    gw = _Gateway(path, commit)
    done = ph.git_clone(gw, URL, "onyx", None, full=False, references=str(tmp_path / "no-mount"))  # type: ignore[arg-type]
    assert done.code == ph.EX_OK and not (tmp_path / "onyx").is_symlink() and (tmp_path / "onyx" / "README.md").is_file()
    (tmp_path / "mine").mkdir()
    (tmp_path / "mine" / "notes.md").write_text("mine")
    with pytest.raises(ph.PhError):
        ph.git_clone(gw, URL, "mine", None, full=False, references=str(tmp_path))  # type: ignore[arg-type]
    assert gw.calls == 1


def test_wp98_fu1_ph_3_adopt_moves_an_old_in_workspace_checkout_and_leaves_everything_else(
    bare: tuple[Path, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, commit = bare
    workspace, mount = tmp_path / "workspace", tmp_path / "references"
    workspace.mkdir()
    mount.mkdir()
    monkeypatch.chdir(workspace)
    ph.checkout_clone(str(path), "repos/onyx", URL, None, full=False, commit=commit)
    moved = ph.git_adopt("repos/onyx", references=str(mount))
    assert moved.data["moved"] is True
    link = workspace / "repos" / "onyx"
    assert link.is_symlink() and (link / "README.md").read_text() == "hello\n"
    assert ph.git_adopt("repos/onyx", references=str(mount)).data["moved"] is False
    (workspace / "own").mkdir()
    _git("init", "-q", cwd=workspace / "own")
    assert ph.git_adopt("own", references=str(mount)).data["moved"] is False
    assert ph.git_adopt("missing", references=str(mount)).data["moved"] is False
    assert not (workspace / "own").is_symlink()


def test_wp98_fu1_ph_4_adopt_is_a_command_with_json_and_one_name_for_the_mount() -> None:
    root, nouns = ph._parsers()
    args = root.parse_args(["git", "adopt", "repos/onyx", "--json"])
    assert ph._command_name(args) == "git.adopt" and "git" in nouns
    assert ph.REFERENCES_DIR == "/pipeshub/references"
