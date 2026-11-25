"""WP-110 PHGIT: ``ph git clone`` of a handed-over copy works for the engine's user on every git the images ship.

A recorded conversation (claude_code, git 2.39.5): both fetches were approved and handed over, then
``ph git clone`` exited 1 with ``ph: git clone failed: and the repository exists.``. The copy was root's (the hand-off
tar's entries had uid 0), so git refused it as dubious ownership; ``-c safe.directory`` and ``GIT_CONFIG_COUNT`` could
not help, because git clears both for the ``upload-pack`` it runs on a local copy, and 2.39 then reads only system and
global config. The fix is the copy's owner: the sandbox user every engine runs as, read-only through the mount.

Unit lane: the tar's owner, that owner being every engine image's user, an old cache entry never handed over again,
git's words in ph's failures. The real images' git against a copy owned
by another uid is ``tests/unit/harness/engines/test_wp110_phgit_docker.py``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tarfile
from pathlib import Path
from typing import Any

import pytest

from app.harness.sandbox.docker_broker import SANDBOX_USER
from harness_core.repos.cache import (
    HAND_OFF_LAYOUT,
    META_FILE,
    TAR_FILE,
    RepositoryCache,
)
from harness_core.repos.fetch import _tar_bare  # pyright: ignore[reportPrivateUsage]
from harness_core.workspace.sync.archive import Owner
from harness_engines.pipeshub import ph
from tests.support.git_host import commit, git, working_copy

IMAGES = Path(__file__).resolve().parents[5] / "deployment" / "sandbox-images"

# What the recorded child run printed for ``git clone --no-hardlinks /pipeshub/repos/63a4….git`` (journal, log.ref).
RECORDED_STDERR = """Cloning into '/workspace/research/onyx'...
fatal: detected dubious ownership in repository at '/pipeshub/repos/63a4de89bf3ce8e42ab6.git'
To add an exception for this directory, call:

\tgit config --global --add safe.directory /pipeshub/repos/63a4de89bf3ce8e42ab6.git
fatal: Could not read from remote repository.

