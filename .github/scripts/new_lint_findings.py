#!/usr/bin/env python3
"""Fail a pull request only on the lint findings it adds.

Lints the changed files twice with the pull request's own config: once as the
pull request leaves them, and once with each file put back to its main version.
Findings are counted per file and rule. The check fails when a count goes up, so
a pull request is never asked to fix findings that were already on main. A new
file has nothing on main, so every finding in it counts as new.

Usage, from the repository root:
    new_lint_findings.py ruff   <merge-base> <file>...   (files under backend/python)
    new_lint_findings.py eslint <merge-base> <file>...   (files under backend/nodejs/apps)
"""
import collections
import json
import os
import subprocess
import sys

CWD = {"ruff": "backend/python", "eslint": "backend/nodejs/apps"}


def lint(tool: str, files: list[str]) -> list[tuple[str, str, int]]:
    """Return (repo-relative file, rule, line) for every finding that fails the build."""
    cwd = CWD[tool]
    rel = [os.path.relpath(f, cwd) for f in files]
    if tool == "ruff":
        cmd = ["ruff", "check", "--output-format=json", "--exit-zero", *rel]
    else:
        cmd = ["npx", "eslint", "--format", "json", *rel]
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    # ESLint exits 1 when it finds errors and 2 when it cannot run at all.
    if proc.returncode not in (0, 1) or (tool == "ruff" and proc.returncode):
        sys.stderr.write(proc.stderr)
        sys.exit(f"{tool} could not run (exit {proc.returncode})")
    root = os.getcwd()
    found = []
    for item in json.loads(proc.stdout or "[]"):
        if tool == "ruff":
            loc = item.get("location") or {}
            found.append((os.path.relpath(item["filename"], root), item["code"] or "syntax", loc.get("row", 0)))
        else:
            for msg in item["messages"]:
                if msg.get("severity") == 2:   # warnings do not fail ESLint, so they are not counted
                    found.append((os.path.relpath(item["filePath"], root), msg.get("ruleId") or "fatal", msg.get("line", 0)))
    return found


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, check=False)


def main() -> None:
    tool, base, files = sys.argv[1], sys.argv[2], sys.argv[3:]
    if tool not in CWD or not files:
        sys.exit(__doc__)

    head = lint(tool, files)

    # Put each file that exists on main back to its main version, lint it, then restore the pull request's version.
    on_main = [f for f in files if git("cat-file", "-e", f"{base}:{f}").returncode == 0]
    try:
        for f in on_main:
            with open(f, "wb") as out:
                out.write(git("show", f"{base}:{f}").stdout)
        main_found = lint(tool, on_main) if on_main else []
    finally:
        if on_main:
            subprocess.run(["git", "checkout", "HEAD", "--", *on_main], check=True)

    head_count = collections.Counter((f, r) for f, r, _ in head)
    main_count = collections.Counter((f, r) for f, r, _ in main_found)
    lines = collections.defaultdict(list)
    for f, r, line in head:
        lines[(f, r)].append(line)

    added = {k: head_count[k] - main_count[k] for k in head_count if head_count[k] > main_count[k]}
    existing = sum(min(head_count[k], main_count[k]) for k in head_count)

    for (f, r), n in sorted(added.items()):
        where = ", ".join(str(x) for x in sorted(lines[(f, r)]))
        msg = f"{n} new {r} (main has {main_count[(f, r)]}, this pull request has {head_count[(f, r)]}); lines {where}"
        print(f"::error file={f},line={min(lines[(f, r)])}::{msg}")
        print(f"{f}: {msg}")
    if existing:
        print(f"::notice::{existing} {tool} findings in these files were already on main. "
              "They do not fail this check; fixing them in a separate pull request is welcome.")
    sys.stdout.flush()
    if added:
        sys.exit(f"{sum(added.values())} new {tool} findings. Fix them before merging.")
    print(f"No new {tool} findings.")


if __name__ == "__main__":
    main()
