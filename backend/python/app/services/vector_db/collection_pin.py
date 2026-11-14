"""Which weight precision an index was written with.

An index (a collection, or an org's space on a provider with one space per org) keeps one
precision. The pin is a separate key in the configuration store, created with an atomic
"create if absent" so two writers of different precision cannot both win, and removed only
together with the vectors it describes. The collection manifest is not used: it is derived
state that any writer may re-record.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from app.config.configuration_service import delete_config_confirmed
from app.utils.precision_witness import get_precision_witness

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

PIN_DIRECTORY = "/services/vectordb/embedding_pin"
FP32 = "fp32"
INT8 = "int8-w"
# Tag values that are not a precision: positive markers so that "text points" can be counted.
IMAGE_TAG = "image"
STAMP_TAG = "stamp"
# A pin is replaced after a crash only when it is older than the replacer's own observation of an
# empty or missing collection by more than this (clock skew between processes).
STALE_PIN_SKEW_SECONDS = 60.0


@dataclass(frozen=True)
class Pin:
    precision: str
    pinned_at: int  # milliseconds since the epoch
    derived: bool = False  # restored from tags rather than claimed by a first write

    def to_value(self) -> dict:
        return asdict(self)

    @classmethod
    def from_value(cls, raw: object) -> Pin | None:
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                return None
        if not isinstance(raw, dict) or raw.get("precision") not in (FP32, INT8):
            return None
        pinned_at = raw.get("pinned_at")
        return cls(raw["precision"], int(pinned_at) if isinstance(pinned_at, (int, float)) else 0, bool(raw.get("derived")))


class CollectionPinStore:
    def __init__(self, config_service, logger, *, clock: Callable[[], float] = time.time) -> None:  # noqa: ANN001
        self._config = config_service
        self._logger = logger
        self._clock = clock

    @staticmethod
    def key(collection: str, org: str | None = None) -> str:
        return f"{PIN_DIRECTORY}/{collection}" + (f"/{org}" if org else "")

    async def get(self, collection: str, org: str | None = None, *, fresh: bool = False) -> Pin | None:
        """The pin, or None. Raises when the store cannot be read: an unreadable pin is not an absent one."""
        raw = await self._config.get_config(self.key(collection, org), use_cache=not fresh, raise_on_error=True)
        return Pin.from_value(raw)

    async def claim(self, collection: str, precision: str, org: str | None = None, *, derived: bool = False) -> Pin:
        """Create the pin if none exists; return the pin that now stands (ours or the winner's)."""
        mine = Pin(precision, int(self._clock() * 1000), derived)
        created = await self._config.create_config_if_absent(self.key(collection, org), mine.to_value())
        if created:
            return mine
        standing = await self.get(collection, org, fresh=True)
        if standing is None:  # removed between the two calls: the caller asks again
            raise RuntimeError("the pin changed while it was being claimed")
        return standing

    async def release(self, collection: str, org: str | None = None) -> None:
        """Remove the pin; raises when the store cannot confirm it is gone."""
        await delete_config_confirmed(self._config, self.key(collection, org))

    async def keys(self) -> list[str]:
        return list(await self._config.list_keys_in_directory(PIN_DIRECTORY) or [])

    async def release_collection(self, collection: str) -> None:
        """Remove the pin of a collection and, where the provider keeps one space per org, each org's pin under it."""
        prefix = self.key(collection)
        for key in await self.keys():
            if key == prefix or key.startswith(prefix + "/"):
                await delete_config_confirmed(self._config, key)

    async def release_orphans(self, exists: Callable[[str], Awaitable[bool]]) -> list[str]:
        """Remove the pins of collections that are gone; a pin over a collection that still exists stays.

        ``exists`` is asked once per collection named under the directory. A collection that cannot be asked about
        raises, and its pin is left alone: an unreadable collection is not a missing one.
        """
        names = {key[len(PIN_DIRECTORY) + 1:].split("/", 1)[0] for key in await self.keys() if key.startswith(PIN_DIRECTORY + "/")}
        released: list[str] = []
        for name in sorted(names):
            if not await exists(name):
                await self.release_collection(name)
                released.append(name)
        return released

    async def replace_stale(
        self, collection: str, precision: str, observed_empty_at: float, org: str | None = None,
        skew: float = STALE_PIN_SKEW_SECONDS, holds_old: Callable[[str], Awaitable[int]] | None = None,
    ) -> bool:
        """Replace a pin left over by a crash, only if it predates *observed_empty_at* by more than *skew*.

        A writer that claimed after the collection came to exist has ``pinned_at`` at or after the
        moment the replacer saw it empty, so it is never replaced.

        A writer that found the old pin and wrote under it does not touch the pin, so the timestamp alone cannot
        see it. ``holds_old`` counts the points that carry the old pin's precision, and is asked once the
        new pin is in place: any such point puts the old pin back and nothing is replaced (a count that
        cannot be read does the same). Writers that read the pin after the replacement see the new one.
        """
        standing = await self.get(collection, org, fresh=True)
        if standing is None or standing.precision == precision:
            return False
        if standing.pinned_at / 1000.0 >= observed_empty_at - skew:
            return False
        await self._config.update_config(self.key(collection, org), Pin(precision, int(self._clock() * 1000)).to_value())
        after = await self.get(collection, org, fresh=True)
        if after is None or after.precision != precision:
            return False
        if holds_old is not None:
            try:
                kept = await holds_old(standing.precision)
            except Exception:
                kept = 1
                self._logger.warning("could not count the points under the old precision pin of '%s'", collection, exc_info=True)
            if kept:
                await self._config.update_config(self.key(collection, org), standing.to_value())
                return False
        return True

    def now(self) -> float:
        """The wall-clock second a creator observes "absent or empty" at (the pin store's own clock)."""
        return self._clock()

    async def replace_stale_for_writer(
        self, collection: str, model: str | None, observed_at: float, org: str | None = None,
        holds_old: Callable[[str], Awaitable[int]] | None = None,
    ) -> bool:
        """The creator-site form of :meth:`replace_stale`: the writer's precision is what this process last saw the model serve.

        Nothing is replaced without a witness, or under a fallback (a pin is never claimed from a fallback), and a
        store that cannot be read leaves the pin alone: the claim in front of the first write then decides.
        """
        witness = get_precision_witness().latest(model) if model else None
        if witness is None or witness.fallback:
            return False
        try:
            return await self.replace_stale(collection, witness.precision, observed_at, org, holds_old=holds_old)
        except Exception:
            self._logger.warning("could not check for a stale precision pin of '%s'", collection, exc_info=True)
            return False
