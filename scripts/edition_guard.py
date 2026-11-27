#!/usr/bin/env python3
"""Keeps this repository edition-neutral: a change adds no edition-only path, import or licence.

Other editions build on this repository through its extension points (see CONTRIBUTING.md, "Extension
points"); their own code lives in their own repositories. A change here therefore fails when it

1. adds a file under an ``ee/`` directory at any depth, or an edition-only build file
   (``Dockerfile.ee``, a workflow named ``*-ee.yml``, a deployment file named ``*.ee.*``), except under one of the
   ``OPEN_EE_ROOTS`` this repository ships (see below);
2. adds an import of an ``ee`` package or directory (Python ``app.ee``; TypeScript ``@/ee/``, ``./ee/``,
   ``../ee/``, or any relative import that resolves under an ``ee/`` directory), except of one of those roots;
3. adds a file to a directory that accepts fixes only (``FROZEN``);
4. adds a proprietary licence marker;
5. names a private edition repository, in an added line or a commit message: both are public once pushed;
6. adds a project-tracking document (a status board, a work log, evidence captures, work-package records, research
   or review notes) under ``docs/agent-harness-v2/``: the project's tracker is kept outside this repository, and
   only reference documents (design, decisions, operation, testing) ship here;
7. leaves an org-admin surface in the tree that ``scripts/edition_guard_admin_allowlist.txt`` does not name: an
   admin-gated route of the harness or the control plane (under ``/agent-harness/admin/``, an operation the API
   reference says only an org admin may call, or an Express route behind ``userAdminCheck``), a workspace settings
   page, or a section of the run policy layer. Controls an organization uses to govern how its people use agents
   stay out of this repository; the allowlist names what it keeps, each with the reason a person needs it.

It warns, without failing, on a commit message or pull-request body that starts with an edition prefix or
carries a session trailer.

Standard library only, so CI and ``scripts/verify.sh`` run it with ``python3 -I`` before any install.

    python3 -I scripts/edition_guard.py --base origin/main      # the commits since the base
    python3 -I scripts/edition_guard.py --worktree              # what is not committed yet
"""

from __future__ import annotations

import argparse
import fnmatch
import posixpath
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

EDITION_PATHS = (
    re.compile(r"(^|/)ee/"),
    re.compile(r"^Dockerfile\.ee$"),
    re.compile(r"^\.github/workflows/.*-ee\.ya?ml$"),
    re.compile(r"^deployment/.*\.ee\."),
)

# The ee/ directories this repository ships: the paid features a licence key turns on (permission sync's readers
# and the licence check), kept in their own named directories so the line between free and paid stays visible.
# A file under one of these, or an import of one, passes rules 1 and 2. Every other ee/ path or import still fails.
OPEN_EE_ROOTS = (
    "backend/python/app/ee/permission_sync/",
    "backend/python/tests/unit/ee/__init__.py",
    "backend/python/tests/unit/ee/permission_sync/",
    "backend/nodejs/apps/src/ee/modules/license/",
    "backend/nodejs/apps/tests/ee/modules/license/",
)
# Every ee module or path a line names, each judged on its own: a line passes only when all of them are open.
_PYTHON_EE_TARGET = re.compile(r"\bapp\.ee(?:\.\w+)*")
_SCRIPT_EE_TARGET = re.compile(r"""["']((?:@/|(?:\.{1,2}/)+)ee/[^"']*)["']""")


def _open_ee_path(path: str) -> bool:
    """A directory root admits what is under it; a file root admits that file only."""
    return any(path.startswith(root) if root.endswith("/") else path == root for root in OPEN_EE_ROOTS)


def _open_python_import(text: str) -> bool:
    targets = _PYTHON_EE_TARGET.findall(text)
    return bool(targets) and all(t == "app.ee.permission_sync" or t.startswith("app.ee.permission_sync.") for t in targets)


# Relative imports, resolved against the importing file: one that lands under an ee/ directory outside the open roots
# fails, wherever the importing file is (``from ..harness import x`` from inside an open root included).
_PYTHON_RELATIVE = re.compile(r"^\s*from\s+(\.+)([\w.]*)\s+import\b")
_SCRIPT_RELATIVE = re.compile(r"""(?:\bfrom\s+|^\s*import\s+|\b(?:require|import)\(\s*)["'](\.{1,2}/[^"']*)["']""")


