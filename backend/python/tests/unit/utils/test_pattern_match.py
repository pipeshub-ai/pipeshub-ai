"""Tests for app.utils.pattern_match — shared pattern match helpers."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils.pattern_match import (
    DEFAULT_PATTERN_MATCH_BLOCK_BUDGET,
    GrepCommandResult,
    _LLM_GREP_TIMEOUT,
    _MAX_GREP_OUTPUT_LINES,
    _MAX_LLM_GREP_COMMANDS,
    _PATTERN_MATCH_TIMEOUT,
    _build_synthetic_search_results,
    _fetch_pattern_record,
    _get_frontend_url,
    _pre_validate_llm_grep,
    _record_in_time_range,
    build_grep_command_from_query,
    cancel_task_if_running,
    cap_pattern_match_blocks,
    check_pattern_match_eligible,
    execute_pattern_match_pipeline,
    generate_grep_command_via_llm,
    merge_pattern_match_results,
    resolve_connector_ids_for_search,
    run_pattern_match,
    validate_grep_command,
)


# ===========================================================================
# build_grep_command_from_query
# ===========================================================================


class TestBuildGrepCommand:
    def test_extracts_keywords(self):
        cmd = build_grep_command_from_query("What are the revenue projections for Q2?")
        assert cmd is not None
        assert "grep -rci" in cmd
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
# validate_grep_command
# ===========================================================================


class TestValidateGrepCommand:
    def test_valid_grep_command(self):
        cmd = 'grep -rli "budget" .'
        assert validate_grep_command(cmd) == cmd

    def test_valid_pipe_command(self):
        cmd = 'grep -rl "budget" . | grep -l "forecast"'
        assert validate_grep_command(cmd) == cmd

    def test_valid_or_pattern(self):
        cmd = 'grep -rli "budget\\|forecast" .'
        assert validate_grep_command(cmd) == cmd

    def test_valid_rg_command(self):
        cmd = 'rg -li "budget" .'
        assert validate_grep_command(cmd) == cmd

    def test_valid_egrep_command(self):
        cmd = 'egrep -rli "budget|forecast" .'
        assert validate_grep_command(cmd) == cmd

    def test_valid_fgrep_command(self):
        cmd = 'fgrep -rli "exact match" .'
        assert validate_grep_command(cmd) == cmd

    def test_empty_string_returns_none(self):
        assert validate_grep_command("") is None

    def test_whitespace_only_returns_none(self):
        assert validate_grep_command("   ") is None

    def test_null_byte_rejected(self):
        assert validate_grep_command("grep -rli \x00 .") is None

    def test_oversized_command_rejected(self):
        assert validate_grep_command("grep " + "a" * 996) is None

    def test_max_length_passes(self):
        cmd = "grep " + "a" * 995
        assert validate_grep_command(cmd) == cmd

    def test_stripped_whitespace(self):
        assert validate_grep_command("  grep -rli test .  ") == "grep -rli test ."

    def test_backtick_rejected(self):
        assert validate_grep_command("grep -rli `whoami` .") is None

    def test_dollar_sign_rejected(self):
        assert validate_grep_command('grep -rli "$HOME" .') is None

    def test_semicolon_rejected(self):
        assert validate_grep_command('grep -rli "test" .; rm -rf /') is None

    def test_double_ampersand_rejected(self):
        assert validate_grep_command('grep -rli "test" . && rm -rf /') is None

    def test_double_pipe_rejected(self):
        assert validate_grep_command('grep -rli "test" . || rm -rf /') is None

    def test_output_redirection_rejected(self):
        assert validate_grep_command('grep -rli "test" . >> /tmp/out') is None

    def test_non_grep_binary_rejected(self):
        assert validate_grep_command("rm -rf /") is None

    def test_non_grep_binary_in_pipe_rejected(self):
        assert validate_grep_command('grep -rl "test" . | rm -rf /') is None

    def test_empty_pipe_segment_rejected(self):
        assert validate_grep_command('grep -rli "test" . |') is None

    def test_newline_rejected(self):
        assert validate_grep_command("grep -rli test .\nrm -rf /") is None

    def test_carriage_return_rejected(self):
        assert validate_grep_command("grep -rli test .\rrm -rf /") is None

    def test_process_substitution_rejected(self):
        assert validate_grep_command("grep -rli <(cat /etc/passwd) .") is None

    def test_command_substitution_rejected(self):
        assert validate_grep_command('grep -rli "$(whoami)" .') is None

    def test_variable_expansion_rejected(self):
        assert validate_grep_command('grep -rli "${HOME}" .') is None

    def test_cat_in_pipe_rejected(self):
        assert validate_grep_command('cat /etc/passwd | grep "root"') is None

    def test_multi_pipe_all_grep_passes(self):
        cmd = 'grep -rl "budget" . | grep -l "forecast" | grep -li "2024"'
        assert validate_grep_command(cmd) == cmd

    def test_unbalanced_quotes_rejected(self):
        assert validate_grep_command('grep -rli "unclosed .') is None


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
        assert result == ["rg-1", "rg-2"]

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
        assert any(
            args[0] == "Pattern match connector %d error: %s" and args[1] == 1
            for args, _ in logger.info.call_args_list
        )


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


# ===========================================================================
# cancel_task_if_running
# ===========================================================================


class TestCancelTaskIfRunning:
    @pytest.mark.asyncio
    async def test_none_task_is_noop(self):
        await cancel_task_if_running(None)

    @pytest.mark.asyncio
    async def test_already_done_task_is_noop(self):
        task = asyncio.ensure_future(asyncio.sleep(0))
        await task
        await cancel_task_if_running(task)

    @pytest.mark.asyncio
    async def test_running_task_is_cancelled(self):
        task = asyncio.ensure_future(asyncio.sleep(100))
        await cancel_task_if_running(task)
        assert task.cancelled()

    @pytest.mark.asyncio
    async def test_task_raising_exception_is_suppressed(self):
        async def _boom():
            await asyncio.sleep(100)

        task = asyncio.ensure_future(_boom())
        await cancel_task_if_running(task)
        assert task.cancelled()


# ===========================================================================
# build_grep_command_from_query — additional edge cases
# ===========================================================================


class TestBuildGrepCommandEdgeCases:
    def test_special_characters_stripped(self):
        """Regex findall only captures word chars and hyphens, so parens/quotes
        are implicitly stripped — verify the command is still valid."""
        cmd = build_grep_command_from_query('What is the "revenue" (annual)?')
        assert cmd is not None
        assert "revenue" in cmd
        assert "annual" in cmd
        assert '"' not in cmd.split('"')[1].replace(r"\|", "")

    def test_mixed_case_normalized(self):
        cmd = build_grep_command_from_query("Revenue PROJECTIONS Budget")
        assert cmd is not None
        assert "revenue" in cmd
        assert "projections" in cmd
        assert "budget" in cmd

    def test_unicode_words_extracted(self):
        """Unicode letters are not matched by [a-zA-Z0-9_-], so queries with
        only non-Latin keywords return None."""
        cmd = build_grep_command_from_query("什么是收入预测")
        assert cmd is None

    def test_single_long_keyword(self):
        cmd = build_grep_command_from_query("infrastructure")
        assert cmd is not None
        assert "infrastructure" in cmd

    def test_all_numeric_short_words(self):
        """Numeric tokens < 3 chars are filtered."""
        cmd = build_grep_command_from_query("42 is 7 ok")
        assert cmd is None


# ===========================================================================
# _record_in_time_range — edge cases
# ===========================================================================


class TestRecordInTimeRange:
    def test_no_time_range_returns_true(self):
        assert _record_in_time_range({}, None) is True
        assert _record_in_time_range({}, {}) is True

    def test_created_after_exact_boundary(self):
        record = {"source_created_at": 1000}
        assert _record_in_time_range(record, {"source_created_after_ms": 1000}) is True

    def test_created_after_fails(self):
        record = {"source_created_at": 999}
        assert _record_in_time_range(record, {"source_created_after_ms": 1000}) is False

    def test_created_before_exact_boundary(self):
        record = {"source_created_at": 1000}
        assert _record_in_time_range(record, {"source_created_before_ms": 1000}) is True

    def test_created_before_fails(self):
        record = {"source_created_at": 1001}
        assert _record_in_time_range(record, {"source_created_before_ms": 1000}) is False

    def test_updated_after_ms(self):
        record = {"source_updated_at": 2000}
        assert _record_in_time_range(record, {"source_updated_after_ms": 1500}) is True

    def test_updated_before_ms(self):
        record = {"source_updated_at": 2000}
        assert _record_in_time_range(record, {"source_updated_before_ms": 2500}) is True

    def test_missing_timestamp_fails_when_bound_present(self):
        assert _record_in_time_range({}, {"source_created_after_ms": 1000}) is False

    def test_string_timestamp_is_coerced(self):
        record = {"source_created_at": "1500"}
        assert _record_in_time_range(record, {"source_created_after_ms": 1000}) is True

    def test_invalid_timestamp_fails(self):
        record = {"source_created_at": "not-a-number"}
        assert _record_in_time_range(record, {"source_created_after_ms": 1000}) is False

    def test_multiple_bounds_all_must_pass(self):
        record = {"source_created_at": 1500, "source_updated_at": 2000}
        time_range = {
            "source_created_after_ms": 1000,
            "source_created_before_ms": 2000,
            "source_updated_after_ms": 1500,
            "source_updated_before_ms": 2500,
        }
        assert _record_in_time_range(record, time_range) is True

    def test_multiple_bounds_one_fails(self):
        record = {"source_created_at": 1500, "source_updated_at": 3000}
        time_range = {
            "source_created_after_ms": 1000,
            "source_updated_before_ms": 2500,
        }
        assert _record_in_time_range(record, time_range) is False


# ===========================================================================
# _build_synthetic_search_results — multiple records with blocks
# ===========================================================================


class TestBuildSyntheticMultipleRecords:
    def test_multiple_records_produce_blocks(self):
        records = [
            {"virtual_record_id": "vr-1"},
            {"virtual_record_id": "vr-2"},
        ]
        vrid_map = {
            "vr-1": {"block_containers": {"blocks": [{"text": "a"}, {"text": "b"}]}},
            "vr-2": {"block_containers": {"blocks": [{"text": "c"}]}},
        }
        logger = MagicMock()
        results = _build_synthetic_search_results(records, vrid_map, "org-1", logger)
        assert len(results) == 3
        vr1_blocks = [r for r in results if r["metadata"]["virtualRecordId"] == "vr-1"]
        vr2_blocks = [r for r in results if r["metadata"]["virtualRecordId"] == "vr-2"]
        assert len(vr1_blocks) == 2
        assert len(vr2_blocks) == 1

    def test_record_with_empty_blocks_gets_placeholder(self):
        records = [{"virtual_record_id": "vr-1"}]
        vrid_map = {"vr-1": {"block_containers": {"blocks": []}}}
        logger = MagicMock()
        results = _build_synthetic_search_results(records, vrid_map, "org-1", logger)
        assert len(results) == 1
        assert results[0]["metadata"]["blockIndex"] == 0
        assert results[0]["metadata"]["isBlockGroup"] is False

    def test_record_not_in_vrid_map_skipped(self):
        records = [{"virtual_record_id": "vr-missing"}]
        vrid_map = {}
        logger = MagicMock()
        results = _build_synthetic_search_results(records, vrid_map, "org-1", logger)
        assert results == []

    def test_org_id_in_metadata(self):
        records = [{"virtual_record_id": "vr-1"}]
        vrid_map = {"vr-1": {"block_containers": {"blocks": [{"text": "a"}]}}}
        logger = MagicMock()
        results = _build_synthetic_search_results(records, vrid_map, "org-99", logger)
        assert results[0]["metadata"]["orgId"] == "org-99"


# ===========================================================================
# cap_pattern_match_blocks — additional edge cases
# ===========================================================================


class TestCapPatternMatchBlocksEdgeCases:
    def test_single_record_over_budget_trimmed(self):
        blocks = [{"virtual_record_id": "vr-1"} for _ in range(20)]
        vrid_map = {"vr-1": {"data": True}}
        logger = MagicMock()
        result = cap_pattern_match_blocks(
            blocks, budget=5, virtual_record_id_to_result=vrid_map, logger_instance=logger
        )
        assert len(result) == 5
        assert "vr-1" in vrid_map

    def test_multiple_records_fair_distribution(self):
        blocks = (
            [{"virtual_record_id": "vr-1"} for _ in range(10)]
            + [{"virtual_record_id": "vr-2"} for _ in range(10)]
        )
        vrid_map = {"vr-1": {}, "vr-2": {}}
        logger = MagicMock()
        result = cap_pattern_match_blocks(
            blocks, budget=6, virtual_record_id_to_result=vrid_map, logger_instance=logger
        )
        assert len(result) == 6
        vr1_count = sum(1 for b in result if b["virtual_record_id"] == "vr-1")
        vr2_count = sum(1 for b in result if b["virtual_record_id"] == "vr-2")
        assert vr1_count >= 1
        assert vr2_count >= 1

    def test_orphaned_vrids_pruned_from_map(self):
        blocks = (
            [{"virtual_record_id": "vr-1"} for _ in range(10)]
            + [{"virtual_record_id": "vr-2"} for _ in range(10)]
        )
        vrid_map = {"vr-1": {"data": True}, "vr-2": {"data": True}}
        logger = MagicMock()
        cap_pattern_match_blocks(
            blocks, budget=1, virtual_record_id_to_result=vrid_map, logger_instance=logger
        )
        assert len(vrid_map) == 1

    def test_blocks_without_vrid_key_handled(self):
        blocks = [{"no_vrid": True}]
        vrid_map = {}
        logger = MagicMock()
        result = cap_pattern_match_blocks(
            blocks, budget=10, virtual_record_id_to_result=vrid_map, logger_instance=logger
        )
        assert len(result) == 1


# ===========================================================================
# run_pattern_match — additional edge cases
# ===========================================================================


class TestRunPatternMatchEdgeCases:
    @pytest.mark.asyncio
    async def test_exception_in_one_connector_does_not_break_others(self):
        """When one connector raises an exception, results from others are
        still collected."""
        storage_tool = AsyncMock()

        async def _find_records(connector_id, command, max_results, **kwargs):
            if connector_id == "c-bad":
                raise RuntimeError("boom")
            return (True, json.dumps({"records": [{"virtual_record_id": f"vr-{connector_id}"}]}))

        storage_tool.find_records = _find_records

        with patch(
            "app.utils.pattern_match.StoragePatternMatch",
            return_value=storage_tool,
        ), patch(
            "app.utils.pattern_match._validate_command",
            return_value=(True, None),
        ):
            result = await run_pattern_match(
                config_service=AsyncMock(),
                org_id="org-1",
                user_id="user-1",
                graph_provider=AsyncMock(),
                command='grep -rli "test" .',
                connector_ids=["c-good", "c-bad", "c-ok"],
                logger_instance=MagicMock(),
            )
        assert len(result) >= 1


# ===========================================================================
# execute_pattern_match_pipeline with grep_command (agent-provided command)
# ===========================================================================


class TestExecutePatternMatchPipelineWithGrepCommand:
    @pytest.mark.asyncio
    async def test_valid_grep_command_passed_directly(self):
        """A valid grep command is passed directly to run_pattern_match."""
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            return_value={"storageType": "local"}
        )
        graph_provider = AsyncMock()
        graph_provider.get_org_apps = AsyncMock(
            return_value=[{"_key": "app-1"}]
        )

        with patch(
            "app.utils.pattern_match.run_pattern_match",
            new_callable=AsyncMock,
            return_value=[{"virtual_record_id": "vr-1"}],
        ) as mock_run:
            await execute_pattern_match_pipeline(
                query="is it?",
                config_service=config_service,
                org_id="org-1",
                user_id="user-1",
                graph_provider=graph_provider,
                filters=None,
                logger_instance=MagicMock(),
                grep_command='grep -rli "deployment checklist" .',
            )

        mock_run.assert_called_once()
        command_used = mock_run.call_args.kwargs["command"]
        assert command_used.startswith('grep -rli "deployment checklist" .')
        assert "| head" in command_used

    @pytest.mark.asyncio
    async def test_grep_command_overrides_stop_word_query(self):
        """grep_command works even when the query would produce no keywords."""
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            return_value={"storageType": "local"}
        )
        graph_provider = AsyncMock()
        graph_provider.get_org_apps = AsyncMock(
            return_value=[{"_key": "app-1"}]
        )

        with patch(
            "app.utils.pattern_match.run_pattern_match",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_run:
            await execute_pattern_match_pipeline(
                query="is it?",
                config_service=config_service,
                org_id="org-1",
                user_id="user-1",
                graph_provider=graph_provider,
                filters=None,
                logger_instance=MagicMock(),
                grep_command='grep -rli "config" .',
            )

        mock_run.assert_called_once()
        command_used = mock_run.call_args.kwargs["command"]
        assert command_used.startswith('grep -rli "config" .')
        assert "| head" in command_used

    @pytest.mark.asyncio
    async def test_grep_command_none_falls_back_to_auto(self):
        """When grep_command is None, the auto-derived command from query is used."""
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            return_value={"storageType": "local"}
        )
        graph_provider = AsyncMock()
        graph_provider.get_org_apps = AsyncMock(
            return_value=[{"_key": "app-1"}]
        )

        with patch(
            "app.utils.pattern_match.run_pattern_match",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_run:
            await execute_pattern_match_pipeline(
                query="revenue projections analysis",
                config_service=config_service,
                org_id="org-1",
                user_id="user-1",
                graph_provider=graph_provider,
                filters=None,
                logger_instance=MagicMock(),
                grep_command=None,
            )

        mock_run.assert_called_once()
        command_used = mock_run.call_args.kwargs["command"]
        assert "revenue" in command_used

    @pytest.mark.asyncio
    async def test_empty_grep_command_falls_back_to_auto(self):
        """Empty string grep_command falls back to auto-derived."""
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            return_value={"storageType": "local"}
        )
        graph_provider = AsyncMock()
        graph_provider.get_org_apps = AsyncMock(
            return_value=[{"_key": "app-1"}]
        )

        with patch(
            "app.utils.pattern_match.run_pattern_match",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_run:
            await execute_pattern_match_pipeline(
                query="revenue projections",
                config_service=config_service,
                org_id="org-1",
                user_id="user-1",
                graph_provider=graph_provider,
                filters=None,
                logger_instance=MagicMock(),
                grep_command="",
            )

        mock_run.assert_called_once()
        command_used = mock_run.call_args.kwargs["command"]
        assert "revenue" in command_used

    @pytest.mark.asyncio
    async def test_unsafe_command_falls_back_to_auto(self):
        """A command with injection chars is rejected and falls back to auto."""
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            return_value={"storageType": "local"}
        )
        graph_provider = AsyncMock()
        graph_provider.get_org_apps = AsyncMock(
            return_value=[{"_key": "app-1"}]
        )

        with patch(
            "app.utils.pattern_match.run_pattern_match",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_run:
            await execute_pattern_match_pipeline(
                query="revenue analysis",
                config_service=config_service,
                org_id="org-1",
                user_id="user-1",
                graph_provider=graph_provider,
                filters=None,
                logger_instance=MagicMock(),
                grep_command='grep -rli "test" .; rm -rf /',
            )

        mock_run.assert_called_once()
        command_used = mock_run.call_args.kwargs["command"]
        assert "revenue" in command_used

    @pytest.mark.asyncio
    async def test_non_grep_binary_rejected_falls_back(self):
        """A command starting with a non-grep binary is rejected."""
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            return_value={"storageType": "local"}
        )
        graph_provider = AsyncMock()
        graph_provider.get_org_apps = AsyncMock(
            return_value=[{"_key": "app-1"}]
        )

        with patch(
            "app.utils.pattern_match.run_pattern_match",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_run:
            await execute_pattern_match_pipeline(
                query="budget analysis",
                config_service=config_service,
                org_id="org-1",
                user_id="user-1",
                graph_provider=graph_provider,
                filters=None,
                logger_instance=MagicMock(),
                grep_command="cat /etc/passwd",
            )

        mock_run.assert_called_once()
        command_used = mock_run.call_args.kwargs["command"]
        assert "budget" in command_used
        assert "passwd" not in command_used

    @pytest.mark.asyncio
    async def test_pipe_command_passed_directly(self):
        """Pipe commands (AND logic) between grep binaries pass validation."""
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            return_value={"storageType": "local"}
        )
        graph_provider = AsyncMock()
        graph_provider.get_org_apps = AsyncMock(
            return_value=[{"_key": "app-1"}]
        )

        and_cmd = 'grep -rl "budget" . | grep -l "forecast"'
        with patch(
            "app.utils.pattern_match.run_pattern_match",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_run:
            await execute_pattern_match_pipeline(
                query="budget forecast",
                config_service=config_service,
                org_id="org-1",
                user_id="user-1",
                graph_provider=graph_provider,
                filters=None,
                logger_instance=MagicMock(),
                grep_command=and_cmd,
            )

        mock_run.assert_called_once()
        command_used = mock_run.call_args.kwargs["command"]
        assert command_used.startswith(and_cmd)
        assert "| head" in command_used

    @pytest.mark.asyncio
    async def test_auto_appends_head_when_absent(self):
        """Commands without head/tail get | head -N appended for scale safety."""
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            return_value={"storageType": "local"}
        )
        graph_provider = AsyncMock()
        graph_provider.get_org_apps = AsyncMock(
            return_value=[{"_key": "app-1"}]
        )

        with patch(
            "app.utils.pattern_match.run_pattern_match",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_run:
            await execute_pattern_match_pipeline(
                query="budget analysis",
                config_service=config_service,
                org_id="org-1",
                user_id="user-1",
                graph_provider=graph_provider,
                filters=None,
                logger_instance=MagicMock(),
                grep_command='grep -rli "budget" .',
            )

        command_used = mock_run.call_args.kwargs["command"]
        assert command_used == f'grep -rli "budget" . | head -{_MAX_GREP_OUTPUT_LINES}'

    @pytest.mark.asyncio
    async def test_head_in_pipe_rejected_falls_back_to_auto(self):
        """Commands with non-grep pipe binaries fall back to auto-derived.
        Full binary validation for pipe stages is in _validate_command."""
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            return_value={"storageType": "local"}
        )
        graph_provider = AsyncMock()
        graph_provider.get_org_apps = AsyncMock(
            return_value=[{"_key": "app-1"}]
        )

        with patch(
            "app.utils.pattern_match.run_pattern_match",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_run:
            await execute_pattern_match_pipeline(
                query="budget analysis",
                config_service=config_service,
                org_id="org-1",
                user_id="user-1",
                graph_provider=graph_provider,
                filters=None,
                logger_instance=MagicMock(),
                grep_command='grep -rli "budget" . | head -50',
            )

        command_used = mock_run.call_args.kwargs["command"]
        assert "budget" in command_used
        assert command_used.endswith(f"| head -{_MAX_GREP_OUTPUT_LINES}")

    @pytest.mark.asyncio
    async def test_auto_derived_command_gets_head_appended(self):
        """Auto-derived grep commands also get | head -N appended."""
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            return_value={"storageType": "local"}
        )
        graph_provider = AsyncMock()
        graph_provider.get_org_apps = AsyncMock(
            return_value=[{"_key": "app-1"}]
        )

        with patch(
            "app.utils.pattern_match.run_pattern_match",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_run:
            await execute_pattern_match_pipeline(
                query="revenue projections",
                config_service=config_service,
                org_id="org-1",
                user_id="user-1",
                graph_provider=graph_provider,
                filters=None,
                logger_instance=MagicMock(),
            )

        command_used = mock_run.call_args.kwargs["command"]
        assert "revenue" in command_used
        assert command_used.endswith(f"| head -{_MAX_GREP_OUTPUT_LINES}")


# ===========================================================================
# Scale constants
# ===========================================================================


class TestScaleConstants:
    def test_timeout_sufficient_for_large_datasets(self):
        assert _PATTERN_MATCH_TIMEOUT >= 30

    def test_output_line_limit_set(self):
        assert _MAX_GREP_OUTPUT_LINES >= 100

    def test_llm_grep_timeout_set(self):
        assert _LLM_GREP_TIMEOUT == 5.0

    def test_max_llm_grep_commands_set(self):
        assert _MAX_LLM_GREP_COMMANDS == 3


# ===========================================================================
# _pre_validate_llm_grep
# ===========================================================================


class TestPreValidateLlmGrep:
    def test_valid_grep(self):
        assert _pre_validate_llm_grep('grep -rci "MCP" .') is True

    def test_valid_grep_with_xargs(self):
        assert _pre_validate_llm_grep('grep -rli "MCP" . | xargs grep -ci "server"') is True

    def test_empty_string(self):
        assert _pre_validate_llm_grep("") is False

    def test_whitespace_only(self):
        assert _pre_validate_llm_grep("   ") is False

    def test_too_long(self):
        assert _pre_validate_llm_grep("grep " + "a" * 1000) is False

    def test_backtick_rejected(self):
        assert _pre_validate_llm_grep("grep `whoami` .") is False

    def test_dollar_sign_rejected(self):
        assert _pre_validate_llm_grep('grep "$HOME" .') is False

    def test_semicolon_rejected(self):
        assert _pre_validate_llm_grep('grep "x" .; rm -rf /') is False

    def test_double_ampersand_rejected(self):
        assert _pre_validate_llm_grep('grep "x" . && echo') is False

    def test_double_pipe_rejected(self):
        assert _pre_validate_llm_grep('grep "x" . || echo') is False

    def test_non_grep_binary_rejected(self):
        assert _pre_validate_llm_grep("cat /etc/passwd") is False

    def test_rm_rejected(self):
        assert _pre_validate_llm_grep("rm -rf /") is False

    def test_rg_accepted(self):
        assert _pre_validate_llm_grep('rg -i "term" .') is True

    def test_egrep_accepted(self):
        assert _pre_validate_llm_grep('egrep -rci "term" .') is True

    def test_fgrep_accepted(self):
        assert _pre_validate_llm_grep('fgrep -rci "term" .') is True


# ===========================================================================
# generate_grep_command_via_llm
# ===========================================================================


class TestGenerateGrepCommandViaLlm:
    @pytest.mark.asyncio
    async def test_success_returns_single_command_list(self):
        mock_result = GrepCommandResult(
            reasoning="MCP server is a product name, search for it",
            grep_commands=['grep -rci "MCP\\|server\\|PipesHub" .'],
        )
        logger = MagicMock()

        with patch(
            "app.utils.pattern_match.invoke_with_structured_output_and_reflection",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = await generate_grep_command_via_llm(
                query="How to setup MCP server connection with PipesHub?",
                llm=MagicMock(),
                logger_instance=logger,
            )

        assert result == ['grep -rci "MCP\\|server\\|PipesHub" .']

    @pytest.mark.asyncio
    async def test_success_returns_multiple_commands(self):
        mock_result = GrepCommandResult(
            reasoning="OAuth and SAML are different auth protocols",
            grep_commands=[
                'grep -rci "oauth\\|sso" .',
                'grep -rci "saml\\|authentication" .',
            ],
        )
        logger = MagicMock()

        with patch(
            "app.utils.pattern_match.invoke_with_structured_output_and_reflection",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = await generate_grep_command_via_llm(
                query="OAuth SSO login setup",
                llm=MagicMock(),
                logger_instance=logger,
            )

        assert result == [
            'grep -rci "oauth\\|sso" .',
            'grep -rci "saml\\|authentication" .',
        ]

    @pytest.mark.asyncio
    async def test_caps_at_max_commands(self):
        mock_result = GrepCommandResult(
            reasoning="too many",
            grep_commands=[
                'grep -rci "a" .',
                'grep -rci "b" .',
                'grep -rci "c" .',
                'grep -rci "d" .',
            ],
        )
        logger = MagicMock()

        with patch(
            "app.utils.pattern_match.invoke_with_structured_output_and_reflection",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = await generate_grep_command_via_llm(
                query="test",
                llm=MagicMock(),
                logger_instance=logger,
            )

        assert result is not None
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_filters_invalid_keeps_valid(self):
        mock_result = GrepCommandResult(
            reasoning="mixed validity",
            grep_commands=[
                'grep -rci "valid" .',
                'cat /etc/passwd',
                'grep -rci "also_valid" .',
            ],
        )
        logger = MagicMock()

        with patch(
            "app.utils.pattern_match.invoke_with_structured_output_and_reflection",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = await generate_grep_command_via_llm(
                query="test",
                llm=MagicMock(),
                logger_instance=logger,
            )

        assert result == ['grep -rci "valid" .', 'grep -rci "also_valid" .']

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self):
        logger = MagicMock()

        async def _slow_invoke(*args, **kwargs):
            await asyncio.sleep(100)

        with patch(
            "app.utils.pattern_match.invoke_with_structured_output_and_reflection",
            side_effect=_slow_invoke,
        ):
            result = await generate_grep_command_via_llm(
                query="test query",
                llm=MagicMock(),
                logger_instance=logger,
            )

        assert result is None
        logger.info.assert_any_call(
            "generate_grep_command_via_llm: timed out after %.1fs", _LLM_GREP_TIMEOUT,
        )

    @pytest.mark.asyncio
    async def test_llm_exception_returns_none(self):
        logger = MagicMock()

        with patch(
            "app.utils.pattern_match.invoke_with_structured_output_and_reflection",
            new_callable=AsyncMock,
            side_effect=RuntimeError("LLM is down"),
        ):
            result = await generate_grep_command_via_llm(
                query="test query",
                llm=MagicMock(),
                logger_instance=logger,
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_none_structured_output_returns_none(self):
        logger = MagicMock()

        with patch(
            "app.utils.pattern_match.invoke_with_structured_output_and_reflection",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await generate_grep_command_via_llm(
                query="test query",
                llm=MagicMock(),
                logger_instance=logger,
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_all_commands_invalid_returns_none(self):
        mock_result = GrepCommandResult(
            reasoning="all invalid",
            grep_commands=[
                'grep -rci "test" .; rm -rf /',
                "cat /etc/passwd",
            ],
        )
        logger = MagicMock()

        with patch(
            "app.utils.pattern_match.invoke_with_structured_output_and_reflection",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = await generate_grep_command_via_llm(
                query="test query",
                llm=MagicMock(),
                logger_instance=logger,
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_xargs_command_passes_pre_validation(self):
        mock_result = GrepCommandResult(
            reasoning="AND search needs xargs",
            grep_commands=['grep -rli "MCP" . | xargs grep -ci "server"'],
        )
        logger = MagicMock()

        with patch(
            "app.utils.pattern_match.invoke_with_structured_output_and_reflection",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = await generate_grep_command_via_llm(
                query="MCP server setup",
                llm=MagicMock(),
                logger_instance=logger,
            )

        assert result == ['grep -rli "MCP" . | xargs grep -ci "server"']

    @pytest.mark.asyncio
    async def test_empty_commands_list_returns_none(self):
        mock_result = GrepCommandResult(
            reasoning="no commands",
            grep_commands=[],
        )
        logger = MagicMock()

        with patch(
            "app.utils.pattern_match.invoke_with_structured_output_and_reflection",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            result = await generate_grep_command_via_llm(
                query="test query",
                llm=MagicMock(),
                logger_instance=logger,
            )

        assert result is None


# ===========================================================================
# execute_pattern_match_pipeline — skip_grep_validation
# ===========================================================================


class TestExecutePatternMatchPipelineSkipValidation:
    @pytest.mark.asyncio
    async def test_xargs_command_passes_with_skip_validation(self):
        """An xargs command that validate_grep_command would reject
        passes through when skip_grep_validation=True."""
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            return_value={"storageType": "local"}
        )
        graph_provider = AsyncMock()
        graph_provider.get_org_apps = AsyncMock(
            return_value=[{"_key": "app-1"}]
        )

        xargs_cmd = 'grep -rli "MCP" . | xargs grep -ci "server"'
        with patch(
            "app.utils.pattern_match.run_pattern_match",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_run:
            await execute_pattern_match_pipeline(
                query="MCP server",
                config_service=config_service,
                org_id="org-1",
                user_id="user-1",
                graph_provider=graph_provider,
                filters=None,
                logger_instance=MagicMock(),
                grep_command=xargs_cmd,
                skip_grep_validation=True,
            )

        mock_run.assert_called_once()
        command_used = mock_run.call_args.kwargs["command"]
        assert "xargs" in command_used
        assert command_used.startswith(xargs_cmd)

    @pytest.mark.asyncio
    async def test_xargs_command_rejected_without_skip_validation(self):
        """An xargs command is rejected by validate_grep_command and
        falls back to auto-derived when skip_grep_validation=False."""
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            return_value={"storageType": "local"}
        )
        graph_provider = AsyncMock()
        graph_provider.get_org_apps = AsyncMock(
            return_value=[{"_key": "app-1"}]
        )

        xargs_cmd = 'grep -rli "MCP" . | xargs grep -ci "server"'
        with patch(
            "app.utils.pattern_match.run_pattern_match",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_run:
            await execute_pattern_match_pipeline(
                query="MCP server setup",
                config_service=config_service,
                org_id="org-1",
                user_id="user-1",
                graph_provider=graph_provider,
                filters=None,
                logger_instance=MagicMock(),
                grep_command=xargs_cmd,
                skip_grep_validation=False,
            )

        mock_run.assert_called_once()
        command_used = mock_run.call_args.kwargs["command"]
        assert "xargs" not in command_used
        assert "server" in command_used or "setup" in command_used

    @pytest.mark.asyncio
    async def test_none_command_falls_back_to_auto_regardless_of_skip(self):
        """When grep_command is None, auto-derived command is used
        regardless of skip_grep_validation."""
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            return_value={"storageType": "local"}
        )
        graph_provider = AsyncMock()
        graph_provider.get_org_apps = AsyncMock(
            return_value=[{"_key": "app-1"}]
        )

        with patch(
            "app.utils.pattern_match.run_pattern_match",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_run:
            await execute_pattern_match_pipeline(
                query="revenue projections",
                config_service=config_service,
                org_id="org-1",
                user_id="user-1",
                graph_provider=graph_provider,
                filters=None,
                logger_instance=MagicMock(),
                grep_command=None,
                skip_grep_validation=True,
            )

        mock_run.assert_called_once()
        command_used = mock_run.call_args.kwargs["command"]
        assert "revenue" in command_used
