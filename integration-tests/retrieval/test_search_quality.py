"""Does search return the right documents?

Ten integration tests cover search today and every one of them is about search
*history* — listing it, sorting it, sharing a saved search, archiving one. None
asks what a query returns. On a product whose purpose is search, the result a
person actually sees is untested.

These tests query a corpus with known right answers and check which document
came back. They assert **rank**, not score: scores move with the model and the
corpus, so a threshold would be a number nobody could justify and everybody
would eventually raise. "The right document came first" is a claim that stays
true and stays meaningful.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from retrieval.corpus import UNIQUE_TOKEN
from retrieval.ranking import describe, ranked_slugs

logger = logging.getLogger("retrieval-quality")

pytestmark = [pytest.mark.integration, pytest.mark.retrieval]


def _hits(response: Any) -> list[dict[str, Any]]:
    assert response.status_code == 200, (
        f"Search failed with HTTP {response.status_code}: {response.text[:400]}"
    )
    body = response.json()
    search_response = body.get("searchResponse") or {}
    return search_response.get("searchResults") or []


class TestFindingAnExactTerm:
    """The test list's 'Keyword search'."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_a_unique_term_finds_its_document_first(
        self, search_client, indexed_corpus
    ) -> None:
        """A token that exists in exactly one document has one right answer.

        This is the weakest possible claim about a search engine, which is why
        it is worth pinning: if it ever fails, retrieval is broken outright
        rather than merely ranking oddly.
        """
        hits = _hits(search_client.search(UNIQUE_TOKEN, limit=10))
        assert hits, (
            f"No results at all for {UNIQUE_TOKEN!r}, which appears verbatim in "
            "one indexed document."
        )

        ranked = ranked_slugs(hits, indexed_corpus.slug_of)
        assert ranked[0] == "servers", (
            f"Searching for {UNIQUE_TOKEN!r} ranked {ranked[0]!r} first. That "
            "token appears in the servers document and nowhere else.\n"
            f"{describe(hits, indexed_corpus.slug_of)}"
        )


class TestFindingSomethingDescribedDifferently:
    """The test list's 'Semantic search'."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_a_query_sharing_no_words_with_its_answer_still_finds_it(
        self, search_client, indexed_corpus
    ) -> None:
        """The query and the document mean the same and share no content words.

        "paying someone back for money they spent out of pocket" against a
        document about reimbursement and expense claims. Literal matching
        cannot answer this, so a pass means semantic retrieval is genuinely
        working rather than string matching getting lucky.
        """
        query = "paying someone back for money they spent out of pocket"
        hits = _hits(search_client.search(query, limit=10))
        assert hits, f"No results at all for {query!r}."

        ranked = ranked_slugs(hits, indexed_corpus.slug_of)
        assert "expenses" in ranked[:2], (
            f"The expenses document was not in the top two for {query!r}; the "
            f"order was {ranked[:3]}. The query describes reimbursement without "
            "using any of the document's words, which is exactly what semantic "
            f"search is for.\n{describe(hits, indexed_corpus.slug_of)}"
        )

    @pytest.mark.asyncio(loop_scope="session")
    async def test_an_unrelated_document_does_not_outrank_the_answer(
        self, search_client, indexed_corpus
    ) -> None:
        """Precision, not just recall.

        Returning the right document somewhere in a long list is not the same
        as returning it above documents that have nothing to do with the query.
        """
        query = "how long does the transmitter keep reporting the bird's position"
        hits = _hits(search_client.search(query, limit=10))
        ranked = ranked_slugs(hits, indexed_corpus.slug_of)

        assert ranked and ranked[0] == "birds", (
            f"A question about bird transmitters ranked {ranked[0]!r} first, "
            f"with the order {ranked[:3]}.\n{describe(hits, indexed_corpus.slug_of)}"
        )


class TestWhatComesBack:
    """A hit has to be usable, not merely present."""

    @pytest.mark.asyncio(loop_scope="session")
    async def test_hits_carry_content_and_a_traceable_record(
        self, search_client, indexed_corpus
    ) -> None:
        """A result nobody can trace back to a document is not an answer.

        ``virtual_record_id`` is what ties a hit to its source; without it the
        interface cannot show where an answer came from, and neither can a
        citation.
        """
        hits = _hits(search_client.search(UNIQUE_TOKEN, limit=5))
        assert hits, "No results to inspect."

        without_content = [h for h in hits if not h.get("content")]
        assert not without_content, (
            f"{len(without_content)} of {len(hits)} hits had no content.\n"
            f"{describe(hits, indexed_corpus.slug_of)}"
        )

        untraceable = [h for h in hits if not h.get("virtual_record_id")]
        assert not untraceable, (
            f"{len(untraceable)} of {len(hits)} hits had no virtual_record_id, "
            "so nothing can say which document they came from.\n"
            f"{describe(hits, indexed_corpus.slug_of)}"
        )
