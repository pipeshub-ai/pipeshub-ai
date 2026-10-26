"""Which nodes a finished full sync unlinks, and which keep their links.

The decision both providers share; the database side runs in
tests/integration/graph_db/test_full_sync_unlink_real_backends.py.
"""

from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.common.full_sync_unlink import (
    split_unseen_by_window,
    unseen_node_keys,
)

RECORDS = CollectionNames.RECORDS.value
GROUPS = CollectionNames.RECORD_GROUPS.value
USER_GROUPS = CollectionNames.GROUPS.value
ROLES = CollectionNames.ROLES.value
WINDOW = 1_000


def _unseen() -> dict[str, list[str]]:
    collected = {
        "record_keys": ["seen", "old", "new", "undated", "in-old-thread", "in-new-thread"],
        "record_group_keys": ["channel", "old-thread", "new-thread", "old-channel"],
        "group_keys": ["team"],
        "role_keys": ["members"],
    }
    return unseen_node_keys(collected, {RECORDS: {"seen"}, GROUPS: {"channel"}})


_RECORD_FIELDS = {
    "old": {"sourceCreatedAtTimestamp": 10},
    "new": {"sourceCreatedAtTimestamp": 5_000},
    "undated": {"sourceCreatedAtTimestamp": None},
    "in-old-thread": {"sourceCreatedAtTimestamp": 5_000, "recordGroupId": "old-thread"},
    "in-new-thread": {"sourceCreatedAtTimestamp": 5_000, "recordGroupId": "new-thread"},
}
_GROUP_FIELDS = {
    "old-thread": {"sourceCreatedAtTimestamp": 10, "groupType": "SLACK_THREAD"},
    "new-thread": {"sourceCreatedAtTimestamp": 5_000, "groupType": "SLACK_THREAD"},
    "old-channel": {"sourceCreatedAtTimestamp": 10, "groupType": "SLACK_CHANNEL"},
}


def test_what_the_sync_saw_is_never_touched() -> None:
    unseen = _unseen()
    assert "seen" not in unseen[RECORDS] and "channel" not in unseen[GROUPS]


def test_without_a_window_everything_unseen_is_unlinked() -> None:
    unlink, kept = split_unseen_by_window(_unseen(), _RECORD_FIELDS, _GROUP_FIELDS, None, {"SLACK_THREAD"})
    assert kept == []
    assert set(unlink) == {
        f"{RECORDS}/old", f"{RECORDS}/new", f"{RECORDS}/undated",
        f"{RECORDS}/in-old-thread", f"{RECORDS}/in-new-thread",
        f"{GROUPS}/old-thread", f"{GROUPS}/new-thread", f"{GROUPS}/old-channel",
        f"{USER_GROUPS}/team", f"{ROLES}/members",
    }


def test_with_a_window_what_lies_before_it_keeps_its_links() -> None:
    unlink, kept = split_unseen_by_window(_unseen(), _RECORD_FIELDS, _GROUP_FIELDS, WINDOW, {"SLACK_THREAD"})
    assert set(kept) == {
        f"{RECORDS}/old",
        f"{GROUPS}/old-thread",
        # Inside the window, but read only through its thread, which is not.
        f"{RECORDS}/in-old-thread",
    }
    assert set(unlink) == {
        f"{RECORDS}/new",
        f"{RECORDS}/undated",  # no creation time: treated as inside, as before
        f"{RECORDS}/in-new-thread",
        f"{GROUPS}/new-thread",
        # Channels are always listed, whatever the window: an unseen one is gone.
        f"{GROUPS}/old-channel",
        f"{USER_GROUPS}/team",
        f"{ROLES}/members",
    }


def test_a_boolean_is_not_a_timestamp() -> None:
    unseen = {RECORDS: ["r"], GROUPS: [], USER_GROUPS: [], ROLES: []}
    unlink, kept = split_unseen_by_window(unseen, {"r": {"sourceCreatedAtTimestamp": True}}, {}, WINDOW, ())
    assert kept == [] and unlink == [f"{RECORDS}/r"]
