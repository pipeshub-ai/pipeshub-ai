"""Name resolution for the AST passes over workflow source.

The verifier and the IR extractor both have to answer "is this `@workflow`?"
and "is this `cron(...)`?", and generated code writes those three ways —
`@workflow`, `@sdk.workflow`, `@workflows.sdk.workflow`, with or without a
call. Each pass having its own matcher is how `@sdk.workflow(name=...)` came
to fail verification as "no entrypoint" while also extracting to an empty IR
(so an empty graph and no declarative triggers).
"""
from __future__ import annotations

import ast

__all__ = ["callable_name", "decorator_name"]


def callable_name(node: ast.expr | None) -> str:
    """Trailing name of a call target: `cron`, `sdk.cron` and `t.sdk.cron` all
    resolve to `cron`. Returns "" for anything not name-like."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def decorator_name(decorator: ast.expr) -> str:
    """Trailing name of a decorator, applied or not: `@workflow`,
    `@sdk.workflow` and `@sdk.workflow(name="x")` all resolve to `workflow`."""
    if isinstance(decorator, ast.Call):
        return callable_name(decorator.func)
    return callable_name(decorator)
