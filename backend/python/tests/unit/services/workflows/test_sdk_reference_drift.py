"""Drift-detection gate between the LLM-facing SDK reference and the real
SDK it describes.

`_SDK_SYMBOLS` (sdk_reference_tool.py) and `_CTX_STUB` (stub_generator.py)
are hand-maintained text -- nothing enforces that they stay in sync with
`step`/`workflow`'s real signatures or `Ctx`'s real methods. Three confirmed
drifts motivated this file: `workflow()`'s `on_event` param and
`create_agent()`'s `mcps` param were both undocumented, and `ctx.map`'s
documented `concurrency: int = 32` didn't match the real `int | None = None`.
A name-only check would have missed the third one, so these tests compare
names *and* defaults, and check the reverse direction too (no documented
parameter that doesn't exist on the real signature).
"""
from __future__ import annotations

import ast
import inspect
from enum import Enum
from typing import Any

from app.services.workflows.codegen.sdk_reference_tool import _SDK_SYMBOLS
from app.services.workflows.codegen.stub_generator import _CTX_STUB
from app.services.workflows.sdk.context import Ctx
from app.services.workflows.sdk.decorators import step, workflow


def _render_default(value: Any) -> str | None:
    """Render a real parameter default the way it would appear in source,
    e.g. `SideEffect.NONE` (an Enum member) rather than `repr()`'s
    `<SideEffect.NONE: 'none'>`."""
    if value is inspect.Parameter.empty:
        return None
    if isinstance(value, Enum):
        return f"{type(value).__name__}.{value.name}"
    return repr(value)


def _extract_documented_call(header_line: str) -> dict[str, str | None]:
    """Parse a docstring header like `@step(retries=0, timeout_s=None)` into
    `{param_name: default_source_text}`. Strips the leading `@` (not valid
    standalone Python) and requires a `Call` node."""
    call_src = header_line.strip().lstrip("@")
    tree = ast.parse(call_src, mode="eval")
    assert isinstance(tree.body, ast.Call), f"expected a call expression, got: {header_line!r}"
    return {
        kw.arg: ast.unparse(kw.value)
        for kw in tree.body.keywords
        if kw.arg is not None
    }


def _first_header_line(reference_text: str) -> str:
    for line in reference_text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    raise AssertionError(f"reference text has no non-blank lines: {reference_text!r}")


def _assert_signature_matches_reference(fn: Any, reference_text: str) -> None:
    sig = inspect.signature(fn)
    documented = _extract_documented_call(_first_header_line(reference_text))
    real_params = {
        name: param
        for name, param in sig.parameters.items()
        if not name.startswith("_") and param.kind != inspect.Parameter.VAR_POSITIONAL
    }

    missing = set(real_params) - set(documented)
    assert not missing, (
        f"Parameters {missing} exist on the real signature {sig} but are not "
        f"documented in the reference text -- the model will not know about "
        f"them and may invent something else."
    )
    extra = set(documented) - set(real_params)
    assert not extra, (
        f"Reference text documents parameters {extra} that do not exist on "
        f"the real signature {sig} -- the model may emit a kwarg that raises "
        f"a TypeError at runtime."
    )
    for name in real_params:
        expected_default = _render_default(real_params[name].default)
        actual_default = documented[name]
        assert actual_default == expected_default, (
            f"Parameter '{name}' is documented with default {actual_default!r} "
            f"but the real default is {expected_default!r}."
        )


def test_step_reference_documents_all_parameters_and_defaults() -> None:
    _assert_signature_matches_reference(step, _SDK_SYMBOLS["step"])


def test_workflow_reference_documents_all_parameters_and_defaults() -> None:
    _assert_signature_matches_reference(workflow, _SDK_SYMBOLS["workflow"])


def test_create_agent_reference_documents_all_parameters() -> None:
    """The `mcps` param drift lived here -- `create_agent()` gained it, and
    the hand-written reference wasn't updated."""
    sig = inspect.signature(Ctx.create_agent)
    real_params = {
        name for name, p in sig.parameters.items()
        if name not in ("self",) and not name.startswith("_")
    }
    reference_text = _SDK_SYMBOLS["ctx.create_agent"]
    for param in real_params:
        assert param in reference_text, (
            f"ctx.create_agent parameter '{param}' is not documented in "
            "_SDK_SYMBOLS['ctx.create_agent']."
        )


def test_ctx_map_concurrency_default_matches_the_real_signature() -> None:
    """The confirmed default-value drift: documented as `int = 32`, real
    default is `int | None = None` (falls back to the workflow's configured
    concurrency). A names-only check would pass this while still misleading
    the model into writing code that assumes 32 is the default."""
    sig = inspect.signature(Ctx.map)
    assert sig.parameters["concurrency"].default is None
    assert "concurrency: int | None = None" in _SDK_SYMBOLS["ctx.map"]
    assert "concurrency: int = 32" not in _SDK_SYMBOLS["ctx.map"]


def test_ctx_reference_covers_all_async_methods() -> None:
    """Every `async def` method on `Ctx` the LLM might plausibly call
    directly must have a `ctx.<name>` entry in `_SDK_SYMBOLS`, or the model
    has no ground truth for it and has to guess -- the exact gap that let
    `ctx.now().date()` ship without an `await`."""
    async_methods = {
        name
        for name, _ in inspect.getmembers(Ctx, predicate=inspect.iscoroutinefunction)
        if not name.startswith("_")
    }
    documented = {key.split(".", 1)[1] for key in _SDK_SYMBOLS if key.startswith("ctx.")}
    missing = async_methods - documented
    assert not missing, (
        f"Ctx async methods {missing} have no entry in _SDK_SYMBOLS -- "
        "sdk_reference() cannot look them up and the codegen prompt cannot "
        "include their signature."
    )


def test_stub_generator_matches_ctx_signatures() -> None:
    """The `.pyi` stub is a *third* hand-maintained copy of the same
    signatures. It must not describe a method that doesn't exist on the
    real `Ctx`, or an editor/typechecker relying on it would suggest a call
    that fails at runtime."""
    stub_tree = ast.parse(_CTX_STUB)
    stub_ctx_class = next(
        node for node in ast.walk(stub_tree)
        if isinstance(node, ast.ClassDef) and node.name == "Ctx"
    )
    stub_method_names = {
        node.name
        for node in stub_ctx_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    }
    real_method_names = {
        name for name, _ in inspect.getmembers(Ctx, predicate=inspect.iscoroutinefunction)
        if not name.startswith("_")
    } | {
        name for name, value in vars(Ctx).items()
        if not name.startswith("_") and (inspect.isfunction(value) or isinstance(value, property))
    }
    extra = stub_method_names - real_method_names
    assert not extra, (
        f"_CTX_STUB describes method(s) {extra} that do not exist on the "
        "real Ctx class."
    )
