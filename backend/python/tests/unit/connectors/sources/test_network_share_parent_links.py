"""Record ids and parent links on a share walked with saved folders and folder patterns together.

On a random case-insensitive share, with saved folders typed in any case or
Unicode form, extra patterns, and values that name the same folder or one
inside another:

- every written record's parent is a record the walk wrote or kept;
- the ids under each saved folder are the ones a walk of that folder alone
  writes, except that, when a pattern's walk starts above it, a folder above
  it that another saved value also passes through takes the first saved spelling;
- no folder is listed twice.
"""

from __future__ import annotations

import asyncio
import logging
import random
import unicodedata

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from test_network_share_walker import (
    SHARE,
    InsensitiveShare,
    _filters,
    _folder_filter,
    _key,
    _share_tree,
    _walk,
    _written,
)

NAMES = ("a", "b", "Reports", "café", "x y", "node_modules", "quotes")
PATTERNS = ("*/quotes", "**/b", "a/*", "*", "**/x y", "Reports/**", "*/*/a", "q?otes")


def _random_share(rng: random.Random) -> tuple[InsensitiveShare, list[str]]:
    paths: list[str] = []
    folders: list[str] = []

    def fill(prefix: str, depth: int) -> None:
        for name in rng.sample(NAMES, rng.randint(1, 3)):
            here = f"{prefix}/{name}" if prefix else name
            if depth < 3 and rng.random() < 0.6:
                folders.append(here)
                fill(here, depth + 1)
                if rng.random() < 0.3:
                    paths.append(f"{here}/")
            else:
                paths.append(f"{here}.txt")

    fill("", 0)
    return InsensitiveShare(tree=_share_tree(*paths)), folders


def _typed(rng: random.Random, folder: str) -> str:
    parts = []
    for name in folder.split("/"):
        roll = rng.random()
        if roll < 0.3:
            parts.append(name.swapcase())
        elif roll < 0.4:
            parts.append(unicodedata.normalize("NFD", name))
        else:
            parts.append(name)
    return "/".join(parts)


def _values(rng: random.Random, folders: list[str]) -> list[str]:
    values: list[str] = []
    for _ in range(rng.randint(1, 3)):
        if not folders:
            break
        folder = rng.choice(folders)
        values.append(_typed(rng, folder))
        roll = rng.random()
        if roll < 0.2:
            values.append(_typed(rng, folder))  # the same folder again, spelled another way
        elif roll < 0.4 and "/" in folder:
            values.append(_typed(rng, folder.rsplit("/", 1)[0]))  # the folder above it
    values += rng.sample(PATTERNS, rng.randint(0, 2))
    rng.shuffle(values)
    return values


def _n_parts(value: str) -> list[str]:
    return [p for p in unicodedata.normalize("NFC", value.replace("\\", "/")).casefold().split("/") if p]


def _is_pattern(value: str) -> bool:
    return "*" in value or "?" in value


def _walked_alone(literal: str, literals: list[str]) -> bool:
    """Whether ``literal`` is walked from its own spelling: not inside another value, nor equal to an earlier one."""
    mine = _n_parts(literal)
    for index, other in enumerate(literals):
        theirs = _n_parts(other)
        if other is literal:
            continue
        inside = len(theirs) < len(mine) and mine[: len(theirs)] == theirs
        earlier_twin = theirs == mine and index < literals.index(literal)
        if inside or earlier_twin:
            return False
    return True


def _fixed_start(pattern: str) -> list[str]:
    fixed: list[str] = []
    for part in _n_parts(pattern):
        if _is_pattern(part):
            break
        fixed.append(part)
    return fixed


def _reached_from_above(literal: str, patterns: list[str]) -> bool:
    """Whether a pattern's walk starts above ``literal``, so the walk reaches it as an entry
    rather than starting at it."""
    mine = _n_parts(literal)
    return any(len(f) < len(mine) and mine[: len(f)] == f for f in map(_fixed_start, patterns))


def _respell(record_id: str, literal: str, literals: list[str]) -> str:
    """``record_id`` from the walk of ``literal`` alone, with each folder down to it spelled
    as the first saved value through that folder spells it."""
    path = record_id[len(SHARE) + 1:].split("/")
    mine = _n_parts(literal)
    for depth in range(min(len(mine), len(path))):
        for other in literals:
            theirs = _n_parts(other)
            if theirs[: depth + 1] == mine[: depth + 1]:
                path[depth] = [p for p in other.replace("\\", "/").split("/") if p][depth]
                break
    return unicodedata.normalize("NFC", f"{SHARE}/{'/'.join(path)}")


async def _check(seed: int) -> None:
    rng = random.Random(seed)
    share, folders = _random_share(rng)
    values = _values(rng, folders)
    result, upserts, _moves = await _walk(share, sync_filters=_filters(_folder_filter(*values)))
    written = _written(upserts)

    for record in written:
        assert record.parent_external_record_id in result.seen | {None}, (values, record.external_record_id)

    listed = [_key(path) for _share, path in share.list_calls]
    assert len(listed) == len(set(listed)), (values, share.list_calls)

    literals = [v for v in values if not _is_pattern(v)]
    patterns = [v for v in values if _is_pattern(v)]
    pairs = {(r.external_record_id, r.parent_external_record_id) for r in written}
    for literal in literals:
        if not _walked_alone(literal, literals):
            continue
        share.list_calls.clear()
        _alone_result, alone, _moves = await _walk(share, sync_filters=_filters(_folder_filter(literal)))
        # Started at, a saved folder keeps its own spelling; reached from a pattern's start
        # above it, the folders down to it take the first saved spelling.
        def spell(record_id: str | None, literal: str = literal) -> str | None:
            if record_id is None or not _reached_from_above(literal, patterns):
                return record_id
            return _respell(record_id, literal, literals)

        expected = {(spell(r.external_record_id), spell(r.parent_external_record_id)) for r in _written(alone)}
        assert expected <= pairs, (values, literal, sorted(expected - pairs))


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.integers(min_value=0, max_value=2**32))
def test_every_parent_resolves_and_saved_folders_keep_their_ids(seed: int) -> None:
    logging.disable(logging.CRITICAL)
    try:
        asyncio.run(_check(seed))
    finally:
        logging.disable(logging.NOTSET)
