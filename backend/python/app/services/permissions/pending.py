"""`PendingScopes`: the records whose subtrees await a rewrite, kept on the connector's app document.

A structural change that reaches only the subtree of one record (a move, a broken or restored inheritance, a record's own rule or
first/last grant) registers that record as a *root*, in the transaction that bumps the connector's structure generation. From then on
every filter hides the points whose ``pathIds`` hold a pending root, and the container-stamped points of the connector are read with
the legacy filter. The value is one JSON string ``{root: generation}``: a compare-and-set on the whole string loses no registration,
and a root registered again after a pass read it stays.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

#: Past this many roots a change is read as one of the whole connector (a full pass), not as a list of subtrees.
MAX_PENDING_ROOTS = 200


class PendingOverflow(ValueError):
    """More roots than `MAX_PENDING_ROOTS`: the caller treats the change as one of the whole connector."""


class PendingUnreadable(ValueError):
    """The stored value is not a ``{root: generation}`` map. It is never read as "no roots": the connector is not current."""


def parse(raw: object) -> dict[str, int]:
    """Roots and the generation each was registered at. Absent (None or "") is no roots; anything else that does not parse raises
    `PendingUnreadable`."""
    if raw is None or raw == "":
        return {}
    try:
        if not isinstance(raw, str):
            raise TypeError
        return {str(k): int(v) for k, v in json.loads(raw).items()}
    except (ValueError, TypeError, AttributeError):
        raise PendingUnreadable(f"not a pending-roots value: {raw!r:.80}") from None


def dump(roots: Mapping[str, int]) -> str | None:
    return json.dumps(dict(sorted(roots.items())), separators=(",", ":")) if roots else None


def with_roots(raw: object, roots: Collection[str], generation: int) -> str:
    """`raw` with `roots` registered at `generation` (a root already there takes the newer generation)."""
    held = parse(raw)
    held.update(dict.fromkeys(roots, generation))
    if len(held) > MAX_PENDING_ROOTS:
        raise PendingOverflow(f"{len(held)} pending roots")
    return dump(held) or ""


def without_done(raw: object, done: Mapping[str, int]) -> str | None:
    """`raw` without the roots covered by a pass (root -> the generation the pass read the graph at): a root registered again since
    keeps its newer generation and stays."""
    held = parse(raw)
    return dump({r: g for r, g in held.items() if not (r in done and g <= done[r])})


def require_filter_terms() -> None:
    """Start-up check: roots may be registered only where the scope filter can hide them (`pending`) and stop reading the container
    stamps of their connector (`blocked`). Without those fields a registered root would hide nothing."""
    import dataclasses

    from app.services.vector_db.filters import ScopeFilter

    missing = {"pending", "blocked"} - {f.name for f in dataclasses.fields(ScopeFilter)}
    if missing:
        raise RuntimeError(f"ScopeFilter lacks the {sorted(missing)} field(s): the pending registry must not run without them")
