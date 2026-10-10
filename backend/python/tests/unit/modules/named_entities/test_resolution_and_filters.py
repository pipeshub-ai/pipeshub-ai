import logging

import pytest

from app.modules.entity_resolution.models import ResolutionMode
from app.modules.named_entities.domain.config import NamedEntityBudgets
from app.modules.named_entities.domain.kinds import EntityKind
from app.modules.named_entities.domain.models import NamedEntity
from app.modules.named_entities.graph_ops import (
    MATCHED_KEYS_AQL,
    NODE_RECORDS_AQL,
    QUERY_AQL,
    RECORD_HITS_AQL,
    REDIRECTS_AQL,
    NamedEntityGraph,
    NamedEntityQuery,
)
from app.modules.named_entities.graph_writer import NamedEntityGraphWriter
from app.modules.named_entities.keys import named_entity_key
from app.modules.named_entities.normalizers.dates import NormalizationContext
from app.modules.named_entities.resolution import NamedEntityResolver, ResolvedEntity
from app.modules.named_entities.strategies.base import (
    ExtractionContext,
    ExtractionDocument,
)
from app.modules.named_entities.strategies.selector import ExtractionStrategySelector
from app.modules.retrieval.entity_filters import (
    TOO_BROAD_MESSAGE,
    EntityFilterResolver,
    EntityFilterUnavailableError,
)


def _org(name: str) -> NamedEntity:
    return NamedEntity(kind=EntityKind.ORGANIZATION, display_name=name, norm_key=f"name:organization:{name.casefold()}")


def test_keys_include_org_and_kind():
    left = named_entity_key("org-a", "organization", "name:organization:acme")
    right = named_entity_key("org-b", "organization", "name:organization:acme")
    other = named_entity_key("org-a", "product", "name:organization:acme")
    assert left != right
    assert left != other


class _Graph:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def find_named_entities(self, org_id, kind, keys):
        self.calls.append((org_id, kind, list(keys)))
        return [row for row in self.rows if row.get("kind", kind) == kind and row.get("orgId", org_id) == org_id]


async def test_shadow_records_a_match_without_changing_the_key():
    existing = "existing-key"
    graph = _Graph([{"normKey": "name:organization:acme", "_key": existing, "orgId": "org", "kind": "organization"}])
    resolved = await NamedEntityResolver(graph, mode=ResolutionMode.SHADOW).resolve("org", [_org("Acme")])
    assert resolved[0].graph_key == named_entity_key("org", "organization", "name:organization:acme")
    assert resolved[0].shadow_target == existing
    assert resolved[0].graph_key != existing


async def test_invalid_candidate_id_stays_new(monkeypatch):
    async def _select(self, ambiguous):
        return {"Acme": "forged-id"}

    monkeypatch.setattr(NamedEntityResolver, "_select", _select)

    async def search(org_id, kind, name):
        return [{"id": "real-id", "name": "Completely Different", "orgId": org_id, "kind": kind}]

    resolved = await NamedEntityResolver(
        _Graph([]), vector_search=search, llm=object(), mode=ResolutionMode.APPLY,
    ).resolve("org", [_org("Acme")])
    own = named_entity_key("org", "organization", "name:organization:acme")
    assert resolved[0].graph_key == own


async def test_value_kinds_do_not_ask_the_graph():
    graph = _Graph([])
    entity = NamedEntity(kind=EntityKind.CURRENCY, display_name="$1", norm_key="money:USD:1")
    resolved = await NamedEntityResolver(graph).resolve("org", [entity])
    assert resolved[0].graph_key == named_entity_key("org", "currency", "money:USD:1")
    assert graph.calls == []


class _Merged(_Graph):
    def __init__(self, rows, redirects=None, fail=False):
        super().__init__(rows)
        self.redirects = redirects or {}
        self.fail = fail

    async def resolve_named_entity_redirects(self, org_id, ids):
        if self.fail:
            raise RuntimeError("graph down")
        return {node_id: self.redirects.get(node_id, node_id) for node_id in ids}


