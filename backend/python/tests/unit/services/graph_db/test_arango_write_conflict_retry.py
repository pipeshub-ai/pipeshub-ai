"""A write outside a transaction that fails with errorNum 1200 runs again.

Two writers on one document at once fail one of them in ArangoDB: a write-write
conflict, or "timeout waiting to lock key" when it waited on the lock. Neither
write landed, so the client sends it again. Inside a transaction the conflict
aborted the transaction, and only its owner can run it again.
"""

import json
import logging
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.graph_db.arango.arango_http_client import (
    ArangoHTTPClient,
    ArangoQueryError,
    is_write_conflict,
)

CONFLICT = json.dumps({"code": 409, "error": True, "errorNum": 1200, "errorMessage": "write-write conflict"})
LOCK_TIMEOUT = json.dumps({
    "code": 409, "error": True, "errorNum": 1200,
    "errorMessage": "timeout waiting to lock key - in index primary of type primary over '_key'; conflicting key: c1",
})
EXISTS = json.dumps({"code": 409, "error": True, "errorNum": 1210, "errorMessage": "unique constraint violated"})


class _Resp:
    def __init__(self, status, json_data=None, text="") -> None:
        self.status = status
        self._json = json_data
        self._text = text

    async def json(self) -> object:
        return self._json

    async def text(self) -> str:
        return self._text

    async def __aenter__(self) -> "_Resp":
        return self

    async def __aexit__(self, *args: object) -> None:
        pass


@pytest.fixture
def client() -> ArangoHTTPClient:
    return ArangoHTTPClient(
        base_url="http://localhost:8529", username="root", password="x",
        database="db", logger=MagicMock(spec=logging.Logger),
    )


@pytest.fixture(autouse=True)
def no_backoff() -> Iterator[None]:
    with patch("app.services.graph_db.arango.arango_http_client.asyncio.sleep", new=AsyncMock()):
        yield


def _session(method: str, *responses: _Resp) -> MagicMock:
    session = MagicMock()
    getattr(session, method).side_effect = list(responses)
    return session


async def _call(client, session, write) -> object:
    with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=session):
        return await write()


@pytest.mark.parametrize("body", [CONFLICT, LOCK_TIMEOUT])
async def test_an_update_that_lost_to_another_writer_runs_again(client, body) -> None:
    session = _session("patch", _Resp(409, text=body), _Resp(200, {"_key": "c1"}))

    result = await _call(client, session, lambda: client.update_document("apps", "c1", {"status": "FULL_SYNCING"}))

    assert result == {"_key": "c1"}
    assert session.patch.call_count == 2


async def test_an_aql_write_that_lost_runs_again(client) -> None:
    session = _session("post", _Resp(409, text=LOCK_TIMEOUT), _Resp(201, {"result": ["c1"], "hasMore": False}))

    rows = await _call(client, session, lambda: client.execute_aql("UPDATE ...", {}))

    assert rows == ["c1"]
    assert session.post.call_count == 2


LOST = {"error": True, "errorNum": 1200, "errorMessage": "write-write conflict"}


async def test_only_the_batch_documents_that_lost_are_sent_again(client) -> None:
    """The others landed; sending them again could undo someone's newer write."""
    session = _session(
        "post",
        _Resp(202, [{"_key": "a"}, LOST, {"_key": "c"}]),
        _Resp(202, [LOST]),
        _Resp(202, [{"_key": "b", "_oldRev": "2"}]),
    )
    docs = [{"_key": "a"}, {"_key": "b"}, {"_key": "c"}]

    result = await _call(client, session, lambda: client.batch_insert_documents("apps", docs))

    assert result["errors"] == 0
    assert [c.kwargs["json"] for c in session.post.call_args_list] == [docs, [{"_key": "b"}], [{"_key": "b"}]]
    assert result["result"] == [{"_key": "a"}, {"_key": "b", "_oldRev": "2"}, {"_key": "c"}]


async def test_only_the_batch_deletes_that_lost_are_sent_again(client) -> None:
    session = _session("delete", _Resp(200, [{"_key": "a"}, LOST]), _Resp(200, [{"_key": "b"}]))

    deleted = await _call(client, session, lambda: client.batch_delete_documents("records", ["a", "b"]))

    assert deleted == 2
    assert [c.kwargs["json"] for c in session.delete.call_args_list] == [["records/a", "records/b"], ["records/b"]]


