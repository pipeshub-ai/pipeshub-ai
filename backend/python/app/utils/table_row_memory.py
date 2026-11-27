"""Row text a record's stored version already holds, offered to table enrichment on an update.

The LLM words the same table row differently from one call to the next (a 30-row table described twice kept the text of none of
its rows), and a row's block id follows its text. So an edit that sent a table to the model again gave every one of its rows a new
id, and the diff re-embedded all of them. On an update the stored version's rows are offered here by a digest of the table's header
row and the row's cells: a row whose digest is found keeps its text, and only the others are described. A table takes text only
from the stored table that shares the most rows with it, so two tables with an equal row each keep their own wording.

A delivery offers the rows (``offer_previous_rows``); they are read from the stored record only when a table first asks
(``previous_row_texts``), once; the delivery withdraws them when it ends (``end_previous_rows``). With ``USE_PARSING_SERVICE`` the
parse request carries them (``encode_stored_tables``), and the parsing service offers them for that parse.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextvars import ContextVar
from typing import TYPE_CHECKING, Optional

from app.models.blocks import BlockType

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from logging import Logger

    from app.models.blocks import BlocksContainer

#: Where a table row block keeps its digest. Left out of the block's content hashes (``hashed_block_data``).
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


def with_row_digest(data: dict, digests: Sequence[Optional[str]], i: int) -> dict:
    """*data* of the *i*-th data row, with that row's digest when it has one (a described row) and without one otherwise (a plain
    row, described again next time)."""
    digest = digests[i] if i < len(digests) else None
    if digest:
        data[ROW_DIGEST_KEY] = digest
    else:
        data.pop(ROW_DIGEST_KEY, None)
    return data


#: Where a table row sits, not what it says. Left out of its content hash, so an inserted or deleted row leaves every other row's
#: id alone; equal rows are still told apart, by occurrence (``BlockIdentity``), and a kept row's point is told its new place.
_ROW_POSITION_KEYS = frozenset({"row_number", "row_end_number"})


def hashed_block_data(data: object) -> object:
    """*data* as the content hashes read it. A table row's leaves out its digest (so a row that gains one keeps its id) and its
    position (``row_number``, Excel's ``row_end_number``); any other block's is *data* itself."""
    if isinstance(data, dict) and (ROW_DIGEST_KEY in data or "row_natural_language_text" in data):
        return {k: v for k, v in data.items() if k != ROW_DIGEST_KEY and k not in _ROW_POSITION_KEYS}
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

    @classmethod
    def already_read(cls, tables: list[StoredTable], logger: Logger, record_id: str) -> PreviousRows:
        """Rows read elsewhere and handed over: the parsing service is sent them with the file it parses."""
        rows = cls(_nothing_to_load, logger, record_id)
        rows._texts = tables
        return rows


async def _nothing_to_load() -> None:
    return None


#: The most stored-row bytes a parse request carries to the parsing service. A larger set is not sent, and its tables are
#: described as on a first index. Only described rows carry a digest (``MAX_TABLE_ROWS_FOR_LLM`` per record), so it is rarely
#: reached.
MAX_SENT_ROW_BYTES = 8 * 1024 * 1024


def encode_stored_tables(tables: list[StoredTable]) -> Optional[bytes]:
    """*tables* as a parse request carries them, or ``None`` when they are over ``MAX_SENT_ROW_BYTES``."""
    raw = json.dumps(tables, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return raw if len(raw) <= MAX_SENT_ROW_BYTES else None


def decode_stored_tables(raw: bytes) -> Optional[list[StoredTable]]:
    """The tables ``encode_stored_tables`` wrote, or ``None`` for anything else (too large, not JSON, another shape)."""
    if len(raw) > MAX_SENT_ROW_BYTES:
        return None
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError):  # RecursionError: nesting deeper than the decoder allows, well under the size cap
        return None
    if not isinstance(value, list) or not all(_is_stored_table(table) for table in value):
        return None
    return value


def _is_stored_table(value: object) -> bool:
    return isinstance(value, dict) and all(
        isinstance(texts, list) and all(isinstance(text, str) for text in texts) for texts in value.values()
    )


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