def _relative_targets(path: str, text: str) -> list[str]:
    """The repository paths this line's relative imports resolve to. ``from .. import a, b`` resolves to the package
    and to each name, since a name may be a module (``from ... import ee``). Names on later lines of a parenthesised
    import are not on this line, so that form also counts as importing ``ee`` from the package."""
    if path.endswith(PYTHON_SOURCES):
        found = _PYTHON_RELATIVE.match(text)
        if not found:
            return []
        base = posixpath.dirname(path)
        for _ in range(len(found.group(1)) - 1):
            base = posixpath.dirname(base)
        if found.group(2):
            return [posixpath.join(base, *found.group(2).split("."))]
        rest = re.sub(r"\bas\s+\w+", "", text[found.end():].split("#")[0])
        names = re.findall(r"\w+", rest)
        continued = "(" in rest and ")" not in rest
        return [base, *(posixpath.join(base, name) for name in names), *([posixpath.join(base, "ee")] if continued else [])]
    if path.endswith(SCRIPT_SOURCES):
        return [posixpath.normpath(posixpath.join(posixpath.dirname(path), spec)) for spec in _SCRIPT_RELATIVE.findall(text)]
    return []


def _closed_ee_target(target: str) -> bool:
    """A resolved import target under an ee/ directory that no open root admits (as a directory or a module file)."""
    stem = target.rstrip("/")
    return bool(EDITION_PATHS[0].search(stem + "/")) and not (
        _open_ee_path(stem + "/") or _open_ee_path(stem + ".py")
    )


def _open_script_import(path: str, text: str) -> bool:
    """Relative specifiers are resolved against the importing file; an alias (``@/ee/``) never names an open root."""
    targets = _SCRIPT_EE_TARGET.findall(text)
    return bool(targets) and all(
        not spec.startswith("@/")
        and any(
            posixpath.normpath(posixpath.join(posixpath.dirname(path), spec)).startswith(root)
            for root in OPEN_EE_ROOTS
            if root.endswith("/")
        )
        for spec in targets
    )

PYTHON_SOURCES = (".py", ".pyi")
SCRIPT_SOURCES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
PYTHON_IMPORTS = (
    re.compile(r"^\s*(?:from|import)\s+app\.ee(?:\.|\s|$)"),
    re.compile(r"""import_module\(\s*["']app\.ee(?:\.|["'])"""),
)
_EE_SPECIFIER = r"""["'](?:@/ee/|(?:\.{1,2}/)+ee/)"""
SCRIPT_IMPORTS = (
    re.compile(rf"\bfrom\s+{_EE_SPECIFIER}"),
    re.compile(rf"^\s*import\s+{_EE_SPECIFIER}"),
    re.compile(rf"\b(?:require|import)\(\s*{_EE_SPECIFIER}"),
)

# These directories accept fixes to what is there, never a new file.
FROZEN = (
    "backend/python/tests/evals/**",
    "backend/python/app/agent_loop_lib/eval/**",
    "backend/python/app/agents/agent_loop/evals/**",
    ".github/workflows/*eval*.yml",
    ".github/workflows/*eval*.yaml",
)

# The harness's project tracker and its review notes live outside this repository; these paths held them once and
# must not come back.
_V2 = r"^docs/agent-harness-v2/"
TRACKER_PATHS = (
    re.compile(_V2 + r"(STATUS|REMAINING|NEXT-PHASE|REPORT|REVIEW|PROJECT-PLAN|research)\.md$"),
    re.compile(_V2 + r"(SECURITY-PROGRAM|TESTING-GUIDE)\.md$"),
    re.compile(_V2 + r"(history|notes|research|pr|ui-revamp|classic-jobs|security)/"),
    re.compile(_V2 + r"impl/(?!tool-effects\.md$|mocks/)"),
    re.compile(r"^docs/agent-harness-pending-work\.md$"),
    re.compile(r"^docs/test-plan/wp/"),
    re.compile(r"^docs/test-plan/07-work-packages\.md$"),
    re.compile(r"^docs/agent-harness-design-review\.md$"),
)

