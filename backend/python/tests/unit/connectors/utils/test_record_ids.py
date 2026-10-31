from uuid import UUID

from app.connectors.utils.record_ids import new_record_id


def test_every_writer_of_one_child_gets_the_same_id() -> None:
    assert new_record_id("conn-1", "att-9") == new_record_id("conn-1", "att-9")
    UUID(new_record_id("conn-1", "att-9"))


def test_the_id_does_not_change_between_releases() -> None:
    # During a rollout an old and a new service can both create the same child.
    assert new_record_id("conn-1", "att-9") == "73a789de-2cc3-56aa-aa1b-7f909543fb05"


def test_two_children_of_one_connector_get_two_ids() -> None:
    assert new_record_id("conn-1", "att-9") != new_record_id("conn-1", "att-10")


def test_the_same_source_id_in_two_connectors_gets_two_ids() -> None:
    assert new_record_id("conn-1", "att-9") != new_record_id("conn-2", "att-9")


def test_joining_the_two_parts_cannot_make_two_children_collide() -> None:
    assert new_record_id("conn-1", "2att") != new_record_id("conn-12", "att")
