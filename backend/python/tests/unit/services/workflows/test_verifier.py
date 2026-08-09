"""The verifier is the last gate before generated code becomes a stored,
schedulable version, so it has to hold against code an LLM plausibly writes
rather than only the textbook form: aliased imports, `from x import y`, and
`__import__` all reach the same banned module a line regex would miss.
"""
from __future__ import annotations

import pytest

from app.services.workflows.codegen.verifier import _lint_dry_exec, verify_workflow_source

_VALID = """
from app.services.workflows.sdk import Ctx, workflow

@workflow
async def my_workflow(ctx: Ctx) -> str:
    return "done"
"""


def _codes(source: str, **kwargs: object) -> list[str]:
    return [e.code for e in verify_workflow_source(source, **kwargs).errors]


class TestBaseline:
    def test_a_minimal_valid_workflow_passes(self) -> None:
        assert verify_workflow_source(_VALID).ok

    def test_syntax_errors_are_reported_with_a_line(self) -> None:
        result = verify_workflow_source("def broken(:\n    pass")
        assert not result.ok
        assert result.errors[0].code == "SYNTAX_ERROR"
        assert result.errors[0].line is not None


class TestImportBan:
    @pytest.mark.parametrize(
        "statement",
        [
            "import subprocess",
            "import subprocess as sp",
            "from subprocess import run",
            "import socket",
            "from urllib.request import urlopen",
            "import requests",
        ],
    )
    def test_every_spelling_of_a_banned_import_is_caught(self, statement: str) -> None:
        assert "BANNED_IMPORT" in _codes(f"{statement}\n{_VALID}")

    def test_dunder_import_cannot_be_used_to_bypass_the_ban(self) -> None:
        source = _VALID.replace('return "done"', 'return __import__("socket")')
        assert "BANNED_BUILTIN" in _codes(source)

    def test_eval_and_exec_are_rejected(self) -> None:
        for builtin in ("eval", "exec"):
            source = _VALID.replace('return "done"', f'return {builtin}("1+1")')
            assert "BANNED_BUILTIN" in _codes(source)

    def test_an_allowed_stdlib_import_is_untouched(self) -> None:
        assert verify_workflow_source(f"import json\n{_VALID}").ok


class TestDeterminism:
    def test_raw_clock_is_rejected_in_favour_of_ctx_now(self) -> None:
        source = _VALID.replace('return "done"', "return datetime.now().isoformat()")
        assert "RAW_CLOCK" in _codes(source)

    def test_raw_uuid_is_rejected_in_favour_of_ctx_uuid(self) -> None:
        source = _VALID.replace('return "done"', "return str(uuid.uuid4())")
        assert "RAW_UUID" in _codes(source)


class TestMissingAwait:
    """Regression: the LLM occasionally writes `ctx.now().astimezone(...)`
    without `await`, which returns a coroutine object instead of a
    `datetime` and fails at runtime far from the actual mistake."""

    @pytest.mark.parametrize(
        "call",
        [
            "ctx.now()",
            "ctx.random()",
            "ctx.uuid()",
            'ctx.tool("jira__create_issue")',
            "ctx.sleep(5)",
            'ctx.wait_for_event("x")',
            'ctx.request_approval("x")',
            'ctx.emit("x")',
            'ctx.search("x")',
        ],
    )
    def test_unawaited_ctx_call_is_rejected(self, call: str) -> None:
        source = _VALID.replace("return \"done\"", f"return {call}")
        assert "MISSING_AWAIT" in _codes(source)

    def test_awaited_ctx_call_passes(self) -> None:
        source = _VALID.replace('return "done"', "return await ctx.now()")
        assert verify_workflow_source(source).ok

    def test_unawaited_call_chained_off_ctx_now_is_still_caught(self) -> None:
        """The exact regression: `.astimezone()` chained onto the unawaited
        coroutine, not the bare call."""
        source = _VALID.replace(
            'return "done"', "return ctx.now().astimezone()",
        )
        assert "MISSING_AWAIT" in _codes(source)

    def test_unrelated_object_method_of_the_same_name_is_not_flagged(self) -> None:
        """Only `ctx.<method>()` is checked; a same-named method on some
        other object (e.g. a dict's `.get()`) must not false-positive."""
        source = _VALID.replace(
            'return "done"', 'return other.now()',
        )
        assert "MISSING_AWAIT" not in _codes(source)


