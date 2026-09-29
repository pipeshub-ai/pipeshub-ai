"""Every record method on ``IGraphDBProvider`` has decided what to do with the trash.

A new query that reads records and forgets ``isDeleted`` is how a deleted record
comes back into search or a listing. This fails as soon as such a method is
added to the interface without an entry in
``tests/support/record_visibility_registry.py``, which forces the decision.
"""

from __future__ import annotations

import inspect
import re

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.common.record_visibility import RecordVisibility
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from tests.support.record_visibility_registry import REGISTRY, Rule

PROVIDERS = {"arango": ArangoHTTPProvider, "neo4j": Neo4jProvider}


def _interface_methods() -> dict[str, inspect.Signature]:
    return {
        name: inspect.signature(member)
        for name, member in inspect.getmembers(IGraphDBProvider, inspect.isfunction)
        if not name.startswith("__")
    }


def _is_record_method(name: str, signature: inspect.Signature) -> bool:
    """Named for records, or returns one. Record groups are a different entity."""
    returns = str(signature.return_annotation)
    names_records = "record" in name.replace("record_group", "")
    returns_records = re.search(r"\bRecord\b|FileRecord", returns) is not None
    return names_records or returns_records


def test_every_record_method_is_classified() -> None:
    methods = _interface_methods()
    record_methods = {n for n, sig in methods.items() if _is_record_method(n, sig)}
    missing = sorted(record_methods - REGISTRY.keys())
    assert not missing, (
        "Record methods on IGraphDBProvider with no trash rule. Add each to "
        f"tests/support/record_visibility_registry.py: {missing}"
    )


def test_the_detector_is_not_vacuous() -> None:
    methods = _interface_methods()
    detected = {n for n, sig in methods.items() if _is_record_method(n, sig)}
    assert len(detected) >= 50, sorted(detected)
    assert {"get_record_by_id", "get_file_record_by_id", "check_record_access_with_details"} <= detected
    assert "get_record_group_by_id" not in detected


def test_registry_names_real_methods() -> None:
    stale = sorted(set(REGISTRY) - set(_interface_methods()))
    assert not stale, f"Registry entries for methods the interface no longer has: {stale}"


def test_every_trash_inclusive_read_says_why() -> None:
    unexplained = sorted(n for n, (rule, why) in REGISTRY.items() if rule is Rule.ALL and not why)
    assert not unexplained


PARAM_METHODS = sorted(n for n, (rule, _) in REGISTRY.items() if rule is Rule.PARAM)


@pytest.mark.parametrize("name", PARAM_METHODS)
@pytest.mark.parametrize("owner", ["interface", *PROVIDERS])
def test_param_methods_default_to_live(name, owner) -> None:
    cls = IGraphDBProvider if owner == "interface" else PROVIDERS[owner]
    params = inspect.signature(getattr(cls, name)).parameters
    assert "visibility" in params, f"{owner}.{name} takes no visibility"
    assert params["visibility"].default is RecordVisibility.LIVE


@pytest.mark.parametrize("name", sorted(n for n, (rule, _) in REGISTRY.items() if rule is not Rule.PARAM))
@pytest.mark.parametrize("owner", ["interface", *PROVIDERS])
def test_other_methods_take_no_visibility(name, owner) -> None:
    """A method that grew a visibility parameter belongs in PARAM, where the default is checked."""
    cls = IGraphDBProvider if owner == "interface" else PROVIDERS[owner]
    assert "visibility" not in inspect.signature(getattr(cls, name)).parameters
