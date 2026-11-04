"""Where the Dropbox connectors send their Dropbox calls: Dropbox itself, unless a test stack says otherwise.

One job: read DROPBOX_API_BASE_URL. The integration tests set it to a Dropbox simulator so the connectors run end to
end without a real Dropbox; nothing else should. The Dropbox SDK always calls https://api.dropboxapi.com,
https://content.dropboxapi.com and https://notify.dropboxapi.com (and refreshes tokens at the first) through its
own pinned certificate bundle, so a TLS stand-in cannot take their place: the SDK is given a session that sends
those hosts to the base URL instead, keeping each request's Host header so the simulator can tell them apart.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests

ENV = "DROPBOX_API_BASE_URL"
DROPBOX_HOSTS = frozenset({"api.dropboxapi.com", "content.dropboxapi.com", "notify.dropboxapi.com"})


def dropbox_api_base_url() -> str | None:
    """The base URL to call instead of Dropbox's hosts, or None. A malformed value is refused rather than guessed
    at: a wrong host would receive the connector's tokens."""
    raw = os.environ.get(ENV, "").strip()
    if not raw:
        return None
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.path not in ("", "/") \
            or parts.query or parts.fragment or parts.username or parts.password:
        raise ValueError(f"{ENV} must be a bare http(s) origin such as http://dropbox-sim.internal")
    return f"{parts.scheme}://{parts.netloc}"


class _RedirectedSession(requests.Session):
    """A requests session that sends Dropbox's API hosts to one base URL and refuses every other host."""

    def __init__(self, base_url: str) -> None:
        super().__init__()
        parts = urlsplit(base_url)
        self._scheme, self._netloc = parts.scheme, parts.netloc

    def request(self, method: str, url: str, *args: Any, **kwargs: Any) -> requests.Response:  # noqa: ANN401
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if host not in DROPBOX_HOSTS:
            raise ValueError(f"{ENV} is set: refusing to send a Dropbox client request to {host}")
        headers = dict(kwargs.pop("headers", None) or {})
        headers["Host"] = host
        target = urlunsplit((self._scheme, self._netloc, parts.path, parts.query, ""))
        return super().request(method, target, *args, headers=headers, **kwargs)


def session_for_base_url() -> requests.Session | None:
    """The session the SDK must use when a test stack points it at a simulator; None in production."""
    base = dropbox_api_base_url()
    return _RedirectedSession(base) if base else None
