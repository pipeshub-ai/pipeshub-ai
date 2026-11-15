"""Checks, in front of a write, that the vectors have the precision of the index they go into.

The witnessed precision of a batch comes from the embedding server's response. It is compared with the
index's pin immediately before ``upsert_points``. An index with no pin takes its pin from the facts in the
index (tagged points, then legacy points) and from the first writer only when it is empty. Every predicate is
positive: it asks for points with a given tag, never for points without one, so image points and the NULL
semantics of a SQL store play no part.
"""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
from dataclasses import dataclass

from app.config.configuration_service import delete_config_confirmed
from app.core.db.errors import DbTimeout
from app.services.messaging.precision_flip import (
    CLEANUP_HINT,
    FenceMoved,
    PrecisionFlip,
    setting_for,
)
from app.services.vector_db.collection_epoch import (
    POINT_EPOCH,
    Fence,
    sweep_window_seconds,
)
from app.services.vector_db.collection_pin import (
    FP32,
    IMAGE_TAG,
    INT8,
    STALE_PIN_SKEW_SECONDS,
    STAMP_TAG,
    CollectionPinStore,
    Pin,
)
from app.utils.precision_witness import get_precision_witness

# The counts that derive an index's precision are unindexed scans on a store with one space per organisation; they
# are bounded so that a large space cannot hold a consumer or a claim.
DERIVE_TIMEOUT_SECONDS = 3.0
TAG_FIELD = "embedPrecision"
_ORG_FIELD = "orgId"


class PrecisionUnreadable(Exception):
    """The pin or the facts behind it could not be read; the write must not go ahead."""


class PrecisionDerivationTimeout(PrecisionUnreadable):
    """The counts that derive an index's precision did not finish in time: nothing is known, which is not "legacy"."""


def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError | DbTimeout):
        return True
    return type(exc).__name__ in {"QueryCanceledError", "LexicalSearchTimeout"}


@dataclass(frozen=True)
class Standing:
    """What an index is: its precision, and whether that is a claim, a pin, or only the legacy default."""

    precision: str | None
    source: str  # "pin", "derived", "legacy", "none"


