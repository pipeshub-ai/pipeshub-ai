"""Nearest CLAUDE.md / AGENTS.md for retrieved code reaches the system prompt."""

import asyncio
import logging
from collections.abc import Callable
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from app.agents.agent_loop.hooks import repo_instructions as ri
from app.agents.agent_loop.hooks.repo_instructions import (
    SECTION_STATE_KEY,
    render_section,
    repo_instructions_on_turn,
    resolve_repo_instructions,
)
from app.agents.agent_loop.section_order import TURN_VOLATILE_NAMES
from app.services.record_content.models import (
    RecordAccessDeniedError,
    RecordContentUnavailableError,
    RecordNotFoundError,
    RecordTooLargeError,
)
from tests.support.record_access import InMemoryRecordAccess

ORG = "org-1"
USER = "user-1"


class FakeGraph:
    """Serves `codeFiles` by exact path, `records` by id, `recordGroups` by key.

    `files` maps a repo-relative path to `(record_id, record_group_id)`; several
    repos may hold the same path, which is the case repo scoping has to survive.

    `fail` maps a method name to the 1-based calls of it that fail. A failed
    call behaves like the real providers: `[]` unless `raise_on_error`, else
    `fail_with` is raised (an exception instance); "hang" never answers.
    """

    def __init__(self, files: dict, group_names: dict | None = None, *,
                 fail: dict | None = None, fail_with: object = None) -> None:
        self.files = files
        self.group_names = group_names or {}
        self.path_lookups = []
        self.fail = fail or {}
        self.fail_with = fail_with or ConnectionError("graph down at 10.0.0.7")
        self.calls: dict[str, int] = {}

    async def _failed(self, method: str, raise_on_error: bool) -> bool:
        self.calls[method] = self.calls.get(method, 0) + 1
        if self.calls[method] not in self.fail.get(method, ()):
            return False
        if self.fail_with == "hang":
            await asyncio.Event().wait()
        # a provider catches Exception only: cancellation reaches the caller either way
        if raise_on_error or not isinstance(self.fail_with, Exception):
            raise self.fail_with
        return True

    async def get_nodes_by_filters(
        self, collection: str, filters: dict, return_fields: list | None = None,
        transaction: str | None = None, *, raise_on_error: bool = False,
    ) -> list[dict]:
        assert collection == "codeFiles"
        assert filters["orgId"] == ORG
        path = filters["filePath"]
        self.path_lookups.append(path)
        if await self._failed("get_nodes_by_filters", raise_on_error):
            return []
        return [{"_key": rid} for rid, _ in self.files.get(path, [])]

    async def get_records_by_record_ids(
        self, record_ids: list[str], org_id: str, *, raise_on_error: bool = False,
    ) -> list[dict]:
        assert org_id == ORG
        if await self._failed("get_records_by_record_ids", raise_on_error):
            return []
        by_id = {rid: grp for entries in self.files.values() for rid, grp in entries}
        return [
            {"_key": rid, "recordGroupId": by_id[rid]}
            for rid in record_ids
            if rid in by_id
        ]

    async def get_document(
        self, document_key: str, collection: str, transaction: str | None = None,
    ) -> dict | None:
        assert collection == "recordGroups"
        name = self.group_names.get(document_key)
        return {"groupName": name} if name else None


def make_context(graph: FakeGraph, anchors: list, contents: dict) -> tuple:
    """`anchors` are `(record_id, file_path, connector)`; `contents` id -> text."""
    virtual_records = {
        f"vr-{i}": {
            "id": rid,
            "record_type": "CODE_FILE",
            "file_path": path,
            "connector_name": connector,
        }
        for i, (rid, path, connector) in enumerate(anchors)
    }
    return SimpleNamespace(
        graph_provider=graph,
        config_service=None,
        blob_store=None,
        artifact_registry=None,
        org_id=ORG,
        user_id=USER,
        tool_state={"virtual_record_id_to_result": virtual_records},
    ), contents


