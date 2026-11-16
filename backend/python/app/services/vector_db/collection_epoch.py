"""Which batches of writes still belong to a collection: the epoch of an index.

A batch of vectors is embedded, then checked against the index's pin, then written, then checked again. A cleanup
that drops the collection in the middle leaves the batch holding facts about an index that no longer exists, and
a pin cannot say so: it is removed with the collection. The epoch is the fact that survives it.

* The collection has a **current token** (a pointer). Every drop makes a new token current before it drops
  anything. A writer reads the pointer before it embeds, tags every point it writes with the token, and the
  claim of a pin and the check after the write are valid only while the pointer still names that token.
* A token is a set of keys, one per state, each created only if absent, so a late writer of a state can never
  bring a token back from a later one: ``issuing`` (created, not yet the pointer's value), ``live`` (has been
  current), ``retired`` (its drop is confirmed: its points are dead; it is ignored in a scope once that scope is swept,
  see ``mark_swept``). A token's state is the highest one present.
* A separate per-scope key says that a pin of the index was released at some time. It is read only to decide
  whether a first claim may skip counting the points.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.utils.env_config import env_seconds

if TYPE_CHECKING:
    from collections.abc import Callable

EPOCH_DIRECTORY = "/services/vectordb/embedding_epoch"
ISSUING, LIVE, RETIRED = "issuing", "live", "retired"
_ORDER = (ISSUING, LIVE, RETIRED)
# Stragglers of a retired token can still write while a record's processing budget runs, plus the hand-back delay,
# the clock skew between processes and the requeue.
SWEEP_MARGIN_SECONDS = 300.0
KEPT_RETIRED_TOKENS = 16
POINT_EPOCH = "embedEpoch"
# A token made by a reset starts with this: its points get ids of their own (``Fence.point_id``). A token made when
# the collection had none (the first, or after the epoch keys were lost) does not, so existing points keep their ids.
RESET_TOKEN_PREFIX = "r"
_POINT_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "pipeshub:epoch-point")


def sweep_window_seconds() -> float:
    """The longest a writer of a retired token can still be writing: the longest record budget plus a margin."""
    return max(
        env_seconds("RECORD_PROCESSING_TIMEOUT", 1800.0), env_seconds("PDF_RECORD_PROCESSING_TIMEOUT", 5400.0),
    ) + SWEEP_MARGIN_SECONDS


@dataclass(frozen=True)
class Fence:
    """The token a batch was accepted under."""

    collection: str
    token: str

    def point_id(self, base: str) -> str:
        """The id a point is stored under: *base* for the first token, else *base* made specific to this token.

        Two batches accepted under different tokens then never address the same point, so neither can replace
        the other's. Within a token the id is a function of the content alone, so a retry overwrites its own.
        """
        if not self.token.startswith(RESET_TOKEN_PREFIX):
            return base
        return str(uuid.uuid5(_POINT_NAMESPACE, f"{base}|{self.token}"))


@dataclass(frozen=True)
class Ticket:
    """What a reset saw live when it began: exactly these are retired when its drop is confirmed."""

    collection: str
    seen: frozenset[str]


@dataclass(frozen=True)
class Dead:
    token: str
    retired_at: float


class CollectionEpochs:
    def __init__(self, config_service, logger, *, clock: Callable[[], float] = time.time) -> None:  # noqa: ANN001
        self._config = config_service
        self._logger = logger
        self._clock = clock
        self._promoted: set[str] = set()  # tokens this process has seen `live`: no need to read their key again

    # -- keys ------------------------------------------------------------------------------------------------

    @staticmethod
    def _base(collection: str) -> str:
        return f"{EPOCH_DIRECTORY}/{collection}"

    def _pointer(self, collection: str) -> str:
        return f"{self._base(collection)}/current"

    def _state_key(self, collection: str, token: str, state: str) -> str:
        return f"{self._base(collection)}/tokens/{token}/{state}"

    def _swept_prefix(self, collection: str, scope: str | None) -> str:
        return f"{self._base(collection)}/swept/{scope or '-'}/"

    def _pin_key(self, collection: str, scope: str | None) -> str:
        return f"{self._base(collection)}/pin/{scope or '-'}"

    # -- primitives ------------------------------------------------------------------------------------------

    async def _read(self, key: str):  # noqa: ANN202
        return await self._config.get_config(key, use_cache=False, raise_on_error=True)

    async def _create(self, key: str, value: dict) -> bool:
        """Create the key if absent; raises when the store cannot say."""
        return bool(await self._config.create_config_if_absent(key, value))

    async def _overwrite(self, key: str, value: dict) -> None:
        """Write the key and read it back; raises when it did not land (``set_config`` answers False instead)."""
        if not await self._config.set_config(key, value, keep_in_cache=False):
            raise RuntimeError("the epoch could not be written")
        stored = await self._read(key)
        if stored != value:
            raise RuntimeError("the epoch could not be written")

    async def current(self, collection: str) -> str | None:
        raw = await self._read(self._pointer(collection))
        token = raw.get("token") if isinstance(raw, dict) else None
        return token if isinstance(token, str) and token else None

    async def _promote(self, collection: str, token: str) -> None:
        if token in self._promoted:
            return
        await self._create(self._state_key(collection, token, LIVE), {"at": int(self._clock() * 1000)})
        self._promoted.add(token)

    async def states(self, collection: str) -> dict[str, tuple[str, float]]:
        """Each known token's highest state, and the time of its retirement when it has one."""
        prefix = f"{self._base(collection)}/tokens/"
        found: dict[str, dict[str, str]] = {}
        for key in await self._config.list_keys_in_directory(prefix) or []:
            if not key.startswith(prefix):
                continue
            parts = key[len(prefix):].split("/")
            if len(parts) == 2 and parts[1] in _ORDER:
                found.setdefault(parts[0], {})[parts[1]] = key
        out: dict[str, tuple[str, float]] = {}
        for token, keys in found.items():
            state = max(keys, key=_ORDER.index)
            when = 0.0
            if state == RETIRED:
                raw = await self._read(keys[RETIRED])
                when = float(raw.get("retired_at", 0)) / 1000.0 if isinstance(raw, dict) else 0.0
            out[token] = (state, when)
        return out

    # -- writers ---------------------------------------------------------------------------------------------

    async def capture(self, collection: str) -> Fence:
        """The token a batch is accepted under. Never none: the first call creates the first token."""
        token = await self.current(collection)
        if token is None:
            fresh = uuid.uuid4().hex
            await self._create(self._state_key(collection, fresh, ISSUING), {"at": int(self._clock() * 1000)})
            await self._create(self._pointer(collection), {"token": fresh})
            token = await self.current(collection)
            if token is None:
                raise RuntimeError("the epoch of the collection could not be created")
        await self._promote(collection, token)
        return Fence(collection, token)

    async def moved(self, fence: Fence) -> bool:
        return await self.current(fence.collection) != fence.token

    # -- resets ----------------------------------------------------------------------------------------------

    async def begin_reset(self, collection: str) -> Ticket:
        """Make a new token current, before anything is dropped. Raises when it could not be written."""
        previous = await self.current(collection)
        seen = {t for t, (state, _) in (await self.states(collection)).items() if state == LIVE}
        if previous:
            seen.add(previous)
        fresh = RESET_TOKEN_PREFIX + uuid.uuid4().hex
        await self._create(self._state_key(collection, fresh, ISSUING), {"at": int(self._clock() * 1000)})
        if not await self._config.set_config(self._pointer(collection), {"token": fresh}, keep_in_cache=False):
            raise RuntimeError("the epoch of the collection could not be moved")
        named = await self.current(collection)
        if named is None or named == previous:
            raise RuntimeError("the epoch of the collection could not be moved")
        await self._create(self._state_key(collection, fresh, LIVE), {"at": int(self._clock() * 1000)})
        self._promoted.add(fresh)
        return Ticket(collection, frozenset(seen - {fresh}))

    async def confirm_reset(self, ticket: Ticket) -> None:
        """The drop is confirmed: the tokens the reset saw live (but not the current one) are retired."""
        current = await self.current(ticket.collection)
        now = int(self._clock() * 1000)
        for token in sorted(ticket.seen):
            if token != current:
                await self._create(self._state_key(ticket.collection, token, RETIRED), {"retired_at": now})
        await self._prune(ticket.collection)

    async def _prune(self, collection: str) -> None:
        await self._forget_old_retired(collection, await self.states(collection))

    async def _forget_old_retired(self, collection: str, states: dict[str, tuple[str, float]]) -> None:
        """A token retired long ago is dropped, with the keys that say where it was swept, once more than the kept number are newer.

        A sweep reaches one space at a time and no sweep knows every space, so without this the retired tokens (and
        their per-scope keys) would grow with every cleanup. Whatever such a token left in a space an idle organisation
        owns is removed by the next drop.
        """
        retired = sorted(((when, t) for t, (state, when) in states.items() if state == RETIRED), reverse=True)
        window = sweep_window_seconds()
        old = [t for when, t in retired[KEPT_RETIRED_TOKENS:] if self._clock() > when + window]
        if not old:
            return
        prefix = f"{self._base(collection)}/swept/"
        scoped = [key for key in await self._config.list_keys_in_directory(prefix) or [] if key.rsplit("/", 1)[-1] in old]
        for token in old:
            for state in _ORDER:
                await self._config.delete_config(self._state_key(collection, token, state))
        for key in scoped:
            await self._config.delete_config(key)

    # -- what the guard asks ---------------------------------------------------------------------------------

    def now(self) -> float:
        return self._clock()

    async def swept_for(self, collection: str, scope: str | None) -> set[str]:
        """The retired tokens whose points are gone from this scope (one organisation's space, or the whole collection)."""
        prefix = self._swept_prefix(collection, scope)
        keys = await self._config.list_keys_in_directory(prefix) or []
        return {key[len(prefix):] for key in keys if key.startswith(prefix) and "/" not in key[len(prefix):]}

    async def dead(self, collection: str, scope: str | None = None) -> list[Dead]:
        """Tokens whose points are dead and not yet swept from this scope."""
        retired = [Dead(t, when) for t, (state, when) in (await self.states(collection)).items() if state == RETIRED]
        if not retired:
            return []
        gone = await self.swept_for(collection, scope)
        return [item for item in retired if item.token not in gone]

    async def quiet(self, collection: str, scope: str | None) -> bool:
        """No reset or release inside the window: nothing but the first writers can be in the index."""
        if await self._read(self._pin_key(collection, scope)) is not None:
            return False
        current = await self.current(collection)
        states = await self.states(collection)
        gone = await self.swept_for(collection, scope) if any(state == RETIRED for state, _ in states.values()) else set()
        return not any(
            (state == RETIRED and token not in gone) or (state == LIVE and token != current)
            for token, (state, _) in states.items()
        )

    async def mark_swept(self, collection: str, token: str, scope: str | None) -> None:
        """The token's points are gone from this scope (one organisation's space, or the whole shared collection).

        A sweep deletes the points of one organisation, so what it proves is that scope's: the token stays retired
        for every other scope until that scope's own sweep.
        """
        await self._create(f"{self._swept_prefix(collection, scope)}{token}", {"at": int(self._clock() * 1000)})

    def expired(self, dead: Dead, window: float) -> bool:
        return self._clock() > dead.retired_at + window

    async def pin_released(self, collection: str, scope: str | None) -> None:
        """Record that a pin of this index was removed (before the removal)."""
        await self._overwrite(self._pin_key(collection, scope), {"token": uuid.uuid4().hex})
