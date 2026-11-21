"""Row text a record's stored version already holds, offered to table enrichment on an update.

The LLM words the same table row differently from one call to the next (a 30-row table described twice kept the text of none of
its rows), and a row's block id follows its text. So an edit that sent a table to the model again gave every one of its rows a new
id, and the diff re-embedded all of them. On an update the stored version's rows are offered here by a digest of the table's header
row and the row's cells: a row whose digest is found keeps its text, and only the others are described.

A delivery offers the rows (``offer_previous_rows``); they are read from the stored record only when a table first asks
(``previous_row_texts``), once; the delivery withdraws them when it ends (``end_previous_rows``).
"""

from __future__ import annotations

import asyncio
import hashlib
from contextvars import ContextVar
from typing import TYPE_CHECKING, Optional

from app.models.blocks import BlockType

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from logging import Logger

    from app.models.blocks import BlocksContainer

#: Where a table row block keeps its digest. Left out of the block's content hashes (``without_row_digest``).
ROW_DIGEST_KEY = "row_digest"
# Changing it makes every stored digest a miss: the next update of each table describes its rows again.
_DIGEST_VERSION = b"table-row-v1"


def row_digest(header: Optional[Sequence[str]], cells: Sequence[str]) -> str:
    """The digest of one data row: the table's header row (``None`` for a table without one) and the row's cells, in order."""
    sha = hashlib.sha256(_DIGEST_VERSION)
    sha.update(b"\x00" if header is None else b"\x01")
    for cell in header or ():
        sha.update(cell.encode("utf-8", "surrogatepass"))
        sha.update(b"\x1f")
    sha.update(b"\x1e")
    for cell in cells:
        sha.update(cell.encode("utf-8", "surrogatepass"))
        sha.update(b"\x1f")
    return sha.hexdigest()[:32]


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


def row_texts_of(container: Optional[BlocksContainer]) -> dict[str, str]:
    """Digest to text, for every table row of *container* that carries a digest and a text."""
    texts: dict[str, str] = {}
    for block in container.blocks if container is not None else ():
        data = block.data
        if block.type != BlockType.TABLE_ROW or not isinstance(data, dict):
            continue
        digest, text = data.get(ROW_DIGEST_KEY), data.get("row_natural_language_text")
        if isinstance(digest, str) and isinstance(text, str) and text.strip():
            texts.setdefault(digest, text)
    return texts


class PreviousRows:
    """The stored version's row texts of one record, read on the first ask. A read that fails gives none, and the tables are
    described as on a first index."""

    def __init__(self, load: Callable[[], Awaitable[Optional[BlocksContainer]]], logger: Logger, record_id: str) -> None:
        self._load = load
        self._logger = logger
        self._record_id = record_id
        self._texts: Optional[dict[str, str]] = None
        self._lock = asyncio.Lock()

    async def texts(self) -> dict[str, str]:
        async with self._lock:
            if self._texts is None:
                try:
                    self._texts = row_texts_of(await self._load())
                except Exception as exc:
                    self._logger.warning(
                        "Record %s: the stored version's table rows could not be read (%s); its tables are described again",
                        self._record_id, type(exc).__name__,
                    )
                    self._texts = {}
            return self._texts


_offered: ContextVar[Optional[PreviousRows]] = ContextVar("previous_table_rows", default=None)


def offer_previous_rows(rows: PreviousRows) -> None:
    _offered.set(rows)


def end_previous_rows() -> None:
    # Set back rather than reset by token, as end_attempt: an async generator closed from another context cannot reset.
    _offered.set(None)


async def previous_row_texts() -> Optional[dict[str, str]]:
    """The row texts the delivery offered (read now if not yet), or ``None`` when it offered none."""
    rows = _offered.get()
    return None if rows is None else await rows.texts()
