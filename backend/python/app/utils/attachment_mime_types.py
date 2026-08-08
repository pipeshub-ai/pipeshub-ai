"""Single source of truth for the MIME types accepted as chat attachments.

Three call sites need to agree on exactly which formats are "supported"
attachments: upload validation (``api/routes/chatbot.py``), chat-mode
attachment resolution (``agents/chat_modes/attachments.py``), and agent-loop
attachment resolution (``utils/attachment_utils.py``). Import from here
instead of re-declaring the allowlist so they can't drift.
"""
from __future__ import annotations

PDF_MIME_TYPE = "application/pdf"
DOCX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

TEXT_ATTACHMENT_MIME_TYPES: frozenset[str] = frozenset({"text/plain", "text/markdown", "text/mdx"})
DOCX_MIME_TYPES: frozenset[str] = frozenset({DOCX_MIME_TYPE})
SPREADSHEET_MIME_TYPES: frozenset[str] = frozenset({XLSX_MIME_TYPE})
DELIMITED_MIME_TYPES: frozenset[str] = frozenset({"text/csv", "text/tab-separated-values"})

# All non-image "document" attachment types, resolved via record_to_message_content.
DOC_ATTACHMENT_MIME_TYPES: frozenset[str] = (
    TEXT_ATTACHMENT_MIME_TYPES
    | {PDF_MIME_TYPE}
    | DOCX_MIME_TYPES
    | SPREADSHEET_MIME_TYPES
    | DELIMITED_MIME_TYPES
)

IMAGE_UPLOAD_MIME_TYPES: frozenset[str] = frozenset({"image/jpeg", "image/jpg", "image/png"})

# Full allowlist for the attachment upload endpoint.
SUPPORTED_ATTACHMENT_MIME_TYPES: frozenset[str] = DOC_ATTACHMENT_MIME_TYPES | IMAGE_UPLOAD_MIME_TYPES


def is_image_attachment_mime(mime_type: str) -> bool:
    return mime_type.lower().startswith("image/")


def is_doc_attachment_mime(mime_type: str) -> bool:
    return mime_type.lower() in DOC_ATTACHMENT_MIME_TYPES
