#!/usr/bin/env python3
"""Fail a pull request only on the lint findings it adds.

Lints the changed files twice with the pull request's own config: once as the
pull request leaves them, and once with each file put back to its main version.
Findings are counted per file and rule. The check fails when a count goes up, so
a pull request is never asked to fix findings that were already on main. A new
file has nothing on main, so every finding in it counts as new.

Counting per rule means a rule that fires once per region does not go up when
that region grows. The one that matters is I001, one finding per unsorted import
block: adding an import to a block main already leaves unsorted passes. The
block was not sorted before and is not made worse; `ruff check --fix --select I`
sorts it in a cleanup pull request.

Usage, from the repository root:
    new_lint_findings.py ruff   <merge-base> <file>...   (files under backend/python)
    new_lint_findings.py eslint <merge-base> <file>...   (files under backend/nodejs/apps)
    new_lint_findings.py log-emoji <merge-base> <file>... (files under backend/python; emoji in log calls)
    new_lint_findings.py log-pii <merge-base> <file>...   (backend/python/app .py and backend/nodejs/apps/src .ts;
                                                           emails and names in log calls)

A code in WARN_ONLY never fails the check: a rise in it is printed as a warning for reviewers.
log-pii reports each reviewed exemption (`# log-pii: ok <reason>`) as LOG-PII-OK.
"""
import collections
import json
import os
import shutil
import subprocess
import sys
import tempfile

# log-pii runs from the repository root: one invocation takes both runtimes' paths.
CWD = {"ruff": "backend/python", "eslint": "backend/nodejs/apps", "log-emoji": "backend/python", "log-pii": "."}
_SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "scripts")
SCRIPTS = {"log-emoji": os.path.join(_SCRIPTS_DIR, "log_emoji.py"), "log-pii": os.path.join(_SCRIPTS_DIR, "log_pii.py")}
WARN_ONLY = frozenset({"LOG-PII-OK"})


# npx hands its command to `sh -c` as one string, and Linux refuses any single argument over 128 KiB
# (MAX_ARG_STRLEN): a pull request touching a couple of thousand files made ESLint exit 249 with no output.
BATCH_BYTES = 64 * 1024


def batches(paths: list[str]) -> list[list[str]]:
    """Split paths into runs whose joined length stays under BATCH_BYTES."""
    out: list[list[str]] = []
    size = 0
    for path in paths:
        if not out or size + len(path) + 1 > BATCH_BYTES:
            out.append([])
            size = 0
        out[-1].append(path)
        size += len(path) + 1
    return out


def lint(tool: str, files: list[str], tree: str = ".") -> list[tuple[str, str, int]]:
    """Return (repo-relative file, rule, line) for every finding that fails the build, linting the copies in tree."""
    cwd = os.path.join(tree, CWD[tool])
    rel = [os.path.relpath(f, CWD[tool]) for f in files]
    items = []
    for batch in batches(rel):
        items.extend(_run(tool, cwd, batch))
    root = os.path.abspath(tree)
    found = []
    for item in items:
        if tool != "eslint":   # ruff, log-emoji and log-pii print the same shape
            loc = item.get("location") or {}
            found.append((os.path.relpath(item["filename"], root), item["code"] or "syntax", loc.get("row", 0)))
        else:
            for msg in item["messages"]:
                if msg.get("severity") == 2:   # warnings do not fail ESLint, so they are not counted
                    found.append((os.path.relpath(item["filePath"], root), msg.get("ruleId") or "fatal", msg.get("line", 0)))
    return found


def _run(tool: str, cwd: str, rel: list[str]) -> list[dict]:
    if tool == "ruff":
        # Ruff lints a path named on the command line even when the config excludes it; --force-exclude keeps
        # generated code that pyproject.toml excludes out of the count.
        cmd = ["ruff", "check", "--force-exclude", "--output-format=json", "--exit-zero", *rel]
    elif tool in SCRIPTS:
        cmd = [sys.executable, SCRIPTS[tool], *rel]
    else:
        cmd = ["npx", "eslint", "--format", "json", *rel]
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
    # ESLint exits 1 when it finds errors and 2 when it cannot run at all.
    if proc.returncode not in (0, 1) or (tool != "eslint" and proc.returncode):
        sys.stderr.write(proc.stderr)
        sys.exit(f"{tool} could not run (exit {proc.returncode})")
    return json.loads(proc.stdout or "[]")


def git(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, check=False, env=env)


def is_symlink_at(commit: str, path: str) -> bool:
    entry = git("ls-tree", commit, "--", path)
    return entry.returncode == 0 and entry.stdout.startswith(b"120000 ")


