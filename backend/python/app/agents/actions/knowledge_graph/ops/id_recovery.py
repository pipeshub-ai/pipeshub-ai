"""What to tell the model when a record id it passed does not resolve.

Models mistype UUIDs: the first groups come out right and the rest is
invented. A bare "not available" leaves them nowhere to go, so the reply also
lists the records this conversation already returned and names the closest
one when the match is clear.

Built only from `known_record_names` (see `remember_record_ids`), which holds
ids that permission-checked tools already showed this model. Nothing here
reads a store, so the reply cannot differ between an id that does not exist
and one this person may not read.
"""
from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

MAX_LISTED_RECORDS = 20
_MIN_SHARED_PREFIX = 8
_MAX_EDITS = 2
# Below this an id is a short label like "R3", where two edits reach almost any other label.
_MIN_COMPARABLE_LENGTH = 16
_MAX_NAME_CHARS = 80


def _bounded_edits(a: str, b: str, limit: int) -> int:
    """Levenshtein distance, or `limit + 1` once it is known to exceed `limit`."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        if min(current) > limit:
            return limit + 1
        previous = current
    return min(previous[-1], limit + 1)


def closest_record_id(requested: str, candidates: Iterable[str]) -> str | None:
    """The one candidate clearly meant by `requested`, or None.

    Clear means a long shared prefix (the mistyped-tail case) or at most two
    edits (a slip anywhere). A tie names nothing rather than guess.
    """
    wanted = requested.strip().lower()
    if len(wanted) < _MIN_COMPARABLE_LENGTH:
        return None
    best: str | None = None
    best_key: tuple[int, int] | None = None
    tied = False
    for candidate in candidates:
        have = candidate.lower()
        if have == wanted:
            continue
        prefix = len(os.path.commonprefix([wanted, have]))
        edits = _bounded_edits(wanted, have, _MAX_EDITS)
        if prefix < _MIN_SHARED_PREFIX and edits > _MAX_EDITS:
            continue
        key = (-edits, prefix)
        if best_key is None or key > best_key:
            best, best_key, tied = candidate, key, False
        elif key == best_key:
            tied = True
    return None if tied else best


def _label(record_id: str, names: Mapping[str, str], shortener: Any) -> str:  # noqa: ANN401
    shown_id = shortener.shorten_if_known(record_id) if shortener is not None else record_id
    name = re.sub(r"\s+", " ", names.get(record_id) or "").strip()
    if len(name) > _MAX_NAME_CHARS:
        name = name[: _MAX_NAME_CHARS - 1] + "…"
    return f"{shown_id} ({name})" if name else shown_id


def unresolved_id_hint(
    unresolved: list[str],
    *,
    requested: Iterable[str],
    known_record_names: Any,  # noqa: ANN401
    shortener: Any = None,  # noqa: ANN401
) -> str:
    """Text to append for ids that resolved to no readable record, or "".

    Ids requested in this same call are left out of the list: they were
    either just read or just failed.
    """
    if not unresolved or not isinstance(known_record_names, dict):
        return ""
    skip = set(requested)
    shown = [rid for rid in known_record_names if rid not in skip]
    if not shown:
        return ""

    def as_shown(rid: str) -> str:
        return shortener.shorten_if_known(rid) if shortener is not None else rid

    listed = shown[::-1][:MAX_LISTED_RECORDS]
    quoted = ", ".join(f"'{as_shown(rid)}'" for rid in unresolved)
    noun = "id" if len(unresolved) == 1 else "ids"
    lines = [
        "",
        "",
        f"No record you can read has the {noun} {quoted}. "
        "Records returned earlier in this conversation:",
    ]
    lines.extend(f"- {_label(rid, known_record_names, shortener)}" for rid in listed)

    for wanted in unresolved:
        match = closest_record_id(wanted, shown)
        if match is None:
            continue
        label = _label(match, known_record_names, shortener)
        if len(unresolved) == 1:
            lines.append(f"The closest to the id you used is {label}.")
        else:
            lines.append(f"The closest to '{as_shown(wanted)}' is {label}.")
    lines.append("Use an id from this list exactly as shown, or search again.")
    return "\n".join(lines)
