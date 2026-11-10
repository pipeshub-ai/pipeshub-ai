"""Typed errors of the pgvector provider.

Messages keep the tokens ``CollectionRegistry`` matches on ("already exists",
"does not exist"), because it classifies provider errors by string.
"""

from __future__ import annotations

import asyncpg

from app.exceptions.non_retryable import NonRetryableError
from app.services.vector_db.errors import (
    OrgMismatch,
    OrgRequired,
    SpaceMismatch,
    VectorOrgError,
)
from app.services.vector_db.models import SearchFailureKind


class PgVectorError(Exception):
    """Base of every error this provider raises on purpose."""


class PgVectorUnsupportedError(PgVectorError, NonRetryableError):
    """The server cannot host the provider: no extension, or a version below the floor."""


class CollectionAlreadyExistsError(PgVectorError):
    def __init__(self, name: str) -> None:
        super().__init__(f"Collection '{name}' already exists")


class CollectionNotFoundError(PgVectorError):
    def __init__(self, name: str) -> None:
        super().__init__(f"Collection '{name}' does not exist")


class LexicalSearchTimeout(PgVectorError):
    """The lexical leg did not answer within its budget (waiting for a connection and running the statement together). The caller
    may continue without it; nothing was returned."""


def classify_pg_search_error(error: BaseException) -> SearchFailureKind | None:
    """Only a timeout or a connection that went away is worth a second attempt."""
    if isinstance(error, asyncpg.QueryCanceledError | TimeoutError | LexicalSearchTimeout):
        return SearchFailureKind.TIMEOUT
    if isinstance(
        error,
        asyncpg.PostgresConnectionError
        | asyncpg.CannotConnectNowError
        | asyncpg.InterfaceError
        | ConnectionError,
    ):
        return SearchFailureKind.UNAVAILABLE
    return None


__all__ = ["OrgMismatch", "OrgRequired", "SpaceMismatch", "VectorOrgError"]
