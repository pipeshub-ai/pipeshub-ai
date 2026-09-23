"""Showing each knowledge block once: which copy stays, and what the others become."""

from __future__ import annotations

import random

from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    ImagePart,
    ImageSource,
    TextPart,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from app.agents.agent_loop.hooks.duplicate_blocks import elide_duplicates
from app.models.blocks import BlockType, GroupType
from app.modules.retrieval.context.manifest import (
    BlockKey,
    ContentManifest,
    ManifestRegistry,
    ManifestSource,
)
from app.modules.retrieval.context.renderer import render_knowledge
from app.utils.chat_helpers import CitationRefMapper

_HEADER = "Top N blocks from M records.\n\n"


def _record(vrid: str) -> dict:
    return {
        "id": f"id-{vrid}",
        "virtual_record_id": vrid,
        "record_name": f"{vrid.upper()} report",
        "context_metadata": f"Record ID: id-{vrid}\nName: {vrid.upper()} report",
        "frontend_url": "https://app.example",
        "block_containers": {"blocks": [], "block_groups": []},
    }


RECORDS = {v: _record(v) for v in ("a", "b", "c")}


def _text(vrid: str, index: int) -> dict:
    return {"virtual_record_id": vrid, "block_index": index, "block_type": BlockType.TEXT.value,
            "content": f"{vrid} block {index} text", "metadata": {}}


def _table(vrid: str, rows: list[int]) -> dict:
    children = [{"virtual_record_id": vrid, "block_index": r, "block_type": BlockType.TABLE_ROW.value,
                 "content": f"{vrid} row {r}", "metadata": {}} for r in rows]
    return {"virtual_record_id": vrid, "block_index": min(rows), "block_group_index": 0,
            "block_type": GroupType.TABLE.value, "content": ("Revenue by quarter", children),
            "metadata": {}}


class _Conversation:
    """Builds a message history the way the tools and bridge register it."""

    def __init__(self) -> None:
        self.registry = ManifestRegistry()
        self.ref_mapper = CitationRefMapper()
        self.messages: list = [UserMessage(content="question")]

    def _tool(self, name: str, content: str | list) -> int:
        call_id = f"call-{len(self.messages)}"
        self.messages.append(AssistantMessage(content="", tool_calls=[ToolCall(id=call_id, name=name, arguments={})]))
        self.messages.append(ToolMessage(content=content, tool_call_id=call_id))
        return len(self.messages) - 1

    def search(self, units: list[dict], *, image: bool = False) -> int:
        rendered = render_knowledge(units, RECORDS, ref_mapper=self.ref_mapper, is_multimodal_llm=False)
        text = _HEADER + rendered.text + "\n\nTip: fetch a record to read it whole."
        self.registry.register(text, rendered.manifest.shifted(len(_HEADER)))
        if image:
            return self._tool("knowledgegraph__search", [
                TextPart(text=text),
                ImagePart(source=ImageSource(type="base64", media_type="image/png", data="iVBORw0KGgo=")),
            ])
        return self._tool("knowledgegraph__search", text)

    def fetch(self, shown: dict[str, list[int]]) -> int:
        text = "".join(
            f"<record>\nRecord ID: id-{vrid}\n" + "".join(f"[ref] {vrid} block {i} text\n" for i in idx) + "</record>\n"
            for vrid, idx in shown.items()
        )
        manifest = ContentManifest(
            ManifestSource.FETCH, (), (),
            shown_blocks=frozenset(BlockKey(v, i) for v, idx in shown.items() for i in idx),
        )
        self.registry.register(text, manifest)
        return self._tool("knowledgegraph__fetch_record", text)

    def prefetch(self, units: list[dict]) -> None:
        rendered = render_knowledge(units, RECORDS, ref_mapper=self.ref_mapper, is_multimodal_llm=False,
                                    source=ManifestSource.PREFETCH)
        self.registry.register_prefetch(rendered.manifest)

    def shaped(self, messages: list | None = None) -> list:
        return elide_duplicates(
            messages if messages is not None else self.messages, self.registry,
            describe_record=lambda record: f"Record ID: {record.record_id}",
        )


class TestFetchSupersedesSearch:
    def test_a_fully_fetched_record_becomes_one_line_in_the_earlier_search(self) -> None:
        chat = _Conversation()
        search = chat.search([_text("a", 0), _text("a", 2), _text("b", 1)])
        fetch = chat.fetch({"a": [0, 1, 2, 3]})

        shaped = chat.shaped()

        text = shaped[search].text
        assert "a block 0 text" not in text and "a block 2 text" not in text
        assert "[Record ID: id-a: its 2 matching blocks are shown in a fetch_record result below.]" in text
        assert "b block 1 text" in text, "records the fetch did not cover stay"
        assert shaped[fetch] is chat.messages[fetch], "fetch results are never edited"

    def test_only_blocks_the_fetch_showed_are_removed(self) -> None:
        chat = _Conversation()
        search = chat.search([_text("a", 0), _text("a", 9)])
        chat.fetch({"a": [0, 1]})

        text = chat.shaped()[search].text

        assert "a block 0 text" not in text
        assert "a block 9 text" in text
        assert "[1 more block of this record is shown in a fetch_record result below.]\n</record>" in text

    def test_a_search_after_a_fetch_drops_what_the_fetch_already_shows(self) -> None:
        chat = _Conversation()
        chat.fetch({"a": [0, 1]})
        search = chat.search([_text("a", 1), _text("c", 4)])

        text = chat.shaped()[search].text

        assert "a block 1 text" not in text
        assert "fetch_record result above" in text
        assert "c block 4 text" in text


