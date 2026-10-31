"""qa prove: a test is proven when it passes on head every time and fails once the
covered change is reverted or a deterministic mutant replaces a line it covers.

Reverts and mutants are applied only in throwaway worktrees; the caller's checkout
is never edited."""

from __future__ import annotations

import ast
import contextlib
import copy
import json
import os
import re
import shutil
import signal
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from qa import catalog, collect, config, globs, run, validate
from qa.config import Config

LINT = "{}: asserts nothing about behavior (only that the call did not raise)"
OPS = ["== -> !=", "!= -> ==", "< -> >=", "> -> <=", "<= -> >", ">= -> <", "in -> not in", "is -> is not",
       "if -> if not", "True -> False", "False -> True", "return -> return None"]
COMPARE = {ast.Eq: (ast.NotEq, "== -> !="), ast.NotEq: (ast.Eq, "!= -> =="), ast.Lt: (ast.GtE, "< -> >="),
           ast.Gt: (ast.LtE, "> -> <="), ast.LtE: (ast.Gt, "<= -> >"), ast.GtE: (ast.Lt, ">= -> <"),
           ast.In: (ast.NotIn, "in -> not in"), ast.Is: (ast.IsNot, "is -> is not")}
KILLS = ("fail", "error")
# An error before any test ran (collection, import, no JUnit) says nothing about the test's assertions.
EARLY_ERROR = re.compile(r"collection|no result in JUnit|wrote no JUnit|run-level error|could not start")


# --- lint ---------------------------------------------------------------------

def _checks_behavior(fn: ast.AST) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Assert) and not isinstance(node.test, ast.Constant):
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr.startswith("assert"):
            return True
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                call = item.context_expr
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "raises":
                    return True
    return False


def assertion_problems(source: str, only: str | None = None) -> list[str]:
    """Tests whose only check is that the call did not raise. only: "test" or "Class::test" limits it to one."""
    tree = ast.parse(source)
    funcs = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            funcs += [(f"{node.name}::{f.name}", f) for f in node.body]
        else:
            funcs.append((getattr(node, "name", ""), node))
    return [LINT.format(f.name) for qual, f in funcs
            if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and f.name.startswith("test")
            and (only is None or qual == only) and not _checks_behavior(f)]


# --- mutants --------------------------------------------------------------------

def _sites(node: ast.AST) -> Iterator[tuple[str, Any]]:
    if isinstance(node, ast.Compare):
        for j, op in enumerate(node.ops):
            if type(op) in COMPARE:
                yield COMPARE[type(op)][1], j
    elif isinstance(node, ast.If):
        yield "if -> if not", None
    elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
        yield ("True -> False" if node.value else "False -> True"), None
    elif isinstance(node, ast.Return) and node.value is not None and not (
            isinstance(node.value, ast.Constant) and node.value.value is None):
        yield "return -> return None", None


def _apply(node: ast.AST, op: str, arg: Any) -> None:
    if isinstance(node, ast.Compare):
        node.ops[arg] = COMPARE[type(node.ops[arg])][0]()
    elif isinstance(node, ast.If):
        node.test = ast.UnaryOp(op=ast.Not(), operand=node.test)
    elif isinstance(node, ast.Constant):
        node.value = not node.value
    else:
        node.value = ast.Constant(value=None)


def mutants(source: str, lines: set[int] | None = None) -> list[tuple[int, str, str]]:
    """One mutant per site: (line, operator, mutated source), by line then operator."""
    tree = ast.parse(source)
    sites = []
    for i, node in enumerate(ast.walk(tree)):
        for op, arg in _sites(node):
            if lines is None or node.lineno in lines:
                sites.append((node.lineno, OPS.index(op), i, op, arg))
    out = []
    for line, _, i, op, arg in sorted(sites, key=lambda s: s[:3]):
        mutated = copy.deepcopy(tree)
        # ast.walk visits a deep copy in the same order, so index i is the same node.
        _apply(next(n for k, n in enumerate(ast.walk(mutated)) if k == i), op, arg)
        out.append((line, op, ast.unparse(mutated)))
    return out


# --- variant trees ----------------------------------------------------------------

def _git(root: Path, *args: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, **kw)


@contextlib.contextmanager
def variant_tree(root: Path, head: str, test_path: str) -> Iterator[Path]:
    """head plus the caller's uncommitted diff, plus the test file when it is untracked."""
    with collect.worktree(root, head) as tree:
        diff = _git(root, "diff", "HEAD", "--binary", check=True).stdout
        if diff:
            _git(tree, "apply", "--binary", "-", input=diff, check=True)
        src = root / test_path
        if src.is_file() and _git(root, "ls-files", "--error-unmatch", "--", test_path).returncode:
            (tree / test_path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, tree / test_path)
        yield tree


# --- prove ----------------------------------------------------------------------------

def _attempt(cfg: Config, rows: dict, test_id: str, head: str, out: Path, stack_mode: str, base_url: str | None) -> tuple[str, str, str]:
    """One run of the test in cfg.root: (outcome, not-run kind, details)."""
    saved = os.environ.get("QA_ROOT")
    os.environ["QA_ROOT"] = str(cfg.root)
    try:
        plan = validate.plan_from_tests(cfg, [test_id], rows)
        plan["head"] = head
        res = run.run(cfg, plan, rows, out, stack_mode=stack_mode, base_url=base_url,
                      build=stack_mode == "managed", retry=False)
    finally:
        if saved is None:
            os.environ.pop("QA_ROOT", None)
        else:
            os.environ["QA_ROOT"] = saved
    t = next((t for t in res["tests"] if t["id"] == test_id), None)
    if t is None:
        return "not_run", "no_result", "no result"
    details = str(t.get("details") or "")
    return t["outcome"], t.get("kind") or details.split(":")[0], details


