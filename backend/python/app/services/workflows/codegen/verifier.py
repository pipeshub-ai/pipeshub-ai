"""Workflow code verifier — the correctness backbone of the codegen loop.

Pipeline:
1. Syntax check — ast.parse(). Stops here: nothing else has a parseable AST.
2. Security lint — banned imports, raw clock/random usage. Stops here: the
   dry-exec compile step (7) must never execute code that failed this.
3. Policy scan — @workflow required, exactly one entry point, async           \\
4. Module-level statement lint — only imports/defs/constants at module scope  |  batched:
5. Decorator signature check — @step/@workflow kwargs vs real signatures     |  pure AST
6. Missing-`await` check — async `Ctx` methods called without `await`        |  reads, so
7. Tool grant check — `ctx.tool(...)` names must be literal and granted      /  one repair
                                                                                 round can
                                                                                 fix all of
                                                                                 them.
8. Dry-exec compile — actually exec() the module body (decorator application
   only; step/workflow bodies are coroutines and never run) against the real
   SDK, to catch the infinite tail of SDK misuses no AST heuristic covers.

Each check returns structured errors: {code, field, fix_hint}
so the repair loop can inject them back into the model's context.
"""
from __future__ import annotations

import ast
import inspect
import re
import signal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from types import FrameType

from app.services.workflows.ast_names import decorator_name
from app.services.workflows.sdk.decorators import step as _step_fn
from app.services.workflows.sdk.decorators import workflow as _workflow_fn
from app.services.workflows.security.sandbox_policy import (
    ALLOWED_IMPORT_ROOTS,
    DISALLOWED_BUILTINS,
    ESCAPE_ATTRIBUTES,
    is_module_allowed,
)

__all__ = [
    "verify_workflow_source",
    "VerificationResult",
    "VerificationError",
    "CURRENT_VERIFIER_VERSION",
    "FATAL_AT_RUNTIME_CODES",
    "is_version_stale",
]


class VerificationError:
    def __init__(self, code: str, field: str, fix_hint: str, line: int | None = None) -> None:
        self.code = code
        self.field = field
        self.fix_hint = fix_hint
        self.line = line

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "field": self.field, "fix_hint": self.fix_hint, "line": self.line}


class VerificationResult:
    def __init__(self, errors: list[VerificationError]) -> None:
        self.errors = errors

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "errors": [e.to_dict() for e in self.errors]}


_BANNED_BUILTINS = frozenset({"__import__", *DISALLOWED_BUILTINS})

_RAW_CLOCK_PATTERNS = [
    (r"\bdatetime\.now\s*\(", "Use await ctx.now() instead of datetime.now()"),
    (r"\bdate\.today\s*\(", "Use await ctx.now() instead of date.today()"),
    (r"\btime\.time\s*\(", "Use await ctx.now() instead of time.time()"),
]

_RAW_RANDOM_PATTERNS = [
    (r"\brandom\.\b(?!seed)", "Use await ctx.random() instead of random.*"),
    (r"\bsecrets\.token", "Use await ctx.uuid() instead of secrets.*"),
]

_RAW_UUID_PATTERNS = [
    (r"\buuid\.uuid4\s*\(", "Use await ctx.uuid() instead of uuid.uuid4()"),
    (r"\buuid\.uuid1\s*\(", "Use await ctx.uuid() instead of uuid.uuid1()"),
]

# Every `async def` method on `Ctx` that the LLM is told to call directly
# (excludes `_AgentHandle`/`_StateProxy` methods like `.run()`/`.get()`/
# `.set()`, whose names are too generic to match safely by attribute alone).
# `agent`/`create_agent` are async too and were missing here — an unawaited
# `ctx.agent("x")` produced the exact 'coroutine' object has no attribute ...`
# class of failure this whole module exists to catch.
_ASYNC_CTX_METHODS = frozenset({
    "now", "random", "uuid", "tool", "sleep",
    "wait_for_event", "request_approval", "emit", "search", "map",
    "agent", "create_agent",
})

