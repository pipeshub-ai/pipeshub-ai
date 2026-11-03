"""`retrieve_artifact_content` pages a large artifact (CE-18).

It used to return one 35K-token slice with no way to read past it, which
the per-result cap (L1, `shape_budget_reduction`) then cut in the middle:
rows 930-2375 and everything after row 2676 of a 6,000-row artifact never
reached the model, while the marker claimed 35K tokens were shown.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from app.agent_loop_lib.context.base import ContextBudget
from app.agent_loop_lib.core.messages import AssistantMessage, ToolCall, ToolMessage
from app.agent_loop_lib.hooks.middleware.builtin.artifact_registration import (
    InMemoryArtifactStore,
)
from app.agent_loop_lib.hooks.middleware.builtin.budget_reduction import (
    DEFAULT_MAX_RESULT_CHARS,
    shape_budget_reduction,
)
from app.agent_loop_lib.tools.builtin.data.retrieve_artifact import (
    RETRIEVE_ARTIFACT_TOOL_NAME,
    RetrieveArtifactContentTool,
    refit_artifact_page,
)

_ROWS = 6000
_FOOTER = re.compile(
    r"\n\n\[Showing characters (?P<first>[\d,]+)–(?P<last>[\d,]+) of (?P<total>[\d,]+)\."
    r"(?: Continue with retrieve_artifact_content\(artifact_id=\"(?P<aid>[^\"]+)\", offset=(?P<next>\d+)\)\.)?\]$"
)


def _rows(n: int = _ROWS) -> str:
    return "\n".join(f"row {i:05d}: ticket PROJ-{i} status=open owner=user{i % 37}" for i in range(n))


async def _after_l1(content: str, *, max_tokens: int = 200_000, arguments: dict | None = None) -> str:
    """What the model receives once the per-result cap has run, wired as `build_context_pipeline` does."""
    call = ToolCall(id="r", name=RETRIEVE_ARTIFACT_TOOL_NAME, arguments=arguments or {})
    msgs = [AssistantMessage(content="", tool_calls=[call]), ToolMessage(tool_call_id="r", content=content)]
    ctx = SimpleNamespace(
        messages=msgs, budget=ContextBudget(max_tokens=max_tokens), turn_index=1, max_turns=12, scope=None,
        run_id="run-1",
    )

    async def _next() -> None:
        return None

    await shape_budget_reduction(refitters={RETRIEVE_ARTIFACT_TOOL_NAME: refit_artifact_page})(ctx, _next)
    return ctx.messages[1].content


async def _read_all(tool: RetrieveArtifactContentTool, artifact_id: str) -> list[str]:
    pages, offset = [], 0
    while True:
        out = await tool.execute(artifact_id=artifact_id, offset=offset)
        assert out.success, out.error
        pages.append(out.data)
        match = _FOOTER.search(out.data)
        if match is None or match["next"] is None:
            return pages
        offset = int(match["next"])
        assert len(pages) < 50


class TestPaging:
    async def test_every_row_is_reachable_and_reaches_the_model_uncut(self) -> None:
        store = InMemoryArtifactStore()
        content = _rows()
        aid = await store.store(content, tool_name="jira__search_issues")

        pages = await _read_all(RetrieveArtifactContentTool(store=store), aid)

        seen: list[int] = []
        for page in pages:
            assert await _after_l1(page) == page
            seen.extend(int(r) for r in re.findall(r"row (\d{5}):", page))
        assert seen == list(range(_ROWS))
        assert "".join(_FOOTER.sub("", p) for p in pages) == content

    async def test_footer_states_exactly_what_was_shown(self) -> None:
        store = InMemoryArtifactStore()
        content = _rows()
        aid = await store.store(content, tool_name="t")

        out = await RetrieveArtifactContentTool(store=store).execute(artifact_id=aid, offset=1000, limit=500)

        match = _FOOTER.search(out.data)
        body = _FOOTER.sub("", out.data)
        assert match is not None
        assert match["aid"] == aid
        assert int(match["first"].replace(",", "")) == 1001
        assert int(match["last"].replace(",", "")) == 1000 + len(body)
        assert int(match["total"].replace(",", "")) == len(content)
        assert int(match["next"]) == 1000 + len(body)
        assert body == content[1000:1000 + len(body)]
        assert 0 < len(body) <= 500

    async def test_a_page_ends_on_a_line_break_when_one_is_in_reach(self) -> None:
        store = InMemoryArtifactStore()
        aid = await store.store(_rows(), tool_name="t")

        out = await RetrieveArtifactContentTool(store=store).execute(artifact_id=aid)

        assert _FOOTER.sub("", out.data).endswith("\n")

    async def test_one_long_line_is_paged_by_characters(self) -> None:
        store = InMemoryArtifactStore()
        content = '{"rows": [' + ",".join(f'"{i:06d}"' for i in range(40_000)) + "]}"
        aid = await store.store(content, tool_name="t")

        pages = await _read_all(RetrieveArtifactContentTool(store=store), aid)

        assert len(pages) > 1
        assert "".join(_FOOTER.sub("", p) for p in pages) == content
        assert all(len(p) <= DEFAULT_MAX_RESULT_CHARS for p in pages)

    async def test_the_last_page_says_it_is_the_end(self) -> None:
        store = InMemoryArtifactStore()
        content = _rows(3000)
        aid = await store.store(content, tool_name="t")
        tool = RetrieveArtifactContentTool(store=store)
        pages = await _read_all(tool, aid)

        last = _FOOTER.search(pages[-1])

        assert last is not None and last["next"] is None
        assert int(last["last"].replace(",", "")) == len(content)

    async def test_a_small_artifact_comes_back_whole_without_a_footer(self) -> None:
        store = InMemoryArtifactStore()
        aid = await store.store("full data content here", tool_name="t")

        out = await RetrieveArtifactContentTool(store=store).execute(artifact_id=aid)

        assert out.data == "full data content here"

    @pytest.mark.parametrize("offset", [-1, 23])
    async def test_an_offset_outside_the_artifact_is_an_error(self, offset: int) -> None:
        store = InMemoryArtifactStore()
        aid = await store.store("full data content here", tool_name="t")

        out = await RetrieveArtifactContentTool(store=store).execute(artifact_id=aid, offset=offset)

        assert out.success is False
        assert "22" in out.error

    async def test_a_limit_above_the_page_size_is_held_to_it(self) -> None:
        store = InMemoryArtifactStore()
        aid = await store.store(_rows(), tool_name="t")

        out = await RetrieveArtifactContentTool(store=store).execute(artifact_id=aid, limit=10_000_000)

        assert len(out.data) <= DEFAULT_MAX_RESULT_CHARS
        assert await _after_l1(out.data) == out.data

    async def test_a_window_smaller_than_a_page_still_reaches_every_character(self) -> None:
        # L1 caps a result at the whole message budget: 3,000 tokens is 12,000 characters, a
        # fraction of a 63K page. Each page is read the way the model reads it, after L1.
        store = InMemoryArtifactStore()
        content = _rows()
        aid = await store.store(content, tool_name="t")
        tool = RetrieveArtifactContentTool(store=store)

        shown, offset = [], 0
        for _ in range(200):
            out = await tool.execute(artifact_id=aid, offset=offset)
            seen = await _after_l1(out.data, max_tokens=3_000, arguments={"artifact_id": aid, "offset": offset})
            assert len(seen) <= 12_000
            assert "truncated" not in seen
            shown.append(_FOOTER.sub("", seen))
            match = _FOOTER.search(seen)
            assert match is not None
            if match["next"] is None:
                break
            offset = int(match["next"])

        assert "".join(shown) == content

    async def test_a_whole_artifact_cut_by_the_window_gets_a_continuation(self) -> None:
        store = InMemoryArtifactStore()
        content = _rows(400)
        aid = await store.store(content, tool_name="t")

        out = await RetrieveArtifactContentTool(store=store).execute(artifact_id=aid)
        seen = await _after_l1(out.data, max_tokens=1_000, arguments={"artifact_id": aid})

        match = _FOOTER.search(seen)
        assert out.data == content
        assert match is not None and match["aid"] == aid
        assert content.startswith(_FOOTER.sub("", seen))
        assert int(match["next"]) == len(_FOOTER.sub("", seen))

    def test_a_cap_too_small_for_a_page_and_its_footer_is_refused(self) -> None:
        with pytest.raises(ValueError, match="max_result_chars=100"):
            RetrieveArtifactContentTool(store=InMemoryArtifactStore(), max_result_chars=100)

    async def test_a_small_cap_holds_page_and_footer(self) -> None:
        store = InMemoryArtifactStore()
        aid = await store.store(_rows(), tool_name="t")

        out = await RetrieveArtifactContentTool(store=store, max_result_chars=600).execute(artifact_id=aid)

        assert len(out.data) <= 600
        assert _FOOTER.search(out.data) is not None

    def test_offset_and_limit_are_declared(self) -> None:
        names = [p.name for p in RetrieveArtifactContentTool(store=InMemoryArtifactStore()).parameters]

        assert names == ["artifact_id", "offset", "limit"]
