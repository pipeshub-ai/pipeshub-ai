"""Salesforce's own answer to a request that failed.

The stream error helpers turn a failed request into an HTTPException that tells a reader
what to do, and drop what Salesforce said. A sync needs it: a 403 is the org's API limit
when its error code is REQUEST_LIMIT_EXCEEDED, and a missing permission otherwise. So the
connector raises that HTTPException from a ``SalesforceRequestFailed`` that keeps
Salesforce's status and error code, and ``salesforce_error`` finds it again.
"""

from __future__ import annotations

import json
from typing import Any, Optional

_MAX_DETAIL = 300


class SalesforceRequestFailed(RuntimeError):
    def __init__(self, status: Optional[int], body: Any) -> None:
        self.status = status
        self.error_code, self.detail = _first_error(body)
        parts = (f"HTTP {status}" if status else None, self.error_code, self.detail)
        super().__init__(" ".join(part for part in parts if part) or "no answer")


def _first_error(body: Any) -> tuple[Optional[str], str]:
    """REST errors are a list of {"errorCode", "message"}; the OAuth endpoint answers one
    object with "error" and "error_description". Anything else is kept as text."""
    if isinstance(body, bytes):
        body = body.decode(errors="replace")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except ValueError:
            return None, body[:_MAX_DETAIL]
    if isinstance(body, list) and body and isinstance(body[0], dict):
        body = body[0]
    if isinstance(body, dict):
        code = body.get("errorCode") or body.get("error")
        message = body.get("message") or body.get("error_description") or ""
        return (str(code) if code else None), str(message)[:_MAX_DETAIL]
    return None, str(body or "")[:_MAX_DETAIL]


def salesforce_error(error: Optional[BaseException]) -> Optional[SalesforceRequestFailed]:
    """The Salesforce answer behind ``error``, if any, through its causes."""
    seen: set[int] = set()
    while error is not None and id(error) not in seen:
        if isinstance(error, SalesforceRequestFailed):
            return error
        seen.add(id(error))
        error = error.__cause__ or error.__context__
    return None
