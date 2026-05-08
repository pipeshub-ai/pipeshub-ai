"""Regression tests for the _make_async_tool_func wrapper in tool_system.

The wrapper is what LangChain invokes for every tool call. If it does the
wrong thing with the tool's return value, the LLM sees garbled ToolMessage
content (e.g. Python ``repr`` of a dict, a stringified tuple, or a stray
``False``). These tests lock in the intended contract:

1. ``(success: bool, str)`` tuples are unwrapped to the string.
2. Already-string results pass through untouched.
3. Dict / list results are JSON-encoded (never Python repr).
4. Lists are NOT unwrapped as tuples, even if they happen to have length 2.
5. Non-JSON-serializable dicts fall back to the default=str JSON encoding.
6. Primitives (int, bool) are stringified.

We keep this test file free of the heavy tool_system import chain (which
pulls in etcd3, googleapiclient, etc. that may not be installed in CI).
Instead we load the wrapper source from disk and exec its body into a
standalone function — this guarantees we test the real production text
without dragging in its transitive dependencies.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest


def _extract_wrapper_function():
    """Parse tool_system.py and return an async closure matching _make_async_tool_func's inner func."""
    src = Path(__file__).resolve().parents[5] / "app" / "modules" / "agents" / "qna" / "tool_system.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))

    target_body = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_async_tool_func":
            target_body = node
            break
    if target_body is None:
        raise RuntimeError("Could not locate _async_tool_func in tool_system.py")

    # Rebuild as a standalone async function that takes `result` instead of
    # awaiting wrapper.arun(). This lets us exercise the normalisation rules
    # in isolation while still using the real production source.
    standalone_src = f"""
import json
async def normalise(result):
{ast.unparse(ast.Module(body=target_body.body[1:], type_ignores=[])).splitlines()[0] and ""}
"""
    # Easier: just copy the body literally after the `result = await ...` line.
    body_src = ast.unparse(target_body)
    # Replace the await line with a simple identity binding.
    body_src = body_src.replace(
        "result = await wrapper.arun(kwargs)",
        "result = result",
    )
    # Rename the function so we can exec + grab it.
    body_src = body_src.replace("async def _async_tool_func", "async def _normalise", 1)
    # Strip the **kwargs signature to accept `result` directly.
    body_src = body_src.replace("async def _normalise(**kwargs: object) -> str:", "async def _normalise(result):", 1)

    ns: dict = {"json": json}
    exec(body_src, ns)
    return ns["_normalise"]


@pytest.fixture(scope="module")
def normalise():
    return _extract_wrapper_function()


@pytest.mark.asyncio
async def test_tuple_result_unwrapped_to_string(normalise):
    assert await normalise((True, '{"ok": true}')) == '{"ok": true}'


@pytest.mark.asyncio
async def test_string_result_passes_through(normalise):
    assert await normalise("already a string") == "already a string"


@pytest.mark.asyncio
async def test_dict_result_is_json_encoded(normalise):
    out = await normalise({"alpha": 1, "beta": "two"})
    assert json.loads(out) == {"alpha": 1, "beta": "two"}


@pytest.mark.asyncio
async def test_list_is_not_unwrapped_as_tuple(normalise):
    """A 2-element list MUST survive intact (was corrupted by old str(data) code)."""
    out = await normalise(["first", "second"])
    assert json.loads(out) == ["first", "second"]


@pytest.mark.asyncio
async def test_nonserializable_dict_is_stringified_via_default_str(normalise):
    class _X:
        def __repr__(self) -> str:
            return "X()"

    out = await normalise({"obj": _X()})
    parsed = json.loads(out)
    assert "obj" in parsed
    # default=str coerces non-JSON objects to their str() form
    assert isinstance(parsed["obj"], str)


@pytest.mark.asyncio
async def test_primitive_bool_is_stringified(normalise):
    assert await normalise(True) == "True"


@pytest.mark.asyncio
async def test_tuple_with_dict_payload_is_json_encoded(normalise):
    """(success, dict) should unwrap AND json-encode the dict payload."""
    out = await normalise((True, {"count": 3}))
    assert json.loads(out) == {"count": 3}
