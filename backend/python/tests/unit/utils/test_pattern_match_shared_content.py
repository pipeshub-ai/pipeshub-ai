"""GREP_SHARED_CONTENT: a scoped keyword search also greps storage outside the
scope that holds content the scope's records share.

Deduplicated content is stored once, under the connector that indexed it first.
A KB whose files duplicate another KB's has no stored file of its own for them,
so grep searches that other KB's directory too. A hit there may surface only as
a record of the scope the user can read, and only when that record's own text
matched: the stored file carries the name of the record that stored it.
"""

import asyncio
import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.storage_search.storage_search import StoragePatternMatch
from app.services.graph_db.interface.graph_db_provider import SharedContentCount
from app.utils import pattern_match
from app.utils.pattern_match import (
    PatternMatchCoverage,
    content_owner_connector_ids,
    execute_pattern_match_pipeline,
    run_pattern_match,
    run_pattern_match_with_llm_grep,
)

needs_grep = pytest.mark.skipif(shutil.which("grep") is None, reason="needs grep")

SCOPE = ("kb-policies",)
OWNER = "kb-archive"


@pytest.fixture(autouse=True)
def _empty_caches() -> Iterator[None]:
    pattern_match._content_owner_cache.clear()
    pattern_match._grep_commands_cache.clear()
    yield
    pattern_match._content_owner_cache.clear()
    pattern_match._grep_commands_cache.clear()


def _graph(shares: dict[str, SharedContentCount] | Exception) -> MagicMock:
    graph = MagicMock()
    if isinstance(shares, Exception):
        graph.get_content_owner_connector_ids = AsyncMock(side_effect=shares)
    else:
        graph.get_content_owner_connector_ids = AsyncMock(return_value=shares)
    return graph


class TestContentOwnerConnectorIds:
    @pytest.mark.asyncio
    async def test_an_unscoped_search_needs_no_other_storage(self) -> None:
        graph = _graph({OWNER: SharedContentCount(shared=10, records=10)})

        assert await content_owner_connector_ids(graph, "org", None, MagicMock()) == []
        graph.get_content_owner_connector_ids.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_failed_lookup_greps_nothing_more_and_is_retried(self) -> None:
        graph = _graph(RuntimeError("graph down"))

        assert await content_owner_connector_ids(graph, "org", SCOPE, MagicMock()) == []
        assert await content_owner_connector_ids(graph, "org", SCOPE, MagicMock()) == []
        assert graph.get_content_owner_connector_ids.await_count == 2

    @pytest.mark.asyncio
    async def test_a_slow_lookup_is_given_up_and_not_repeated(self) -> None:
        graph = _graph({OWNER: SharedContentCount(shared=5, records=10)})

        with patch.object(pattern_match, "_CONTENT_OWNER_LOOKUP_TIMEOUT", 0.01):
            async def _slow(*_args: object) -> dict:
                await asyncio.sleep(1)
                return {}

            graph.get_content_owner_connector_ids = AsyncMock(side_effect=_slow)
            assert await content_owner_connector_ids(graph, "org", SCOPE, MagicMock()) == []
            assert await content_owner_connector_ids(graph, "org", SCOPE, MagicMock()) == []
        assert graph.get_content_owner_connector_ids.await_count == 1

    @pytest.mark.asyncio
    async def test_storage_with_little_shared_content_is_skipped(self) -> None:
        graph = _graph({
            OWNER: SharedContentCount(shared=812, records=2400),
            "kb-sprawl": SharedContentCount(shared=3, records=5000),
            "kb-policies": SharedContentCount(shared=5, records=5),
        })

        assert await content_owner_connector_ids(graph, "org", SCOPE, MagicMock()) == [OWNER]
        assert graph.get_content_owner_connector_ids.await_args.args == (list(SCOPE), "org")

    @pytest.mark.asyncio
    async def test_at_most_five_most_shared_first(self) -> None:
        graph = _graph({f"kb-{i}": SharedContentCount(shared=i, records=10) for i in range(1, 9)})

        assert await content_owner_connector_ids(graph, "org", SCOPE, MagicMock()) == [
            "kb-8", "kb-7", "kb-6", "kb-5", "kb-4",
        ]

    @pytest.mark.asyncio
    async def test_cached_per_org_and_scope(self) -> None:
        graph = _graph({OWNER: SharedContentCount(shared=5, records=10)})

        for _ in range(2):
            await content_owner_connector_ids(graph, "org", SCOPE, MagicMock())
        await content_owner_connector_ids(graph, "org-2", SCOPE, MagicMock())

        assert graph.get_content_owner_connector_ids.await_count == 2


def _storage(find: AsyncMock) -> MagicMock:
    tool = MagicMock()
    tool.find_records = find
    return tool


async def _run(tool: MagicMock, **kwargs: object) -> list[dict]:
    graph = AsyncMock()
    graph.get_accessible_containers = AsyncMock(return_value=None)
    with patch("app.utils.pattern_match.StoragePatternMatch", return_value=tool):
        return await run_pattern_match(
            config_service=MagicMock(), org_id="org", user_id="u", graph_provider=graph,
            command='grep -rci "x" .', logger_instance=MagicMock(), **kwargs,
        )


