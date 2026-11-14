"""Stack several pull requests on a base branch, for one QA run over all of them.

Each PR lands as one squashed commit, in the order given, so the result has no merge commits.
When a PR conflicts with the ones before it, that PR's commit keeps the combined side of the
conflicting hunks, and the resolver (Claude Code, through prompts/combine.md) writes the
resolution, which goes in a commit of its own so a person can review exactly what it chose.
The builder checks the resolution: no conflict markers left and no other file touched. A
conflict it cannot hand over (a delete against a change, a binary file) stops the build.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from . import paths

MARKERS = re.compile(r"^(<<<<<<<|=======|>>>>>>>)( |$)", re.M)
MAX_PRS = 8

Resolver = Callable[[Path, list[str], dict[str, Any]], str | None]


class CombineError(RuntimeError):
    def __init__(self, message: str, result: dict[str, Any]):
        super().__init__(message)
        self.result = result


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=check)


def _commit(root: Path, message: str, allow_empty: bool = False) -> str:
    _git(root, "commit", "-q", "-s", "--no-verify", *(["--allow-empty"] if allow_empty else []), "-m", message)
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def _stages(root: Path, path: str) -> set[int]:
    out = _git(root, "ls-files", "-u", "-z", "--", path).stdout
    return {int(entry.split()[2]) for entry in out.split("\0") if entry.strip()}


def build(root: Path, base: str, prs: list[dict[str, Any]], resolve: Resolver) -> dict[str, Any]:
    """prs: [{"number": int, "sha": str, "title": str}] in stacking order. Checks out base (detached)
    and leaves HEAD at the combined commit. Raises CombineError with the partial result on a stop."""
    if not 1 <= len(prs) <= MAX_PRS:
        raise CombineError(f"combine takes 1 to {MAX_PRS} pull requests", {})
    base_sha = _git(root, "rev-parse", "--verify", f"{base}^{{commit}}").stdout.strip()
    result: dict[str, Any] = {"base": base_sha, "head": base_sha, "prs": [], "conflicts": []}
    if _git(root, "status", "--porcelain", "--untracked-files=no").stdout.strip():
        raise CombineError("the checkout has uncommitted changes", result)
    _git(root, "checkout", "-q", "--detach", base_sha)
    touched: dict[int, set[str]] = {}
    for pr in prs:
        n, sha = int(pr["number"]), pr["sha"]
        if _git(root, "cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode:
            raise CombineError(f"#{n}: commit {sha[:12]} is not in this checkout (fetch the PR head first)", result)
        title = pr.get("title") or _git(root, "log", "-1", "--format=%s", sha).stdout.strip()
        mb = _git(root, "merge-base", base_sha, sha).stdout.strip()
        files = set(_git(root, "diff", "--name-only", mb, sha).stdout.split())
        entry = {"number": n, "sha": sha, "title": title[:200], "files": sorted(files)}
        _git(root, "merge", "--squash", "--no-commit", sha, check=False)
        unmerged = _git(root, "diff", "--name-only", "--diff-filter=U").stdout.split()
        if not unmerged:
            if _git(root, "diff", "--cached", "--quiet", check=False).returncode:
                entry["commit"] = _commit(root, f"combine: #{n} {title}")
            else:
                entry["commit"] = None
                entry["note"] = "no changes on top of the PRs before it"
            result["prs"].append(entry)
            touched[n] = files
            result["head"] = _git(root, "rev-parse", "HEAD").stdout.strip()
            continue
        against = sorted({p for p, fs in touched.items() if fs & set(unmerged)})
        for f in unmerged:
            if not {2, 3} <= _stages(root, f):
                _git(root, "reset", "-q", "--hard", "HEAD")
                raise CombineError(f"#{n}: {f} is deleted on one side and changed on the other; resolve it by hand", result)
        marked = {f: (root / f).read_bytes() for f in unmerged}
        binary = [f for f, body in marked.items() if b"\0" in body]
        if binary:
            _git(root, "reset", "-q", "--hard", "HEAD")
            raise CombineError(f"#{n}: binary conflict in {', '.join(binary)}", result)
        for f in unmerged:
            _git(root, "checkout", "--ours", "--", f)
        _git(root, "add", "--", *unmerged)
        entry["commit"] = _commit(root, f"combine: #{n} {title}\n\nIts hunks that conflict with "
                                  f"{', '.join(f'#{p}' for p in against) or 'the PRs before it'} follow in the next commit.", allow_empty=True)
        for f, body in marked.items():
            (root / f).write_bytes(body)
        problem = resolve(root, unmerged, {"pr": entry, "against": against, "base": base_sha})
        leftover = [f for f in unmerged if (root / f).is_file() and MARKERS.search((root / f).read_text(encoding="utf-8", errors="replace"))]
        changed = {line[3:] for line in _git(root, "status", "--porcelain", "--untracked-files=all").stdout.splitlines()}
        extra = sorted(changed - set(unmerged))
        if problem or leftover or extra:
            _git(root, "reset", "-q", "--hard", "HEAD")
            for f in extra:
                if (root / f).is_file() and _git(root, "ls-files", "--error-unmatch", "--", f, check=False).returncode:
                    (root / f).unlink()
            why = problem or (f"conflict markers left in {', '.join(leftover)}" if leftover else f"the resolver also changed {', '.join(extra)}")
            result["prs"].append(entry)
            raise CombineError(f"#{n}: the conflict with {', '.join(f'#{p}' for p in against) or 'earlier PRs'} "
                               f"in {', '.join(unmerged)} was not resolved: {why}", result)
        _git(root, "add", "--", *unmerged)
        resolution = _commit(root, f"combine: resolve #{n} against {', '.join(f'#{p}' for p in against) or 'the PRs before it'}"
                             f" in {', '.join(unmerged)}")
        result["conflicts"].append({"pr": n, "against": against, "files": unmerged, "commit": resolution,
                                    "diff": _git(root, "show", "--format=", resolution).stdout[:20_000]})
        result["prs"].append(entry)
        touched[n] = files
        result["head"] = resolution
    return result


def claude_resolver(minutes: float = 20, creds: dict[str, str] | None = None) -> Resolver:
    """Claude Code edits only the conflicted files; build() checks what it did."""
    def resolve(root: Path, files: list[str], ctx: dict[str, Any]) -> str | None:
        exe = shutil.which("claude")
        if not exe:
            return "the claude CLI is not installed"
        pr = ctx["pr"]
        prompt = (paths.REPO / "scripts/qa/prompts/combine.md").read_text(encoding="utf-8") + (
            f"\n\n## This conflict\n\nPR #{pr['number']} \"{pr['title']}\" (commit {pr['sha']}) conflicts with "
            f"{', '.join(f'#{p}' for p in ctx['against']) or 'the PRs stacked before it'} on base {ctx['base']}.\n"
            f"Files with conflict markers: {', '.join(files)}.\n"
            f"See the PR's own change with `git diff {ctx['base']} {pr['sha']} -- <file>` and the combined side with `git diff {ctx['base']} HEAD -- <file>`.\n")
        # Edit anywhere; build() stops the combine if any file but the conflicted ones changed.
        tools = ["Read", "Glob", "Grep", "Edit", "Bash(git diff:*)", "Bash(git log:*)", "Bash(git show:*)"]
        cmd = [exe, "-p", prompt, "--output-format", "json", "--max-turns", "40", "--allowedTools", ",".join(tools),
               "--disallowedTools", "Read(//proc/**),Read(//sys/**)", "--setting-sources", "user", "--strict-mcp-config"]
        if os.environ.get("QA_PLANNER_MODEL") and os.environ.get("QA_PLANNER", "claude_code") == "claude_code":
            cmd += ["--model", os.environ["QA_PLANNER_MODEL"]]
        try:
            out = subprocess.run(cmd, cwd=root, env={**os.environ, **(creds or {})}, capture_output=True, text=True, timeout=minutes * 60)
        except subprocess.TimeoutExpired:
            return f"the resolver stopped at its {minutes:.0f}-minute limit"
        return None if out.returncode == 0 else f"claude exited {out.returncode}: {(out.stdout or out.stderr).strip()[:300]}"
    return resolve
