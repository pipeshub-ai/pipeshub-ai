"""Every field the product's edge schemas declare must be in the YAML the edge checks use.

`assert_graph_edges` validates each edge against a YAML schema that rejects any
field it does not name. #3115 started writing `isExternalUser` on every
user-app edge, and the user-property checks of GitLab, Jira, Linear, Notion and
GitHub Teams failed on both graph backends the next night. This compares the
two so the gap is named on the pull request instead.
"""

from __future__ import annotations

import pytest
from app.schema.arango import edges as product_edges

from validation.graph_edge_validator import (
    _ARANGO_SYSTEM_KEYS,
    _EDGE_COLLECTION_TO_YAML,
    _EDGE_SCHEMA_DIR,
)
from response_validator import load_yaml_schema

pytestmark = pytest.mark.unit

PRODUCT_SCHEMA = {
    "permission": product_edges.permissions_schema,
    "belongsTo": product_edges.belongs_to_schema,
    "inheritPermissions": product_edges.inherit_permissions_schema,
    "recordRelations": product_edges.record_relations_schema,
    "isOfType": product_edges.is_of_type_schema,
    "userAppRelation": product_edges.user_app_relation_schema,
    "entityRelations": product_edges.entity_relations_schema,
}


def test_every_checked_edge_collection_has_a_product_schema() -> None:
    assert set(PRODUCT_SCHEMA) == set(_EDGE_COLLECTION_TO_YAML)


@pytest.mark.parametrize("collection", sorted(_EDGE_COLLECTION_TO_YAML))
def test_the_yaml_names_every_field_the_product_declares(collection: str) -> None:
    declared = set(PRODUCT_SCHEMA[collection]["rule"]["properties"]) - _ARANGO_SYSTEM_KEYS
    yaml_schema = load_yaml_schema(_EDGE_SCHEMA_DIR / _EDGE_COLLECTION_TO_YAML[collection])
    missing = sorted(declared - set(yaml_schema.fields))
    assert not missing, (
        f"{_EDGE_COLLECTION_TO_YAML[collection]} lacks {missing}, which the product's "
        f"{collection} edges carry; every edge check on them would fail."
    )
