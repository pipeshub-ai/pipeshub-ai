"""Typed database errors. Anything else asyncpg raises (constraint violations, syntax errors) passes through unchanged."""

from __future__ import annotations


class DbError(Exception):
    """Base for errors the runtime classifies."""

    retry_after_s: float | None = None  # API layers answer 503 + Retry-After when set


class DbConfigError(DbError):
    """Missing or invalid database configuration (including a jobs unit of work without a jobs DSN)."""


class RetryableConflict(DbError):
    """SQLSTATE 40001 / 40P01. ``KernelDB.run`` re-runs the whole callable; a bare ``uow()`` cannot."""


class SerializationConflict(RetryableConflict):
    pass


class DeadlockDetected(RetryableConflict):
    pass


class DbTimeout(DbError):
    """A statement (57014) or lock wait (55P03) timed out. Not retried."""

    retry_after_s = 1.0


class LockTimeout(DbTimeout):
    pass


class PoolTimeout(DbTimeout):
    """No pooled connection became free within the class timeout."""


class ConnectionLost(DbError):
    """The server connection went away. ``retry_safe`` is true only when re-running cannot repeat an effect."""

    def __init__(self, message: str, *, retry_safe: bool) -> None:
        super().__init__(message)
        self.retry_safe = retry_safe


class CommitOutcomeUnknown(DbError):
    """The connection was lost after COMMIT was sent. The transaction may or may not have committed."""


class SessionRoleLeak(DbError):
    """A session-level SET ROLE was found on a pooled connection; the connection was discarded."""


class ProfileEpochMismatch(DbError):
    """This process's deployment-profile epoch differs from the database's (a profile switch is under way)."""


class PrologueFailed(DbError):
    """The unit-of-work prologue failed; no caller statement ran."""


class CapabilityError(DbError):
    """The database lacks something the profile requires (strict mode)."""


class UnsupportedIsolation(DbError):
    """A write unit of work asked for an isolation level other than read_committed (the fence requires it)."""


class FailedTransaction(DbError):
    """A statement failed inside the unit and the error was swallowed: COMMIT would silently roll back, so the unit fails."""


class UowUsageError(DbError):
    """Misuse of the API: a unit started inside another unit, or a statement that changes the unit's identity."""