@pytest.fixture
def patched_fetch(monkeypatch: pytest.MonkeyPatch) -> Callable:
    """Replace the ACL'd content resolver with a dict lookup.

    Returns the list every fetched record id is appended to, so a test can
    assert a file is not re-fetched.
    """
    fetches: list[str] = []

    def _install(contents: dict, denied: tuple = ()) -> list[str]:
        async def _fake(context: object, record_ids: list[str], file_cache: dict[str, str]) -> None:
            fetches.extend(record_ids)
            file_cache.update({
                r: ("" if r in denied else contents.get(r, ""))
                for r in record_ids
            })

        monkeypatch.setattr(
            "app.agents.agent_loop.hooks.repo_instructions._fetch_contents", _fake,
        )
        return fetches
    return _install


class TestNearestWins:
    @pytest.mark.asyncio
    async def test_nested_claude_md_beats_repo_root(self, patched_fetch) -> None:
        graph = FakeGraph(
            files={
                "frontend/CLAUDE.md": [("rec-frontend", "grp-a")],
                "CLAUDE.md": [("rec-root", "grp-a")],
            },
            group_names={"grp-a": "pipeshub-ai"},
        )
        graph.files["frontend/app/kb/page.tsx"] = [("rec-anchor", "grp-a")]
        context, contents = make_context(
            graph,
            [("rec-anchor", "frontend/app/kb/page.tsx", "GITHUB")],
            {"rec-frontend": "UI conventions here", "rec-root": "repo-wide rules"},
        )
        patched_fetch(contents)

        section = await resolve_repo_instructions(context)

        assert section is not None
        assert "UI conventions here" in section
        assert "repo-wide rules" not in section
        assert "frontend/CLAUDE.md" in section
        assert "pipeshub-ai" in section

    @pytest.mark.asyncio
    async def test_claude_md_preferred_over_agents_md_in_same_dir(self, patched_fetch) -> None:
        graph = FakeGraph(files={
            "backend/CLAUDE.md": [("rec-claude", "grp-a")],
            "backend/AGENTS.md": [("rec-agents", "grp-a")],
            "backend/api.py": [("rec-anchor", "grp-a")],
        })
        context, contents = make_context(
            graph,
            [("rec-anchor", "backend/api.py", "GITHUB")],
            {"rec-claude": "claude text", "rec-agents": "agents text"},
        )
        patched_fetch(contents)

        section = await resolve_repo_instructions(context)

        assert "claude text" in section
        assert "agents text" not in section

    @pytest.mark.asyncio
    async def test_falls_through_to_repo_root_when_no_nested_file(self, patched_fetch) -> None:
        graph = FakeGraph(files={
            "AGENTS.md": [("rec-root", "grp-a")],
            "a/b/c/deep.py": [("rec-anchor", "grp-a")],
        })
        context, contents = make_context(
            graph, [("rec-anchor", "a/b/c/deep.py", "GITLAB")], {"rec-root": "root rules"},
        )
        patched_fetch(contents)

        section = await resolve_repo_instructions(context)

        assert "root rules" in section


class TestRepoIsolation:
    @pytest.mark.asyncio
    async def test_other_repos_root_file_is_never_used(self, patched_fetch) -> None:
        """Two repos in one connector, same path. Connector scoping alone would leak."""
        graph = FakeGraph(files={
            "CLAUDE.md": [("rec-a-root", "grp-a"), ("rec-b-root", "grp-b")],
            "src/main.py": [("rec-anchor", "grp-b")],
        })
        context, contents = make_context(
            graph,
            [("rec-anchor", "src/main.py", "GITHUB")],
            {"rec-a-root": "repo A rules", "rec-b-root": "repo B rules"},
        )
        patched_fetch(contents)

        section = await resolve_repo_instructions(context)

        assert "repo B rules" in section
        assert "repo A rules" not in section


class TestAnchorGating:
    @pytest.mark.asyncio
    async def test_kb_uploaded_code_file_is_skipped(self, patched_fetch) -> None:
        """No codeFiles row means file_path is a bare basename — unwalkable."""
        graph = FakeGraph(files={"CLAUDE.md": [("rec-root", "grp-a")]})
        context, contents = make_context(
            graph,
            [("rec-anchor", "helpers.py", "KNOWLEDGE_BASE")],
            {"rec-root": "root rules"},
        )
        patched_fetch(contents)

        assert await resolve_repo_instructions(context) is None
        assert graph.path_lookups == []

    @pytest.mark.asyncio
    async def test_non_code_retrieval_resolves_nothing(self, patched_fetch) -> None:
        graph = FakeGraph(files={"CLAUDE.md": [("rec-root", "grp-a")]})
        context, _ = make_context(graph, [], {})
        context.tool_state["virtual_record_id_to_result"] = {
            "vr-1": {"id": "t-1", "record_type": "TICKET", "connector_name": "JIRA"},
        }
        patched_fetch({})

        assert await resolve_repo_instructions(context) is None
        assert graph.path_lookups == []


