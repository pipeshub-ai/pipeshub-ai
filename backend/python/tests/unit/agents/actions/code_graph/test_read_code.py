"""`read_code` resolves, gates, then reads -- and never says which step failed.

A miss and a denial share one payload. The blob is keyed by the stored
qualified name, so a name `resolve_symbol` matched case-insensitively still
has to reach the right blob block.
"""
import pytest

from app.agents.actions.code_graph.ops import read_code_impl

from .conftest import CONN, ORG, USER, FakeBlobStore, FakeGraphProvider

pytestmark = pytest.mark.asyncio

TARGET_SRC = "def target():\n    return 1\n"


def _blob() -> FakeBlobStore:
    return FakeBlobStore({
        "v-b": {
            "block_containers": {
                "blocks": [{
                    "code_metadata": {
                        "qualified_name": "function:target", "kind": "function",
                        "start_line": 1, "end_line": 2,
                    },
                    "data": {"text": TARGET_SRC},
                }],
                "block_groups": [],
            }
        }
    })


async def _read(graph, file_path="src/b.py", **kwargs) -> dict:
    return await read_code_impl(
        graph_provider=graph, connector_id=CONN, blob_store=_blob(),
        org_id=ORG, user_id=USER, file_path=file_path, **kwargs,
    )


class TestAccessControl:
    async def test_a_missing_file_and_a_denied_one_are_the_same_miss(self) -> None:
        missing = await _read(FakeGraphProvider(), file_path="src/nope.py")
        denied = await _read(FakeGraphProvider(deny_records=["rec-b"]))
        assert set(missing) == set(denied) == {"error"}
        assert missing["error"].replace("src/nope.py", "src/b.py") == denied["error"]

    async def test_a_missing_symbol_and_a_denied_one_are_the_same_miss(self) -> None:
        missing = await _read(FakeGraphProvider(), qualified_name="function:nope")
        denied = await _read(
            FakeGraphProvider(deny_records=["rec-b"]), qualified_name="function:target"
        )
        assert set(missing) == set(denied) == {"error"}
        assert missing["error"].replace("function:nope", "function:target") == denied["error"]

    async def test_a_missing_symbol_in_a_missing_file_is_that_same_miss(self) -> None:
        missing = await _read(
            FakeGraphProvider(), file_path="src/nope.py", qualified_name="function:nope"
        )
        denied = await _read(
            FakeGraphProvider(deny_records=["rec-b"]), qualified_name="function:target"
        )
        assert (
            missing["error"].replace("src/nope.py", "src/b.py")
            .replace("function:nope", "function:target")
            == denied["error"]
        )


class TestSymbolRead:
    async def test_a_symbol_is_read_from_the_blob(self) -> None:
        result = await _read(FakeGraphProvider(), qualified_name="function:target")
        assert result["code"] == TARGET_SRC

    async def test_a_differently_cased_name_still_reaches_the_blob(self) -> None:
        result = await _read(FakeGraphProvider(), qualified_name="Function:Target")
        assert "error" not in result
        assert result["code"] == TARGET_SRC
        assert result["qualified_name"] == "function:target", "the stored spelling is returned"