class TestDecoratorSignatures:
    """Regression: the LLM invented a `name=` kwarg for `@step(...)`,
    borrowing the convention from other workflow frameworks (Prefect/
    Dagster). This check validates decorator kwargs against the *real*
    `step`/`workflow` signatures via `inspect`, so any invented kwarg is
    caught — not just this specific one."""

    def test_nonexistent_step_kwarg_is_rejected(self) -> None:
        source = _VALID.replace(
            "@workflow\nasync def my_workflow(ctx: Ctx) -> str:",
            '@step(name="fetch_data", side_effect=SideEffect.READ)\n'
            "async def fetch(ctx: Ctx) -> str:\n"
            '    return "x"\n\n'
            "@workflow\nasync def my_workflow(ctx: Ctx) -> str:",
        )
        errors = _codes(source)
        assert "INVALID_DECORATOR_ARG" in errors

    def test_nonexistent_workflow_kwarg_is_rejected(self) -> None:
        source = _VALID.replace(
            "@workflow\n", '@workflow(concurrency=4)\n',
        )
        assert "INVALID_DECORATOR_ARG" in _codes(source)

    def test_valid_step_kwargs_pass(self) -> None:
        source = _VALID.replace(
            "from app.services.workflows.sdk import Ctx, workflow",
            "from app.services.workflows.sdk import Ctx, SideEffect, step, workflow",
        ).replace(
            "@workflow\nasync def my_workflow(ctx: Ctx) -> str:",
            "@step(retries=3, timeout_s=30.0, side_effect=SideEffect.WRITE)\n"
            "async def fetch(ctx: Ctx) -> str:\n"
            '    return "x"\n\n'
            "@workflow\nasync def my_workflow(ctx: Ctx) -> str:",
        )
        assert verify_workflow_source(source).ok

    def test_valid_workflow_kwargs_pass(self) -> None:
        source = _VALID.replace(
            "@workflow\n", '@workflow(name="my_flow")\n',
        )
        assert verify_workflow_source(source).ok

    def test_bare_decorator_has_nothing_to_check(self) -> None:
        assert verify_workflow_source(_VALID).ok

    def test_positional_args_are_rejected(self) -> None:
        source = _VALID.replace('@workflow\n', '@workflow("my_flow")\n')
        assert "INVALID_DECORATOR_ARG" in _codes(source)

    def test_spread_kwargs_are_not_statically_checked(self) -> None:
        """`**extra` can't be validated without evaluating it; treat it the
        same way the tool-grant check treats a computed tool name."""
        source = _VALID.replace(
            "@workflow\n", "extra = {}\n\n@workflow(**extra)\n",
        )
        assert verify_workflow_source(source).ok


class TestEntryPoint:
    def test_source_without_an_entry_point_is_rejected(self) -> None:
        assert "NO_WORKFLOW_ENTRY" in _codes("async def helper(ctx):\n    return 1\n")

    def test_two_entry_points_are_rejected(self) -> None:
        """Which one runs would otherwise depend on definition order."""
        source = _VALID + """
@workflow
async def second_workflow(ctx: Ctx) -> str:
    return "also me"
"""
        assert "MULTIPLE_WORKFLOW_ENTRIES" in _codes(source)

    def test_a_sync_entry_point_is_rejected(self) -> None:
        source = _VALID.replace("async def my_workflow", "def my_workflow")
        assert "WORKFLOW_NOT_ASYNC" in _codes(source)

    def test_a_module_qualified_entry_point_counts_as_one(self) -> None:
        """`@sdk.workflow(...)` is as valid as `@workflow(...)`; rejecting it
        as "no entrypoint" sends the repair loop chasing a non-problem.

        `sdk` is a self-contained stand-in (rather than a real import) purely
        so the dry-exec compile step has something real to call — the point
        under test is the AST name resolution, not signature validation.
        """
        source = '''
class sdk:
    @staticmethod
    def workflow(**_kwargs):
        def _decorator(fn):
            return fn
        return _decorator

@sdk.workflow(name="mine")
async def my_workflow(ctx) -> str:
    return "done"
'''
        assert _codes(source) == []


class TestToolGrant:
    def test_an_ungranted_tool_is_rejected_at_generation_time(self) -> None:
        """Otherwise the broker rejects it mid-run, after earlier steps in the
        same workflow have already written to real systems."""
        source = _VALID.replace(
            'return "done"', 'return await ctx.tool("slack__post_message", text="hi")',
        )
        assert "UNGRANTED_TOOL" in _codes(source, allowed_tools=["jira__create_issue"])

    def test_a_granted_tool_passes(self) -> None:
        source = _VALID.replace(
            'return "done"', 'return await ctx.tool("jira__create_issue", summary="x")',
        )
        assert verify_workflow_source(source, allowed_tools=["jira__create_issue"]).ok

    def test_no_grant_means_no_grant_check(self) -> None:
        """Matches the broker's convention that an empty grant is "every tool
        declared on the task", so the two layers cannot disagree."""
        source = _VALID.replace(
            'return "done"', 'return await ctx.tool("slack__post_message", text="hi")',
        )
        assert verify_workflow_source(source).ok
        assert verify_workflow_source(source, allowed_tools=[]).ok

    def test_a_dynamic_tool_name_is_rejected(self) -> None:
        """`tool_pins` -- and therefore the run's grant -- is derived from the
        same literals this lint reads, so a computed name would let a workflow
        widen its own authority by committing an expression."""
        source = _VALID.replace(
            'return "done"', "return await ctx.tool(chosen_tool)",
        )
        assert "DYNAMIC_TOOL_NAME" in _codes(source, allowed_tools=["jira__create_issue"])

    def test_a_dynamic_tool_name_is_rejected_even_with_no_grant(self) -> None:
        """An empty grant skips the UNGRANTED_TOOL check, but a name the
        extractor cannot pin still has to fail -- that is the case that would
        produce an open grant."""
        source = _VALID.replace(
            'return "done"', 'return await ctx.tool("slack__" + action)',
        )
        assert "DYNAMIC_TOOL_NAME" in _codes(source)


