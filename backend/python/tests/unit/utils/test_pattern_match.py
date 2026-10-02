"""Tests for app.utils.pattern_match — shared pattern match helpers."""

import asyncio
import contextlib
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.storage_search.storage_search import _validate_command
from app.utils.pattern_match import (
    DEFAULT_PATTERN_MATCH_BLOCK_BUDGET,
    GrepCommandResult,
    _LLM_GREP_TIMEOUT,
    _MAX_GREP_OUTPUT_LINES,
    _MAX_LLM_GREP_COMMANDS,
    _MAX_SCOPED_SEARCH_PATHS,
    _PATTERN_MATCH_TIMEOUT,
    _build_synthetic_search_results,
    _ensure_null_delimited_pipeline,
    _split_pipeline,
    _fetch_pattern_record,
    _get_frontend_url,
    _record_in_time_range,
    _scope_grep_to_paths,
    await_pattern_match,
    build_grep_command_from_query,
    cancel_task_if_running,
    cap_pattern_match_blocks,
    check_pattern_match_eligible,
    execute_pattern_match_pipeline,
    generate_grep_command_via_llm,
    merge_pattern_match_results,
    render_pattern_match_hint,
    resolve_connector_ids_for_search,
    run_pattern_match,
    run_pattern_match_with_llm_grep,
    validate_grep_command,
    validate_pattern_match_command,
)


# ===========================================================================
# build_grep_command_from_query
# ===========================================================================


class TestBuildGrepCommand:
    def test_keyword_starting_with_dash_is_passed_with_e(self):
        cmd = build_grep_command_from_query("-rf delete")
        assert cmd == 'grep -rci -e "-rf\\|delete" .'
        assert _validate_command(cmd)[0] is True

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

    def test_bare_dollar_allowed_since_no_shell_expands_it(self):
        assert validate_grep_command('grep -rli "$HOME" .') == 'grep -rli "$HOME" .'

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
        # Caught per connector, so one crash cannot discard the others' results.
        assert any(
            args[0] == "pattern_match: grep raised for cid=%s" and args[1] == "conn-2"
            for args, _ in logger.warning.call_args_list
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
        graph_provider.filter_accessible_virtual_record_ids = AsyncMock(
            return_value={"vr-1": "rec-1", "vr-2": "rec-2"}
        )
        graph_provider.get_document = AsyncMock(return_value={"_key": "rec-1"})

        blob_store = AsyncMock()
        blob_store.config_service = AsyncMock()
        blob_store.config_service.get_config = AsyncMock(return_value={})

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

        assert graph_provider.filter_accessible_virtual_record_ids.call_count == 1
        called_vrids = graph_provider.filter_accessible_virtual_record_ids.call_args[0][0]
        assert len(called_vrids) == 2

    @pytest.mark.asyncio
    async def test_skips_already_in_semantic_results(self):
        raw = [
            {"virtual_record_id": "vr-1"},
            {"virtual_record_id": "vr-2"},
        ]
        existing = {"vr-1": {"some": "data"}}

        graph_provider = AsyncMock()
        graph_provider.filter_accessible_virtual_record_ids = AsyncMock(
            return_value={"vr-2": "rec-2"}
        )
        graph_provider.get_document = AsyncMock(return_value={"_key": "rec-2"})

        blob_store = AsyncMock()
        blob_store.config_service = AsyncMock()
        blob_store.config_service.get_config = AsyncMock(return_value={})

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

        called_vrids = graph_provider.filter_accessible_virtual_record_ids.call_args[0][0]
        assert "vr-1" not in called_vrids
        assert "vr-2" in called_vrids

    @pytest.mark.asyncio
    async def test_no_accessible_returns_empty(self):
        raw = [{"virtual_record_id": "vr-1"}]
        graph_provider = AsyncMock()
        graph_provider.filter_accessible_virtual_record_ids = AsyncMock(return_value={})

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
    async def test_returns_metadata_entries_for_accessible_records(self):
        raw = [{"virtual_record_id": "vr-1"}]
        graph_rec = {
            "_key": "rec-1",
            "indexingStatus": "COMPLETED",
            "title": "Revenue Report",
            "recordType": "FILE",
            "appName": "Google Drive",
        }
        graph_provider = AsyncMock()
        graph_provider.filter_accessible_virtual_record_ids = AsyncMock(
            return_value={"vr-1": "rec-1"}
        )
        graph_provider.get_records_by_record_ids = AsyncMock(
            return_value=[graph_rec]
        )

        vr_map: dict[str, dict] = {}
        result = await merge_pattern_match_results(
            raw_records=raw,
            virtual_record_id_to_result=vr_map,
            user_id="user-1",
            org_id="org-1",
            blob_store=AsyncMock(),
            graph_provider=graph_provider,
            is_multimodal_llm=False,
            logger_instance=MagicMock(),
        )

        assert len(result) == 1
        assert result[0]["virtual_record_id"] == "vr-1"
        assert result[0]["source"] == "pattern_match"
        assert result[0]["score"] == 0.0
        assert vr_map["vr-1"] == graph_rec

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_graph_records_found(self):
        raw = [{"virtual_record_id": "vr-1"}]
        graph_provider = AsyncMock()
        graph_provider.filter_accessible_virtual_record_ids = AsyncMock(
            return_value={"vr-1": "rec-1"}
        )
        graph_provider.get_records_by_record_ids = AsyncMock(return_value=[])

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
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            return_value={"storageType": "local"}
        )
        result = await execute_pattern_match_pipeline(
            query="is it?",
            config_service=config_service,
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
    async def test_s3_skips_grep_command_processing(self):
        """When storage is S3, pipeline returns [] immediately without
        processing the grep_command — no redundant validation or keyword
        extraction occurs."""
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            return_value={"storageType": "s3"}
        )
        with patch(
            "app.utils.pattern_match.run_pattern_match",
            new_callable=AsyncMock,
        ) as mock_run:
            result = await execute_pattern_match_pipeline(
                query="revenue projections",
                config_service=config_service,
                org_id="org-1",
                user_id="user-1",
                graph_provider=AsyncMock(),
                filters=None,
                logger_instance=MagicMock(),
                grep_command='grep -rci "revenue" .',
            )
        assert result == []
        mock_run.assert_not_called()

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


