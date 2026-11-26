"""An aiohttp session that never sends a write twice.

aiohttp sends a ``PUT`` or ``DELETE`` again, once, when the server disconnects before answering
(``IDEMPOTENT_METHODS`` in ``aiohttp.client``): an upload chunk or a delete that landed would go out a second time.
The session has no public switch for it, so the attribute its request loop reads is cleared; SR-13 fails if a
later aiohttp renames it.
"""

from __future__ import annotations

from typing import Any

import aiohttp  # type: ignore


def send_safe_session(**kwargs: Any) -> aiohttp.ClientSession:  # noqa: ANN401 - ClientSession's own options
    session = aiohttp.ClientSession(**kwargs)
    session._retry_connection = False  # pyright: ignore[reportAttributeAccessIssue, reportPrivateUsage]
    return session


__all__ = ["send_safe_session"]
