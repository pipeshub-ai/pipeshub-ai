"""Ask MongoDB what it still holds.

MongoDB does not store records — those live in the graph. What it stores is the
*storage document*: the metadata describing where a record's bytes live, its
version history, and its soft-delete flag. That makes it the store where two of
the scenarios on the test list are actually answerable:

* "MongoDB document collection — record and metadata storage document deleted"
* "Must be marked for soft delete, then cleaned via the scheduled job"

The second one is why ``is_soft_deleted`` exists separately from
``document_exists``. Soft deletion and hard deletion look identical from the
graph's side and are completely different in MongoDB, and confusing the two is
how a record that was only ever flagged gets reported as cleaned up.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import MongoClient

from helper.config import MONGO_DB_NAME, MONGO_URI

logger = logging.getLogger("mongo-store-probe")

_DEFAULT_TIMEOUT = 120
_POLL_INTERVAL = 2.0

# The storage document collection. Named here rather than inline so the two
# places that would otherwise repeat the literal cannot drift apart.
DOCUMENTS = "documents"


class MongoStoreProbe:
    """Read-only questions about the storage documents MongoDB still holds."""

    def __init__(self, uri: str | None = None, db_name: str | None = None) -> None:
        self._uri = uri or MONGO_URI
        self._db_name = db_name or MONGO_DB_NAME
        self._client: MongoClient | None = None

    def _conn(self) -> MongoClient:
        if self._client is None:
            self._client = MongoClient(self._uri, serverSelectionTimeoutMS=10000)
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # ------------------------------------------------------------------ #
    # Reads — pymongo is synchronous, so each one is handed to a thread to
    # keep it off the event loop the async tests are running on.
    # ------------------------------------------------------------------ #

    def _documents(self) -> Any:
        return self._conn()[self._db_name][DOCUMENTS]

    async def find_document(self, document_id: str) -> dict[str, Any] | None:
        def _find() -> dict[str, Any] | None:
            return self._documents().find_one({"_id": {"$in": _id_forms(document_id)}})

        return await asyncio.to_thread(_find)

    async def count_documents_under_path(self, path_prefix: str) -> int:
        """Storage documents whose path starts with the given prefix.

        A record's documents sit under a path containing its virtual record id,
        so this is how a record's storage metadata is found without knowing
        each document id up front.
        """

        def _count() -> int:
            return self._documents().count_documents(
                {"documentPath": {"$regex": f"^{_escape(path_prefix)}"}}
            )

        return await asyncio.to_thread(_count)

    async def count_documents_for_org(self, org_id: str) -> int:
        def _count() -> int:
            return self._documents().count_documents(
                {"orgId": {"$in": _id_forms(org_id)}}
            )

        return await asyncio.to_thread(_count)

    async def is_soft_deleted(self, document_id: str) -> bool | None:
        """``True`` / ``False`` for a document that exists, ``None`` if it does not.

        Three states, not two — a caller checking soft deletion needs to tell
        "flagged" from "already gone", and a bare boolean cannot.
        """
        doc = await self.find_document(document_id)
        if doc is None:
            return None
        return bool(doc.get("isDeleted", False))

    # ------------------------------------------------------------------ #
    # Assertions
    # ------------------------------------------------------------------ #

    async def assert_document_gone(
        self, document_id: str, timeout: int = _DEFAULT_TIMEOUT
    ) -> None:
        deadline = asyncio.get_event_loop().time() + timeout
        doc = await self.find_document(document_id)
        while doc is not None and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(_POLL_INTERVAL)
            doc = await self.find_document(document_id)
        if doc is not None:
            state = "flagged as deleted" if doc.get("isDeleted") else "not even flagged"
            raise AssertionError(
                f"Storage document {document_id} still exists in MongoDB after "
                f"{timeout}s ({state}). Its blob may be unreachable while the "
                "metadata pointing at it survives."
            )

    async def assert_document_present(self, document_id: str) -> dict[str, Any]:
        doc = await self.find_document(document_id)
        assert doc is not None, (
            f"Storage document {document_id} is not in MongoDB before the "
            "delete. A cleanup test has to start from something."
        )
        return doc

    async def assert_soft_deleted(
        self, document_id: str, timeout: int = _DEFAULT_TIMEOUT
    ) -> None:
        """The document is still present, and flagged.

        Deliberately fails on a document that has already vanished: a hard
        delete where the list expects a soft one is a real difference, and
        treating "gone" as "well, it is at least deleted" would hide it.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        flagged = await self.is_soft_deleted(document_id)
        while flagged is False and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(_POLL_INTERVAL)
            flagged = await self.is_soft_deleted(document_id)

        if flagged is None:
            raise AssertionError(
                f"Storage document {document_id} was removed outright. The "
                "expected behaviour is a soft delete first, with the scheduled "
                "job clearing it later."
            )
        assert flagged, (
            f"Storage document {document_id} was never flagged as deleted "
            f"within {timeout}s. Nothing will clean it up, because the "
            "scheduled job only collects flagged documents."
        )

    async def assert_documents_under_path_gone(
        self, path_prefix: str, timeout: int = _DEFAULT_TIMEOUT
    ) -> None:
        deadline = asyncio.get_event_loop().time() + timeout
        remaining = await self.count_documents_under_path(path_prefix)
        while remaining > 0 and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(_POLL_INTERVAL)
            remaining = await self.count_documents_under_path(path_prefix)
        if remaining:
            raise AssertionError(
                f"{remaining} storage document(s) still exist under "
                f"{path_prefix!r} after {timeout}s."
            )


def _escape(value: str) -> str:
    """Quote a path so regex metacharacters in an id cannot alter the match."""
    import re

    return re.escape(value)


def _id_forms(value: str) -> list[Any]:
    """Both spellings of an identifier, because the two are not interchangeable.

    ``orgId`` and ``_id`` are stored as BSON ObjectIds, not strings, and a
    string query against an ObjectId column matches nothing and raises nothing.
    Every count would come back zero, and a cleanup assertion reading zero
    concludes the store is clean — a false pass, in the exact direction that
    hides the bug these probes exist to find.

    Not every id is an ObjectId (record and virtual ids are UUIDs), so both
    forms are offered and Mongo takes whichever fits.
    """
    forms: list[Any] = [value]
    try:
        forms.append(ObjectId(value))
    except (InvalidId, TypeError):
        pass
    return forms
