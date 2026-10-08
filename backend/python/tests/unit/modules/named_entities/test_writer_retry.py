import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.named_entities import graph_writer, write_retry
from app.modules.named_entities.domain.kinds import EntityKind
from app.modules.named_entities.domain.models import (
    EXTRACTOR_VERSION,
    Mention,
    NamedEntity,
)
from app.modules.named_entities.graph_ops import NamedEntityGraphMixin
from app.modules.named_entities.graph_writer import (
    NamedEntityGraphWriter,
    node_document,
)
from app.modules.named_entities.keys import KEY_SCHEME
from app.modules.named_entities.resolution import ResolvedEntity


class Transient(Exception):
    pass


def _resolved(name: str, key: str) -> ResolvedEntity:
    entity = NamedEntity(
        kind=EntityKind.ORGANIZATION,
        display_name=name,
        norm_key=f"name:organization:{name.lower()}",
        mentions=[Mention(block_index=0, char_start=0, char_end=len(name), surface=name, extractor="agent")],
    )
    return ResolvedEntity(entity=entity, graph_key=key)


class _Tx:
    txn = "txn-1"

    def __init__(self):
        self.updates = []

    async def batch_update_nodes(self, docs, collection):
        self.updates.append(docs)


class _Store:
    def __init__(self, failures: list[Exception | None]):
        self.tx = _Tx()
        self.failures = list(failures)
        self.entered = 0

    def transaction(self):
        return self

    async def __aenter__(self):
        self.entered += 1
        if self.failures:
            failure = self.failures.pop(0)
            if failure is not None:
                raise failure
        return self.tx

    async def __aexit__(self, *args):
        return False


def _graph(retryable=lambda exc: isinstance(exc, Transient)):
    graph = MagicMock()
    graph.create_named_entities_if_absent = AsyncMock()
    graph.add_named_entity_aliases = AsyncMock()
    graph.replace_named_entity_values = AsyncMock()
    graph.clear_named_entity_persist_retry = AsyncMock(return_value=True)
    graph.is_named_entity_write_retryable = retryable
    return graph


@pytest.fixture(autouse=True)
def _no_sleep():
    with patch.object(write_retry, "_backoff", AsyncMock()) as sleep:
        yield sleep


@pytest.fixture
def reconcile():
    with patch.object(graph_writer.EdgeReconciler, "reconcile", AsyncMock()) as mocked:
        yield mocked


async def _write(store, graph=None):
    writer = NamedEntityGraphWriter(graph or _graph(), store, logging.getLogger("test"))
    return await writer.write("org", "rec", [_resolved("Acme", "k1")])


def test_nodes_record_the_key_recipe():
    doc = node_document("org", _resolved("Acme", "k1"), 1)
    assert doc["keyScheme"] == KEY_SCHEME


async def test_a_deadlock_is_retried_and_the_second_run_lands(reconcile, _no_sleep):
    store = _Store([Transient("deadlock"), None])
    await _write(store)
    assert store.entered == 2
    reconcile.assert_awaited_once()
    _no_sleep.assert_awaited_once()


async def test_a_retry_claims_the_nodes_again_before_linking(reconcile):
    graph = _graph()
    calls = []
    graph.create_named_entities_if_absent = AsyncMock(side_effect=lambda docs: calls.append("claim"))
    reconcile.side_effect = lambda *a, **k: calls.append("link")
    store = _Store([Transient("conflict with the sweep"), None])
    await _write(store, graph)
    assert calls == ["claim", "claim", "link"]


async def test_a_conflict_while_claiming_is_retried(reconcile):
    graph = _graph()
    graph.create_named_entities_if_absent = AsyncMock(side_effect=[Transient("1200"), None])
    store = _Store([None])
    await _write(store, graph)
    assert graph.create_named_entities_if_absent.await_count == 2
    reconcile.assert_awaited_once()


async def test_an_error_that_is_not_a_collision_is_not_retried(reconcile):
    store = _Store([RuntimeError("bad"), None])
    with pytest.raises(RuntimeError):
        await _write(store)
    assert store.entered == 1


async def test_retries_are_bounded(reconcile):
    store = _Store([Transient(str(i)) for i in range(write_retry.WRITE_ATTEMPTS)] + [None])
    with pytest.raises(Transient):
        await _write(store)
    assert store.entered == write_retry.WRITE_ATTEMPTS
    reconcile.assert_not_awaited()


async def test_clearing_a_record_retries_too(reconcile):
    store = _Store([Transient("deadlock"), None])
    await NamedEntityGraphWriter(_graph(), store, logging.getLogger("test")).clear_for_record("rec")
    assert store.entered == 2
    assert reconcile.await_args.kwargs["new_tos"] == {}


async def test_edges_are_written_in_key_order_whatever_the_input_order(reconcile):
    writer = NamedEntityGraphWriter(_graph(), _Store([None]), logging.getLogger("test"))
    resolved = [_resolved("C", "k3"), _resolved("A", "k1"), _resolved("B", "k2")]
    await writer.write("org", "rec", resolved)
    kwargs = reconcile.await_args.kwargs
    assert list(kwargs["new_tos"]) == ["namedEntities/k1", "namedEntities/k2", "namedEntities/k3"]
    assert list(kwargs["edge_properties"]) == list(kwargs["new_tos"])


