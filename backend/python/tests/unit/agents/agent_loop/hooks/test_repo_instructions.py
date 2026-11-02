"""Nearest CLAUDE.md / AGENTS.md for retrieved code reaches the system prompt."""

from collections.abc import Callable
from types import SimpleNamespace

import pytest

from app.agents.agent_loop.hooks.repo_instructions import (
    SECTION_STATE_KEY,
    render_section,
    repo_instructions_on_turn,
    resolve_repo_instructions,
)
from app.agents.agent_loop.section_order import TURN_VOLATILE_NAMES

ORG = "org-1"
USER = "user-1"


class FakeGraph:
    """Serves `codeFiles` by exact path, `records` by id, `recordGroups` by key.

    `files` maps a repo-relative path to `(record_id, record_group_id)`; several
    repos may hold the same path, which is the case repo scoping has to survive.
    """

    def __init__(self, files: dict, group_names: dict | None = None) -> None:
        self.files = files
        self.group_names = group_names or {}
        self.path_lookups = []

    async def get_nodes_by_filters(
        self, collection: str, filters: dict, return_fields: list | None = None,
        transaction: str | None = None,
    ) -> list[dict]:
        assert collection == "codeFiles"
        assert filters["orgId"] == ORG
        path = filters["filePath"]
        self.path_lookups.append(path)
        return [{"_key": rid} for rid, _ in self.files.get(path, [])]

    async def get_records_by_record_ids(self, record_ids: list[str], org_id: str) -> list[dict]:
        assert org_id == ORG
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
        async def _fake(context: object, record_ids: list[str]) -> dict[str, str]:
            fetches.extend(record_ids)
            return {
                r: ("" if r in denied else contents.get(r, ""))
                for r in record_ids
            }

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
