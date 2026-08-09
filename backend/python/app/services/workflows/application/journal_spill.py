"""`SpillingExecutionJournal` -- keeps oversized step results out of the hot
journal.

A journal entry's `result_ref.inline` is whatever the step returned. A step
that returns a page of search hits or a document body puts all of it in Redis,
where it stays for the journal's whole TTL, multiplied by every step of every
concurrent run. One workflow looping over a few hundred documents is enough to
make the journal the largest thing in the instance.

`ResultRef` already models the alternative -- `inline` OR `artifact` -- but
nothing ever wrote the `artifact` side. This decorator does: payloads over a
threshold go to an `IJournalPayloadStore` and only the reference stays hot.
Rehydration happens on the way back out, so `SdkContext` and every other
reader still just sees `result_ref.inline` and needs no changes.

Written as a decorator over `IExecutionJournal` rather than folded into
`RedisExecutionJournal` so the spill policy is one testable thing and any
future journal backend inherits it.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from app.services.workflows.domain.models import JournalEntry, ResultRef

if TYPE_CHECKING:
    from app.services.workflows.interface.journal import IExecutionJournal
    from app.services.workflows.interface.payload_store import IJournalPayloadStore

__all__ = ["SpillingExecutionJournal", "DEFAULT_SPILL_THRESHOLD_BYTES"]

logger = logging.getLogger(__name__)

DEFAULT_SPILL_THRESHOLD_BYTES = 32 * 1024
"""Big enough that ordinary step results -- ids, flags, small dicts -- never
pay for a round trip to the object store; small enough that a run cannot hold
tens of megabytes of Redis. Tuned for count, not size: the cost that matters
is one spilled payload per large step across all live runs."""


class SpillingExecutionJournal:
    """`IExecutionJournal` that offloads large results to a payload store."""

    def __init__(
        self,
        inner: "IExecutionJournal",
        payload_store: "IJournalPayloadStore",
        *,
        threshold_bytes: int = DEFAULT_SPILL_THRESHOLD_BYTES,
    ) -> None:
        self._inner = inner
        self._payloads = payload_store
        self._threshold = threshold_bytes

    async def append(self, entry: JournalEntry) -> None:
        await self._inner.append(await self._spill(entry))

    async def lookup(self, run_id: str, step_key: str) -> JournalEntry | None:
        entry = await self._inner.lookup(run_id, step_key)
        return None if entry is None else await self._rehydrate(entry)

    async def load(self, run_id: str) -> list[JournalEntry]:
        entries = await self._inner.load(run_id)
        return [await self._rehydrate(entry) for entry in entries]

    async def touch(self, run_id: str) -> str | None:
        # Spilled payloads live in the artifact registry, which does not
        # expire them, so only the inner journal has a clock to restart.
        return await self._inner.touch(run_id)

    async def compact(self, run_id: str, upto_seq: int) -> None:
        await self._inner.compact(run_id, upto_seq)

    async def _spill(self, entry: JournalEntry) -> JournalEntry:
        ref = entry.result_ref
        if ref is None or ref.inline is None or ref.artifact is not None:
            return entry

        try:
            encoded = json.dumps(ref.inline).encode()
        except (TypeError, ValueError):
            # Not JSON-encodable, so it cannot be spilled and re-read
            # faithfully. `SdkContext._make_entry` already coerces results
            # through json, so reaching here means a caller built the entry
            # itself; leave it alone rather than corrupt it.
            return entry

        if len(encoded) <= self._threshold:
            return entry

        try:
            artifact = await self._payloads.put(
                run_id=entry.run_id, step_key=entry.step_key, payload=encoded,
            )
        except Exception:
            # A spill failure must not fail the step: the result is still
            # correct, it is just larger than we would like in Redis.
            logger.exception(
                "journal: failed to spill %d bytes for run=%s step=%s, keeping inline",
                len(encoded), entry.run_id, entry.step_key,
            )
            return entry

        logger.info(
            "journal: spilled %d bytes to artifact=%s for run=%s step=%s",
            len(encoded), artifact.artifact_id, entry.run_id, entry.step_key,
        )
        return entry.model_copy(update={"result_ref": ResultRef(artifact=artifact)})

    async def _rehydrate(self, entry: JournalEntry) -> JournalEntry:
        ref = entry.result_ref
        if ref is None or ref.artifact is None or ref.inline is not None:
            return entry

        try:
            raw = await self._payloads.get(ref.artifact)
        except Exception:
            logger.exception(
                "journal: failed to read spilled payload artifact=%s for run=%s step=%s",
                ref.artifact.artifact_id, entry.run_id, entry.step_key,
            )
            raw = None

        if raw is None:
            # Replay will see a step with no result. That is the same shape as
            # a step that legitimately returned None, so it is logged loudly:
            # a workflow that branches on this value would take a different
            # path than it did originally.
            logger.error(
                "journal: spilled payload artifact=%s is gone; run=%s step=%s will "
                "replay as None",
                ref.artifact.artifact_id, entry.run_id, entry.step_key,
            )
            return entry

        try:
            inline: Any = json.loads(raw.decode())
        except (UnicodeDecodeError, ValueError):
            logger.exception(
                "journal: spilled payload artifact=%s is not decodable for run=%s step=%s",
                ref.artifact.artifact_id, entry.run_id, entry.step_key,
            )
            return entry

        return entry.model_copy(
            update={"result_ref": ResultRef(inline=inline, artifact=ref.artifact)},
        )