class TestOtherCopies:
    def test_a_newer_search_drops_blocks_an_earlier_search_shows(self) -> None:
        chat = _Conversation()
        first = chat.search([_text("a", 0), _text("b", 1)])
        second = chat.search([_text("b", 1), _text("c", 2)])

        shaped = chat.shaped()

        assert shaped[first] is chat.messages[first], "the older message and the cached prefix stay"
        assert "b block 1 text" not in shaped[second].text
        assert "another search result above" in shaped[second].text
        assert "c block 2 text" in shaped[second].text

    def test_blocks_in_the_prefetched_context_are_not_sent_again(self) -> None:
        chat = _Conversation()
        chat.prefetch([_text("a", 0)])
        search = chat.search([_text("a", 0), _text("b", 3)])

        text = chat.shaped()[search].text

        assert "a block 0 text" not in text
        assert "the context given at the start" in text


class TestWhatIsNeverRemoved:
    def test_a_copy_changed_by_another_shaper_does_not_count(self) -> None:
        """Cleared, truncated or compacted: its hash misses, so the search
        copy is the only one the model has and must stay."""
        chat = _Conversation()
        search = chat.search([_text("a", 0)])
        fetch = chat.fetch({"a": [0]})
        view = list(chat.messages)
        view[fetch] = view[fetch].model_copy(update={"content": "tool: knowledgegraph__fetch_record\nsummary: …"})

        assert chat.shaped(view)[search] is view[search]

    def test_a_table_stays_unless_every_row_it_shows_is_covered(self) -> None:
        chat = _Conversation()
        search = chat.search([_table("a", [4, 5])])
        chat.fetch({"a": [4]})
        assert "a row 5" in chat.shaped()[search].text

        chat.fetch({"a": [4, 5]})
        assert "a row 5" not in chat.shaped()[search].text

    def test_an_image_block_and_its_image_part_stay(self) -> None:
        chat = _Conversation()
        image = {"virtual_record_id": "a", "block_index": 3, "block_type": BlockType.IMAGE.value,
                 "content": "a bar chart", "metadata": {}}
        search = chat.search([image, _text("a", 0)], image=True)
        chat.fetch({"a": [0, 3]})

        shaped = chat.shaped()[search]

        assert "a bar chart" in shaped.text
        assert "a block 0 text" not in shaped.text
        assert isinstance(shaped.content[1], ImagePart)

    def test_a_record_found_only_by_its_summary_stays(self) -> None:
        """Its page citation line has no stand-in in a fetch."""
        chat = _Conversation()
        summary = {"virtual_record_id": "a", "block_index": None,
                   "block_type": BlockType.RECORD_SUMMARY.value, "content": "summary", "metadata": {}}
        search = chat.search([summary])
        chat.fetch({"a": [0]})

        text = chat.shaped()[search].text
        assert "<record>" in text and "Citation ID for summary" in text

    def test_other_messages_ids_and_order_are_untouched(self) -> None:
        chat = _Conversation()
        chat.search([_text("a", 0)])
        chat.fetch({"a": [0]})

        shaped = chat.shaped()

        assert len(shaped) == len(chat.messages)
        assert [type(m) for m in shaped] == [type(m) for m in chat.messages]
        assert [getattr(m, "tool_call_id", None) for m in shaped] == [
            getattr(m, "tool_call_id", None) for m in chat.messages
        ]
        assert shaped[0] is chat.messages[0]


def _texts(messages: list) -> list[str]:
    return [m.text if isinstance(m, ToolMessage) else str(m.content) for m in messages]


def test_running_twice_changes_nothing_more() -> None:
    chat = _Conversation()
    chat.search([_text("a", 0), _text("b", 1)])
    chat.search([_text("b", 1), _text("c", 2)])
    chat.fetch({"a": [0]})

    once = chat.shaped()
    assert _texts(chat.shaped(once)) == _texts(once)


def test_no_block_visible_before_is_lost_after() -> None:
    """Across random conversations: every removed copy has a kept copy that
    is still in the view, unedited where it matters."""
    rng = random.Random(7)
    for _ in range(200):
        chat = _Conversation()
        if rng.random() < 0.3:
            chat.prefetch([_text(rng.choice("abc"), rng.randrange(6))])
        visible: dict[int, set[tuple[str, int]]] = {}
        for _step in range(rng.randrange(1, 6)):
            keys = {(rng.choice("abc"), rng.randrange(6)) for _ in range(rng.randrange(1, 5))}
            if rng.random() < 0.3:
                shown: dict[str, list[int]] = {}
                for vrid, index in sorted(keys):
                    shown.setdefault(vrid, []).append(index)
                visible[chat.fetch(shown)] = keys
            else:
                visible[chat.search([_text(v, i) for v, i in sorted(keys)])] = keys

        shaped = chat.shaped()
        prefetched = {(k.virtual_record_id, k.block_index) for m in chat.registry.prefetch for k in m.blocks}
        for vrid, index in set().union(*visible.values()) - prefetched:
            block_text = f"{vrid} block {index} text"
            assert any(block_text in text for text in _texts(shaped)), (vrid, index)