# Split so this file does not carry the marker it looks for.
LICENCE_MARKER = "LicenseRef-" + "PipesHub-Proprietary"

# Built from parts so this file does not carry the name it looks for.
PRIVATE_REPOSITORY = re.compile(r"(?i)\bpipeshub-?ai" + r"-ee\b")

# Rule 7: the org-admin surfaces this repository keeps, and where each kind is read from.
ADMIN_ALLOWLIST = "scripts/edition_guard_admin_allowlist.txt"
OPENAPI = "backend/nodejs/apps/src/modules/api-docs/pipeshub-openapi.yaml"
POLICY_LAYER = "contracts/harness/core/policy/policy-layer.schema.json"
ADMIN_API = ("/agent-harness/", "/control-plane/")
ADMIN_PATH = "/agent-harness/admin/"
ADMIN_ONLY = re.compile(r"(?i)\b(?:org admins only|not an org admin|admins only)\b")
ADMIN_SOURCES = ("backend/nodejs/apps/src/modules/agent-harness/", "backend/nodejs/apps/src/modules/control-plane/")
ADMIN_CHECKED_ROUTE = re.compile(
    r"router\.(get|put|post|patch|delete)\(\s*(['\"`])([^'\"`]+)\2((?:(?!router\.)[\s\S]){0,600}?)\buserAdminCheck\b"
)
WORKSPACE_PAGE = re.compile(r"^frontend/app/\(main\)/(?:@sidebar/)?workspace/(.+)/page\.tsx$")
# Members of a layer that hold no policy of their own: its version, its scope and its edit history.
LAYER_STRUCTURE = frozenset({"schemaVersion", "scope", "updated", "lockedBy"})
SURFACE_KINDS = ("route", "page", "section")

MESSAGE_PREFIX = re.compile(r"^\s*(?:ee:|EE-)")
SESSION_TRAILER = re.compile(r"^Claude-Session:", re.M)


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AddedLine:
    path: str
    number: int
    text: str


def added_lines(diff: str) -> list[AddedLine]:
    """The lines a unified diff (``git diff -U0``) adds, with the file and line number each lands at."""
    out: list[AddedLine] = []
    path: str | None = None
    number = 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            target = line[4:].strip()
            path = None if target == "/dev/null" else target.removeprefix("b/")
        elif line.startswith("@@"):
            hunk = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)", line)
            number = int(hunk.group(1)) if hunk else 0
        elif line.startswith("+") and path is not None:
            out.append(AddedLine(path, number, line[1:]))
            number += 1
    return out


def _frozen(path: str) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in FROZEN)


def check(added_paths: Sequence[str], diff: str, messages: Sequence[str] = ()) -> Report:
    report = Report()
    for path in added_paths:
        if any(pattern.search(path) for pattern in EDITION_PATHS) and not _open_ee_path(path):
            report.errors.append(f"{path}: an edition-only path; it belongs in that edition's repository")
        if _frozen(path):
            report.errors.append(f"{path}: a new file in a directory that accepts fixes only")
        if any(pattern.search(path) for pattern in TRACKER_PATHS):
            report.errors.append(f"{path}: a project-tracking document; the tracker is kept outside this repository")
    for line in added_lines(diff):
        where = f"{line.path}:{line.number}"
        closed_relative = any(_closed_ee_target(t) for t in _relative_targets(line.path, line.text))
        python_import = closed_relative or (
            any(p.search(line.text) for p in PYTHON_IMPORTS) and not _open_python_import(line.text)
        )
        script_import = closed_relative or (
            any(p.search(line.text) for p in SCRIPT_IMPORTS) and not _open_script_import(line.path, line.text)
        )
        if line.path.endswith(PYTHON_SOURCES) and python_import:
            report.errors.append(f"{where}: imports an edition package; use an extension point instead")
        if line.path.endswith(SCRIPT_SOURCES) and script_import:
            report.errors.append(f"{where}: imports an edition directory; use an extension point instead")
        if LICENCE_MARKER in line.text:
            report.errors.append(f"{where}: a proprietary licence marker; this repository's code is Apache-2.0")
        if PRIVATE_REPOSITORY.search(line.text):
            report.errors.append(f"{where}: names a private repository; say \"the enterprise edition\" instead")
    for message in messages:
        subject = message.strip().splitlines()[0] if message.strip() else ""
        if PRIVATE_REPOSITORY.search(message):
            report.errors.append(f"message {subject!r}: names a private repository")
        if MESSAGE_PREFIX.match(subject):
            report.warnings.append(f"message {subject!r}: starts with an edition prefix")
        if SESSION_TRAILER.search(message):
            report.warnings.append(f"message {subject!r}: carries a session trailer")
    return report


