"""C2.10 (WP-91): a checkpoint names the run's repository commit, and a redriven turn makes it reachable again.

Real git on the host (``HostSandbox``), no network: the run's clone is a working copy whose origin is a local bare
repository. The repository is a durable workspace folder (ADR-RUN-01 D2), so what the redrive restores is the
checkpoint's *commit*, never by discarding work saved after it (D6).

- WP91-CKPT-1: at the checkpoint's commit, nothing is done and the engine is told nothing.
- WP91-CKPT-2: commits after the checkpoint (an interrupted turn's autosave) are kept; the checkpoint is pinned.
- WP91-CKPT-3: a rewritten history keeps its head; the checkpoint's commit is pinned, never checked out.
- WP91-CKPT-4: a workspace that lost the commit (a deliverables-only save, a fresh clone) gets it back from the
  session branch, checked out on that branch.
- WP91-CKPT-5: a commit nowhere the run can read is reported lost; the repository is unchanged.
- WP91-CKPT-6: a dirty working tree is never checked out over, even when the commit could be recovered.
- WP91-CKPT-7: the checkpoint's commit is read from the sandbox, with its branch; a folder without one has none.
- WP91-CKPT-8: the commit travels in ``engine.checkpointed`` (schema-valid, additive) and folds back.
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from harness_core.contracts.generated.events.engine_checkpointed import (
    EngineCheckpointedData,
)
from harness_core.engines.durable.fold import checkpoint_data, checkpoint_of
from harness_core.engines.events import EngineStateBlob
from harness_core.engines.git_checkout import GitCheckout, session_branch
from harness_core.engines.repository_checkpoint import (
    CHECKPOINT_REF,
    COMMIT_SCRIPT,
    RESTORE_SCRIPT,
    CommitRestore,
    RepositoryCommit,
    read_commit,
    restore_commit,
)
from harness_core.engines.types import EngineVersion
from harness_core.workspace.types import ObjectRef
from tests.support.git_host import (
    HostSandbox,
    bare_repository,
    commit,
    git,
    working_copy,
)
from tests.unit.harness_core.engines.test_controller import task
from tests.unit.harness_core.engines.test_gitflow_controller import (
    GitWorld,
    LaunchAdapter,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from harness_core.testing.sandbox.fake import FakeProcessContext

SESSION = "sess-ckpt"
BRANCH = session_branch(SESSION)
URL = "https://git.example.test/acme/widgets.git"


class Repo:
    """A bare origin with ``main``, and the run's clone at ``<root>/repo`` on the session branch."""

    def __init__(self, tmp: Path) -> None:
        seed = working_copy(tmp / "seed")
        self.base = commit(seed, "README.md", "base\n")
        self.bare = bare_repository(tmp / "host", "acme/widgets", seed)
        self.root = tmp / "ws"
        self.root.mkdir()
        self.clone()
        self.sandbox = HostSandbox(tmp / "home")
        (tmp / "home").mkdir()
        self.checkout = GitCheckout(url=URL, session_branch=BRANCH)

    @property
    def work(self) -> Path:
        return self.root / "repo"

    def clone(self) -> None:
        git("clone", "-q", str(self.bare), str(self.work))
        git("checkout", "-q", "-B", BRANCH, cwd=self.work)

    def head(self) -> str:
        return git("rev-parse", "HEAD", cwd=self.work)

    def saved(self, sha: str) -> RepositoryCommit:
        return RepositoryCommit("repo", BRANCH, sha)

    async def restore(self, saved: RepositoryCommit, env: Mapping[str, str] | None = None) -> CommitRestore:
        # The run's origin is a local path here: the proxy route an https origin takes is GIT-FLOW's own test.
        given = {"GIT_CONFIG_COUNT": "0", **(env or {})}
        checkout = GitCheckout(url="http://git.example.test/acme/widgets.git", session_branch=BRANCH)
        return await restore_commit(
            self.sandbox, checkout, saved, workspace_root=str(self.root), placeholder=None, env=given
        )


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path)


