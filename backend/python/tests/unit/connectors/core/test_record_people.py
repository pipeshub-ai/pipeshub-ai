"""Record -> member edges (KG-13 slice 1): who a record names, resolved to
existing users only, written idempotently, never bcc."""
from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.config.constants.arangodb import Connectors, EntityRelations, OriginTypes
from app.connectors.core.base.data_processor import record_people
from app.connectors.core.base.data_processor.record_people import (
    link_record_people,
    person_links,
)
from app.models.entities import (
    CommentRecord,
    DealRecord,
    FileRecord,
    MailRecord,
    ProjectRecord,
    PullRequestRecord,
    RecordType,
    TicketRecord,
)

BASE = {
    "id": "rec-1", "org_id": "org-1", "external_record_id": "ext-1", "record_name": "r",
    "origin": OriginTypes.CONNECTOR.value, "connector_name": Connectors.GOOGLE_MAIL, "connector_id": "conn-1",
    "version": 1, "source_created_at": 1000, "source_updated_at": 2000,
}
MEMBERS = {"ann@acme.com": "u-ann", "bob@acme.com": "u-bob", "cat@acme.com": "u-cat"}
SOURCE_MEMBERS = {"src-ann": "u-ann", "src-dan": "u-dan"}


def _store() -> MagicMock:
    store = MagicMock()
    store.get_user_by_email = AsyncMock(
        side_effect=lambda email: SimpleNamespace(id=MEMBERS[email]) if email in MEMBERS else None,
    )
    store.get_user_by_source_id = AsyncMock(
        side_effect=lambda sid, connector_id: SimpleNamespace(id=SOURCE_MEMBERS[sid]) if sid in SOURCE_MEMBERS else None,
    )
    store.delete_edges_from = AsyncMock()
    store.batch_create_entity_relations = AsyncMock()
    return store


def _written(store: MagicMock) -> list[dict]:
    """Every edge written, across the record's write batches."""
    return [e for call in store.batch_create_entity_relations.await_args_list for e in call.args[0]]


def _edges(store: MagicMock) -> set[tuple[str, str]]:
    return {(e["_to"], e["edgeType"]) for e in _written(store)}


def _mail(**kw: object) -> MailRecord:
    return MailRecord(record_type=RecordType.MAIL, **{**BASE, **kw})


class TestMail:
    async def test_sender_to_and_cc_members_are_linked_and_bcc_never(self) -> None:
        store = _store()
        mail = _mail(
            from_email="Ann Lee <Ann@Acme.com>", to_emails=["bob@acme.com", "stranger@else.com"],
            cc_emails=["cat@acme.com"], bcc_emails=["bob@acme.com", "cat@acme.com"],
        )
        written = await link_record_people(mail, store, logging.getLogger("t"))
        assert _edges(store) == {
            ("users/u-ann", "AUTHORED_BY"), ("users/u-bob", "ADDRESSED_TO"), ("users/u-cat", "ADDRESSED_TO"),
        }
        assert written == 3
        assert all(e["sourceTimestamp"] == 1000 for e in _written(store))

    async def test_a_non_member_gets_no_edge_and_no_node(self) -> None:
        store = _store()
        await link_record_people(_mail(from_email="stranger@else.com"), store, logging.getLogger("t"))
        store.batch_create_entity_relations.assert_not_awaited()
        assert not any(name.startswith("upsert") for name, *_ in store.method_calls)

    async def test_a_recipient_named_twice_is_one_edge(self) -> None:
        store = _store()
        await link_record_people(
            _mail(from_email="ann@acme.com", to_emails=["bob@acme.com"], cc_emails=["Bob <bob@acme.com>"]),
            store, logging.getLogger("t"),
        )
        assert sorted(_edges(store)) == [("users/u-ann", "AUTHORED_BY"), ("users/u-bob", "ADDRESSED_TO")]
        assert store.get_user_by_email.await_count == 2


