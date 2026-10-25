"""Ticket properties per connector, on the path indexing really takes, and their cost.

Each ticket below is shaped like what that connector builds (its value mapper's
output, or the raw picklist it passes through), then checked three ways: what
``ticket_metadata`` provides, that the same ticket read back from the graph
documents provides the same values, and that indexing, which builds its record
from the graph's record document, writes ``properties.json`` for it.
"""

import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.events.processor as processor_module
from app.config.constants.arangodb import CollectionNames, Connectors
from app.connectors.utils.value_mapper import ValueMapper
from app.events.processor import Processor, convert_record_dict_to_record
from app.models.blocks import Block, BlocksContainer, BlockType
from app.models.entities import (
    DeliveryStatus,
    ItemType,
    Priority,
    RecordType,
    Status,
    TicketRecord,
)
from app.models.record_metadata import MAX_LIST_ITEMS, MAX_STRING_BYTES, RecordMetadata
from app.modules.artifacts import store as store_module
from app.modules.artifacts.models import PROPERTIES_PATH
from app.modules.artifacts.store import NodeArtifactStore
from app.modules.artifacts.writer import ArtifactWriter, build_revision, canonical_json
from app.modules.record_metadata.builtin import (
    TICKET_SCHEMA,
    TICKET_SCHEMA_ID,
    connector_source,
    ticket_metadata,
)
from app.modules.transformers import sink_orchestrator as sink_module
from app.modules.transformers.sink_orchestrator import SinkOrchestrator
from tests.unit.modules.artifacts.fake_storage import FakeStorage

ORG = "org-1"
MAPPER = ValueMapper()


def _ticket(connector: Connectors, **fields: Any) -> TicketRecord:  # noqa: ANN401
    return TicketRecord(
        id=f"t-{connector.name.lower()}", record_name="Login fails", record_type=fields.pop("record_type", RecordType.TICKET),
        external_record_id="EXT-1", version=1, origin="CONNECTOR", connector_name=connector,
        connector_id="conn-1", org_id=ORG, virtual_record_id="vr-1", mime_type="text/blocks",
        **fields,
    )


