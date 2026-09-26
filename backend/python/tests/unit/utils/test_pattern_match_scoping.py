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
from app.utils.pattern_match import (
    _MAX_GREP_OUTPUT_CHARS,
    _MAX_GREP_STDOUT_BYTES,
    _cap_grep_output,
    _ensure_null_delimited_pipeline,
    _pre_validate_llm_grep,
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
        assert _pre_validate_llm_grep(command)
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
        graph.get_record_group_path = AsyncMock(side_effect=lambda rg_id: chains[rg_id])

        paths = await _resolve_search_paths(
            graph_provider=graph, connector_id="c1", connector_dir=str(tmp_path),
            accessible_rgs=[{"id": k, "group_name": v[-1]} for k, v in chains.items()],
            logger_instance=MagicMock(),
        )

        assert paths == ["./Root"]


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
async def test_unscopable_command_falls_back_to_record_level(tmp_path):
    """If the path rewrite cannot apply, results must not be trusted as group-scoped."""
    (tmp_path / "Team").mkdir()
    containers = MagicMock(
        fallback_reason=None, app_ids_trusted=frozenset(),
        record_group_ids_trusted=frozenset({"rg1"}),
    )
    graph = MagicMock()
    graph.get_accessible_containers = AsyncMock(return_value=containers)
    graph.get_accessible_record_groups_for_connector = AsyncMock(
        return_value=[{"id": "rg1", "group_name": "Team"}]
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

    assert [r["_access_scope"] for r in records] == ["record"]
    assert tool.find_records.await_args.kwargs["command"] == 'grep -rci "x"'


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
    graph.check_vrids_accessible = AsyncMock(return_value={vrid: "rid-1"})
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
async def test_failed_scoped_grep_falls_back_to_record_level(tmp_path):
    (tmp_path / "Team").mkdir()
    containers = MagicMock(
        fallback_reason=None, app_ids_trusted=frozenset(),
        record_group_ids_trusted=frozenset({"rg1"}),
    )
    graph = MagicMock()
    graph.get_accessible_containers = AsyncMock(return_value=containers)
    graph.get_accessible_record_groups_for_connector = AsyncMock(
        return_value=[{"id": "rg1", "group_name": "Team"}]
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

    assert [r["_access_scope"] for r in records] == ["record"]


class TestMergeFallbacks:
    def _graph(self):
        graph = MagicMock()
        graph.get_records_by_record_ids = AsyncMock(side_effect=lambda record_ids, org_id: [
            {"_key": rid, "title": rid} for rid in record_ids
        ])
        return graph

    async def _merge(self, graph, raw):
        return await merge_pattern_match_results(
            raw_records=raw, virtual_record_id_to_result={}, user_id="u", org_id="o",
            blob_store=None, graph_provider=graph, is_multimodal_llm=False,
            logger_instance=MagicMock(),
        )

    @pytest.mark.asyncio
    async def test_failed_lightweight_lookup_uses_permission_check(self):
        graph = self._graph()
        graph.resolve_vrids_to_record_ids = AsyncMock(side_effect=RuntimeError("db"))
        graph.check_vrids_accessible = AsyncMock(return_value={"v1": "r1"})

        results = await self._merge(graph, [{"virtual_record_id": "v1", "_access_scope": "container"}])

        assert [r["virtual_record_id"] for r in results] == ["v1"]

    @pytest.mark.asyncio
    async def test_failed_record_check_keeps_container_results(self):
        graph = self._graph()
        graph.resolve_vrids_to_record_ids = AsyncMock(return_value={"v1": "r1"})
        graph.check_vrids_accessible = AsyncMock(side_effect=RuntimeError("db"))

        results = await self._merge(graph, [
            {"virtual_record_id": "v1", "_access_scope": "container"},
            {"virtual_record_id": "v2", "_access_scope": "record"},
        ])

        assert [r["virtual_record_id"] for r in results] == ["v1"]

    @pytest.mark.asyncio
    async def test_ranks_by_distinct_terms_before_repetition(self):
        graph = self._graph()
        graph.resolve_vrids_to_record_ids = AsyncMock(return_value={"noisy": "r1", "answer": "r2"})

        results = await self._merge(graph, [
            {"virtual_record_id": "noisy", "_access_scope": "container",
             "matched_terms": "1", "match_count": "50"},
            {"virtual_record_id": "answer", "_access_scope": "container",
             "matched_terms": "3", "match_count": "3"},
        ])

        assert [r["virtual_record_id"] for r in results] == ["answer", "noisy"]