class TestOtherStorageIsGrepped:
    @pytest.mark.asyncio
    async def test_it_is_searched_with_the_request_scope(self) -> None:
        find = AsyncMock(return_value=(True, json.dumps({"records": [{"virtual_record_id": "vr-1"}]})))

        records = await _run(
            _storage(find), connector_ids=list(SCOPE),
            scope_connector_ids=frozenset(SCOPE), content_owner_ids=[OWNER],
        )

        calls = {c.kwargs["connector_id"]: c.kwargs for c in find.await_args_list}
        assert set(calls) == {"kb-policies", OWNER}
        assert calls[OWNER]["content_owner"] is True
        assert calls[OWNER]["scope_connector_ids"] == frozenset(SCOPE)
        assert calls["kb-policies"]["content_owner"] is False
        assert calls["kb-policies"]["scope_connector_ids"] is None
        assert len(records) == 2

    @pytest.mark.asyncio
    async def test_never_without_a_scope_nor_for_a_connector_already_in_it(self) -> None:
        find = AsyncMock(return_value=(True, json.dumps({"records": []})))

        await _run(_storage(find), connector_ids=["c1"], content_owner_ids=[OWNER])
        await _run(
            _storage(find), connector_ids=["c1"],
            scope_connector_ids=frozenset({"c1"}), content_owner_ids=["c1"],
        )

        assert [c.kwargs["connector_id"] for c in find.await_args_list] == ["c1", "c1"]

    @pytest.mark.asyncio
    async def test_its_failures_are_not_counted_as_the_scopes(self) -> None:
        async def _find(**kwargs: object) -> tuple[bool, str]:
            if kwargs["content_owner"]:
                raise RuntimeError("boom")
            return True, json.dumps({"records": []})

        coverage = PatternMatchCoverage()
        await _run(
            _storage(AsyncMock(side_effect=_find)), connector_ids=list(SCOPE), coverage=coverage,
            scope_connector_ids=frozenset(SCOPE), content_owner_ids=[OWNER],
        )

        assert coverage.sources == set(SCOPE)
        assert coverage.complete

    @pytest.mark.asyncio
    async def test_the_pipeline_passes_the_scope_it_searched(self) -> None:
        config = AsyncMock()
        config.get_config = AsyncMock(return_value={"storageType": "local"})
        with patch("app.utils.pattern_match.run_pattern_match", new_callable=AsyncMock, return_value=[]) as run:
            await execute_pattern_match_pipeline(
                query="q", config_service=config, org_id="org", user_id="u",
                graph_provider=AsyncMock(), filters={"kb": list(SCOPE)}, logger_instance=MagicMock(),
                grep_command='grep -rci "x" .', skip_grep_validation=True, content_owner_ids=[OWNER],
            )

        assert run.await_args.kwargs["scope_connector_ids"] == frozenset(SCOPE)
        assert run.await_args.kwargs["content_owner_ids"] == [OWNER]


class TestSwitch:
    @staticmethod
    async def _search(graph: MagicMock) -> AsyncMock:
        config = AsyncMock()
        config.get_config = AsyncMock(return_value={"storageType": "local"})
        with patch(
            "app.utils.pattern_match.generate_grep_command_via_llm",
            new_callable=AsyncMock, return_value=['grep -rci "x" .'],
        ), patch(
            "app.utils.pattern_match.execute_pattern_match_pipeline",
            new_callable=AsyncMock, return_value=[],
        ) as pipeline:
            await run_pattern_match_with_llm_grep(
                query="q", config_service=config, org_id="org", user_id="u",
                graph_provider=graph, filters={"kb": list(SCOPE)},
                logger_instance=MagicMock(), llm=MagicMock(),
            )
        return pipeline

    @pytest.mark.asyncio
    async def test_off_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GREP_SHARED_CONTENT", raising=False)
        monkeypatch.setenv("HIGH_ACCURACY_MODE", "true")
        graph = _graph({OWNER: SharedContentCount(shared=5, records=10)})

        pipeline = await self._search(graph)

        graph.get_content_owner_connector_ids.assert_not_awaited()
        assert pipeline.await_args.kwargs["content_owner_ids"] == []

    @pytest.mark.asyncio
    async def test_on_it_looks_the_storage_up_beside_command_generation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GREP_SHARED_CONTENT", "true")
        graph = _graph({OWNER: SharedContentCount(shared=5, records=10)})

        pipeline = await self._search(graph)

        assert pipeline.await_args.kwargs["content_owner_ids"] == [OWNER]


def _stored(name: str, text: str) -> str:
    return json.dumps({"isCompressed": False, "record": {
        "record_name": name,
        "block_containers": {"blocks": [{"data": text}]},
    }})


