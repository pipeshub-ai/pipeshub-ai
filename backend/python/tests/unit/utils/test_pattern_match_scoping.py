"""Recall and scoping regressions in app.utils.pattern_match."""

import json
import os
import shutil
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.storage_search.storage_search import (
    StoragePatternMatch,
    _grep_search_regexes,
    _rank_candidates,
    _run_subprocess,
)
from app.services.graph_db.interface.graph_db_provider import AccessibleContainers
from app.utils.pattern_match import (
    _MAX_GREP_OUTPUT_CHARS,
    _MAX_GREP_STDOUT_BYTES,
    _cap_grep_output,
    _ensure_null_delimited_pipeline,
    validate_pattern_match_command,
    _resolve_search_paths,
    merge_pattern_match_results,
    render_pattern_match_hint,
    resolve_connector_ids_for_search,
    run_pattern_match,
    run_pattern_match_with_llm_grep,
)


# Shape of a stored record file: one line of JSON (see blob_storage.py).
def _record_json(name: str, text: str) -> str:
    return json.dumps({"isCompressed": False, "record": {
        "record_name": name,
        "block_containers": {"blocks": [{"data": f"# {name}\n{text}"}]},
    }})


class TestRanking:
    """Every record file is one line, so grep -c scores all hits 1; ranking must
    come from the record text or the answer is dropped by the per-connector cap."""

    @pytest.fixture
    def connector(self, tmp_path):
        for i in range(60):
            (tmp_path / f"record_{i:08x}-0000-0000-0000-000000000000.json").write_text(
                _record_json(f"[PT-{i}] pipeshub test ticket", "testing the pipeshub jira connector")
            )
        (tmp_path / "record_3107958a-8b30-4852-a9e2-7814000e7d19.json").write_text(_record_json(
            "[PST-34] Pipeshub General knowledge testing",
            "so pipeshub has 10 employees and pipeshub is private company "
            "also pipeshub valuation is 1 billion dollar",
        ))
        return tmp_path

    def _rank(self, connector, command):
        candidates = [(f"./{p.name}", p.stem, 1) for p in sorted(connector.iterdir())]
        return _rank_candidates(candidates, str(connector), _grep_search_regexes(command))

    def test_keyword_fallback_puts_answer_first(self, connector):
        ranked = self._rank(connector, _cap_grep_output(
            'grep -rci "pipeshub\\|valuation\\|billion\\|dollars\\|billion" .'
        ))
        path, _vrid, matched_terms, _count, snippet = ranked[0]
        assert "3107958a" in path
        assert matched_terms == 3
        assert "valuation is 1 billion dollar" in snippet

    def test_llm_command_with_dollar_ranks_answer_first(self, connector):
        command = (
            'grep -rliZ "pipeshub\\|pipes hub" . | '
            'xargs -0 grep -Hci "valuat\\|billion\\|\\$1[[:space:]]*b\\|unicorn"'
        )
        assert validate_pattern_match_command(command)[0]
        assert "3107958a" in self._rank(connector, command)[0][0]

    def test_inverted_filter_stage_is_not_a_search_term(self):
        regexes = _grep_search_regexes('grep -rci "billion" . | grep -v \':0$\' | head -200')
        assert [r.pattern for r in regexes] == ["billion"]


def test_hint_shows_matched_text():
    hint = render_pattern_match_hint(
        [{"virtual_record_id": "v1"}],
        {"v1": {"_key": "r1", "title": "PST-34", "_match_snippet": "valuation is 1 billion dollar"}},
    )
    assert "Matched Text: valuation is 1 billion dollar" in hint


class TestCapGrepOutput:
    def test_zero_counts_dropped_before_default_head(self):
        assert _cap_grep_output('grep -rci "x" .') == (
            "grep -rci \"x\" . | grep -v ':0$' | head -200"
        )

    def test_existing_head_kept_after_filter(self):
        assert _cap_grep_output('grep -rci "x" . | head -5') == (
            "grep -rci \"x\" . | grep -v ':0$' | head -5"
        )


@pytest.mark.skipif(shutil.which("grep") is None or shutil.which("xargs") is None,
                    reason="needs grep and xargs")
