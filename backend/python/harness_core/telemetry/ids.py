"""A run's trace context, derived from its id: every process that knows the run joins one trace.

No header or run-document field carries it (Node admits, the worker claims later, the gateways see a token):
the trace id is ``sha256("pipeshub.run/" + runId)[:32]`` and the root (admission) span id
``sha256("pipeshub.run/" + runId + "/admit")[:16]``. Node derives the same
(``contracts/harness/vectors/trace-ids.json`` pins both).
"""

from __future__ import annotations

import hashlib

_PREFIX = "pipeshub.run/"


def _hex(text: str, size: int) -> str:
    digest = hashlib.sha256(text.encode()).hexdigest()[:size]
    # An all-zero id is invalid in OpenTelemetry; a sha256 prefix of zeros is astronomically rare, but defined.
    return digest if int(digest, 16) else "1".rjust(size, "0")


def run_trace_id(run_id: str) -> str:
    return _hex(_PREFIX + run_id, 32)


def run_root_span_id(run_id: str) -> str:
    return _hex(_PREFIX + run_id + "/admit", 16)


__all__ = ["run_root_span_id", "run_trace_id"]
