"""`read_code` records a complete file read in `full_records_fetched`.

Record-escalation reads that set to decide which records still need fetching.
Before this, a whole-file `read_code` stayed invisible to it, so the candidate
list kept offering a file the agent already held in full -- and a coverage
figure the model could see was wrong is what teaches it to ignore the accurate
ones.
"""
import json

import pytest

from app.agents.actions.code_graph import code_graph as cg

RECORD_ID = "rec-1"


def _make_instance(state: dict) -> cg.CodeGraph:
    state.setdefault("org_id", "org-1")
    state.setdefault("user_id", "user-1")
    state.setdefault("graph_provider", object())
    state.setdefault("blob_store", object())
    return cg.CodeGraph(state)


@pytest.fixture
def read_code(monkeypatch):
    """Returns (invoke, state). `invoke` runs the tool over a stubbed impl."""
    state: dict = {}
    instance = _make_instance(state)

    async def run(impl_result, **kwargs):
        async def fake_impl(**_):
            return dict(impl_result)

        monkeypatch.setattr(cg, "read_code_impl", fake_impl)
        args = {"connector_id": "conn-1", "file_path": "a/b.py", **kwargs}
        success, json_str = await instance.read_code(**args)
        result = json.loads(json_str)
        return result, state

    return run


@pytest.mark.asyncio
async def test_whole_file_read_is_recorded(read_code) -> None:
    result, state = await read_code(
        {"file_path": "a/b.py", "truncated": False, "blocks": [], "_record_id": RECORD_ID}
    )
    assert state["full_records_fetched"] == {RECORD_ID}
    assert "_record_id" not in result, "internal handle must not reach the model"


@pytest.mark.asyncio
async def test_symbol_read_is_not_recorded(read_code) -> None:
    """A single symbol is not the file: marking it would suppress the prompt
    for a record the model has barely seen."""
    _, state = await read_code(
        {"kind": "function", "code": "...", "_record_id": RECORD_ID},
        qualified_name="function:main",
    )
    assert state.get("full_records_fetched", set()) == set()


@pytest.mark.asyncio
async def test_line_range_read_is_not_recorded(read_code) -> None:
    _, state = await read_code(
        {"file_path": "a/b.py", "truncated": False, "blocks": [], "_record_id": RECORD_ID},
        lines="10-20",
    )
    assert state.get("full_records_fetched", set()) == set()


@pytest.mark.asyncio
async def test_truncated_read_is_not_recorded(read_code) -> None:
    _, state = await read_code(
        {"file_path": "a/b.py", "truncated": True, "blocks": [], "_record_id": RECORD_ID}
    )
    assert state.get("full_records_fetched", set()) == set()


@pytest.mark.asyncio
async def test_existing_entries_are_preserved(read_code) -> None:
    """`fetch_record` writes the same set, so this must add, never replace."""
    result, state = await read_code(
        {"file_path": "a/b.py", "truncated": False, "blocks": [], "_record_id": RECORD_ID}
    )
    assert result is not None
    state["full_records_fetched"].add("rec-from-fetch-tool")
    await read_code(
        {"file_path": "c/d.py", "truncated": False, "blocks": [], "_record_id": "rec-2"}
    )
    assert state["full_records_fetched"] == {RECORD_ID, "rec-from-fetch-tool", "rec-2"}