@pytest.mark.asyncio
async def test_match_after_many_non_matching_files_is_found(tmp_path):
    """A match late in the directory walk must survive the output caps."""
    deep = "page-" * 20
    for i in range(400):
        d = tmp_path / "Space" / f"{deep}{i:03d}"
        d.mkdir(parents=True)
        (d / f"record_{i:08x}-0000-0000-0000-000000000000.json").write_text(
            '{"text": "pipeshub docs"}'
        )
    hit = tmp_path / "zzz-last" / "record_ffffffff-0000-0000-0000-000000000000.json"
    hit.parent.mkdir()
    hit.write_text('{"text": "pipeshub raised funding"}')

    command = _cap_grep_output(_ensure_null_delimited_pipeline(
        'grep -rliZ "pipeshub" . | xargs -0 grep -Hci "funding"'
    ))
    ok, output = await _run_subprocess(
        command, cwd=str(tmp_path),
        max_stdout_bytes=_MAX_GREP_STDOUT_BYTES, max_output_chars=_MAX_GREP_OUTPUT_CHARS,
    )

    assert ok
    assert "record_ffffffff-0000-0000-0000-000000000000.json:1" in output
    assert ":0\n" not in output


class TestResolveSearchPaths:
    @pytest.mark.asyncio
    async def test_nested_group_uses_full_ancestor_chain(self, tmp_path):
        (tmp_path / "Root" / "Child").mkdir(parents=True)
        graph = MagicMock()
        graph.get_record_group_path = AsyncMock(return_value=["Root", "Child"])

        paths = await _resolve_search_paths(
            graph_provider=graph, connector_id="c1", connector_dir=str(tmp_path),
            accessible_rgs=[{"id": "rg-child", "group_name": "Child"}],
            logger_instance=MagicMock(),
        )

        assert paths == ["./Root/Child"]

    @pytest.mark.asyncio
    async def test_descendant_of_accessible_group_collapsed_and_missing_dir_skipped(self, tmp_path):
        (tmp_path / "Root" / "Child").mkdir(parents=True)
        chains = {"rg-root": ["Root"], "rg-child": ["Root", "Child"], "rg-empty": ["Nothing"]}
        graph = MagicMock()
        graph.get_record_group_path = AsyncMock(side_effect=lambda rg_id, **_: chains[rg_id])

        paths = await _resolve_search_paths(
            graph_provider=graph, connector_id="c1", connector_dir=str(tmp_path),
            accessible_rgs=[{"id": k, "group_name": v[-1]} for k, v in chains.items()],
            logger_instance=MagicMock(),
        )

        assert paths == ["./Root"]

    @pytest.mark.asyncio
    async def test_unknown_group_ancestry_disables_scoping(self, tmp_path):
        (tmp_path / "Child").mkdir()
        graph = MagicMock()
        graph.get_record_group_path = AsyncMock(return_value=[])

        paths = await _resolve_search_paths(
            graph_provider=graph, connector_id="c1", connector_dir=str(tmp_path),
            accessible_rgs=[{"id": "rg-child", "group_name": "Child"}],
            logger_instance=MagicMock(),
        )

        assert paths is None
        graph.get_record_group_path.assert_awaited_once_with("rg-child", raise_on_error=True)

    @pytest.mark.asyncio
    async def test_failed_path_lookup_disables_scoping(self, tmp_path):
        (tmp_path / "Child").mkdir()
        graph = MagicMock()
        graph.get_record_group_path = AsyncMock(side_effect=RuntimeError("graph down"))

        paths = await _resolve_search_paths(
            graph_provider=graph, connector_id="c1", connector_dir=str(tmp_path),
            accessible_rgs=[{"id": "rg-child", "group_name": "Child"}],
            logger_instance=MagicMock(),
        )

        assert paths is None


@pytest.mark.asyncio
async def test_strict_scope_with_empty_selection_searches_nothing():
    graph = MagicMock()
    graph.get_org_apps = AsyncMock(return_value=[{"_key": "every-app"}])

    result = await resolve_connector_ids_for_search(
        graph, "org1", {"apps": [], "kb": [], "strictScope": True},
    )

    assert result == []
    graph.get_org_apps.assert_not_called()


