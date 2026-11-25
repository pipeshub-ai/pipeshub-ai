"""Where a process learns of a revoked release key (R41SUPPLY-FU-6, ADR-SEC-06 D1).

The harness worker fetches the signed list (``PIPESHUB_REVOKED_KEYS_URL``, by default the repository's
``contracts/release-keys/revoked_keys.json`` and its ``.sig``) every ``REFRESH_S``, and keeps the newest valid one in
the deployment's store (``REVOCATIONS_CONFIG_KEY``) as the last good copy; every process that verifies release data
(the worker, the sandbox broker) reads that copy at start and at each refresh, and checks its signature itself, so the
store is trusted for nothing but availability.

The fail-safe, decided in ADR-SEC-06:

- Unreachable, offline, or no list published: keep the list in force (the stored copy, else the packaged one) and go
  on. Revocation is the fast path for an install that can hear of a theft; one that cannot (air-gapped) is bounded by
  each key's not-after, which needs no network.
- A list that does not verify, is older, drops a revocation or is dated ahead: refused and logged; the list in force
  stays. Nothing a fetch returns can widen trust.
- Only a key that is revoked or past its not-after fails closed: what it signed stops verifying.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final, cast

from app.core.org_context import spawn_detached
from harness_core.logs import HarnessLog
from harness_core.supply_chain.release_keys import RevocationRefused, format_instant
from harness_engines.release_keys import apply_revocations, revocations

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.config.configuration_service import ConfigurationService

REVOCATIONS_CONFIG_KEY: Final = "/services/harness/revokedReleaseKeys"
URL_ENV: Final = "PIPESHUB_REVOKED_KEYS_URL"
DEFAULT_URL: Final = (
    "https://raw.githubusercontent.com/pipeshub-ai/pipeshub-ai/main/contracts/release-keys/revoked_keys.json"
)
REFRESH_S: Final = 6 * 3600.0
FETCH_TIMEOUT_S: Final = 10.0
MAX_BYTES: Final = 64 * 1024
#: After this long without a successful fetch (fetching on), each refresh says so.
STALE_AFTER: Final = timedelta(days=7)
_LOG = HarnessLog.named("app.harness.release_keys")


def revocations_url(env: dict[str, str] | None = None) -> str | None:
    """The list's URL; ``None`` when fetching is off (an empty value or ``off``)."""
    raw = (os.environ if env is None else env).get(URL_ENV)
    if raw is None:
        return DEFAULT_URL
    raw = raw.strip()
    return None if raw in ("", "off") else raw


async def http_fetch(url: str) -> bytes | None:
    """The body at ``url``; ``None`` when nothing is published there (404). Raises on anything else."""
    import httpx

    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_S, follow_redirects=False) as client:
        async with client.stream("GET", url) as response:
            if response.status_code == 404:
                return None
            _ = response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_BYTES:
                    raise ValueError("the revocation list is larger than a list can be")
            return bytes(body)


class RevocationRefresher:
    """Reads the stored copy, fetches when it may, applies what is newer, and stores what it applied."""

    def __init__(
        self,
        config_service: ConfigurationService,
        *,
        url: str | None,
        fetch: Callable[[str], Awaitable[bytes | None]] = http_fetch,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._config = config_service
        self._url = url
        self._fetch = fetch
        self._clock = clock
        self._last_fetched: datetime | None = None
        self._started = clock()
        self._task: asyncio.Task[None] | None = None

    def _apply(self, document: bytes | None, signature: bytes | None, source: str) -> bool:
        before = revocations()
        try:
            after = apply_revocations(document, signature, self._clock())
        except RevocationRefused as refused:
            if refused.reason != "stale":
                _LOG.warning("release_keys.revocations_refused", source=source, reason=refused.reason)
            return False
        if after != before:
            _LOG.warning(
                "release_keys.revocations_applied",
                source=source,
                issued=format_instant(after.issued) if after.issued else None,
                revoked=sorted(after.revoked),
            )
        return True

    async def _stored(self) -> None:
        raw = await self._config.get_config(REVOCATIONS_CONFIG_KEY, use_cache=False, raise_on_error=True)
        if isinstance(raw, dict):
            stored = cast("dict[str, object]", raw)
            document, signature = stored.get("document"), stored.get("signature")
            if isinstance(document, str) and isinstance(signature, str):
                _ = self._apply(document.encode(), signature.encode(), "store")

    async def _fetched(self, url: str) -> None:
        document = await self._fetch(url)
        signature = await self._fetch(f"{url}.sig") if document is not None else None
        self._last_fetched = self._clock()
        if document is None or signature is None:
            return
        if self._apply(document, signature, "fetch"):
            _ = await self._config.set_config(
                REVOCATIONS_CONFIG_KEY, {"document": document.decode(), "signature": signature.decode()}
            )

    async def read_stored(self) -> None:
        """Puts the stored copy in force; never raises."""
        try:
            await self._stored()
        except Exception as exc:
            _LOG.warning("release_keys.store_unreadable", errorType=type(exc).__name__)

    async def refresh_once(self) -> None:
        """The stored copy, then a fetch when fetching is on. Never raises: whatever fails, the list in force stays."""
        await self.read_stored()
        if self._url is None:
            return
        try:
            await self._fetched(self._url)
        except Exception as exc:
            _LOG.info("release_keys.revocations_unreachable", errorType=type(exc).__name__)
        since = self._last_fetched or self._started
        if self._clock() - since > STALE_AFTER:
            _LOG.warning("release_keys.revocations_stale", lastFetched=format_instant(since))

    async def _run(self, interval_s: float) -> None:
        while True:
            await self.refresh_once()
            await asyncio.sleep(interval_s)

    async def start(self, interval_s: float = REFRESH_S) -> None:
        """The stored copy is in force when this returns, before the caller verifies anything; the fetch, which may
        wait on a network an air-gapped host lacks, runs in the background, then every ``interval_s``."""
        await self.read_stored()
        self._task = spawn_detached(self._run(interval_s), name="release-key-revocations")

    async def stop(self) -> None:
        if self._task is not None:
            _ = self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None


__all__ = [
    "DEFAULT_URL",
    "REFRESH_S",
    "REVOCATIONS_CONFIG_KEY",
    "URL_ENV",
    "RevocationRefresher",
    "http_fetch",
    "revocations_url",
]
