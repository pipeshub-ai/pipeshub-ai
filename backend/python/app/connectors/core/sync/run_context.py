"""Per-run identifier for a sync.

A sync runs in the connectors process; the indexing process consumes the
`record-events` it publishes. A ContextVar set around a single `run_sync()` call
lets the record-event producers stamp the run's id onto every message without
threading it through each connector's signature, so downstream processing can
be correlated back to the run that produced it. ContextVars propagate into tasks
created after they are set, so one `set_run_sync_id` around the run covers the
whole downstream call tree.
"""

from contextvars import ContextVar, Token

_run_sync_id: ContextVar[str | None] = ContextVar("run_sync_id", default=None)


def set_run_sync_id(run_sync_id: str) -> Token:
    return _run_sync_id.set(run_sync_id)


def get_run_sync_id() -> str | None:
    return _run_sync_id.get()


def reset_run_sync_id(token: Token) -> None:
    _run_sync_id.reset(token)
