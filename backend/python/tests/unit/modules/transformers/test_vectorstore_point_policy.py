"""How a record's blocks become points under EMBED_POINT_POLICY A (default), B and C."""

from types import SimpleNamespace as NS

import pytest
from langchain_core.documents import Document

import app.modules.transformers.vectorstore as vs

LONG = " ".join(f"Sentence number {i} has some words in it to count." for i in range(20))


def _points(policy, monkeypatch, text=LONG, token_limit=None):
    if policy:
        monkeypatch.setenv("EMBED_POINT_POLICY", policy)
    else:
        monkeypatch.delenv("EMBED_POINT_POLICY", raising=False)
    return vs._build_text_documents([NS(id="b", index=3, data=text)], "v", "o", "en", token_limit)


def _rows(n, tables=1):
    return [
        Document(
            page_content=f"r{i}",
            metadata={"blockId": f"r{i}", "blockIndex": i, "blockType": "table_row",
                      **vs._row_group_meta(i * tables // n)},
        )
        for i in range(n)
    ]


def test_default_keeps_sentence_points_and_the_payload(monkeypatch):
    docs = _points(None, monkeypatch)
    assert vs._point_policy() == "A"
    assert len(docs) == 21
    assert docs[-1].metadata == {
        "virtualRecordId": "v", "blockId": "b", "blockIndex": 3, "orgId": "o",
        "isBlockGroup": False, "blockType": "text", "isBlock": True,
    }
    assert vs._row_group_meta(1) == {}


@pytest.mark.parametrize("raw", ["", "x", "D"])
def test_unknown_policy_falls_back_to_a(monkeypatch, raw):
    monkeypatch.setenv("EMBED_POINT_POLICY", raw)
    assert vs._point_policy() == "A"


@pytest.mark.parametrize("policy", ["B", "C"])
def test_b_and_c_embed_one_point_per_block(policy, monkeypatch):
    docs = _points(policy, monkeypatch)
    assert [d.page_content for d in docs] == [LONG]
    assert docs[0].metadata["blockId"] == "b" and docs[0].metadata["isBlock"] is True


def test_block_over_the_model_limit_gets_only_the_windows_it_needs(monkeypatch):
    whole = vs._token_len(LONG)
    limit = int(whole / 2.5 / vs._WINDOW_HEADROOM) + 1
    docs = _points("B", monkeypatch, token_limit=limit)
    assert len(docs) == 3
    assert "".join(d.page_content for d in docs) == LONG
    assert all(vs._token_len(d.page_content) <= int(limit * vs._WINDOW_HEADROOM) for d in docs)
    # every window still names the block, so a citation lands on it
    assert {(d.metadata["blockId"], d.metadata["blockIndex"]) for d in docs} == {("b", 3)}
    assert [d.metadata["isBlock"] for d in docs] == [True, False, False]
    assert len({vs.deterministic_point_id(d.metadata, d.page_content) for d in docs}) == 3


def test_a_block_that_fits_is_not_windowed(monkeypatch):
    assert len(_points("B", monkeypatch, token_limit=vs._token_len(LONG) * 2)) == 1


def test_policy_a_ignores_the_model_limit(monkeypatch):
    assert len(_points("A", monkeypatch, token_limit=16)) == 21


def test_point_token_limit(monkeypatch):
    monkeypatch.delenv("PIPESHUB_EMBED_TOKEN_LIMIT", raising=False)
    monkeypatch.setenv("EMBED_POINT_POLICY", "C")
    assert vs._point_token_limit(None) == 8191
    assert vs._point_token_limit(512) == int(512 * vs._WINDOW_HEADROOM)
    assert vs._point_token_limit(100_000) == 8191
    monkeypatch.setenv("PIPESHUB_EMBED_TOKEN_LIMIT", "256")
    assert vs._point_token_limit(512) == 256
    monkeypatch.setenv("EMBED_POINT_POLICY", "A")
    assert vs._point_token_limit(64) == 256


def test_rows_group_five_to_a_point_per_table(monkeypatch):
    monkeypatch.setenv("EMBED_POINT_POLICY", "C")
    grouped = vs._group_table_row_documents(_rows(24, tables=2), 512)  # two 12-row tables
    assert [len(d.metadata["rowBlockIds"]) for d in grouped] == [5, 5, 2, 5, 5, 2]
    first = grouped[0]
    assert first.metadata["blockId"] == "r0" and first.metadata["blockIndex"] == 0
    assert first.page_content == "r0\nr1\nr2\nr3\nr4"
    assert vs._ROW_GROUP_META not in first.metadata


def test_a_single_row_keeps_its_plain_payload(monkeypatch):
    monkeypatch.setenv("EMBED_POINT_POLICY", "C")
    (only,) = vs._group_table_row_documents(_rows(1), 512)
    assert "rowBlockIds" not in only.metadata and vs._ROW_GROUP_META not in only.metadata


def test_table_points_are_capped(monkeypatch):
    monkeypatch.setenv("EMBED_POINT_POLICY", "C")
    monkeypatch.setenv("EMBED_MAX_TABLE_POINTS", "2")
    assert len(vs._group_table_row_documents(_rows(24), 512)) == 2


def test_non_row_documents_split_runs_and_pass_through(monkeypatch):
    monkeypatch.setenv("EMBED_POINT_POLICY", "C")
    summary = Document(page_content="s", metadata={"blockId": "t", "blockType": "table"})
    out = vs._group_table_row_documents([*_rows(2), summary, *_rows(2)], 512)
    assert [d.page_content for d in out] == ["r0\nr1", "s", "r0\nr1"]


def test_a_group_longer_than_the_limit_is_split_and_keeps_its_row_ids(monkeypatch):
    monkeypatch.setenv("EMBED_POINT_POLICY", "C")
    rows = [Document(page_content=LONG, metadata={"blockId": f"r{i}", "blockType": "table_row",
                                                  **vs._row_group_meta(0)}) for i in range(2)]
    out = vs._group_table_row_documents(rows, 100)
    assert len(out) > 1
    assert all(d.metadata["rowBlockIds"] == ["r0", "r1"] for d in out)


@pytest.mark.asyncio
async def test_model_token_limit_comes_from_the_client_and_only_off_policy_a(monkeypatch):
    class Reports:
        async def max_input_tokens(self):
            return 512

    monkeypatch.setenv("EMBED_POINT_POLICY", "C")
    assert await vs._model_token_limit(Reports()) == 512
    assert await vs._model_token_limit(object()) is None
    monkeypatch.setenv("EMBED_POINT_POLICY", "A")
    assert await vs._model_token_limit(Reports()) is None
