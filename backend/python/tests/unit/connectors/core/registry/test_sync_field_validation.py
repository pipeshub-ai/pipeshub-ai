"""Sync custom field metadata in the connector schema, and the save routes refusing values outside it."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

# The connector router and factory import each other through a cycle; load the router first.
from app.connectors.api import router
from app.connectors.core.registry.connector_builder import ConnectorConfigBuilder
from app.connectors.core.registry.sync_field_validation import (
    is_shown,
    sync_custom_field_problems,
)
from app.connectors.core.registry.types import CustomField
from app.connectors.sources.web.connector import WebConnector


def _web_sync_schema() -> dict:
    return WebConnector._connector_metadata["config"]["sync"]


def _web_fields() -> list[dict]:
    return _web_sync_schema()["customFields"]


def test_builder_emits_metadata_only_when_a_connector_sets_it() -> None:
    builder = ConnectorConfigBuilder()
    builder.with_sync_field_group("crawl", "What to crawl")
    builder.add_sync_custom_field(CustomField(name="plain", display_name="Plain", field_type="TEXT"))
    builder.add_sync_custom_field(CustomField(
        name="pages", display_name="Pages", field_type="NUMBER", min_value=1, max_value=50, unit="pages",
        group="crawl", show_when={"mode": ["deep"]}, option_labels={},
    ))
    sync = builder.config["sync"]

    plain, pages = sync["customFields"]
    assert set(plain) == {"name", "displayName", "description", "fieldType", "required", "defaultValue",
                          "validation", "isSecret", "nonEditable"}
    assert pages["validation"] == {"min": 1, "max": 50}
    assert (pages["unit"], pages["group"], pages["showWhen"]) == ("pages", "crawl", {"mode": ["deep"]})
    assert "optionLabels" not in pages
    assert sync["customFieldGroups"] == [
        {"name": "crawl", "displayName": "What to crawl", "description": "", "collapsed": False}
    ]


def test_every_web_setting_sits_in_a_declared_section_in_section_order() -> None:
    schema = _web_sync_schema()
    groups = [g["name"] for g in schema["customFieldGroups"]]
    assert groups == ["website", "crawl", "skip", "advanced"]
    assert [g["collapsed"] for g in schema["customFieldGroups"]] == [False, False, False, True]
    order = [groups.index(f["group"]) for f in schema["customFields"]]
    assert order == sorted(order)


def test_web_numbers_carry_a_range_not_a_text_length() -> None:
    by_name = {f["name"]: f for f in _web_fields()}
    assert by_name["max_pages"]["validation"] == {"min": 1, "max": 10_000}
    assert by_name["depth"]["validation"] == {"min": 1, "max": 10}
    assert by_name["max_size_mb"]["validation"] == {"min": 1, "max": 100}
    assert set(by_name["type"]["optionLabels"]) == set(by_name["type"]["options"])
    assert set(by_name["discovery"]["optionLabels"]) == set(by_name["discovery"]["options"])


def test_web_filters_offer_only_what_a_crawl_can_store() -> None:
    filters = WebConnector._connector_metadata["config"]["filters"]
    indexing = [f["name"] for f in filters["indexing"]["schema"]["fields"]]
    assert "attachments" not in indexing
    (extensions,) = [f for f in filters["sync"]["schema"]["fields"] if f["name"] == "file_extensions"]
    ids = {o["id"] if isinstance(o, dict) else o.id for o in extensions["options"]}
    assert {"html", "pdf", "docx", "png"} <= ids
    assert not ids & {"sql_table", "tsx", "py"}


@pytest.mark.parametrize(("submitted", "message"), [
    ({"max_pages": 50_000}, "Maximum pages: Enter a whole number from 1 to 10,000."),
    ({"max_pages": "0"}, "Maximum pages: Enter a whole number from 1 to 10,000."),
    ({"max_pages": "12.5"}, "Maximum pages: Enter a whole number from 1 to 10,000."),
    ({"max_pages": True}, "Maximum pages: Enter a whole number from 1 to 10,000."),
    ({"max_pages": "--5"}, "Maximum pages: Enter a whole number from 1 to 10,000."),
    ({"max_pages": "²"}, "Maximum pages: Enter a whole number from 1 to 10,000."),
    ({"discovery": "everything"}, "Find pages using: Choose one of the listed options."),
    ({"url": "  "}, "Start URL: This field is required."),
])
def test_a_value_outside_its_rules_is_named_with_the_reason(submitted: dict, message: str) -> None:
    assert sync_custom_field_problems(_web_fields(), submitted, {"type": "recursive"}) == [message]


def test_valid_values_and_values_not_submitted_pass() -> None:
    fields = _web_fields()
    assert sync_custom_field_problems(fields, {"max_pages": "250", "depth": 3, "discovery": "links"}, {}) == []
    # A stored out-of-range value doesn't block saving something else.
    assert sync_custom_field_problems(fields, {"discovery": "links"}, {"max_pages": 50_000}) == []


def test_a_hidden_setting_is_not_checked() -> None:
    fields = _web_fields()
    depth = next(f for f in fields if f["name"] == "depth")
    assert not is_shown(depth, {"type": "single"})
    assert is_shown(depth, {"type": "recursive"})
    assert sync_custom_field_problems(fields, {"depth": 99}, {"type": "single"}) == []
    assert sync_custom_field_problems(fields, {"depth": 99}, {}) != [], "type defaults to recursive"


async def test_the_save_route_refuses_bad_values_with_every_reason() -> None:
    registry = SimpleNamespace(get_connector_metadata=lambda _type: _async(WebConnector._connector_metadata))
    with pytest.raises(HTTPException) as refused:
        await router._validate_sync_custom_fields(
            registry, "Web", {"max_pages": 0, "discovery": "x"}, {"sync": {"type": "recursive"}}
        )
    assert refused.value.status_code == 400
    assert refused.value.detail == (
        "Find pages using: Choose one of the listed options. Maximum pages: Enter a whole number from 1 to 10,000."
    )

    await router._validate_sync_custom_fields(registry, "Web", {"max_pages": 10}, {"sync": {}})


async def _async(value: object) -> object:
    return value
