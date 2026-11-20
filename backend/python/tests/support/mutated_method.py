"""Swap one method of a class for a copy of itself with its source edited, for mutation controls.

A control proves a test can fail: edit the code under test in a small, wrong way, run the test, and expect it to
notice. The copy is compiled inside a class of the same name, so name-mangled attributes (``self.__x``) still resolve,
and runs with the defining module's globals, so it sees everything the original does.
"""
from __future__ import annotations

import inspect
import sys
import textwrap
from typing import Any


def mutate(cls: type, method: str, replacements: list[tuple[str, str]]) -> None:
    """Set ``cls.<method>`` to its source with each ``(old, new)`` applied. ``method`` is the attribute name, mangled
    if it is private. Every ``old`` must be found, so a refactor that moves the code fails here and not silently."""
    source = textwrap.dedent(inspect.getsource(getattr(cls, method)))
    for old, new in replacements:
        if old not in source:
            raise AssertionError(f"mutation target not found in {cls.__name__}.{method}: {old!r}")
        source = source.replace(old, new)
    module = sys.modules[cls.__module__]
    namespace: dict[str, Any] = {}
    body = textwrap.indent(source, "    ")
    exec(compile(f"class {cls.__name__}:\n{body}", f"<mutant {cls.__name__}.{method}>", "exec"), vars(module), namespace)
    function = next(v for k, v in vars(namespace[cls.__name__]).items() if inspect.isfunction(v) or k == method)
    setattr(cls, method, function)