def openapi_admin_routes(text: str) -> set[str]:
    """``route <METHOD> <path>`` for each admin-gated operation of the harness and the control plane in the API
    reference: every one under ``/agent-harness/admin/``, and any other whose text says only an org admin may call it."""
    routes: set[str] = set()
    in_paths, path, method, body = False, None, None, []

    def flush() -> None:
        if path is None or method is None or not path.startswith(ADMIN_API):
            return
        if path.startswith(ADMIN_PATH) or ADMIN_ONLY.search("\n".join(body)):
            routes.add(f"route {method.upper()} {path}")

    for line in text.splitlines():
        if re.match(r"^\S", line):
            if in_paths:
                flush()
                path = method = None
            in_paths = line.startswith("paths:")
            continue
        if not in_paths:
            continue
        if match := re.match(r"^  (/\S*):\s*$", line):
            flush()
            path, method, body = match.group(1), None, []
        elif match := re.match(r"^    (get|put|post|patch|delete):\s*$", line):
            flush()
            method, body = match.group(1), []
        else:
            body.append(line)
    if in_paths:
        flush()
    return routes


def source_admin_routes(path: str, text: str) -> set[str]:
    """``<METHOD> <path>`` of each Express route a harness or control-plane file puts behind ``userAdminCheck``, the
    path as the router sees it (``:id`` written ``{id}``, as the API reference writes it)."""
    if not path.startswith(ADMIN_SOURCES) or not path.endswith(SCRIPT_SOURCES):
        return set()
    return {
        f"{match.group(1).upper()} {re.sub(r':([A-Za-z_]\w*)', r'{\1}', match.group(3))}"
        for match in ADMIN_CHECKED_ROUTE.finditer(text)
    }


def layer_sections(text: str) -> set[str]:
    """``section <name>`` for each policy section the run policy layer's schema holds."""
    import json

    properties = json.loads(text).get("properties", {})
    return {f"section {name}" for name in properties if name not in LAYER_STRUCTURE}


def workspace_pages(paths: Sequence[str]) -> set[str]:
    """``page <dir>`` for each workspace settings page, in the main tree or the sidebar's."""
    return {f"page {match.group(1)}" for p in paths if (match := WORKSPACE_PAGE.match(p))}


def parse_allowlist(text: str) -> tuple[dict[str, str], list[str]]:
    """The allowlist's entries (``<kind> <name> -- <reason>``) by surface, and its malformed lines."""
    entries: dict[str, str] = {}
    malformed: list[str] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        surface, _, reason = line.partition(" -- ")
        kind = surface.split(" ", 1)[0]
        if kind not in SURFACE_KINDS or " " not in surface or not reason.strip():
            malformed.append(f"{ADMIN_ALLOWLIST}:{number}: not `<{'|'.join(SURFACE_KINDS)}> <name> -- <reason>`")
            continue
        entries[surface.strip()] = reason.strip()
    return entries, malformed


