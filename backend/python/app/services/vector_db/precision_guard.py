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

from app.core.db.errors import DbTimeout
from app.services.messaging.precision_flip import (
    CLEANUP_HINT,
    PrecisionFlip,
    setting_for,
)
from app.services.vector_db.collection_pin import (
    FP32,
    IMAGE_TAG,
    INT8,
    STAMP_TAG,
    CollectionPinStore,
    Pin,
)

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

    async def _count(self, collection: str, org: str | None, tag: str) -> int:
        must: dict = {TAG_FIELD: [tag]}
        if org:
            must[_ORG_FIELD] = org
        expression = await self._db.filter_collection(must=must)
        return int(await self._db.count_points(collection, expression))

    async def tag_counts(self, collection: str, org: str | None) -> dict[str, int]:
        return {tag: await self._count(collection, org, tag) for tag in (INT8, FP32, IMAGE_TAG, STAMP_TAG)}

    async def _points_count(self, collection: str) -> int:
        info = await self._db.get_collection_info(collection)
        return int(info.points_count) if info.exists else 0

    def _per_org_spaces(self) -> bool:
        """Whether the store keeps one space per organisation (its counts need the org bound and are unindexed scans)."""
        try:
            return getattr(self._db.get_capabilities(), "spaces_per_org", False) is True
        except Exception:  # a store that does not say is not one
            return False

    async def derive(self, collection: str, org: str | None) -> Standing:
        """The precision of an index without a pin, from positive facts, and nothing written.

        On a store with one space per organisation the reads run for that org (bound for the block) under a
        statement timeout; running out of time raises ``PrecisionDerivationTimeout``: nothing was derived, and
        the caller decides what that means (refuse a claimant, or no derivation at read time).
        """
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
        if counts[INT8] and counts[FP32]:
            raise PrecisionUnreadable(f"collection '{collection}' holds points of both precisions")
        if counts[INT8]:
            return Standing(INT8, "derived")
        if counts[FP32]:
            return Standing(FP32, "derived")
        if await self._points_count(collection) > counts[IMAGE_TAG] + counts[STAMP_TAG]:
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

    # -- the pin ---------------------------------------------------------------------------------------------

    async def standing(self, collection: str, org: str | None, *, fresh: bool = False, derive: bool = True) -> Standing:
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
        try:
            standing = await self.standing(collection, org)
            if standing.source == "derived" and standing.precision:
                try:
                    await self.restore(collection, org, standing.precision)
                except Exception:
                    self._logger.warning("could not restore the precision pin of '%s'", collection, exc_info=True)
            return standing
        except PrecisionDerivationTimeout:
            self._logger.warning("The precision of '%s' could not be derived in time; the read-time check does not use it", collection)
            return Standing(None, "unknown")

    async def restore(self, collection: str, org: str | None, precision: str) -> None:
        """Write a pin derived from tags, from facts read now: never from cached ones.

        The pin key is read again and the tag is counted again immediately before the write; the pin is created
        only if it is still absent and the count is non-zero, so a pin released by a drop cannot be brought back
        by a cache that has not seen the drop.
        """
        if await self._pins.get(collection, org, fresh=True) is not None:
            return
        if await self._count(collection, org, precision) == 0:
            return
        await self._pins.claim(collection, precision, org, derived=True)

    async def release_stale_derived(self, collection: str, org: str | None) -> bool:
        """Delete a derived pin whose collection has no points left (exact count after the index is searchable)."""
        pin = await self._pins.get(collection, org, fresh=True)
        if pin is None or not pin.derived:
            return False
        await self._db.make_searchable(collection)
        if await self._points_count(collection) > 0:
            return False
        await self._pins.release(collection, org)
        return True

    # -- the check -------------------------------------------------------------------------------------------

    async def verify_batch(
        self, collection: str, org: str | None, precision: str, *, fallback: bool, intent: str | None = None,
        claim: bool = True, derive: bool = True,
    ) -> Pin | None:
        """Raise ``PrecisionFlip`` unless a batch of this precision may be written to the collection.

        Claims the pin when the collection is empty and the writer is not under a fallback.
        """
        # The pin is read from the store on every batch, never from this process's cache: another process may have
        # released it and claimed it again since, and a cached answer would let this batch land beside the other
        # precision's points. That is one key read per batch.
        # On a store with one space per organisation the derivation is a scan. A writer that serves fp32 on purpose
        # (no fallback) claims without it: int8 weights did not exist before this check did, so an unpinned index
        # of such a store cannot hold them. An int8 writer is probed, and refused when the probe times out.
        probe = derive and not (self._per_org_spaces() and precision == FP32 and not fallback)
        standing = await self.standing(collection, org, fresh=True, derive=probe)
        if standing.precision is None:
            if not claim:
                return None
            if fallback:
                raise PrecisionFlip(
                    f"the embedding server fell back to {precision} where {intent or INT8} was asked for; "
                    f"nothing is written to '{collection}' until it serves what was asked",
                )
            try:
                pin = await self._pins.claim(collection, precision, org)
            except Exception as exc:
                raise PrecisionUnreadable(f"could not claim the precision of '{collection}': {type(exc).__name__}") from exc
            standing = Standing(pin.precision, "pin")
        elif standing.source == "derived":
            try:
                await self.restore(collection, org, standing.precision)
            except Exception:
                self._logger.warning("could not restore the precision pin of '%s'", collection, exc_info=True)
        if standing.precision != precision:
            raise PrecisionFlip(
                f"collection '{collection}' holds {standing.precision} vectors and the embedding server now produces {precision}; "
                f"set {setting_for(standing.precision)} and restart the embedding server, {CLEANUP_HINT}",
            )
        if fallback and standing.precision != FP32:
            raise PrecisionFlip(f"the embedding server fell back to {precision}; '{collection}' holds {standing.precision}")
        return None