class TestFailureIsSilent:
    @pytest.mark.asyncio
    async def test_unreadable_file_yields_no_section_and_is_not_retried(
        self, patched_fetch,
    ) -> None:
        graph = FakeGraph(files={
            "CLAUDE.md": [("rec-root", "grp-a")],
            "main.py": [("rec-anchor", "grp-a")],
        })
        context, contents = make_context(
            graph, [("rec-anchor", "main.py", "GITHUB")], {"rec-root": "secret rules"},
        )
        fetches = patched_fetch(contents, denied=("rec-root",))

        assert await resolve_repo_instructions(context) is None
        assert await resolve_repo_instructions(context) is None
        assert fetches == ["rec-root"], "a denied file is attempted once per conversation"


class TestRendering:
    def test_shallower_path_survives_truncation(self) -> None:
        section = render_section([
            ("repo", "frontend/app/CLAUDE.md", "N" * 9_000),
            ("repo", "CLAUDE.md", "R" * 8_000),
        ])

        root_at = section.index("— CLAUDE.md")
        nested_at = section.index("— frontend/app/CLAUDE.md")
        assert root_at < nested_at, "root file must come first"
        assert "R" * 8_000 in section, "root file must survive whole"
        assert "truncated" in section
        assert "N" * 9_000 not in section

    def test_empty_entries_render_nothing(self) -> None:
        assert render_section([]) is None


class TestRepeatTurns:
    @pytest.mark.asyncio
    async def test_unchanged_retrieval_does_not_re_query_on_a_later_turn(
        self, patched_fetch,
    ) -> None:
        graph = FakeGraph(files={
            "CLAUDE.md": [("rec-root", "grp-a")],
            "main.py": [("rec-anchor", "grp-a")],
        })
        context, contents = make_context(
            graph, [("rec-anchor", "main.py", "GITHUB")], {"rec-root": "root rules"},
        )
        patched_fetch(contents)

        first = await resolve_repo_instructions(context)
        lookups_after_first = len(graph.path_lookups)
        second = await resolve_repo_instructions(context)

        assert first == second
        assert len(graph.path_lookups) == lookups_after_first


class TestMiddleware:
    @pytest.mark.asyncio
    async def test_pre_turn_publishes_the_section_for_the_prompt_builder(
        self, patched_fetch,
    ) -> None:
        graph = FakeGraph(files={
            "CLAUDE.md": [("rec-root", "grp-a")],
            "main.py": [("rec-anchor", "grp-a")],
        })
        context, contents = make_context(
            graph, [("rec-anchor", "main.py", "GITHUB")], {"rec-root": "root rules"},
        )
        patched_fetch(contents)
        called = False

        async def _next() -> None:
            nonlocal called
            called = True

        await repo_instructions_on_turn(context)(SimpleNamespace(scope=None), _next)

        assert called
        assert "root rules" in context.tool_state[SECTION_STATE_KEY]


class TestSectionPlacement:
    def test_repo_instructions_is_cacheable_not_turn_volatile(self) -> None:
        assert "repo_instructions" not in TURN_VOLATILE_NAMES


# --- A failed lookup is retried, a definitive answer is not ----------------------------------

ROOT_REPO = {"CLAUDE.md": [("rec-root", "grp-a")], "main.py": [("rec-anchor", "grp-a")]}
MAIN_ANCHOR = [("rec-anchor", "main.py", "GITHUB")]


async def _turn(context: SimpleNamespace) -> str | None:
    """One PRE_TURN refresh through the public hook; the section it left for the prompt."""
    async def _next() -> None:
        return None

    await repo_instructions_on_turn(context)(SimpleNamespace(scope=None), _next)
    return context.tool_state.get(SECTION_STATE_KEY)


