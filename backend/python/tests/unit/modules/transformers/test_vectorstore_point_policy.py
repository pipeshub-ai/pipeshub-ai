"""How a record's blocks become points under EMBED_POINT_POLICY A (default), B and C."""

from types import SimpleNamespace as NS
from typing import Any
from unittest.mock import AsyncMock

import pytest
from langchain_core.documents import Document

import app.modules.transformers.vectorstore as vs
from app.models.blocks import (
    Block,
    BlockGroup,
    BlockGroupChildren,
    BlocksContainer,
    BlockType,
    DataFormat,
    GroupType,
)
from app.modules.transformers.transformer import ReconciliationContext, TransformContext
from tests.unit.modules.transformers.test_vectorstore_deep import _make_vectorstore_p1

LONG = " ".join(f"Sentence number {i} has some words in it to count." for i in range(20))


def _points(policy: str | None, monkeypatch: pytest.MonkeyPatch, text: str = LONG,
            window: "vs._Window | None" = None) -> list[Document]:
    if policy:
        monkeypatch.setenv("EMBED_POINT_POLICY", policy)
    else:
        monkeypatch.delenv("EMBED_POINT_POLICY", raising=False)
    return vs._build_text_documents([NS(id="b", index=3, data=text)], "v", "o", "en", window)


