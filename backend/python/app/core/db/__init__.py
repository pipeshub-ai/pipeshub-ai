"""Database runtime (asyncpg) behind a transaction pooler. See README.md."""

from app.core.db.config import DbConfig
from app.core.db.errors import (
    CapabilityError,
    CommitOutcomeUnknown,
    ConnectionLost,
    DbConfigError,
    DbError,
    DbTimeout,
    DeadlockDetected,
    FailedTransaction,
    LockTimeout,
    PoolTimeout,
    ProfileEpochMismatch,
    PrologueFailed,
    RetryableConflict,
    SerializationConflict,
    SessionRoleLeak,
    UnsupportedIsolation,
    UowUsageError,
)
from app.core.db.uow import KernelDB, OrgLimits, Tx, safe_request_id

__all__ = [
    "CapabilityError", "CommitOutcomeUnknown", "ConnectionLost", "DbConfig", "DbConfigError", "DbError", "DbTimeout",
    "DeadlockDetected", "FailedTransaction", "UowUsageError", "KernelDB", "LockTimeout", "OrgLimits", "PoolTimeout", "PrologueFailed", "ProfileEpochMismatch",
    "RetryableConflict", "SerializationConflict", "SessionRoleLeak", "Tx", "UnsupportedIsolation", "safe_request_id",
]
