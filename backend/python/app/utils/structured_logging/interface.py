"""Logging provider port.

``create_logger`` and the log-record factory ask a provider for three things:
the console formatter, whether to keep per-service log files, and how to show
the request's org on a record. The default provider keeps the platform's text
logging exactly as it is. A build can supply its own provider through
``app.edition.structured_logging_provider`` without touching the logger.
"""

import logging
from abc import ABC, abstractmethod


class StructuredLoggingProvider(ABC):
    @abstractmethod
    def console_formatter(self, default: logging.Formatter) -> logging.Formatter:
        """Formatter for the stdout handler. ``default`` is the text formatter
        the logger would use on its own. May raise on a misconfigured
        deployment; callers must not swallow that."""

    @abstractmethod
    def file_logging_enabled(self) -> bool:
        """Whether ``create_logger`` also writes ``logs/<service>.log``."""

    @abstractmethod
    def hash_org_id(self, org_id: str) -> str | None:
        """Non-reversible stand-in for ``org_id``, set on the record as
        ``organization_id_hash``; ``None`` leaves the org off the record.
        Never return ``org_id`` itself."""


class DefaultLoggingProvider(StructuredLoggingProvider):
    """Text to stdout plus a per-service file; the org stays off the record."""

    def console_formatter(self, default: logging.Formatter) -> logging.Formatter:
        return default

    def file_logging_enabled(self) -> bool:
        return True

    def hash_org_id(self, org_id: str) -> str | None:
        return None