@pytest.mark.asyncio
async def test_unscopable_command_falls_back_to_full_grep(tmp_path):
    """If the path rewrite cannot apply, the whole connector is searched."""
    (tmp_path / "Team").mkdir()
    containers = AccessibleContainers(record_group_ids_trusted=frozenset({"rg1"}))
    graph = MagicMock()
    graph.get_accessible_containers = AsyncMock(return_value=containers)
    graph.get_nodes_by_field_in = AsyncMock(
        return_value=[{"id": "rg1", "groupName": "Team", "connectorId": "c1", "orgId": "org1"}]
    )
    graph.get_record_group_path = AsyncMock(return_value=["Team"])

    with patch("app.utils.pattern_match.StoragePatternMatch") as spm, \
         patch("app.utils.pattern_match._validate_command", return_value=(True, "")):
        tool = spm.return_value
        tool._resolve_connector_path = AsyncMock(return_value=(str(tmp_path), None))
        tool.find_records = AsyncMock(
            return_value=(True, json.dumps({"records": [{"virtual_record_id": "v1"}]}))
        )
        records = await run_pattern_match(
            config_service=MagicMock(), org_id="org1", user_id="u1",
            graph_provider=graph, command='grep -rci "x"', connector_ids=["c1"],
            logger_instance=MagicMock(),
        )

    assert [r["virtual_record_id"] for r in records] == ["v1"]
    assert tool.find_records.await_args.kwargs["command"] == 'grep -rci "x"'
    assert graph.get_nodes_by_field_in.await_args.args[:3] == ("recordGroups", "id", ["rg1"])


class TestGrepSearchRegexes:
    def test_value_flags_are_not_taken_as_the_pattern(self):
        regexes = _grep_search_regexes('grep -m 5 -A 2 -rci "billion" .')
        assert [r.pattern for r in regexes] == ["billion"]

    def test_grouped_ere_is_kept_whole(self):
        (regex,) = _grep_search_regexes('grep -Erci "(val|pric)uation" .')
        assert regex.search("valuation") and regex.search("pricuation")

    def test_nested_quantifier_is_matched_literally(self):
        (regex,) = _grep_search_regexes('grep -Erci "(a+)+b" .')
        assert regex.search("a" * 5000 + "c") is None
        assert regex.search("x(a+)+b")

    def test_unbounded_dot_is_capped(self):
        (regex,) = _grep_search_regexes('grep -rci "pipeshub.*billion" .')
        assert ".*" not in regex.pattern
        assert regex.search("pipeshub valuation is 1 billion")


@pytest.mark.skipif(shutil.which("grep") is None, reason="needs grep")
@pytest.mark.asyncio
async def test_dollar_is_passed_to_grep_literally_not_expanded(tmp_path):
    """Pipelines run without a shell, so "$HOME" must reach grep as text."""
    (tmp_path / "literal.json").write_text("price is $HOME dollars")
    (tmp_path / "expanded.json").write_text(os.path.expanduser("~"))

    ok, output = await _run_subprocess('grep -rlF "$HOME" .', cwd=str(tmp_path))

    assert ok
    assert "literal.json" in output
    assert "expanded.json" not in output


@pytest.mark.skipif(shutil.which("grep") is None, reason="needs grep")
@pytest.mark.asyncio
async def test_find_records_keeps_results_when_ranking_fails(tmp_path):
    vrid = "3107958a-8b30-4852-a9e2-7814000e7d19"
    (tmp_path / f"record_{vrid}.json").write_text(_record_json("PST-34", "pipeshub valuation"))
    graph = MagicMock()
    graph.filter_accessible_virtual_record_ids = AsyncMock(return_value={vrid: "rid-1"})
    graph.get_records_by_record_ids = AsyncMock(return_value=[{"_key": "rid-1", "recordName": "PST-34"}])
    config = AsyncMock()
    config.get_config = AsyncMock(return_value={"storageType": "local"})
    tool = StoragePatternMatch({
        "org_id": "org1", "user_id": "u1", "config_service": config, "graph_provider": graph,
    })
    tool._resolve_connector_path = AsyncMock(return_value=(str(tmp_path), None))

    with patch(
        "app.agents.actions.storage_search.storage_search._rank_candidates",
        side_effect=RuntimeError("boom"),
    ):
        ok, output = await tool.find_records("c1", 'grep -rl "valuation" .')

    assert ok
    assert [r["record_id"] for r in json.loads(output)["records"]] == ["rid-1"]


