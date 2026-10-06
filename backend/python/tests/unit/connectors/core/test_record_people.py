"""Record -> member edges (KG-13 slice 1): who a record names, resolved to
existing users only, written idempotently, never bcc."""
from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

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
    Person,
    ProjectRecord,
    PullRequestRecord,
    RecordType,
    SourcePerson,
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
    store.delete_edges_between_collections = AsyncMock()
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
        assert all((e["origin"], e["source"]) == ("INFERRED", "conn-1") for e in _written(store))

    async def test_a_recipient_who_is_not_a_member_gets_no_edge_and_no_node(self) -> None:
        store = _store()
        await link_record_people(_mail(to_emails=["stranger@else.com"]), store, logging.getLogger("t"))
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
    async def test_existing_person_edges_are_cleared_first_and_only_those(self) -> None:
        """Edges to organisations belong to record_organizations."""
        store = _store()
        order: list[str] = []
        store.delete_edges_between_collections.side_effect = lambda *a: order.append("delete")
        store.batch_create_entity_relations.side_effect = lambda edges: order.append("create")
        await link_record_people(_mail(from_email="ann@acme.com"), store, logging.getLogger("t"))
        assert order == ["delete", "delete", "create"]
        assert [c.args for c in store.delete_edges_between_collections.await_args_list] == [
            ("rec-1", "records", "entityRelations", "users"),
            ("rec-1", "records", "entityRelations", "person"),
        ]

    async def test_a_new_record_that_names_nobody_is_left_alone(self) -> None:
        store = _store()
        file_record = FileRecord(record_type=RecordType.FILE, is_file=True, extension="txt", **BASE)
        assert await link_record_people(file_record, store, logging.getLogger("t"), may_have_edges=False) == 0
        store.delete_edges_between_collections.assert_not_awaited()

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


def _file(**kw: object) -> FileRecord:
    return FileRecord(record_type=RecordType.FILE, is_file=True, extension="pdf", **{**BASE, **kw})


def _person_store() -> MagicMock:
    """A store that also creates person nodes for people who are not members."""
    store = _store()
    store.get_person_by_email = AsyncMock(return_value=None)
    store.get_person_by_source_key = AsyncMock(return_value=None)
    store.is_transient_error = MagicMock(return_value=False)
    store.upsert_person_by_email = AsyncMock(side_effect=lambda person, **_: f"p-{person.email}")
    store.upsert_person_by_source_key = AsyncMock(side_effect=lambda person, **_: f"p-{person.source_key}")
    return store


class TestSourcePeople:
    """Records without their own person fields (files, pages) name people
    through SourcePerson: author, creator, last editor, owners."""

    def test_the_roles_come_from_the_record_fields(self) -> None:
        f = _file(
            authored_by=SourcePerson(email="ann@acme.com"),
            last_modified_by=SourcePerson(source_id="src-dan"),
            owners=[SourcePerson(email="bob@acme.com")],
        )
        assert [(link.edge_type, link.email or link.source_id) for link in person_links(f)] == [
            (EntityRelations.AUTHORED_BY, "ann@acme.com"),
            (EntityRelations.LAST_MODIFIED_BY, "src-dan"),
            (EntityRelations.OWNED_BY, "bob@acme.com"),
        ]

    def test_an_uploader_is_a_creator_not_an_author(self) -> None:
        f = _file(created_by=SourcePerson(email="ann@acme.com"))
        assert [link.edge_type for link in person_links(f)] == [EntityRelations.CREATED_BY]

    def test_service_accounts_and_unidentified_people_are_skipped(self) -> None:
        f = _file(
            authored_by=SourcePerson(email="bot@acme.com", is_service_account=True),
            last_modified_by=SourcePerson(display_name="Someone"),
        )
        assert person_links(f) == []

    def test_typed_records_keep_their_links_and_add_the_source_people(self) -> None:
        mail = _mail(from_email="ann@acme.com", last_modified_by=SourcePerson(email="bob@acme.com"))
        assert {link.edge_type for link in person_links(mail)} == {
            EntityRelations.AUTHORED_BY, EntityRelations.LAST_MODIFIED_BY,
        }

    async def test_members_are_linked_by_email_or_source_id(self) -> None:
        store = _person_store()
        f = _file(authored_by=SourcePerson(email="ann@acme.com"), last_modified_by=SourcePerson(source_id="src-dan"))
        await link_record_people(f, store, logging.getLogger("t"))
        assert _edges(store) == {("users/u-ann", "AUTHORED_BY"), ("users/u-dan", "LAST_MODIFIED_BY")}
        store.upsert_person_by_email.assert_not_awaited()

    async def test_a_non_member_author_gets_a_person_node(self) -> None:
        store = _person_store()
        f = _file(
            authored_by=SourcePerson(email="Eve@Partner.com", display_name="Eve"),
            last_modified_by=SourcePerson(source_id="acc-9", display_name="Finn"),
        )
        await link_record_people(f, store, logging.getLogger("t"))
        assert _edges(store) == {
            ("person/p-eve@partner.com", "AUTHORED_BY"),
            ("person/p-conn-1:acc-9", "LAST_MODIFIED_BY"),
        }
        (by_email,) = [c.args[0] for c in store.upsert_person_by_email.await_args_list]
        assert (by_email.email, by_email.org_id, by_email.full_name) == ("eve@partner.com", "org-1", "Eve")
        (by_source,) = [c.args[0] for c in store.upsert_person_by_source_key.await_args_list]
        assert (by_source.email, by_source.source_key, by_source.full_name) == (None, "conn-1:acc-9", "Finn")

    async def test_a_mail_recipient_who_is_not_a_member_gets_no_node(self) -> None:
        store = _person_store()
        await link_record_people(_mail(from_email="eve@partner.com", to_emails=["stranger@else.com"]),
                                 store, logging.getLogger("t"))
        assert _edges(store) == {("person/p-eve@partner.com", "AUTHORED_BY")}
        assert [c.args[0].email for c in store.upsert_person_by_email.await_args_list] == ["eve@partner.com"]

    async def test_a_failed_person_write_skips_only_that_person(self) -> None:
        store = _person_store()
        store.upsert_person_by_source_key = AsyncMock(side_effect=RuntimeError("schema"))
        f = _file(authored_by=SourcePerson(email="ann@acme.com"), last_modified_by=SourcePerson(source_id="acc-9"))
        await link_record_people(f, store, logging.getLogger("t"))
        assert _edges(store) == {("users/u-ann", "AUTHORED_BY")}

    async def test_both_member_and_person_edges_are_cleared_first(self) -> None:
        store = _person_store()
        await link_record_people(_file(authored_by=SourcePerson(email="ann@acme.com")), store, logging.getLogger("t"))
        cleared = {c.args[3] for c in store.delete_edges_between_collections.await_args_list}
        assert cleared == {"users", "person"}

    async def test_a_record_that_names_nobody_costs_nothing(self) -> None:
        store = _person_store()
        assert await link_record_people(_file(), store, logging.getLogger("t"), may_have_edges=False) == 0
        store.delete_edges_between_collections.assert_not_awaited()


