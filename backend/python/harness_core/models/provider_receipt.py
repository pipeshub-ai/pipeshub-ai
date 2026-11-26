"""Which provider response a model call became, for a later usage lookup (WP-66).

A call the gateway cuts short has no usage from its provider. A provider that keeps a response (OpenAI and Azure
Responses with ``store``) can say what it billed for it afterwards, by the response's id. The gateway opens a scope
around the call's request with a fresh receipt; a transport that learns the id while it streams notes it there, and
says whether the provider kept the response. A transport that can look usage up offers ``UsageLookup``; the
gateway asks only the transport that made the call, so the lookup goes out with that call's credentials, account
and region and never another's.

Every PipesHub transport sends ``store: false`` (a conversation is not kept at the vendor), so none notes a kept
response today and every cut call settles on its estimate after the hold's time.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Generator

    from harness_core.kernel.core.responses import TokenUsage


@dataclass(slots=True)
class ProviderReceipt:
    """Filled by the transport during the call: the provider's response id, and whether the provider kept it."""

    response_id: str | None = None
    stored: bool = False

    def note(self, response_id: str | None, *, stored: bool) -> None:
        if response_id:
            self.response_id, self.stored = response_id, stored


@runtime_checkable
class UsageLookup(Protocol):
    async def reported_usage(self, response_id: str) -> TokenUsage | None:
        """What the provider billed for a response it kept; ``None`` when it cannot say (yet)."""
        ...


_RECEIPT: ContextVar[ProviderReceipt | None] = ContextVar("harness_model_provider_receipt", default=None)


@contextmanager
def receipt_scope(receipt: ProviderReceipt) -> Generator[None]:
    token = _RECEIPT.set(receipt)
    try:
        yield
    finally:
        _RECEIPT.reset(token)


def current_receipt() -> ProviderReceipt | None:
    """The receipt of the call being built; a transport keeps it for the rest of the call. ``None``: nobody asked."""
    return _RECEIPT.get()


__all__ = ["ProviderReceipt", "UsageLookup", "current_receipt", "receipt_scope"]