async def test_wp91_ckpt_1_at_the_checkpoint_nothing_is_done(repo: Repo) -> None:
    at = commit(repo.work, "a.txt")
    restored = await repo.restore(repo.saved(at))
    assert restored.status == "at_checkpoint"
    assert restored.note is None
    assert git("for-each-ref", CHECKPOINT_REF, cwd=repo.work) == ""


async def test_wp91_ckpt_2_commits_after_the_checkpoint_are_kept_and_it_is_pinned(repo: Repo) -> None:
    at = commit(repo.work, "a.txt")
    later = commit(repo.work, "b.txt")
    restored = await repo.restore(repo.saved(at))
    assert (restored.status, restored.head) == ("ahead", later)
    assert repo.head() == later
    assert git("rev-parse", CHECKPOINT_REF, cwd=repo.work) == at
    assert restored.note is not None
    assert at[:7] in restored.note and f"git log {CHECKPOINT_REF}..HEAD" in restored.note


async def test_wp91_ckpt_3_a_rewritten_history_keeps_its_head(repo: Repo) -> None:
    at = commit(repo.work, "a.txt")
    git("reset", "-q", "--hard", repo.base, cwd=repo.work)
    other = commit(repo.work, "c.txt")
    restored = await repo.restore(repo.saved(at))
    assert (restored.status, restored.head) == ("diverged", other)
    assert repo.head() == other
    assert git("rev-parse", CHECKPOINT_REF, cwd=repo.work) == at
    assert restored.note is not None and "Nothing was discarded" in restored.note


async def test_wp91_ckpt_4_a_lost_commit_comes_back_from_the_session_branch(repo: Repo) -> None:
    at = commit(repo.work, "a.txt")
    git("push", "-q", "origin", f"HEAD:refs/heads/{BRANCH}", cwd=repo.work)
    # The workspace kept only the deliverables: the repository the next sandbox gets is the base clone again.
    shutil.rmtree(repo.work)
    git("clone", "-q", str(repo.bare), str(repo.work))
    git("checkout", "-q", "-B", BRANCH, "origin/main", cwd=repo.work)
    assert repo.head() == repo.base
    restored = await repo.restore(repo.saved(at))
    assert restored.status == "recovered"
    assert repo.head() == at
    assert git("symbolic-ref", "--short", "HEAD", cwd=repo.work) == BRANCH
    assert (repo.work / "a.txt").exists()
    assert restored.note is not None and "restored to the last checkpoint's commit" in restored.note


async def test_wp91_ckpt_5_a_commit_nowhere_is_lost_and_nothing_changes(repo: Repo) -> None:
    here = commit(repo.work, "a.txt")
    restored = await repo.restore(repo.saved("d" * 40))
    assert (restored.status, restored.head) == ("lost", here)
    assert repo.head() == here
    assert restored.note is not None and "could not be found" in restored.note


async def test_wp91_ckpt_6_a_dirty_tree_is_never_checked_out_over(repo: Repo) -> None:
    at = commit(repo.work, "a.txt")
    git("reset", "-q", "--hard", repo.base, cwd=repo.work)
    (repo.work / "README.md").write_text("edited, not committed\n")
    restored = await repo.restore(repo.saved(at))
    assert restored.status == "diverged"
    assert repo.head() == repo.base
    assert (repo.work / "README.md").read_text() == "edited, not committed\n"


async def test_wp91_ckpt_7_the_commit_is_read_from_the_sandbox(repo: Repo, tmp_path: Path) -> None:
    at = commit(repo.work, "a.txt")
    read = await read_commit(repo.sandbox, repo.checkout, workspace_root=str(repo.root))
    assert read == RepositoryCommit("repo", BRANCH, at)
    empty = tmp_path / "empty"
    empty.mkdir()
    assert await read_commit(repo.sandbox, repo.checkout, workspace_root=str(empty)) is None


def _event_data(blob: EngineStateBlob) -> dict[str, object]:
    return checkpoint_data(blob, "trn_0123456789abcdef0123456789abcdef")  # type: ignore[return-value]


