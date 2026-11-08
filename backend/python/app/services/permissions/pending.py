"""`PendingScopes`: the records whose subtrees are being rewritten, kept on the connector's app document.

A structural change that reaches only the subtree of one record (a move, a broken or restored inheritance, a record
filed under another group) registers that record as a *root*, in the transaction that writes the change, together with the
connector's new scope generation. From then on every filter hides the points whose ``pathIds`` hold a pending root, and the
container-stamped points of the connector are read with the legacy filter (a point with no path cannot be hidden by one), until
the rewrite job has restamped the subtree and removes the root. The value is one JSON string ``{root: generation}``: a
compare-and-set on the whole string loses no registration, and a root registered again after the job read it stays.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

#: Past this many roots a connector is read as changed as a whole (a full pass), not as a list of subtrees.
MAX_PENDING_ROOTS = 200


class PendingOverflow(ValueError):
    """More roots than `MAX_PENDING_ROOTS`: the caller treats the change as one of the whole connector."""


def parse(raw: object) -> dict[str, int]:
    """Roots and the generation each was registered at. A value that does not parse reads as no roots: the generation, which is
    bumped in the same write, already keeps the connector's stamps out of use."""
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        return {str(k): int(v) for k, v in json.loads(raw).items()}
    except (ValueError, TypeError, AttributeError):
        return {}


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
    """`raw` without the roots rewritten from the state `done` read (root -> the generation it had then): a root registered again
    since keeps its newer generation and stays."""
    held = parse(raw)
    return dump({r: g for r, g in held.items() if not (r in done and g <= done[r])})
