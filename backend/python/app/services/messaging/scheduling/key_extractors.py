"""Default :class:`FairnessKeyExtractor` implementations."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from app.services.messaging.config import StreamMessage
    from app.services.messaging.scheduling.interface import (
        FairnessKey,
        FairnessKeyExtractor,
    )

__all__ = ["CompositeKeyExtractor", "TieredKeyExtractor"]

DEFAULT_MISSING = "__default__"


class CompositeKeyExtractor:
    """Builds a hierarchical fairness key by reading one payload field per
    level, outermost first.

    The default ``("orgId", "connectorId")`` is what makes fairness work on a
    single-org install: every user in an org shares its ``orgId``, so keying
    on org alone yields one queue and no fairness. ``connectorId`` -- the
    connector *instance* id, one per individually-configured connector, and
    the knowledge-base id for uploads -- is what separates one user's sync
    from another's.

    Levels are kept separate rather than joined into one string so the
    scheduler can be fair *between* orgs as well as within them; a joined
    key would hand an org with fifty connectors fifty shares.

    A level whose field is missing or empty collapses to a shared sentinel,
    so such messages compete with each other rather than being dropped or
    crashing the read loop.
    """

    def __init__(
        self,
        fields: "Sequence[str]" = ("orgId", "connectorId"),
        default: str = DEFAULT_MISSING,
    ) -> None:
        if not fields:
            raise ValueError("CompositeKeyExtractor needs at least one field")
        self._fields = tuple(fields)
        self._default = default

    @property
    def fields(self) -> tuple[str, ...]:
        return self._fields

    def extract(self, message: "StreamMessage") -> "FairnessKey":
        payload = message.payload
        return tuple(
            self._default
            if (value := payload.get(field)) is None or value == ""
            else str(value)
            for field in self._fields
        )


class TieredKeyExtractor:
    """Appends a document-tier level below whatever ``inner`` extracts.

    The tier is not a payload field: it is derived from ``extension`` and
    ``mimeType`` *and* from the governor's ceilings (a host with no light
    budget routes every record to heavy), so it is supplied as a callable by
    the consumer that knows both. Pairs with ``FairSchedulerConfig.tier_level``,
    which adds the matching depth to the scheduler.
    """

    def __init__(
        self,
        inner: "FairnessKeyExtractor",
        tier_of: "Callable[[StreamMessage], str]",
    ) -> None:
        self._inner = inner
        self._tier_of = tier_of

    @property
    def inner(self) -> "FairnessKeyExtractor":
        return self._inner

    def extract(self, message: "StreamMessage") -> "FairnessKey":
        return (*self._inner.extract(message), self._tier_of(message))
