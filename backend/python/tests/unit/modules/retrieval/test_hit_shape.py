"""``hit_shape``: the two ids of a hit, and the one boundary where internal marks and local indices stop."""
from __future__ import annotations

from app.modules.retrieval.content_locator import SingleHolderLocator
from app.modules.retrieval.hit_shape import (
    INTERNAL_HIT_KEYS,
    carry_holder,
    content_vrid,
    holder_index_of,
    identity_vrid,
    public_hit,
    public_hits,
    public_results,
)


class _ShiftingLocator(SingleHolderLocator):
    """A test locator whose holders number their blocks in a range of their own and that stamps an extra payload key."""

    internal_payload_keys = ("holderNote",)

    def public_index(self, holder_index: int | None, local: int) -> int:
        return (holder_index or 0) * 1000 + local


def _flat(**over: object) -> dict:
    return {"virtual_record_id": "content", "identity_vrid": "record", "holder_index": 2, "block_index": 7,
            "metadata": {"virtualRecordId": "record", "contentVrid": "content", "blockAddress": 2007, "holderIndex": 2,
                         "holderNote": "x", "blockIndex": 7, "kept": True}, **over}


def test_the_default_locator_leaves_ids_and_indices_alone() -> None:
    hit = {"virtual_record_id": "v", "block_index": 3, "metadata": {"virtualRecordId": "v", "blockIndex": 3}}
    assert public_hit(hit, SingleHolderLocator()) == hit
    assert public_hit(hit) == hit
    assert content_vrid(hit) == identity_vrid(hit) == "v"


def test_content_and_identity_ids_are_told_apart_for_a_hit_held_elsewhere() -> None:
    hit = _flat()
    assert content_vrid(hit) == "content" and identity_vrid(hit) == "record" and holder_index_of(hit) == 2
    assert content_vrid({"metadata": {"contentVrid": "c", "virtualRecordId": "r"}}) == "c"
    assert identity_vrid({"metadata": {"virtualRecordId": "r"}, "virtual_record_id": "c"}) == "r"


def test_the_boundary_drops_every_internal_key_and_writes_the_clients_index() -> None:
    out = public_hit(_flat(), _ShiftingLocator())
    assert out["virtual_record_id"] == "record"
    assert out["block_index"] == 2007
    assert not set(INTERNAL_HIT_KEYS) & set(out)
    assert out["metadata"] == {"virtualRecordId": "record", "blockIndex": 2007, "kept": True}


def test_children_of_a_group_pass_the_boundary_too() -> None:
    group = {"block_index": 1, "content": ("summary", [_flat()])}
    out = public_hit(group, _ShiftingLocator())
    assert out["content"][1][0]["block_index"] == 2007 and "holder_index" not in out["content"][1][0]


def test_a_response_is_cleaned_wherever_it_holds_a_passage_and_record_maps_are_left_as_they_are() -> None:
    response = {
        "searchResults": [_flat()], "outsideDates": {"searchResults": [_flat()]},
        "records": {"record": {"identity_vrid": "keep"}}, "virtual_to_record_map": {"record": {"holder_index": 3}},
    }
    out = public_results(response, _ShiftingLocator())
    for hit in (out["searchResults"][0], out["outsideDates"]["searchResults"][0]):
        assert hit["block_index"] == 2007 and "identity_vrid" not in hit and "holderNote" not in hit["metadata"]
    assert out["records"] == response["records"] and out["virtual_to_record_map"] == response["virtual_to_record_map"]
    assert public_hits([_flat(), "x"], _ShiftingLocator())[1] == "x"


def test_a_neighbour_carries_its_anchors_marks() -> None:
    target: dict = {}
    assert carry_holder({"identity_vrid": "record", "holder_index": 2, "other": 1}, target) == {"identity_vrid": "record", "holder_index": 2}


def test_negative_control_a_boundary_that_returns_the_hit_unchanged_leaks() -> None:
    """The checkers of the boundary must be able to fail: a copy that skips ``public_hit`` still carries the marks."""
    leaked = dict(_flat())
    assert set(INTERNAL_HIT_KEYS) <= set(leaked) and "contentVrid" in leaked["metadata"]
