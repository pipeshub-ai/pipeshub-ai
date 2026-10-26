"""Which URLs may be fetched with the Linear credential.

File links come from markdown that anyone who can write an issue or a comment controls.
Only Linear's own upload host is contacted, over HTTPS, and only it gets the
Authorization header.
"""

from urllib.parse import quote, urlsplit

LINEAR_UPLOAD_HOST = "uploads.linear.app"


def is_linear_upload_url(url: str | None) -> bool:
    """True only for ``https://uploads.linear.app/...`` with no credentials or odd port."""
    if not url or not isinstance(url, str):
        return False
    url = url.strip()
    # urlsplit quietly drops tabs and line breaks, so a URL containing any is refused.
    if any(ch.isspace() or ch < " " or ch == "\x7f" for ch in url):
        return False
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and (parts.hostname or "").lower() == LINEAR_UPLOAD_HOST
        and port in (None, 443)
        and not parts.username
        and not parts.password
    )


def content_disposition(filename: str, disposition: str = "attachment") -> str:
    """A Content-Disposition value that is safe for any file name.

    Header values must be latin-1 and free of control characters, and the name comes from
    link text in Linear. RFC 6266: an ASCII fallback plus the real name percent-encoded.
    """
    cleaned = "".join(ch for ch in (filename or "") if ch >= " " and ch != "\x7f").strip() or "file"
    fallback = "".join(ch if ch.isascii() and ch not in '"\\' else "_" for ch in cleaned)
    return f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{quote(cleaned, safe='')}"