# Error codes that mean the generated code is *guaranteed* to crash at
# runtime, as opposed to advisory/policy codes (e.g. UNGRANTED_TOOL) that the
# broker already enforces mid-run. Used to decide which stale, already-pinned
# versions are worth flagging as needing regeneration (see Phase 4 /
# `application/version_writer.py`) without a new advisory rule invalidating
# the whole fleet the moment it ships.
FATAL_AT_RUNTIME_CODES = frozenset({
    "SYNTAX_ERROR",
    "MODULE_LEVEL_STATEMENT",
    "INVALID_DECORATOR_ARG",
    "MISSING_AWAIT",
    "SDK_COMPILE_ERROR",
    "SDK_COMPILE_TIMEOUT",
})

# Bump whenever a rule feeding `FATAL_AT_RUNTIME_CODES` changes (new check
# added, existing one tightened). `WorkflowVersion.verifier_version` records
# the value in effect when a version was generated, so versions pinned
# before a fix landed can be told apart from ones that already account for
# it — see `application/version_writer.py` and `is_version_stale()` below.
CURRENT_VERIFIER_VERSION = 1


def is_version_stale(verifier_version: int) -> bool:
    """True when a persisted version predates the current fatal-error rule
    set and should be flagged for regeneration rather than trusted blindly."""
    return verifier_version < CURRENT_VERIFIER_VERSION

# Introspected from the *real* decorator functions rather than a
# hand-maintained duplicate list, so this check can never drift from the SDK
# as it evolves — the failure mode this replaces is an LLM inventing a
# plausible-but-nonexistent kwarg (e.g. `@step(name=...)`, borrowed from
# other workflow frameworks) that only surfaces as a `TypeError` deep in a
# sandboxed subprocess, long after codegen already reported success.
_DECORATOR_SIGNATURES: dict[str, "inspect.Signature"] = {
    "step": inspect.signature(_step_fn),
    "workflow": inspect.signature(_workflow_fn),
}


