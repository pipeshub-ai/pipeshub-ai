"""One sync-point contract every graph store meets: written fields merge into the stored point, and a field written
as None is removed, so it reads back absent.

Neo4j's ``SET sp += $data`` removes a property set to null, and PostgreSQL drops a top-level JSON null; ArangoDB's
UPDATE keeps one unless told ``keepNull: false``. A reader then sees ``{"cursor": None}`` on one store and no
``cursor`` on the others (``data.get("cursor", default)`` and ``"cursor" in data`` differ). Used by the Neo4j and
ArangoDB cases in ``test_sync_point_rekey_real_backends.py`` and the PostgreSQL case in ``postgres/test_records.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

SYNC_POINTS = "syncPoints"


async def assert_a_null_clears_a_sync_point_field(provider: IGraphDBProvider, key: str) -> None:
    """Call inside the org's context; ``key`` must not exist yet."""
    await provider.upsert_sync_point(key, {"cursor": "c1", "page": None, "n": 1, "keep": "k"}, SYNC_POINTS)
    got = await provider.get_sync_point(key, SYNC_POINTS)
    assert got["cursor"] == "c1" and got["n"] == 1, got
    assert "page" not in got, f"a first write stored a null: {got}"

    await provider.upsert_sync_point(key, {"cursor": None, "n": 2, "token": "t"}, SYNC_POINTS)
    got = await provider.get_sync_point(key, SYNC_POINTS)
    assert "cursor" not in got, f"clearing a field left a null: {got}"
    assert got["n"] == 2 and got["token"] == "t", got
    assert got.get("keep") == "k", f"a field the update did not name was lost: {got}"

    # The compare-and-set: an expected None matches the cleared field, and a None in the data clears another.
    assert await provider.update_sync_point_if(key, {"cursor": None, "n": 2}, {"token": None, "n": 3}, SYNC_POINTS)
    got = await provider.get_sync_point(key, SYNC_POINTS)
    assert "token" not in got and "cursor" not in got, f"the compare-and-set left a null: {got}"
    assert got["n"] == 3, got
    assert got.get("keep") == "k", f"a field the compare-and-set did not name was lost: {got}"
