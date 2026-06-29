"""Tests for Jira custom field discovery."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.sources.atlassian.jira.enrichment import field_discovery
from app.connectors.sources.atlassian.jira.enrichment.field_discovery import (
    _field_schema_custom,
    _matches_schema_rule,
    _parse_fields_response,
    discover_custom_field_ids,
)


def _fields_response(fields: list[dict], *, status: int = HttpStatusCode.OK.value) -> MagicMock:
    response = MagicMock()
    response.status = status
    response.json.return_value = fields
    return response


@pytest.fixture(autouse=True)
def clear_discovery_cache():
    field_discovery._discovery_cache.clear()
    yield
    field_discovery._discovery_cache.clear()


class TestFieldSchemaHelpers:
    def test_field_schema_custom_returns_none_without_schema(self):
        assert _field_schema_custom({}) is None
        assert _field_schema_custom({"schema": {}}) is None

    def test_field_schema_custom_returns_string(self):
        field_def = {"schema": {"custom": "com.example:custom-type"}}
        assert _field_schema_custom(field_def) == "com.example:custom-type"

    def test_matches_schema_rule_string_equality(self):
        field_def = {"schema": {"custom": "com.pyxis.greenhopper.jira:gh-sprint"}}
        assert _matches_schema_rule(field_def, "com.pyxis.greenhopper.jira:gh-sprint") is True
        assert _matches_schema_rule(field_def, "other") is False

    def test_matches_schema_rule_dict_with_name_in(self):
        field_def = {
            "name": "Story Points",
            "schema": {"custom": "com.atlassian.jira.plugin.system.customfieldtypes:float"},
        }
        rule = {
            "schema": "com.atlassian.jira.plugin.system.customfieldtypes:float",
            "name_in": ("Story Points", "Story point estimate"),
        }
        assert _matches_schema_rule(field_def, rule) is True

        field_def["name"] = "Velocity"
        assert _matches_schema_rule(field_def, rule) is False

    def test_matches_schema_rule_dict_without_name_in(self):
        field_def = {
            "schema": {"custom": "com.pyxis.greenhopper.jira:gh-epic-link"},
        }
        rule = {"schema": "com.pyxis.greenhopper.jira:gh-epic-link"}
        assert _matches_schema_rule(field_def, rule) is True

    def test_matches_schema_rule_uses_untranslated_name(self):
        field_def = {
            "untranslatedName": "Severity",
            "schema": {"custom": "com.atlassian.jira.plugin.system.customfieldtypes:select"},
        }
        rule = {
            "schema": "com.atlassian.jira.plugin.system.customfieldtypes:select",
            "name_in": ("Severity",),
        }
        assert _matches_schema_rule(field_def, rule) is True

    def test_parse_fields_response_filters_non_dicts(self):
        payload = [{"id": "customfield_1"}, "bad", None, {"id": "customfield_2"}]
        assert _parse_fields_response(payload) == [{"id": "customfield_1"}, {"id": "customfield_2"}]

    def test_parse_fields_response_non_list_returns_empty(self):
        assert _parse_fields_response({"fields": []}) == []


class TestDiscoverCustomFieldIds:
    @pytest.mark.asyncio
    async def test_cloud_uses_get_fields(self):
        data_source = MagicMock()
        data_source.get_fields = AsyncMock(
            return_value=_fields_response([
                {
                    "id": "customfield_10110",
                    "schema": {"custom": "com.pyxis.greenhopper.jira:gh-sprint"},
                },
            ]),
        )

        result = await discover_custom_field_ids(
            data_source,
            is_cloud=True,
            connector_id="conn-1",
        )

        data_source.get_fields.assert_awaited_once()
        data_source.get_fields_v2.assert_not_called()
        assert result["sprint"] == "customfield_10110"

    @pytest.mark.asyncio
    async def test_dc_uses_get_fields_v2(self):
        data_source = MagicMock()
        data_source.get_fields_v2 = AsyncMock(
            return_value=_fields_response([
                {
                    "key": "customfield_10016",
                    "schema": {"custom": "com.pyxis.greenhopper.jira:jsw-story-points"},
                },
            ]),
        )

        result = await discover_custom_field_ids(
            data_source,
            is_cloud=False,
            connector_id="conn-dc",
        )

        data_source.get_fields_v2.assert_awaited_once()
        assert result["story_points"] == "customfield_10016"

    @pytest.mark.asyncio
    async def test_returns_cached_result_without_api_call(self):
        field_discovery._discovery_cache["conn-cached"] = {"sprint": "customfield_999"}
        data_source = MagicMock()
        data_source.get_fields = AsyncMock()

        result = await discover_custom_field_ids(
            data_source,
            is_cloud=True,
            connector_id="conn-cached",
        )

        assert result == {"sprint": "customfield_999"}
        data_source.get_fields.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_ok_response_returns_empty(self):
        data_source = MagicMock()
        data_source.get_fields = AsyncMock(
            return_value=_fields_response([], status=HttpStatusCode.INTERNAL_SERVER_ERROR.value),
        )

        result = await discover_custom_field_ids(
            data_source,
            is_cloud=True,
            connector_id="conn-fail",
        )

        assert result == {}

    @pytest.mark.asyncio
    async def test_skips_lexo_rank_and_epic_color_schemas(self):
        data_source = MagicMock()
        data_source.get_fields = AsyncMock(
            return_value=_fields_response([
                {
                    "id": "customfield_rank",
                    "schema": {"custom": "com.pyxis.greenhopper.jira:gh-lexo-rank"},
                },
                {
                    "id": "customfield_color",
                    "schema": {"custom": "com.pyxis.greenhopper.jira:gh-epic-color"},
                },
            ]),
        )

        result = await discover_custom_field_ids(
            data_source,
            is_cloud=True,
            connector_id="conn-skip",
        )

        assert result == {}

    @pytest.mark.asyncio
    async def test_story_points_matcher_with_name_in(self):
        data_source = MagicMock()
        data_source.get_fields = AsyncMock(
            return_value=_fields_response([
                {
                    "id": "customfield_10050",
                    "name": "Story point estimate",
                    "schema": {"custom": "com.atlassian.jira.plugin.system.customfieldtypes:float"},
                },
            ]),
        )

        result = await discover_custom_field_ids(
            data_source,
            is_cloud=True,
            connector_id="conn-sp",
        )

        assert result["story_points"] == "customfield_10050"

    @pytest.mark.asyncio
    async def test_field_without_id_is_not_discovered(self):
        data_source = MagicMock()
        data_source.get_fields = AsyncMock(
            return_value=_fields_response([
                {
                    "schema": {"custom": "com.pyxis.greenhopper.jira:gh-sprint"},
                },
            ]),
        )

        result = await discover_custom_field_ids(
            data_source,
            is_cloud=True,
            connector_id="conn-no-id",
        )

        assert result == {}
