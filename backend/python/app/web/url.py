"""One way to name a web page, shared by deduplication, citations and access checks."""

import contextlib
from urllib.parse import urlsplit, urlunsplit

from app.connectors.sources.web.url_identity import clean_url

_DEFAULT_PORTS = {"http": 80, "https": 443}


def canonical_url(url: str) -> str:
    """``url`` without its fragment and click trackers, with a lowercase scheme and host and no
    default port. Kept query parameters stay in the site's order."""
    parts = urlsplit(clean_url(url.strip()))
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if ":" in host:  # an IPv6 literal keeps its brackets
        host = f"[{host}]"
    port = parts.port if parts.port and parts.port != _DEFAULT_PORTS.get(scheme) else None
    netloc = f"{host}:{port}" if port else host
    if parts.username:
        netloc = f"{parts.username}{':' + parts.password if parts.password else ''}@{netloc}"
    return urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))


def display_domain(url: str) -> str:
    """The host a person would recognise: lowercase, without ``www.``. Internationalised names
    are shown in their ``xn--`` form so a lookalike (``аpple.com`` with a Cyrillic а) can't pass
    for the real site."""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    with contextlib.suppress(UnicodeError):  # not a valid IDN: shown as it is
        host = host.encode("idna").decode("ascii")
    return host.removeprefix("www.")
