"""What changed: a PR, a commit range ("this commit and everything after"), or
the working tree. Writes changeset.json and the unified diff beside it."""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from qa import classify, config, globs
from qa.config import Config

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


def resolve_base(root: Path, base_ref: str | None = None) -> str:
    """Merge-base with the PR's target branch (GITHUB_BASE_REF, else main)."""
    target = base_ref or os.environ.get("GITHUB_BASE_REF") or "main"
    for ref in (f"origin/{target}", target):
        try:
            return git(root, "merge-base", ref, "HEAD").strip()
        except subprocess.CalledProcessError:
            continue
    raise SystemExit(f"cannot find a merge-base with {target!r}; fetch it or pass --base")


def rev(root: Path, ref: str) -> str:
    return git(root, "rev-parse", "--verify", f"{ref}^{{commit}}").strip()


@contextlib.contextmanager
def worktree(root: Path, sha: str) -> Iterator[Path]:
    """A throwaway detached worktree at sha; the caller's checkout is never touched."""
    tmp = Path(tempfile.mkdtemp(prefix="qa-worktree-"))
    try:
        subprocess.run(["git", "worktree", "add", "--detach", str(tmp), sha], cwd=root, check=True, capture_output=True)
        config.clear_cache()
        yield tmp
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(tmp)], check=False, cwd=root, capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)
        config.clear_cache()


def parent_or_empty(root: Path, sha: str) -> str:
    try:
        return rev(root, f"{sha}^")
    except subprocess.CalledProcessError:
        return git(root, "hash-object", "-t", "tree", "/dev/null").strip()


def _hunks(diff: str) -> dict[str, list[list[int]]]:
    out: dict[str, list[list[int]]] = {}
    current = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = None if line[4:] == "/dev/null" else line[6:]
        elif current and line.startswith("@@"):
            m = HUNK.match(line)
            if m:
                start, length = int(m.group(1)), int(m.group(2) or 1)
                if length:
                    out.setdefault(current, []).append([start, length])
    return out


def collect(
    cfg: Config, base: str, head: str | None, mode: str, include_worktree: bool = False,
) -> tuple[dict[str, Any], str]:
    root = cfg.root
    head_sha = rev(root, head) if head else rev(root, "HEAD")
    target = [] if include_worktree else [head_sha]
    name_status = git(root, "diff", "--name-status", "-M", base, *target)
    numstat = git(root, "diff", "--numstat", "-M", base, *target)
    diff = git(root, "diff", "--unified=0", "-M", base, *target)
    counts: dict[str, tuple[int, int]] = {}
    for line in numstat.splitlines():
        add, delete, path = line.split("\t", 2)
        path = path.split(" => ")[-1].replace("{", "").replace("}", "")
        counts[path] = (int(add) if add != "-" else 0, int(delete) if delete != "-" else 0)

    test_globs = [g for s in cfg.suites for g in s.get("tests", [])]
    files = []
    for line in name_status.splitlines():
        parts = line.split("\t")
        status, path = parts[0][0], parts[-1]
        old = parts[1] if status in ("R", "C") else None
        add, delete = counts.get(path, (0, 0))
        files.append({
            "path": path,
            "status": status,
            "old_path": old,
            "additions": add,
            "deletions": delete,
            "edition": cfg.edition(path),
            "kind": classify.kind(path, test_globs),
        })
    if include_worktree:
        for path in git(root, "ls-files", "--others", "--exclude-standard").splitlines():
            if not any(f["path"] == path for f in files):
                files.append({
                    "path": path, "status": "A", "old_path": None, "additions": 0, "deletions": 0,
                    "edition": cfg.edition(path), "kind": classify.kind(path, test_globs),
                })

    log = git(root, "log", "--first-parent", "--format=%H%x09%an%x09%s", f"{base}..{head_sha}")
    commits = [dict(zip(("sha", "author", "subject"), line.split("\t", 2))) for line in log.splitlines() if line]
    branch = os.environ.get("GITHUB_HEAD_REF") or git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    changeset = {
        "schema": "qa-changeset/v1",
        "mode": mode,
        "base": base,
        "head": head_sha,
        "branch": branch,
        "worktree": include_worktree,
        "commits": list(reversed(commits)),
        "files": sorted(files, key=lambda f: f["path"]),
        "hunks": _hunks(diff),
    }
    return changeset, diff


def files_changed_by(cfg: Config, sha: str) -> list[str]:
    out = git(cfg.root, "diff-tree", "--no-commit-id", "--name-only", "-r", "-m", "--first-parent", sha)
    return sorted(set(out.split()))


def touches(changeset: dict[str, Any], patterns: list[str]) -> bool:
    return any(globs.match_any(f["path"], patterns) for f in changeset["files"])