@pytest.mark.parametrize("mode", list(ResolutionMode))
async def test_a_name_whose_node_was_merged_links_to_the_survivor_in_every_mode(mode):
    own = named_entity_key("org", "organization", "name:organization:acme")
    graph = _Merged(
        [{"normKey": "name:organization:acme", "_key": own, "orgId": "org", "kind": "organization", "mergedInto": "winner"}],
        {own: "winner"},
    )
    (resolved,) = await NamedEntityResolver(graph, mode=mode).resolve("org", [_org("Acme")])
    assert (resolved.graph_key, resolved.merged_into) == ("winner", "winner")


async def test_a_failed_redirect_lookup_keeps_the_entity_on_its_own_key():
    own = named_entity_key("org", "organization", "name:organization:acme")
    graph = _Merged(
        [{"normKey": "name:organization:acme", "_key": own, "orgId": "org", "kind": "organization", "mergedInto": "winner"}],
        fail=True,
    )
    (resolved,) = await NamedEntityResolver(graph, mode=ResolutionMode.APPLY).resolve("org", [_org("Acme")])
    assert resolved.graph_key == own


def _redirect_graph(hops):
    calls = []

    async def execute(statement, binds, **_):
        calls.append(binds)
        assert statement == REDIRECTS_AQL and binds["orgId"] == "org"
        return [{"id": node, "mergedInto": hops[node]} for node in binds["ids"] if node in hops]

    return NamedEntityGraph(execute, "arango"), calls


async def test_redirects_follow_a_chain_to_its_end():
    graph, _ = _redirect_graph({"a": "b", "b": "c"})
    assert await graph.resolve_redirects("org", ["a", "b", "x"]) == {"a": "c", "b": "c", "x": "x"}


async def test_a_redirect_cycle_stops_instead_of_looping():
    graph, calls = _redirect_graph({"a": "b", "b": "a"})
    assert await graph.resolve_redirects("org", ["a"]) == {"a": "b"}
    assert len(calls) <= 3


async def test_an_entity_id_filter_also_matches_the_node_it_was_merged_into():
    targets = []

    async def execute(statement, binds, **_):
        if statement == REDIRECTS_AQL:
            return [{"id": "loser", "mergedInto": "winner"}] if "loser" in binds["ids"] else []
        if statement == NODE_RECORDS_AQL:
            targets.extend(binds["targets"])
            return ["r1"]
        return [{"recordId": "r1", "virtualRecordId": "v1", "connectorId": "c1"}]

    result = await NamedEntityGraph(execute, "arango").records_for("org", ["loser"], NamedEntityQuery(), limit=10)
    assert sorted(targets) == ["namedEntities/loser", "namedEntities/winner"]
    assert [hit["recordId"] for hit in result["hits"]] == ["r1"]


class _Records:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    async def get_records_for_named_entities(self, org_id, entity_ids, query, limit=5000, within=None):
        self.calls += 1
        self.args = (entity_ids, query)
        return self.payload


async def test_entity_ids_and_a_typed_range_are_both_applied():
    graph = _Records({"hits": [], "truncated": False})
    await EntityFilterResolver(graph).match(
        "org", {"entityIds": ["e1"], "amount": {"min": 10, "max": 20, "currency": "USD"}},
    )
    ids, query = graph.args
    assert ids == ["e1"]
    assert (query.amount_min, query.amount_max, query.currency) == (10, 20, "USD")


