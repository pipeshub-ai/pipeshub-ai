"""Utilities for persisting Microsoft Graph attachment content types without hardcoded maps."""

import mimetypes

from app.utils.image_utils import get_extension_from_mimetype


def normalize_content_type(content_type: str) -> str:
    """Return the base MIME type from a Graph API contentType value."""
    return content_type.split(";", 1)[0].strip().lower()


def derive_attachment_extension(file_name: str | None, content_type: str) -> str | None:
    """Derive a file extension from the attachment name and/or content type."""
    if file_name and "." in file_name:
        extension = file_name.rsplit(".", 1)[-1].lower()
        if extension:
            return extension

    normalized_mime_type = normalize_content_type(content_type)
    extension = get_extension_from_mimetype(normalized_mime_type)
    if extension:
        return extension

    if file_name:
        guessed_mime_type, _ = mimetypes.guess_type(file_name)
        if guessed_mime_type:
            return get_extension_from_mimetype(guessed_mime_type)

    return None


def attachment_metadata_from_graph(
    attachment_name: str | None,
    content_type: str,
    default_name: str,
) -> tuple[str, str, str | None]:
    """Build attachment record metadata from Graph API fields."""
    file_name = attachment_name or default_name
    mime_type = normalize_content_type(content_type)
    extension = derive_attachment_extension(file_name, content_type)
    return file_name, mime_type, extension