@pytest.mark.asyncio
async def test_llm_grep_with_no_hits_falls_back_to_keyword_grep():
    llm_cmd = 'grep -rliZ "a" . | xargs -0 grep -Hci "b"'
    calls = []

    async def pipeline(**kwargs):
        calls.append(kwargs.get("grep_command"))
        return [] if kwargs.get("grep_command") else [{"virtual_record_id": "v1"}]

    with patch("app.utils.pattern_match.check_pattern_match_eligible", AsyncMock(return_value=True)), \
         patch("app.utils.pattern_match.generate_grep_command_via_llm", AsyncMock(return_value=[llm_cmd])), \
         patch("app.utils.pattern_match.execute_pattern_match_pipeline", side_effect=pipeline):
        records = await run_pattern_match_with_llm_grep(
            query="pipeshub valuation", config_service=MagicMock(), org_id="o", user_id="u",
            graph_provider=MagicMock(), filters=None, logger_instance=MagicMock(), llm=MagicMock(),
        )

    assert calls == [llm_cmd, None]
    assert records == [{"virtual_record_id": "v1"}]


@pytest.mark.asyncio
async def test_failed_scoped_grep_falls_back_to_full_grep(tmp_path):
    (tmp_path / "Team").mkdir()
    containers = AccessibleContainers(record_group_ids_trusted=frozenset({"rg1"}))
    graph = MagicMock()
    graph.get_accessible_containers = AsyncMock(return_value=containers)
    graph.get_nodes_by_field_in = AsyncMock(
        return_value=[{"id": "rg1", "groupName": "Team", "connectorId": "c1", "orgId": "org1"}]
    )
    graph.get_record_group_path = AsyncMock(return_value=["Team"])

    async def find_records(connector_id, command, **_kwargs):
        if "./Team" in command:
            return False, "Error: command rejected"
        return True, json.dumps({"records": [{"virtual_record_id": "v1"}]})

    with patch("app.utils.pattern_match.StoragePatternMatch") as spm, \
         patch("app.utils.pattern_match._validate_command", return_value=(True, "")):
        tool = spm.return_value
        tool._resolve_connector_path = AsyncMock(return_value=(str(tmp_path), None))
        tool.find_records = AsyncMock(side_effect=find_records)
        records = await run_pattern_match(
            config_service=MagicMock(), org_id="org1", user_id="u1",
            graph_provider=graph, command='grep -rci "x" .', connector_ids=["c1"],
            logger_instance=MagicMock(),
        )

    assert [r["virtual_record_id"] for r in records] == ["v1"]
    assert tool.find_records.await_args.kwargs["command"] == 'grep -rci "x" .'