JIRA_DUE = 1_767_225_600_000
#: connector -> (ticket, the values its properties should hold)
CASES: dict[str, tuple[TicketRecord, dict[str, Any]]] = {
    "jira": (
        _ticket(Connectors.JIRA, status=Status.IN_PROGRESS, source_status="In Review", priority=Priority.HIGH,
                type=ItemType.BUG, assignee="Ada Lovelace", assignee_email="ada@example.com",
                reporter_name="Grace Hopper", reporter_email="grace@example.com", creator_name="Grace Hopper",
                creator_email="grace@example.com", labels=["auth", "web"], due_date=JIRA_DUE,
                resolved_at=None),
        {"status": "IN_PROGRESS", "sourceStatus": "In Review", "priority": "HIGH", "issueType": "BUG",
         "assignee": "Ada Lovelace", "assigneeEmail": "ada@example.com", "reporterName": "Grace Hopper",
         "reporterEmail": "grace@example.com", "creatorName": "Grace Hopper", "creatorEmail": "grace@example.com",
         "labels": ["auth", "web"], "dueDate": JIRA_DUE},
    ),
    "jira_data_center_unknowns": (
        _ticket(Connectors.JIRA_DATA_CENTER, status=Status.UNKNOWN, priority=Priority.UNKNOWN,
                type=ItemType.UNKNOWN, delivery_status=DeliveryStatus.UNKNOWN, source_status="Needs Triage",
                assignee_email="dc.user@example.com"),
        {"sourceStatus": "Needs Triage", "assigneeEmail": "dc.user@example.com"},
    ),
    "linear": (
        _ticket(Connectors.LINEAR, status=MAPPER.map_status("In Progress"), priority=MAPPER.map_priority("Urgent"),
                type=ItemType.SUB_ISSUE, delivery_status=None, assignee_email="lin@example.com",
                creator_email="lin2@example.com", labels=["Bug", "Frontend"]),
        {"status": "IN_PROGRESS", "priority": "HIGHEST", "issueType": "SUB_ISSUE",
         "assigneeEmail": "lin@example.com", "creatorEmail": "lin2@example.com", "labels": ["Bug", "Frontend"]},
    ),
    "zendesk": (
        _ticket(Connectors.ZENDESK, status=MAPPER.map_status("hold") or Status.UNKNOWN,
                priority=MAPPER.map_priority("normal") or Priority.UNKNOWN,
                type=MAPPER.map_type("problem") or ItemType.UNKNOWN, source_status="On-hold",
                reporter_email="req@example.com", assignee_email="agent@example.com",
                creator_email="req@example.com", labels=["vip", "refund_request"]),
        None,  # filled below from what the mapper returns
    ),
    "zammad": (
        _ticket(Connectors.ZAMMAD, status=Status.OPEN, priority=Priority.MEDIUM, type=ItemType.ISSUE,
                assignee_email="zam@example.com", creator_email="requester@example.com"),
        {"status": "OPEN", "priority": "MEDIUM", "issueType": "ISSUE", "assigneeEmail": "zam@example.com",
         "creatorEmail": "requester@example.com"},
    ),
    "salesforce_case": (
        # Salesforce passes the org's picklist values through as they are spelled there.
        _ticket(Connectors.SALESFORCE, record_type=RecordType.CASE, status="New", priority="High",
                type="Problem", assignee_email="owner@example.com", reporter_email="contact@example.com",
                creator_email="creator@example.com"),
        {"status": "NEW", "priority": "HIGH", "issueType": "PROBLEM", "assigneeEmail": "owner@example.com",
         "reporterEmail": "contact@example.com", "creatorEmail": "creator@example.com"},
    ),
    "github_teams_hidden_emails": (
        _ticket(Connectors.GITHUB_TEAMS, status=Status.DONE.value, priority="HIGH", type=ItemType.ISSUE.value,
                assignee_email="583231", creator_email="1024025", reporter_email="1024025",
                labels=["bug", "good first issue"], resolved_at=1_760_000_000_000, is_email_hidden=True),
        {"status": "DONE", "priority": "HIGH", "issueType": "ISSUE", "labels": ["bug", "good first issue"],
         "resolvedAt": 1_760_000_000_000},
    ),
    "gitlab": (
        # GitLab's issue.state, passed through.
        _ticket(Connectors.GITLAB, status="closed", type=ItemType.ISSUE.value, labels=["backend"],
                resolved_at=1_760_000_000_000, due_date=JIRA_DUE),
        {"status": "CLOSED", "issueType": "ISSUE", "labels": ["backend"], "resolvedAt": 1_760_000_000_000,
         "dueDate": JIRA_DUE},
    ),
}
_zd = CASES["zendesk"][0]
CASES["zendesk"] = (_zd, {
    k: v for k, v in {
        "status": getattr(_zd.status, "value", _zd.status), "priority": getattr(_zd.priority, "value", _zd.priority),
        "issueType": getattr(_zd.type, "value", _zd.type), "sourceStatus": "On-hold",
        "reporterEmail": "req@example.com", "assigneeEmail": "agent@example.com", "creatorEmail": "req@example.com",
        "labels": ["vip", "refund_request"],
    }.items() if v not in ("UNKNOWN", None)
})


def _values(metadata: RecordMetadata | None) -> dict[str, Any]:
    return {} if metadata is None else {k: v.value for k, v in metadata.values.items()}


@pytest.mark.parametrize("case", sorted(CASES))
def test_each_ticket_connector_maps_its_values(case: str) -> None:
    ticket, expected = CASES[case]
    metadata = ticket_metadata(ticket)
    assert _values(metadata) == expected
    if metadata is not None:
        assert metadata.schema_id == TICKET_SCHEMA_ID
        assert {v.source for v in metadata.values.values()} == {connector_source(ticket.connector_name)}


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_same_ticket_read_from_the_graph_provides_the_same_values(case: str) -> None:
    """The graph documents hold the ticket the connector wrote; reading them back must not change its properties,
    and must not turn hidden emails (the source's user ids) into emails."""
    ticket, _ = CASES[case]
    back = TicketRecord.from_arango_record(ticket.to_arango_record(), ticket.to_arango_base_record())
    assert _values(ticket_metadata(back)) == _values(ticket_metadata(ticket))


def test_hidden_emails_never_reach_properties_json() -> None:
    ticket, _ = CASES["github_teams_hidden_emails"]
    data = build_revision(ticket, BlocksContainer(blocks=[], block_groups=[])).files[PROPERTIES_PATH].data
    for user_id in ("583231", "1024025"):
        assert user_id.encode() not in data