@needs_grep
class TestFindRecordsInOtherStorage:
    VR_TEXT = "11111111-0000-0000-0000-000000000000"
    VR_NAME = "22222222-0000-0000-0000-000000000000"

    @pytest.fixture
    def owner_dir(self, tmp_path: Path) -> str:
        (tmp_path / f"record_{self.VR_TEXT}.json").write_text(
            _stored("Storing record's title", "the lease renewal for the depot is due in April"))
        # Matches only through the stored name of the record that stored it.
        (tmp_path / f"record_{self.VR_NAME}.json").write_text(
            _stored("Depot lease Heron", "fuel invoices for the second half"))
        return str(tmp_path)

    @staticmethod
    def _graph(accessible: dict[str, str]) -> MagicMock:
        graph = MagicMock()
        graph.filter_accessible_virtual_record_ids = AsyncMock(return_value=accessible)
        graph.get_records_by_record_ids = AsyncMock(return_value=[
            {"_key": rid, "recordName": f"scope record {rid}"} for rid in accessible.values()
        ])
        return graph

    @staticmethod
    def _tool(graph: MagicMock, directory: str) -> StoragePatternMatch:
        config = AsyncMock()
        config.get_config = AsyncMock(return_value={"storageType": "local"})
        tool = StoragePatternMatch({"org_id": "org", "user_id": "u", "config_service": config, "graph_provider": graph})
        tool._resolve_connector_path = AsyncMock(return_value=(directory, None))
        return tool

    @pytest.mark.asyncio
    async def test_a_hit_on_the_stored_name_is_dropped(self, owner_dir: str) -> None:
        tool = self._tool(self._graph({self.VR_TEXT: "rid-text", self.VR_NAME: "rid-name"}), owner_dir)

        ok, output = await tool.find_records(
            OWNER, 'grep -rli "lease" .', scope_connector_ids=frozenset(SCOPE), content_owner=True,
        )

        assert ok
        records = json.loads(output)["records"]
        assert [r["record_id"] for r in records] == ["rid-text"]
        assert records[0]["record_name"] == "scope record rid-text", "named from the record of the scope"
        assert "Storing record" not in output
        _, unfiltered = await tool.find_records(OWNER, 'grep -rli "lease" .', scope_connector_ids=frozenset(SCOPE))
        assert len(json.loads(unfiltered)["records"]) == 2, "the stored name does match"

    @pytest.mark.asyncio
    async def test_every_stage_must_match_the_text(self, owner_dir: str) -> None:
        tool = self._tool(self._graph({self.VR_TEXT: "rid-text", self.VR_NAME: "rid-name"}), owner_dir)

        ok, output = await tool.find_records(
            OWNER, 'grep -rliZ "heron" . | xargs -0 grep -Hci "invoices"',
            scope_connector_ids=frozenset(SCOPE), content_owner=True,
        )

        assert ok
        assert json.loads(output)["records"] == []

    @pytest.mark.asyncio
    async def test_hits_name_only_records_of_the_scope(self, owner_dir: str) -> None:
        graph = self._graph({})
        tool = self._tool(graph, owner_dir)

        ok, output = await tool.find_records(
            OWNER, 'grep -rli "lease" .', scope_connector_ids=frozenset(SCOPE), content_owner=True,
        )

        assert ok
        assert json.loads(output)["records"] == []
        assert graph.filter_accessible_virtual_record_ids.await_args.kwargs["scope_connector_ids"] == frozenset(SCOPE)

    @pytest.mark.asyncio
    async def test_the_request_scope_narrows_the_agents(self, owner_dir: str) -> None:
        graph = self._graph({})
        tool = self._tool(graph, owner_dir)
        tool.state["filters"] = {"kb": ["kb-policies", "kb-drafts"]}

        await tool.find_records(
            "kb-policies", 'grep -rli "lease" .', scope_connector_ids=frozenset({"kb-policies", "kb-minutes"}),
        )

        assert graph.filter_accessible_virtual_record_ids.await_args.kwargs["scope_connector_ids"] == frozenset(
            {"kb-policies"},
        )

    @pytest.mark.asyncio
    async def test_scoped_and_unscoped_checks_are_cached_apart(self, owner_dir: str) -> None:
        graph = self._graph({self.VR_TEXT: "rid-text"})
        tool = self._tool(graph, owner_dir)

        await tool.find_records(OWNER, 'grep -rli "lease" .')
        await tool.find_records(OWNER, 'grep -rli "lease" .', scope_connector_ids=frozenset(SCOPE), content_owner=True)

        scopes = [c.kwargs["scope_connector_ids"] for c in graph.filter_accessible_virtual_record_ids.await_args_list]
        assert scopes == [None, frozenset(SCOPE)]

    @pytest.mark.asyncio
    async def test_an_unavailable_permission_check_fails_closed(self, owner_dir: str) -> None:
        graph = self._graph({})
        graph.filter_accessible_virtual_record_ids = AsyncMock(side_effect=RuntimeError("down"))
        tool = self._tool(graph, owner_dir)

        ok, output = await tool.find_records(
            OWNER, 'grep -rli "lease" .', scope_connector_ids=frozenset(SCOPE), content_owner=True,
        )

        assert not ok
        assert "lease" not in output.lower()
