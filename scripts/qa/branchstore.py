"""The qa-history branch: run history kept as flat files on an orphan branch.

Writes are plumbing only (hash-object, mktree, commit-tree), so the work tree, the
index and HEAD are never touched. Every write is a fast-forward: a concurrent
writer makes the push (or the local compare-and-swap) fail, and the same pure
mutation is re-applied on the new tip. Nothing here ever forces.
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import time
from collections.abc import Callable, Mapping
from pathlib import Path

BRANCH = "qa-history"
LOCAL_REF = f"refs/heads/{BRANCH}"
REMOTE_REF = "refs/qa/history-remote"
ZERO = "0" * 40
IDENT = {f"GIT_{who}_{what}": value for who in ("AUTHOR", "COMMITTER")
         for what, value in (("NAME", "qa-history"), ("EMAIL", "qa-history@users.noreply.github.com"))}


class Refused(Exception):
    """This run may not write qa-history."""


class Conflict(Exception):
    """Another writer moved qa-history first."""


def _default_branch(env: Mapping[str, str]) -> str:
    try:
        with open(env["GITHUB_EVENT_PATH"], encoding="utf-8") as fh:
            name = json.load(fh)["repository"]["default_branch"]
        if isinstance(name, str) and name:
            return name
    except (KeyError, OSError, ValueError, TypeError):
        pass
    return env.get("QA_DEFAULT_BRANCH") or "main"


def may_write(env: Mapping[str, str]) -> tuple[bool, str]:
    event, ref = env.get("GITHUB_EVENT_NAME"), env.get("GITHUB_REF", "")
    if not event and not env.get("GITHUB_ACTIONS"):
        return True, "local run"
    main = f"refs/heads/{_default_branch(env)}"
    if event == "schedule":
        return True, f"schedule run on {ref}"
    if event == "push" and ref == main:
        return True, f"push to {ref}"
    # A dispatch can name any commit, and PR or comment runs carry untrusted code.
    return False, f"{event} run on {ref or '(no ref)'} may not write {BRANCH}; only schedule runs and pushes to {main} may"


def _git(root: Path, args: list[str], *, input: str | None = None,
         env: Mapping[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, input=input, capture_output=True, text=True,
                          encoding="utf-8", env={**os.environ, "LC_ALL": "C", **(env or {})})  # the error-text matches need English


def _ok(root: Path, args: list[str], **kw) -> str:
    r = _git(root, args, **kw)
    if r.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout


def read(root: Path, remote: str | None) -> tuple[str | None, dict[str, str]]:
    ref = LOCAL_REF
    if remote:
        ref = REMOTE_REF
        r = _git(root, ["fetch", "--no-tags", remote, f"+{LOCAL_REF}:{REMOTE_REF}"])
        if r.returncode:
            if "couldn't find remote ref" not in r.stderr:
                raise RuntimeError(f"git fetch {remote}: {r.stderr.strip()}")
            _git(root, ["update-ref", "-d", REMOTE_REF])  # drop a tip left from a deleted branch
            return None, {}
    r = _git(root, ["rev-parse", "--verify", "-q", f"{ref}^{{commit}}"])
    if r.returncode:
        return None, {}
    sha = r.stdout.strip()
    files = {}
    for line in _ok(root, ["ls-tree", "-z", sha]).split("\0"):
        if line:
            meta, name = line.split("\t", 1)
            _, kind, oid = meta.split()
            if kind == "blob":
                files[name] = _ok(root, ["cat-file", "blob", oid])
    return sha, files


def _commit(root: Path, parent: str | None, files: dict[str, str], message: str) -> str:
    entries = []
    for name, text in sorted(files.items()):
        if "/" in name or "\n" in name:
            raise ValueError(f"{BRANCH} holds flat files only: {name!r}")
        oid = _ok(root, ["hash-object", "-w", "--stdin"], input=text).strip()
        entries.append(f"100644 blob {oid}\t{name}\n")
    tree = _ok(root, ["mktree"], input="".join(entries)).strip()
    return _ok(root, ["commit-tree", tree, *(["-p", parent] if parent else []), "-m", message], env=IDENT).strip()


def _write(root: Path, sha: str, parent: str | None, remote: str | None) -> None:
    if remote:
        r = _git(root, ["push", "--porcelain", remote, f"{sha}:{LOCAL_REF}"])
        if r.returncode:
            if "[rejected]" in r.stdout or "cannot lock ref" in r.stdout + r.stderr:
                raise Conflict(r.stdout.strip())
            raise RuntimeError(f"git push {remote}: {r.stderr.strip() or r.stdout.strip()}")
        return
    r = _git(root, ["update-ref", LOCAL_REF, sha, parent or ZERO])
    if r.returncode:
        now = _git(root, ["rev-parse", "--verify", "-q", LOCAL_REF]).stdout.strip() or None
        if now != parent:
            raise Conflict(f"{LOCAL_REF} moved to {now}")
        raise RuntimeError(f"git update-ref: {r.stderr.strip()}")


def update(root: Path, mutate: Callable[[dict[str, str]], dict[str, str]], *, remote: str | None,
           message: str, attempts: int = 5) -> str:
    """Apply `mutate` to the branch's files and fast-forward the branch. Returns the new commit."""
    for attempt in range(1, attempts + 1):
        parent, files = read(root, remote)
        # An unchanged tree still commits: the commit is the run's audit record.
        sha = _commit(root, parent, mutate(files), message)
        try:
            _write(root, sha, parent, remote)
            return sha
        except Conflict:
            if attempt == attempts:
                raise
            time.sleep(random.uniform(1, 3) * attempt)
    raise Conflict(f"{BRANCH}: no attempts")


def _gh(args: list[str]) -> str:
    r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8")
    if r.returncode:
        raise RuntimeError(f"gh {' '.join(args[:2])}: {r.stderr.strip() or f'exit {r.returncode}'}")
    return r.stdout


def fetch_file(repo: str, path: str, ref: str = BRANCH) -> str | None:
    try:
        return _gh(["api", "-H", "Accept: application/vnd.github.raw", f"repos/{repo}/contents/{path}?ref={ref}"])
    except RuntimeError as err:
        if "HTTP 404" in str(err):
            return None
        raise
