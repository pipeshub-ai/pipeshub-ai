#!/usr/bin/env python3
"""Find emoji in the text of Python log calls.

The level already says what a ✅, ❌ or ⚠️ would, and emoji break grep and
log parsers. A log call is a call to a method named after a level
(``logger.info(...)``, ``self.logger.error(...)``, ``logging.warning(...)``,
``log.log(level, ...)``). Only its direct arguments are read: a string, the
literal parts of an f-string, and the operands of ``%`` or ``+``. A string
inside a nested call (``logger.info("%s", s.split("✅"))``) is not log text.
A non-logging method with a level name (``warnings.warn``) is flagged too.

Prints ruff-shaped JSON (filename, code, location.row) for
.github/scripts/new_lint_findings.py, which fails a pull request only on the
findings it adds. A file that does not parse has no findings; ruff reports it.

Usage: log_emoji.py <file>...
"""
import ast
import json
import os
import re
import sys

CODE = "LOG-EMOJI"
LOG_METHODS = frozenset({"debug", "info", "warning", "warn", "error", "exception", "critical", "fatal", "log"})
EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2300-\u23FF\u2B00-\u2BFF\uFE0F\u2139\u203C\u2049]"
)


def _texts(node: ast.expr):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value
    elif isinstance(node, ast.JoinedStr):
        for part in node.values:
            yield from _texts(part)
    elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mod, ast.Add)):
        yield from _texts(node.left)
        yield from _texts(node.right)


def findings(source: str) -> list[int]:
    """Line numbers of the log calls in ``source`` whose text holds an emoji."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    rows = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in LOG_METHODS:
            continue
        args = [*node.args, *(k.value for k in node.keywords)]
        if any(EMOJI.search(text) for arg in args for text in _texts(arg)):
            rows.append(node.lineno)
    return rows


def main(paths: list[str]) -> None:
    out = []
    for path in paths:
        with open(path, encoding="utf-8", errors="replace") as f:
            source = f.read()
        out += [{"filename": os.path.abspath(path), "code": CODE, "location": {"row": row}} for row in findings(source)]
    json.dump(out, sys.stdout)


if __name__ == "__main__":
    main(sys.argv[1:])
