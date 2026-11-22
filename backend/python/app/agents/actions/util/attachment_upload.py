"""Protocol for per-app attachment upload adapters.

Each toolset (Slack, Outlook, Salesforce, Gmail) implements
`IAttachmentUploader` so the tool logic only handles "give me
`ResolvedRecordContent` objects, I will upload them via the native API"
without knowing platform specifics.

`AttachmentTarget` carries the destination identifier (channel, message ID,
opportunity ID, etc.) — its shape is platform-specific but always passed
through the protocol as a plain dataclass to avoid coupling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence, runtime_checkable

from app.services.record_content.models import ResolvedRecordContent

__all__ = [
    "AttachmentTarget",
    "AttachmentUploadResult",
    "IAttachmentUploader",
    "SupportsPreflight",
]


@dataclass
class AttachmentTarget:
    """Platform-agnostic destination for an attachment upload.

    Not all fields are used by all platforms — each uploader reads what it
    needs and ignores the rest.
    """

    # Primary destination ID (channel ID, message/draft ID, SF record ID, …)
    destination_id: str
    # Optional secondary field (thread_ts for Slack, etc.)
    thread_ts: str | None = None
    # Human-readable label for error messages
    display_name: str = ""
    # Extra platform-specific metadata
    extra: dict = field(default_factory=dict)


@dataclass
class AttachmentUploadResult:
    """Outcome for one `ResolvedRecordContent` upload attempt."""

    record_id: str
    filename: str
    success: bool
    platform_id: str | None = None  # e.g. Slack file ID, SF ContentDocument ID
    error: str | None = None
    # Link a person can open, when the platform returns one.
    platform_url: str | None = None
    # Name the platform stored the file under, when it differs from `filename`.
    stored_filename: str | None = None
    mime_type: str | None = None

    @property
    def is_image(self) -> bool:
        return bool(self.mime_type and self.mime_type.startswith("image/"))

    @property
    def display_name(self) -> str:
        return self.stored_filename or self.filename

    def to_dict(self) -> dict:
        d: dict = {
            "record_id": self.record_id,
            "filename": self.filename,
            "success": self.success,
        }
        if self.platform_id is not None:
            d["platform_id"] = self.platform_id
        if self.platform_url:
            d["url"] = self.platform_url
        if self.stored_filename and self.stored_filename != self.filename:
            d["stored_as"] = self.stored_filename
        if self.error:
            d["error"] = self.error
        return d


@runtime_checkable
class IAttachmentUploader(Protocol):
    """Contract every per-app attachment uploader must satisfy.

    `max_single_bytes` and `max_total_bytes` are enforced by
    `resolve_attachments` BEFORE calling `upload`; the uploader only needs to
    enforce platform-specific limits (e.g. Outlook ≤ 150 MiB, Salesforce ≤
    25 MiB raw, Gmail conservative cap).
    """

    max_single_bytes: int
    max_total_bytes: int

    async def upload(
        self,
        target: AttachmentTarget,
        attachments: Sequence[ResolvedRecordContent],
    ) -> list[AttachmentUploadResult]:
        """Upload `attachments` to `target`. Returns one result per attachment."""
        ...


@runtime_checkable
class SupportsPreflight(Protocol):
    """Optional uploader capability: check the destination before anything is written.

    Returns an error message when uploads cannot succeed at all (e.g. attachments
    are turned off on the site), else None. May lower `max_single_bytes` to the
    destination's own limit so oversize files fail before the record is created.
    """

    async def preflight(self) -> str | None:
        ...
