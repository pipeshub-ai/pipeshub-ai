import logging

from app.modules.agents.qna.reference_data import (
    CORE_FIELDS,
    METADATA_FIELDS,
    REFERENCE_DATA_FIELDS,
    format_reference_data,
    generate_field_instructions,
    generate_field_rules_table,
    get_all_field_names,
    normalize_reference_data_items,
)


def test_generate_field_rules_table_contains_core_and_metadata_locations():
    table = generate_field_rules_table()
    assert "| Field | Required | Description |" in table
    assert "`name`" in table
    assert "`webUrl`" in table
    assert "| `metadata` |" not in table  # metadata is container, not a declared field row
    assert "Top-level JSON key" in table
    assert "Inside `metadata` object" in table


def test_generate_field_instructions_includes_all_declared_fields():
    instructions = generate_field_instructions()
    for field in REFERENCE_DATA_FIELDS:
        assert f"`{field.name}`" in instructions


def test_get_all_field_names_matches_declaration_order():
    expected = [field.name for field in REFERENCE_DATA_FIELDS]
    assert get_all_field_names() == expected


def test_format_reference_data_groups_by_app_and_surfaces_metadata_from_legacy_top_level():
    data = [
        {
            "name": "Project Alpha",
            "type": "project",
            "app": "jira",
            "id": "100",
            "webUrl": "https://jira.example.com/projects/PA",
            "key": "PA",
        },
        {
            "name": "Notebook A",
            "type": "notebook",
            "app": "sharepoint",
            "id": "nb-1",
            "metadata": {"siteId": "site-1"},
        },
    ]
    result = format_reference_data(data, log=logging.getLogger("test"))

    assert "## Reference Data" in result
    assert "**Jira**" in result
    assert "**Sharepoint**" in result
    assert "key=PA" in result  # legacy top-level metadata field still displayed
    assert "siteId=site-1" in result


def test_format_reference_data_respects_max_items():
    data = [{"name": f"Issue {i}", "type": "issue", "app": "jira", "id": str(i)} for i in range(20)]
    result = format_reference_data(data, max_items=5)
    for i in range(5):
        assert f"Issue {i}" in result
    assert "Issue 5" not in result


def test_normalize_reference_data_items_moves_extension_fields_into_metadata():
    raw = [
        {
            "name": "Issue 1",
            "type": "issue",
            "app": "jira",
            "id": "1",
            "key": "PA-1",
            "accountId": "acc-1",
        }
    ]

    normalized = normalize_reference_data_items(raw)

    assert normalized[0]["name"] == "Issue 1"
    assert "key" not in normalized[0]
    assert "accountId" not in normalized[0]
    assert normalized[0]["metadata"] == {"key": "PA-1", "accountId": "acc-1"}


def test_normalize_reference_data_items_merges_existing_metadata_and_ignores_blank_values():
    raw = [
        {
            "name": "Notebook",
            "type": "notebook",
            "app": "sharepoint",
            "id": "n1",
            "metadata": {"siteId": "site-existing", "key": ""},
            "siteId": "site-new",
            "key": "   ",
        }
    ]

    normalized = normalize_reference_data_items(raw)
    assert normalized[0]["metadata"]["siteId"] == "site-existing"
    assert "key" not in normalized[0]["metadata"]


def test_normalize_reference_data_items_handles_non_list_and_non_dict_entries():
    assert normalize_reference_data_items(None) == []
    assert normalize_reference_data_items("bad") == []
    assert normalize_reference_data_items([1, "x", {"name": "ok", "type": "file", "app": "drive"}]) == [
        {"name": "ok", "type": "file", "app": "drive"}
    ]


def test_reference_data_field_sets_are_consistent():
    assert len(CORE_FIELDS) + len(METADATA_FIELDS) == len(REFERENCE_DATA_FIELDS)
    assert "app" in CORE_FIELDS
    assert "siteId" in METADATA_FIELDS