async def test_the_record_itself_is_never_written(reconcile):
    """An older build's strict records schema rejects every later update to a
    record carrying the entity-extraction fields, so this release writes none."""
    store = _Store([None, None])
    writer = NamedEntityGraphWriter(_graph(), store, logging.getLogger("test"))
    await writer.write("org", "rec", [_resolved("Acme", "k1")])
    await writer.clear_for_record("rec")
    assert store.tx.updates == []


async def test_the_extractor_version_is_stored_on_each_mention(reconcile):
    await _write(_Store([None]))
    (edge,) = reconcile.await_args.kwargs["edge_properties"].values()
    assert edge["extractorVersion"] == EXTRACTOR_VERSION


class _Provider(NamedEntityGraphMixin):
    _ner_dialect = "arango"

    def __init__(self, conflict=False, transient=False):
        self._conflict, self._transient = conflict, transient

    def is_write_conflict(self, error):
        return self._conflict

    def is_transient_error(self, error):
        return self._transient


@pytest.mark.parametrize(
    ("provider", "error", "expected"),
    [
        (_Provider(conflict=True), RuntimeError("x"), True),
        (_Provider(transient=True), RuntimeError("x"), True),
        (_Provider(), RuntimeError('{"errorNum":1210,"errorMessage":"unique constraint violated"}'), True),
        (_Provider(), RuntimeError("[1210] unique constraint violated"), True),
        (_Provider(), RuntimeError('{"errorNum":1200}'), False),
        (_Provider(), RuntimeError("schema validation failed"), False),
    ],
)
def test_arango_classification(provider, error, expected):
    assert provider.is_named_entity_write_retryable(error) is expected


def test_a_unique_violation_means_nothing_on_neo4j():
    provider = _Provider()
    provider._ner_dialect = "neo4j"
    assert provider.is_named_entity_write_retryable(RuntimeError("[1210]")) is False


def test_a_node_deleted_under_a_neo4j_write_is_retried_but_not_on_arango():
    error = RuntimeError("{neo4j_code: Neo.ClientError.Statement.EntityNotFound} {message: Unable to load NODE}")
    provider = _Provider()
    provider._ner_dialect = "neo4j"
    assert provider.is_named_entity_write_retryable(error) is True
    provider._ner_dialect = "arango"
    assert provider.is_named_entity_write_retryable(error) is False


async def test_arango_takes_back_the_orphan_mark_before_inserting():
    from app.modules.named_entities.graph_ops import (
        CREATE_AQL,
        RECLAIM_AQL,
        NamedEntityGraph,
    )

    statements = []

    async def execute(statement, binds):
        statements.append((statement, binds))

    await NamedEntityGraph(execute, "arango").create_if_absent([node_document("org", _resolved("Acme", "k1"), 1)])
    assert [statement for statement, _ in statements] == [RECLAIM_AQL, CREATE_AQL]
    assert statements[0][1] == {"keys": ["k1"]}


def test_neo4j_merge_takes_back_the_orphan_mark_on_a_marked_node_only():
    from app.modules.named_entities.graph_ops import CREATE_CYPHER

    assert "WHERE n.orphanedAt IS NOT NULL" in CREATE_CYPHER
    assert "SET n.orphanedAt = null" in CREATE_CYPHER


def test_a_dangling_edge_is_removed_only_while_its_target_is_gone():
    from app.modules.named_entities.graph_ops import DANGLING_MENTIONS_AQL

    assert "FILTER DOCUMENT(e._to) == null" in DANGLING_MENTIONS_AQL


async def test_a_retry_that_claimed_its_marker_and_then_deadlocked_still_lands(reconcile) -> None:
    """Where the claim auto-commits (Neo4j without explicit transactions) the marker
    is gone after the first attempt; the second must not read that as a newer write."""
    graph = _graph()
    graph.clear_named_entity_persist_retry = AsyncMock(side_effect=[True, False])
    reconcile.side_effect = [Transient("deadlock"), None]
    writer = NamedEntityGraphWriter(graph, _Store([None, None]), logging.getLogger("test"))

    await writer.write("org", "rec", [_resolved("Acme", "k1")], retry_due=100)

    dues = [call.args[1] for call in graph.clear_named_entity_persist_retry.await_args_list]
    assert dues == [100, None]
    assert reconcile.await_count == 2


async def test_a_retry_whose_marker_was_gone_before_it_claimed_is_superseded(reconcile) -> None:
    graph = _graph()
    graph.clear_named_entity_persist_retry = AsyncMock(return_value=False)
    writer = NamedEntityGraphWriter(graph, _Store([None]), logging.getLogger("test"))
    with pytest.raises(graph_writer.PersistRetrySupersededError):
        await writer.write("org", "rec", [_resolved("Acme", "k1")], retry_due=100)
    reconcile.assert_not_awaited()
