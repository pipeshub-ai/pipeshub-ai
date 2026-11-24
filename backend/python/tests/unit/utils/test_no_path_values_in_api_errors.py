"""Stop a route's 4xx detail from quoting its own path value.

Node shows a Python ``detail`` to the client and logs it on ``http.request.error``. A path value that
matched nothing can be anything the client typed, an email included, so ``HTTPException(detail=f"…{agent_key}…")``
in the route that takes ``{agent_key}`` puts it there. The spots left quote a value only after it matched a
stored record (an id); this test fails when a file gains one, so the count only goes down.

What it does NOT see (the per-site tests in tests/unit/api/test_unmatched_path_values_in_details.py
cover those): a helper given the value as an argument, an exception class that builds its own detail,
a graph provider's ``reason``, a detail parked in a variable first, and ``.format`` / ``%`` strings.

To lower a baseline after cleaning a file up, run this test: the failure message prints the new count.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[3] / "app"

_PATH_PARAM = re.compile(r"\{(\w+)(?::\w+)?\}")

# path -> how many such spots that file may still have; every other file has none.
BASELINE = {
    # :3358, :3369 (reindex, after the instance was found), :8096, :8120 (filter options, after the field and
    # instance matched), :9423, :9429 (connection check, after the type matched the registry).
    "connectors/api/router.py": 6,
    # :3470 (agent toolset auth, after the agent and instance matched).
    "api/routes/toolsets.py": 1,
}


def _path_params(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    return {
        param
        for d in fn.decorator_list
        if isinstance(d, ast.Call) and d.args and isinstance(d.args[0], ast.Constant) and isinstance(d.args[0].value, str)
        for param in _PATH_PARAM.findall(d.args[0].value)
    }


def _is_http_exception(call: ast.Call) -> bool:
    func = call.func
    name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
    return name == "HTTPException"


def _interpolated(node: ast.AST) -> set[str]:
    return {
        name.id
        for joined in ast.walk(node) if isinstance(joined, ast.JoinedStr)
        for part in joined.values if isinstance(part, ast.FormattedValue)
        for name in ast.walk(part.value) if isinstance(name, ast.Name)
    }


def quoted_path_values(source: str) -> list[str]:
    found = []
    for fn in ast.walk(ast.parse(source)):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or not (params := _path_params(fn)):
            continue
        for call in ast.walk(fn):
            if not (isinstance(call, ast.Call) and _is_http_exception(call)):
                continue
            details = [k.value for k in call.keywords if k.arg == "detail"] or call.args[1:2]
            if any(_interpolated(d) & params for d in details):
                found.append(f"{fn.name}:{call.lineno}")
    return found


def test_route_details_do_not_quote_new_path_values() -> None:
    over = {}
    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(APP).as_posix()
        found = quoted_path_values(path.read_text(encoding="utf-8-sig"))
        if len(found) > BASELINE.get(rel, 0):
            over[rel] = found
    assert not over, (
        "A route's HTTPException detail quotes its own path value. Leave the value out (the client knows what it "
        f"sent), or, if it matched a stored record first, raise the baseline with the reason: {over}"
    )


def test_baselines_are_not_stale() -> None:
    for rel, allowed in BASELINE.items():
        found = quoted_path_values((APP / rel).read_text(encoding="utf-8-sig"))
        assert len(found) == allowed, f"{rel}: baseline {allowed}, now {len(found)} ({found}); set it to {len(found)}"


def test_the_scan_sees_a_route_quoting_its_path_value() -> None:
    source = (
        '@router.get("/x/{item_id}")\n'
        "async def get_x(item_id: str):\n"
        '    raise HTTPException(status_code=404, detail=f"Item {item_id} not found")\n'
        '    raise HTTPException(404, f"Item {item_id}")\n'
        '@router.get("/y/{rest:path}")\n'
        "async def get_y(rest: str):\n"
        '    raise HTTPException(404, detail=f"No {rest}")\n'
    )
    assert quoted_path_values(source) == ["get_x:3", "get_x:4", "get_y:7"]


def test_the_scan_ignores_a_detail_without_the_path_value() -> None:
    source = (
        '@router.get("/x/{item_id}")\n'
        "async def get_x(item_id: str, kind: str):\n"
        '    raise HTTPException(status_code=404, detail=f"{kind} not found")\n'
        '    raise HTTPException(404, "Item not found")\n'
    )
    assert quoted_path_values(source) == []
