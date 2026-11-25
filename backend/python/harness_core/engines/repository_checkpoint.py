"""The run's repository commit in an engine checkpoint (C2.10): recorded at the turn's end, restored on a redrive.

A checkpoint is the engine's state, the workspace revision, the journal's acked seq and, for a run that works on a
repository (GIT-FLOW), the commit the repository was at. The repository is the person's work, so it travels in the
workspace store as a durable folder (ADR-RUN-01 D2); the checkpoint names only its commit, never content.

On the next attempt the workspace is materialised from its latest revision, which may be newer than the checkpoint
(an interrupted turn's autosave) or older (a save that kept only the deliverables). ``restore_commit`` makes the
checkpoint's commit reachable again without discarding saved work (D6: a redrive resumes, never starts over):

* ``at_checkpoint``: the repository is where the checkpoint left it.
* ``ahead``: the interrupted attempt committed after the checkpoint; the commits stay, the checkpoint is pinned.
* ``diverged``: the repository's head does not contain the checkpoint's commit (history was rewritten); it stays
  as it is, the checkpoint's commit is pinned.
* ``recovered``: the workspace did not hold the commit; it was fetched from the session branch and checked out,
  because the working tree held nothing of its own.
* ``lost``: the commit is nowhere the run can read; the repository stays as it is.

The pin is ``refs/pipeshub/checkpoint``, a ref the engine can diff against and that keeps the commit from git's
garbage collection. What the engine is told is ``CommitRestore.note``; ``at_checkpoint`` tells it nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, cast

from harness_core.engines.git_checkout import agent_git_env, run_in
from harness_core.sandbox.egress.trust import trusted_command
from harness_core.sandbox.errors import SandboxError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.engines.git_checkout import GitCheckout
    from harness_core.sandbox.ports import SandboxHandle

RestoreStatus = Literal["at_checkpoint", "ahead", "diverged", "recovered", "lost", "unavailable"]
CHECKPOINT_REF: Final = "refs/pipeshub/checkpoint"
_COMMIT = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,199}")
_STATUSES: Final = frozenset({"at_checkpoint", "ahead", "diverged", "recovered", "lost"})

# The repository's head commit and branch, one per line; nothing when the folder holds no commit.
COMMIT_SCRIPT: Final = r"""set -eu
dir="$1"
cd "$dir" 2>/dev/null || exit 0
git rev-parse -q --verify HEAD^{commit} || exit 0
git symbolic-ref -q --short HEAD || true
"""

# Every argument positional. Only a commit the run can read is pinned or checked out, and work is never discarded:
# a checkout happens only when the working tree is clean and the head is an ancestor of the commit.
RESTORE_SCRIPT: Final = r"""set -u
dir="$1"; commit="$2"; branch="$3"
cd "$dir" 2>/dev/null || { echo "lost -"; exit 0; }
head=$(git rev-parse -q --verify 'HEAD^{commit}' 2>/dev/null || echo -)
if [ "$head" = "$commit" ]; then echo "at_checkpoint $head"; exit 0; fi
if ! git cat-file -e "$commit^{commit}" 2>/dev/null; then
  git fetch -q --no-tags origin "+refs/heads/$branch:refs/pipeshub/session" >/dev/null 2>&1 || true
fi
if ! git cat-file -e "$commit^{commit}" 2>/dev/null; then echo "lost $head"; exit 0; fi
git update-ref refs/pipeshub/checkpoint "$commit"
if [ "$head" != - ] && git merge-base --is-ancestor "$commit" "$head" 2>/dev/null; then echo "ahead $head"; exit 0; fi
if [ -z "$(git status --porcelain --untracked-files=no 2>/dev/null)" ]; then
  if [ "$head" = - ] || git merge-base --is-ancestor "$head" "$commit" 2>/dev/null; then
    git checkout -q -B "$branch" "$commit" >/dev/null 2>&1 && { echo "recovered $commit"; exit 0; }
  fi
fi
echo "diverged $head"
"""


@dataclass(frozen=True, slots=True)
class RepositoryCommit:
    """Where the run's repository was when a checkpoint was taken: its folder, branch and commit."""

    directory: str
    branch: str
    commit: str

    def __post_init__(self) -> None:
        if not _COMMIT.fullmatch(self.commit) or not _BRANCH.fullmatch(self.branch):
            raise ValueError("a checkpoint's repository commit is a full object id on a plain branch")

    @property
    def short(self) -> str:
        return self.commit[:7]

    def wire(self) -> dict[str, str]:
        return {"directory": self.directory, "branch": self.branch, "commit": self.commit}

    @classmethod
    def from_wire(cls, raw: object) -> RepositoryCommit | None:
        """The commit an ``engine.checkpointed`` event names; ``None`` for an older event or one this worker cannot
        read (the checkpoint then restores as it did before C2.10)."""
        if not isinstance(raw, dict):
            return None
        data = cast("dict[str, object]", raw)
        directory, branch, commit = data.get("directory"), data.get("branch"), data.get("commit")
        if not (isinstance(directory, str) and isinstance(branch, str) and isinstance(commit, str)):
            return None
        try:
            return cls(directory, branch, commit)
        except ValueError:
            return None