def _lint_imports(tree: ast.AST) -> list[VerificationError]:
    """AST-based import allowlist plus reflection-escape check.

    Walking the tree rather than regexing lines catches `from os import
    system`, `import os as _o`, and `__import__("os")` alike. The allowlist
    lives in `security.sandbox_policy` so this check and the builtins the
    sandbox execs under cannot drift apart.
    """
    errors: list[VerificationError] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not is_module_allowed(alias.name):
                    errors.append(_banned_import_error(alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            # `level > 0` is `from . import x`: no package to resolve against
            # in a single-module workflow, so `node.module` alone would read
            # as an allowed absolute import.
            module = node.module or ""
            if node.level > 0 or not is_module_allowed(module):
                errors.append(_banned_import_error(module or ".", node.lineno))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in _BANNED_BUILTINS
        ):
            errors.append(VerificationError(
                code="BANNED_BUILTIN",
                field=f"line:{node.lineno}",
                fix_hint=(
                    f"'{node.func.id}(...)' is not allowed in workflow code; "
                    "it bypasses the import allowlist and replay determinism."
                ),
                line=node.lineno,
            ))
        elif isinstance(node, ast.Attribute) and node.attr in ESCAPE_ATTRIBUTES:
            # `().__class__.__bases__[0].__subclasses__()` reaches every loaded
            # class without importing anything, so the allowlist above does not
            # cover it.
            errors.append(VerificationError(
                code="BANNED_ATTRIBUTE",
                field=f"line:{node.lineno}",
                fix_hint=(
                    f"Attribute '{node.attr}' is not allowed in workflow code; "
                    "introspecting the interpreter escapes the sandbox."
                ),
                line=node.lineno,
            ))
    return errors


def _banned_import_error(module: str, line: int) -> VerificationError:
    return VerificationError(
        code="BANNED_IMPORT",
        field=f"line:{line}",
        fix_hint=(
            f"Import of '{module}' is not allowed. Workflow code may import "
            f"{', '.join(sorted(ALLOWED_IMPORT_ROOTS))} and the workflow SDK. "
            "Use ctx.tool() for external I/O and ctx.now()/ctx.random()/"
            "ctx.uuid() for non-deterministic values."
        ),
        line=line,
    )


def _lint_security(source: str) -> list[VerificationError]:
    errors = []
    for line_no, line in enumerate(source.splitlines(), start=1):
        for pattern, hint in _RAW_CLOCK_PATTERNS:
            if re.search(pattern, line):
                errors.append(VerificationError(
                    code="RAW_CLOCK",
                    field=f"line:{line_no}",
                    fix_hint=hint,
                    line=line_no,
                ))
        for pattern, hint in _RAW_RANDOM_PATTERNS:
            if re.search(pattern, line):
                errors.append(VerificationError(
                    code="RAW_RANDOM",
                    field=f"line:{line_no}",
                    fix_hint=hint,
                    line=line_no,
                ))
        for pattern, hint in _RAW_UUID_PATTERNS:
            if re.search(pattern, line):
                errors.append(VerificationError(
                    code="RAW_UUID",
                    field=f"line:{line_no}",
                    fix_hint=hint,
                    line=line_no,
                ))
    return errors


def _lint_missing_await(tree: ast.AST) -> list[VerificationError]:
    """Catch `ctx.now()` used without `await`.

    All I/O and non-determinism on `Ctx` (`now`, `random`, `uuid`, `tool`,
    ...) is `async def`. Calling it without `await` returns a coroutine
    object instead of the value, which fails far from the mistake (e.g.
    `'coroutine' object has no attribute 'astimezone'`) instead of at
    generation time. Only matches calls on a variable literally named
    `ctx`, per the SDK convention every step/workflow function follows.
    """
    awaited_call_ids = {
        id(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call)
    }
    errors: list[VerificationError] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if not isinstance(node.func.value, ast.Name) or node.func.value.id != "ctx":
            continue
        if node.func.attr not in _ASYNC_CTX_METHODS:
            continue
        if id(node) in awaited_call_ids:
            continue
        errors.append(VerificationError(
            code="MISSING_AWAIT",
            field=f"line:{node.lineno}",
            fix_hint=(
                f"'ctx.{node.func.attr}(...)' is an async method and must be "
                f"awaited: use 'await ctx.{node.func.attr}(...)'. Without "
                "await this returns a coroutine object, not the value."
            ),
            line=node.lineno,
        ))
    return errors


_ALLOWED_MODULE_LEVEL_NODES = (
    ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef,
    ast.ClassDef, ast.Assign, ast.AnnAssign,
)


def _lint_module_level_statements(tree: ast.Module) -> list[VerificationError]:
    """Reject module-level code that is not an import, a def, or a constant.

    Module-level statements run at import time — in the dry-exec check (8)
    below and in the real sandbox harness's `exec(compiled, ns)` alike. A
    top-level loop or call is both a determinism hazard (it runs once at
    generation-adjacent time, not once per run) and, for the in-process dry
    exec, an unbounded-resource hazard: a `while True` or a large list
    literal has no rlimit to stop it outside the subprocess sandbox. This
    check is the primary defence against that, independent of the dry exec.
    """
    errors: list[VerificationError] = []
    for node in tree.body:
        if isinstance(node, ast.Expr):
            if isinstance(node.value, ast.Constant):
                continue  # docstring
            errors.append(VerificationError(
                code="MODULE_LEVEL_STATEMENT",
                field=f"line:{node.lineno}",
                fix_hint=(
                    "A bare expression/call at module level is not allowed. "
                    "Put all logic inside @step or @workflow functions — "
                    "module-level code runs at import time, before the "
                    "workflow starts."
                ),
                line=node.lineno,
            ))
        elif not isinstance(node, _ALLOWED_MODULE_LEVEL_NODES):
            errors.append(VerificationError(
                code="MODULE_LEVEL_STATEMENT",
                field=f"line:{node.lineno}",
                fix_hint=(
                    f"'{type(node).__name__}' at module level is not allowed. "
                    "Put all logic inside @step or @workflow functions — "
                    "module-level code runs at import time, before the "
                    "workflow starts."
                ),
                line=node.lineno,
            ))
    return errors


_DRY_EXEC_TIMEOUT_S = 5


class _DryExecTimeout(Exception):
    pass


def _dry_exec_alarm_handler(signum: int, frame: "FrameType | None") -> None:
    raise _DryExecTimeout("dry-exec compile step exceeded its time budget")


def _lint_dry_exec(source: str) -> list[VerificationError]:
    """Actually exec() the generated source against the real SDK to catch
    TypeError/AttributeError/NameError at decoration time — the "compile
    against the SDK" step that generically catches any SDK misuse without
    needing a per-symptom AST heuristic for each one.

    Safe because:
    - Security lint (`_lint_imports`/`_lint_security`) already ran and this
      is only reached if it passed.
    - `_lint_module_level_statements` already rejected anything but imports,
      defs, and constants, so this can only run decorator application —
      `async def` bodies never execute until awaited.
    - The import guard (`_guarded_import` via `build_safe_builtins()`)
      restricts imports to the SDK + safe stdlib, identical to the real
      sandbox harness.

    Bounded by a best-effort wall-clock alarm as defence in depth; the
    module-level-statement lint is the primary guard since a thread/signal
    timeout cannot forcibly kill code that ignores it (e.g. a tight
    `while True` with no I/O yields).
    """
    from app.services.workflows.security.sandbox_policy import build_safe_builtins

    ns: dict[str, Any] = {"__builtins__": build_safe_builtins(), "__name__": "__main__"}
    alarm_armed = False
    previous_handler = None
    try:
        previous_handler = signal.signal(signal.SIGALRM, _dry_exec_alarm_handler)
        signal.alarm(_DRY_EXEC_TIMEOUT_S)
        alarm_armed = True
    except (ValueError, OSError, AttributeError):
        # Not the main thread, or a platform without SIGALRM (e.g. Windows).
        # `_lint_module_level_statements` remains the primary defence.
        alarm_armed = False

    try:
        compiled = compile(source, "<generated>", "exec")
        exec(compiled, ns)
    except _DryExecTimeout:
        return [VerificationError(
            code="SDK_COMPILE_TIMEOUT",
            field="source",
            fix_hint=(
                f"Compiling the generated code against the SDK did not finish "
                f"within {_DRY_EXEC_TIMEOUT_S}s. This usually means a "
                "module-level loop or blocking call. Move all logic inside "
                "@step or @workflow functions."
            ),
        )]
    except (TypeError, AttributeError, NameError, ImportError) as exc:
        return [VerificationError(
            code="SDK_COMPILE_ERROR",
            field="source",
            fix_hint=(
                f"Code failed to compile against the SDK: {type(exc).__name__}: {exc}. "
                "This usually means a decorator was called with invalid arguments, "
                "an import is wrong, or a name is undefined. Check the SDK reference "
                "for the correct signatures."
            ),
        )]
    except Exception:
        # Anything else (e.g. a network/runtime error from module-level code
        # that slipped past the statement lint) is not a static SDK-usage
        # issue this check is meant to diagnose.
        return []
    finally:
        if alarm_armed:
            signal.alarm(0)
            if previous_handler is not None:
                signal.signal(signal.SIGALRM, previous_handler)
    return []


def _lint_decorator_signatures(tree: ast.AST) -> list[VerificationError]:
    """Validate `@step(...)`/`@workflow(...)` calls against the real
    decorator signatures via `inspect.signature(...).bind_partial(...)` —
    i.e. actually "compiling" the call against the SDK instead of hoping the
    LLM remembered the right kwargs. Catches unknown keywords (e.g. `name=`,
    which doesn't exist on `@step`) and positional arguments (both
    decorators are keyword-only when called with parens).

    Bare decorators (`@step`, `@workflow` with no call) have nothing to
    check. `**kwargs`-spread keywords (`ast.keyword(arg=None)`) are skipped
    since their keys aren't statically known — same leniency the tool-grant
    check applies to computed tool names.
    """
    errors: list[VerificationError] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            name = decorator_name(dec)
            sig = _DECORATOR_SIGNATURES.get(name)
            if sig is None:
                continue
            valid_params = ", ".join(p for p in sig.parameters if not p.startswith("_"))
            if dec.args:
                errors.append(VerificationError(
                    code="INVALID_DECORATOR_ARG",
                    field=f"line:{dec.lineno}",
                    fix_hint=(
                        f"@{name}(...) takes keyword arguments only, not positional. "
                        f"Valid parameters: {valid_params}."
                    ),
                    line=dec.lineno,
                ))
                continue
            kwarg_names = [kw.arg for kw in dec.keywords if kw.arg is not None]
            if len(kwarg_names) != len(dec.keywords):
                continue  # a **spread keyword is present; can't statically check it
            try:
                sig.bind_partial(**dict.fromkeys(kwarg_names))
            except TypeError as exc:
                errors.append(VerificationError(
                    code="INVALID_DECORATOR_ARG",
                    field=f"line:{dec.lineno}",
                    fix_hint=f"@{name}(...) call is invalid: {exc}. Valid parameters: {valid_params}.",
                    line=dec.lineno,
                ))
    return errors


def _lint_syntax(source: str) -> list[VerificationError]:
    try:
        ast.parse(source)
        return []
    except SyntaxError as exc:
        return [VerificationError(
            code="SYNTAX_ERROR",
            field=f"line:{exc.lineno}",
            fix_hint=f"SyntaxError: {exc.msg}. Fix the syntax at line {exc.lineno}.",
            line=exc.lineno,
        )]


def _lint_policy(tree: ast.AST) -> list[VerificationError]:
    """Check for required structural elements."""
    errors: list[VerificationError] = []

    entrypoints = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef))
        and any(decorator_name(d) == "workflow" for d in node.decorator_list)
    ]

    if not entrypoints:
        errors.append(VerificationError(
            code="NO_WORKFLOW_ENTRY",
            field="source",
            fix_hint="No @workflow-decorated function found. Every workflow file must have exactly one.",
        ))
        return errors

    if len(entrypoints) > 1:
        # The runner resolves a single entry point; two would make which one
        # executes depend on definition order.
        names = ", ".join(fn.name for fn in entrypoints)
        errors.append(VerificationError(
            code="MULTIPLE_WORKFLOW_ENTRIES",
            field="source",
            fix_hint=(
                f"Found {len(entrypoints)} @workflow functions ({names}). "
                "Keep exactly one entry point and turn the others into @step functions."
            ),
            line=entrypoints[1].lineno,
        ))

    errors.extend(
        VerificationError(
            code="WORKFLOW_NOT_ASYNC",
            field=f"line:{fn.lineno}",
            fix_hint=f"'{fn.name}' must be declared 'async def' — workflows are awaited by the runner.",
            line=fn.lineno,
        )
        for fn in entrypoints
        if isinstance(fn, ast.FunctionDef)
    )

    return errors