class TestModuleLevelStatements:
    """Module-level code runs at import time -- in the real sandbox harness's
    `exec(compiled, ns)` and in the dry-exec check below alike. A stray
    top-level loop or call is both a determinism hazard and, for the
    in-process dry exec, an unbounded-resource hazard with no rlimit to stop
    it (unlike the subprocess sandbox)."""

    def test_top_level_while_loop_is_rejected(self) -> None:
        source = "while True:\n    pass\n" + _VALID
        assert "MODULE_LEVEL_STATEMENT" in _codes(source)

    def test_top_level_call_is_rejected(self) -> None:
        source = "print('hello')\n" + _VALID
        assert "MODULE_LEVEL_STATEMENT" in _codes(source)

    def test_module_docstring_is_allowed(self) -> None:
        source = '"""A module docstring."""\n' + _VALID
        assert "MODULE_LEVEL_STATEMENT" not in _codes(source)

    def test_module_level_constant_is_allowed(self) -> None:
        source = "MAX_RETRIES = 3\n" + _VALID
        assert "MODULE_LEVEL_STATEMENT" not in _codes(source)


class TestDryExecCompile:
    """Unit tests on `_lint_dry_exec` directly: it "compiles" generated
    source against the real SDK by actually exec()ing the module body, which
    generically catches any SDK misuse rather than needing a dedicated AST
    heuristic per symptom. Tested in isolation from the AST checks that run
    before it in `verify_workflow_source` (several of which would otherwise
    intercept the same bad input first, e.g. `_lint_decorator_signatures`
    also rejects `@step(name=...)` -- both catching it is fine, but this
    file is specifically about what `_lint_dry_exec` itself catches)."""

    def test_step_with_invalid_kwarg_caught_by_dry_exec(self) -> None:
        source = (
            "from app.services.workflows.sdk import Ctx, step, workflow\n\n"
            '@step(name="x")\n'
            "async def fetch(ctx: Ctx) -> str:\n"
            '    return "x"\n\n'
            "@workflow\n"
            "async def my_workflow(ctx: Ctx) -> str:\n"
            "    return await fetch(ctx)\n"
        )
        errors = _lint_dry_exec(source)
        assert len(errors) == 1
        assert errors[0].code == "SDK_COMPILE_ERROR"
        assert "TypeError" in errors[0].fix_hint

    def test_undefined_name_caught_by_dry_exec(self) -> None:
        """A module-level reference, not one buried in a function body that
        never runs during dry exec."""
        source = "_x = UndefinedType\n" + _VALID
        errors = _lint_dry_exec(source)
        assert len(errors) == 1
        assert errors[0].code == "SDK_COMPILE_ERROR"
        assert "NameError" in errors[0].fix_hint

    def test_valid_workflow_passes_dry_exec(self) -> None:
        assert _lint_dry_exec(_VALID) == []

    def test_dry_exec_does_not_execute_function_bodies(self) -> None:
        """A body that would blow up if actually run must not trigger during
        dry exec -- `async def` bodies are coroutines until awaited, and
        nothing at module level awaits them."""
        source = _VALID.replace('return "done"', "raise RuntimeError('should not run')")
        assert _lint_dry_exec(source) == []

    def test_full_pipeline_reaches_dry_exec_for_an_sdk_misuse_no_ast_check_covers(self) -> None:
        """Integration check that `verify_workflow_source` actually wires
        `_lint_dry_exec` in: an enum attribute typo is a valid kwarg *name*
        (so `_lint_decorator_signatures` passes it) but a bad *value*, which
        only surfaces once the module is actually executed."""
        source = (
            "from app.services.workflows.sdk import Ctx, SideEffect, step, workflow\n\n"
            "@step(side_effect=SideEffect.WRITEE)\n"
            "async def fetch(ctx: Ctx) -> str:\n"
            '    return "x"\n\n'
            "@workflow\n"
            "async def my_workflow(ctx: Ctx) -> str:\n"
            "    return await fetch(ctx)\n"
        )
        assert "SDK_COMPILE_ERROR" in _codes(source)
