"""R13-ROOT: run_code's sandbox folders are the running user's own, never a shared, guessable name.

The local, Docker and broker executors kept each run's files under ``<tempdir>/pipeshub_sandbox``,
``pipeshub_sandbox_docker`` and ``pipeshub_sandbox_broker``, created with ``exist_ok`` and trusted as found. On a
machine with more than one user, anyone can create those names first: own them (another user's
``pipeshub_sandbox_docker`` once made the Docker lane fail "Permission denied"), plant a symlink to another place, or
make them group-writable and swap a run's files. Each root is now per user (``<tempdir>/<name>-<uid>``, or under
``PIPESHUB_SANDBOX_ROOT``), made 0700, and used only when it is a real directory the current user owns that nobody else
can write. The artifact reader's containment check reads the same roots, from one function.
"""

from __future__ import annotations

import os
import stat
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from app.sandbox import roots
from app.sandbox.roots import (
    BROKER,
    DOCKER,
    LOCAL,
    SANDBOX_ROOT_ENV,
    SandboxRootUnsafe,
    ensure_private_dir,
    sandbox_root,
    sandbox_roots,
)

if TYPE_CHECKING:
    from pathlib import Path

UID = os.getuid()


@pytest.fixture
def tmp_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private temp dir standing in for the system's, and no operator root."""
    monkeypatch.delenv(SANDBOX_ROOT_ENV, raising=False)
    monkeypatch.setattr(roots.tempfile, "gettempdir", lambda: str(tmp_path))
    return tmp_path


def test_r13_root_1_each_root_is_the_running_users_own_name(tmp_root: Path) -> None:
    for name in (LOCAL, DOCKER, BROKER):
        assert sandbox_root(name) == str(tmp_root / f"{name}-{UID}")


