"""The runtime of ``pipeshub_tools``, the generated Python stubs of a run's tools (WS-14, C3.15).

Seeded as ``pipeshub_tools/_runtime.py`` in each turn's ``/pipeshub/session/sdk-<digest>/python/``. Every stub is
one ``ph tools call``: this module loads the worker's own ``ph`` from the session folder (the managed policy hook's
program, ``session/ph.py``) and runs that command in process, ``--json`` envelope and all. The stubs therefore have
no network path, credential or decision of their own: the tool gateway, the run-bound placeholder the egress proxy
swaps, the grant, approvals, classification and the journal are ``ph``'s, and an answer is exactly ``ph``'s
(C3.16), errors included.

Standard library only: it runs on the engine image's own CPython.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import json
import os
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Mapping
    from types import ModuleType

# The image's ``ph`` when the session folder holds none (it always does in a run; kept for a hand-made folder).
IMAGE_PH = "/usr/local/bin/ph"
COMMAND = "tools.call"


@dataclass(frozen=True)
class CallResult:
    """What ``ph --json tools call`` answers on success: the tool's name and the text of its result."""

    tool: str
    text: str

    def json(self) -> Any:  # noqa: ANN401 - the tool's own JSON
        """The result's text read as JSON (most tools answer JSON)."""
        return json.loads(self.text)


class CallError(Exception):
    """A failed call, as ``ph --json tools call`` reports it: ``code`` (its error code), ``exit`` (the exit code
    ``ph`` ends with) and ``message`` (what ``ph`` prints on stderr)."""

    code = "failed"

    def __init__(self, code: str, exit_code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.exit = exit_code
        self.message = message


class ToolFailed(CallError):
    """``tool_error`` (exit 1): the tool ran and failed, or the gateway refused the call (policy, grant)."""


class ApprovalPending(CallError):
    """``approval_pending`` (exit 75): the call waits for a person's approval. End the turn; do not call it again
    until you are told the decision."""


class UsageError(CallError):
    """``usage`` (exit 64): the arguments were not a JSON object."""


class NotFound(CallError):
    """``not_found`` (exit 65): this run has no tool of that name."""


class Unavailable(CallError):
    """``unavailable`` (exit 69): the tool gateway could not be reached or did not answer in time."""


class BadAnswer(CallError):
    """``bad_answer`` (exit 70): the tool gateway's answer could not be read."""


class NotPermitted(CallError):
    """``not_permitted`` (exit 77): the gateway refused the sandbox's credentials (the run has ended)."""


class NotInRun(CallError):
    """``not_in_run`` (exit 78): not inside a PipesHub engine run."""


ERRORS: dict[str, type[CallError]] = {
    "tool_error": ToolFailed,
    "approval_pending": ApprovalPending,
    "usage": UsageError,
    "not_found": NotFound,
    "unavailable": Unavailable,
    "bad_answer": BadAnswer,
    "not_permitted": NotPermitted,
    "not_in_run": NotInRun,
}

_loaded: list[ModuleType] = []


def program() -> str:
    """The ``ph`` the stubs run: the session folder's (``session/ph.py``, beside ``sdk-<digest>/``), else the image's."""
    here = os.path.realpath(__file__)
    session = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(here))))
    seeded = os.path.join(session, "ph.py")
    return seeded if os.path.isfile(seeded) else IMAGE_PH


def _ph() -> ModuleType:
    if not _loaded:
        path = program()
        if not os.path.isfile(path):
            raise NotInRun("not_in_run", 78, f"pipeshub_tools: no ph program at {path}; the stubs work inside a PipesHub engine run.")
        # ``ph`` in the image has no .py suffix, so the loader is named rather than guessed from the path.
        loader = importlib.machinery.SourceFileLoader("pipeshub_tools._ph", path)
        spec = importlib.util.spec_from_loader(loader.name, loader)
        assert spec is not None
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        _loaded.append(module)
    return _loaded[0]


def envelope(tool: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """``ph --json tools call <tool> --json -`` with ``arguments`` on stdin: its exit code and its envelope."""
    out = io.StringIO()
    stdin = io.StringIO(json.dumps(dict(arguments), ensure_ascii=False))
    code = _ph().main(["--json", "tools", "call", tool, "--json", "-"], out=out, err=sys.stderr, stdin=stdin)
    if code == 130:
        raise KeyboardInterrupt
    lines = [line for line in out.getvalue().splitlines() if line.strip()]
    try:
        answer: Any = json.loads(lines[-1]) if lines else None
    except ValueError:
        answer = None
    if not isinstance(answer, dict):
        raise BadAnswer("bad_answer", 70, "pipeshub_tools: ph's answer could not be read.")
    return cast("dict[str, Any]", answer)


def call(tool: str, arguments: Mapping[str, Any] | None = None) -> CallResult:
    """Calls any tool of this run by its full name (``<toolset>__<tool>``), exactly as ``ph tools call`` does.

    Returns the result; raises ``ApprovalPending`` while a person decides, and another ``CallError`` on a failure.
    """
    answer = envelope(tool, arguments or {})
    if answer.get("ok") is True:
        data = cast("dict[str, Any]", answer.get("data") or {})
        return CallResult(str(data.get("tool", tool)), str(data.get("text", "")))
    error = cast("dict[str, Any]", answer.get("error") or {})
    code, exit_code = str(error.get("code", "failed")), int(error.get("exit", 1))
    raise ERRORS.get(code, CallError)(code, exit_code, str(error.get("message", "")))


def invoke(tool: str, given: Mapping[str, Any], optional: frozenset[str]) -> CallResult:
    """A generated stub's call: an optional argument left as ``None`` is not sent."""
    return call(tool, {k: v for k, v in given.items() if not (v is None and k in optional)})


__all__ = [
    "ERRORS",
    "ApprovalPending",
    "BadAnswer",
    "NotFound",
    "NotInRun",
    "NotPermitted",
    "CallError",
    "ToolFailed",
    "CallResult",
    "Unavailable",
    "UsageError",
    "call",
    "envelope",
    "invoke",
    "program",
]