def test_wp91_ckpt_8_the_commit_travels_in_the_checkpoint_event_and_folds_back() -> None:
    engine = EngineVersion(kind="opencode", version="1.18.34", binary_digest="sha256:" + "c" * 64)  # type: ignore[arg-type]
    content = ObjectRef("sha256:" + "a" * 64, "sha256:" + "b" * 64, 10)
    saved = RepositoryCommit("repo", BRANCH, "e" * 40)
    with_repo = EngineStateBlob(engine, 1, content, repository=saved)
    for blob in (with_repo, EngineStateBlob(engine, 1, content)):
        data = _event_data(blob)
        _ = EngineCheckpointedData.model_validate(data)
        assert checkpoint_of(data) == blob  # type: ignore[arg-type]
    assert "repository" not in _event_data(EngineStateBlob(engine, 1, content))
    bad = {**_event_data(with_repo), "repository": {"directory": "repo", "branch": BRANCH, "commit": "nope"}}
    with pytest.raises(ValidationError):
        _ = EngineCheckpointedData.model_validate(bad)
    # A worker reading a malformed commit restores as before C2.10 (no repository), never fails the fold.
    assert checkpoint_of(bad).repository is None  # type: ignore[arg-type]


class _Sessions:
    """Keeps each session the world's adapter opens, so a test reads the prompt the engine got."""

    def __init__(self, adapter: LaunchAdapter) -> None:
        self.opened: list[object] = []
        opener = adapter.open

        async def open_(sandbox, launch, restore, *, correlation=None):  # type: ignore[no-untyped-def] # noqa: ANN202
            session = await opener(sandbox, launch, restore, correlation=correlation)
            self.opened.append(session)
            return session

        adapter.open = open_  # type: ignore[method-assign]

    def last_prompt(self) -> str:
        history = self.opened[-1].history  # type: ignore[attr-defined]
        return [m.content for m in history if m.role == "user"][-1]


def _git_world(answer: str) -> tuple[GitWorld, list[tuple[str, ...]], _Sessions]:
    git_world = GitWorld()
    sessions = _Sessions(git_world.adapter)
    asked: list[tuple[str, ...]] = []
    inner = git_world.world.broker.programs["sh"]

    async def sh(context: FakeProcessContext) -> int:
        argv = context.argv
        script = argv[2] if len(argv) > 2 and argv[1] == "-c" else ""
        if script == COMMIT_SCRIPT:
            asked.append(("commit", *argv[4:]))
            await context.emit(f"{'f' * 40}\n{BRANCH}\n".encode())
            return 0
        if script.endswith(RESTORE_SCRIPT):
            asked.append(("restore", *argv[argv.index("pxh-git-restore") + 1 :]))
            await context.emit(f"{answer}\n".encode())
            return 0
        return await inner(context)

    git_world.world.broker.programs["sh"] = sh
    return git_world, asked, sessions


async def test_wp91_ckpt_9_the_controller_checkpoints_the_commit_and_restores_it_next_turn() -> None:
    git_world, asked, sessions = _git_world(f"ahead {'9' * 40}")
    world = git_world.world
    assert await git_world.turn(0, attempt=1) == "completed"
    [record] = world.sink.checkpoints
    assert record.blob.repository == RepositoryCommit("repo", BRANCH, "f" * 40)
    assert [a[0] for a in asked] == ["commit"]

    run = world.run(2, checkout=git_world.checkout, restore=record.blob)
    outcome = await world.controller.run_turn(run, task(1, "say again"))
    assert outcome.reason == "completed"
    [restore] = [a for a in asked if a[0] == "restore"]
    assert restore[1].endswith("/repo") and restore[2:] == ("f" * 40, BRANCH)
    prompt = sessions.last_prompt()
    assert "say again" in prompt and "the commits your interrupted attempt made after the last checkpoint" in prompt


async def test_wp91_ckpt_10_a_turn_at_its_checkpoint_is_told_nothing() -> None:
    git_world, asked, sessions = _git_world(f"at_checkpoint {'f' * 40}")
    world = git_world.world
    assert await git_world.turn(0, attempt=1) == "completed"
    [record] = world.sink.checkpoints
    run = world.run(2, checkout=git_world.checkout, restore=record.blob)
    assert (await world.controller.run_turn(run, task(1, "say again"))).reason == "completed"
    assert sessions.last_prompt() == "say again"
    assert [a[0] for a in asked] == ["commit", "restore", "commit"]
