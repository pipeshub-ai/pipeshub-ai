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
from app.services.vector_db.collection_epoch import CollectionEpochs

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
        self.epochs = CollectionEpochs(config_service, logger, clock=clock)

    @staticmethod
    def key(collection: str, org: str | None = None) -> str:
        return f"{PIN_DIRECTORY}/{collection}" + (f"/{org}" if org else "")

    async def get(self, collection: str, org: str | None = None, *, fresh: bool = False) -> Pin | None:
        """The pin, or None. Raises when the store cannot be read: an unreadable pin is not an absent one."""
        raw = await self._config.get_config(self.key(collection, org), use_cache=not fresh, raise_on_error=True)
        return Pin.from_value(raw)

    async def _try_create(
        self, collection: str, precision: str, org: str | None = None, *, derived: bool = False,
    ) -> tuple[Pin, bool]:
        """Create the pin if none exists: the pin that now stands, and whether this call created it.

        Only ``PrecisionGuard._claim`` (which checks the epoch around it) may call this: a pin made any other way
        is a claim no cleanup can see.
        """
        mine = Pin(precision, int(self._clock() * 1000), derived)
        created = await self._config.create_config_if_absent(self.key(collection, org), mine.to_value())
        if created:
            return mine, True
        standing = await self.get(collection, org, fresh=True)
        if standing is None:  # removed between the two calls: the caller asks again
            raise RuntimeError("the pin changed while it was being claimed")
        return standing, False

    async def _claim_unfenced(self, collection: str, precision: str, org: str | None = None, *, derived: bool = False) -> Pin:
        """:meth:`_try_create` without the epoch checks. For tests that seed a pin; nothing in ``app`` may call it."""
        return (await self._try_create(collection, precision, org, derived=derived))[0]

    async def release(self, collection: str, org: str | None = None) -> None:
        """Remove the pin; raises when the store cannot confirm it is gone.

        The release is recorded first (a failure to record it stops the release): a first claim skips counting
        the points only while no pin of the index was ever released.
        """
        await self.epochs.pin_released(collection, org)
        await delete_config_confirmed(self._config, self.key(collection, org))

    async def keys(self) -> list[str]:
        return list(await self._config.list_keys_in_directory(PIN_DIRECTORY) or [])

    async def release_collection(self, collection: str) -> None:
        """Remove the pin of a collection and, where the provider keeps one space per org, each org's pin under it.

        Each release is recorded before its pin goes, as :meth:`release` does: a space whose pin was removed is not quiet,
        so a writer that finds the pin missing counts the points instead of trusting an empty index.
        """
        prefix = self.key(collection)
        for key in await self.keys():
            if key == prefix or key.startswith(prefix + "/"):
                scope = key[len(prefix) + 1:] or None
                await self.epochs.pin_released(collection, scope)
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

    def now(self) -> float:
        """The wall-clock second a creator observes "absent or empty" at (the pin store's own clock)."""
        return self._clock()