def check_admin_surfaces(paths: Sequence[str], read: Callable[[str], str | None]) -> Report:
    """Rule 7 over a tree: ``paths`` its files, ``read`` a file's text (None when absent)."""
    report = Report()
    allowlist = read(ADMIN_ALLOWLIST)
    if allowlist is None:
        report.errors.append(f"{ADMIN_ALLOWLIST}: missing; it names the org-admin surfaces this repository keeps")
        return report
    allowed, malformed = parse_allowlist(allowlist)
    report.errors.extend(malformed)
    surfaces = workspace_pages(paths)
    if (openapi := read(OPENAPI)) is not None:
        surfaces |= openapi_admin_routes(openapi)
    if (layer := read(POLICY_LAYER)) is not None:
        surfaces |= layer_sections(layer)
    for surface in sorted(surfaces - set(allowed)):
        report.errors.append(
            f"{surface}: an org-admin surface the allowlist does not name; org-wide controls stay out of this "
            f"repository (name it in {ADMIN_ALLOWLIST} only with the reason a person needs it)"
        )
    allowed_routes = [entry.split(" ", 1)[1] for entry in allowed if entry.startswith("route ")]
    for path in paths:
        if not path.startswith(ADMIN_SOURCES):
            continue
        for route in sorted(source_admin_routes(path, read(path) or "")):
            method, route_path = route.split(" ", 1)
            if not any(a.startswith(f"{method} ") and a.endswith(route_path) for a in allowed_routes):
                report.errors.append(
                    f"{path}: {route} is admin-only and the allowlist names no such route; org-wide controls stay out "
                    f"of this repository"
                )
    for surface in sorted(set(allowed) - surfaces):
        report.warnings.append(f"{ADMIN_ALLOWLIST}: names {surface}, which this tree does not have")
    return report


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, check=True, capture_output=True, text=True).stdout


def _untracked() -> list[str]:
    return [p for p in _git("ls-files", "--others", "--exclude-standard").splitlines() if p]


def _untracked_diff(paths: list[str]) -> str:
    """An untracked file as a diff that adds every line of it."""
    parts: list[str] = []
    for path in paths:
        try:
            text = (REPO / path).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        lines = text.splitlines()
        parts.append(f"+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n" + "".join(f"+{line}\n" for line in lines))
    return "".join(parts)


def collect(base: str | None, worktree: bool) -> tuple[list[str], str, list[str]]:
    if worktree:
        untracked = _untracked()
        added = [p for p in _git("diff", "HEAD", "--name-only", "--no-renames", "--diff-filter=A").splitlines() if p]
        diff = _git("diff", "HEAD", "-U0", "--no-color", "--no-renames", "--diff-filter=AM")
        return [*added, *untracked], diff + _untracked_diff(untracked), []
    assert base is not None
    span = f"{base}...HEAD"
    added = [p for p in _git("diff", span, "--name-only", "--no-renames", "--diff-filter=A").splitlines() if p]
    diff = _git("diff", span, "-U0", "--no-color", "--no-renames", "--diff-filter=AM")
    messages = [m for m in _git("log", "--format=%B%x00", f"{base}..HEAD").split("\x00") if m.strip()]
    return added, diff, messages


def tree(worktree: bool) -> tuple[list[str], Callable[[str], str | None]]:
    """The files of the tree the check reads (the working tree, else the commit at ``HEAD``) and how to read one."""
    if worktree:
        files = [p for p in _git("ls-files", "--cached", "--others", "--exclude-standard").splitlines() if p]

        def read_file(path: str) -> str | None:
            try:
                return (REPO / path).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                return None

        return files, read_file
    files = [p for p in _git("ls-tree", "-r", "--name-only", "HEAD").splitlines() if p]

    def read_blob(path: str) -> str | None:
        shown = subprocess.run(["git", "show", f"HEAD:{path}"], cwd=REPO, capture_output=True, text=True)
        return shown.stdout if shown.returncode == 0 else None

    return files, read_blob


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--base", help="check the commits since this ref (a pull request's base)")
    where.add_argument("--worktree", action="store_true", help="check what is not committed yet")
    parser.add_argument("--pr-body", type=Path, help="a file holding the pull request's body, to warn on")
    args = parser.parse_args(argv)
    added, diff, messages = collect(args.base, args.worktree)
    if args.pr_body is not None and args.pr_body.is_file():
        messages.append(args.pr_body.read_text(encoding="utf-8"))
    report = check(added, diff, messages)
    surfaces = check_admin_surfaces(*tree(args.worktree))
    report.errors.extend(surfaces.errors)
    report.warnings.extend(surfaces.warnings)
    for warning in report.warnings:
        print(f"warning: {warning}")
    for error in report.errors:
        print(f"error: {error}")
    if report.errors:
        print(f"edition guard: {len(report.errors)} problem(s); see CONTRIBUTING.md, \"Extension points\"")
        return 1
    print("edition guard: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
