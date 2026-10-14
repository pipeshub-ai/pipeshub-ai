"""Stop raw exception text reaching people through API errors.

Three shapes are caught: the exception handed over on the spot (``detail=str(e)``),
parked in a variable first (``error_msg = f"…{e}"`` … then ``detail=error_msg``),
and passed to a helper that builds the body (``_error_response(500, str(e))``).
The last two read as fixed messages on the raising line.

A line ending ``# user-written message`` is skipped: a few handlers re-raise our
own exceptions whose text was written for the person asking.

The web app shows an API error's ``message``/``reason``/``detail`` word for word,
so ``detail=str(e)`` puts a Python repr in a toast. These files still hold a few
such spots; this test fails when a file gains one, so the count only goes down.

To lower a baseline after cleaning a file up, run this test — the failure message
prints the new count — and set it here.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[3] / "app"

# Raw exception text handed to the caller: detail=str(e), detail=f"…{e}",
# "reason": str(e), "message": str(exc) — in either quote style.
_EXC_NAME = r"(?:e|exc|err|error|\w*_error)"
# The web app reads message → reason → error → detail (frontend/lib/api/api-error.ts),
# as a keyword argument (HTTPException(detail=…)) or a key in a returned dict.
_KEYS = r"(?:detail|reason|message|error)"
_FIELD = r"(?:" + _KEYS + r"|[\"']" + _KEYS + r"[\"'])"
_F_STRING = (
    r"f\"[^\"]*\{\s*(?:str\()?" + _EXC_NAME + r"\)?\s*\}[^\"]*\""
    r"|f'[^']*\{\s*(?:str\()?" + _EXC_NAME + r"\)?\s*\}[^']*'"
)
RAW_EXCEPTION = re.compile(
    _FIELD + r"\s*[=:]\s*(?:str\(" + _EXC_NAME + r"\)|" + _F_STRING + r")"
)

# Keys the web app reads out of an error body, in its order of preference.
_DICT_KEYS = {"message", "reason", "error", "detail"}

# path -> how many such spots that file is still allowed to have.
BASELINE = {
    "connectors/sources/localKB/api/kb_router.py": 0,
    "connectors/sources/localKB/api/knowledge_hub_router.py": 0,
    # Not cleaned up yet: these hand back ValueErrors that are sometimes written
    # for the reader ("Invalid npm package name") and sometimes not. Lower the
    # number as each one is given wording of its own.
    "api/routes/skills.py": 11,
    "api/routes/mcp_servers.py": 3,
    "api/routes/search.py": 1,
    "connectors/sources/localKB/handlers/kb_service.py": 0,
    "connectors/sources/localKB/handlers/knowledge_hub_service.py": 0,
    # The graph providers answer a failed call with {"success": False, "reason":
    # str(e)}. Callers must not pass that on — see _browse_failure in kb_service —
    # but the text starts here, so the counts are held at today's numbers.
    "services/graph_db/arango/arango_http_provider.py": 28,
    "services/graph_db/neo4j/neo4j_provider.py": 19,
    "connectors/api/router.py": 0,
    "api/routes/agent.py": 0,
    "api/routes/toolsets.py": 0,
    "api/routes/entity.py": 0,
    # Health payloads: the 5 hits here are diagnostics on /health, read by
    # monitoring and the health gate rather than shown as a failure to someone
    # mid-task. Changing them is an operational decision, not a wording one.
    "connectors_main.py": 5,
}


def count_raw(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    found = [
        f"{path.name}:{n}: {line.strip()}"
        for n, line in enumerate(lines, 1)
        if RAW_EXCEPTION.search(line) and "user-written message" not in line
    ]
    return found + count_aliased(path.name, source, lines)


def _holds_exception_text(node: ast.AST) -> bool:
    """``str(e)`` or an f-string interpolating an exception."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "str":
        return bool(node.args) and isinstance(node.args[0], ast.Name) and _is_exc(node.args[0].id)
    if isinstance(node, ast.JoinedStr):
        for part in node.values:
            if not isinstance(part, ast.FormattedValue):
                continue
            inner = part.value
            if isinstance(inner, ast.Name) and _is_exc(inner.id):
                return True
            if _holds_exception_text(inner):
                return True
    return False