def _lint_tool_grant(tree: ast.AST, allowed_tools: "frozenset[str]") -> list[VerificationError]:
    """Reject `ctx.tool("x")` for a tool the workflow was not granted.

    Catches at generation time what the broker would otherwise only reject
    mid-run, when half the workflow's writes have already happened.
    """
    from app.services.workflows.interface.broker import normalize_tool_name

    normalized_allowed = {
        normalized
        for normalized in (normalize_tool_name(name) for name in allowed_tools)
        if normalized is not None
    }
    errors: list[VerificationError] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr == "agent":
            errors.extend(_lint_agent_grant(node))
            continue
        if node.func.attr != "tool":
            continue
        if not node.args:
            continue
        target_node = node.args[0]
        if not isinstance(target_node, ast.Constant) or not isinstance(target_node.value, str):
            # A computed name defeats both this lint and `tool_pins`, which is
            # derived from the same constants -- the run would either widen its
            # own grant or fail mid-execution.
            errors.append(VerificationError(
                code="DYNAMIC_TOOL_NAME",
                field=f"line:{node.lineno}",
                fix_hint=(
                    "ctx.tool() requires a string literal as its first argument. "
                    "A computed tool name cannot be checked against this workflow's "
                    "grant before the run starts; branch on the result instead of "
                    "on the tool name."
                ),
                line=node.lineno,
            ))
            continue
        target = target_node.value
        if not normalized_allowed:
            continue
        if normalize_tool_name(target) not in normalized_allowed:
            errors.append(VerificationError(
                code="UNGRANTED_TOOL",
                field=f"line:{node.lineno}",
                fix_hint=(
                    f"ctx.tool({target!r}) is not in this workflow's granted tools "
                    f"({', '.join(sorted(allowed_tools)) or 'none'}). "
                    "Use a granted tool or ask the user to add it to the workflow."
                ),
                line=node.lineno,
            ))
    return errors


