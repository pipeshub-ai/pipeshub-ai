"""Task-local ownership for connector sync work.

Connector discovery publishes asynchronous record events.  The run identifier
must travel with those events so a cancelled run cannot update a replacement
run's progress counters.
"""

from contextvars import ContextVar, Token


_sync_run_id: ContextVar[str | None] = ContextVar("connector_sync_run_id", default=None)


def get_sync_run_id() -> str | None:
    return _sync_run_id.get()


def set_sync_run_id(run_id: str | None) -> Token[str | None]:
    return _sync_run_id.set(run_id)


def reset_sync_run_id(token: Token[str | None]) -> None:
    _sync_run_id.reset(token)