def _is_exc(name: str) -> bool:
    return bool(re.fullmatch(_EXC_NAME, name))


# Calls that build an answer for the caller: _error_response(500, str(e)) hands the
# exception on just as surely as detail=str(e). Logging calls are the opposite — the
# exception belongs there — so they are skipped.
_ANSWER_BUILDER = re.compile(r"(?i).*(error|fail|response|reason|detail|message).*")
_LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical"}


def _called_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _is_logging_call(node: ast.Call) -> bool:
    return isinstance(node.func, ast.Attribute) and node.func.attr in _LOG_METHODS


def count_aliased(filename: str, source: str, lines: list[str]) -> list[str]:
    """Exception text that reaches the caller indirectly.

    Two shapes the line scan cannot see: ``error_msg = f"…{e}"`` then
    ``detail=error_msg``, and ``_error_response(500, str(e))``, where the text is
    handed to a helper that builds the body.
    """
    hits: list[str] = []
    for func in ast.walk(ast.parse(source)):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        carriers: dict[str, list[int]] = {}
        for node in ast.walk(func):
            if not isinstance(node, ast.Assign) or not _holds_exception_text(node.value):
                continue
            if "user-written message" in lines[node.lineno - 1]:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    carriers.setdefault(target.id, []).append(node.lineno)
        # a helper can be handed the exception directly, with no carrier in sight
        hits += _handed_to_a_builder(filename, func, carriers, lines)
        if not carriers:
            continue
        for node in ast.walk(func):
            used: list[ast.expr] = []
            if isinstance(node, ast.keyword) and node.arg == "detail":
                used.append(node.value)
            elif isinstance(node, ast.Dict):
                used += [
                    value
                    for key, value in zip(node.keys, node.values)
                    if isinstance(key, ast.Constant) and key.value in _DICT_KEYS
                ]
            for named in used:
                if not isinstance(named, ast.Name) or named.id not in carriers:
                    continue
                # the assignment that reaches this use is the closest one above it
                above = [n for n in carriers[named.id] if n <= named.lineno]
                assigned = above[-1] if above else carriers[named.id][0]
                hits.append(
                    f"{filename}:{named.lineno}: {named.id} carries the exception "
                    f"from line {assigned}: {lines[assigned - 1].strip()}"
                )
    return sorted(set(hits))


def _handed_to_a_builder(
    filename: str, func: ast.AST, carriers: dict[str, list[int]], lines: list[str]
) -> list[str]:
    """Exception text passed as an argument to something that builds the answer.

    ``raise SomeError(f"…{e}")`` is skipped: wrapping an exception with context is
    ordinary chaining, and whether that text reaches anyone is decided by the
    handler that turns it into a response — which this check already covers.
    """
    raised = {node.exc for node in ast.walk(func) if isinstance(node, ast.Raise) and node.exc}
    hits: list[str] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Call) or _is_logging_call(node) or node in raised:
            continue
        name = _called_name(node)
        if not name or not _ANSWER_BUILDER.fullmatch(name):
            continue
        for arg in list(node.args) + [kw.value for kw in node.keywords]:
            if _holds_exception_text(arg) and "user-written message" not in lines[arg.lineno - 1]:
                hits.append(
                    f"{filename}:{arg.lineno}: {name}() is handed the exception: "
                    f"{lines[arg.lineno - 1].strip()}"
                )
            elif isinstance(arg, ast.Name) and arg.id in carriers:
                above = [n for n in carriers[arg.id] if n <= arg.lineno]
                assigned = above[-1] if above else carriers[arg.id][0]
                hits.append(
                    f"{filename}:{arg.lineno}: {name}() is handed {arg.id}, which carries "
                    f"the exception from line {assigned}: {lines[assigned - 1].strip()}"
                )
    return hits


@pytest.mark.parametrize(("relative", "allowed"), sorted(BASELINE.items()))
def test_file_hands_no_new_exception_text_to_people(relative: str, allowed: int) -> None:
    found = count_raw(APP / relative)
    assert len(found) <= allowed, (
        f"{relative} now returns raw exception text to people in {len(found)} place(s), "
        f"baseline {allowed}. Use app.utils.user_messages.action_failed()/not_found() "
        f"and log the exception instead:\n  " + "\n  ".join(found)
    )