@pytest.fixture
def scripted_resolver(monkeypatch: pytest.MonkeyPatch) -> Callable:
    """The real `_fetch_contents` over a resolver that answers each ref from a script.

    `scripts` maps a ref to its answers in call order (the last one repeats);
    an exception instance is raised, "hang" never answers. Returns the list of
    refs resolved.
    """
    calls: list[str] = []

    def _install(scripts: dict) -> list[str]:
        remaining = {ref: list(answers) for ref, answers in scripts.items()}

        class _Resolver:
            def __init__(self, **_: object) -> None:
                pass

            async def resolve(self, *, actor: object, ref: str, max_bytes: int,
                              session: object) -> SimpleNamespace:
                calls.append(ref)
                answers = remaining[ref]
                answer = answers.pop(0) if len(answers) > 1 else answers[0]
                if answer == "hang":
                    await asyncio.Event().wait()
                if isinstance(answer, BaseException):
                    raise answer
                return SimpleNamespace(content=answer)

        monkeypatch.setattr(ri, "RecordContentResolver", _Resolver)
        return calls
    return _install


def _events(caplog: pytest.LogCaptureFixture, event: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == event]


class TestOutageIsRetried:
    @pytest.mark.asyncio
    async def test_unavailable_file_is_fetched_again_and_renders_once_back(
        self, scripted_resolver,
    ) -> None:
        calls = scripted_resolver({"rec-root": [
            RecordContentUnavailableError("graph did not answer"), b"root rules",
        ]})
        context, _ = make_context(FakeGraph(dict(ROOT_REPO)), MAIN_ANCHOR, {})

        assert await _turn(context) is None
        section = await _turn(context)

        assert calls == ["rec-root", "rec-root"]
        assert section is not None and "root rules" in section

    @pytest.mark.asyncio
    async def test_path_lookup_outage_caches_no_miss(self, scripted_resolver) -> None:
        scripted_resolver({"rec-root": [b"root rules"]})
        graph = FakeGraph(dict(ROOT_REPO), fail={"get_nodes_by_filters": {1, 2}})
        context, _ = make_context(graph, MAIN_ANCHOR, {})

        assert await _turn(context) is None
        assert context.tool_state.get("repo_instructions_dir_cache") == {}
        section = await _turn(context)

        assert graph.calls["get_nodes_by_filters"] == 4
        assert section is not None and "root rules" in section

    @pytest.mark.asyncio
    async def test_anchor_repo_lookup_outage_keeps_the_anchor(self, scripted_resolver) -> None:
        scripted_resolver({"rec-root": [b"root rules"]})
        graph = FakeGraph(dict(ROOT_REPO), fail={"get_records_by_record_ids": {1}})
        context, _ = make_context(graph, MAIN_ANCHOR, {})

        assert await _turn(context) is None
        assert not context.tool_state.get("repo_instructions_seen_anchors")
        section = await _turn(context)

        assert section is not None and "root rules" in section

    @pytest.mark.asyncio
    async def test_failed_walk_level_never_falls_back_to_a_farther_file(
        self, scripted_resolver,
    ) -> None:
        scripted_resolver({"rec-nested": [b"frontend rules"], "rec-root": [b"root rules"]})
        graph = FakeGraph({
            "frontend/CLAUDE.md": [("rec-nested", "grp-a")],
            "CLAUDE.md": [("rec-root", "grp-a")],
            "frontend/page.tsx": [("rec-anchor", "grp-a")],
        }, fail={"get_records_by_record_ids": {2}})  # 1: the anchor's repo; 2: level 0's matches
        context, _ = make_context(graph, [("rec-anchor", "frontend/page.tsx", "GITHUB")], {})

        assert await _turn(context) is None
        dir_cache = context.tool_state["repo_instructions_dir_cache"]
        assert ("grp-a", "frontend") not in dir_cache
        assert ("grp-a", "") not in dir_cache
        section = await _turn(context)

        assert section is not None and "frontend rules" in section
        assert "root rules" not in section

    @pytest.mark.asyncio
    async def test_timeout_mid_walk_is_retried(
        self, scripted_resolver, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        scripted_resolver({"rec-root": [b"root rules"]})
        graph = FakeGraph(dict(ROOT_REPO), fail={"get_nodes_by_filters": {1}}, fail_with="hang")
        context, _ = make_context(graph, MAIN_ANCHOR, {})

        monkeypatch.setattr(ri, "_RESOLVE_TIMEOUT_SECONDS", 0.05)
        assert await _turn(context) is None
        monkeypatch.setattr(ri, "_RESOLVE_TIMEOUT_SECONDS", 10.0)
        section = await _turn(context)

        assert section is not None and "root rules" in section

    @pytest.mark.asyncio
    async def test_one_unavailable_file_does_not_hold_back_the_others(
        self, scripted_resolver,
    ) -> None:
        calls = scripted_resolver({
            "rec-a": [b"repo a rules"],
            "rec-b": [RecordContentUnavailableError("down"), b"repo b rules"],
        })
        graph = FakeGraph({
            "CLAUDE.md": [("rec-a", "grp-a"), ("rec-b", "grp-b")],
            "a.py": [("anchor-a", "grp-a")],
            "b.py": [("anchor-b", "grp-b")],
        })
        context, _ = make_context(
            graph, [("anchor-a", "a.py", "GITHUB"), ("anchor-b", "b.py", "GITHUB")], {},
        )

        first = await _turn(context)
        second = await _turn(context)

        assert "repo a rules" in first and "repo b rules" not in first
        assert "repo a rules" in second and "repo b rules" in second
        assert sorted(calls) == ["rec-a", "rec-b", "rec-b"]

    @pytest.mark.asyncio
    async def test_a_failed_refresh_keeps_the_section_already_published(
        self, scripted_resolver,
    ) -> None:
        scripted_resolver({"rec-root": [b"root rules"]})
        graph = FakeGraph(dict(ROOT_REPO), fail={"get_records_by_record_ids": {3}})
        context, _ = make_context(graph, MAIN_ANCHOR, {})
        published = await _turn(context)
        context.tool_state["virtual_record_id_to_result"]["vr-new"] = {
            "id": "rec-new", "record_type": "CODE_FILE", "file_path": "lib/x.py",
            "connector_name": "GITHUB",
        }

        assert await _turn(context) == published
        assert graph.calls["get_records_by_record_ids"] == 3

    @pytest.mark.asyncio
    async def test_cancellation_propagates_and_leaves_the_anchor_unwalked(
        self, scripted_resolver,
    ) -> None:
        scripted_resolver({"rec-root": [b"root rules"]})
        graph = FakeGraph(dict(ROOT_REPO), fail={"get_records_by_record_ids": {1}},
                          fail_with=asyncio.CancelledError())
        context, _ = make_context(graph, MAIN_ANCHOR, {})

        with pytest.raises(asyncio.CancelledError):
            await _turn(context)

        assert not context.tool_state.get("repo_instructions_seen_anchors")
        assert not context.tool_state.get("repo_instructions_attempts")


class TestAccessCheckOutage:
    @pytest.mark.asyncio
    async def test_a_store_error_from_the_access_check_is_retried(
        self, scripted_resolver,
    ) -> None:
        # the authorizer lets the store's own error through, unwrapped
        calls = scripted_resolver({"rec-root": [ConnectionError("permission query failed"), b"root rules"]})
        context, _ = make_context(FakeGraph(dict(ROOT_REPO)), MAIN_ANCHOR, {})

        assert await _turn(context) is None
        section = await _turn(context)

        assert calls == ["rec-root", "rec-root"]
        assert section is not None and "root rules" in section


class TestDefinitiveAnswersAreCached:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("answer", [
        b"root rules",
        RecordNotFoundError("Record not found"),
        RecordAccessDeniedError("denied"),
        RecordTooLargeError("rec-root", 100_000, 65_536),
    ], ids=["found", "not_found", "denied", "too_large"])
    async def test_fetched_once_per_run(self, scripted_resolver, answer) -> None:
        calls = scripted_resolver({"rec-root": [answer]})
        context, _ = make_context(FakeGraph(dict(ROOT_REPO)), MAIN_ANCHOR, {})

        first = await _turn(context)
        second = await _turn(context)

        assert calls == ["rec-root"]
        assert first == second
        assert (first is not None) == (answer == b"root rules")

    @pytest.mark.asyncio
    async def test_a_bug_in_one_file_costs_only_that_file(
        self, scripted_resolver, caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.DEBUG)
        calls = scripted_resolver({
            "rec-a": [TypeError("bad call")], "rec-b": [b"repo b rules"],
        })
        graph = FakeGraph({
            "CLAUDE.md": [("rec-a", "grp-a"), ("rec-b", "grp-b")],
            "a.py": [("anchor-a", "grp-a")],
            "b.py": [("anchor-b", "grp-b")],
        })
        context, _ = make_context(
            graph, [("anchor-a", "a.py", "GITHUB"), ("anchor-b", "b.py", "GITHUB")], {},
        )

        first = await _turn(context)
        await _turn(context)

        assert "repo b rules" in first
        assert calls.count("rec-a") == 1
        (record,) = _events(caplog, "repo_instructions.fetch_failed")
        assert record.levelno == logging.ERROR
        assert record.error_class == "internal"
        assert record.record_id == "rec-a"


class TestRetryBudget:
    @pytest.mark.asyncio
    async def test_a_file_that_stays_unavailable_is_tried_three_times(
        self, scripted_resolver,
    ) -> None:
        calls = scripted_resolver({"rec-root": [RecordContentUnavailableError("gone")]})
        context, _ = make_context(FakeGraph(dict(ROOT_REPO)), MAIN_ANCHOR, {})

        for _ in range(5):
            assert await _turn(context) is None

        assert calls == ["rec-root"] * 3

    @pytest.mark.asyncio
    async def test_a_graph_that_hangs_costs_at_most_three_timeouts(
        self, scripted_resolver, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        scripted_resolver({"rec-root": [b"root rules"]})
        graph = FakeGraph(dict(ROOT_REPO), fail={"get_records_by_record_ids": set(range(1, 10))},
                          fail_with="hang")
        context, _ = make_context(graph, MAIN_ANCHOR, {})
        monkeypatch.setattr(ri, "_RESOLVE_TIMEOUT_SECONDS", 0.05)

        for _ in range(5):
            assert await _turn(context) is None

        assert graph.calls["get_records_by_record_ids"] == 3

    @pytest.mark.asyncio
    async def test_a_fetch_that_hangs_costs_at_most_three_timeouts(
        self, scripted_resolver, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls = scripted_resolver({"rec-root": ["hang"]})
        context, _ = make_context(FakeGraph(dict(ROOT_REPO)), MAIN_ANCHOR, {})
        monkeypatch.setattr(ri, "_RESOLVE_TIMEOUT_SECONDS", 0.05)

        for _ in range(5):
            assert await _turn(context) is None

        assert calls == ["rec-root"] * 3


    @pytest.mark.asyncio
    async def test_a_file_that_arrived_is_kept_when_another_hangs(
        self, scripted_resolver, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls = scripted_resolver({"rec-a": [b"repo a rules"], "rec-b": ["hang"]})
        graph = FakeGraph({
            "CLAUDE.md": [("rec-a", "grp-a"), ("rec-b", "grp-b")],
            "a.py": [("anchor-a", "grp-a")],
            "b.py": [("anchor-b", "grp-b")],
        })
        context, _ = make_context(
            graph, [("anchor-a", "a.py", "GITHUB"), ("anchor-b", "b.py", "GITHUB")], {},
        )
        monkeypatch.setattr(ri, "_RESOLVE_TIMEOUT_SECONDS", 0.05)

        for _ in range(3):  # each times out on rec-b; the third gives rec-b up
            assert await _turn(context) is None
        section = await _turn(context)

        assert section is not None and "repo a rules" in section
        assert sorted(calls) == ["rec-a", "rec-b", "rec-b", "rec-b"]

    @pytest.mark.asyncio
    async def test_a_new_anchor_after_three_graph_timeouts_reads_no_graph(
        self, scripted_resolver, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        scripted_resolver({"rec-root": [b"root rules"]})
        graph = FakeGraph(dict(ROOT_REPO))
        context, _ = make_context(graph, MAIN_ANCHOR, {})
        published = await _turn(context)
        reads = graph.calls["get_records_by_record_ids"]
        graph.fail, graph.fail_with = {"get_records_by_record_ids": set(range(reads + 1, reads + 10))}, "hang"
        monkeypatch.setattr(ri, "_RESOLVE_TIMEOUT_SECONDS", 0.05)
        anchors = context.tool_state["virtual_record_id_to_result"]

        for n in range(4):  # a new code hit on every turn: three time out, the fourth is not walked
            anchors[f"vr-new-{n}"] = {
                "id": f"rec-new-{n}", "record_type": "CODE_FILE", "file_path": f"lib/x{n}.py",
                "connector_name": "GITHUB",
            }
            assert await _turn(context) == published

        assert graph.calls["get_records_by_record_ids"] == reads + 3
        context.tool_state.pop(SECTION_STATE_KEY)
        assert await _turn(context) == published  # rendered again from the cached file


class ConnectorGraph(FakeGraph):
    """FakeGraph that also serves the record and the permission check the real resolver reads."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        InMemoryRecordAccess().readable(USER, ORG, "rec-root").serve(self)

    async def get_record_by_id(self, record_id: str, *, raise_on_error: bool = False) -> dict:
        return {"_key": record_id, "orgId": ORG, "origin": "CONNECTOR", "recordName": "CLAUDE.md"}


class TestConnectorAnswer:
    """The real resolver and connector strategy against a connector that answers one status."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("status", "requests"), [(403, 1), (503, 3)], ids=["denied", "outage"])
    async def test_a_denial_is_asked_once_and_an_outage_three_times(self, status: int, requests: int) -> None:
        hits: list[str] = []

        async def _content(request: web.Request) -> web.Response:
            hits.append(request.match_info["record_id"])
            return web.Response(status=status)

        app = web.Application()
        app.router.add_get("/api/v1/internal/records/{record_id}/content", _content)
        async with TestServer(app) as server:
            async def _get_config(path: str, **_: object) -> dict:
                if "secret" in path.lower():
                    return {"scopedJwtSecret": "test-secret"}
                return {"connectors": {"endpoint": str(server.make_url(""))}}

            context, _ = make_context(ConnectorGraph(dict(ROOT_REPO)), MAIN_ANCHOR, {})
            context.config_service = SimpleNamespace(get_config=_get_config)

            for _ in range(5):
                assert await _turn(context) is None

        assert hits == ["rec-root"] * requests


class TestFailureLogging:
    @pytest.mark.asyncio
    async def test_unavailable_file_logs_one_warning_without_its_text(
        self, scripted_resolver, caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.DEBUG)
        scripted_resolver({"rec-root": [
            RecordContentUnavailableError("connector said: secret body"), b"root rules",
        ]})
        context, _ = make_context(FakeGraph(dict(ROOT_REPO)), MAIN_ANCHOR, {})

        await _turn(context)

        (record,) = _events(caplog, "repo_instructions.fetch_failed")
        assert record.levelno == logging.WARNING
        assert record.error_class == "unavailable"
        assert record.record_id == "rec-root"
        assert not hasattr(record, "error_message")
        assert "secret body" not in caplog.text

    @pytest.mark.asyncio
    async def test_graph_outage_logs_one_warning_without_driver_text(
        self, scripted_resolver, caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.DEBUG)
        scripted_resolver({"rec-root": [b"root rules"]})
        graph = FakeGraph(dict(ROOT_REPO), fail={"get_records_by_record_ids": {1}})
        context, _ = make_context(graph, MAIN_ANCHOR, {})

        await _turn(context)

        (record,) = _events(caplog, "repo_instructions.resolve_failed")
        assert record.levelno == logging.WARNING
        assert record.error_class == "unavailable"
        assert not hasattr(record, "error_message")
        assert "10.0.0.7" not in caplog.text

    @pytest.mark.asyncio
    async def test_timeout_logs_one_warning(
        self, scripted_resolver, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        caplog.set_level(logging.DEBUG)
        scripted_resolver({"rec-root": [b"root rules"]})
        graph = FakeGraph(dict(ROOT_REPO), fail={"get_nodes_by_filters": {1}}, fail_with="hang")
        context, _ = make_context(graph, MAIN_ANCHOR, {})
        monkeypatch.setattr(ri, "_RESOLVE_TIMEOUT_SECONDS", 0.05)

        await _turn(context)

        (record,) = _events(caplog, "repo_instructions.resolve_failed")
        assert record.levelno == logging.WARNING
        assert record.error_class == "timeout"
