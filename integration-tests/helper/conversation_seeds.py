"""Seed queries for conversations that exist only as test fixtures."""

from __future__ import annotations


def seed_query(token: str) -> str:
    """Query for a conversation a test needs only in order to have an id to act on.

    Phrased so a knowledge-enabled agent has nothing worth searching for: a query the
    model can answer outright costs one LLM turn, while a KB-shaped one costs two (a
    retrieval tool call, then an answer carrying the retrieved blocks).

    ``token`` must stay in the returned string — the gateway titles conversations from
    ``query.slice(0, 100)``, and the listing/search tests match on that title.
    """
    return f"Reply with OK. token={token}"
