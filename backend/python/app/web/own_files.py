"""Download links PipesHub handed the model for a file of its own.

Read through ``fetch_url`` such a link becomes a web page: the answer cites a
link that downloads the file instead of the file, and the signed link is shown
in the step and may go to an outside page reader. ``get_record_download_url``
remembers each link it hands out so ``fetch_url`` can send the model back to
the record.
"""

import re
from collections.abc import MutableMapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

from app.utils.url_redaction import REDACTED, redact_sensitive_query_params

ISSUED_DOWNLOAD_LINKS_KEY = "issued_download_links"

# Query parameters that carry a storage provider's signature: S3, Google Cloud Storage, Azure SAS.
_SIGNATURE_PARAMS = frozenset({"x-amz-signature", "x-goog-signature", "sig"})
# Ends at a quote, so a link inside a JSON string stops at the string's end.
_URL_IN_TEXT = re.compile(r"https?://[^\s'\"<>)\\]+")
# A signature or credential value wherever it is: a link with a space, `(` or `'` in its file name
# ends the scan above early, and the signature after that point still opens the file. The value stops
# at a backslash, which in JSON text escapes the quote that closes the string. JSON written by some
# encoders (Go's, by default) spells `&` as `\u0026`.
_SIGNATURE_VALUE = re.compile(
    r"(?i)((?:[?&]|\\u0026)(?:x-amz-signature|x-goog-signature|sig|x-amz-credential|x-amz-security-token|x-goog-credential)=)"
    r"[^&\s\"'<>)\\]+"
)


@dataclass(frozen=True)
class IssuedFile:
    record_id: str
    file_name: str


def _signatures(query: str) -> list[str]:
    # `unquote`, not parse_qsl's `unquote_plus`: a base64 signature's `+` arrives as `%2B` or as a
    # literal `+` depending on who copied the link, and both must read the same.
    found = []
    for pair in query.split("&"):
        name, _, value = pair.partition("=")
        if unquote(name).lower() in _SIGNATURE_PARAMS and value:
            found.append(unquote(value))
    return found


def link_keys(url: str) -> list[str]:
    """The keys a link is compared by: the model may re-encode it or reorder its query when copying it.

    A signed link is known by its signatures, which no other link carries. Every one counts: an Azure
    SAS still opens the file with parameters added, a made-up signature parameter among them."""
    parts = urlsplit(url.strip())
    if signatures := _signatures(parts.query):
        return [f"signature:{signature}" for signature in signatures]
    query = sorted(parse_qsl(parts.query, keep_blank_values=True))
    return [f"{parts.scheme.lower()}://{parts.netloc.lower()}{unquote(parts.path)}?{query}"]


def remember_download_link(state: MutableMapping[str, Any], url: str | None, *, record_id: str, file_name: str) -> None:
    if not url:
        return
    issued = state.setdefault(ISSUED_DOWNLOAD_LINKS_KEY, {})
    for key in link_keys(url):
        issued[key] = IssuedFile(record_id, file_name)


def issued_file(issued: MutableMapping[str, IssuedFile] | None, url: str) -> IssuedFile | None:
    if not issued or not url:
        return None
    try:
        keys = link_keys(url)
    except ValueError:  # not a URL urlsplit can read; fetch_url reports it as such
        return None
    return next((issued[key] for key in keys if key in issued), None)


def signature_removed(url: str) -> bool:
    """A signed link as a saved step keeps it: the signature hidden, so it no longer opens anything."""
    try:
        return REDACTED in _signatures(urlsplit(url.strip()).query)
    except ValueError:
        return False


def is_signed_storage_link(url: str) -> bool:
    try:
        names = {name.lower() for name, _ in parse_qsl(urlsplit(url).query, keep_blank_values=True)}
    except ValueError:
        return False
    return bool(names & _SIGNATURE_PARAMS)


def hide_storage_signatures(text: str) -> str:
    """``text`` with the signature and credentials of each signed storage link hidden. The rest of
    such a link, and every other link, is left as it was."""
    if "?" not in text:
        return text
    text = _URL_IN_TEXT.sub(
        lambda m: redact_sensitive_query_params(m.group(0)) if is_signed_storage_link(m.group(0)) else m.group(0),
        text,
    )
    return _SIGNATURE_VALUE.sub(lambda m: m.group(1) + REDACTED, text)