def _rows(n: int, tables: int = 1) -> list[Document]:
    return [
        Document(
            page_content=f"r{i}",
            metadata={"blockId": f"r{i}", "blockIndex": i, "blockType": "table_row",
                      **vs._row_group_meta(i * tables // n)},
        )
        for i in range(n)
    ]


def test_default_keeps_sentence_points_and_the_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    docs = _points(None, monkeypatch)
    assert vs._point_policy() == "A"
    assert len(docs) == 21
    assert docs[-1].metadata == {
        "virtualRecordId": "v", "blockId": "b", "blockIndex": 3, "orgId": "o",
        "isBlockGroup": False, "blockType": "text", "isBlock": True,
    }
    assert vs._row_group_meta(1) == {}


@pytest.mark.parametrize("raw", ["", "x", "D"])
def test_unknown_policy_falls_back_to_a(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("EMBED_POINT_POLICY", raw)
    assert vs._point_policy() == "A"


@pytest.mark.parametrize("policy", ["B", "C"])
def test_b_and_c_embed_one_point_per_block(policy: str, monkeypatch: pytest.MonkeyPatch) -> None:
    docs = _points(policy, monkeypatch)
    assert [d.page_content for d in docs] == [LONG]
    assert docs[0].metadata["blockId"] == "b" and docs[0].metadata["isBlock"] is True


def test_block_over_the_limit_gets_only_the_windows_it_needs(monkeypatch: pytest.MonkeyPatch) -> None:
    limit = vs._token_len(LONG) // 2 + 10
    docs = _points("B", monkeypatch, window=vs._Window(limit))
    assert len(docs) == 2
    assert "".join(d.page_content for d in docs) == LONG
    assert all(vs._token_len(d.page_content) <= limit for d in docs)
    # every window still names the block, so a citation lands on it
    assert {(d.metadata["blockId"], d.metadata["blockIndex"]) for d in docs} == {("b", 3)}
    assert [d.metadata["isBlock"] for d in docs] == [True, False]
    assert len({vs.deterministic_point_id(d.metadata, d.page_content) for d in docs}) == 2


def test_a_block_that_fits_is_not_windowed(monkeypatch: pytest.MonkeyPatch) -> None:
    assert len(_points("B", monkeypatch, window=vs._Window(vs._token_len(LONG) * 2))) == 1


def test_policy_a_ignores_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    assert len(_points("A", monkeypatch, window=vs._Window(16))) == 21


def _heavy_count(texts: list[str]) -> list[int]:
    # Reads more tokens than cl100k does, as a WordPiece model does for Russian and code.
    return [len(t.encode("utf-8")) // 2 + 2 for t in texts]


RUSSIAN = " ".join(["Быстрая коричневая лисица перепрыгивает через ленивую собаку."] * 120)
CODE = "\n".join(f"def f{i}(a, b):\n    return a[{i}] + b['k{i}'] * (a ^ b) % 0x{i:x}" for i in range(150))


@pytest.mark.parametrize("text", [RUSSIAN, CODE, "x " * 30000], ids=["russian", "code", "oversized"])
@pytest.mark.parametrize("policy", ["B", "C"])
def test_no_window_exceeds_the_model_limit_by_the_models_own_count(
    policy: str, text: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    docs = _points(policy, monkeypatch, text=text, window=vs._Window(128, _heavy_count))
    assert len(docs) > 1
    assert max(_heavy_count([d.page_content for d in docs])) <= 128
    assert "".join(d.page_content for d in docs) == vs.anchor_text_only(text)
    assert {d.metadata["blockId"] for d in docs} == {"b"}


@pytest.mark.asyncio
async def test_model_window_asks_the_server_only_for_texts_that_may_not_fit(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[list[str]] = []

    class Local:
        async def max_input_tokens(self) -> int:
            return 100

        def count_tokens(self, texts: list[str]) -> list[int]:
            asked.append(texts)
            return [999 for _ in texts]

    monkeypatch.setenv("EMBED_POINT_POLICY", "C")
    window = await vs._model_window(Local())
    assert window.limit == 100
    assert window.count(["short", "x" * 150]) == [7, 999]
    assert asked == [["x" * 150]]
    assert window.count(["short"]) == [7] and len(asked) == 1


@pytest.mark.asyncio
async def test_model_window_is_none_without_a_limit_and_under_policy_a(monkeypatch: pytest.MonkeyPatch) -> None:
    class NoLimit:
        async def max_input_tokens(self) -> None:
            return None

    class Local:
        async def max_input_tokens(self) -> int:
            return 512

        def count_tokens(self, texts: list[str]) -> list[int]:
            return [1 for _ in texts]

    monkeypatch.setenv("EMBED_POINT_POLICY", "C")
    assert await vs._model_window(NoLimit()) is None
    assert await vs._model_window(object()) is None
    monkeypatch.setenv("EMBED_POINT_POLICY", "A")
    assert await vs._model_window(Local()) is None


def test_rows_group_five_to_a_point_per_table(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBED_POINT_POLICY", "C")
    grouped = vs._group_table_row_documents(_rows(24, tables=2))  # two 12-row tables
    assert [len(d.metadata["rowBlockIds"]) for d in grouped] == [5, 5, 2, 5, 5, 2]
    first = grouped[0]
    assert first.metadata["blockId"] == "r0" and first.metadata["blockIndex"] == 0
    assert first.page_content == "r0\nr1\nr2\nr3\nr4"
    assert vs._ROW_GROUP_META not in first.metadata


def test_a_single_row_keeps_its_plain_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBED_POINT_POLICY", "C")
    (only,) = vs._group_table_row_documents(_rows(1))
    assert "rowBlockIds" not in only.metadata and vs._ROW_GROUP_META not in only.metadata


def test_table_points_are_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBED_POINT_POLICY", "C")
    monkeypatch.setenv("EMBED_MAX_TABLE_POINTS", "2")
    assert len(vs._group_table_row_documents(_rows(24))) == 2


def test_a_table_never_has_more_groups_than_the_cap_and_every_row_is_listed_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMBED_POINT_POLICY", "C")
    out = vs._group_table_row_documents(_rows(20001))
    assert len(out) <= 200
    assert [i for d in out for i in d.metadata["rowBlockIds"]] == [f"r{i}" for i in range(20001)]
    assert max(len(d.metadata["rowBlockIds"]) for d in out) == 101


def test_non_row_documents_split_runs_and_pass_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBED_POINT_POLICY", "C")
    summary = Document(page_content="s", metadata={"blockId": "t", "blockType": "table"})
    out = vs._group_table_row_documents([*_rows(2), summary, *_rows(2)])
    assert [d.page_content for d in out] == ["r0\nr1", "s", "r0\nr1"]


def test_a_group_longer_than_the_limit_is_cut_and_each_window_lists_only_its_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EMBED_POINT_POLICY", "C")
    rows = [
        Document(page_content=f"row {i} " + "word " * 30,
                 metadata={"blockId": f"r{i}", "blockIndex": i, "blockType": "table_row", **vs._row_group_meta(0)})
        for i in range(5)
    ]
    out = vs._group_table_row_documents(rows, vs._Window(60))
    assert len(out) > 1
    listed = [d.metadata.get("rowBlockIds", [d.metadata["blockId"]]) for d in out]
    assert all(len(ids) < 5 for ids in listed)
    assert {i for ids in listed for i in ids} == {f"r{i}" for i in range(5)}
    assert all(d.metadata["blockId"] == ids[0] for d, ids in zip(out, listed, strict=True))


def test_rows_of_two_tables_are_never_grouped_together(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBED_POINT_POLICY", "C")
    rows = [
        Document(page_content=f"t{t}r{i}",
                 metadata={"blockId": f"t{t}r{i}", "blockType": "table_row", **vs._row_group_meta(t)})
        for t in (0, 1) for i in range(3)
    ]
    out = vs._group_table_row_documents(rows)
    assert [d.metadata["rowBlockIds"] for d in out] == [["t0r0", "t0r1", "t0r2"], ["t1r0", "t1r1", "t1r2"]]


# ---- partial updates (reconciliation) under C -----------------------------------------------

class _Store:
    """A vector store that keeps points by id and deletes by blockId, as delete_blocks_by_ids asks."""

    def __init__(self) -> None:
        self.vs = _make_vectorstore_p1(supports_sparse=True)
        self.points: dict[str, Any] = {}
        db = self.vs.vector_db_service

        async def upsert(collection_name: str, points: list) -> None:
            self.points.update({p.id: p for p in points})

        async def delete(collection_name: str, flt: dict) -> None:
            gone = set(flt.get("blockId", []))
            self.points = {i: p for i, p in self.points.items() if p.payload["metadata"].get("blockId") not in gone}

        db.upsert_points = AsyncMock(side_effect=upsert)
        db.delete_points = AsyncMock(side_effect=delete)
        db.filter_collection = AsyncMock(side_effect=lambda **kw: kw["must"])
        self.vs.dense_embeddings.aembed_documents = AsyncMock(side_effect=lambda t: [[0.1] * 4 for _ in t])
        self.vs._compute_sparse_embeddings = AsyncMock(side_effect=lambda t: [None] * len(t))
        self.vs.get_embedding_model_instance = AsyncMock(return_value=False)
        self.vs.delete_embeddings = AsyncMock()
        self.vs._cleanup_orphaned_embeddings_if_needed = AsyncMock()
        self.vs._resync_membership_after_write = AsyncMock()

    def rows(self) -> dict[str, list[str]]:
        """Each row id to the texts of the row points that hold it."""
        held: dict[str, list[str]] = {}
        for p in self.points.values():
            m = p.payload["metadata"]
            if m.get("blockType") == "table_row":
                for row_id in m.get("rowBlockIds", [m["blockId"]]):
                    held.setdefault(row_id, []).append(p.payload["page_content"])
        return held


def _table(texts: dict[int, str], n: int = 7) -> tuple[BlocksContainer, list[Block]]:
    rows = [
        Block(index=i, type=BlockType.TABLE_ROW, format=DataFormat.JSON, parent_index=0, id=f"r{i}",
              data={"row_natural_language_text": texts.get(i, f"row {i}"), "row_number": i})
        for i in range(n)
    ]
    group = BlockGroup(index=0, type=GroupType.TABLE, data={"table_summary": "T"},
                       children=BlockGroupChildren.from_indices(block_indices=list(range(n))))
    return BlocksContainer(blocks=rows, block_groups=[group]), rows


async def _apply(store: _Store, container: BlocksContainer, changed: set[str] | None, deleted: set[str]) -> None:
    record = NS(id="rec", virtual_record_id="vr", block_containers=container, org_id="o")
    recon = None if changed is None else ReconciliationContext(blocks_to_index_ids=changed, block_ids_to_delete=deleted)
    await store.vs.apply(TransformContext.model_construct(record=record, reconciliation_context=recon))


@pytest.fixture
def policy_c(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBED_POINT_POLICY", "C")


async def _indexed_table(n: int = 7) -> _Store:
    store = _Store()
    await _apply(store, _table({}, n)[0], None, set())
    return store


@pytest.mark.asyncio
@pytest.mark.usefixtures("policy_c")
async def test_editing_the_first_row_keeps_a_point_for_every_other_row_of_its_group() -> None:
    store = await _indexed_table()
    container, _ = _table({0: "row 0 EDITED"})
    await _apply(store, container, {"r0"}, set())
    held = store.rows()
    assert sorted(held) == [f"r{i}" for i in range(7)]
    assert all(len(texts) == 1 for texts in held.values())
    assert any("row 0 EDITED" in t for t in held["r0"])


@pytest.mark.asyncio
@pytest.mark.usefixtures("policy_c")
async def test_editing_a_later_row_leaves_no_stale_text_and_no_duplicate() -> None:
    store = await _indexed_table()
    container, _ = _table({3: "row 3 EDITED"})
    await _apply(store, container, {"r3"}, set())
    held = store.rows()
    assert all(len(texts) == 1 for texts in held.values())
    assert all("row 3\n" not in t + "\n" and "row 3 EDITED" in t for t in held["r3"])
    assert not any(t == "row 3" or "\nrow 3\n" in f"\n{t}\n" for texts in held.values() for t in texts)


@pytest.mark.asyncio
@pytest.mark.usefixtures("policy_c")
async def test_deleting_a_row_leaves_no_point_listing_it_and_keeps_the_others() -> None:
    store = await _indexed_table()
    container, rows = _table({})
    container = BlocksContainer(blocks=[r for r in rows if r.id != "r3"], block_groups=container.block_groups)
    await _apply(store, container, set(), {"r3"})
    held = store.rows()
    assert "r3" not in held
    assert sorted(held) == sorted(f"r{i}" for i in range(7) if i != 3)
    assert all(len(texts) == 1 for texts in held.values())
    assert not any("row 3" in t.split("\n") for texts in held.values() for t in texts)


@pytest.mark.asyncio
@pytest.mark.usefixtures("policy_c")
async def test_deleting_the_first_row_of_a_group_replaces_the_whole_group() -> None:
    store = await _indexed_table()
    container, rows = _table({})
    container = BlocksContainer(blocks=[r for r in rows if r.id != "r0"], block_groups=container.block_groups)
    await _apply(store, container, set(), {"r0"})
    held = store.rows()
    assert sorted(held) == [f"r{i}" for i in range(1, 7)]
    assert all(len(texts) == 1 for texts in held.values())


def test_with_whole_tables_only_regroups_tables_that_changed() -> None:
    a = [Block(index=i, type=BlockType.TABLE_ROW, format=DataFormat.JSON, parent_index=p, id=f"{p}-{i}", data={})
         for p in (0, 1) for i in range(2)]
    assert vs._with_whole_tables(a, {"0-1"}, any_deleted=False) == {"0-0", "0-1"}
    assert vs._with_whole_tables(a, set(), any_deleted=True) == {b.id for b in a}
    assert vs._with_whole_tables(a, set(), any_deleted=False) == set()


@pytest.mark.asyncio
@pytest.mark.usefixtures("policy_c")
async def test_rows_of_two_sql_tables_are_indexed_in_separate_groups() -> None:
    store = _Store()
    blocks = []
    for table in (0, 1):
        for i in range(3):
            b = Block(index=table * 3 + i, type=BlockType.TABLE_ROW, format=DataFormat.JSON, parent_index=table,
                      id=f"s{table}_{i}", data={"row_natural_language_text": f"t{table} row {i}"})
            b.sub_type = "sql_table"
            blocks.append(b)
    await store.vs.index_documents(BlocksContainer(blocks=blocks, block_groups=[]), "o", "rec", "vr")
    groups = sorted(p.payload["metadata"].get("rowBlockIds", []) for p in store.points.values())
    assert groups == [["s0_0", "s0_1", "s0_2"], ["s1_0", "s1_1", "s1_2"]]


@pytest.fixture
def tokenize_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vs, "_tokenize_unavailable", False)


def _status_error(status: int) -> Exception:
    import httpx
    import openai

    request = httpx.Request("POST", "http://x/v1/tokenize")
    return openai.APIStatusError("no", response=httpx.Response(status, request=request), body=None)


@pytest.mark.parametrize("status", [404, 405])
@pytest.mark.usefixtures("tokenize_state")
def test_a_server_without_tokenize_switches_the_process_to_scaled_cl100k_for_good(
    status: int, caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[int] = []

    def ask(texts: list[str]) -> list[int]:
        calls.append(len(texts))
        raise _status_error(status)

    text = "word " * 400
    with caplog.at_level("WARNING"):
        first = vs._server_counts(ask, [text])
        second = vs._server_counts(ask, [text])
    assert first == second
    assert first[0] >= int(vs._token_len(text) * vs._WORST_CASE_TOKEN_RATIO)
    assert calls == [1]  # asked once; the process then stays on the fallback
    assert sum("no token counting" in r.message for r in caplog.records) == 1


@pytest.mark.usefixtures("tokenize_state")
def test_a_transient_server_error_falls_back_for_that_call_only() -> None:
    answers: list[object] = [_status_error(500), [42]]

    def ask(texts: list[str]) -> list[int]:
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    text = "word " * 400
    assert vs._server_counts(ask, [text])[0] >= vs._token_len(text) * 2  # scaled fallback
    assert vs._server_counts(ask, [text]) == [42]  # back to exact counts
    assert not vs._tokenize_unavailable


@pytest.mark.asyncio
@pytest.mark.usefixtures("tokenize_state")
async def test_a_window_with_no_tokenize_still_fits_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx
    import openai

    class Old:
        async def max_input_tokens(self) -> int:
            return 128

        def count_tokens(self, texts: list[str]) -> list[int]:
            raise openai.APIStatusError(
                "no", response=httpx.Response(404, request=httpx.Request("POST", "http://x")), body=None,
            )

    monkeypatch.setenv("EMBED_POINT_POLICY", "C")
    window = await vs._model_window(Old())
    docs = _points("C", monkeypatch, text=RUSSIAN, window=window)
    assert len(docs) > 1
    assert max(_heavy_count([d.page_content for d in docs])) <= 128


def _recording_window(limit: int = 1000) -> "tuple[vs._Window, list[int]]":
    calls: list[int] = []

    def count(texts: list[str]) -> list[int]:
        calls.append(len(texts))
        return [len(t.split()) for t in texts]

    return vs._Window(limit, count), calls


def test_a_table_asks_the_server_once_for_all_its_groups(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBED_POINT_POLICY", "C")
    window, calls = _recording_window()
    out = vs._group_table_row_documents(_rows(1000), window)
    assert len(out) == 200
    assert calls == [200]


def test_a_group_that_must_be_cut_costs_one_request_per_attempt_not_per_piece(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBED_POINT_POLICY", "C")
    window, calls = _recording_window(limit=8)
    rows = [
        Document(page_content=" ".join(["w"] * 30), metadata={"blockId": f"r{i}", "blockType": "table_row", **vs._row_group_meta(0)})
        for i in range(5)
    ]
    out = vs._group_table_row_documents(rows, window)
    assert len(out) > 1
    assert len(calls) <= 1 + vs._MAX_SPLIT_ATTEMPTS + 1


def test_blocks_of_a_record_are_counted_in_one_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBED_POINT_POLICY", "B")
    window, calls = _recording_window()
    blocks = [NS(id=f"b{i}", index=i, data=f"block {i} " + "word " * 20) for i in range(50)]
    docs = vs._build_text_documents(blocks, "v", "o", "en", window)
    assert len(docs) == 50
    assert calls == [50]


@pytest.mark.asyncio
@pytest.mark.usefixtures("policy_c")
async def test_counting_tokens_does_not_block_the_event_loop() -> None:
    import asyncio
    import time

    store = _Store()

    def slow_count(texts: list[str]) -> list[int]:
        time.sleep(0.4)  # a blocking request to the embedding server
        return [1 for _ in texts]

    store.vs.model_window = vs._Window(512, slow_count)
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    task = asyncio.create_task(ticker())
    await store.vs.index_documents(_table({}, 12)[0], "o", "rec", "vr")
    task.cancel()
    assert ticks >= 20  # the loop kept turning during the 0.4 s of counting
    assert store.rows()


@pytest.mark.usefixtures("tokenize_state")
def test_five_thousand_texts_go_out_in_three_requests_within_the_servers_limits() -> None:
    from app.config.constants.ai_models import TOKENIZE_MAX_CHARS, TOKENIZE_MAX_ITEMS

    sent: list[list[str]] = []

    def ask(texts: list[str]) -> list[int]:
        assert len(texts) <= TOKENIZE_MAX_ITEMS and sum(map(len, texts)) <= TOKENIZE_MAX_CHARS
        sent.append(texts)
        return [7 for _ in texts]

    texts = [f"row {i}" for i in range(5000)]
    assert vs._server_counts(ask, texts) == [7] * 5000
    assert [len(c) for c in sent] == [2048, 2048, 904]


@pytest.mark.usefixtures("tokenize_state")
def test_a_request_near_the_character_limit_is_split_in_two() -> None:
    from app.config.constants.ai_models import TOKENIZE_MAX_CHARS

    sent: list[int] = []

    def ask(texts: list[str]) -> list[int]:
        sent.append(sum(map(len, texts)))
        return [1 for _ in texts]

    half = TOKENIZE_MAX_CHARS // 2
    assert vs._server_counts(ask, ["a" * half, "b" * (half + 1)]) == [1, 1]
    assert len(sent) == 2 and max(sent) <= TOKENIZE_MAX_CHARS


@pytest.mark.usefixtures("tokenize_state")
def test_a_413_is_logged_as_an_error_and_split_smaller_without_falling_back(caplog: pytest.LogCaptureFixture) -> None:
    def ask(texts: list[str]) -> list[int]:
        if len(texts) > 2:
            raise _status_error(413)
        return [9 for _ in texts]

    with caplog.at_level("ERROR"):
        assert vs._server_counts(ask, ["a", "b", "c", "d", "e"]) == [9] * 5
    assert any("too large" in r.message for r in caplog.records)
    assert not vs._tokenize_unavailable


class _PromptedModel:
    """A local model that reads a 20-token prompt in front of every input, limit 100."""

    prompt_tokens = 20

    async def max_input_tokens(self) -> int:
        return 100

    def count_tokens(self, texts: list[str]) -> list[int]:
        return [len(t) + self.prompt_tokens + 2 for t in texts]


@pytest.mark.asyncio
@pytest.mark.usefixtures("tokenize_state")
async def test_a_near_limit_block_is_cut_when_the_models_prompt_takes_room(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBED_POINT_POLICY", "B")
    window = await vs._model_window(_PromptedModel())
    text = "x" * 85  # 85 + 2 < 100, but 85 + 20 prompt + 2 does not fit
    docs = _points("B", monkeypatch, text=text, window=window)
    assert len(docs) > 1
    assert max(_PromptedModel().count_tokens([d.page_content for d in docs])) <= 100


@pytest.mark.asyncio
async def test_a_model_without_a_prompt_keeps_its_near_limit_block_whole(monkeypatch: pytest.MonkeyPatch) -> None:
    class Plain(_PromptedModel):
        prompt_tokens = 0

    monkeypatch.setenv("EMBED_POINT_POLICY", "B")
    window = await vs._model_window(Plain())
    assert len(_points("B", monkeypatch, text="x" * 85, window=window)) == 1


@pytest.mark.usefixtures("tokenize_state")
def test_the_fallback_count_includes_the_prompt_overhead() -> None:
    def ask(texts: list[str]) -> list[int]:
        raise _status_error(404)

    assert vs._server_counts(ask, ["word " * 100], 20)[0] == vs._server_counts(ask, ["word " * 100])[0] + 20


@pytest.mark.asyncio
@pytest.mark.parametrize(("limit", "prompt"), [(100, 100), (100, 120), (100, 90)])
async def test_a_prompt_that_leaves_no_room_for_text_is_a_configuration_error(
    limit: int, prompt: int, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.exceptions.indexing_exceptions import EmbeddingNotConfiguredError

    class Crowded(_PromptedModel):
        model = "crowded-model"
        prompt_tokens = prompt

        async def max_input_tokens(self) -> int:
            return limit

    monkeypatch.setenv("EMBED_POINT_POLICY", "C")
    with pytest.raises(EmbeddingNotConfiguredError) as err:
        await vs._model_window(Crowded())
    assert "crowded-model" in str(err.value) and str(limit) in str(err.value) and str(prompt) in str(err.value)


def test_the_splitter_raises_instead_of_returning_pieces_the_model_would_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.exceptions.indexing_exceptions import ChunkingError, IndexingError

    def never_fits(texts: list[str]) -> list[int]:
        return [10_000 for _ in texts]

    with pytest.raises(ChunkingError) as err:
        vs._split_to_token_ceiling("word " * 200, 100, never_fits)
    assert isinstance(err.value, IndexingError)  # index_documents reports it as an indexing failure


@pytest.mark.asyncio
@pytest.mark.usefixtures("policy_c")
async def test_a_record_whose_text_cannot_be_cut_to_fit_fails_to_index() -> None:
    from app.exceptions.indexing_exceptions import IndexingError

    store = _Store()
    store.vs.model_window = vs._Window(100, lambda texts: [10_000 for _ in texts])
    container = BlocksContainer(
        blocks=[Block(index=0, type=BlockType.TEXT, format=DataFormat.TXT, data="word " * 200)], block_groups=[],
    )
    with pytest.raises(IndexingError):
        await store.vs.index_documents(container, "o", "rec", "vr")
    assert not store.points
