"""E2E4 LR: ``lookup_record`` resolves the Record IDs the other knowledge tools hand the model.

Found by E2E3's matrix: a Record ID (the identifier search, navigate, list_files and fetch_record all show and
take) answered "Not found or no access.", for the user's own record too. The resolver parsed it as an external id
(or, UUID-shaped, as a Drive file id) and looked it up only by ``externalRecordId``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.agents.actions.knowledge_graph.knowledge_graph import KnowledgeGraph
from app.config.constants.arangodb import Connectors, OriginTypes
from app.models.entities import Record, RecordType

ORG, USER, USER_KEY = "org-1", "user-1", "user-key-1"
DRIVE, OTHER = "conn-drive", "conn-elsewhere"
UUID_ID = "6f1c2b7e-3a9d-4c1e-9b0a-2d4e8f7a1c35"


def _record(record_id: str, name: str, *, connector_id: str = DRIVE, external: str | None = None) -> Record:
    return Record(
        id=record_id,
        record_name=name,
        record_type=RecordType.FILE,
        external_record_id=external or f"ext-{record_id}",
        version=1,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.GOOGLE_DRIVE,
        connector_id=connector_id,
        weburl=f"https://drive.example/{record_id}",
        indexing_status="COMPLETED",
    )


@dataclass
class Graph:
    """The provider methods the resolver calls, over a record table and the user's readable set."""

    records: dict[str, Record] = field(default_factory=dict[str, Record])
    readable: set[str] = field(default_factory=set[str])
    reads: list[str] = field(default_factory=list[str])

    async def get_user_by_user_id(self, user_id: str, **_: object) -> dict[str, str] | None:
        return {"_key": USER_KEY} if user_id == USER else None

    async def get_record_by_id(self, record_id: str, transaction: str | None = None) -> Record | None:
        self.reads.append(record_id)
        return self.records.get(record_id)

    async def get_record_by_external_id(self, connector_id: str, external_id: str, transaction: str | None = None) -> Record | None:
        return next(
            (r for r in self.records.values() if r.connector_id == connector_id and r.external_record_id == external_id),
            None,
        )

    async def get_record_by_issue_key(self, connector_id: str, key: str, transaction: str | None = None) -> Record | None:
        return next((r for r in self.records.values() if r.connector_id == connector_id and r.record_name == key), None)

    async def get_record_by_weburl(self, weburl: str, org_id: str | None = None) -> Record | None:
        return None

    async def get_knowledge_hub_node_access(self, node_id: str, user_key: str, org_id: str, folder_mime_types: list[str], transaction: str | None = None) -> dict[str, Any] | None:
        if user_key != USER_KEY or org_id != ORG or node_id not in self.readable or node_id not in self.records:
            return None
        record = self.records[node_id]
        return {"id": node_id, "name": record.record_name, "nodeType": "record", "connector": "DRIVE"}


def _state(graph: Graph, **extra: Any) -> dict[str, Any]:  # noqa: ANN401
    return {
        "org_id": ORG,
        "user_id": USER,
        "graph_provider": graph,
        "has_knowledge": True,
        "agent_knowledge": [{"connectorId": DRIVE, "type": "DRIVE", "name": "Drive"}],
        **extra,
    }


def _graph() -> Graph:
    records = {
        "rec-alice-x1": _record("rec-alice-x1", "Alice ledger"),
        "rec-bob-x1": _record("rec-bob-x1", "Bob ledger"),
        "rec-trash-x1": _record("rec-trash-x1", "Trashed ledger"),
        UUID_ID: _record(UUID_ID, "Quarterly plan"),
        "rec-far-x1": _record("rec-far-x1", "Elsewhere", connector_id=OTHER),
        "rec-ext-x1": _record("rec-ext-x1", "Named by external id", external="drive-file-4471"),
    }
    # The trashed record and bob's are not readable: the gate answers None for both, as for a missing id.
    return Graph(records=records, readable={"rec-alice-x1", UUID_ID, "rec-far-x1", "rec-ext-x1"})


async def _lookup(graph: Graph, *identifiers: str, **extra: Any) -> tuple[bool, str]:  # noqa: ANN401
    return await KnowledgeGraph(state=_state(graph, **extra)).lookup_record(identifiers=list(identifiers))


@pytest.mark.asyncio
async def test_lr1_a_readable_records_own_id_resolves_to_it() -> None:
    ok, text = await _lookup(_graph(), "rec-alice-x1")
    assert ok
    assert "Alice ledger" in text and "rec-alice-x1" in text, text


@pytest.mark.asyncio
async def test_lr2_a_uuid_record_id_resolves_as_the_record_not_as_a_drive_file_id() -> None:
    ok, text = await _lookup(_graph(), UUID_ID)
    assert ok
    assert "Quarterly plan" in text and UUID_ID in text, text


@pytest.mark.asyncio
async def test_lr3_unreadable_trashed_and_missing_ids_read_alike() -> None:
    graph = _graph()
    answers = [await _lookup(graph, rid) for rid in ("rec-bob-x1", "rec-trash-x1", "rec-none-x1")]
    texts = {text.replace(rid, "ID") for (_, text), rid in zip(answers, ("rec-bob-x1", "rec-trash-x1", "rec-none-x1"))}
    assert len(texts) == 1, answers
    assert "Bob ledger" not in str(answers) and "Trashed ledger" not in str(answers)
    # The unreadable ones were found and then refused by the gate, not missed by the lookup.
    assert {"rec-bob-x1", "rec-trash-x1"} <= set(graph.reads)


@pytest.mark.asyncio
async def test_lr4_a_short_label_from_the_requests_shortener_resolves() -> None:
    from app.utils.chat_helpers import RecordIdShortener

    shortener = RecordIdShortener()
    label = shortener.get_or_create_short_id("rec-alice-x1")
    ok, text = await _lookup(
        _graph(), label, enable_record_id_shortening=True, record_id_shortener=shortener
    )
    assert ok and "Alice ledger" in text, text


@pytest.mark.asyncio
async def test_lr5_an_external_id_still_resolves_by_external_id() -> None:
    ok, text = await _lookup(_graph(), "drive-file-4471")
    assert ok and "Named by external id" in text, text


@pytest.mark.asyncio
async def test_lr6_a_record_outside_the_users_catalog_is_not_resolved_by_its_id() -> None:
    ok, text = await _lookup(_graph(), "rec-far-x1")
    assert "Elsewhere" not in text, text
    assert ok and "Not found" in text, text


@pytest.mark.asyncio
async def test_lr7_found_while_building_a_denied_match_never_shortens_the_searched_trail() -> None:
    """The walk stopped at the first strategy that found a candidate, before the access gate, so the trail of
    strategies named in the answer told a denied issue key from one that does not exist."""
    graph = Graph(records={"rec-ops7": _record("rec-ops7", "OPS-7", connector_id="conn-jira")})
    jira = {"agent_knowledge": [{"connectorId": "conn-jira", "type": "JIRA", "name": "Jira"}]}
    _, denied = await _lookup(graph, "OPS-7", **jira)
    _, missing = await _lookup(graph, "OPS-8", **jira)
    assert denied.replace("OPS-7", "KEY") == missing.replace("OPS-8", "KEY"), (denied, missing)