def _lint_agent_grant(node: ast.Call) -> list[VerificationError]:
    """Require a literal agent id in `ctx.agent(...)`.

    `WorkflowVersion.agent_pins` is read off these literals and becomes the
    run's `grant.agent_ids`; a computed id contributes no pin, and an empty
    `agent_ids` is what the broker reads as "any agent in this org".
    """
    if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
        return []
    return [VerificationError(
        code="DYNAMIC_AGENT_ID",
        field=f"line:{node.lineno}",
        fix_hint=(
            "ctx.agent() requires a string literal as its first argument. "
            "A computed agent id cannot be pinned into this workflow's grant "
            "before the run starts."
        ),
        line=node.lineno,
    )]


def verify_workflow_source(
    source: str,
    *,
    allowed_tools: "list[str] | frozenset[str] | None" = None,
) -> VerificationResult:
    """Run all verification checks, batching errors so one repair round can
    fix everything the model got wrong instead of discovering one category
    per attempt.

    Only two categories still short-circuit, because a later check cannot
    safely run without them:
    - Syntax: everything else needs a parseable AST.
    - Security/imports: the dry-exec step below must never execute code that
      failed this — it would otherwise `exec()` a banned import or builtin.

    Every other check is a pure AST read (or, for dry-exec, only reachable
    once module-level statements are already constrained), so they run and
    report together. Previously each returned early on its own, which meant
    code with problems in N different categories could never pass within a
    bounded repair-attempt budget: each attempt revealed exactly one more
    category, so the model appeared to be "failing repeatedly" while it was
    actually fixing everything it had been told about, one layer at a time.

    `allowed_tools` is the workflow's tool grant; when omitted (or empty) only
    the grant comparison is skipped -- `ctx.tool()` must still name a literal,
    since `WorkflowVersion.tool_pins` is derived from those literals and a
    computed name would leave the run unpinned.
    """
    syntax_errors = _lint_syntax(source)
    if syntax_errors:
        return VerificationResult(syntax_errors)

    tree = ast.parse(source)

    security_errors = _lint_imports(tree) + _lint_security(source)
    if security_errors:
        return VerificationResult(security_errors)

    errors = (
        _lint_policy(tree)
        + _lint_module_level_statements(tree)
        + _lint_decorator_signatures(tree)
        + _lint_missing_await(tree)
        + _lint_tool_grant(tree, frozenset(allowed_tools or ()))
    )
    if errors:
        return VerificationResult(errors)

    return VerificationResult(_lint_dry_exec(source))