def test_one_overlong_label_does_not_drop_every_label() -> None:
    ticket = _ticket(Connectors.ZENDESK, labels=["vip", "x" * (MAX_STRING_BYTES + 1), "refunds"])
    assert _values(ticket_metadata(ticket)).get("labels") == ["vip", "refunds"]


def test_labels_past_the_cap_are_cut_and_long_names_left_out() -> None:
    ticket = _ticket(Connectors.JIRA, labels=[f"l{i}" for i in range(200)], assignee="é" * 600)
    values = _values(ticket_metadata(ticket))
    assert values["labels"] == [f"l{i}" for i in range(MAX_LIST_ITEMS)]
    assert "assignee" not in values


@pytest.mark.parametrize("bad", [{"status": "Triage"}, {"priority": "P1"}, {"type": "Spike"},
                                 {"delivery_status": "late"}, {"due_date": -(2**64)}, {"resolved_at": 2**63}])
def test_a_value_that_does_not_fit_never_raises(bad: dict[str, Any]) -> None:
    ticket = _ticket(Connectors.JIRA, assignee="Ada", **bad)
    assert _values(ticket_metadata(ticket)) == {"assignee": "Ada"}


def test_records_that_are_not_tickets_have_none() -> None:
    plain = convert_record_dict_to_record(CASES["jira"][0].to_arango_base_record())
    assert ticket_metadata(plain) is None


# --- the path indexing takes --------------------------------------------------------------

def _blocks() -> BlocksContainer:
    return BlocksContainer(blocks=[Block(index=0, type=BlockType.TEXT, data="Login fails on Safari")],
                           block_groups=[])


REV = "0123456789abcdef"


def _graph(ticket: TicketRecord) -> MagicMock:
    """The record and tickets documents, as either graph store answers them."""
    docs = {
        CollectionNames.RECORDS.value: {**ticket.to_arango_base_record(), "contentRev": REV},
        CollectionNames.TICKETS.value: ticket.to_arango_record(),
    }
    graph = MagicMock()
    graph.get_document = AsyncMock(side_effect=lambda key, collection, *a, **kw: (
        dict(docs[collection]) if key == ticket.id and collection in docs else None
    ))
    graph.batch_update_nodes = AsyncMock(return_value=True)
    graph.update_node_fields_if_match = AsyncMock(return_value=True)
    return graph


async def _index_through_process_blocks(ticket: TicketRecord, storage: FakeStorage) -> MagicMock:
    """Processor.process_blocks (how a ticket's BlocksContainer is indexed) builds ctx.record from the
    graph's record document, then the real sink and artifact writer store the set."""
    graph = _graph(ticket)
    logger = logging.getLogger("test-ticket-properties")
    sink = SinkOrchestrator(graphdb=AsyncMock(), blob_storage=AsyncMock(), vector_store=AsyncMock(),
                            graph_provider=graph, logger=logger, config_service=MagicMock())
    sink.vector_store.apply = AsyncMock(return_value=True)
    sink._save_reconciliation_metadata = AsyncMock()  # type: ignore[method-assign]
    sink._sync_record_identity_entities = AsyncMock()  # type: ignore[method-assign]
    sink.artifact_writer = ArtifactWriter(NodeArtifactStore(storage.blob_storage(), logger), logger)

    class _Pipeline:
        async def apply(self, ctx: Any) -> None:  # noqa: ANN401
            await sink.index(ctx)

    processor = Processor.__new__(Processor)
    processor.logger = MagicMock()
    processor.graph_provider = graph
    processor.config_service = MagicMock()
    processor._pipeline = lambda: _Pipeline()  # type: ignore[method-assign]
    processor._process_blockgroups = AsyncMock(side_effect=lambda containers, _name: containers)  # type: ignore[method-assign]

    with patch.object(processor_module, "enhance_tables_with_llm", AsyncMock()), \
            patch.object(sink_module, "is_artifact_write_enabled", AsyncMock(return_value=True)), \
            patch.object(sink_module, "notify_record_indexed", AsyncMock()):
        async for _ in processor.process_blocks(
            ticket.record_name, ticket.id, 1, "CONNECTOR", ORG, _blocks().model_dump(mode="json", exclude_none=True),
            "vr-1",
        ):
            pass
    return graph