def _is_test(cfg: Config, path: str) -> bool:
    return globs.match_any(path, cfg.always.get("test_files", [])) or catalog.suite_of(path, cfg) is not None


def _changed_lines(root: Path, base: str, path: str) -> set[int]:
    diff = _git(root, "diff", "-U0", base, "--", path, check=True, text=True).stdout
    return {n for start, length in collect._hunks(diff).get(path, []) for n in range(start, start + length)}


def prove(cfg: Config, row: dict, out: Path, base: str | None = None, repeat: int | None = None,
          max_mutants: int | None = None, stack_mode: str = "none", base_url: str | None = None,
          rows: dict | None = None) -> dict:
    """rows: the catalog index of cfg.root. Variants change only covered sources, so it serves them too."""
    stack = "service:stack" in row.get("needs", [])
    repeat = max(1, repeat or (1 if stack else 3))
    max_mutants = max_mutants if max_mutants is not None else (1 if stack else 5)
    root = cfg.root
    head = collect.rev(root, "HEAD")
    base_sha = collect.rev(root, base) if base else None
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", row["id"])
    work = out / "prove" / safe
    result: dict[str, Any] = {"schema": "qa-proof/v1", "id": row["id"], "head": head, "lint": [], "head_runs": [],
                              "revert": None, "mutants": [], "verdict": "unproven", "reason": ""}

    def finish(verdict: str, reason: str) -> dict:
        result.update(verdict=verdict, reason=reason)
        path = out / "prove" / f"{safe}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        return result

    # A test-grain id ("...::test" or "...::Class::test") is linted alone; a weak sibling is not its problem.
    _, _, only = row["id"].partition("::")
    if row["path"].endswith(".py") and (root / row["path"]).is_file():
        try:
            result["lint"] = assertion_problems((root / row["path"]).read_text(encoding="utf-8"), only or None)
        except SyntaxError as exc:
            result["lint"] = [f"{row['path']}: does not parse: {exc.msg}"]
    if result["lint"]:
        return finish("unproven", "; ".join(result["lint"])[:500])

    rows = rows if rows is not None else catalog.index(catalog.build(cfg))
    # SIGTERM would skip the worktree cleanup; turn it into an exception (run.run does the same).
    previous = signal.signal(signal.SIGTERM, run._on_term)
    try:
        for n in range(repeat):
            # Its own tag, so a managed stack never builds or removes the plain qa-<sha12> image of a qa run.
            result["head_runs"].append(_attempt(cfg, rows, row["id"], f"{head[:8]}h", work / f"head-{n + 1}", stack_mode, base_url)[0])
        if any(o != "pass" for o in result["head_runs"]):
            return finish("unproven", "did not pass on head: " + ", ".join(result["head_runs"]))

        k = 0
        not_run: list[str] = []

        def variant(edit) -> dict:
            nonlocal k
            k += 1
            with variant_tree(root, head, row["path"]) as tree:
                edit(tree)
                outcome, kind, details = _attempt(config.load(tree), rows, row["id"], f"{head[:8]}v{k}", work / f"v{k}", stack_mode, base_url)
            if outcome == "error" and EARLY_ERROR.search(details):
                outcome, kind, details = "not_run", "early error", f"error before any test ran: {details}"[:300]
            if outcome == "not_run":
                not_run.append(kind)
                return {"outcome": outcome, "details": details[:300]}
            return {"outcome": outcome}

        covers = [c for c in row.get("covers", []) if not _is_test(cfg, c)]
        if base_sha:
            changed = set(collect.git(root, "diff", "--name-only", base_sha).split())
            files = [c for c in covers if c in changed]
            if files:
                def revert(tree: Path) -> None:
                    for f in files:
                        if _git(tree, "cat-file", "-e", f"{base_sha}:{f}").returncode:
                            (tree / f).unlink(missing_ok=True)
                        else:
                            _git(tree, "checkout", base_sha, "--", f, check=True)
                result["revert"] = {"base": base_sha, "files": files, **variant(revert)}
                if result["revert"]["outcome"] in KILLS:
                    return finish("proven", "fails when the change is reverted")

        sources = [c for c in covers if c.endswith(".py") and (root / c).is_file()]
        candidates = []
        for f in sources:
            lines = _changed_lines(root, base_sha, f) if base_sha else None
            candidates += [(f, line, op, src) for line, op, src in mutants((root / f).read_text(encoding="utf-8"), lines)]
        for f, line, op, src in candidates[:max_mutants]:
            try:
                ast.parse(src)
            except SyntaxError:
                not_run.append("unparsable mutant")
                result["mutants"].append({"file": f, "line": line, "op": op, "outcome": "not_run", "details": "mutant does not parse"})
                continue
            got = variant(lambda tree: (tree / f).write_text(src, encoding="utf-8"))
            result["mutants"].append({"file": f, "line": line, "op": op, **got})
            if got["outcome"] in KILLS:
                return finish("proven", f"fails under mutation {op} at {f}:{line}")
    finally:
        signal.signal(signal.SIGTERM, previous)

    if not_run:
        return finish("unproven", f"inconclusive: {len(not_run)} variants not run ({', '.join(sorted(set(not_run)))})")
    if not sources and not result["revert"]:
        return finish("unproven", "no Python source to mutate")
    return finish("unproven", "no revert or mutation made it fail")