def test_r13_root_2_an_operator_root_holds_the_three(tmp_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = tmp_root / "operator"
    monkeypatch.setenv(SANDBOX_ROOT_ENV, str(base))
    assert sandbox_root(DOCKER) == str(base / DOCKER)
    made = ensure_private_dir(sandbox_root(DOCKER))
    for path in (base, base / DOCKER):
        assert stat.S_IMODE(path.stat().st_mode) == 0o700
    assert made == os.path.realpath(base / DOCKER)


def test_r13_root_3_a_new_root_is_made_private(tmp_root: Path) -> None:
    path = ensure_private_dir(sandbox_root(DOCKER))
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o700
    assert ensure_private_dir(sandbox_root(DOCKER)) == path, "asked again, the same folder"


def test_r13_root_4_a_symlinked_root_is_refused(tmp_root: Path) -> None:
    elsewhere = tmp_root / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    os.symlink(elsewhere, sandbox_root(DOCKER))
    with pytest.raises(SandboxRootUnsafe, match="link"):
        _ = ensure_private_dir(sandbox_root(DOCKER))


def test_r13_root_5_a_root_others_can_write_is_refused(tmp_root: Path) -> None:
    path = sandbox_root(DOCKER)
    os.mkdir(path)
    os.chmod(path, 0o777)
    with pytest.raises(SandboxRootUnsafe, match="(?i)other users can write"):
        _ = ensure_private_dir(path)


def test_r13_root_6_a_root_another_user_made_is_refused(tmp_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Another uid created the name first: refused in plain words, never used, never touched."""
    path = sandbox_root(DOCKER)
    os.mkdir(path, 0o700)
    monkeypatch.setattr(roots.os, "getuid", lambda: UID + 1)
    with pytest.raises(SandboxRootUnsafe, match="another user") as refused:
        _ = ensure_private_dir(path)
    assert SANDBOX_ROOT_ENV in str(refused.value)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o700, "the other user's folder is left as it was"


def test_r13_root_7_a_file_in_place_of_the_root_is_refused(tmp_root: Path) -> None:
    open(sandbox_root(DOCKER), "w").close()
    with pytest.raises(SandboxRootUnsafe, match="not a folder"):
        _ = ensure_private_dir(sandbox_root(DOCKER))


def test_r13_root_8_the_executors_and_the_artifact_reader_read_one_set_of_roots(tmp_root: Path) -> None:
    from app.sandbox import (
        artifact_upload,
        broker_executor,
        docker_executor,
        local_executor,
    )

    def executors() -> tuple[str, str, str]:
        return (
            local_executor.LocalExecutor.get_sandbox_root(),
            docker_executor.DockerExecutor.get_sandbox_root(),
            broker_executor.BrokerExecutor(broker=AsyncMock()).work_root,
        )

    assert executors() == (sandbox_root(LOCAL), sandbox_root(DOCKER), sandbox_root(BROKER))
    # The temp folder can move after the executors were imported (a test's, or a process that sets it late):
    # a root read once at import would write runs where the reader no longer looks.
    moved = tmp_root / "moved"
    moved.mkdir()
    with patch.object(roots.tempfile, "gettempdir", lambda: str(moved)):
        assert executors() == (sandbox_root(LOCAL), sandbox_root(DOCKER), sandbox_root(BROKER))
        assert {os.path.realpath(r) for r in executors()} == set(sandbox_roots())
    assert artifact_upload.sandbox_roots is sandbox_roots


def test_r13_root_9_the_artifact_reader_refuses_the_old_shared_names(tmp_root: Path) -> None:
    from app.sandbox.artifact_upload import (
        _read_file_bytes,  # pyright: ignore[reportPrivateUsage]
    )

    shared = tmp_root / "pipeshub_sandbox_docker" / "x"
    shared.mkdir(parents=True)
    (shared / "a.txt").write_text("planted")
    assert _read_file_bytes(str(shared / "a.txt")) is None
    own = ensure_private_dir(sandbox_root(DOCKER))
    os.makedirs(os.path.join(own, "x"))
    with open(os.path.join(own, "x", "a.txt"), "w") as f:
        f.write("ours")
    assert _read_file_bytes(os.path.join(own, "x", "a.txt")) == b"ours"


def test_r13_root_10_redaction_covers_the_per_user_roots(tmp_root: Path) -> None:
    from app.sandbox.redact import redact_sandbox_paths

    for name in (LOCAL, DOCKER):
        root = sandbox_root(name)
        said = f"Saved at: {root}/abc-1/output/report.pdf and {root}/abc-1/script.py"
        assert redact_sandbox_paths(said) == "Saved at: <output>/report.pdf and <workdir>/script.py"
    assert redact_sandbox_paths("/tmp/pipeshub_sandbox_docker-1000/e1/output/a.csv") == "<output>/a.csv"


# R13-ROOT-11..14: every other folder the backend keeps under the shared temp folder.


def test_r13_root_11_shared_parse_slots_refuse_a_folder_another_user_made(
    tmp_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.resource_governor.shared_slots import (
        open_shared_slots,
        shared_slots_directory,
    )

    directory = shared_slots_directory("pipeshub-heavy-parse")
    assert directory is not None and str(directory) == str(tmp_root / f"pipeshub-heavy-parse-{UID}")
    directory.mkdir(mode=0o700)
    monkeypatch.setattr(roots.os, "getuid", lambda: UID + 1)
    assert open_shared_slots(directory, 2) is None, "refused, and the indexer falls back to its own share"
    assert list(directory.iterdir()) == [], "nothing written into the other user's folder"


def test_r13_root_12_shared_parse_slots_refuse_a_link_and_a_writable_folder(tmp_root: Path) -> None:
    from app.services.resource_governor.shared_slots import open_shared_slots

    target = tmp_root / "target"
    target.mkdir(mode=0o700)
    link = tmp_root / "slots-link"
    link.symlink_to(target)
    assert open_shared_slots(link, 2) is None
    assert list(target.iterdir()) == []
    open_dir = tmp_root / "slots-open"
    open_dir.mkdir()
    open_dir.chmod(0o777)
    assert open_shared_slots(open_dir, 2) is None
    made = open_shared_slots(tmp_root / "slots-new", 2)
    assert made is not None and stat.S_IMODE((tmp_root / "slots-new").stat().st_mode) == 0o700


def test_r13_root_13_a_coding_sandboxs_working_folder_is_private(tmp_root: Path) -> None:
    from app.agent_loop_lib.sandbox.coding.environment import EnvironmentManager

    work = tmp_root / "alcs-0123456789"
    EnvironmentManager(str(work)).prepare_working_dir()
    assert stat.S_IMODE(work.stat().st_mode) == 0o700, "a run's files are not readable by other users"


def test_r13_root_14_a_coding_sandbox_refuses_a_working_folder_it_does_not_own(
    tmp_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.agent_loop_lib.sandbox.coding.environment import EnvironmentManager
    from app.utils.private_dirs import PrivateDirUnsafe

    work = tmp_root / "alcs-0123456789"
    work.mkdir(mode=0o700)
    monkeypatch.setattr(roots.os, "getuid", lambda: UID + 1)
    with pytest.raises(PrivateDirUnsafe, match="another user"):
        EnvironmentManager(str(work)).prepare_working_dir()
