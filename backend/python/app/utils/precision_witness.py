"""What the embedding server last said about the weights that computed its vectors, per model.

The server names the precision on every ``/v1/embeddings`` response. A process keeps the
latest answer for each model it talks to, so a check in front of a delete can ask for a
witness that is recent enough instead of trusting what the client read when it was built.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


@dataclass(frozen=True)
class Witness:
    precision: str
    fallback: bool
    at: float


class PrecisionWitness:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._latest: dict[str, Witness] = {}
        self._probes: dict[str, asyncio.Future[Witness]] = {}

    def record(self, model: str, precision: str, fallback: bool = False) -> None:  # noqa: FBT001, FBT002
        self._latest[model] = Witness(precision, fallback, self._clock())

    def latest(self, model: str) -> Witness | None:
        return self._latest.get(model)

    async def fresh(
        self, model: str, max_age: float, probe: Callable[[], Awaitable[tuple[str, bool]]],
    ) -> Witness:
        """The latest witness if it is no older than *max_age*, else one made by *probe*.

        One probe per model is in flight at a time: waiters share its answer, so a restart does not
        make every worker embed against a model that is still loading.
        """
        known = self._latest.get(model)
        if known is not None and self._clock() - known.at <= max_age:
            return known
        running = self._probes.get(model)
        if running is not None:
            return await asyncio.shield(running)
        future: asyncio.Future[Witness] = asyncio.get_running_loop().create_future()
        self._probes[model] = future
        try:
            precision, fallback = await probe()
            self.record(model, precision, fallback)
            future.set_result(self._latest[model])
        except BaseException as exc:
            if not future.done():
                future.set_exception(exc)
                future.exception()  # mark retrieved when nobody else waits
            raise
        finally:
            self._probes.pop(model, None)
        return future.result()


_default: PrecisionWitness | None = None


def get_precision_witness() -> PrecisionWitness:
    global _default  # noqa: PLW0603
    if _default is None:
        _default = PrecisionWitness()
    return _default


def set_precision_witness(witness: PrecisionWitness | None) -> None:
    global _default  # noqa: PLW0603
    _default = witness