@pytest.fixture
async def storage() -> AsyncIterator[FakeStorage]:
    store_module.ARTIFACT_ROUTES.reset()
    store_module.PROPERTIES_FILE.reset()
    fake = FakeStorage()
    await fake.start()
    yield fake
    await fake.stop()


async def test_indexing_a_ticket_writes_its_properties(storage: FakeStorage) -> None:
    """A ticket with values gets properties.json on the path indexing takes, not only when a test
    hands the writer a TicketRecord."""
    ticket, expected = CASES["jira"]
    await _index_through_process_blocks(ticket, storage)

    root = FakeStorage.root("vr-1", REV)
    assert f"{root}/{PROPERTIES_PATH}" in storage.puts, "indexing wrote no properties.json"
    stored = storage.puts[f"{root}/{PROPERTIES_PATH}"].data
    assert _values(RecordMetadata.model_validate_json(stored)) == expected
    manifest = json.loads(storage.puts[f"{root}/manifest.json"].data)
    assert manifest["artifacts"]["properties"]["path"] == PROPERTIES_PATH
    # record.json stays the record as indexing built it: the stored copy readers compare against.
    assert "status" not in json.loads(storage.puts[f"{root}/record.json"].data)


async def test_indexing_a_ticket_with_hidden_emails_keeps_them_out(storage: FakeStorage) -> None:
    ticket, expected = CASES["github_teams_hidden_emails"]
    await _index_through_process_blocks(ticket, storage)

    stored = storage.puts[f"{FakeStorage.root('vr-1', REV)}/{PROPERTIES_PATH}"].data
    assert _values(RecordMetadata.model_validate_json(stored)) == expected
    for user_id in (b"583231", b"1024025"):
        assert user_id not in stored


# --- cost -----------------------------------------------------------------------------------

def _realistic(n: int) -> list[TicketRecord]:
    statuses, priorities = list(Status), list(Priority)
    return [
        _ticket(Connectors.JIRA, status=statuses[i % len(statuses)], priority=priorities[i % len(priorities)],
                type=ItemType.BUG, source_status="In Review", assignee=f"User {i}", assignee_email=f"u{i}@example.com",
                reporter_name="Reporter", reporter_email="r@example.com", creator_name="Creator",
                creator_email="c@example.com", labels=[f"label-{j}" for j in range(i % 12)],
                resolved_at=1_760_000_000_000 + i, due_date=JIRA_DUE)
        for i in range(n)
    ]


def test_ten_thousand_tickets_build_their_properties_within_budget() -> None:
    tickets = _realistic(10_000)
    started = time.perf_counter()
    sizes = [len(canonical_json(m.to_json_dict())) for m in map(ticket_metadata, tickets) if m is not None]
    elapsed = time.perf_counter() - started
    assert len(sizes) == 10_000
    # Budget: 0.5 ms per record (5 s for 10k), loose enough for a loaded CI worker.
    assert elapsed < 5.0, f"{elapsed:.2f}s for 10k tickets"
    print(f"\n10k tickets: {elapsed * 1000:.0f} ms ({elapsed / 10_000 * 1e6:.1f} us per record); "
          f"mean file {sum(sizes) / len(sizes):.0f} B")


def test_the_write_path_reads_no_configuration() -> None:
    """build_revision uses the built-in schema: no config store read per record."""
    import app.config.configuration_service as config_module

    calls: list[str] = []
    original = config_module.ConfigurationService.get_config

    async def spy(self: Any, key: str, *a: Any, **kw: Any) -> Any:  # noqa: ANN401
        calls.append(key)
        return await original(self, key, *a, **kw)

    config_module.ConfigurationService.get_config = spy  # type: ignore[method-assign]
    try:
        for ticket in _realistic(200):
            build_revision(ticket, _blocks())
    finally:
        config_module.ConfigurationService.get_config = original  # type: ignore[method-assign]
    assert calls == []


def test_properties_json_is_deterministic_and_canonical() -> None:
    ticket, _ = CASES["jira"]
    a = build_revision(ticket, _blocks()).files[PROPERTIES_PATH].data
    b = build_revision(ticket.model_copy(deep=True), _blocks()).files[PROPERTIES_PATH].data
    assert a == b == canonical_json(json.loads(a))
    assert set(json.loads(a)["values"]) <= {p.name for p in TICKET_SCHEMA.properties}
