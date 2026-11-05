"""The collection of an edge's other end is taken from its known collection label, not from the position of a label in `labels()`."""

import pytest

from app.services.graph_db.neo4j.neo4j_provider import collection_of_labels


@pytest.mark.parametrize("labels", [["KhInherits", "RecordGroup"], ["RecordGroup", "KhInherits"], ["RecordGroup"]])
def test_a_record_group_is_a_record_group_whatever_other_labels_it_has_and_wherever_they_stand(labels: list[str]) -> None:
    assert collection_of_labels(labels) == "recordGroups"


def test_a_record_and_a_record_group_with_one_id_stay_two_ends() -> None:
    assert collection_of_labels(["KhInherits", "Record"]) != collection_of_labels(["KhInherits", "RecordGroup"])
    assert collection_of_labels(["Record", "KhInherits"]) != collection_of_labels(["RecordGroup", "KhInherits"])


def test_no_known_label_gives_an_empty_collection() -> None:
    assert collection_of_labels(["Other"]) == "" and collection_of_labels(None) == ""