class TestOtherRecordTypes:
    def test_ticket_with_hidden_emails_links_by_source_id(self) -> None:
        ticket = TicketRecord(
            record_type=RecordType.TICKET, is_email_hidden=True, assignee_source_id=["src-ann"],
            reporter_source_id="src-dan", assignee_email="12345", **BASE,
        )
        links = person_links(ticket)
        assert {(link.edge_type, link.source_id) for link in links} == {
            (EntityRelations.ASSIGNED_TO, "src-ann"), (EntityRelations.REPORTED_BY, "src-dan"),
        }
        assert all(link.email is None for link in links)

    async def test_ticket_and_project_keep_their_edges(self) -> None:
        store = _store()
        ticket = TicketRecord(
            record_type=RecordType.TICKET, assignee_email="ann@acme.com", creator_email="bob@acme.com",
            reporter_email="cat@acme.com", assignee_source_timestamp=5, **BASE,
        )
        await link_record_people(ticket, store, logging.getLogger("t"))
        edges = _written(store)
        assert {(e["_to"], e["edgeType"]) for e in edges} == {
            ("users/u-ann", "ASSIGNED_TO"), ("users/u-bob", "CREATED_BY"), ("users/u-cat", "REPORTED_BY"),
        }
        assert next(e for e in edges if e["edgeType"] == "ASSIGNED_TO")["sourceTimestamp"] == 5

        store = _store()
        await link_record_people(
            ProjectRecord(record_type=RecordType.PROJECT, lead_email="bob@acme.com", **BASE), store, logging.getLogger("t"),
        )
        assert _edges(store) == {("users/u-bob", "LEAD_BY")}

    async def test_comment_pull_request_and_deal(self) -> None:
        store = _store()
        await link_record_people(
            CommentRecord(record_type=RecordType.COMMENT, author_source_id="src-ann", **BASE), store, logging.getLogger("t"),
        )
        assert _edges(store) == {("users/u-ann", "AUTHORED_BY")}

        store = _store()
        await link_record_people(
            PullRequestRecord(
                record_type=RecordType.PULL_REQUEST, creator_email="ann@acme.com",
                assignee_email=["bob@acme.com"], review_email=["cat@acme.com"], **BASE,
            ),
            store, logging.getLogger("t"),
        )
        assert _edges(store) == {
            ("users/u-ann", "CREATED_BY"), ("users/u-bob", "ASSIGNED_TO"), ("users/u-cat", "REVIEWED_BY"),
        }

        store = _store()
        await link_record_people(DealRecord(record_type=RecordType.DEAL, owner_id="src-dan", **BASE), store, logging.getLogger("t"))
        assert _edges(store) == {("users/u-dan", "OWNED_BY")}


class TestWriteBehaviour:
    async def test_existing_edges_are_cleared_first(self) -> None:
        store = _store()
        order: list[str] = []
        store.delete_edges_from.side_effect = lambda *a: order.append("delete")
        store.batch_create_entity_relations.side_effect = lambda edges: order.append("create")
        await link_record_people(_mail(from_email="ann@acme.com"), store, logging.getLogger("t"))
        assert order == ["delete", "create"]
        store.delete_edges_from.assert_awaited_once_with("rec-1", "records", "entityRelations")

    async def test_other_record_types_are_left_alone(self) -> None:
        store = _store()
        file_record = FileRecord(record_type=RecordType.FILE, is_file=True, extension="txt", **BASE)
        assert await link_record_people(file_record, store, logging.getLogger("t")) == 0
        store.delete_edges_from.assert_not_awaited()

    async def test_a_failed_lookup_skips_that_person_only_and_logs_no_address(self, caplog) -> None:
        store = _store()
        store.get_user_by_email.side_effect = lambda email: (
            (_ for _ in ()).throw(RuntimeError("db")) if email == "bob@acme.com" else SimpleNamespace(id=MEMBERS[email])
        )
        with caplog.at_level(logging.WARNING, logger="t"):
            await link_record_people(
                _mail(from_email="ann@acme.com", to_emails=["bob@acme.com"]), store, logging.getLogger("t"),
            )
        assert _edges(store) == {("users/u-ann", "AUTHORED_BY")}
        assert "bob" not in caplog.text

    async def test_the_number_of_edges_per_record_is_capped(self, monkeypatch) -> None:
        monkeypatch.setattr(record_people, "MAX_LINKED_PEOPLE", 2)
        store = _store()
        await link_record_people(
            _mail(from_email="ann@acme.com", to_emails=["bob@acme.com", "cat@acme.com"]), store, logging.getLogger("t"),
        )
        assert len(_written(store)) == 2


