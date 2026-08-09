"""What generated workflow code is allowed to import and call.

One source of truth for two enforcement points that must not disagree:

- `codegen/verifier.py` rejects a violation at commit time, so the author gets
  a fixable error in chat instead of a run that dies at 3am.
- `build_safe_builtins()` enforces the same policy inside the process that
  actually execs the code, because the verifier only ever sees the source that
  went through codegen -- a version row edited by any other path, or a verifier
  bug, must not become arbitrary code execution.

The list is an allowlist. The previous denylist named `subprocess`/`socket`/
`requests` and omitted `os`, `sys`, `io`, `pathlib`, `builtins` and `posix`,
so `import os; os.system(...)` passed verification and then ran as the service
user with the whole application on `PYTHONPATH`. Enumerating what is safe is
the only form of this check that fails closed against the next module nobody
thought of.
"""
from __future__ import annotations

import builtins
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from types import ModuleType

__all__ = [
    "ALLOWED_IMPORT_ROOTS",
    "ALLOWED_MODULE_PREFIXES",
    "DISALLOWED_BUILTINS",
    "ESCAPE_ATTRIBUTES",
    "build_safe_builtins",
    "is_module_allowed",
]

ALLOWED_IMPORT_ROOTS = frozenset({
    "__future__",
    "abc",
    "base64",
    "collections",
    "dataclasses",
    "datetime",
    "decimal",
    "enum",
    "functools",
    "hashlib",
    "itertools",
    "json",
    "math",
    "operator",
    "re",
    "statistics",
    "string",
    "textwrap",
    "typing",
    "uuid",
})
"""Pure-computation stdlib. Nothing here opens a file, a socket, or a process.

`datetime` and `uuid` are allowed despite being the source of the two most
common determinism bugs, because parsing and type-annotating with them is
normal in workflow code (`datetime.fromisoformat` on a tool result). The
determinism lint is what rejects the non-deterministic *calls*
(`datetime.now()`, `uuid.uuid4()`) and names `ctx.now()`/`ctx.uuid()` as the
journaled replacements -- an import ban would reject the safe uses too.

`time`, `random` and `secrets` get no such exemption: `random.*` is rejected
wholesale by the same lint, and `time` additionally lets a workflow block its
sandbox slot with `sleep`.

`__future__` is a compiler directive rather than a module -- every generated
workflow opens with `from __future__ import annotations`, and it exposes
nothing.
"""

ALLOWED_MODULE_PREFIXES = ("app.services.workflows.sdk",)
"""The SDK the generated entry point imports.

Matched on the full dotted path rather than the `app` root: allowing the root
would also allow `app.config.configuration_service`, handing workflow code the
etcd client and every provider credential in it.
"""

DISALLOWED_BUILTINS = frozenset({
    "breakpoint",
    "compile",
    "delattr",
    "eval",
    "exec",
    "exit",
    "getattr",
    "globals",
    "help",
    "input",
    "locals",
    "memoryview",
    "open",
    "quit",
    "setattr",
    "vars",
})
"""Builtins removed from the sandbox namespace.

`eval`/`exec`/`compile`/`open` are the obvious ones. `getattr`/`setattr`/
`vars`/`globals`/`locals` are here because reflection reaches the same places
by another route: `getattr(x, "__class__")` walks to `object.__subclasses__()`
and from there to any loaded class, which is the standard Python sandbox
escape and needs no import at all.

`__import__` is not removed -- it is replaced by a guarded version below,
because the sandbox harness execs source that legitimately imports the SDK.
"""

ESCAPE_ATTRIBUTES = frozenset({
    "__bases__",
    "__builtins__",
    "__class__",
    "__closure__",
    "__code__",
    "__dict__",
    "__getattribute__",
    "__globals__",
    "__mro__",
    "__reduce__",
    "__reduce_ex__",
    "__subclasses__",
})
"""Attribute names that turn any object into a path back to the interpreter.

Removing `getattr` closes the dynamic route; this closes the literal one
(`().__class__.__bases__[0].__subclasses__()`), which needs no builtin at all.
"""


def is_module_allowed(module_name: str) -> bool:
    """True when workflow code may import `module_name`.

    Submodules of an allowed root are allowed (`collections.abc`); the root of
    a dotted path is what decides, so `os.path` is rejected with `os`.
    """
    if not module_name:
        return False
    if any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in ALLOWED_MODULE_PREFIXES
    ):
        return True
    return module_name.split(".", 1)[0] in ALLOWED_IMPORT_ROOTS


def build_safe_builtins() -> dict[str, Any]:
    """The `__builtins__` mapping to exec workflow source under.

    Returns a plain dict rather than the `builtins` module, so the exec'd code
    cannot reach unlisted names by attribute access on the module object.
    """
    safe = {
        name: value
        for name, value in vars(builtins).items()
        if name not in DISALLOWED_BUILTINS
    }
    safe["__import__"] = _guarded_import
    return safe


def _guarded_import(
    name: str,
    globals_: "dict[str, Any] | None" = None,
    locals_: "dict[str, Any] | None" = None,
    fromlist: "tuple[str, ...]" = (),
    level: int = 0,
) -> "ModuleType":
    """`__import__` restricted to `is_module_allowed`.

    The harness cannot simply drop `__import__`: the generated source imports
    the SDK by name, so every `import` statement in it routes through here.
    Relative imports (`level > 0`) are rejected outright -- workflow source is
    a single module with no package to be relative to, so a non-zero level can
    only be an attempt to walk somewhere else.
    """
    if level != 0:
        raise ImportError(
            f"relative import of {name!r} is not allowed in workflow code",
        )
    if not is_module_allowed(name):
        raise ImportError(
            f"import of {name!r} is not allowed in workflow code. "
            f"Allowed modules: {', '.join(sorted(ALLOWED_IMPORT_ROOTS))}. "
            "Use ctx.tool() for external I/O."
        )
    return builtins.__import__(name, globals_, locals_, fromlist, level)