class TestMergeAdjudication:
    """merge_pattern_match_results adjudicates exactly as semantic search does."""

    def _graph(self, *, trusted_apps=frozenset(), trusted_groups=frozenset()):
        from app.services.graph_db.interface.graph_db_provider import AccessibleContainers

        graph = MagicMock()
        graph.get_accessible_containers = AsyncMock(return_value=AccessibleContainers(
            app_ids_trusted=frozenset(trusted_apps),
            record_group_ids_trusted=frozenset(trusted_groups),
        ))
        graph.get_records_by_record_ids = AsyncMock(side_effect=lambda record_ids, org_id: [
            {"_key": rid, "title": rid, "indexingStatus": "COMPLETED"} for rid in record_ids
        ])
        return graph

    async def _merge(self, graph, raw, filters=None):
        return await merge_pattern_match_results(
            raw_records=raw, virtual_record_id_to_result={}, user_id="u", org_id="o",
            blob_store=None, graph_provider=graph, is_multimodal_llm=False,
            logger_instance=MagicMock(), filters=filters,
        )

    @pytest.mark.asyncio
    async def test_uses_trusted_containers_and_request_scope(self):
        graph = self._graph(trusted_apps={"app-1"}, trusted_groups={"rg-1"})
        graph.filter_accessible_virtual_record_ids = AsyncMock(return_value={"v1": "r1"})

        results = await self._merge(
            graph, [{"virtual_record_id": "v1"}], filters={"apps": ["app-1"], "kb": []},
        )

        graph.filter_accessible_virtual_record_ids.assert_awaited_once_with(
            ["v1"], "u", "o",
            trusted_app_ids=frozenset({"app-1"}),
            trusted_group_ids=frozenset({"rg-1"}),
            scope_connector_ids=frozenset({"app-1"}),
        )
        assert [r["virtual_record_id"] for r in results] == ["v1"]

    @pytest.mark.asyncio
    async def test_unscoped_request_passes_no_scope(self):
        graph = self._graph()
        graph.filter_accessible_virtual_record_ids = AsyncMock(return_value={"v1": "r1"})

        await self._merge(graph, [{"virtual_record_id": "v1"}])

        assert graph.filter_accessible_virtual_record_ids.await_args.kwargs[
            "scope_connector_ids"
        ] is None

    @pytest.mark.asyncio
    async def test_container_lookup_failure_means_full_adjudication(self):
        graph = self._graph()
        graph.get_accessible_containers = AsyncMock(side_effect=RuntimeError("db"))
        graph.filter_accessible_virtual_record_ids = AsyncMock(return_value={"v1": "r1"})

        results = await self._merge(graph, [{"virtual_record_id": "v1"}])

        kwargs = graph.filter_accessible_virtual_record_ids.await_args.kwargs
        assert kwargs["trusted_app_ids"] == frozenset()
        assert kwargs["trusted_group_ids"] == frozenset()
        assert [r["virtual_record_id"] for r in results] == ["v1"]

    @pytest.mark.asyncio
    async def test_adjudication_failure_fails_closed(self):
        graph = self._graph()
        graph.filter_accessible_virtual_record_ids = AsyncMock(side_effect=RuntimeError("db"))

        results = await self._merge(graph, [{"virtual_record_id": "v1"}])

        assert results == []
        graph.get_records_by_record_ids.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_shared_vrid_resolves_to_the_readable_record(self):
        # Content deduplicated across connectors: the blob was found under one
        # connector, but the record the user may read lives in another.
        graph = self._graph()
        graph.filter_accessible_virtual_record_ids = AsyncMock(return_value={"v1": "r-other"})

        results = await self._merge(graph, [{"virtual_record_id": "v1"}])

        graph.get_records_by_record_ids.assert_awaited_once_with(
            record_ids=["r-other"], org_id="o",
        )
        assert [r["virtual_record_id"] for r in results] == ["v1"]

    @pytest.mark.asyncio
    async def test_ranks_by_distinct_terms_before_repetition(self):
        graph = self._graph()
        graph.filter_accessible_virtual_record_ids = AsyncMock(
            return_value={"noisy": "r1", "answer": "r2"},
        )

        results = await self._merge(graph, [
            {"virtual_record_id": "noisy", "matched_terms": "1", "match_count": "50"},
            {"virtual_record_id": "answer", "matched_terms": "3", "match_count": "3"},
        ])

        assert [r["virtual_record_id"] for r in results] == ["answer", "noisy"]


