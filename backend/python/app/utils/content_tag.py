"""The validator of a record's served bytes (``content-tag v2``).

The tag is derived from the storage service's own verified fingerprint of the stored object, so it moves
on every write to that object (a replace, a rollback, a presigned PUT) and on nothing else. It is opaque
and versioned: the version and the conversion pipeline are part of its basis.

``content_tag`` is the only place a tag is built. A record that is not backed by a trusted storage answer
has no tag, and the caller then sends none and never answers 304.
"""

from __future__ import annotations

import base64
import functools
import hashlib
import re
import subprocess
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.services.storage_documents import StorageDocumentInfo

Variant = Literal["original", "pdf"]

#: Bound on the storage service's verified read behind a validator; a slower answer means no validator.
SERVED_VERSION_TIMEOUT_SECONDS = 2.0

_TAG_VERSION = "v2"
_SEP = "\x1f"
_SOFFICE_VERSION_TIMEOUT_SECONDS = 5.0
_CONVERSION_REVISION = "conv:1"


@functools.cache
def preview_pdf_pipeline() -> str | None:
    """The identity of the PDF conversion pipeline, read once per process.

    None when the converter's version cannot be read: a converted answer then gets no tag, because a
    converter upgrade could not be told apart.
    """
    try:
        out = subprocess.run(
            ["soffice", "--version"],
            capture_output=True,
            text=True,
            timeout=_SOFFICE_VERSION_TIMEOUT_SECONDS,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return f"lo:{out}|{_CONVERSION_REVISION}" if out else None


def content_tag(
    record_id: str,
    info: StorageDocumentInfo | None,
    variant: Variant,
    *,
    pipeline: str | None = None,
) -> str | None:
    """The ``ETag`` header value for this record's served bytes, or None when no validator can be given.

    A converted (``pdf``) answer is weak: the converter's output is not byte-identical between runs.
    ``pipeline`` is the conversion pipeline identity; it is required for ``pdf``.
    """
    if info is None or not info.trusted or not info.fingerprint or not info.document_id:
        return None
    if variant == "pdf":
        if not pipeline:
            return None
    else:
        pipeline = ""
    basis = _SEP.join((record_id, info.document_id, info.fingerprint, variant, pipeline or "", _TAG_VERSION))
    digest = hashlib.sha256(basis.encode("utf-8")).digest()
    tag = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")[:22]
    return f'W/"{tag}"' if variant == "pdf" else f'"{tag}"'


_ENTITY_TAG = re.compile(r'\s*(?:W/)?"[^"]*"\s*')


def _opaque(tag: str) -> str:
    tag = tag.strip()
    if tag.startswith("W/"):
        tag = tag[2:]
    return tag


def if_none_match_matches(header: str | None, tag: str | None) -> bool:
    """Whether ``If-None-Match`` names the current tag, by weak comparison (RFC 9110 section 13.1.2).

    ``*`` matches any current representation, so it matches when there is a tag. A header that does not
    parse as a list of entity tags matches nothing.
    """
    if tag is None or not header:
        return False
    value = header.strip()
    if value == "*":
        return True
    parts = [p for p in value.split(",") if p.strip()]
    if not parts or not all(_ENTITY_TAG.fullmatch(p) for p in parts):
        return False
    current = _opaque(tag)
    return any(_opaque(p) == current for p in parts)
