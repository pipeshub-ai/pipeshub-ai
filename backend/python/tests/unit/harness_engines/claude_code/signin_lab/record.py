"""Reduces a SIGNIN-1 lab run to the committed fixtures: what the plan relies on, nothing proprietary.

Stream-json output keeps only the lines the adapter reads after a failure (``rate_limit_event``, ``api_retry``,
the assistant's error and text, the result's error fields); the init line (tool and command lists) is dropped.
The lab's tokens are the fake's own; the credentials file is kept as its shape (keys and value types) only.
"""

from __future__ import annotations

import json
from typing import Any

from tests.unit.harness_engines.claude_code.signin_lab.lab import FIXTURES

RESULT_FIELDS = (
    "subtype",
    "is_error",
    "result",
    "api_error_status",
    "terminal_reason",
    "total_cost_usd",
)


def reduce_stream(stdout: str) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        event = json.loads(line)
        kind, sub = event.get("type"), event.get("subtype")
        if kind == "rate_limit_event":
            kept.append({"type": kind, "rate_limit_info": event["rate_limit_info"]})
        elif kind == "system" and sub == "api_retry":
            kept.append(
                {
                    k: event[k]
                    for k in ("type", "subtype", "attempt", "error_status", "error")
                }
            )
        elif kind == "assistant":
            message = event["message"]
            kept.append(
                {
                    "type": kind,
                    "error": event.get("error"),
                    "model": message.get("model"),
                    "content": [
                        b for b in message.get("content", []) if b.get("type") == "text"
                    ],
                }
            )
        elif kind == "result":
            kept.append({"type": kind, **{k: event.get(k) for k in RESULT_FIELDS}})
    return kept


def reduce(raw: dict[str, Any]) -> dict[str, Any]:
    out = dict(raw)
    for key in ("prompt_ok", "prompt_expired", "prompt_limit", "prompt_signed_out"):
        step = raw[key]
        out[key] = {
            "rc": step["rc"],
            "stderr": step["stderr"],
            "events": reduce_stream(step["stdout"]),
        }
    return out


def write_fixtures(raw: dict[str, Any]) -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / "recorded.json").write_text(
        json.dumps(reduce(raw), indent=1, sort_keys=True) + "\n"
    )
