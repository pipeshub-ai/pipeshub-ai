"""Where the Gmail connectors send their Google calls: Google itself, unless a test stack says otherwise.

One job: read GMAIL_GOOGLE_API_BASE_URL. The integration tests set it to a Gmail simulator so the connectors run
end to end without a real mailbox; nothing else should. It covers every call the Gmail connectors make (Gmail,
the Directory API, Drive for linked files, and the OAuth token endpoint), and only theirs: the Drive connectors
in the same deployment keep calling Google.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

ENV = "GMAIL_GOOGLE_API_BASE_URL"


def gmail_api_base_url() -> str | None:
    """The base URL to call instead of Google's hosts, or None. A malformed value is refused rather than
    guessed at: a wrong host would receive the connector's tokens."""
    raw = os.environ.get(ENV, "").strip()
    if not raw:
        return None
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.path not in ("", "/") \
            or parts.query or parts.fragment or parts.username or parts.password:
        raise ValueError(f"{ENV} must be a bare http(s) origin such as http://gmail-sim.internal")
    return f"{parts.scheme}://{parts.netloc}"
