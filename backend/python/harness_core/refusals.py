"""One base for typed refusals across the control layer (engines, sandbox, gateway).

A refusal is something deliberately not done, for a stable ``reason_code`` that
logs, metrics and callers branch on. Each layer keeps its own family; this base
lets a host handle any of them alike.
"""

from __future__ import annotations


class HarnessRefusal(Exception):
    reason_code: str = "refused"


__all__ = ["HarnessRefusal"]