class TestAwaitPatternMatch:
    """Semantic results must survive whatever happens inside pattern match."""

    @pytest.mark.asyncio
    async def test_records_are_returned(self):
        async def _ok():
            return [{"_key": "r1"}]

        assert await await_pattern_match(asyncio.ensure_future(_ok()), MagicMock()) == [{"_key": "r1"}]

    @pytest.mark.asyncio
    async def test_none_is_no_records(self):
        async def _none():
            return None

        assert await await_pattern_match(asyncio.ensure_future(_none()), MagicMock()) == []

    @pytest.mark.asyncio
    async def test_an_error_is_no_records(self):
        async def _boom():
            raise RuntimeError("grep blew up")

        assert await await_pattern_match(asyncio.ensure_future(_boom()), MagicMock()) == []

    @pytest.mark.asyncio
    async def test_a_cancellation_inside_pattern_match_is_no_records(self):
        async def _cancelled():
            raise asyncio.CancelledError()

        assert await await_pattern_match(asyncio.ensure_future(_cancelled()), MagicMock()) == []

    @pytest.mark.asyncio
    async def test_cancelling_the_caller_still_cancels_it(self):
        started = asyncio.Event()

        async def _slow():
            started.set()
            await asyncio.sleep(100)

        async def _caller():
            return await await_pattern_match(asyncio.ensure_future(_slow()), MagicMock())

        caller = asyncio.ensure_future(_caller())
        await started.wait()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller


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
        # Zero-count lines are dropped before the cap so real matches are not crowded out.
        assert command_used == (
            f'grep -rli "budget" . | grep -v \':0$\' | head -{_MAX_GREP_OUTPUT_LINES}'
        )

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
        assert _LLM_GREP_TIMEOUT == 15.0

    def test_max_llm_grep_commands_set(self):
        assert _MAX_LLM_GREP_COMMANDS == 3


# ===========================================================================
# validate_pattern_match_command
# ===========================================================================


class TestValidatePatternMatchCommand:
    @pytest.mark.parametrize("cmd", [
        'grep -rci "MCP" .',
        'grep -rli "MCP" . | xargs grep -ci "server"',
        'grep -rci "deploy\\|deployment\\|ci.cd\\|pipeline" .',
        'grep -rliZ "invoice\\|billing" . | xargs -0 grep -Hci "recurring\\|subscri"',
        'grep -rliZ "hierarch" . | xargs -0 grep -liZ "storage" | xargs -0 grep -Hci "pattern"',
        'grep "$HOME" .',
        'rg -i "term" .',
        'egrep -rci "term" .',
        'fgrep -rci "term" .',
        'grep -rci -e "-rf" .',
    ])
    def test_accepts_prompt_shaped_commands(self, cmd):
        assert validate_pattern_match_command(cmd) == (True, "")

    @pytest.mark.parametrize("cmd", [
        "",
        "   ",
        "grep " + "a" * 1000,
        "grep `whoami` .",
        'grep "$(id)" .',
        'grep "x" .; rm -rf /',
        'grep "x" . && echo',
        'grep "x" . || echo',
        "cat /etc/passwd",
        "rm -rf /",
        'grep -rP "(a+)+b" .',
        'grep -r --perl-regexp "x" .',
        'grep -r "\\(a\\)\\1" .',
        "grep -rf patterns.txt .",
        'rg --pre cat "x" .',
        'grep -r "x" ./secret',
        'grep -r "x" . | xargs -0 cat',
        'grep -rl "x" . | sort',
        'grep -rl "x" . | xargs -I{} grep "y" {}',
        'grep -rl "x" . | grep -r "y"',
        'grep -rlZ "a" . | xargs -0 grep -lZ "b" | xargs -0 grep -lZ "c" | xargs -0 grep -c "d"',
        'xargs grep "x"',
        'find . -name "*.json"',
        'grep -rR "x" .',
        'grep -rli "x" ./',
        'grep -rli "x" "."',
        'grep -rli "x" . .',
        'grep -rli "x"',
    ])
    def test_rejects_everything_else(self, cmd):
        ok, reason = validate_pattern_match_command(cmd)
        assert ok is False
        assert reason


# ===========================================================================
# generate_grep_command_via_llm
# ===========================================================================


def _mock_structured_llm(return_value=None, side_effect=None):
    """Create a mock LLM that ``_apply_structured_output`` returns unchanged,
    with ``ainvoke`` returning *return_value* or raising *side_effect*."""
    structured_llm = AsyncMock()
    if side_effect is not None:
        structured_llm.ainvoke = AsyncMock(side_effect=side_effect)
    else:
        structured_llm.ainvoke = AsyncMock(return_value=return_value)
    return structured_llm


def _grep_llm_patches(structured_llm):
    """Context manager that patches ``_apply_structured_output`` and
    ``build_langchain_opik_callbacks`` for grep LLM tests."""
    return contextlib.ExitStack()