@pytest.mark.asyncio
async def test_direct_grants_outside_groups_disable_group_scoping(tmp_path):
    """A record shared directly with the user lives in another group's folder;
    scoping to the user's groups would never find it, so grep the whole
    connector (merge still adjudicates every hit)."""
    from app.services.graph_db.interface.graph_db_provider import AccessibleContainers

    (tmp_path / "Team").mkdir()
    graph = MagicMock()
    graph.get_accessible_containers = AsyncMock(return_value=AccessibleContainers(
        record_group_ids_trusted=frozenset({"rg1"}),
        direct_records={"v-shared": "r-shared"},
    ))
    graph.get_nodes_by_field_in = AsyncMock(
        return_value=[{"id": "rg1", "groupName": "Team", "connectorId": "c1", "orgId": "org1"}]
    )
    graph.get_record_group_path = AsyncMock(return_value=["Team"])

    with patch("app.utils.pattern_match.StoragePatternMatch") as spm, \
         patch("app.utils.pattern_match._validate_command", return_value=(True, "")):
        tool = spm.return_value
        tool._resolve_connector_path = AsyncMock(return_value=(str(tmp_path), None))
        tool.find_records = AsyncMock(
            return_value=(True, json.dumps({"records": [{"virtual_record_id": "v-shared"}]}))
        )
        records = await run_pattern_match(
            config_service=MagicMock(), org_id="org1", user_id="u1",
            graph_provider=graph, command='grep -rci "x" .', connector_ids=["c1"],
            logger_instance=MagicMock(),
        )

    assert tool.find_records.await_args.kwargs["command"] == 'grep -rci "x" .'
    assert [r["virtual_record_id"] for r in records] == ["v-shared"]


from app.utils.pattern_match import _scope_grep_to_paths


def test_scope_rewrites_only_the_trailing_bare_dot():
    cmd = 'grep -rli -e "a" -e "b . c" . | xargs -0 grep -ci "d"'
    assert _scope_grep_to_paths(cmd, ["./A", "./B"]) == (
        'grep -rli -e "a" -e "b . c" "./A" "./B" | xargs -0 grep -ci "d"'
    )


class TestMergeMatchesFetchRules:
    @staticmethod
    def _graph(records):
        graph = MagicMock()
        graph.get_accessible_containers = AsyncMock(return_value=AccessibleContainers())
        graph.filter_accessible_virtual_record_ids = AsyncMock(
            return_value={"v-ok": "r-ok", "v-demo": "r-demo", "v-new": "r-new"},
        )
        graph.get_records_by_record_ids = AsyncMock(return_value=records)
        return graph

    @staticmethod
    def _records():
        return [
            {"_key": "r-ok", "connectorId": "c-real", "indexingStatus": "COMPLETED"},
            {"_key": "r-demo", "connectorId": "c-demo", "indexingStatus": "COMPLETED"},
            {"_key": "r-new", "connectorId": "c-real", "indexingStatus": "IN_PROGRESS"},
        ]

    async def _merge(self, graph, vr_map, **kwargs):
        return await merge_pattern_match_results(
            raw_records=[{"virtual_record_id": v} for v in ("v-ok", "v-demo", "v-new")],
            virtual_record_id_to_result=vr_map, user_id="u", org_id="o",
            blob_store=None, graph_provider=graph, is_multimodal_llm=False,
            logger_instance=MagicMock(), **kwargs,
        )

    @pytest.mark.asyncio
    async def test_hidden_demo_and_unindexed_records_are_dropped(self):
        vr_map: dict = {}
        lookup = AsyncMock(return_value=frozenset({"c-demo"}))
        with patch("app.utils.pattern_match.excluded_demo_connector_ids", lookup):
            results = await self._merge(
                self._graph(self._records()), vr_map, config_service=MagicMock(),
            )

        assert [r["virtual_record_id"] for r in results] == ["v-ok"]
        assert set(vr_map) == {"v-ok"}
        lookup.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unreadable_demo_setting_excludes_nothing(self):
        vr_map: dict = {}
        with patch("app.utils.pattern_match.excluded_demo_connector_ids",
                   AsyncMock(side_effect=RuntimeError("boom"))):
            results = await self._merge(
                self._graph(self._records()), vr_map, config_service=MagicMock(),
            )

        assert sorted(r["virtual_record_id"] for r in results) == ["v-demo", "v-ok"]

    @pytest.mark.asyncio
    async def test_no_config_service_skips_demo_check_only(self):
        vr_map: dict = {}
        lookup = AsyncMock(return_value=frozenset({"c-demo"}))
        with patch("app.utils.pattern_match.excluded_demo_connector_ids", lookup):
            results = await self._merge(self._graph(self._records()), vr_map)

        assert sorted(r["virtual_record_id"] for r in results) == ["v-demo", "v-ok"]
        lookup.assert_not_awaited()