Please make sure you have the correct access rights
and the repository exists.
"""


def _bare(tmp_path: Path) -> Path:
    seed = working_copy(tmp_path / "seed")
    _ = commit(seed, "README.md", "hello\n")
    bare = tmp_path / "repo.git"
    git("clone", "-q", "--bare", "--template=", str(seed), str(bare))
    return bare


def test_phgit1_the_hand_off_tar_belongs_to_the_sandbox_user_and_is_read_only(tmp_path: Path) -> None:
    bare = _bare(tmp_path)
    out = tmp_path / "repo.tar"
    _tar_bare(bare, out, "63a4de89bf3ce8e42ab6.git")
    with tarfile.open(out) as archive:
        members = archive.getmembers()
    owner = Owner()
    assert members and all((m.uid, m.gid) == (owner.uid, owner.gid) for m in members), "git trusts only its own user's copy"
    assert all(m.isdir() or m.isfile() for m in members), "no link or device"
    assert all(m.mode == 0o444 for m in members if m.isfile()), "read-only files"
    assert {m.name.split("/")[0] for m in members} == {"63a4de89bf3ce8e42ab6.git"}


def test_phgit2_that_owner_is_the_user_the_broker_and_every_engine_image_run_as() -> None:
    owner = Owner()
    assert f"{owner.uid}:{owner.gid}" == SANDBOX_USER
    dockerfiles = sorted(IMAGES.glob("*/Dockerfile"))
    assert len(dockerfiles) >= 8
    for dockerfile in dockerfiles:
        users = re.findall(r"^USER\s+(\S+)", dockerfile.read_text(), flags=re.MULTILINE)
        assert users and users[-1] == SANDBOX_USER, f"{dockerfile.parent.name} ends as {users[-1:]}"


def _staged(cache: RepositoryCache) -> Path:
    staged = cache.temporary()
    (staged / TAR_FILE).write_bytes(b"x" * 512)
    return staged


def test_phgit3_a_cache_entry_from_before_the_owner_was_set_is_never_handed_over_again(tmp_path: Path) -> None:
    cache = RepositoryCache(tmp_path / "cache", max_bytes=10**9, max_age_s=600, clock=lambda: 1000.0)
    entry = cache.put("k", _staged(cache), head="a" * 40, ref=None, label="github.com/acme/widgets")
    meta_path = tmp_path / "cache" / "k" / META_FILE
    assert json.loads(meta_path.read_text())["layout"] == HAND_OFF_LAYOUT
    assert cache.get("k") == entry
    # An entry an older worker wrote holds a root-owned tar: a miss, refetched as the sandbox user's.
    old = json.loads(meta_path.read_text())
    del old["layout"]
    meta_path.write_text(json.dumps(old))
    assert cache.get("k") is None
    assert cache.get("k", head="a" * 40) is None, "not even for a session that holds that commit"
    assert cache.known_size("k") == 512, "the card still says about how large it is"


class _Done:
    def __init__(self, code: int, stderr: str, stdout: str = "") -> None:
        self.returncode, self.stderr, self.stdout = code, stderr, stdout


def _runner(stderr: str, code: int = 128) -> Any:  # noqa: ANN401
    return lambda *_args, **_kwargs: _Done(code, stderr)


def test_phgit4_a_failed_checkout_shows_gits_reasons_not_its_last_line() -> None:
    with pytest.raises(ph.PhError) as failed:
        ph.checkout_clone("/pipeshub/repos/63a4de89bf3ce8e42ab6.git", "onyx", "https://github.com/onyx-dot-app/onyx",
                          None, full=False, runner=_runner(RECORDED_STDERR))
    text = str(failed.value)
    assert failed.value.code == ph.EX_TOOL_ERROR
    assert text.startswith("ph: git clone failed:")
    assert "detected dubious ownership in repository at '/pipeshub/repos/63a4de89bf3ce8e42ab6.git'" in text
    assert "Could not read from remote repository." in text
    # git's advice is for a person at a terminal: opening the copy to every repository is not the fix.
    assert "git config --global" not in text and "To add an exception" not in text
    assert "and the repository exists." not in text and "Cloning into" not in text
    assert "PipesHub" in text and "not the repository's" in text, "the cause told plainly"
    assert "{" not in text and ".." not in text, "plain words, no raw JSON"


def test_phgit5_each_reason_once_and_a_failure_without_one_shows_its_last_lines() -> None:
    twice = "fatal: unable to access 'x': timed out\nfatal: unable to access 'x': timed out\n"
    with pytest.raises(ph.PhError) as failed:
        ph.run_git("-C", "widgets", "fetch", runner=_runner(twice))
    assert str(failed.value) == "ph: git fetch failed: fatal: unable to access 'x': timed out", "the command, not -C"
    with pytest.raises(ph.PhError) as plain:
        ph.run_git("clone", runner=_runner("Cloning into 'x'...\nsomething odd happened\n"))
    assert str(plain.value) == "ph: git clone failed: something odd happened"
    with pytest.raises(ph.PhError) as silent:
        ph.run_git("clone", runner=_runner(""))
    assert str(silent.value) == "ph: git clone failed: git exited 128"
    long = "fatal: " + "x" * 2000
    with pytest.raises(ph.PhError) as bounded:
        ph.run_git("clone", runner=_runner(long))
    assert len(str(bounded.value)) < 600


def test_phgit5_a_real_git_failure_reads_the_same(tmp_path: Path) -> None:
    with pytest.raises(ph.PhError) as failed:
        ph.checkout_clone(str(tmp_path / "missing.git"), str(tmp_path / "out"), "https://github.com/acme/widgets",
                          None, full=False)
    text = str(failed.value)
    assert text.startswith("ph: git clone failed: fatal:") and "missing.git" in text, text


@pytest.mark.parametrize("variable", ["GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT"])
def test_phgit7_git_clears_command_line_config_for_a_local_upload_pack(variable: str, tmp_path: Path) -> None:
    """Why ``-c safe.directory`` could not rescue the copy on git 2.39: the variables that carry ``-c`` and
    ``GIT_CONFIG_COUNT`` are in git's local-repository list, which ``git clone`` of a local path clears for the
    ``upload-pack`` child it runs (a wrapper records that child's environment)."""
    bare = _bare(tmp_path)
    probe = tmp_path / "upload-pack"
    seen = tmp_path / "seen"
    probe.write_text(f'#!/bin/sh\nenv > {seen}\nexec git-upload-pack "$@"\n')
    probe.chmod(0o755)
    env = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.pxhprobe", "GIT_CONFIG_VALUE_0": "1"}
    exec_path = subprocess.run(["git", "--exec-path"], capture_output=True, text=True, check=True).stdout.strip()
    _ = git(
        "-c", "core.pxhprobe=1", "clone", "-q", "--no-local", "--upload-pack", str(probe), str(bare), str(tmp_path / "o"),
        env={**env, "PATH": f"{exec_path}:{os.environ['PATH']}"},
    )
    names = {line.split("=", 1)[0] for line in seen.read_text().splitlines()}
    assert variable not in names


_PRODUCT = Path(__file__).resolve().parents[5]
# A ``safe.directory`` written as git config: a ``-c`` pair, a ``(key, value)`` pair, a config line or a
# ``GIT_CONFIG_KEY_n`` value. Prose naming it (why it is not the fix) is not config.
_SETS_SAFE_DIRECTORY = re.compile(r"""safe\.directory\s*=|["']safe\.directory["']|config\b[^#\n]*\bsafe\.directory\s+\S""")


def test_phgit6_no_mirror_or_hand_off_carries_a_safe_directory(tmp_path: Path) -> None:
    """The copy is trusted by its owner, never by opening ``safe.directory``: no product code (the worker, ph, the
    broker, Node, the sandbox images) writes one for a mirror or a handed-over copy, and the hand-off holds none. Shared
    caches' mirrors are an extension point (``SharedCacheMounts``); this build has none, and the controller passes a
    mount's git config through as given."""
    roots = [
        _PRODUCT / "backend" / "python" / part for part in ("app", "harness_core", "harness_engines")
    ] + [_PRODUCT / "backend" / "nodejs" / "apps" / "src", _PRODUCT / "deployment" / "sandbox-images"]
    found = [
        f"{path.relative_to(_PRODUCT)}:{n}"
        for root in roots
        for path in root.rglob("*")
        if path.is_file() and path.suffix in {".py", ".ts", ".sh", ""} and "node_modules" not in path.parts
        for n, line in enumerate(path.read_text(errors="replace").splitlines(), 1)
        if _SETS_SAFE_DIRECTORY.search(line)
    ]
    assert found == []
    bare = _bare(tmp_path)
    out = tmp_path / "repo.tar"
    _tar_bare(bare, out, "63a4de89bf3ce8e42ab6.git")
    with tarfile.open(out) as archive:
        config = archive.extractfile("63a4de89bf3ce8e42ab6.git/config")
        assert config is not None
        assert b"safe" not in config.read()
