"""Nothing a locator stamps on a hit for use inside the process reaches a client: the serialisation boundary removes every internal key."""
from __future__ import annotations

import json

from app.api.routes.search import _retrieve_chunks
from app.modules.retrieval.content_locator import SingleHolderLocator
from app.modules.retrieval.hit_shape import (
    INTERNAL_HIT_KEYS,
    INTERNAL_PAYLOAD_KEYS,
    public_results,
)


class StampingLocator(SingleHolderLocator):
    internal_payload_keys = ("locatorNote",)


def _corpus(stamped: bool) -> dict:
    extra = dict.fromkeys((*INTERNAL_PAYLOAD_KEYS, "locatorNote"), "x") if stamped else {}
    hit = {
        "content": "text", "score": 0.9, "block_index": 3, "virtual_record_id": "v", "block_type": "text",
        "metadata": {"virtualRecordId": "v", "recordId": "r1", "blockIndex": 3, **extra},
    }
    if stamped:
        hit.update(dict.fromkeys(INTERNAL_HIT_KEYS, 1))
    return {"searchResults": [hit], "outsideDates": {"searchResults": [dict(hit)]}, "records": [{"_key": "r1"}]}


def _leaks(payload: object, banned: set[str]) -> set[str]:
    return {k for k in banned if f'"{k}"' in json.dumps(payload)}


BANNED = {*INTERNAL_PAYLOAD_KEYS, *INTERNAL_HIT_KEYS, "locatorNote"}


def passthrough(results: dict, locator: object = None) -> dict:
    return results


def test_the_default_locator_stamps_nothing_so_nothing_is_there_to_leak() -> None:
    assert _leaks(public_results(_corpus(False), SingleHolderLocator()), BANNED) == set()


def test_every_internal_key_a_locator_stamps_is_removed_at_the_boundary() -> None:
    out = public_results(_corpus(True), StampingLocator())

    assert _leaks(out, BANNED) == set()
    assert out["searchResults"][0]["content"] == "text"  # the rest of the hit is kept


def test_the_chunk_list_never_carries_an_internal_key() -> None:
    assert _leaks(_retrieve_chunks(_corpus(True), None), BANNED) == set()


def test_negative_control_a_boundary_that_returns_hits_unchanged_is_caught() -> None:
    assert _leaks(passthrough(_corpus(True), StampingLocator()), BANNED) != set()
