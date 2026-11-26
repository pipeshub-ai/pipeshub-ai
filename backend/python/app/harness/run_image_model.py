"""The run's image-generation model (WP-55): an image a run's tool asks for is checked, admitted and charged.

``generate_image`` builds its adapter through ``app.utils.llm.build_image_generation_model``, which asks the org's
region pin first (MG-15); inside a harness run the run's scope hands back this wrapper. Each call is checked
against the conversation's classification first (the prompt carries what the conversation read), then reserves the
provider's per-image price for the images asked for on the run's ledger account, takes its provider slot at the run's
class, and settles for the images that came back.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from harness_core.budget.types import MICRO_USD, Amount
from harness_core.gateway.priority_gate import provider_key
from harness_core.kernel.budget.image_prices import image_price_usd
from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from app.harness.classification import ModelGuard
    from harness_core.budget.ports import BudgetAccounts
    from harness_core.budget.types import Reservation, RunAccountRef
    from harness_core.gateway.priority_gate import ProviderAdmission
    from harness_core.ids import IdSource
    from harness_core.priority import PriorityClass

# An image call takes a minute at most; a crashed worker's hold returns after this.
IMAGE_RESERVATION_TTL_S = 600

_LOG = HarnessLog.named("app.harness.run_image_model")


class RunImageModel:
    """An image-generation adapter whose every call is one of the run's: classified, metered per image, admitted."""

    def __init__(
        self,
        inner: Any,  # noqa: ANN401 - app.utils.aimodels.ImageGenerationAdapter
        *,
        guard: ModelGuard,
        configured_provider: str,
        admission: ProviderAdmission | None,
        priority_class: PriorityClass | None,
        accounts: BudgetAccounts | None,
        account: RunAccountRef,
        ids: IdSource,
    ) -> None:
        self._inner = inner
        self._guard = guard
        self._configured_provider = configured_provider
        self._admission = admission
        self._priority_class = priority_class
        self._accounts = accounts
        self._account = account
        self._ids = ids

    @property
    def provider(self) -> str:
        return str(self._inner.provider)

    @property
    def model(self) -> str:
        return str(self._inner.model)

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401 - the adapter's own attributes
        return getattr(self._inner, name)

    def price_usd_micros(self, size: str, count: int) -> int:
        usd, listed = image_price_usd(self.model, size)
        if not listed:
            _LOG.warning("budget.image_unpriced", model=self.model, chargedUsd=usd)
        return round(usd * MICRO_USD) * max(count, 0)

    async def generate(self, prompt: str, *, size: str = "1024x1024", n: int = 1) -> list[bytes]:
        return await self._call(size, n, lambda: self._inner.generate(prompt, size=size, n=n))

    async def edit(self, prompt: str, *, input_image: bytes, size: str = "1024x1024", n: int = 1) -> list[bytes]:
        return await self._call(
            size, n, lambda: self._inner.edit(prompt, input_image=input_image, size=size, n=n)
        )

    async def _call(self, size: str, n: int, ask: Any) -> list[bytes]:  # noqa: ANN401 - a coroutine factory
        self._guard.check(self.model, self._configured_provider)
        held = await self._reserve(size, n)
        try:
            images = await self._admitted(ask)
        except BaseException:
            await self._release(held)
            raise
        await self._commit(held, size, len(images or ()))
        return images

    async def _admitted(self, ask: Any) -> list[bytes]:  # noqa: ANN401
        if self._admission is None:
            return await ask()
        slot = await self._admission.acquire(provider_key(self._configured_provider or self.provider), self._priority_class)
        try:
            return await ask()
        finally:
            slot.release()

    async def _reserve(self, size: str, n: int) -> Reservation | None:
        if self._accounts is None:
            return None
        amount = Amount(usd_micros=self.price_usd_micros(size, n))
        return await self._accounts.reserve(
            self._account, self._ids.new_id("image"), amount, minimum=amount, ttl_s=IMAGE_RESERVATION_TTL_S
        )

    async def _commit(self, held: Reservation | None, size: str, count: int) -> None:
        if held is None or self._accounts is None:
            return
        try:
            _ = await self._accounts.commit(held, Amount(usd_micros=self.price_usd_micros(size, count)))
        except Exception as exc:
            _LOG.error("budget.charge_failed", errorType=type(exc).__name__)

    async def _release(self, held: Reservation | None) -> None:
        if held is None or self._accounts is None:
            return
        try:
            _ = await self._accounts.release(held)
        except Exception as exc:
            _LOG.error("budget.release_failed", errorType=type(exc).__name__)


__all__ = ["IMAGE_RESERVATION_TTL_S", "RunImageModel"]
