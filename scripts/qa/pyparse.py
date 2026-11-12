"""ast.parse without SyntaxWarnings from other people's files leaking to stderr."""

from __future__ import annotations

import ast
import warnings


def parse(source: str) -> ast.Module:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        return ast.parse(source)
