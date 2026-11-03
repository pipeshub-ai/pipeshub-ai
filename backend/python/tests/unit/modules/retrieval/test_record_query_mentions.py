"""The ``mentions`` condition of a record query (``docs/record-query.md``):
text-extracted values resolved to records before the scan, the date range
inclusive by day, and a text mention never decided as a field."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.modules.retrieval.entity_permissions import (
    EntityAccessContext,
    EntityAccessError,
)
from app.modules.retrieval.record_query import engine
from app.modules.retrieval.record_query.engine import (
    TOO_BROAD_MENTION_MSG,
    QueryNotRunnable,
    run_record_query,
)
from app.modules.retrieval.record_query.evaluate import MENTIONS, evaluate_all
from app.modules.retrieval.record_query.model import MentionCondition, RecordQuery
from app.modules.retrieval.record_query.pushdown import is_selective, pushdown
from app.services.graph_db.common.record_query_rows import (
    CandidateKey,
    RecordCandidates,
    matches_nothing,
    record_query_seed,
)

_MAR_1 = 1_772_323_200_000  # 2026-03-01T00:00:00Z
_MAR_31 = 1_774_915_200_000  # 2026-03-31T00:00:00Z
_APR_1 = 1_775_001_600_000  # 2026-04-01T00:00:00Z
_DAY = 86_400_000

CONTEXT = EntityAccessContext(
    org_id="org", user_key="u", app_level_app_ids=frozenset(), record_level_app_ids=frozenset({"zd"}),
    record_group_ids=frozenset(), app_names={"zd": "Zendesk"},
)


def _mention(**mentions: Any) -> MentionCondition:  # noqa: ANN401
    (cond,) = RecordQuery.model_validate({"where": [{"mentions": mentions}]}).where
    assert isinstance(cond, MentionCondition)
    return cond


def _overlaps(raw: dict[str, Any], start_ms: int, end_ms: int) -> bool:
    """The graph's date predicate: a mention [start, end) overlaps [from, to)."""
    date = raw["mentionedDate"]
    return start_ms < date["to"] and end_ms > date["from"]


@pytest.mark.parametrize(
    ("date_from", "date_to", "mention", "matches"),
    [
        # "In March" as the model writes it: the last day as a date.
        ("2026-03-01", "2026-03-31", (_MAR_31, _APR_1), True),
        # One day: from == to.
        ("2026-03-31", "2026-03-31", (_MAR_31, _APR_1), True),
        ("2026-03-01", "2026-03-31", (_APR_1, _APR_1 + _DAY), False),
        # Epoch ms: a UTC midnight covers that whole day, any other instant ends there.
        (_MAR_1, _MAR_31, (_MAR_31, _APR_1), True),
        (_MAR_1, _APR_1 - 1, (_APR_1, _APR_1 + _DAY), False),
        (_MAR_1, _MAR_31 + 3_600_000, (_MAR_31 + 7_200_000, _MAR_31 + 7_200_001), False),
    ],
)
def test_the_date_range_end_is_inclusive_by_day(
    date_from: str | int, date_to: str | int, mention: tuple[int, int], matches: bool,
) -> None:
    raw = _mention(date={"from": date_from, "to": date_to}).entity_filter()
    assert _overlaps(raw, *mention) is matches


def test_a_mention_is_read_as_the_named_entity_filter() -> None:
    raw = _mention(kind=["organization", "product"], name="Acme", amount={"from": 50_000, "currency": "usd"},
                   percent={"to": 0.2}).entity_filter()
    assert raw == {
        "kinds": ["organization", "product"], "name": "Acme",
        "amount": {"min": 50_000, "max": None, "currency": "usd"}, "percent": {"min": None, "max": 0.2},
    }


@pytest.mark.parametrize("where", [
    [{"mentions": {}}],
    [{"mentions": {"kind": "invoice"}}],
    [{"mentions": {"date": {"from": "March"}}}],
    [{"any_of": [{"mentions": {"name": "Acme"}}, {"field": "status", "in": ["OPEN"]}]}],
    [{"not": {"mentions": {"name": "Acme"}}}],
])
def test_a_mention_the_graph_cannot_take_is_refused_before_any_database_work(where: list[Any]) -> None:
    with pytest.raises(ValidationError):
        RecordQuery.model_validate({"where": where})


def test_a_resolved_mention_pushes_its_records_down_and_seeds_the_scan() -> None:
    cond = _mention(name="Acme")
    assert "records" not in pushdown(RecordQuery(where=[cond]))
    cond.resolve(frozenset({"r2", "r1"}))
    pushed = pushdown(RecordQuery(where=[cond]))
    assert pushed["records"] == [["r1", "r2"]]
    assert record_query_seed(pushed).key == "records" and is_selective(pushed)
    cond.resolve(frozenset())
    assert matches_nothing(pushdown(RecordQuery(where=[cond])))


def test_a_record_the_mention_did_not_find_is_unknown_never_a_miss() -> None:
    cond = _mention(name="Acme")
    cond.resolve(frozenset({"r1"}))
    assert evaluate_all([cond], {"_key": "r1"}).value is True
    missed = evaluate_all([cond], {"_key": "r2"})
    assert missed.value is None and missed.unknown_fields == {MENTIONS}