def main() -> None:
    tool, base, files = sys.argv[1], sys.argv[2], sys.argv[3:]
    if tool not in CWD or not files:
        sys.exit(__doc__)
    # log_pii.py resolves TS_MODULE from its cwd, which is the worktree on the baseline run.
    if os.environ.get("TS_MODULE"):
        os.environ["TS_MODULE"] = os.path.abspath(os.environ["TS_MODULE"])

    head = lint(tool, files)

    # A renamed file is compared with its old path on main, not treated as new.
    renamed = {}
    diff = git("diff", "--name-status", "--find-renames", "-z", base, "HEAD").stdout.decode().split("\0")
    i = 0
    while i < len(diff) - 1:
        status = diff[i]
        if status[:1] in ("R", "C"):   # a rename or copy lists two paths: old, then new
            if status[0] == "R":
                renamed[diff[i + 2]] = diff[i + 1]
            i += 3
        else:
            i += 2
    main_path = {f: renamed.get(f, f) for f in files}

    # Main's versions are written into a throwaway worktree of HEAD, never this checkout: a failed or killed run
    # must not leave them here to be committed. The worktree keeps the pull request's config and every path.
    # A path that was a symlink on main is skipped: git show gives the link text, not source to compare.
    on_main = [
        f for f in files
        if not is_symlink_at(base, main_path[f])
        and git("cat-file", "-e", f"{base}:{main_path[f]}").returncode == 0
    ]
    main_found = []
    if on_main:
        # Uncommitted and untracked files are copied in too, so both passes read the same config and imports.
        changed = git("diff", "--name-only", "--no-renames", "-z", "HEAD")
        untracked = git("ls-files", "-z", "--others", "--exclude-standard")
        if changed.returncode or untracked.returncode:
            sys.exit(f"git could not list uncommitted files: {(changed.stderr + untracked.stderr).decode().strip()}")
        # An entry ending in / is a nested repository, not a file. fsdecode keeps names that are not UTF-8.
        local = [p for p in os.fsdecode(changed.stdout + untracked.stdout).split("\0") if p and not p.endswith("/")]
        with tempfile.TemporaryDirectory() as tmp:
            tree = os.path.join(os.path.realpath(tmp), "tree")
            # No hook directory: a developer's post-checkout hook must not run on, or change, the baseline.
            no_hooks = ("-c", "core.hooksPath=/dev/null")
            # git commit exports GIT_INDEX_FILE to its hooks; worktree add would reset that index to HEAD.
            wt_env = {k: v for k, v in os.environ.items() if k != "GIT_INDEX_FILE"}
            added = git(*no_hooks, "worktree", "add", "--detach", "--quiet", tree, "HEAD", env=wt_env)
            if added.returncode:
                sys.exit(f"git worktree add failed: {added.stderr.decode().strip()}")
            try:
                # git lists paths sorted, and changed ones first, so a file or link replaced by a directory is
                # removed before the directory's files are copied in.
                for p in local:
                    dst = os.path.join(tree, p)
                    if os.path.isdir(dst) and not os.path.islink(dst):
                        shutil.rmtree(dst)   # a directory the checkout replaced with a file
                    elif os.path.lexists(dst):
                        os.remove(dst)   # unlinks a symlink instead of writing through it
                    if os.path.lexists(p) and (os.path.islink(p) or not os.path.isdir(p)):
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        shutil.copy2(p, dst, follow_symlinks=False)
                # Nothing is written through a symlink, in the file or a parent: its target may be outside the worktree.
                on_main = [f for f in on_main
                           if os.path.realpath(os.path.join(tree, f)) == os.path.normpath(os.path.join(tree, f))]
                for f in on_main:
                    shown = git("show", f"{base}:{main_path[f]}")
                    if shown.returncode:
                        sys.exit(f"git show {base}:{main_path[f]} failed: {shown.stderr.decode().strip()}")
                    os.makedirs(os.path.dirname(os.path.join(tree, f)), exist_ok=True)
                    with open(os.path.join(tree, f), "wb") as out:
                        out.write(shown.stdout)
                if tool == "eslint":   # ESLint loads its plugins from the install, which a fresh worktree lacks
                    os.symlink(os.path.abspath(os.path.join(CWD[tool], "node_modules")),
                               os.path.join(tree, CWD[tool], "node_modules"))
                main_found = lint(tool, on_main, tree) if on_main else []
            finally:
                git(*no_hooks, "worktree", "remove", "--force", tree, env=wt_env)

    head_count = collections.Counter((f, r) for f, r, _ in head)
    main_count = collections.Counter((f, r) for f, r, _ in main_found)
    lines = collections.defaultdict(list)
    for f, r, line in head:
        lines[(f, r)].append(line)

    added = {k: head_count[k] - main_count[k] for k in head_count if head_count[k] > main_count[k]}
    existing = sum(min(head_count[k], main_count[k]) for k in head_count if k[1] not in WARN_ONLY)

    for (f, r), n in sorted(added.items()):
        where = ", ".join(str(x) for x in sorted(lines[(f, r)]))
        msg = f"{n} new {r} (main has {main_count[(f, r)]}, this pull request has {head_count[(f, r)]}); lines {where}"
        print(f"::{'warning' if r in WARN_ONLY else 'error'} file={f},line={min(lines[(f, r)])}::{msg}")
        print(f"{f}: {msg}")
    failing = sum(n for (_, r), n in added.items() if r not in WARN_ONLY)
    if existing:
        print(f"::notice::{existing} {tool} findings in these files were already on main. "
              "They do not fail this check; fixing them in a separate pull request is welcome.")
    sys.stdout.flush()
    if failing:
        sys.exit(f"{failing} new {tool} findings. Fix them before merging.")
    print(f"No new {tool} findings.")


if __name__ == "__main__":
    main()