class TestReviewFixes:
    async def test_a_display_name_with_a_comma_keeps_the_address(self) -> None:
        """Gmail splits the header on every comma: '"Doe, John" <j@x.com>'
        reaches us as two fragments."""
        store = _store()
        mail = _mail(from_email="ann@acme.com", to_emails=['"Doe', 'John" <bob@acme.com>', "cat@acme.com, ann@acme.com"])
        await link_record_people(mail, store, logging.getLogger("t"))
        assert ("users/u-bob", "ADDRESSED_TO") in _edges(store)
        assert ("users/u-cat", "ADDRESSED_TO") in _edges(store)

    async def test_a_user_of_another_org_is_never_linked(self) -> None:
        store = _store()
        store.get_user_by_email.side_effect = lambda email: SimpleNamespace(id="u-x", org_id="org-2")
        await link_record_people(_mail(from_email="ann@acme.com"), store, logging.getLogger("t"))
        store.batch_create_entity_relations.assert_not_awaited()

    async def test_lookups_are_capped_not_only_edges(self, monkeypatch) -> None:
        monkeypatch.setattr(record_people, "MAX_PERSON_LOOKUPS", 3)
        store = _store()
        mail = _mail(from_email="ann@acme.com", to_emails=[f"x{i}@else.com" for i in range(50)])
        await link_record_people(mail, store, logging.getLogger("t"))
        assert store.get_user_by_email.await_count == 3

    async def test_a_rejected_new_edge_type_does_not_fail_the_record(self, caplog) -> None:
        """During a rolling deploy an old pod can restore an edge-type enum
        without the new types; those edges are derived data and must not
        abort the record's sync. Long-standing types still raise."""
        store = _store()

        async def _create(edges: list) -> None:
            if any(e["edgeType"] == "ADDRESSED_TO" for e in edges):
                raise RuntimeError("Document does not match the entity relations schema")

        store.batch_create_entity_relations = AsyncMock(side_effect=_create)
        with caplog.at_level(logging.WARNING, logger="t"):
            await link_record_people(
                TicketRecord(record_type=RecordType.TICKET, assignee_email="ann@acme.com", **BASE),
                store, logging.getLogger("t"),
            )
            await link_record_people(_mail(from_email="ann@acme.com", to_emails=["bob@acme.com"]), store, logging.getLogger("t"))
        assert "rec-1" in caplog.text

    async def test_long_standing_edge_types_still_raise(self) -> None:
        import pytest

        store = _store()
        store.batch_create_entity_relations = AsyncMock(side_effect=RuntimeError("db down"))
        with pytest.raises(RuntimeError):
            await link_record_people(
                TicketRecord(record_type=RecordType.TICKET, assignee_email="ann@acme.com", **BASE),
                store, logging.getLogger("t"),
            )


def test_hidden_email_ticket_fields_survive_a_graph_round_trip() -> None:
    ticket = TicketRecord(
        record_type=RecordType.TICKET, is_email_hidden=True, assignee_source_id=["src-ann"],
        reporter_source_id="src-dan", **BASE,
    )
    record_doc = {**ticket.to_arango_base_record(), "_key": ticket.id}
    again = TicketRecord.from_arango_record(ticket.to_arango_record(), record_doc)
    assert (again.is_email_hidden, again.assignee_source_id, again.reporter_source_id) == (True, ["src-ann"], "src-dan")