def test_exception_text_parked_in_a_variable_is_caught() -> None:
    source = (
        "async def handler():\n"
        "    try:\n"
        "        await work()\n"
        "    except Exception as e:\n"
        '        error_msg = f"Connection test failed: {str(e)}"\n'
        "        raise HTTPException(status_code=500, detail=error_msg) from e\n"
    )
    found = count_aliased("handler.py", source, source.splitlines())
    assert len(found) == 1, found
    assert "carries the exception" in found[0]


def test_exception_text_returned_under_the_detail_key_is_caught() -> None:
    """The web app reads `detail` out of a returned body, not just HTTPException."""
    source = (
        "async def handler():\n"
        "    try:\n"
        "        await work()\n"
        "    except Exception as e:\n"
        "        error_msg = str(e)\n"
        '        return {"detail": error_msg}\n'
    )
    found = count_aliased("handler.py", source, source.splitlines())
    assert len(found) == 1, found
    assert "carries the exception" in found[0]


def test_exception_text_returned_under_the_error_key_is_caught() -> None:
    """`error` is third in the web app's order of preference."""
    source = (
        "async def handler():\n"
        "    try:\n"
        "        await work()\n"
        "    except Exception as e:\n"
        '        failure = f"Listing failed: {e}"\n'
        '        return {"records": [], "error": failure}\n'
    )
    assert len(count_aliased("handler.py", source, source.splitlines())) == 1


def test_exception_text_handed_to_an_error_helper_is_caught() -> None:
    """`_error_response(500, str(e))` hands it on as surely as `detail=str(e)`."""
    source = (
        "async def handler(self):\n"
        "    try:\n"
        "        await work()\n"
        "    except Exception as e:\n"
        "        return self._error_response(500, str(e))\n"
    )
    found = count_aliased("handler.py", source, source.splitlines())
    assert len(found) == 1, found
    assert "is handed the exception" in found[0]


def test_a_carrier_handed_to_an_error_helper_is_caught() -> None:
    source = (
        "async def handler(self):\n"
        "    try:\n"
        "        await work()\n"
        "    except Exception as e:\n"
        '        reason = f"Listing failed: {e}"\n'
        "        return self._error_response(500, reason)\n"
    )
    assert len(count_aliased("handler.py", source, source.splitlines())) == 1


def test_logging_the_exception_is_not_a_leak() -> None:
    """The exception belongs in the log; only what goes back to the caller counts."""
    source = (
        "async def handler(self):\n"
        "    try:\n"
        "        await work()\n"
        "    except Exception as e:\n"
        '        self.logger.error(f"Listing failed: {e}", exc_info=True)\n'
        '        return self._error_response(500, action_failed("load these files"))\n'
    )
    assert count_aliased("handler.py", source, source.splitlines()) == []


def test_a_fixed_message_in_a_variable_is_left_alone() -> None:
    source = (
        "async def handler():\n"
        "    try:\n"
        "        await work()\n"
        "    except Exception as e:\n"
        '        error_msg = action_failed("connect to this connector")\n'
        "        raise HTTPException(status_code=500, detail=error_msg) from e\n"
    )
    assert count_aliased("handler.py", source, source.splitlines()) == []


def test_the_pattern_catches_the_shapes_it_claims_to() -> None:
    caught = [
        'detail=str(e)',
        'detail=f"Unexpected error: {str(e)}"',
        '"reason": str(e)',
        '"message": str(exc),',
        'detail=f"Failed to update user roles: {str(e)}"',
        'detail=f"Failed to publish reindex event: {str(event_error)}"',
        "detail=f'Failure: {e}'",
        "'reason': str(exc),",
        '"detail": str(e),',
        '"error": str(e),',
        "'error': f'Listing failed: {e}',"
    ]
    for line in caught:
        assert RAW_EXCEPTION.search(line), line
    ignored = [
        'detail=action_failed("open this file")',
        'detail=not_found("This folder")',
        # names that merely start with "e" are not exception text
        'detail=f"Cannot change auth type from {existing_auth_type} to {new_auth_type}."',
    ]
    for line in ignored:
        assert not RAW_EXCEPTION.search(line), line
