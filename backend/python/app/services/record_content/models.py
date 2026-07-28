"""Domain models for the record-content resolution kernel.

Kept dependency-free of storage/graph shapes on purpose — callers (agent
tools, the internal endpoint) code against these types; the resolver and
strategies translate to/from graph and blob shapes at the edges.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


__all__ = [
    "ResolvedRecordContent",
    "AttachmentFailure",
    "RecordContentError",
    "RecordNotFoundError",
    "RecordAccessDeniedError",
    "RecordTooLargeError",
    "RecordContentUnavailableError",
]


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


class RecordContentError(Exception):
    """Base for all record-content resolution failures."""


class RecordNotFoundError(RecordContentError):
    """The supplied ref does not map to any accessible record."""


class RecordAccessDeniedError(RecordContentError):
    """The actor does not have read permission for the record.

    Deliberately indistinguishable from "not found" in tool error strings —
    never expose org-membership signals to model-supplied IDs.
    """


class RecordTooLargeError(RecordContentError):
    """Record content exceeds the caller's byte budget."""

    def __init__(self, record_id: str, size_bytes: int, max_bytes: int) -> None:
        super().__init__(
            f"Record {record_id} is {size_bytes:,} bytes, exceeds limit of {max_bytes:,} bytes"
        )
        self.record_id = record_id
        self.size_bytes = size_bytes
        self.max_bytes = max_bytes


class RecordContentUnavailableError(RecordContentError):
    """Bytes cannot be fetched right now (connector deleted, network error, etc.)."""


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedRecordContent:
    """Bytes for one record, fully resolved and authorized.

    `source` tells the caller which path was used — useful for diagnostics
    and for deciding upload mechanics (e.g. Salesforce can skip multipart
    for blobs under 25 MiB, Outlook needs the chunked-PUT path for > 3 MiB).
    """

    record_id: str
    filename: str
    mime_type: str
    size_bytes: int
    content: bytes
    version: int | None
    source: Literal["blob", "connector"]


@dataclass
class AttachmentFailure:
    """Per-record failure description returned in AttachmentBundle.failures."""

    ref: str
    error: str
    error_type: str  # class name of the exception

    def to_dict(self) -> dict:
        return {"ref": self.ref, "error": self.error, "error_type": self.error_type}
