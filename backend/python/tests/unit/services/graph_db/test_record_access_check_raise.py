"""``check_record_access_with_details`` raises a graph read that failed, on every store.

It answers ``None`` for "may not open", so a failed read answered ``None`` too told a person who may open the record
that they may not: during an outage the record-content routes answered 403/404 instead of 503. Each store here is the
real provider with only its lowest client faked; the fake fails the reads named in ``failing`` and honours
``raise_on_error`` as the real client does (a failure without the flag is None, with it a raise).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
from app.config.constants.neo4j import collection_to_label
from app.core.org_context import OrgContext, bind
from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.services.graph_db.arango.arango_http_client import ArangoHTTPClient
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.interface.graph_db_provider import AccessCheck
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.services.graph_db.postgres.provider import PostgresGraphProvider

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

ORG, USER, USER_KEY, REC, KB_APP = "org-1", "u1", "uk1", "rec-1", "kb-1"
TYPE_DOCS = {"FILE": CollectionNames.FILES.value, "MAIL": CollectionNames.MAILS.value, "TICKET": CollectionNames.TICKETS.value}
SUB_RECORD = {"FILE": "fileRecord", "MAIL": "mailRecord", "TICKET": "ticketRecord"}
_LABELS = {collection_to_label(c): c for c in ("records", *TYPE_DOCS.values())}


class _Down(ConnectionError):
    pass


class _Graph:
    """What the fake store holds, and which reads fail ("user", "apps", "kb_app", or a collection name)."""

    def __init__(self, failing: set[str], *, record_type: str = "FILE", user: bool = True, record: bool = True,
                 deleted: bool = False, kb: bool = False, type_doc: bool = True) -> None:
        self.failing, self.user, self.has_type_doc = failing, user, type_doc
        self.record = {"id": REC, "orgId": ORG, "recordType": record_type, "recordName": "a.pdf", "origin": "UPLOAD",
                       "isDeleted": deleted} if record else None
        if kb and self.record:
            self.record |= {"connectorName": Connectors.KNOWLEDGE_BASE.value, "connectorId": KB_APP}

    def read(self, what: str) -> None:
        if what in self.failing:
            raise _Down("graph down at 10.0.0.7:8529")

    def doc(self, coll: str) -> dict | None:
        if coll == "records":
            return dict(self.record) if self.record else None
        return {"id": REC, "webUrl": "https://x"} if self.has_type_doc else None


def _granted(_user_key: str, _org_id: str, *, node_ids=(), **_k: object) -> AccessCheck:
    return AccessCheck(node_ids=frozenset(node_ids))


def _arango(g: _Graph) -> ArangoHTTPProvider:
    async def execute_aql(_q: str, bind_vars: dict[str, Any] | None = None, **_k: object) -> list:
        bv = bind_vars or {}
        if "user_id" in bv:
            g.read("user")
            return [{"_key": USER_KEY, "userId": USER, "email": "u@x"}] if g.user else []
        if "user_key" in bv:
            g.read("apps")
            return []
        return [[{"type": "DIRECT", "role": "OWNER", "source": {}}]] if "userId" in bv else [{}]

    async def get_document(collection: str, _key: str, txn_id=None, *, raise_on_error: bool = False) -> dict | None:
        try:
            g.read(collection)
        except _Down:
            if raise_on_error:
                raise
            return None
        d = g.doc(collection)
        return {"_key": d.pop("id"), **d} if d else None

    p = ArangoHTTPProvider(logger=MagicMock(spec=logging.Logger), config_service=MagicMock())
    p.http_client = AsyncMock()
    p.http_client.execute_aql = AsyncMock(side_effect=execute_aql)
    p.http_client.get_document = AsyncMock(side_effect=get_document)
    return p


def _neo4j(g: _Graph) -> Neo4jProvider:
    async def execute_query(q: str, parameters: dict[str, Any] | None = None, **_k: object) -> list:
        prm = parameters or {}
        if "user_id" in prm:
            g.read("user")
            return [{"u": {"id": USER_KEY, "userId": USER, "email": "u@x"}}] if g.user else []
        if "key" in prm:
            coll = next(c for label, c in _LABELS.items() if f"(n:{label} " in q)
            g.read(coll)
            d = g.doc(coll)
            return [{"n": d}] if d else []
        return []   # the path and the label queries: no paths, no labels

    p = Neo4jProvider(logger=MagicMock(spec=logging.Logger), config_service=MagicMock())
    p.client = AsyncMock()
    p.client.execute_query = AsyncMock(side_effect=execute_query)
    p.get_knowledge_hub_access_context_v2 = AsyncMock(return_value={"gated_app_ids": []})
    return p


def _postgres(g: _Graph) -> PostgresGraphProvider:
    class _Tx:
        async def fetchrow(self, sql: str, *args: object) -> dict | None:
            if "graph.user_by_user_id" in sql:
                g.read("user")
                return {"node_id": USER_KEY, "doc": {"id": USER_KEY, "userId": USER, "email": "u@x"}} if g.user else None
            coll, key = args[1], args[2]
            if coll == "apps":
                g.read("kb_app")
                return {"doc": {"id": key, "type": Connectors.KNOWLEDGE_BASE.value, "name": "KB"}}
            g.read(coll)
            d = g.doc(coll)
            return {"doc": d} if d else None

        async def fetch(self, _sql: str, *_a: object) -> list:
            return []

    async def run(fn: Callable, **_k: object) -> object:
        return await fn(_Tx())

    db = MagicMock()
    db.run = AsyncMock(side_effect=run)
    p = PostgresGraphProvider(MagicMock(spec=logging.Logger), db)
    p._role_on = AsyncMock(return_value=None)
    return p


STORES = {"arango": _arango, "neo4j": _neo4j, "postgres": _postgres}


def _provider(make: Callable, g: _Graph, *, check: object = _granted) -> Any:  # noqa: ANN401
    p = make(g)
    p.check_access = AsyncMock(return_value=check) if isinstance(check, AccessCheck) else AsyncMock(side_effect=check)
    return p


async def _check(p: Any) -> dict | None:  # noqa: ANN401
    return await p.check_record_access_with_details(USER, ORG, REC)


def _warned(p: Any) -> list:  # noqa: ANN401
    return [c for level in ("warning", "error", "exception", "critical") for c in getattr(p.logger, level).call_args_list]


@pytest.fixture(autouse=True)
def _org() -> Iterator[None]:
    with bind(OrgContext(ORG, USER, "user", f"user:{USER}", "t")):
        yield


@pytest.mark.parametrize("make", STORES.values(), ids=STORES.keys())
class TestAFailedReadRaises:
    async def test_the_fake_store_grants_with_details(self, make: Callable) -> None:
        got = await _check(_provider(make, _Graph(set())))
        assert got is not None
        assert got["record"]["fileRecord"]["webUrl"] == "https://x"

    async def test_a_failed_user_read(self, make: Callable) -> None:
        with pytest.raises(_Down):
            await _check(_provider(make, _Graph({"user"})))

    async def test_a_failed_record_read(self, make: Callable) -> None:
        with pytest.raises(_Down):
            await _check(_provider(make, _Graph({"records"})))

    @pytest.mark.parametrize("record_type", TYPE_DOCS)
    async def test_a_failed_type_document_read(self, make: Callable, record_type: str) -> None:
        with pytest.raises(_Down):
            await _check(_provider(make, _Graph({TYPE_DOCS[record_type]}, record_type=record_type)))

    async def test_an_unavailable_access_check_propagates(self, make: Callable) -> None:
        with pytest.raises(PermissionVerificationUnavailableError):
            await _check(_provider(make, _Graph(set()), check=PermissionVerificationUnavailableError()))

    @pytest.mark.parametrize("read", ["user", "records", "files"])
    async def test_the_provider_leaves_the_raised_failure_to_its_caller_to_log(self, make: Callable, read: str) -> None:
        p = _provider(make, _Graph({read}))
        with pytest.raises(_Down):
            await _check(p)
        assert not _warned(p), "logged once, by the layer that handles the raise"


@pytest.mark.parametrize("make", STORES.values(), ids=STORES.keys())
class TestAnAnswerStaysAnAnswer:
    async def test_an_absent_user_is_none(self, make: Callable) -> None:
        assert await _check(_provider(make, _Graph(set(), user=False))) is None

    async def test_an_absent_record_is_none(self, make: Callable) -> None:
        assert await _check(_provider(make, _Graph(set(), record=False))) is None

    async def test_a_record_in_the_trash_is_none(self, make: Callable) -> None:
        # Postgres leaves the trash to the batch check, which never admits a deleted node.
        check = AccessCheck() if make is _postgres else _granted
        assert await _check(_provider(make, _Graph(set(), deleted=True), check=check)) is None

    async def test_a_record_the_check_refuses_is_none(self, make: Callable) -> None:
        assert await _check(_provider(make, _Graph(set()), check=AccessCheck())) is None

    async def test_a_placeholder_is_none(self, make: Callable) -> None:
        stub = AccessCheck(node_ids=frozenset({REC}), placeholder_ids=frozenset({REC}))
        assert await _check(_provider(make, _Graph(set()), check=stub)) is None

    @pytest.mark.parametrize("record_type", TYPE_DOCS)
    async def test_a_record_without_its_type_document_keeps_its_details(self, make: Callable, record_type: str) -> None:
        got = await _check(_provider(make, _Graph(set(), record_type=record_type, type_doc=False)))
        assert got is not None
        assert got["record"][SUB_RECORD[record_type]] is None


class TestArango:
    async def test_a_failed_app_ids_read_raises_and_is_left_to_the_caller_to_log(self) -> None:
        p = _provider(_arango, _Graph({"apps"}))
        with pytest.raises(_Down):
            await _check(p)
        assert not _warned(p)

    async def test_the_user_and_the_record_are_each_read_once_more_no_longer(self) -> None:
        """The user is read twice (the gate reads it again), the record once: the re-reads after the path query are gone."""
        p = _provider(_arango, _Graph(set()))
        assert await _check(p)
        user_reads = [c for c in p.http_client.execute_aql.await_args_list if "user_id" in (c.kwargs.get("bind_vars") or {})]
        record_reads = [c for c in p.http_client.get_document.await_args_list if c.args[0] == "records"]
        assert (len(user_reads), len(record_reads)) == (2, 1)

    @pytest.mark.parametrize("body", ['{"error":true,"errorNum":1202,"code":404}', ""], ids=["1202", "empty"])
    async def test_a_document_the_server_does_not_have_is_none_through_the_real_client(self, body: str) -> None:
        """Not found at the client boundary is an answer, not an outage: the route keeps its 403/404."""
        response = MagicMock(status=404, text=AsyncMock(return_value=body))
        response.__aenter__ = AsyncMock(return_value=response)
        response.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.get.return_value = response
        p = _provider(_arango, _Graph(set()))
        p.http_client = ArangoHTTPClient("http://localhost:8529", "root", "pw", "db", MagicMock(spec=logging.Logger))
        p.http_client.execute_aql = AsyncMock(return_value=[{"_key": USER_KEY, "userId": USER}])
        with patch.object(p.http_client, "_get_session", new=AsyncMock(return_value=session)):
            assert await _check(p) is None


class TestPostgres:
    async def test_a_failed_collection_read_raises_for_a_kb_record(self) -> None:
        with pytest.raises(_Down):
            await _check(_provider(_postgres, _Graph({"kb_app"}, kb=True)))

    async def test_a_kb_record_whose_collection_is_read_answers(self) -> None:
        got = await _check(_provider(_postgres, _Graph(set(), kb=True)))
        assert got is not None
        assert got["knowledgeBase"] is None     # the fake user holds no role on it: read, and answered