async def test_records_for_narrows_a_name_match_to_the_given_ids():
    calls: list[tuple[str, dict]] = []

    async def execute(statement, binds, **_):
        calls.append((statement, dict(binds)))
        if statement == MATCHED_KEYS_AQL:
            return ["e1"]
        if statement == NODE_RECORDS_AQL:
            return ["r1"]
        return [{"recordId": "r1", "virtualRecordId": "v1", "connectorId": "c1"}]

    graph = NamedEntityGraph(execute, "arango")
    result = await graph.records_for("org", ["e1", "e2"], NamedEntityQuery(kinds=["organization"], name_prefix="Ac"), limit=10)

    (matched_sql, matched_binds), (_, record_binds), _hydrate = [c for c in calls if c[0] != REDIRECTS_AQL]
    assert "n._key IN @ids" in matched_sql and matched_binds["ids"] == ["e1", "e2"]
    assert (matched_binds["kinds"], matched_binds["prefix"]) == (["organization"], "ac")
    assert record_binds["targets"] == ["namedEntities/e1"]
    assert [hit["recordId"] for hit in result["hits"]] == ["r1"]


def _value_graph(by_constraint, cap_breaker=None):
    """A graph whose value rows answer per constraint: amount → records, date → records."""
    calls: list[dict] = []

    async def execute(statement, binds, **_):
        calls.append({"statement": statement, **binds})
        if statement == RECORD_HITS_AQL:
            return [{"recordId": rid, "virtualRecordId": f"v-{rid}", "connectorId": "c"} for rid in binds["recordIds"]]
        name = "amount" if binds.get("amountMin") is not None else "date" if binds.get("dateStart") is not None else "kinds"
        rows = by_constraint[name]
        if binds.get("within"):
            rows = [rid for rid in rows if rid in binds["candidates"]]
        return rows[: binds["limit"]]

    return NamedEntityGraph(execute, "arango"), calls


async def test_an_amount_and_a_date_match_a_record_that_mentions_both():
    graph, _ = _value_graph({"amount": ["r1", "r2"], "date": ["r2", "r3"]})
    query = NamedEntityQuery(amount_min=1000, amount_max=2000, currency="USD", date_start_ms=1, date_end_ms=2)
    result = await graph.records_for("org", None, query, limit=10)
    assert [hit["recordId"] for hit in result["hits"]] == ["r2"]
    assert result["truncated"] is False


async def test_a_constraint_too_broad_to_list_is_checked_within_a_narrow_one():
    broad = [f"r{i}" for i in range(50)]
    graph, calls = _value_graph({"amount": ["r7", "r99"], "date": broad})
    query = NamedEntityQuery(amount_min=1, amount_max=2, date_start_ms=1, date_end_ms=2)
    result = await graph.records_for("org", None, query, limit=10)
    assert [hit["recordId"] for hit in result["hits"]] == ["r7"]
    within = [call for call in calls if call.get("within")]
    assert len(within) == 1 and within[0]["candidates"] == ["r7", "r99"]


async def test_a_filter_whose_every_constraint_is_too_broad_is_truncated():
    broad = [f"r{i}" for i in range(50)]
    graph, _ = _value_graph({"amount": broad, "date": broad})
    query = NamedEntityQuery(amount_min=1, amount_max=2, date_start_ms=1, date_end_ms=2)
    assert await graph.records_for("org", None, query, limit=10) == {"hits": [], "truncated": True}


async def test_a_kind_of_the_typed_constraint_narrows_it_and_others_stand_alone():
    graph, calls = _value_graph({"amount": ["r1"], "date": ["r1"], "kinds": ["r1"]})
    query = NamedEntityQuery(kinds=["currency", "date"], amount_min=1, amount_max=2)
    await graph.records_for("org", None, query, limit=10)
    amount = next(call for call in calls if call.get("amountMin") is not None)
    standalone = next(call for call in calls if call.get("amountMin") is None and call["statement"] != RECORD_HITS_AQL)
    assert (amount["kinds"], standalone["kinds"]) == (["currency"], ["date"])


