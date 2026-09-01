"""Default :class:`FairnessKeyExtractor` implementations."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from app.services.messaging.config import StreamMessage
    from app.services.messaging.scheduling.interface import FairnessKey

__all__ = ["CompositeKeyExtractor"]

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