async def test_a_batch_that_keeps_losing_is_raised(client) -> None:
    session = _session("post", *[_Resp(202, [{"_key": "a"}, LOST])] + [_Resp(202, [LOST])] * 5)

    with pytest.raises(Exception, match=r"\[1200\]"):
        await _call(client, session, lambda: client.batch_insert_documents("apps", [{"_key": "a"}, {"_key": "b"}]))
    assert session.post.call_count == 6


async def test_a_batch_in_a_transaction_is_left_to_its_owner(client) -> None:
    session = _session("post", _Resp(202, [{"_key": "a"}, LOST]), _Resp(202, [{"_key": "b"}]))

    with pytest.raises(Exception, match=r"\[1200\]"):
        await _call(
            client, session, lambda: client.batch_insert_documents("apps", [{"_key": "a"}, {"_key": "b"}], txn_id="t1")
        )
    assert session.post.call_count == 1


async def test_a_delete_that_lost_runs_again(client) -> None:
    session = _session("delete", _Resp(409, text=CONFLICT), _Resp(200, {"_key": "a"}))

    assert await _call(client, session, lambda: client.delete_document("records", "a")) is True
    assert session.delete.call_count == 2


@pytest.mark.parametrize("method", ["create_document", "create_edge"])
async def test_a_create_that_lost_is_not_taken_for_an_existing_document(client, method) -> None:
    """Both are a 409: only a unique-key violation means the document is there."""
    session = _session("post", _Resp(409, text=CONFLICT), _Resp(201, {"_key": "k"}))
    write = {
        "create_document": lambda: client.create_document("records", {"_key": "k"}),
        "create_edge": lambda: client.create_edge("edges", "a/1", "b/2", {"_key": "k"}),
    }[method]

    assert await _call(client, session, write) == {"_key": "k"}
    assert session.post.call_count == 2


async def test_a_create_of_an_existing_document_is_still_success(client) -> None:
    session = _session("post", _Resp(409, text=EXISTS))

    assert await _call(client, session, lambda: client.create_document("records", {"_key": "k"})) == {"_key": "k"}
    assert session.post.call_count == 1


@pytest.mark.parametrize("method, pass_txn", [
    ("patch", lambda c: c.update_document("apps", "c1", {"s": 1}, txn_id="t1")),
    ("post", lambda c: c.execute_aql("UPDATE ...", {}, "t1")),  # positional, as the provider passes it
])
async def test_a_write_in_a_transaction_is_left_to_its_owner(client, method, pass_txn) -> None:
    session = _session(method, _Resp(409, text=CONFLICT), _Resp(200, {}))

    with pytest.raises(Exception, match="1200"):
        await _call(client, session, lambda: pass_txn(client))
    assert getattr(session, method).call_count == 1


async def test_a_conflict_that_persists_is_raised(client) -> None:
    session = _session("patch", *[_Resp(409, text=CONFLICT)] * 6)

    with pytest.raises(Exception, match="write-write conflict"):
        await _call(client, session, lambda: client.update_document("apps", "c1", {"s": 1}))
    assert session.patch.call_count == 6


@pytest.mark.parametrize("status, body", [(404, '{"errorNum": 1202}'), (500, "server error"), (409, EXISTS)])
async def test_any_other_failure_is_not_run_again(client, status, body) -> None:
    session = _session("patch", _Resp(status, text=body), _Resp(200, {}))

    with pytest.raises(Exception, match=f"status={status}"):
        await _call(client, session, lambda: client.update_document("apps", "c1", {"s": 1}))
    assert session.patch.call_count == 1


@pytest.mark.parametrize("error, expected", [
    (ArangoQueryError("Query failed", 409, CONFLICT), True),
    (ArangoQueryError("Query failed", 409, EXISTS), False),
    (Exception(f"Failed to update document (status=409): {LOCK_TIMEOUT}"), True),
    (Exception("Batch insert failed with 1 error(s): Item 1: [1200] write-write conflict"), True),
    (Exception("Batch delete failed with 1 error(s): Document r/a: [1200] write-write conflict"), True),
    (Exception("Item 0: [12000] other"), False),
    (Exception("connection reset"), False),
])
def test_what_counts_as_a_write_conflict(error, expected) -> None:
    assert is_write_conflict(error) is expected