class _Graph:
    """Named-entity hits for every mention, and a scan that applies ``records``."""

    def __init__(self, hits: list[dict[str, Any]], *, truncated: bool = False, fail: bool = False,
                 readable: frozenset[str] | None = None) -> None:
        self.hits, self.truncated, self.fail = hits, truncated, fail
        # Records the user can read; None: every one.
        self.readable = readable
        self.entity_calls: list[tuple[Any, dict[str, Any]]] = []
        self.scans: list[dict[str, Any]] = []

    async def get_records_for_named_entities(self, org_id: str, entity_ids: Any, query: Any, **kw: Any) -> dict:  # noqa: ANN401
        if self.fail:
            raise RuntimeError("down")
        self.entity_calls.append((query, kw))
        if kw.get("within") is not None:
            within = set(kw["within"])
            # Too broad within everything only when the user reads every record.
            return {"hits": [h for h in self.hits if h["recordId"] in within],
                    "truncated": self.truncated and self.readable is None}
        return {"hits": self.hits, "truncated": self.truncated}

    async def get_accessible_virtual_record_ids(self, user_id: str, org_id: str, filters: Any, **kw: Any) -> dict:  # noqa: ANN401
        ids = [h["recordId"] for h in self.hits] if self.readable is None else sorted(self.readable)
        return {f"v-{i}": i for i in ids}

    async def get_record_query_candidates(self, org_id: str, **kw: Any) -> RecordCandidates:  # noqa: ANN401
        self.scans.append(kw)
        ids = sorted(set.intersection(*(set(r) for r in kw["pushdown"].get("records", [[]]))))
        return RecordCandidates([CandidateKey(i, 1) for i in ids])

    async def get_record_query_rows(self, org_id: str, *, record_ids: list[str], **kw: Any) -> list[dict]:  # noqa: ANN401
        return [{"_key": i, "recordType": "FILE", "connectorName": "ZENDESK", "connectorId": "zd", "people": [],
                 "organizations": [], "links": []} for i in record_ids]


@pytest.fixture(autouse=True)
def _everything_readable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def admitted(provider: Any, context: object, keys: Any) -> frozenset[str]:  # noqa: ANN401
        readable = getattr(provider, "readable", None)
        return frozenset(k for k in keys if readable is None or k in readable)

    monkeypatch.setattr(engine, "admitted_node_ids", admitted)


def _hit(record_id: str, connector_id: str = "zd") -> dict[str, Any]:
    return {"recordId": record_id, "connectorId": connector_id, "virtualRecordId": f"v-{record_id}", "sortTs": 1}


async def test_the_engine_counts_the_records_whose_text_mentions_the_value_in_scope_only() -> None:
    graph = _Graph([_hit("r1"), _hit("r2"), _hit("r9", connector_id="elsewhere")])
    result = await run_record_query(graph, CONTEXT, RecordQuery.model_validate(
        {"where": [{"mentions": {"name": "Acme", "amount": {"from": 100_000, "currency": "USD"}}}]},
    ))
    assert result.total == 2 and result.unknown == {}
    (scan,) = graph.scans
    assert scan["pushdown"]["records"] == [["r1", "r2"]]
    (query, options), = graph.entity_calls
    assert query.name_prefix == "Acme" and query.currency == "USD" and options["limit"] == engine.SCAN_CAP


async def test_a_mention_too_broad_to_list_says_how_to_narrow_it() -> None:
    with pytest.raises(QueryNotRunnable, match="too many records"):
        await run_record_query(_Graph([], truncated=True), CONTEXT, RecordQuery.model_validate(
            {"where": [{"mentions": {"kind": "date"}}]},
        ))
    assert "narrow" in TOO_BROAD_MENTION_MSG


async def test_a_failed_mention_lookup_is_an_error_not_zero() -> None:
    with pytest.raises(EntityAccessError):
        await run_record_query(_Graph([], fail=True), CONTEXT, RecordQuery.model_validate(
            {"where": [{"mentions": {"name": "Acme"}}]},
        ))


async def test_a_person_given_by_name_never_reaches_the_engine_unresolved() -> None:
    with pytest.raises(QueryNotRunnable):
        await run_record_query(_Graph([]), CONTEXT, RecordQuery.model_validate(
            {"where": [{"person": {"name": "Priya Nair"}}]},
        ))


async def test_a_mention_too_broad_across_the_org_is_counted_within_what_the_user_can_read() -> None:
    graph = _Graph([_hit(f"r{i}") for i in range(5)], truncated=True, readable=frozenset({"r3"}))
    result = await run_record_query(graph, CONTEXT, RecordQuery.model_validate(
        {"where": [{"mentions": {"kind": "date"}}]},
    ), user_id="user-1")
    assert result.total == 1 and result.matched_ids == {"r3"} and not result.approximate
    assert graph.entity_calls[-1][1]["within"] == ["r3"]


async def test_a_mention_too_broad_across_the_org_that_the_user_reads_nowhere_counts_zero_exactly() -> None:
    graph = _Graph([_hit(f"r{i}") for i in range(5)], truncated=True, readable=frozenset())
    result = await run_record_query(graph, CONTEXT, RecordQuery.model_validate(
        {"where": [{"mentions": {"kind": "date"}}]},
    ), user_id="user-1")
    assert result.total == 0 and not result.approximate and result.next_cursor is None


@pytest.mark.parametrize(("readable", "approximate"), [(frozenset(), False), (frozenset({"r0"}), True)])
async def test_a_scan_stopped_at_its_deadline_is_approximate_only_once_it_admitted_a_record(
    readable: frozenset[str], approximate: bool,
) -> None:
    graph = _Graph([_hit(f"r{i}") for i in range(5)], readable=readable)
    result = await run_record_query(graph, CONTEXT, RecordQuery.model_validate(
        {"where": [{"mentions": {"name": "Acme"}}]},
    ), window=1, deadline_seconds=0.0)
    assert result.approximate is approximate and result.total == len(readable)
