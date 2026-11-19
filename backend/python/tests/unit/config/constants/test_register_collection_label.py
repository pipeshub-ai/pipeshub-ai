"""``register_collection_label`` adds a collection's Neo4j label in both directions, once."""

from __future__ import annotations

import pytest

from app.config.constants import neo4j as labels


@pytest.fixture(autouse=True)
def _clean() -> None:
    labels._reset_registered_labels()
    yield
    labels._reset_registered_labels()


def test_both_maps_are_updated() -> None:
    labels.register_collection_label("things", "Thing")
    assert labels.collection_to_label("things") == "Thing"
    assert labels.label_to_collection("Thing") == "things"


def test_an_unregistered_collection_keeps_the_fallback_label() -> None:
    assert labels.collection_to_label("things") == "Things"


def test_registering_the_same_pair_again_is_a_no_op() -> None:
    labels.register_collection_label("things", "Thing")
    labels.register_collection_label("things", "Thing")
    assert labels.COLLECTION_TO_LABEL["things"] == "Thing"
    assert list(labels.LABEL_TO_COLLECTION).count("Thing") == 1


def test_a_different_label_for_a_known_collection_raises() -> None:
    labels.register_collection_label("things", "Thing")
    with pytest.raises(ValueError, match="already maps"):
        labels.register_collection_label("things", "Other")
    assert labels.collection_to_label("things") == "Thing"


def test_a_built_in_collection_cannot_be_relabelled() -> None:
    with pytest.raises(ValueError, match="already maps"):
        labels.register_collection_label("records", "Elsewhere")
    assert labels.collection_to_label("records") == "Record"


def test_registering_the_label_a_built_in_collection_has_changes_nothing() -> None:
    labels.register_collection_label("records", "Record")
    labels._reset_registered_labels()
    assert labels.collection_to_label("records") == "Record"
    assert labels.label_to_collection("Record") == "records"


def test_reset_removes_only_what_was_registered() -> None:
    before = dict(labels.COLLECTION_TO_LABEL)
    labels.register_collection_label("things", "Thing")
    labels._reset_registered_labels()
    assert labels.COLLECTION_TO_LABEL == before
    assert "Thing" not in labels.LABEL_TO_COLLECTION
