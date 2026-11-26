"""The gateways' request logs as the auditor reads them (P9): one executed call per line.

Two encodings: HarnessLog's text (``... <event> orgId=o runId=r ... toolId=t``, values never contain spaces) and
JSON lines carrying the record's ``harness_event`` and ``harness`` fields. Lines: the tool gateway's
``gateway.call`` (dispositions that ran or replayed a call) and the model gateway's ``model_gateway.call``.
"""

from __future__ import annotations

import json
from typing import cast

from harness_core.audit.types import ExecutedCall

TOOL_CALL = "gateway.call"
MODEL_CALL = "model_gateway.call"
# A refused, denied or held call never reached the tool; ``recorded`` replays an outcome the tool produced.
EXECUTED_DISPOSITIONS = frozenset({"executed", "recorded"})
_EVENTS = (TOOL_CALL, MODEL_CALL)


class MalformedLine(ValueError):
    pass


def _text_fields(line: str) -> tuple[str, dict[str, str]] | None:
    tokens = line.split()
    at = next((i for i, token in enumerate(tokens) if token in _EVENTS), None)
    if at is None:
        return None
    pairs = (token.partition("=") for token in tokens[at + 1 :])
    return tokens[at], {key: value for key, sep, value in pairs if sep}


def _json_fields(line: str) -> tuple[str, dict[str, str]] | None:
    try:
        record = json.loads(line)
    except ValueError as exc:
        raise MalformedLine("not JSON") from exc
    if not isinstance(record, dict):
        raise MalformedLine("not a JSON object")
    event = cast("dict[str, object]", record).get("harness_event")
    if event not in _EVENTS:
        return None
    raw = cast("dict[str, object]", record).get("harness")
    if not isinstance(raw, dict):
        raise MalformedLine("no harness fields")
    return str(event), {str(k): str(v) for k, v in cast("dict[str, object]", raw).items() if v is not None}


def parse_call(line: str) -> ExecutedCall | None:
    """The executed call a log line records; ``None`` for any other line; ``MalformedLine`` for a call line
    that lacks what the auditor needs (counted by the caller, never fatal)."""
    stripped = line.strip()
    if not stripped:
        return None
    found = _json_fields(stripped) if stripped.startswith("{") else _text_fields(stripped)
    if found is None:
        return None
    event, fields = found
    org, run = fields.get("orgId"), fields.get("runId")
    if not org or not run:
        raise MalformedLine(f"{event} without orgId/runId")
    if event == TOOL_CALL:
        if fields.get("disposition") not in EXECUTED_DISPOSITIONS:
            return None
        tool = fields.get("toolId")
        if not tool:
            raise MalformedLine("gateway.call without toolId")
        return ExecutedCall("tool_gateway", org, run, tool, fields.get("attemptId"), fields.get("invocationId"))
    route = fields.get("routeId")
    if not route:
        raise MalformedLine("model_gateway.call without routeId")
    return ExecutedCall("model_gateway", org, run, route, fields.get("attemptId"))


__all__ = ["EXECUTED_DISPOSITIONS", "MODEL_CALL", "TOOL_CALL", "MalformedLine", "parse_call"]
