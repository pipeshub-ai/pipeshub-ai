"""``restore_items``: a restore clears the trash state, so a caller's value for it is not passed on to be written over the clearing."""
from app.services.graph_db.common.utils import TRASH_STATE_FIELDS, restore_items


def test_a_caller_cannot_set_the_trash_state_back_through_a_restore() -> None:
    stale = {"isDeleted": True, **dict.fromkeys(TRASH_STATE_FIELDS, "x")}
    items, _ = restore_items([{"id": "a", "set": {**stale, "indexingStatus": "NOT_STARTED", "md5Checksum": None}}], None)
    assert items == [{"id": "a", "set": {"indexingStatus": "NOT_STARTED", "md5Checksum": None}}]


def test_an_external_id_taken_back_is_still_set() -> None:
    items, reclaims = restore_items([{"id": "a", "reclaimExternalRecordId": "x-a"}], "c1")
    assert items == [{"id": "a", "set": {"externalRecordId": "x-a"}}] and reclaims == [{"id": "a", "ext": "x-a"}]
