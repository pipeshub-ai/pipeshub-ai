"""Row text a record's stored version already holds, offered to table enrichment on an update.

The LLM words the same table row differently from one call to the next (a 30-row table described twice kept the text of none of
its rows), and a row's block id follows its text. So an edit that sent a table to the model again gave every one of its rows a new
id, and the diff re-embedded all of them. On an update the stored version's rows are offered here by a digest of the table's header
row and the row's cells: a row whose digest is found keeps its text, and only the others are described. A table takes text only
from the stored table that shares the most rows with it, so two tables with an equal row each keep their own wording.

A delivery offers the rows (``offer_previous_rows``); they are read from the stored record only when a table first asks
(``previous_row_texts``), once; the delivery withdraws them when it ends (``end_previous_rows``).
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
from collections import Counter
from contextvars import ContextVar
from typing import TYPE_CHECKING, Optional

from app.models.blocks import BlockType

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence
    from logging import Logger

    from app.models.blocks import BlocksContainer

#: Where a table row block keeps its digest. Left out of the block's content hashes (``without_row_digest``).
ROW_DIGEST_KEY = "row_digest"
# Changing it makes every stored digest a miss: the next update of each table describes its rows again.
_DIGEST_VERSION = b"table-row-v2"


def row_digest(header: Optional[Sequence[str]], cells: Sequence[str]) -> str:
    """The digest of one data row: the table's header row (``None`` for a table without one) and the row's cells, in order."""
    sha = hashlib.sha256(_DIGEST_VERSION)
    sha.update(b"\x00" if header is None else b"\x01")
    for part in (header or (), cells):
        # Counted and length-prefixed, so no cell's text (a separator character included) can move a boundary.
        sha.update(len(part).to_bytes(8, "big"))
        for cell in part:
            raw = cell.encode("utf-8", "surrogatepass")
            sha.update(len(raw).to_bytes(8, "big"))
            sha.update(raw)
    return sha.hexdigest()[:32]


def dict_row_digest(row: Mapping[object, object]) -> str:
    """The digest of a row given as one dict per row (CSV, Excel): its keys are the table's header (an Excel header can be a
    number or a date), its values the cells."""
    return row_digest([_cell_text(key) for key in row], [_cell_text(value) for value in row.values()])


def _cell_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime.date, datetime.time)):
        return value.isoformat()
    return str(value)


def with_row_digest(data: dict, digests: Sequence[Optional[str]], i: int) -> dict:
    """*data* of the *i*-th data row, with that row's digest when it has one (a described row) and without one otherwise (a plain
    row, described again next time)."""
    digest = digests[i] if i < len(digests) else None
    if digest:
        data[ROW_DIGEST_KEY] = digest
    else:
        data.pop(ROW_DIGEST_KEY, None)
    return data


def without_row_digest(data: object) -> object:
    """*data* as the content hashes read it: without the digest, so a row's id and fingerprint are what they were before rows
    carried one, and a row that gains it keeps its id."""
    if isinstance(data, dict) and ROW_DIGEST_KEY in data:
        return {k: v for k, v in data.items() if k != ROW_DIGEST_KEY}
    return data


#: One stored table's rows: digest to the texts of its rows with that digest, in row order (a repeated row has one each).
StoredTable = dict[str, list[str]]


def row_texts_of(container: Optional[BlocksContainer]) -> list[StoredTable]:
    """The table rows of *container* that carry a digest and a text, one entry per table."""
    tables: dict[Optional[int], StoredTable] = {}
    for block in container.blocks if container is not None else ():
        data = block.data
        if block.type != BlockType.TABLE_ROW or not isinstance(data, dict):
            continue
        digest, text = data.get(ROW_DIGEST_KEY), data.get("row_natural_language_text")
        if isinstance(digest, str) and isinstance(text, str) and text.strip():
            tables.setdefault(block.parent_index, {}).setdefault(digest, []).append(text)
    return list(tables.values())


def best_stored_table(tables: Sequence[StoredTable], candidates: Sequence[Sequence[str]]) -> Optional[StoredTable]:
    """The stored table that can give a table the most texts, *candidates* being its rows' digests under each header reading;
    ``None`` when none can, or when two tie (neither is surely this table). Only one stored table is used, so a row equal to one
    of another table never takes that table's wording."""
    best, best_shared, tied = None, 0, False
    for digests in candidates:
        counts = Counter(digests)
        for table in tables:
            # A repeated row counts as many times as both versions hold it, never more than the stored table can give.
            shared = sum(min(n, len(table.get(digest, ()))) for digest, n in counts.items())
            if shared > best_shared:
                best, best_shared, tied = table, shared, False
            elif shared and shared == best_shared and table is not best:
                tied = True
    return None if tied else best


def kept_texts(table: Optional[StoredTable], digests: Sequence[str]) -> list[Optional[str]]:
    """Each row's stored text in *table* (``None`` where it has none). A repeated row takes the stored texts of its repeats in
    order, one each; a repeat more than were stored has none."""
    left = {digest: iter(texts) for digest, texts in (table or {}).items()}
    return [next(left.get(digest, iter(())), None) for digest in digests]


class PreviousRows:
    """The stored version's row texts of one record, read on the first ask. A read that fails gives none, and the tables are
    described as on a first index."""

    def __init__(self, load: Callable[[], Awaitable[Optional[BlocksContainer]]], logger: Logger, record_id: str) -> None:
        self._load = load
        self._logger = logger
        self._record_id = record_id
        self._texts: Optional[list[StoredTable]] = None
        self._lock = asyncio.Lock()

    async def texts(self) -> list[StoredTable]:
        async with self._lock:
            if self._texts is None:
                try:
                    self._texts = row_texts_of(await self._load())
                except Exception as exc:
                    self._logger.warning(
                        "Record %s: the stored version's table rows could not be read (%s); its tables are described again",
                        self._record_id, type(exc).__name__,
                    )
                    self._texts = []
            return self._texts


_offered: ContextVar[Optional[PreviousRows]] = ContextVar("previous_table_rows", default=None)


def offer_previous_rows(rows: PreviousRows) -> None:
    _offered.set(rows)


def end_previous_rows() -> None:
    # Set back rather than reset by token, as end_attempt: an async generator closed from another context cannot reset.
    _offered.set(None)


async def previous_row_texts() -> Optional[list[StoredTable]]:
    """The row texts the delivery offered (read now if not yet), or ``None`` when it offered none."""
    rows = _offered.get()
    return None if rows is None else await rows.texts()
