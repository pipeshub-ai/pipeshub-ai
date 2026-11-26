"""The broker API's credential in PipesHub: the per-deployment worker credential Node provisions.

Both sides read it through ``ConfiguredWorkerCredential`` on every request, so
a rotation needs no restart and the previous credential works through its
grace window.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.sandbox.auth import AUTHORIZATION, bearer

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.harness.journal_node.credentials import ConfiguredWorkerCredential


class WorkerCredentialBrokerAuth:
    """``BrokerAuth`` and ``BrokerAuthVerifier`` over the worker credential."""

    def __init__(self, credentials: ConfiguredWorkerCredential) -> None:
        self._credentials = credentials

    async def headers(self) -> Mapping[str, str]:
        return {AUTHORIZATION: f"Bearer {await self._credentials.worker_credential()}"}

    async def verify(self, headers: Mapping[str, str], path: str = "") -> bool:
        del path
        return await self._credentials.accepts(bearer(headers))


__all__ = ["WorkerCredentialBrokerAuth"]
