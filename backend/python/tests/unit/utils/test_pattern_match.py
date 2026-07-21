"""Tests for app.utils.pattern_match — shared pattern match helpers."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils.pattern_match import (
    DEFAULT_PATTERN_MATCH_BLOCK_BUDGET,
    _build_synthetic_search_results,
    _fetch_pattern_record,
    _get_frontend_url,
    build_grep_command_from_query,
    cap_pattern_match_blocks,
    check_pattern_match_eligible,
    execute_pattern_match_pipeline,
    merge_pattern_match_results,
    resolve_connector_ids_for_search,
    run_pattern_match,
)


# ===========================================================================
# build_grep_command_from_query
# ===========================================================================


class TestBuildGrepCommand:
    def test_extracts_keywords(self):
        cmd = build_grep_command_from_query("What are the revenue projections for Q2?")
        assert cmd is not None
        assert "grep -rli" in cmd
        assert "revenue" in cmd
        assert "projections" in cmd

    def test_filters_stop_words(self):
        cmd = build_grep_command_from_query("What is the best way to do this?")
        assert cmd is not None
        assert "what" not in cmd
        assert "the" not in cmd
        assert "best" in cmd
        assert "way" in cmd

    def test_filters_short_words(self):
        cmd = build_grep_command_from_query("AI ML is ok")
        assert cmd is None

    def test_returns_none_for_only_stop_words(self):
        cmd = build_grep_command_from_query("what is this?")
        assert cmd is None

    def test_caps_at_five_keywords(self):
        cmd = build_grep_command_from_query(
            "revenue projections budget forecast analysis summary breakdown details"
        )
        assert cmd is not None
        parts = cmd.split('"')[1]
        keywords = parts.split(r"\|")
        assert len(keywords) == 5

    def test_empty_query(self):
        assert build_grep_command_from_query("") is None

    def test_numeric_keywords(self):
        cmd = build_grep_command_from_query("error code 404 timeout 500")
        assert cmd is not None
        assert "error" in cmd
        assert "code" in cmd
        assert "timeout" in cmd

    def test_hyphenated_words(self):
        cmd = build_grep_command_from_query("pre-release version")
        assert cmd is not None
        assert "pre-release" in cmd
        assert "version" in cmd


# ===========================================================================
# check_pattern_match_eligible
# ===========================================================================


class TestCheckPatternMatchEligible:
    @pytest.mark.asyncio
    async def test_local_storage_returns_true(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            return_value={"storageType": "local", "mountName": "PipesHub"}
        )
        logger = MagicMock()

        result = await check_pattern_match_eligible(config_service, logger)
        assert result is True

    @pytest.mark.asyncio
    async def test_s3_storage_returns_false(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            return_value={"storageType": "s3", "mountName": "PipesHub"}
        )
        logger = MagicMock()

        result = await check_pattern_match_eligible(config_service, logger)
        assert result is False

    @pytest.mark.asyncio
    async def test_config_error_returns_false(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(side_effect=Exception("etcd down"))
        logger = MagicMock()

        result = await check_pattern_match_eligible(config_service, logger)
        assert result is False


# ===========================================================================
# resolve_connector_ids_for_search
# ===========================================================================


class TestResolveConnectorIds:
    @pytest.mark.asyncio
    async def test_uses_apps_filter_when_present(self):
        graph_provider = AsyncMock()
        filters = {"apps": ["conn-1", "conn-2"]}

        result = await resolve_connector_ids_for_search(
            graph_provider, "org-1", filters
        )
        assert result == ["conn-1", "conn-2"]
        graph_provider.get_org_apps.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_filters_gets_all_org_apps(self):
        graph_provider = AsyncMock()
        graph_provider.get_org_apps = AsyncMock(
            return_value=[
                {"_key": "app-1", "name": "Gmail"},
                {"_key": "app-2", "name": "Slack"},
            ]
        )

        result = await resolve_connector_ids_for_search(
            graph_provider, "org-1", None
        )
        assert result == ["app-1", "app-2"]

    @pytest.mark.asyncio
    async def test_empty_filters_gets_all_org_apps(self):
        graph_provider = AsyncMock()
        graph_provider.get_org_apps = AsyncMock(
            return_value=[{"_key": "app-1"}]
        )

        result = await resolve_connector_ids_for_search(
            graph_provider, "org-1", {}
        )
        assert result == ["app-1"]

    @pytest.mark.asyncio
    async def test_only_kb_filter_returns_empty(self):
        graph_provider = AsyncMock()
        filters = {"kb": ["rg-1", "rg-2"]}

        result = await resolve_connector_ids_for_search(
            graph_provider, "org-1", filters
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_apps_empty_list_gets_all_org_apps(self):
        graph_provider = AsyncMock()
        graph_provider.get_org_apps = AsyncMock(
            return_value=[{"_key": "app-1"}]
        )
        filters = {"apps": []}

        result = await resolve_connector_ids_for_search(
            graph_provider, "org-1", filters
        )
        assert result == ["app-1"]

    @pytest.mark.asyncio
    async def test_get_org_apps_error_returns_empty(self):
        graph_provider = AsyncMock()
        graph_provider.get_org_apps = AsyncMock(side_effect=Exception("DB down"))

        result = await resolve_connector_ids_for_search(
            graph_provider, "org-1", None
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_skips_apps_without_key(self):
        graph_provider = AsyncMock()
        graph_provider.get_org_apps = AsyncMock(
            return_value=[
                {"_key": "app-1"},
                {"name": "no-key-app"},
                {"_key": "app-3"},
            ]
        )

        result = await resolve_connector_ids_for_search(
            graph_provider, "org-1", None
        )
        assert result == ["app-1", "app-3"]


# ===========================================================================
# run_pattern_match
# ===========================================================================


class TestRunPatternMatch:
    @pytest.mark.asyncio
    async def test_empty_connector_ids_returns_empty(self):
        result = await run_pattern_match(
            config_service=AsyncMock(),
            org_id="org-1",
            user_id="user-1",
            graph_provider=AsyncMock(),
            command='grep -rli "test" .',
            connector_ids=[],
            logger_instance=MagicMock(),
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_empty_command_returns_empty(self):
        result = await run_pattern_match(
            config_service=AsyncMock(),
            org_id="org-1",
            user_id="user-1",
            graph_provider=AsyncMock(),
            command="",
            connector_ids=["conn-1"],
            logger_instance=MagicMock(),
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_invalid_command_returns_empty(self):
        result = await run_pattern_match(
            config_service=AsyncMock(),
            org_id="org-1",
            user_id="user-1",
            graph_provider=AsyncMock(),
            command="rm -rf /",
            connector_ids=["conn-1"],
            logger_instance=MagicMock(),
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_aggregates_results_across_connectors(self):
        records_1 = [{"virtual_record_id": "vr-1", "file": "a.json"}]
        records_2 = [{"virtual_record_id": "vr-2", "file": "b.json"}]

        mock_storage = AsyncMock()
        mock_storage.find_records = AsyncMock(
            side_effect=[
                (True, json.dumps({"records": records_1})),
                (True, json.dumps({"records": records_2})),
            ]
        )

        with patch(
            "app.utils.pattern_match.StoragePatternMatch",
            return_value=mock_storage,
        ):
            result = await run_pattern_match(
                config_service=AsyncMock(),
                org_id="org-1",
                user_id="user-1",
                graph_provider=AsyncMock(),
                command='grep -rli "test" .',
                connector_ids=["conn-1", "conn-2"],
                logger_instance=MagicMock(),
            )

        assert len(result) == 2
        vrids = {r["virtual_record_id"] for r in result}
        assert vrids == {"vr-1", "vr-2"}

    @pytest.mark.asyncio
    async def test_unsuccessful_find_records_skipped(self):
        mock_storage = AsyncMock()
        mock_storage.find_records = AsyncMock(
            return_value=(False, "error: connector unreachable")
        )

        with patch(
            "app.utils.pattern_match.StoragePatternMatch",
            return_value=mock_storage,
        ):
            result = await run_pattern_match(
                config_service=AsyncMock(),
                org_id="org-1",
                user_id="user-1",
                graph_provider=AsyncMock(),
                command='grep -rli "test" .',
                connector_ids=["conn-1"],
                logger_instance=MagicMock(),
            )
        assert result == []

    @pytest.mark.asyncio
    async def test_invalid_json_output_skipped(self):
        mock_storage = AsyncMock()
        mock_storage.find_records = AsyncMock(
            side_effect=[
                (True, "not valid json"),
                (True, None),
            ]
        )

        with patch(
            "app.utils.pattern_match.StoragePatternMatch",
            return_value=mock_storage,
        ):
            result = await run_pattern_match(
                config_service=AsyncMock(),
                org_id="org-1",
                user_id="user-1",
                graph_provider=AsyncMock(),
                command='grep -rli "test" .',
                connector_ids=["conn-1", "conn-2"],
                logger_instance=MagicMock(),
            )
        assert result == []

    @pytest.mark.asyncio
    async def test_timeout_returns_empty(self):
        mock_storage = AsyncMock()
        mock_storage.find_records = AsyncMock(
            return_value=(True, json.dumps({"records": []}))
        )
        logger = MagicMock()

        with patch(
            "app.utils.pattern_match.StoragePatternMatch",
            return_value=mock_storage,
        ), patch(
            "app.utils.pattern_match.asyncio.wait_for",
            side_effect=asyncio.TimeoutError,
        ):
            result = await run_pattern_match(
                config_service=AsyncMock(),
                org_id="org-1",
                user_id="user-1",
                graph_provider=AsyncMock(),
                command='grep -rli "test" .',
                connector_ids=["conn-1"],
                logger_instance=logger,
            )
        assert result == []
        logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_connector_exception_skipped(self):
        records_ok = [{"virtual_record_id": "vr-1"}]
        mock_storage = AsyncMock()
        mock_storage.find_records = AsyncMock(
            side_effect=[
                (True, json.dumps({"records": records_ok})),
                Exception("connector crashed"),
            ]
        )
        logger = MagicMock()

        with patch(
            "app.utils.pattern_match.StoragePatternMatch",
            return_value=mock_storage,
        ):
            result = await run_pattern_match(
                config_service=AsyncMock(),
                org_id="org-1",
                user_id="user-1",
                graph_provider=AsyncMock(),
                command='grep -rli "test" .',
                connector_ids=["conn-1", "conn-2"],
                logger_instance=logger,
            )
        assert len(result) == 1
        assert result[0]["virtual_record_id"] == "vr-1"
        logger.debug.assert_called_once()


# ===========================================================================
# merge_pattern_match_results
# ===========================================================================


class TestMergePatternMatchResults:
    @pytest.mark.asyncio
    async def test_dedup_by_vrid(self):
        raw = [
            {"virtual_record_id": "vr-1"},
            {"virtual_record_id": "vr-1"},
            {"virtual_record_id": "vr-2"},
        ]
        graph_provider = AsyncMock()
        graph_provider.check_vrids_accessible = AsyncMock(
            return_value={"vr-1": "rec-1", "vr-2": "rec-2"}
        )
        graph_provider.get_document = AsyncMock(return_value={"_key": "rec-1"})

        blob_store = AsyncMock()
        blob_store.config_service = AsyncMock()
        blob_store.config_service.get_config = AsyncMock(return_value={})

        with patch(
            "app.utils.pattern_match.get_record", new_callable=AsyncMock
        ) as mock_get_record, patch(
            "app.utils.pattern_match.get_flattened_results",
            new_callable=AsyncMock,
            return_value=[
                {"virtual_record_id": "vr-1", "block_index": 0},
                {"virtual_record_id": "vr-2", "block_index": 0},
            ],
        ):
            mock_get_record.return_value = None

            result = await merge_pattern_match_results(
                raw_records=raw,
                virtual_record_id_to_result={},
                user_id="user-1",
                org_id="org-1",
                blob_store=blob_store,
                graph_provider=graph_provider,
                is_multimodal_llm=False,
                logger_instance=MagicMock(),
            )

        assert graph_provider.check_vrids_accessible.call_count == 1
        called_vrids = graph_provider.check_vrids_accessible.call_args[1][
            "virtual_record_ids"
        ]
        assert len(called_vrids) == 2

    @pytest.mark.asyncio
    async def test_skips_already_in_semantic_results(self):
        raw = [
            {"virtual_record_id": "vr-1"},
            {"virtual_record_id": "vr-2"},
        ]
        existing = {"vr-1": {"some": "data"}}

        graph_provider = AsyncMock()
        graph_provider.check_vrids_accessible = AsyncMock(
            return_value={"vr-2": "rec-2"}
        )
        graph_provider.get_document = AsyncMock(return_value={"_key": "rec-2"})

        blob_store = AsyncMock()
        blob_store.config_service = AsyncMock()
        blob_store.config_service.get_config = AsyncMock(return_value={})

        with patch(
            "app.utils.pattern_match.get_record", new_callable=AsyncMock
        ), patch(
            "app.utils.pattern_match.get_flattened_results",
            new_callable=AsyncMock,
            return_value=[{"virtual_record_id": "vr-2", "block_index": 0}],
        ):
            result = await merge_pattern_match_results(
                raw_records=raw,
                virtual_record_id_to_result=existing,
                user_id="user-1",
                org_id="org-1",
                blob_store=blob_store,
                graph_provider=graph_provider,
                is_multimodal_llm=False,
                logger_instance=MagicMock(),
            )

        called_vrids = graph_provider.check_vrids_accessible.call_args[1][
            "virtual_record_ids"
        ]
        assert "vr-1" not in called_vrids
        assert "vr-2" in called_vrids

    @pytest.mark.asyncio
    async def test_no_accessible_returns_empty(self):
        raw = [{"virtual_record_id": "vr-1"}]
        graph_provider = AsyncMock()
        graph_provider.check_vrids_accessible = AsyncMock(return_value={})

        result = await merge_pattern_match_results(
            raw_records=raw,
            virtual_record_id_to_result={},
            user_id="user-1",
            org_id="org-1",
            blob_store=AsyncMock(),
            graph_provider=graph_provider,
            is_multimodal_llm=False,
            logger_instance=MagicMock(),
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_empty_records_returns_empty(self):
        result = await merge_pattern_match_results(
            raw_records=[],
            virtual_record_id_to_result={},
            user_id="user-1",
            org_id="org-1",
            blob_store=AsyncMock(),
            graph_provider=AsyncMock(),
            is_multimodal_llm=False,
            logger_instance=MagicMock(),
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_flattened_results_when_synthetic_nonempty(self):
        raw = [{"virtual_record_id": "vr-1"}]
        graph_provider = AsyncMock()
        graph_provider.check_vrids_accessible = AsyncMock(
            return_value={"vr-1": "rec-1"}
        )
        graph_provider.get_document = AsyncMock(return_value={"_key": "rec-1"})

        blob_store = AsyncMock()
        blob_store.config_service = AsyncMock()
        blob_store.config_service.get_config = AsyncMock(return_value={})

        async def fake_get_record(
            vrid,
            vrid_map,
            blob_store_arg,
            org_id,
            graph_records,
            graph_provider_arg,
            frontend_url,
        ) -> None:
            # Simulate get_record populating the shared result map with a
            # record that has blocks, so _build_synthetic_search_results
            # produces non-empty output and the flatten branch is exercised.
            vrid_map[vrid] = {
                "block_containers": {"blocks": [{"text": "hello"}]}
            }

        flattened_expected = [{"virtual_record_id": "vr-1", "block_index": 0}]

        with patch(
            "app.utils.pattern_match.get_record",
            new_callable=AsyncMock,
            side_effect=fake_get_record,
        ), patch(
            "app.utils.pattern_match.get_flattened_results",
            new_callable=AsyncMock,
            return_value=flattened_expected,
        ) as mock_flatten:
            result = await merge_pattern_match_results(
                raw_records=raw,
                virtual_record_id_to_result={},
                user_id="user-1",
                org_id="org-1",
                blob_store=blob_store,
                graph_provider=graph_provider,
                is_multimodal_llm=False,
                logger_instance=MagicMock(),
            )

        assert result == flattened_expected
        mock_flatten.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_empty_when_flattened_empty(self):
        raw = [{"virtual_record_id": "vr-1"}]
        graph_provider = AsyncMock()
        graph_provider.check_vrids_accessible = AsyncMock(
            return_value={"vr-1": "rec-1"}
        )
        graph_provider.get_document = AsyncMock(return_value={"_key": "rec-1"})

        blob_store = AsyncMock()
        blob_store.config_service = AsyncMock()
        blob_store.config_service.get_config = AsyncMock(return_value={})

        async def fake_get_record(
            vrid,
            vrid_map,
            blob_store_arg,
            org_id,
            graph_records,
            graph_provider_arg,
            frontend_url,
        ) -> None:
            vrid_map[vrid] = {
                "block_containers": {"blocks": [{"text": "hello"}]}
            }

        with patch(
            "app.utils.pattern_match.get_record",
            new_callable=AsyncMock,
            side_effect=fake_get_record,
        ), patch(
            "app.utils.pattern_match.get_flattened_results",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await merge_pattern_match_results(
                raw_records=raw,
                virtual_record_id_to_result={},
                user_id="user-1",
                org_id="org-1",
                blob_store=blob_store,
                graph_provider=graph_provider,
                is_multimodal_llm=False,
                logger_instance=MagicMock(),
            )

        assert result == []


# ===========================================================================
# _build_synthetic_search_results
# ===========================================================================


class TestBuildSyntheticSearchResults:
    def test_builds_block_entries(self):
        records = [{"virtual_record_id": "vr-1"}]
        vrid_map = {
            "vr-1": {
                "block_containers": {
                    "blocks": [{"text": "block0"}, {"text": "block1"}]
                }
            }
        }

        results = _build_synthetic_search_results(
            records, vrid_map, "org-1", MagicMock()
        )
        assert len(results) == 2
        assert results[0]["metadata"]["virtualRecordId"] == "vr-1"
        assert results[0]["metadata"]["blockIndex"] == 0
        assert results[1]["metadata"]["blockIndex"] == 1
        assert all(r["score"] == 0.0 for r in results)

    def test_no_blocks_creates_single_entry(self):
        records = [{"virtual_record_id": "vr-1"}]
        vrid_map = {"vr-1": {"block_containers": {"blocks": []}}}

        results = _build_synthetic_search_results(
            records, vrid_map, "org-1", MagicMock()
        )
        assert len(results) == 1
        assert results[0]["metadata"]["blockIndex"] == 0
        assert results[0]["metadata"]["isBlockGroup"] is False

    def test_missing_record_skipped(self):
        records = [{"virtual_record_id": "vr-missing"}]
        vrid_map = {}

        results = _build_synthetic_search_results(
            records, vrid_map, "org-1", MagicMock()
        )
        assert results == []


# ===========================================================================
# execute_pattern_match_pipeline
# ===========================================================================


class TestExecutePatternMatchPipeline:
    @pytest.mark.asyncio
    async def test_returns_empty_when_no_keywords(self):
        result = await execute_pattern_match_pipeline(
            query="is it?",
            config_service=AsyncMock(),
            org_id="org-1",
            user_id="user-1",
            graph_provider=AsyncMock(),
            filters=None,
            logger_instance=MagicMock(),
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_not_local(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            return_value={"storageType": "s3"}
        )

        result = await execute_pattern_match_pipeline(
            query="revenue projections",
            config_service=config_service,
            org_id="org-1",
            user_id="user-1",
            graph_provider=AsyncMock(),
            filters=None,
            logger_instance=MagicMock(),
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_connectors(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            return_value={"storageType": "local"}
        )
        graph_provider = AsyncMock()
        graph_provider.get_org_apps = AsyncMock(return_value=[])

        result = await execute_pattern_match_pipeline(
            query="revenue projections",
            config_service=config_service,
            org_id="org-1",
            user_id="user-1",
            graph_provider=graph_provider,
            filters=None,
            logger_instance=MagicMock(),
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_full_pipeline_success_delegates_to_run_pattern_match(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            return_value={"storageType": "local"}
        )
        graph_provider = AsyncMock()
        graph_provider.get_org_apps = AsyncMock(
            return_value=[{"_key": "app-1"}]
        )

        expected = [{"virtual_record_id": "vr-1"}]
        with patch(
            "app.utils.pattern_match.run_pattern_match",
            new_callable=AsyncMock,
            return_value=expected,
        ) as mock_run:
            result = await execute_pattern_match_pipeline(
                query="revenue projections",
                config_service=config_service,
                org_id="org-1",
                user_id="user-1",
                graph_provider=graph_provider,
                filters=None,
                logger_instance=MagicMock(),
            )

        assert result == expected
        mock_run.assert_called_once()
        assert mock_run.call_args.kwargs["connector_ids"] == ["app-1"]


# ===========================================================================
# _get_frontend_url
# ===========================================================================


class TestGetFrontendUrl:
    @pytest.mark.asyncio
    async def test_config_error_returns_none(self):
        blob_store = AsyncMock()
        blob_store.config_service = AsyncMock()
        blob_store.config_service.get_config = AsyncMock(
            side_effect=Exception("etcd down")
        )

        result = await _get_frontend_url(blob_store)
        assert result is None

    @pytest.mark.asyncio
    async def test_non_dict_config_returns_none(self):
        blob_store = AsyncMock()
        blob_store.config_service = AsyncMock()
        blob_store.config_service.get_config = AsyncMock(return_value=None)

        result = await _get_frontend_url(blob_store)
        assert result is None

    @pytest.mark.asyncio
    async def test_valid_config_returns_url(self):
        blob_store = AsyncMock()
        blob_store.config_service = AsyncMock()
        blob_store.config_service.get_config = AsyncMock(
            return_value={"frontend": {"publicEndpoint": "https://app.example.com"}}
        )

        result = await _get_frontend_url(blob_store)
        assert result == "https://app.example.com"


# ===========================================================================
# _fetch_pattern_record
# ===========================================================================


class TestFetchPatternRecord:
    @pytest.mark.asyncio
    async def test_missing_graph_record_returns_without_fetching(self):
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value=None)

        with patch(
            "app.utils.pattern_match.get_record", new_callable=AsyncMock
        ) as mock_get_record:
            await _fetch_pattern_record(
                vrid="vr-1",
                record_id="rec-1",
                virtual_record_id_to_result={},
                blob_store=AsyncMock(),
                org_id="org-1",
                graph_provider=graph_provider,
                frontend_url=None,
                logger_instance=MagicMock(),
            )
        mock_get_record.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_is_caught_and_logged(self):
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(side_effect=Exception("db timeout"))
        logger = MagicMock()

        # Should not raise - errors for a single record must not abort the batch.
        await _fetch_pattern_record(
            vrid="vr-1",
            record_id="rec-1",
            virtual_record_id_to_result={},
            blob_store=AsyncMock(),
            org_id="org-1",
            graph_provider=graph_provider,
            frontend_url=None,
            logger_instance=logger,
        )
        logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_success_calls_get_record(self):
        graph_provider = AsyncMock()
        graph_provider.get_document = AsyncMock(return_value={"_key": "rec-1"})

        with patch(
            "app.utils.pattern_match.get_record", new_callable=AsyncMock
        ) as mock_get_record:
            await _fetch_pattern_record(
                vrid="vr-1",
                record_id="rec-1",
                virtual_record_id_to_result={},
                blob_store=AsyncMock(),
                org_id="org-1",
                graph_provider=graph_provider,
                frontend_url="https://app.example.com",
                logger_instance=MagicMock(),
            )
        mock_get_record.assert_called_once()


# ===========================================================================
# cap_pattern_match_blocks
# ===========================================================================


class TestCapPatternMatchBlocks:
    """Shared budget cap used by both chatbot.py call sites and the
    retrieval agent action, so a single large record's blocks can't overflow
    the context sent to the LLM."""

    def test_under_budget_passes_through_unchanged(self):
        blocks = [{"virtual_record_id": "vr-a", "block_index": i} for i in range(5)]
        vmap = {"vr-a": {"_id": "vr-a"}}

        out = cap_pattern_match_blocks(
            blocks,
            budget=10,
            virtual_record_id_to_result=vmap,
            logger_instance=MagicMock(),
        )

        assert out is blocks
        assert vmap == {"vr-a": {"_id": "vr-a"}}

    def test_zero_budget_drops_everything_and_prunes_vrid_map(self):
        blocks = [
            {"virtual_record_id": "vr-a", "block_index": 0},
            {"virtual_record_id": "vr-b", "block_index": 0},
        ]
        vmap = {"vr-a": {"_id": "vr-a"}, "vr-b": {"_id": "vr-b"}}

        out = cap_pattern_match_blocks(
            blocks,
            budget=0,
            virtual_record_id_to_result=vmap,
            logger_instance=MagicMock(),
        )

        assert out == []
        assert vmap == {}

    def test_over_budget_distributes_proportionally_across_records(self):
        # vr-a: 100 blocks, vr-b: 5 blocks, vr-c: 1 block, vr-d: 1 block.
        # Budget of 50 should be consumed by vr-a/vr-b/vr-c, orphaning vr-d.
        blocks = (
            [{"virtual_record_id": "vr-a", "block_index": i} for i in range(100)]
            + [{"virtual_record_id": "vr-b", "block_index": i} for i in range(5)]
            + [{"virtual_record_id": "vr-c", "block_index": 0}]
            + [{"virtual_record_id": "vr-d", "block_index": 0}]
        )
        vmap = {vid: {"_id": vid} for vid in ("vr-a", "vr-b", "vr-c", "vr-d")}

        out = cap_pattern_match_blocks(
            blocks,
            budget=50,
            virtual_record_id_to_result=vmap,
            logger_instance=MagicMock(),
        )

        assert 0 < len(out) <= 50
        assert "vr-d" not in vmap
        assert "vr-a" in vmap
        assert "vr-b" in vmap
        assert "vr-c" in vmap

    def test_default_budget_constant_matches_retrieval_default(self):
        # Documents the shared baseline referenced by chatbot.py's fallback
        # (`limit or DEFAULT_PATTERN_MATCH_BLOCK_BUDGET`) and retrieval.py's
        # `adjusted_limit = 50` default for the <=1 source case.
        assert DEFAULT_PATTERN_MATCH_BLOCK_BUDGET == 50

    def test_no_vrid_key_does_not_raise(self):
        blocks = [{"block_index": i} for i in range(60)]
        vmap: dict = {}

        out = cap_pattern_match_blocks(
            blocks,
            budget=10,
            virtual_record_id_to_result=vmap,
            logger_instance=MagicMock(),
        )

        assert len(out) <= 10
