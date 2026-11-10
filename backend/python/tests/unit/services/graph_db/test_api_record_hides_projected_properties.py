"""Projected property values and their controls are stored on a record but are not part of the record API."""

import pytest

from app.services.graph_db.common.utils import INTERNAL_RECORD_FIELDS, api_record

pytestmark = pytest.mark.unit


def test_the_arango_form_and_the_controls_are_internal() -> None:
    assert {"recordProps", "propsSet", "propsGen", "propsSrc", "propsVer"} <= INTERNAL_RECORD_FIELDS


def test_api_record_drops_the_neo4j_flat_values_and_keeps_everything_else() -> None:
    stored = {"id": "r1", "recordName": "n", "p_status": "OPEN", "p_labels": ["a"], "propsVer": 2, "recordProps": {"a": 1},
              "pushOrigin": True, "priorityLevel": "p_high", "p_": "kept: not a projected key", "p_bad-name": "kept"}
    assert api_record(stored) == {
        "id": "r1", "recordName": "n", "priorityLevel": "p_high", "p_": "kept: not a projected key", "p_bad-name": "kept",
    }