class TestReviewFixesAuthorship:
    async def test_a_known_person_is_read_not_written(self) -> None:
        """A write takes the node's lock inside the sync transaction; two syncs
        naming the same outsider then collide, so an existing node is only read."""
        store = _person_store()
        store.get_person_by_email = AsyncMock(return_value=SimpleNamespace(id="p-eve"))
        await link_record_people(_file(authored_by=SourcePerson(email="eve@partner.com")), store, logging.getLogger("t"))
        assert _edges(store) == {("person/p-eve", "AUTHORED_BY")}
        store.upsert_person_by_email.assert_not_awaited()

    async def test_a_person_known_by_source_key_is_found_before_email(self) -> None:
        store = _person_store()
        store.get_person_by_source_key = AsyncMock(return_value=SimpleNamespace(id="p-acc"))
        await link_record_people(
            _file(authored_by=SourcePerson(source_id="acc-9", email="eve@partner.com")), store, logging.getLogger("t"),
        )
        assert _edges(store) == {("person/p-acc", "AUTHORED_BY")}
        store.get_person_by_source_key.assert_awaited_once_with("conn-1:acc-9", "org-1")

    async def test_a_new_email_person_also_records_its_source_key(self) -> None:
        store = _person_store()
        await link_record_people(
            _file(authored_by=SourcePerson(source_id="acc-9", email="eve@partner.com")), store, logging.getLogger("t"),
        )
        (person,) = [c.args[0] for c in store.upsert_person_by_email.await_args_list]
        assert (person.email, person.source_key) == ("eve@partner.com", "conn-1:acc-9")

    async def test_a_write_conflict_fails_the_record_so_its_transaction_retries(self) -> None:
        store = _person_store()
        conflict = RuntimeError("[1200] timeout waiting to lock key")
        store.upsert_person_by_email = AsyncMock(side_effect=conflict)
        store.is_transient_error = MagicMock(side_effect=lambda exc: exc is conflict)
        with pytest.raises(RuntimeError, match="1200"):
            await link_record_people(_file(authored_by=SourcePerson(email="eve@partner.com")), store, logging.getLogger("t"))

    async def test_an_updated_record_that_names_nobody_loses_its_old_edges(self) -> None:
        store = _person_store()
        await link_record_people(_file(), store, logging.getLogger("t"), may_have_edges=True)
        assert {c.args[3] for c in store.delete_edges_between_collections.await_args_list} == {"users", "person"}

    def test_an_unset_key_is_left_out_of_the_stored_person(self) -> None:
        """An older pod can restore the old strict schema (email required, no
        sourceKey) mid-rollout; an email person must still validate against it."""
        assert "sourceKey" not in Person(email="eve@partner.com", org_id="o").to_arango_person()
        assert "email" not in Person(source_key="c:a", org_id="o").to_arango_person()

    async def test_another_orgs_user_with_the_address_is_not_made_a_person_here(self) -> None:
        """Email lookups are not org-scoped: if they answer with another org's
        user, this org's member must not be recorded as an outside person."""
        store = _person_store()
        store.get_user_by_email = AsyncMock(return_value=SimpleNamespace(id="u-x", org_id="org-2"))
        await link_record_people(_file(authored_by=SourcePerson(email="ann@acme.com")), store, logging.getLogger("t"))
        store.batch_create_entity_relations.assert_not_awaited()
        store.upsert_person_by_email.assert_not_awaited()
