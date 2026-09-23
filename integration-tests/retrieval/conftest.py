"""Fixtures for the search-quality scenarios.

The corpus is uploaded once per session and every test queries it. Each
document's virtual record id is captured at upload time, because that is what a
search hit carries — ``virtual_record_id`` on the hit is the only thing tying a
result back to the document it came from, so without the mapping a test can see
that *something* matched but not *what*.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import AsyncGenerator

import pytest_asyncio

from helper.clients.kb_client import KBClient
from retrieval.corpus import CORPUS

logger = logging.getLogger("retrieval-fixtures")

INDEXING_TIMEOUT = 420
POLL = 5


@dataclass(frozen=True)
class IndexedCorpus:
    """The uploaded corpus, and which document each virtual record id belongs to."""

    kb_id: str
    by_slug: dict[str, str]          # slug -> virtual record id
    by_virtual_id: dict[str, str]    # virtual record id -> slug

    def slug_of(self, virtual_record_id: str | None) -> str:
        """Which document a hit came from, or a marker if it is not ours.

        Other suites leave records in the same tenant, so a hit on something
        outside this corpus is expected and has to be distinguishable rather
        than silently counted as one of ours.
        """
        if not virtual_record_id:
            return "<no virtual id>"
        return self.by_virtual_id.get(virtual_record_id, "<not in corpus>")


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def indexed_corpus(
    pipeshub_client,
    vector_store,
    ai_models_configured,
) -> AsyncGenerator[IndexedCorpus, None]:
    """Upload the corpus and wait until every document is searchable.

    Depends on ``ai_models_configured`` because search needs an LLM: without one
    the endpoint returns 500 with "LLM configuration is missing", which would
    otherwise look like a retrieval failure rather than a missing credential.
    """
    kb_client = KBClient(pipeshub_client)
    kb = kb_client.create_kb(f"retrieval-{uuid.uuid4().hex[:8]}")
    kb_id = kb["id"]
    by_slug: dict[str, str] = {}

    try:
        for document in CORPUS:
            upload = kb_client.upload_file(
                kb_id, document.filename, document.body, mimetype="text/markdown"
            )
            assert upload["summary"]["failed"] == 0, (
                f"Upload of {document.filename} failed: {upload}"
            )
            record_id = upload["records"][0]["recordId"]
            virtual_id = await _wait_for_virtual_id(kb_client, record_id)
            by_slug[document.slug] = virtual_id
            logger.info("%s -> virtual record %s", document.slug, virtual_id)

        for slug, virtual_id in by_slug.items():
            await _wait_for_embeddings(vector_store, virtual_id, slug)

        yield IndexedCorpus(
            kb_id=kb_id,
            by_slug=by_slug,
            by_virtual_id={v: k for k, v in by_slug.items()},
        )
    finally:
        try:
            kb_client.delete_kb(kb_id)
        except Exception as exc:  # noqa: BLE001 - teardown must not mask a failure
            logger.warning("Could not delete knowledge base %s: %s", kb_id, exc)


async def _wait_for_virtual_id(kb_client: KBClient, record_id: str) -> str:
    deadline = asyncio.get_event_loop().time() + INDEXING_TIMEOUT
    while asyncio.get_event_loop().time() < deadline:
        payload = kb_client.get_record(record_id)
        virtual_id = (payload.get("record") or {}).get("virtualRecordId")
        if virtual_id:
            return str(virtual_id)
        await asyncio.sleep(POLL)
    raise AssertionError(
        f"Record {record_id} never got a virtualRecordId within "
        f"{INDEXING_TIMEOUT}s, so no search hit could be traced back to it."
    )


async def _wait_for_embeddings(vector_store, virtual_id: str, slug: str) -> None:
    """Fail rather than query an unindexed corpus.

    A search over documents that have not finished indexing returns nothing,
    and "nothing came back" would read as a ranking failure. This makes the
    real reason the one that gets reported.
    """
    deadline = asyncio.get_event_loop().time() + INDEXING_TIMEOUT
    while asyncio.get_event_loop().time() < deadline:
        if await vector_store.count_for_virtual_record(virtual_id):
            return
        await asyncio.sleep(POLL)
    raise AssertionError(
        f"Document {slug!r} (virtual {virtual_id}) produced no embeddings "
        f"within {INDEXING_TIMEOUT}s, so it cannot be found by any query."
    )
