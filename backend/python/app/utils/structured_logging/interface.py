"""Logging provider port.

``create_logger`` and the log-record factory ask a provider for four things:
the console formatter, whether to keep per-service log files, how to show the
org as ``organization_id_hash``, and how to show the org and user as the
``org_id``/``user_id`` fields. The default provider keeps the platform's text
logging and shows the raw ids. A build can supply its own provider through
``app.edition.structured_logging_provider`` without touching the logger.
"""

import logging
from abc import ABC, abstractmethod
from typing import TypedDict


class LogIdentity(TypedDict, total=False):
    org_id: str
    user_id: str


class StructuredLoggingProvider(ABC):
    @abstractmethod
    def console_formatter(self, default: logging.Formatter) -> logging.Formatter:
        """Formatter for the console handler every logger writes through
        (stdout), and for uvicorn's handlers under ``LOG_FORMAT=json``.
        ``default`` is the formatter the logger would use on its own: text, or
        JSON under ``LOG_FORMAT=json``. A formatter that does not redact is
        wrapped so that it does. May raise on a misconfigured deployment;
        callers must not swallow that."""

    @abstractmethod
    def file_logging_enabled(self) -> bool:
        """Whether ``create_logger`` also writes ``logs/<service>.log``."""

    @abstractmethod
    def hash_org_id(self, org_id: str) -> str | None:
        """Non-reversible stand-in for ``org_id``, set on the record as
        ``organization_id_hash`` (so it is a field of the line); ``None``
        leaves the org off the record. Never return ``org_id`` itself."""

    def log_identity(self, ids: LogIdentity) -> LogIdentity:
        """How the org and user of the work in flight appear as the ``org_id`` and
        ``user_id`` fields: as they are, hashed, or with a key left out to hide it.
        ``ids`` holds only the ids that are known. Must not log. The default shows
        neither, so a provider written before this hook shows no raw ids; one that
        returns a hashed ``org_id`` here may return ``None`` from ``hash_org_id``
        so the org is not on the line twice."""
        return {}


class DefaultLoggingProvider(StructuredLoggingProvider):
    """Text to stdout plus a per-service file; raw org and user ids as fields, no hash."""

    def console_formatter(self, default: logging.Formatter) -> logging.Formatter:
        return default

    def file_logging_enabled(self) -> bool:
        return True

    def hash_org_id(self, org_id: str) -> str | None:
        return None

    def log_identity(self, ids: LogIdentity) -> LogIdentity:
        return ids