class TestGenerateGrepCommandViaLlm:
    def _patches(self, structured_llm):
        """Return stacked patches for the new generate_grep_command_via_llm internals."""
        stack = contextlib.ExitStack()
        stack.enter_context(
            patch(
                "app.utils.streaming._apply_structured_output",
                return_value=structured_llm,
            )
        )
        stack.enter_context(
            patch(
                "app.agent_loop_lib.transport.opik_tracing.build_langchain_opik_callbacks",
                return_value=[],
            )
        )
        return stack

    @pytest.mark.asyncio
    async def test_strictly_rejected_commands_are_dropped(self):
        mock_result = GrepCommandResult(
            reasoning="r",
            grep_commands=['grep -rP "(a+)+b" .', 'grep -rl "x" . | xargs -0 cat'],
        )
        structured_llm = _mock_structured_llm(return_value=mock_result)

        with self._patches(structured_llm):
            result = await generate_grep_command_via_llm(
                query="q", llm=MagicMock(), logger_instance=MagicMock(),
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_success_returns_single_command_list(self):
        mock_result = GrepCommandResult(
            reasoning="MCP server is a product name, search for it",
            grep_commands=['grep -rci "MCP\\|server\\|PipesHub" .'],
        )
        structured_llm = _mock_structured_llm(return_value=mock_result)
        logger = MagicMock()

        with self._patches(structured_llm):
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
        structured_llm = _mock_structured_llm(return_value=mock_result)
        logger = MagicMock()

        with self._patches(structured_llm):
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
        structured_llm = _mock_structured_llm(return_value=mock_result)
        logger = MagicMock()

        with self._patches(structured_llm):
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
        structured_llm = _mock_structured_llm(return_value=mock_result)
        logger = MagicMock()

        with self._patches(structured_llm):
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

        structured_llm = _mock_structured_llm(side_effect=_slow_invoke)
        with self._patches(structured_llm):
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
        structured_llm = _mock_structured_llm(side_effect=RuntimeError("LLM is down"))

        with self._patches(structured_llm):
            result = await generate_grep_command_via_llm(
                query="test query",
                llm=MagicMock(),
                logger_instance=logger,
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_none_structured_output_returns_none(self):
        logger = MagicMock()
        structured_llm = _mock_structured_llm(return_value=None)

        with self._patches(structured_llm):
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
        structured_llm = _mock_structured_llm(return_value=mock_result)
        logger = MagicMock()

        with self._patches(structured_llm):
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
        structured_llm = _mock_structured_llm(return_value=mock_result)
        logger = MagicMock()

        with self._patches(structured_llm):
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
        structured_llm = _mock_structured_llm(return_value=mock_result)
        logger = MagicMock()

        with self._patches(structured_llm):
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
        assert "xargs -0" in command_used
        assert command_used.startswith('grep -rliZ "MCP" . | xargs -0 grep -ciH "server"')

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


# ===========================================================================
# render_pattern_match_hint
# ===========================================================================


class TestRenderPatternMatchHint:
    def test_empty_records_returns_empty(self):
        assert render_pattern_match_hint([], {}) == ""

    def test_single_record_renders_full_metadata(self):
        entries = [{"virtual_record_id": "vrid-1", "score": 0.0, "source": "pattern_match"}]
        vr_map = {
            "vrid-1": {
                "id": "rec-123",
                "recordName": "Revenue Report Q3",
                "recordType": "FILE",
                "connectorName": "DRIVE",
                "externalRecordId": "ext-99",
                "connectorId": "conn-1",
                "mimeType": "application/pdf",
                "webUrl": "https://drive.google.com/doc/123",
                "sourceCreatedAtTimestamp": 1631348507000,
                "sourceLastModifiedTimestamp": 1631348507000,
                "externalParentId": "parent-1",
                "summary": "Quarterly revenue report for Q3",
                "topics": ["revenue", "finance"],
                "categories": ["Business"],
                "subCategoryLevel1": "Finance",
            },
        }

        result = render_pattern_match_hint(entries, vr_map)

        assert "<record>" in result
        assert "</record>" in result
        assert "Record ID: rec-123" in result
        assert "Name: Revenue Report Q3" in result
        assert "Type: FILE" in result
        assert "Connector: DRIVE" in result
        assert "External ID: ext-99" in result
        assert "Created At: 2021-09-11" in result
        assert "Last Updated At: 2021-09-11" in result
        assert "Connector ID: conn-1" in result
        assert "External Parent ID: parent-1" in result
        assert "MIME Type: application/pdf" in result
        assert "Web URL: https://drive.google.com/doc/123" in result
        assert "Summary: Quarterly revenue report" in result
        assert "Topics:" in result
        assert "Category: Business > Finance" in result
        assert "File Information:" in result
        assert "Extension: pdf" in result
        assert "knowledgegraph__fetch_record" in result
        assert "metadata only" in result

    def test_multiple_records(self):
        entries = [
            {"virtual_record_id": "vrid-1"},
            {"virtual_record_id": "vrid-2"},
        ]
        vr_map = {
            "vrid-1": {"id": "rec-1", "recordName": "Doc A", "recordType": "file"},
            "vrid-2": {"_key": "rec-2", "recordName": "Doc B", "recordType": "file"},
        }

        result = render_pattern_match_hint(entries, vr_map)

        assert result.count("<record>") == 2
        assert "rec-1" in result
        assert "rec-2" in result

    def test_skips_entries_missing_from_vr_map(self):
        entries = [
            {"virtual_record_id": "vrid-1"},
            {"virtual_record_id": "vrid-missing"},
        ]
        vr_map = {
            "vrid-1": {"id": "rec-1", "recordName": "Doc A"},
        }

        result = render_pattern_match_hint(entries, vr_map)

        assert result.count("<record>") == 1
        assert "rec-1" in result

    def test_caps_at_max_records(self):
        entries = [{"virtual_record_id": f"vrid-{i}"} for i in range(10)]
        vr_map = {
            f"vrid-{i}": {"id": f"rec-{i}", "recordName": f"Doc {i}"}
            for i in range(10)
        }

        result = render_pattern_match_hint(entries, vr_map, max_records=3)

        assert result.count("<record>") == 3
        assert "rec-0" in result
        assert "rec-2" in result
        assert "rec-3" not in result

    def test_returns_empty_when_no_vr_map_matches(self):
        entries = [{"virtual_record_id": "vrid-gone"}]
        vr_map = {}

        result = render_pattern_match_hint(entries, vr_map)

        assert result == ""


# ===========================================================================
# _scope_grep_to_paths
# ===========================================================================


class TestScopeGrepToPaths:
    def test_replaces_dot_in_simple_grep(self):
        cmd = 'grep -rci "revenue" .'
        result = _scope_grep_to_paths(cmd, ["./Sales", "./Finance"])
        assert '"./Sales"' in result
        assert '"./Finance"' in result
        assert result.endswith('"./Finance"')

    def test_replaces_dot_in_piped_command(self):
        cmd = 'grep -rli "term" . | xargs grep -ci "pattern"'
        result = _scope_grep_to_paths(cmd, ["./A", "./B"])
        assert '"./A"' in result
        assert '"./B"' in result
        assert '| xargs grep -ci "pattern"' in result

    def test_preserves_command_when_no_dot(self):
        cmd = 'grep -ci "term" somefile.json'
        result = _scope_grep_to_paths(cmd, ["./A"])
        assert result == cmd

    def test_replaces_space_dot_space(self):
        cmd = 'grep -rli "hello" . | xargs grep -ci "world"'
        result = _scope_grep_to_paths(cmd, ["./X"])
        assert '"./X"' in result
        assert ". " not in result.split("|")[0]

    def test_single_path(self):
        cmd = 'grep -rci "test" .'
        result = _scope_grep_to_paths(cmd, ["./OnlyGroup"])
        assert '"./OnlyGroup"' in result

    def test_pipe_in_pattern_not_split(self):
        cmd = 'egrep -rli "error|warning" . | xargs grep -ci "critical"'
        result = _scope_grep_to_paths(cmd, ["./Logs"])
        assert '"./Logs"' in result
        assert "error|warning" in result
        assert "xargs grep" in result


# ===========================================================================
# _split_pipeline
# ===========================================================================


class TestSplitPipeline:
    def test_simple_command(self):
        assert _split_pipeline('grep -rci "test" .') == ['grep -rci "test" .']

    def test_piped_command(self):
        stages = _split_pipeline('grep -rli "term" . | xargs grep -ci "pattern"')
        assert len(stages) == 2
        assert stages[0].strip() == 'grep -rli "term" .'
        assert stages[1].strip() == 'xargs grep -ci "pattern"'

    def test_pipe_inside_double_quotes(self):
        stages = _split_pipeline('egrep -rci "pat1|pat2" .')
        assert len(stages) == 1
        assert "pat1|pat2" in stages[0]

    def test_pipe_inside_single_quotes(self):
        stages = _split_pipeline("egrep -rci 'pat1|pat2' .")
        assert len(stages) == 1

    def test_pipe_in_pattern_and_in_pipeline(self):
        stages = _split_pipeline('egrep -rli "a|b" . | xargs grep -ci "c"')
        assert len(stages) == 2
        assert "a|b" in stages[0]
        assert "xargs" in stages[1]

    def test_empty_command(self):
        assert _split_pipeline("") == [""]

    def test_multiple_pipes(self):
        stages = _split_pipeline('grep -rli "x" . | xargs grep -li "y" | xargs grep -ci "z"')
        assert len(stages) == 3


# ===========================================================================
# run_pattern_match — scoped grep integration
# ===========================================================================


class TestRunPatternMatchScopedGrep:
    """Tests for record-group-scoped grep in run_pattern_match.

    Scoping engages only for trusted record groups whose directories exist
    under the connector; it replaces the full grep, and its results are still
    checked per record downstream.
    """

    def _make_record(self, vrid, count=1):
        return {"virtual_record_id": vrid, "match_count": count}

    @staticmethod
    def _scoping_graph(tmp_path, groups_by_connector, trusted_ids):
        from app.services.graph_db.interface.graph_db_provider import AccessibleContainers

        graph = AsyncMock()
        graph.get_accessible_containers = AsyncMock(
            return_value=AccessibleContainers(record_group_ids_trusted=frozenset(trusted_ids)),
        )

        graph.get_nodes_by_field_in = AsyncMock(return_value=[
            {"id": rg["id"], "groupName": rg["group_name"], "connectorId": cid, "orgId": "org1"}
            for cid, rgs in groups_by_connector.items() for rg in rgs
        ])
        names = {
            rg["id"]: rg["group_name"]
            for rgs in groups_by_connector.values() for rg in rgs
        }
        graph.get_record_group_path = AsyncMock(side_effect=lambda rg_id, **_: [names[rg_id]])
        for rgs in groups_by_connector.values():
            for rg in rgs:
                (tmp_path / rg["group_name"]).mkdir(exist_ok=True)
        return graph

    @staticmethod
    def _tool(tmp_path, mock_find):
        instance = MagicMock()
        instance.find_records = AsyncMock(side_effect=mock_find)
        instance._resolve_connector_path = AsyncMock(return_value=(str(tmp_path), None))
        return instance

    @pytest.mark.asyncio
    async def test_scoped_grep_used_when_rgs_are_trusted(self, tmp_path):
        graph = self._scoping_graph(
            tmp_path, {"c1": [{"id": "rg1", "group_name": "Engineering"}]}, {"rg1"},
        )

        async def mock_find(connector_id, command, **_kwargs):
            if '"./Engineering"' in command:
                return (True, json.dumps({"records": [self._make_record("vr-1")]}))
            return (True, json.dumps({"records": [self._make_record("vr-outside")]}))

        with patch("app.utils.pattern_match.StoragePatternMatch") as MockSPM:
            MockSPM.return_value = self._tool(tmp_path, mock_find)
            with patch("app.utils.pattern_match._validate_command", return_value=(True, None)):
                result = await run_pattern_match(
                    config_service=MagicMock(),
                    org_id="org1",
                    user_id="user1",
                    graph_provider=graph,
                    command='grep -rci "test" .',
                    connector_ids=["c1"],
                    logger_instance=MagicMock(),
                )

        assert [r["virtual_record_id"] for r in result] == ["vr-1"]

    @pytest.mark.asyncio
    async def test_falls_back_to_full_grep_when_no_rgs(self):
        config = MagicMock()
        graph = AsyncMock()
        log = MagicMock()

        full_records = json.dumps({"records": [self._make_record("vr-full")]})

        with patch("app.utils.pattern_match.StoragePatternMatch") as MockSPM:
            instance = MagicMock()
            instance.find_records = AsyncMock(return_value=(True, full_records))
            MockSPM.return_value = instance

            with patch("app.utils.pattern_match._validate_command", return_value=(True, None)):
                result = await run_pattern_match(
                    config_service=config,
                    org_id="org1",
                    user_id="user1",
                    graph_provider=graph,
                    command='grep -rci "test" .',
                    connector_ids=["c1"],
                    logger_instance=log,
                )

        assert len(result) == 1
        assert result[0]["virtual_record_id"] == "vr-full"

    @pytest.mark.asyncio
    async def test_falls_back_when_too_many_rgs(self):
        rgs = [{"id": f"rg{i}", "group_name": f"Group {i}"} for i in range(_MAX_SCOPED_SEARCH_PATHS + 5)]
        config = MagicMock()
        graph = AsyncMock()
        log = MagicMock()

        full_records = json.dumps({"records": [self._make_record("vr-full")]})

        with patch("app.utils.pattern_match.StoragePatternMatch") as MockSPM:
            instance = MagicMock()
            instance.find_records = AsyncMock(return_value=(True, full_records))
            MockSPM.return_value = instance

            with patch("app.utils.pattern_match._validate_command", return_value=(True, None)):
                result = await run_pattern_match(
                    config_service=config,
                    org_id="org1",
                    user_id="user1",
                    graph_provider=graph,
                    command='grep -rci "test" .',
                    connector_ids=["c1"],
                    logger_instance=log,
                )

        assert len(result) == 1
        assert result[0]["virtual_record_id"] == "vr-full"

    @pytest.mark.asyncio
    async def test_falls_back_when_rg_lookup_raises(self):
        config = MagicMock()
        graph = AsyncMock()
        graph.get_nodes_by_field_in = AsyncMock(side_effect=RuntimeError("db down"))
        log = MagicMock()

        full_records = json.dumps({"records": [self._make_record("vr-fallback")]})

        with patch("app.utils.pattern_match.StoragePatternMatch") as MockSPM:
            instance = MagicMock()
            instance.find_records = AsyncMock(return_value=(True, full_records))
            MockSPM.return_value = instance

            with patch("app.utils.pattern_match._validate_command", return_value=(True, None)):
                result = await run_pattern_match(
                    config_service=config,
                    org_id="org1",
                    user_id="user1",
                    graph_provider=graph,
                    command='grep -rci "test" .',
                    connector_ids=["c1"],
                    logger_instance=log,
                )

        assert len(result) == 1
        assert result[0]["virtual_record_id"] == "vr-fallback"

    @pytest.mark.asyncio
    async def test_successful_scoped_grep_does_not_also_run_full_grep(self, tmp_path):
        graph = self._scoping_graph(
            tmp_path, {"c1": [{"id": "rg1", "group_name": "Shared"}]}, {"rg1"},
        )

        async def mock_find(connector_id, command, **_kwargs):
            return (True, json.dumps({"records": [self._make_record("vr-dup", count=5)]}))

        with patch("app.utils.pattern_match.StoragePatternMatch") as MockSPM:
            tool = self._tool(tmp_path, mock_find)
            MockSPM.return_value = tool
            with patch("app.utils.pattern_match._validate_command", return_value=(True, None)):
                result = await run_pattern_match(
                    config_service=MagicMock(),
                    org_id="org1",
                    user_id="user1",
                    graph_provider=graph,
                    command='grep -rci "test" .',
                    connector_ids=["c1"],
                    logger_instance=MagicMock(),
                )

        assert [r["virtual_record_id"] for r in result] == ["vr-dup"]
        tool.find_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_multiple_connectors_scoped_independently(self, tmp_path):
        graph = self._scoping_graph(
            tmp_path, {"c1": [{"id": "rg1", "group_name": "Team A"}]}, {"rg1"},
        )
        commands: dict[str, str] = {}

        async def mock_find(connector_id, command, **_kwargs):
            commands[connector_id] = command
            return (True, json.dumps({"records": [{"virtual_record_id": f"vr-{connector_id}"}]}))

        with patch("app.utils.pattern_match.StoragePatternMatch") as MockSPM:
            MockSPM.return_value = self._tool(tmp_path, mock_find)
            with patch("app.utils.pattern_match._validate_command", return_value=(True, None)):
                result = await run_pattern_match(
                    config_service=MagicMock(),
                    org_id="org1",
                    user_id="user1",
                    graph_provider=graph,
                    command='grep -rci "test" .',
                    connector_ids=["c1", "c2"],
                    logger_instance=MagicMock(),
                )

        assert {r["virtual_record_id"] for r in result} == {"vr-c1", "vr-c2"}
        assert '"./Team A"' in commands["c1"]
        assert commands["c2"] == 'grep -rci "test" .'


# ===========================================================================
# APP_LEVEL permission model integration
# ===========================================================================


class TestRunPatternMatchPermissionModel:
    """Tests that the permission model correctly routes APP_LEVEL connectors
    to full grep and non-APP_LEVEL to scoped grep."""

    def _make_record(self, vrid, count=1):
        return {"virtual_record_id": vrid, "match_count": count}

    def _make_containers(self, *, app_ids_trusted=frozenset(), fallback_reason=None):
        from app.services.graph_db.interface.graph_db_provider import AccessibleContainers
        return AccessibleContainers(
            app_ids_trusted=app_ids_trusted,
            fallback_reason=fallback_reason,
        )

    @pytest.mark.asyncio
    async def test_app_level_connector_skips_rg_lookup(self):
        """APP_LEVEL connectors should run full grep without looking up record groups."""
        config = MagicMock()
        graph = AsyncMock()
        graph.get_accessible_containers = AsyncMock(
            return_value=self._make_containers(app_ids_trusted=frozenset({"c1"})),
        )
        log = MagicMock()

        full_records = json.dumps({"records": [self._make_record("vr-app")]})

        with patch("app.utils.pattern_match.StoragePatternMatch") as MockSPM:
            instance = MagicMock()
            instance.find_records = AsyncMock(return_value=(True, full_records))
            MockSPM.return_value = instance

            with patch("app.utils.pattern_match._validate_command", return_value=(True, None)):
                result = await run_pattern_match(
                    config_service=config,
                    org_id="org1",
                    user_id="user1",
                    graph_provider=graph,
                    command='grep -rci "test" .',
                    connector_ids=["c1"],
                    logger_instance=log,
                )

        assert len(result) == 1
        assert result[0]["virtual_record_id"] == "vr-app"
        assert instance.find_records.await_args.kwargs["command"] == 'grep -rci "test" .'
        graph.get_nodes_by_field_in.assert_not_called()

    @staticmethod
    def _scoping_setup(tmp_path, *, app_level=frozenset(), trusted=frozenset(), verify=frozenset(), rgs=None):
        from app.services.graph_db.interface.graph_db_provider import AccessibleContainers

        rgs = rgs or {}
        graph = AsyncMock()
        graph.get_accessible_containers = AsyncMock(
            return_value=AccessibleContainers(
                app_ids_trusted=frozenset(app_level),
                record_group_ids_trusted=frozenset(trusted),
                record_group_ids_verify=frozenset(verify),
            ),
        )

        graph.get_nodes_by_field_in = AsyncMock(return_value=[
            {"id": rg["id"], "groupName": rg["group_name"], "connectorId": cid, "orgId": "org1"}
            for cid, groups in rgs.items() for rg in groups
        ])
        names = {rg["id"]: rg["group_name"] for groups in rgs.values() for rg in groups}
        graph.get_record_group_path = AsyncMock(side_effect=lambda rg_id, **_: [names[rg_id]])
        for groups in rgs.values():
            for rg in groups:
                (tmp_path / rg["group_name"]).mkdir(exist_ok=True)
        return graph

    @staticmethod
    async def _run(tmp_path, graph, mock_find, connector_ids):
        with patch("app.utils.pattern_match.StoragePatternMatch") as MockSPM:
            instance = MagicMock()
            instance.find_records = AsyncMock(side_effect=mock_find)
            instance._resolve_connector_path = AsyncMock(return_value=(str(tmp_path), None))
            MockSPM.return_value = instance
            with patch("app.utils.pattern_match._validate_command", return_value=(True, None)):
                return await run_pattern_match(
                    config_service=MagicMock(),
                    org_id="org1",
                    user_id="user1",
                    graph_provider=graph,
                    command='grep -rci "test" .',
                    connector_ids=connector_ids,
                    logger_instance=MagicMock(),
                )

    @pytest.mark.asyncio
    async def test_non_app_level_connector_uses_rg_scoping(self, tmp_path):
        """Non-APP_LEVEL connectors scope the grep to trusted record groups."""
        graph = self._scoping_setup(
            tmp_path, trusted={"rg1"}, rgs={"c1": [{"id": "rg1", "group_name": "Team A"}]},
        )

        async def mock_find(connector_id, command, **_kwargs):
            if '"./Team A"' in command:
                return (True, json.dumps({"records": [self._make_record("vr-scoped")]}))
            return (True, json.dumps({"records": []}))

        result = await self._run(tmp_path, graph, mock_find, ["c1"])

        assert [r["virtual_record_id"] for r in result] == ["vr-scoped"]
        graph.get_nodes_by_field_in.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mixed_connectors_app_level_and_scoped(self, tmp_path):
        """APP_LEVEL connectors grep the whole directory; the others grep
        only their trusted groups' directories."""
        graph = self._scoping_setup(
            tmp_path, app_level={"c-app"}, trusted={"rg1"},
            rgs={"c-rg": [{"id": "rg1", "group_name": "Sales"}]},
        )

        commands: dict[str, str] = {}

        async def mock_find(connector_id, command, **_kwargs):
            commands[connector_id] = command
            return (True, json.dumps({"records": [{"virtual_record_id": f"vr-{connector_id}"}]}))

        result = await self._run(tmp_path, graph, mock_find, ["c-app", "c-rg"])

        assert {r["virtual_record_id"] for r in result} == {"vr-c-app", "vr-c-rg"}
        assert commands["c-app"] == 'grep -rci "test" .'
        assert '"./Sales"' in commands["c-rg"]

    @pytest.mark.asyncio
    async def test_containers_fallback_skips_rg_lookup(self):
        """A fallback trusts no group, so the lookup could not be used; full grep."""
        config = MagicMock()
        graph = AsyncMock()
        graph.get_accessible_containers = AsyncMock(
            return_value=self._make_containers(
                app_ids_trusted=frozenset({"c1"}),
                fallback_reason="provider does not implement container filtering",
            ),
        )
        log = MagicMock()

        full_records = json.dumps({"records": [self._make_record("vr-fallback")]})

        with patch("app.utils.pattern_match.StoragePatternMatch") as MockSPM:
            instance = MagicMock()
            instance.find_records = AsyncMock(return_value=(True, full_records))
            MockSPM.return_value = instance

            with patch("app.utils.pattern_match._validate_command", return_value=(True, None)):
                result = await run_pattern_match(
                    config_service=config,
                    org_id="org1",
                    user_id="user1",
                    graph_provider=graph,
                    command='grep -rci "test" .',
                    connector_ids=["c1"],
                    logger_instance=log,
                )

        assert len(result) == 1
        graph.get_nodes_by_field_in.assert_not_called()

    @pytest.mark.asyncio
    async def test_containers_exception_skips_rg_lookup(self):
        """A fallback trusts no group, so the lookup could not be used; full grep."""
        config = MagicMock()
        graph = AsyncMock()
        graph.get_accessible_containers = AsyncMock(side_effect=RuntimeError("db down"))
        log = MagicMock()

        full_records = json.dumps({"records": [self._make_record("vr-err")]})

        with patch("app.utils.pattern_match.StoragePatternMatch") as MockSPM:
            instance = MagicMock()
            instance.find_records = AsyncMock(return_value=(True, full_records))
            MockSPM.return_value = instance

            with patch("app.utils.pattern_match._validate_command", return_value=(True, None)):
                result = await run_pattern_match(
                    config_service=config,
                    org_id="org1",
                    user_id="user1",
                    graph_provider=graph,
                    command='grep -rci "test" .',
                    connector_ids=["c1"],
                    logger_instance=log,
                )

        assert len(result) == 1
        graph.get_nodes_by_field_in.assert_not_called()

    @pytest.mark.asyncio
    async def test_rg_scoped_grep_searches_only_trusted_group_directories(self, tmp_path):
        """Scoping narrows where grep looks; merge still adjudicates every hit."""
        graph = self._scoping_setup(
            tmp_path, trusted={"rg-trusted"},
            rgs={"c1": [{"id": "rg-trusted", "group_name": "Engineering"}]},
        )

        async def mock_find(connector_id, command, **_kwargs):
            if '"./Engineering"' in command:
                return (True, json.dumps({"records": [{"virtual_record_id": "vr-scoped"}]}))
            return (True, json.dumps({"records": []}))

        result = await self._run(tmp_path, graph, mock_find, ["c1"])

        assert [r["virtual_record_id"] for r in result] == ["vr-scoped"]

    @pytest.mark.asyncio
    async def test_rg_in_verify_set_is_not_scoped(self, tmp_path):
        """Groups that are not trusted do not narrow the grep: the full
        connector is searched."""
        graph = self._scoping_setup(
            tmp_path, verify={"rg-verify"},
            rgs={"c1": [{"id": "rg-verify", "group_name": "Sales"}]},
        )
        commands: list[str] = []

        async def mock_find(connector_id, command, **_kwargs):
            commands.append(command)
            return (True, json.dumps({"records": [{"virtual_record_id": "vr-verify"}]}))

        result = await self._run(tmp_path, graph, mock_find, ["c1"])

        assert commands == ['grep -rci "test" .']
        assert [r["virtual_record_id"] for r in result] == ["vr-verify"]


class TestMergePatternMatchTimeRange:
    """Time-range filtering after the permission check."""

    @pytest.mark.asyncio
    async def test_time_range_filters_accessible_records(self):
        """Records outside the time_range should be excluded even when
        they pass the permission check."""
        graph = AsyncMock()
        graph.filter_accessible_virtual_record_ids = AsyncMock(return_value={
            "vr-old": "rec-old", "vr-new": "rec-new",
        })
        graph.get_records_by_record_ids = AsyncMock(return_value=[
            {
                "_key": "rec-old", "recordName": "Old", "indexingStatus": "COMPLETED",
                "sourceCreatedAtTimestamp": 1704067200000,
                "sourceLastModifiedTimestamp": 1704153600000,
            },
            {
                "_key": "rec-new", "recordName": "New", "indexingStatus": "COMPLETED",
                "sourceCreatedAtTimestamp": 1748736000000,
                "sourceLastModifiedTimestamp": 1749945600000,
            },
        ])

        raw = [
            {"virtual_record_id": "vr-old", "match_count": 5},
            {"virtual_record_id": "vr-new", "match_count": 3},
        ]

        result = await merge_pattern_match_results(
            raw_records=raw,
            virtual_record_id_to_result={},
            user_id="user1", org_id="org1",
            blob_store=MagicMock(), graph_provider=graph,
            is_multimodal_llm=False, logger_instance=MagicMock(),
            time_range={"source_created_after_ms": 1735689600000},
        )

        assert len(result) == 1
        assert result[0]["virtual_record_id"] == "vr-new"

    @pytest.mark.asyncio
    async def test_time_range_filters_all_returns_empty(self):
        """When time_range excludes every record, merge returns []."""
        graph = AsyncMock()
        graph.filter_accessible_virtual_record_ids = AsyncMock(return_value={
            "vr-1": "rec-1",
        })
        graph.get_records_by_record_ids = AsyncMock(return_value=[
            {
                "_key": "rec-1", "recordName": "Ancient", "indexingStatus": "COMPLETED",
                "sourceCreatedAtTimestamp": 1577836800000,
                "sourceLastModifiedTimestamp": 1577923200000,
            },
        ])

        raw = [{"virtual_record_id": "vr-1", "match_count": 10}]

        result = await merge_pattern_match_results(
            raw_records=raw,
            virtual_record_id_to_result={},
            user_id="user1", org_id="org1",
            blob_store=MagicMock(), graph_provider=graph,
            is_multimodal_llm=False, logger_instance=MagicMock(),
            time_range={"source_created_after_ms": 1735689600000},
        )

        assert result == []


# ──────────────────────────────────────────────────────────────────────────────
# Null-delimited pipeline tests (filenames with spaces fix)
# ──────────────────────────────────────────────────────────────────────────────

class TestEnsureNullDelimitedPipeline:

    def test_single_grep_unchanged(self):
        cmd = 'grep -rci "deploy" .'
        assert _ensure_null_delimited_pipeline(cmd) == cmd

    def test_two_stage_injects_Z_and_0(self):
        cmd = 'grep -rli "invoice" . | xargs grep -ci "recurring"'
        result = _ensure_null_delimited_pipeline(cmd)
        assert result == 'grep -rliZ "invoice" . | xargs -0 grep -ciH "recurring"'

    def test_three_stage_injects_all(self):
        cmd = ('grep -rli "hierarch" . | xargs grep -li "storage" '
               '| xargs grep -ci "pattern"')
        result = _ensure_null_delimited_pipeline(cmd)
        assert 'grep -rliZ "hierarch"' in result
        assert 'xargs -0 grep -liZ "storage"' in result
        assert 'xargs -0 grep -ciH "pattern"' in result

    def test_already_has_Z_and_0(self):
        cmd = 'grep -rliZ "term" . | xargs -0 grep -Hci "term2"'
        result = _ensure_null_delimited_pipeline(cmd)
        assert result == cmd

    def test_head_not_modified(self):
        cmd = 'grep -rli "term" . | xargs grep -ci "t2" | head -200'
        result = _ensure_null_delimited_pipeline(cmd)
        assert result == 'grep -rliZ "term" . | xargs -0 grep -ciH "t2" | head -200'

    @pytest.mark.parametrize("cmd,expected", [
        ('grep -e "invoice" -rli . | xargs grep -ci "x"',
         'grep -Z -e "invoice" -rli . | xargs -0 grep -ciH "x"'),
        ('grep -rie "invoice" . | xargs grep -ci "x"',
         'grep -Z -rie "invoice" . | xargs -0 grep -ciH "x"'),
        ('grep -m5 -rli "a" . | xargs grep -ci "b"',
         'grep -Z -m5 -rli "a" . | xargs -0 grep -ciH "b"'),
    ])
    def test_flag_never_glued_to_a_value_taking_option(self, cmd, expected):
        # "-eZ" would make Z the pattern and the real pattern a file name.
        assert _ensure_null_delimited_pipeline(cmd) == expected

    def test_count_flag_found_in_any_flag_group(self):
        cmd = 'grep -rli "a" . | xargs grep -i -c "b"'
        assert _ensure_null_delimited_pipeline(cmd) == (
            'grep -rliZ "a" . | xargs -0 grep -iH -c "b"'
        )

    def test_count_long_flag_gets_filename_prefix(self):
        cmd = 'grep -rl --null "a" . | xargs grep --count "b"'
        assert _ensure_null_delimited_pipeline(cmd) == (
            'grep -rl --null "a" . | xargs -0 grep -H --count "b"'
        )

    def test_pattern_that_looks_like_count_flag_is_not_count(self):
        cmd = 'grep -rli "a" . | xargs grep -i -e "-c"'
        assert _ensure_null_delimited_pipeline(cmd) == (
            'grep -rliZ "a" . | xargs -0 grep -i -e "-c"'
        )

    def test_rg_uses_its_own_null_flag(self):
        # rg's -Z is --search-zip, not NUL-separated output.
        cmd = 'rg -li "a" . | xargs rg -c "b"'
        assert _ensure_null_delimited_pipeline(cmd) == 'rg -li0 "a" . | xargs -0 rg -cH "b"'


# ===========================================================================
# run_pattern_match_with_llm_grep  (GP-09)
# ===========================================================================


class TestRunPatternMatchWithLlmGrep:

    def _base_kwargs(self):
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(
            return_value={"storageType": "local"}
        )
        return dict(
            query="MCP server setup",
            config_service=config_service,
            org_id="org-1",
            user_id="user-1",
            graph_provider=AsyncMock(),
            filters=None,
            logger_instance=MagicMock(),
        )

    @pytest.mark.asyncio
    async def test_slow_pipeline_is_cut_off_at_the_total_budget(self):
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def _hang(**_kwargs):
            started.set()
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        with patch("app.utils.pattern_match._PATTERN_MATCH_TOTAL_BUDGET", 0.05),              patch("app.utils.pattern_match.execute_pattern_match_pipeline", side_effect=_hang):
            result = await asyncio.wait_for(
                run_pattern_match_with_llm_grep(**self._base_kwargs(), llm=None), timeout=5,
            )

        assert result == []
        assert started.is_set() and cancelled.is_set()

    @pytest.mark.asyncio
    async def test_keyword_fallback_runs_inside_the_same_budget(self):
        # The fallback must not get a fresh per-connector timeout of its own.
        fallback_cancelled = asyncio.Event()

        async def _pipeline(**kwargs):
            if kwargs["grep_command"]:
                return []
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                fallback_cancelled.set()
                raise

        with patch("app.utils.pattern_match._PATTERN_MATCH_TOTAL_BUDGET", 0.1),              patch("app.utils.pattern_match.generate_grep_command_via_llm",
                   new_callable=AsyncMock, return_value=['grep -rci "MCP" .']),              patch("app.utils.pattern_match.execute_pattern_match_pipeline", side_effect=_pipeline):
            result = await asyncio.wait_for(
                run_pattern_match_with_llm_grep(**self._base_kwargs(), llm=MagicMock()), timeout=5,
            )

        assert result == []
        assert fallback_cancelled.is_set()

    @pytest.mark.asyncio
    async def test_fast_empty_llm_commands_still_fall_back_to_keywords(self):
        expected = [{"virtual_record_id": "vr-kw"}]

        async def _pipeline(**kwargs):
            return [] if kwargs["grep_command"] else expected

        with patch("app.utils.pattern_match.generate_grep_command_via_llm",
                   new_callable=AsyncMock, return_value=['grep -rci "MCP" .']),              patch("app.utils.pattern_match.execute_pattern_match_pipeline", side_effect=_pipeline):
            result = await run_pattern_match_with_llm_grep(**self._base_kwargs(), llm=MagicMock())

        assert result == expected

    @pytest.mark.asyncio
    async def test_no_llm_delegates_to_pipeline_without_grep_command(self):
        """When no LLM is provided, delegates to execute_pattern_match_pipeline
        with grep_command=None."""
        expected = [{"virtual_record_id": "vr-1"}]
        with patch(
            "app.utils.pattern_match.execute_pattern_match_pipeline",
            new_callable=AsyncMock,
            return_value=expected,
        ) as mock_pipeline:
            result = await run_pattern_match_with_llm_grep(
                **self._base_kwargs(), llm=None,
            )
        assert result == expected
        mock_pipeline.assert_called_once()
        assert mock_pipeline.call_args.kwargs["grep_command"] is None
        assert mock_pipeline.call_args.kwargs["skip_grep_validation"] is False

    @pytest.mark.asyncio
    async def test_single_llm_command_delegates_with_skip_validation(self):
        """When LLM returns a single command, it's passed directly with
        skip_grep_validation=True."""
        expected = [{"virtual_record_id": "vr-2"}]
        with patch(
            "app.utils.pattern_match.generate_grep_command_via_llm",
            new_callable=AsyncMock,
            return_value=['grep -rci "MCP" .'],
        ), patch(
            "app.utils.pattern_match.execute_pattern_match_pipeline",
            new_callable=AsyncMock,
            return_value=expected,
        ) as mock_pipeline:
            result = await run_pattern_match_with_llm_grep(
                **self._base_kwargs(), llm=MagicMock(),
            )
        assert result == expected
        mock_pipeline.assert_called_once()
        assert mock_pipeline.call_args.kwargs["grep_command"] == 'grep -rci "MCP" .'
        assert mock_pipeline.call_args.kwargs["skip_grep_validation"] is True

    @pytest.mark.asyncio
    async def test_multiple_llm_commands_run_in_parallel_and_dedup(self):
        """When LLM returns multiple commands, they run in parallel and
        results are deduplicated by virtual_record_id."""
        cmd1_results = [
            {"virtual_record_id": "vr-A"},
            {"virtual_record_id": "vr-B"},
        ]
        cmd2_results = [
            {"virtual_record_id": "vr-B"},
            {"virtual_record_id": "vr-C"},
        ]

        call_count = 0

        async def _side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if kwargs.get("grep_command") == 'grep -rci "oauth" .':
                return cmd1_results
            return cmd2_results

        with patch(
            "app.utils.pattern_match.generate_grep_command_via_llm",
            new_callable=AsyncMock,
            return_value=[
                'grep -rci "oauth" .',
                'grep -rci "saml" .',
            ],
        ), patch(
            "app.utils.pattern_match.execute_pattern_match_pipeline",
            new_callable=AsyncMock,
            side_effect=_side_effect,
        ):
            result = await run_pattern_match_with_llm_grep(
                **self._base_kwargs(), llm=MagicMock(),
            )

        assert call_count == 2
        vrids = [r["virtual_record_id"] for r in result]
        assert sorted(vrids) == ["vr-A", "vr-B", "vr-C"]
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_llm_returns_none_falls_back_to_keyword_grep(self):
        """When LLM returns None (timeout/failure), pipeline runs with
        grep_command=None (falls back to keyword-based grep)."""
        expected = [{"virtual_record_id": "vr-fallback"}]
        with patch(
            "app.utils.pattern_match.generate_grep_command_via_llm",
            new_callable=AsyncMock,
            return_value=None,
        ), patch(
            "app.utils.pattern_match.execute_pattern_match_pipeline",
            new_callable=AsyncMock,
            return_value=expected,
        ) as mock_pipeline:
            result = await run_pattern_match_with_llm_grep(
                **self._base_kwargs(), llm=MagicMock(),
            )
        assert result == expected
        assert mock_pipeline.call_args.kwargs["grep_command"] is None

    @pytest.mark.asyncio
    async def test_not_eligible_returns_empty(self):
        """When storage is not local, returns empty immediately."""
        kwargs = self._base_kwargs()
        kwargs["config_service"].get_config = AsyncMock(
            return_value={"storageType": "s3"}
        )
        result = await run_pattern_match_with_llm_grep(
            **kwargs, llm=MagicMock(),
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_parallel_exception_is_skipped(self):
        """When one parallel pipeline raises, its results are skipped
        and the other pipeline's results are returned."""
        call_idx = 0

        async def _side_effect(**kwargs):
            nonlocal call_idx
            call_idx += 1
            if call_idx == 1:
                return [{"virtual_record_id": "vr-good"}]
            raise RuntimeError("subprocess failed")

        with patch(
            "app.utils.pattern_match.generate_grep_command_via_llm",
            new_callable=AsyncMock,
            return_value=[
                'grep -rci "good" .',
                'grep -rci "bad" .',
            ],
        ), patch(
            "app.utils.pattern_match.execute_pattern_match_pipeline",
            new_callable=AsyncMock,
            side_effect=_side_effect,
        ):
            result = await run_pattern_match_with_llm_grep(
                **self._base_kwargs(), llm=MagicMock(),
            )

        assert len(result) == 1
        assert result[0]["virtual_record_id"] == "vr-good"

    @pytest.mark.asyncio
    async def test_user_query_passed_to_llm(self):
        """user_query is forwarded to generate_grep_command_via_llm."""
        with patch(
            "app.utils.pattern_match.generate_grep_command_via_llm",
            new_callable=AsyncMock,
            return_value=['grep -rci "test" .'],
        ) as mock_gen, patch(
            "app.utils.pattern_match.execute_pattern_match_pipeline",
            new_callable=AsyncMock,
            return_value=[],
        ):
            await run_pattern_match_with_llm_grep(
                **self._base_kwargs(),
                llm=MagicMock(),
                user_query="original user question",
            )
        assert mock_gen.call_args.kwargs["user_query"] == "original user question"


# ===========================================================================
# Special characters in grep patterns (GP-11)
# ===========================================================================


class TestValidateGrepCommandSpecialCharacters:

    def test_single_quotes_in_pattern(self):
        cmd = "grep -rci \"it's working\" ."
        assert validate_grep_command(cmd) == cmd

    def test_unicode_pattern(self):
        cmd = 'grep -rci "äöüß" .'
        assert validate_grep_command(cmd) == cmd

    def test_cjk_characters(self):
        cmd = 'grep -rci "测试文件" .'
        assert validate_grep_command(cmd) == cmd

    def test_extremely_long_pattern_within_limit(self):
        pattern = "a" * 500
        cmd = f'grep -rci "{pattern}" .'
        assert validate_grep_command(cmd) == cmd

    def test_pattern_with_regex_special_chars(self):
        cmd = 'grep -rci "price\\[0\\]\\.value" .'
        assert validate_grep_command(cmd) == cmd

    def test_pattern_with_pipe_inside_quotes(self):
        cmd = 'grep -rci "foo\\|bar\\|baz" .'
        assert validate_grep_command(cmd) == cmd

    def test_pattern_with_parentheses(self):
        cmd = 'grep -rci "func(arg1, arg2)" .'
        assert validate_grep_command(cmd) == cmd

    def test_pattern_with_equals_and_ampersand_inside_quotes(self):
        cmd = 'grep -rci "key=value" .'
        assert validate_grep_command(cmd) == cmd

    def test_hyphenated_flag_like_pattern(self):
        cmd = 'grep -rci "error-code-404" .'
        assert validate_grep_command(cmd) == cmd