class PrecisionGuard:
    def __init__(self, pins: CollectionPinStore, vector_db_service, logger) -> None:  # noqa: ANN001
        self._pins = pins
        self._db = vector_db_service
        self._logger = logger

    # -- facts about the points ------------------------------------------------------------------------------

    async def _count(self, collection: str, org: str | None, tag: str, epochs: list[str] | None = None) -> int:
        must: dict = {TAG_FIELD: [tag]}
        if epochs:
            must[POINT_EPOCH] = list(epochs)
        if org:
            must[_ORG_FIELD] = org
        expression = await self._db.filter_collection(must=must)
        return int(await self._db.count_points(collection, expression))

    async def tag_counts(self, collection: str, org: str | None) -> dict[str, int]:
        """Every tagged point by tag, dead ones included."""
        return {tag: await self._count(collection, org, tag) for tag in (INT8, FP32, IMAGE_TAG, STAMP_TAG)}

    async def _dead_counts(self, collection: str, org: str | None, tokens: list[str]) -> dict[str, int]:
        """The points of the retired tokens by precision: read after the totals, so a sweep between them only lowers a live count."""
        return {tag: await self._count(collection, org, tag, tokens) for tag in (INT8, FP32)}

    async def _points_count(self, collection: str) -> int:
        info = await self._db.get_collection_info(collection)
        return int(info.points_count) if info.exists else 0

    def _per_org_spaces(self) -> bool:
        """Whether the store keeps one space per organisation (its counts need the org bound and are unindexed scans)."""
        try:
            return getattr(self._db.get_capabilities(), "spaces_per_org", False) is True
        except Exception:  # a store that does not say is not one
            return False

    def per_org_spaces(self) -> bool:
        return self._per_org_spaces()

    def scope(self, org: str | None) -> str | None:
        """The org that names an index: the org on a store with one space per organisation, none on any other.

        A store with one collection for everyone has one pin and one set of counts for that collection, whatever
        organisation a writer or the verdict poll happens to carry; keying them by org would give one collection
        several pins, and compare an org's tag counts with the collection's whole point count.
        """
        return org if self._per_org_spaces() else None

    async def derive(self, collection: str, org: str | None) -> Standing:
        """The precision of an index without a pin, from positive facts, and nothing written.

        On a store with one space per organisation the reads run for that org (bound for the block) under a
        statement timeout; running out of time raises ``PrecisionDerivationTimeout``: nothing was derived, and
        the caller decides what that means (refuse a claimant, or no derivation at read time).
        """
        org = self.scope(org)
        if not self._per_org_spaces():
            return await self._derive(collection, org)
        if not org:
            raise PrecisionUnreadable(f"the precision of '{collection}' is derived per organisation and none was given")
        from app.services.vector_db.pgvector.scope import org_scope, statement_timeout

        try:
            with ExitStack() as stack:
                stack.enter_context(org_scope(org))
                stack.enter_context(statement_timeout(int(DERIVE_TIMEOUT_SECONDS * 1000)))
                async with asyncio.timeout(DERIVE_TIMEOUT_SECONDS + 1.0):
                    return await self._derive(collection, org)
        except PrecisionUnreadable:
            raise
        except Exception as exc:
            if _is_timeout(exc):
                raise PrecisionDerivationTimeout(f"counting the points of '{collection}' took too long") from exc
            raise

    async def _derive(self, collection: str, org: str | None) -> Standing:
        counts = await self.tag_counts(collection, org)
        dead = await self._pins.epochs.dead(collection)
        live = dict(counts)
        if dead and (counts[INT8] or counts[FP32]):
            gone = await self._dead_counts(collection, org, [d.token for d in dead])
            live[INT8] = max(0, counts[INT8] - gone[INT8])
            live[FP32] = max(0, counts[FP32] - gone[FP32])
        if live[INT8] and live[FP32]:
            raise PrecisionUnreadable(f"collection '{collection}' holds points of both precisions")
        # Text points with no tag were written before the tags existed, by weights that were never int8: fp32.
        # Every tagged point is taken out of the total, the dead ones too: a dead int8 point is not a legacy one.
        untagged = await self._points_count(collection) - counts[INT8] - counts[FP32] - counts[IMAGE_TAG] - counts[STAMP_TAG]
        if live[INT8]:
            if untagged > 0:
                raise PrecisionUnreadable(f"collection '{collection}' holds int8 points and untagged (fp32) points")
            return Standing(INT8, "derived")
        if live[FP32]:
            return Standing(FP32, "derived")
        if untagged > 0:
            return Standing(FP32, "legacy")
        return Standing(None, "none")

    # -- leaks at the end of a record ------------------------------------------------------------------------

    @staticmethod
    def leak_tag(pin_precision: str) -> str:
        """The tag a point must not carry in an index of this precision. Positive on purpose: a tag, not an absence."""
        return INT8 if pin_precision == FP32 else FP32

    async def _record_filter(self, org: str | None, virtual_record_id: str, tag: str):  # noqa: ANN202
        must: dict = {"virtualRecordId": virtual_record_id, TAG_FIELD: [tag]}
        if org:
            must[_ORG_FIELD] = org
        return await self._db.filter_collection(must=must)

    async def leaked(self, collection: str, org: str | None, virtual_record_id: str, pin_precision: str) -> int:
        expression = await self._record_filter(org, virtual_record_id, self.leak_tag(pin_precision))
        return int(await self._db.count_points(collection, expression))

    async def delete_leaked(self, collection: str, org: str | None, virtual_record_id: str, pin_precision: str) -> None:
        """Delete only the points of the record that carry the other precision's tag, never the whole record."""
        expression = await self._record_filter(org, virtual_record_id, self.leak_tag(pin_precision))
        await self._db.delete_points(collection, expression)

    # -- the fence -------------------------------------------------------------------------------------------

    async def capture(self, collection: str) -> Fence:
        """The epoch a batch is accepted under: read before the batch is embedded, so before anything is known about it."""
        try:
            return await self._pins.epochs.capture(collection)
        except Exception as exc:
            raise PrecisionUnreadable(f"could not read the epoch of '{collection}': {type(exc).__name__}") from exc

    async def _require_fence(self, fence: Fence) -> None:
        try:
            moved = await self._pins.epochs.moved(fence)
        except Exception as exc:
            raise PrecisionUnreadable(f"could not read the epoch of '{fence.collection}': {type(exc).__name__}") from exc
        if moved:
            raise FenceMoved(f"collection '{fence.collection}' was reset while this batch was being embedded; it is embedded again")

    async def sweep_dead(self, collection: str, org: str | None) -> None:
        """Delete the points written under retired tokens, and mark a token swept once no writer of it can still be writing.

        Never raises: a sweep that cannot run is repeated at the end of the next record.
        """
        try:
            dead = await self._pins.epochs.dead(collection)
            if not dead:
                return
            org = self.scope(org)
            must: dict = {POINT_EPOCH: [d.token for d in dead]}
            if org:
                must[_ORG_FIELD] = org
            await self._db.delete_points(collection, await self._db.filter_collection(must=must))
            window = sweep_window_seconds()
            for item in dead:
                if self._pins.epochs.expired(item, window):
                    await self._pins.epochs.mark_swept(collection, item.token)
        except Exception:
            self._logger.warning("could not sweep the points of retired epochs of '%s'", collection, exc_info=True)

    # -- the pin ---------------------------------------------------------------------------------------------

    async def standing(self, collection: str, org: str | None, *, fresh: bool = False, derive: bool = True) -> Standing:
        org = self.scope(org)
        try:
            pin = await self._pins.get(collection, org, fresh=fresh)
            if pin is not None:
                return Standing(pin.precision, "pin")
            if not derive:
                return Standing(None, "none")
            return await self.derive(collection, org)
        except PrecisionUnreadable:
            raise
        except Exception as exc:
            if _is_timeout(exc):
                raise PrecisionDerivationTimeout(f"counting the points of '{collection}' took too long") from exc
            raise PrecisionUnreadable(f"could not read the precision of '{collection}': {type(exc).__name__}") from exc

    async def standing_for_read(self, collection: str, org: str | None) -> Standing:
        """What the read-time check learns about an index: a derivation that timed out is "no derivation", never legacy."""
        org = self.scope(org)
        try:
            fence = await self.capture(collection)
            standing = await self.standing(collection, org)
            if standing.source == "derived" and standing.precision:
                standing = await self._restored(collection, org, standing, fence)
            return standing
        except PrecisionDerivationTimeout:
            self._logger.warning("The precision of '%s' could not be derived in time; the read-time check does not use it", collection)
            return Standing(None, "unknown")
        except FenceMoved:  # the index was reset while it was being read: what was derived is gone, and a read claims nothing
            return Standing(None, "unknown")

    async def restore(self, collection: str, org: str | None, precision: str, fence: Fence | None = None) -> Pin | None:
        """Write a pin derived from tags, from facts read now: never from cached ones.

        The pin key is read again and the tag is counted again immediately before the write; the pin is created
        only if it is still absent and the count is non-zero. The creation is a fenced claim: a reset since the
        fence was taken (the points counted are gone) makes it raise ``FenceMoved`` and creates nothing. Returns the
        pin that stands afterwards, or None when there is none.
        """
        org = self.scope(org)
        fence = fence or await self.capture(collection)
        current = await self._pins.get(collection, org, fresh=True)
        if current is not None:
            return current
        if await self._count(collection, org, precision) == 0:
            return None
        return await self._claim(collection, org, precision, fence, derived=True, counted=True)

    async def _restored(self, collection: str, org: str | None, standing: Standing, fence: Fence | None = None) -> Standing:
        """The standing after a derived one is restored as a pin: the pin as it stands now, not the derivation.

        A pin written or replaced since the derivation was read (another writer's, a replacement of a stale one)
        is what the next write must be judged against.
        """
        try:
            current = await self.restore(collection, org, standing.precision or "", fence)
        except PrecisionFlip:
            raise
        except Exception:
            self._logger.warning("could not restore the precision pin of '%s'", collection, exc_info=True)
            return standing
        return Standing(current.precision, "pin") if current is not None else standing

    async def _claim(
        self, collection: str, org: str | None, precision: str, fence: Fence, *, derived: bool, counted: bool = False,
    ) -> Pin:
        """The one place a pin is created.

        The epoch is checked before and after. Unless the caller has just counted the points (a restore) or the
        index is known to have had no reset or release (``counted``), the points are made visible and counted
        first, and a point of another precision refuses the claim: create-if-absent then decides between
        concurrent claimants, so no pin is made on a count that could not see a writer's points.
        """
        org = self.scope(org)
        await self._require_fence(fence)
        if not counted:
            await self._db.make_searchable(collection)
            found = await self.derive(collection, org)
            if found.precision not in (None, precision):
                raise PrecisionFlip(
                    f"collection '{collection}' holds {found.precision} vectors and the embedding server now produces {precision}; "
                    f"set {setting_for(found.precision)} and restart the embedding server, {CLEANUP_HINT}",
                )
        try:
            pin, created = await self._pins._try_create(collection, precision, org, derived=derived)
        except Exception as exc:
            raise PrecisionUnreadable(f"could not claim the precision of '{collection}': {type(exc).__name__}") from exc
        if not created:
            return pin
        try:
            moved = await self._pins.epochs.moved(fence)
        except Exception:
            moved = True
        if moved:
            await delete_config_confirmed(self._pins._config, self._pins.key(collection, org))
            raise FenceMoved(f"collection '{collection}' was reset while this batch was being embedded; it is embedded again")
        await self.sweep_dead(collection, org)
        return pin

    async def release_stale_derived(self, collection: str, org: str | None) -> bool:
        """Delete a derived pin whose collection has no points left (exact count after the index is searchable)."""
        org = self.scope(org)
        pin = await self._pins.get(collection, org, fresh=True)
        if pin is None or not pin.derived:
            return False
        await self._db.make_searchable(collection)
        if await self._points_count(collection) > 0:
            return False
        await self._pins.release(collection, org)
        return True

    async def confirm_after_write(self, collection: str, org: str | None, precision: str, fence: Fence) -> None:
        """Raise ``PrecisionFlip`` unless, once a batch is written, the epoch is the batch's and the pin is the batch's precision.

        The check in front of the write cannot see a reset, or a pin released and claimed again, between it and
        the write. This never claims or restores a pin from the points the batch wrote. The caller removes what it wrote.
        """
        org = self.scope(org)
        try:
            current, moved = await asyncio.gather(
                self._pins.get(collection, org, fresh=True), self._pins.epochs.moved(fence),
            )
        except Exception as exc:
            raise PrecisionUnreadable(f"could not confirm the precision of '{collection}': {type(exc).__name__}") from exc
        if moved or current is None:
            raise FenceMoved(f"collection '{collection}' was reset while a batch was being written; it is embedded again")
        if current.precision != precision:
            raise PrecisionFlip(
                f"collection '{collection}' holds {current.precision} vectors and a batch of {precision} was written to it; "
                f"set {setting_for(current.precision)} and restart the embedding server, {CLEANUP_HINT}",
            )

    async def count_of(self, collection: str, org: str | None, precision: str) -> int:
        """How many points carry this precision's tag in the index."""
        return await self._count(collection, self.scope(org), precision)

    async def delete_written(
        self, collection: str, org: str | None, precision: str, fence: Fence, extra: dict | None = None,
    ) -> None:
        """Remove what a rejected batch wrote: its precision, its epoch, and the record or ids in *extra*; nothing of another writer's."""
        must: dict = {TAG_FIELD: [precision], POINT_EPOCH: [fence.token], **(extra or {})}
        org = self.scope(org)
        if org:
            must[_ORG_FIELD] = org
        await self._db.delete_points(collection, await self._db.filter_collection(must=must))

    # -- replacing a pin left by a crash -------------------------------------------------------------------

    async def replace_stale(
        self, collection: str, precision: str, observed_empty_at: float, org: str | None = None,
        skew: float = STALE_PIN_SKEW_SECONDS,
    ) -> bool:
        """Remove a pin left by a crash, if it predates *observed_empty_at* by more than *skew* and the index is empty.

        No pin of the new precision is installed: the pin is released, the points are made visible and counted,
        and a pin is made again (through the fenced claim) only from points that exist. A writer of the old
        precision either wrote before its own confirmation, which precedes the release and so the count, or
        finds no pin at its confirmation and removes what it wrote. Returns True when the stale pin is gone and
        nothing is in the index.
        """
        org = self.scope(org)
        fence = await self.capture(collection)
        pin = await self._pins.get(collection, org, fresh=True)
        if pin is None or pin.precision == precision:
            return False
        if pin.pinned_at / 1000.0 >= observed_empty_at - skew:
            return False
        if await self._pins.get(collection, org, fresh=True) != pin:  # changed under us: leave it
            return False
        await self._pins.release(collection, org)
        await self.sweep_dead(collection, org)
        await self._db.make_searchable(collection)
        found = await self.derive(collection, org)
        if found.precision is None:
            return True
        restored = await self._claim(collection, org, found.precision, fence, derived=True, counted=True)
        if restored.precision != found.precision:
            raise PrecisionFlip(
                f"collection '{collection}' holds {found.precision} vectors but a pin of {restored.precision} was made meanwhile",
            )
        return False

    async def replace_stale_for_writer(self, collection: str, model: str | None, observed_at: float, org: str | None = None) -> bool:
        """The creator-site form of :meth:`replace_stale`: the writer's precision is what this process last saw the model serve.

        Nothing is replaced without a witness, or under a fallback (a pin is never claimed from a fallback), and a
        store that cannot be read leaves the pin alone: the claim in front of the first write then decides.
        """
        witness = get_precision_witness().latest(model) if model else None
        if witness is None or witness.fallback:
            return False
        try:
            return await self.replace_stale(collection, witness.precision, observed_at, org)
        except Exception:
            self._logger.warning("could not check for a stale precision pin of '%s'", collection, exc_info=True)
            return False

    async def _replaced_if_empty(self, collection: str, org: str | None, precision: str, standing: Standing) -> Standing:
        """A pin of the other precision over an index that holds no point is a leftover: replace it, as a creator would.

        The check in front of a write would otherwise refuse the batch that is to clear the leftover, on every
        retry. The pin is replaced only under the rules of ``replace_stale``; an index with any point keeps its pin.
        """
        try:
            if (await self.derive(collection, org)).precision is not None:
                return standing
            if not await self.replace_stale(collection, precision, self._pins.now(), org):
                return standing
            current = await self._pins.get(collection, org, fresh=True)
        except PrecisionFlip:
            raise
        except Exception:  # unreadable facts are not an empty index: the pin stands and the batch is refused
            self._logger.warning("could not check whether '%s' is empty under a stale pin", collection, exc_info=True)
            return standing
        return Standing(current.precision, "pin") if current is not None else Standing(None, "none")

    # -- the check -------------------------------------------------------------------------------------------

    async def verify_batch(
        self, collection: str, org: str | None, precision: str, *, fallback: bool, intent: str | None = None,
        claim: bool = True, derive: bool = True, fence: Fence | None = None,
    ) -> Pin | None:
        """Raise ``PrecisionFlip`` unless a batch of this precision may be written to the collection.

        Claims the pin when the collection is empty and the writer is not under a fallback. *fence* is the epoch
        the batch was accepted under (``capture``, before it was embedded); a claim is made only under it.
        """
        org = self.scope(org)
        if claim and fence is None:
            fence = await self.capture(collection)
        # The pin is read from the store on every batch, never from this process's cache: another process may have
        # released it and claimed it again since, and a cached answer would let this batch land beside the other
        # precision's points.
        standing = await self.standing(collection, org, fresh=True, derive=False)
        if standing.precision is None and derive:
            # On a store with one space per organisation the derivation is a scan. A writer that serves fp32 on
            # purpose (no fallback) claims without it while no pin of the index was ever released and no reset
            # is inside its window: int8 weights did not exist before the pins did, so such an index cannot hold
            # them. Any other writer derives (an int8 writer is probed, and refused when the probe times out).
            skip = (
                claim and not fallback and precision == FP32 and self._per_org_spaces() and await self._quiet(collection, org)
            )
            if not skip:
                # The points are made visible first: a count taken before a writer's points can be seen would
                # let a claim be made over an index that is not empty.
                if claim:
                    await self._db.make_searchable(collection)
                standing = await self.standing(collection, org, fresh=True)
        if standing.precision is None:
            if not claim:
                return None
            if fallback:
                raise PrecisionFlip(
                    f"the embedding server fell back to {precision} where {intent or INT8} was asked for; "
                    f"nothing is written to '{collection}' until it serves what was asked",
                )
            pin = await self._claim(collection, org, precision, fence, derived=False, counted=True)
            standing = Standing(pin.precision, "pin")
        elif standing.source == "derived":
            standing = await self._restored(collection, org, standing, fence)
        if standing.source == "pin" and standing.precision != precision and not fallback:
            standing = await self._replaced_if_empty(collection, org, precision, standing)
            if standing.precision is None:
                pin = await self._claim(collection, org, precision, fence, derived=False)
                standing = Standing(pin.precision, "pin")
        self._judged(collection, standing, precision, fallback=fallback)
        return None

    async def _quiet(self, collection: str, org: str | None) -> bool:
        try:
            return await self._pins.epochs.quiet(collection, org)
        except Exception:
            return False

    @staticmethod
    def _judged(collection: str, standing: Standing, precision: str, *, fallback: bool) -> None:
        if standing.precision != precision:
            raise PrecisionFlip(
                f"collection '{collection}' holds {standing.precision} vectors and the embedding server now produces {precision}; "
                f"set {setting_for(standing.precision)} and restart the embedding server, {CLEANUP_HINT}",
            )
        if fallback and standing.precision != FP32:
            raise PrecisionFlip(f"the embedding server fell back to {precision}; '{collection}' holds {standing.precision}")
