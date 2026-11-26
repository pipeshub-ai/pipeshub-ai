"""The per-deployment worker credential Node generates (REPORT 6.1.8), read through configuration.

Every claimer reads it the same way: the dispatched worker and the inline
claimer in the query service. It is read on every claim, so a rotation needs
no restart; Node accepts the previous credential for a grace window.
"""

from __future__ import annotations

import hmac
import time
from typing import TYPE_CHECKING

from app.config.constants.service import config_node_constants
from harness_core.journal.types import JournalUnavailable
from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.config.configuration_service import ConfigurationService

_LOG = HarnessLog.named("app.harness.journal_node.credentials")


class WorkerCredentialUnavailable(JournalUnavailable):
    """Not provisioned yet (Node provisions it on startup); retry later."""


# DD-3: KV-on-Redis authority stays readable this long through a store outage, then closes.
STALE_LIMIT_S = 15 * 60


class ConfiguredWorkerCredential:
    """A role's credential set as Node stores it (SEC-C-10): the worker's by default, or another role's ``key``."""

    def __init__(
        self,
        config_service: ConfigurationService,
        *,
        clock: Callable[[], float] = time.monotonic,
        stale_limit_s: float = STALE_LIMIT_S,
        key: str = config_node_constants.AGENT_HARNESS_WORKER_CREDENTIAL.value,
    ) -> None:
        self._config_service = config_service
        self._key = key
        self._clock = clock
        self._stale_limit_s = stale_limit_s
        self._last: tuple[dict[str, object], float] | None = None
        self._stale_logged = False

    async def _document(self) -> dict[str, object]:
        """The document as last read. An absent one refuses at once; an unreadable store serves the last read
        until it is ``stale_limit_s`` old, so a Redis outage does not stop every run in flight (QA17-F2)."""
        try:
            document = await self._config_service.get_config(self._key, raise_on_error=True)
        except Exception as exc:
            if self._last is not None and self._clock() - self._last[1] <= self._stale_limit_s:
                if not self._stale_logged:
                    self._stale_logged = True
                    _LOG.warning("worker_credential.stale", reason="store_unavailable", errorType=type(exc).__name__)
                return self._last[0]
            return {}
        current = document if isinstance(document, dict) else {}
        self._last, self._stale_logged = (current, self._clock()), False
        return current

    async def worker_credential(self) -> str:
        current = (await self._document()).get("current")
        if not isinstance(current, str) or not current:
            raise WorkerCredentialUnavailable("agent harness worker credential is not provisioned")
        return current

    async def accepts(self, presented: str | None) -> bool:
        """Whether ``presented`` is the current credential, or the previous one
        inside its grace window. Constant time in the credential's content."""
        if not presented:
            return False
        document = await self._document()
        accepted = [document.get("current")]
        valid_until = document.get("previousValidUntil")
        if isinstance(valid_until, int | float) and time.time() * 1000 < valid_until:
            accepted.append(document.get("previous"))
        return any(
            isinstance(value, str)
            and value
            and hmac.compare_digest(value.encode("utf-8"), presented.encode("utf-8"))
            for value in accepted
        )


__all__ = ["STALE_LIMIT_S", "ConfiguredWorkerCredential", "WorkerCredentialUnavailable"]
