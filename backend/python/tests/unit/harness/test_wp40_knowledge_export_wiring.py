"""WP-40 (G-35): the PipesHub side of knowledge exports.

BW-1 (GA-19) an export reads the same scoped, ACL-filtered listing as ``knowledgegraph__list_files``, as the run's
     person: records of the run's sources only (never another source the person could read), every record inside
     them with or without a query, one line of metadata each and never content. A conversation limited to a
     selection is refused, and so is a scope that names nothing.
BW-2 (GA-19) the export events are the contract's, non-critical, and audited counts only.
BW-4 this build's knowledge access gate lets every read and export through: the run's policy is unchanged and one
     export writes up to ``MAX_EXPORT_RECORDS``; an edition's gate is the one the harness reads when it supplies one.
BW-5 (GA-19) the export tools are opaque to the run's label: a run that exported is of unknown sensitivity, so the
     flow rule asks before it sends anything out.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, create_autospec

import pytest

from app.harness.knowledge.access_gate import (
    MAX_EXPORT_RECORDS,
    OPEN_KNOWLEDGE_ACCESS,
    GatedKnowledge,
    KnowledgeAccessGate,
    knowledge_access_gate,
)
from app.harness.knowledge.bulk_export import (
    EXPORT_TOOL,
    ListingPages,
    exported_event,
    listing_pages_for,
)
from app.harness.labels import tool_kind
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from harness_core.edition import NO_HARNESS_EDITION, HarnessEdition
from harness_core.effects.types import allow_all, tool_id_for
from harness_core.knowledge.bulk_export import ExportRefusal
from harness_core.logs import HarnessLog
from tests.unit.agents.actions.kh_listing_stub import node, stub_listing

if TYPE_CHECKING:
    from pydantic import JsonValue

    from app.harness.journal_node.client import AttemptRoute
    from harness_core.effects.types import ApprovalPolicy
    from harness_core.journal.types import Claim as JournalClaim

USER_KEY = "user-key-1"
_VISIBLE = {
    "app-jira": [node(f"jira-{i}", f"ticket {i}") for i in range(250)],
    "app-drive": [node("drive-1", "budget sheet")],
    "kb-hr": [node("hr-1", "policy", origin="COLLECTION")],
}


@pytest.fixture
def graph() -> MagicMock:
    g = create_autospec(ArangoHTTPProvider, instance=True)
    stub_listing(g, USER_KEY, _VISIBLE)
    return g


def _state(graph: MagicMock, *, apps: list[str], kb: list[str], filters: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "graph_provider": graph,
        "org_id": "org-1",
        "user_id": "user-ext-1",
        "apps": apps,
        "kb": kb,
        "filters": filters or {},
        "logger": logging.getLogger("test.wp40"),
    }


async def _all(pages: ListingPages) -> list[dict[str, JsonValue]]:
    rows: list[dict[str, JsonValue]] = []
    cursor: str | None = None
    while True:
        page = await pages(cursor, 10_000)
        rows.extend(dict(row) for row in page.rows)
        cursor = page.next
        if cursor is None:
            return rows


async def test_bw1_ga19_an_export_reads_the_runs_sources_as_the_person_every_record_inside_them() -> None:
    graph = create_autospec(ArangoHTTPProvider, instance=True)
    stub_listing(graph, USER_KEY, _VISIBLE)
    plan = listing_pages_for(_state(graph, apps=["app-jira"], kb=[]), HarnessLog.named("test"))
    pages = await plan({})
    assert isinstance(pages, ListingPages)
    rows = await _all(pages)
    # Every Jira record (the service pages them), none of Drive's or the KB's, though the person could read them.
    assert {row["id"] for row in rows} == {f"jira-{i}" for i in range(250)}
    assert all(row["nodeType"] == "record" for row in rows)
    assert set(rows[0]) <= {
        "id", "name", "nodeType", "origin", "createdAt", "updatedAt", "recordType", "connector", "connectorId",
        "parentId", "mimeType", "extension", "sizeInBytes", "webUrl",
    }
    # Narrowing by source never widens: a source outside the run's scope is ignored.
    narrowed = await plan({"source_ids": ["app-drive"]})
    assert isinstance(narrowed, ListingPages)
    assert {row["id"] for row in await _all(narrowed)} == {f"jira-{i}" for i in range(250)}
    named = await plan({"query": "ticket 24"})
    assert isinstance(named, ListingPages)
    assert {row["id"] for row in await _all(named)} == {"jira-24"} | {f"jira-{i}" for i in range(240, 250)}


async def test_bw1_a_selection_or_an_empty_scope_is_refused(graph: MagicMock) -> None:
    selection = listing_pages_for(
        _state(graph, apps=["app-jira"], kb=[], filters={"records": ["jira-1"]}), HarnessLog.named("test")
    )
    refused = await selection({})
    assert isinstance(refused, ExportRefusal)
    assert "limited to items the user selected" in refused.message
    nothing = await listing_pages_for(_state(graph, apps=[], kb=[]), HarnessLog.named("test"))({})
    assert isinstance(nothing, ExportRefusal)


def test_bw2_ga19_the_export_events_are_the_contracts_and_counts_only() -> None:
    event = exported_event(
        {"taskId": "exp-1", "phase": "started", "records": 10_000, "requested": 10_000, "argsDigest": "sha256:" + "a" * 64}
    )
    assert (event.type, event.critical) == ("x.pipeshub.knowledge.exported", False)
    with pytest.raises(Exception):  # noqa: B017 - the contract's own validation error
        _ = exported_event({"taskId": "exp-1", "phase": "started", "records": 1, "requested": 1,
                            "argsDigest": "sha256:" + "a" * 64, "rows": [{"id": "r"}]})


class Claim:
    accepts = frozenset[str]()


class _Narrowing(KnowledgeAccessGate):
    def gate(
        self, policy: ApprovalPolicy, *, route: AttemptRoute | None, claim: JournalClaim, log: HarnessLog
    ) -> GatedKnowledge:
        del route, claim, log
        return GatedKnowledge(policy, lambda: 7)


def test_bw4_the_open_gate_lets_every_read_and_export_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.edition_services.harness_edition", lambda: NO_HARNESS_EDITION)
    gated = OPEN_KNOWLEDGE_ACCESS.gate(allow_all, route=None, claim=Claim(), log=HarnessLog.named("test"))  # type: ignore[arg-type]
    assert gated.policy is allow_all
    assert gated.export_allowance() == MAX_EXPORT_RECORDS == 10_000
    assert knowledge_access_gate() is OPEN_KNOWLEDGE_ACCESS
    narrowing = _Narrowing()
    monkeypatch.setattr(
        "app.edition_services.harness_edition", lambda: HarnessEdition({KnowledgeAccessGate: narrowing})
    )
    assert knowledge_access_gate() is narrowing


def test_bw5_ga19_a_run_that_exported_is_of_unknown_sensitivity() -> None:
    assert tool_kind(EXPORT_TOOL) == "opaque"
    assert tool_kind(tool_id_for("export_records", source="knowledgegraph")) == "opaque"
