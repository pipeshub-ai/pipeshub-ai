"""Deterministic node keys: same input, same key; scoped by org and collection."""

import uuid

import pytest

from app.config.constants.arangodb import CollectionNames
from app.modules.entity_resolution.keys import taxonomy_node_key
from app.modules.transformers.entity_vectorstore import EntityVectorStore


def test_same_inputs_same_key() -> None:
    a = taxonomy_node_key("org-1", CollectionNames.TOPICS.value, "bug bash testing")
    b = taxonomy_node_key("org-1", CollectionNames.TOPICS.value, "bug bash testing")
    assert a == b
    uuid.UUID(a)  # valid uuid, valid Arango _key


def test_org_scopes_key() -> None:
    a = taxonomy_node_key("org-1", CollectionNames.TOPICS.value, "bug bash testing")
    b = taxonomy_node_key("org-2", CollectionNames.TOPICS.value, "bug bash testing")
    assert a != b


def test_collection_scopes_key() -> None:
    sub1 = taxonomy_node_key("org-1", CollectionNames.SUBCATEGORIES1.value, "contract")
    sub2 = taxonomy_node_key("org-1", CollectionNames.SUBCATEGORIES2.value, "contract")
    topic = taxonomy_node_key("org-1", CollectionNames.TOPICS.value, "contract")
    assert len({sub1, sub2, topic}) == 3


@pytest.mark.parametrize(
    ("org", "collection", "name"),
    [("", "topics", "x"), ("org", "", "x"), ("org", "topics", "")],
)
def test_missing_parts_rejected(org, collection, name) -> None:
    with pytest.raises(ValueError):
        taxonomy_node_key(org, collection, name)


def test_one_node_one_point() -> None:
    """The vector point id is derived from the node key, so one canonical
    node can only ever have one point."""
    key = taxonomy_node_key("org-1", CollectionNames.TOPICS.value, "bug bash testing")
    assert EntityVectorStore._point_id("org-1", "topic", key) == EntityVectorStore._point_id(
        "org-1", "topic", key
    )