async def test_a_name_with_only_value_kinds_matches_nothing():
    graph, calls = _value_graph({"amount": [], "date": [], "kinds": []})
    result = await graph.records_for("org", None, NamedEntityQuery(kinds=["currency"], name_prefix="acme"), limit=10)
    assert result == {"hits": [], "truncated": False}
    assert not any(call["statement"] == MATCHED_KEYS_AQL for call in calls)


async def test_records_for_skips_the_lookup_when_nothing_matched():
    calls: list[str] = []

    async def execute(statement, binds, **_):
        calls.append(statement)
        return []

    graph = NamedEntityGraph(execute, "arango")
    result = await graph.records_for("org", None, NamedEntityQuery(kinds=["currency"]), limit=10)
    assert result == {"hits": [], "truncated": False}
    assert len(calls) == 1


async def test_entity_filter_returns_the_mentioning_records_and_grants_nothing():
    graph = _Records({"hits": [
        {"recordId": "r1", "virtualRecordId": "v1", "connectorId": "app-1"},
        {"recordId": "r2", "virtualRecordId": "v1", "connectorId": "app-2"},
        {"recordId": "r3", "virtualRecordId": "v3", "connectorId": "app-1"},
    ], "truncated": False})
    match = await EntityFilterResolver(graph).match("org", {"entityIds": ["e1"]})
    assert match.error is None
    assert [hit.record_id for hit in match.hits] == ["r1", "r2", "r3"]
    assert match.virtual_record_ids == ["v1", "v3"]


async def test_truncated_entity_filter_is_an_error():
    graph = _Records({"hits": [], "truncated": True})
    match = await EntityFilterResolver(graph).match("org", {"entityIds": ["e1"]})
    assert match.error == TOO_BROAD_MESSAGE
    assert match.hits == ()


async def test_an_entity_filter_without_a_constraint_narrows_nothing():
    graph = _Records({"hits": [], "truncated": False})
    match = await EntityFilterResolver(graph).match("org", {"kinds": []})
    assert not match.constrained


async def test_a_failed_entity_lookup_is_not_an_empty_match():
    class _Down:
        async def get_records_for_named_entities(self, *args, **kwargs):
            raise RuntimeError("graph down")

    with pytest.raises(EntityFilterUnavailableError):
        await EntityFilterResolver(_Down()).match("org", {"entityIds": ["e1"]})


def test_selector_failure_ladder():
    selector = ExtractionStrategySelector()
    doc = ExtractionDocument(units=[])
    ctx = ExtractionContext(
        org_id="org",
        norm=NormalizationContext(),
        enabled=frozenset({EntityKind.EMAIL}),
        budgets=NamedEntityBudgets(),
    )
    assert selector.choose(doc, ctx) == "single_call"
    from app.modules.named_entities.text import TextUnit

    doc = ExtractionDocument(units=[TextUnit(0, "b", "a@b.co")])
    assert selector.choose(doc, ctx) == "deterministic"
    ctx.enabled = frozenset({EntityKind.ORGANIZATION})
    ctx.under_pressure = True
    assert selector.choose(doc, ctx) == "single_call"
    ctx.under_pressure = False
    ctx.supports_tools = False
    assert selector.choose(doc, ctx) == "single_call"
    ctx.supports_tools = True
    assert selector.choose(doc, ctx) == "agent"


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        ({"kinds": ["spaceship"]}, "Unknown entity kind: spaceship"),
        ({"name": "   "}, "name must not be blank"),
        ({"mentionedDate": {"from": "last tuesday"}}, "mentionedDate.from must be epoch milliseconds or an ISO-8601 date"),
        ({"amount": {"min": 1, "currency": "dollars"}}, "amount.currency must be a three-letter ISO 4217 code"),
        ({"amount": {"min": 10, "max": 1}}, "a range's start must not be after its end"),
        ({"percent": {"min": float("nan")}}, "percent.min must be a finite number"),
    ],
)
async def test_an_unusable_filter_is_refused_before_any_lookup(raw, error):
    graph = _Records({"hits": [], "truncated": False})
    match = await EntityFilterResolver(graph).match("org", raw)
    assert match.error == error
    assert graph.calls == 0