@dataclass(frozen=True, slots=True)
class CommitRestore:
    """What ``restore_commit`` found and did."""

    status: RestoreStatus
    saved: RepositoryCommit
    head: str | None = None

    @property
    def note(self) -> str | None:
        """What the redriven engine is told about its repository; ``None`` when there is nothing to tell."""
        where, at = f"{self.saved.directory}/", self.saved.short
        head = self.head[:7] if self.head else "no commit"
        match self.status:
            case "ahead":
                return (
                    f"The repository {where} is at {head}: the commits your interrupted attempt made after the last "
                    f"checkpoint ({at}) are kept. `git log {CHECKPOINT_REF}..HEAD` lists them."
                )
            case "diverged":
                return (
                    f"The repository {where} is at {head}, which does not contain the last checkpoint's commit {at}. "
                    f"Nothing was discarded; that commit is kept as {CHECKPOINT_REF}."
                )
            case "recovered":
                return (
                    f"The repository {where} was restored to the last checkpoint's commit {at} on "
                    f"{self.saved.branch}, from the session branch."
                )
            case "lost":
                return (
                    f"The last checkpoint's commit {at} in {where} could not be found; the repository is at {head}. "
                    "Check its state before you continue."
                )
            case "at_checkpoint" | "unavailable":
                return None


def _git_env(checkout: GitCheckout, placeholder: str | None) -> dict[str, str]:
    return {**agent_git_env(checkout, placeholder), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}


def _directory(checkout: GitCheckout, workspace_root: str) -> str:
    return f"{workspace_root.rstrip('/')}/{checkout.directory}"


async def read_commit(sandbox: SandboxHandle, checkout: GitCheckout, *, workspace_root: str) -> RepositoryCommit | None:
    """The repository's commit for a checkpoint; ``None`` when it holds none (or the read failed: a checkpoint is
    still taken, without a commit, as before C2.10)."""
    argv = ["sh", "-c", COMMIT_SCRIPT, "pxh-git-commit", _directory(checkout, workspace_root)]
    try:
        code, out, _ = await run_in(sandbox, argv, _git_env(checkout, None))
    except (SandboxError, OSError, TimeoutError):
        return None
    lines = out.strip().splitlines()
    if code != 0 or not lines or not _COMMIT.fullmatch(lines[0].strip()):
        return None
    branch = lines[1].strip() if len(lines) > 1 and lines[1].strip() else checkout.session_branch
    try:
        return RepositoryCommit(checkout.directory, branch, lines[0].strip())
    except ValueError:
        return RepositoryCommit(checkout.directory, checkout.session_branch, lines[0].strip())


async def restore_commit(
    sandbox: SandboxHandle,
    checkout: GitCheckout,
    saved: RepositoryCommit,
    *,
    workspace_root: str,
    placeholder: str | None,
    env: Mapping[str, str] | None = None,
) -> CommitRestore:
    """Make the checkpoint's commit reachable in the materialised repository (see the module's statuses).
    ``placeholder`` is the agent phase's fetch placeholder: a commit the workspace lost is fetched from the session
    branch through the egress proxy, read only."""
    argv = ["sh", "-c", RESTORE_SCRIPT, "pxh-git-restore", _directory(checkout, workspace_root), saved.commit, saved.branch]
    given = {**_git_env(checkout, placeholder), **(env or {})}
    try:
        code, out, _ = await run_in(sandbox, trusted_command(argv) if checkout.tls else argv, given)
    except (SandboxError, OSError, TimeoutError):
        return CommitRestore("unavailable", saved)
    status, _, head = out.strip().rpartition("\n")[-1].partition(" ")
    if code != 0 or status not in _STATUSES:
        return CommitRestore("unavailable", saved)
    return CommitRestore(cast("RestoreStatus", status), saved, head if _COMMIT.fullmatch(head) else None)


__all__ = [
    "CHECKPOINT_REF",
    "COMMIT_SCRIPT",
    "RESTORE_SCRIPT",
    "CommitRestore",
    "RepositoryCommit",
    "RestoreStatus",
    "read_commit",
    "restore_commit",
]