async def test_kinds_and_currency_are_read_case_insensitively_and_dates_as_iso():
    graph = _Records({"hits": [], "truncated": False})
    await EntityFilterResolver(graph).match("org", {
        "kinds": ["PERSON"], "amount": {"min": 1, "currency": "usd"},
        "mentionedDate": {"from": "2026-03-01", "to": "2026-04-01T00:00:00+00:00"},
    })
    _, query = graph.args
    assert (query.kinds, query.currency) == (["person"], "USD")
    assert (query.date_start_ms, query.date_end_ms) == (1772323200000, 1775001600000)


async def test_query_binds_are_parameters():
    seen = {}

    async def execute(query, binds):
        seen["query"] = query
        seen["binds"] = binds
        return []

    await NamedEntityGraph(execute, "arango").query(
        "org-1",
        NamedEntityQuery(kinds=["organization"], name_prefix="Q"),
    )
    assert seen["query"] == QUERY_AQL
    assert seen["binds"]["orgId"] == "org-1"
    assert (seen["binds"]["kinds"], seen["binds"]["prefix"]) == (["organization"], "q")
    # Typed values live on value rows, never on entity nodes.
    assert "dateStart" not in seen["binds"]
    assert "org-1" not in seen["query"]


class _Tx:
    txn = "txn-1"

    def __init__(self, existing):
        self.existing = existing
        self.created = []
        self.deleted = []
        self.updates = []

    async def get_edges_from_node_with_target_name(self, record_from, edge_collection, raise_on_error=True):
        return self.existing

    async def batch_create_edges(self, edges, collection):
        self.created.extend(edges)

    async def batch_delete_edges(self, edges, collection):
        self.deleted.extend(edges)
        return len(edges)

    async def batch_update_nodes(self, docs, collection):
        self.updates.append(docs)


class _Store:
    def __init__(self, tx):
        self.tx = tx

    def transaction(self):
        return self

    async def __aenter__(self):
        return self.tx

    async def __aexit__(self, *args):
        return False


class _Nodes:
    def __init__(self):
        self.created = []
        self.values = {}

    def is_named_entity_write_retryable(self, error):
        return False

    async def replace_named_entity_values(self, record_id, docs, transaction=None):
        self.values[record_id] = docs

    async def clear_named_entity_persist_retry(self, record_id, due_at, transaction=None):
        return True

    async def create_named_entities_if_absent(self, docs):
        self.created.extend(docs)

    async def add_named_entity_aliases(self, *args):
        return None


async def test_writer_reconciles_edges_and_leaves_the_record_alone():
    tx = _Tx([{"_to": "namedEntities/old", "name": "Old", "createdAtTimestamp": 1}])
    graph = _Nodes()
    writer = NamedEntityGraphWriter(graph, _Store(tx), logging.getLogger("test"))
    entity = _org("Acme")
    resolved = ResolvedEntity(entity=entity, graph_key="new-key")
    embedded = await writer.write("org", "rec", [resolved])
    assert embedded and embedded[0].kind == "organization"
    assert len(tx.created) == 1
    assert tx.deleted

    await writer.clear_for_record("rec")
    assert tx.updates == []


async def test_a_caller_may_list_more_matches_than_the_default_page_up_to_the_scan_cap():
    many = [f"r{i}" for i in range(6000)]
    graph, _ = _value_graph({"amount": many, "date": [], "kinds": []})
    query = NamedEntityQuery(amount_min=1, amount_max=2, currency="USD")

    assert (await graph.records_for("org", None, query, limit=5000))["truncated"] is True
    listed = await graph.records_for("org", None, query, limit=20_000)
    assert listed["truncated"] is False and len(listed["hits"]) == 6000
    capped = await graph.records_for("org", None, query, limit=10**9)
    assert capped["truncated"] is False
