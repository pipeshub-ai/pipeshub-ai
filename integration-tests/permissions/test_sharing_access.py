"""Sharing reaches the people it names and nobody else.

CTO list: Sharing, and Access Control List. Knowledge-base content is shared at
the knowledge-base level (the API has no per-file share), so "a record shared
with a person or a team" is a note in a knowledge base shared with them. Each
case is checked as the person it was shared with and as a colleague it was not,
through search, opening the record and a chat answer's citations.

Domain-wide, "anyone" and "anyone with the link" shares are meant to grant no
access in PipesHub; that was decided, not overlooked. Only connectors produce
them, as source permissions handed to ``DataSourceEntitiesProcessor``, so the
test hands it exactly those and asks the colleague what they can reach.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Iterator

import pytest
from app.config.constants.arangodb import CollectionNames
from app.models.permission import EntityType, Permission, PermissionType

from helper import kb_sharing
from helper.access_probe import (
    ask_until_cited,
    assert_no_chat_leak,
    open_status,
    search,
)
from helper.kb_notes import Note, create_kb_with_note, delete_kb
from helper.pipeshub_client import PipeshubClient
from helper.second_user import (
    NO_ACCESS_STATUSES,
    SecondUser,
    create_second_user,
    delete_second_user,
)

logger = logging.getLogger("sharing-access")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.permissions,
    # The people and the knowledge base are shared by every test here.
    pytest.mark.xdist_group("sharing-access"),
]

KB_PREFIX = "it-sharing-access"


@dataclasses.dataclass(frozen=True)
class People:
    reader: SecondUser
    colleague: SecondUser
    # Given a direct record permission by the processor test, as a control.
    control: SecondUser


@pytest.fixture(scope="module")
def people(pipeshub_client: PipeshubClient) -> Iterator[People]:
    pipeshub_client._ensure_access_token()
    made: list[SecondUser] = []
    try:
        for _ in range(3):
            made.append(create_second_user(pipeshub_client))
        yield People(*made)
    finally:
        for user in made:
            delete_second_user(pipeshub_client, user, strict=True)


@pytest.fixture
def note(pipeshub_client: PipeshubClient, ai_models_configured) -> Iterator[Note]:
    """A knowledge base shared with nobody, holding one indexed note."""
    del ai_models_configured  # ordering only: indexing needs an LLM and an embedding model
    made = create_kb_with_note(pipeshub_client, KB_PREFIX, "sharedtallow")
    try:
        yield made
    finally:
        delete_kb(pipeshub_client, made.kb_id)


def assert_reaches(user: SecondUser, note: Note, why: str) -> None:
    outcome = search(user, note.word, note.kb_id)
    assert outcome.found(note.virtual_id), (
        f"{why}: searching for {note.word!r} did not return the note ({outcome.describe()})."
    )
    status = open_status(user, note.record_id)
    assert status == 200, f"{why}: opening the note returned HTTP {status}."


def assert_cannot_reach(user: SecondUser, note: Note, why: str) -> None:
    for kb_id in (note.kb_id, None):
        outcome = search(user, note.word, kb_id)
        assert outcome.refused_or_missing(note.virtual_id), (
            f"{why}: searching for {note.word!r} gave {outcome.describe()}; the note must "
            "not come back, and an error does not prove it stayed hidden."
        )
    status = open_status(user, note.record_id)
    assert status in NO_ACCESS_STATUSES, f"{why}: opening the note returned HTTP {status}."


def assert_chat_cites(user: SecondUser, note: Note, why: str) -> None:
    answer = ask_until_cited(user, note.question, note.record_id, note.virtual_id, [note.kb_id])
    assert answer.cites(note.record_id, note.virtual_id), (
        f"{why}: asked about the note, the answer did not cite it: {answer.describe()}"
    )


def assert_chat_does_not_cite(user: SecondUser, note: Note, why: str) -> None:
    assert_no_chat_leak(user, note.question, note.record_id, note.virtual_id, why)


class TestSharedWithAPerson:
    def test_only_they_reach_it_until_it_is_unshared(
        self, pipeshub_client: PipeshubClient, people: People, note: Note
    ) -> None:
        kb_sharing.grant(pipeshub_client, note.kb_id, user_ids=[people.reader.user_id])

        assert_reaches(people.reader, note, "shared with them")
        assert_chat_cites(people.reader, note, "shared with them")
        assert_cannot_reach(people.colleague, note, "shared with someone else")
        assert_chat_does_not_cite(people.colleague, note, "shared with someone else")

        kb_sharing.revoke(pipeshub_client, note.kb_id, user_ids=[people.reader.user_id])
        assert_cannot_reach(people.reader, note, "after it was unshared")
        assert_chat_does_not_cite(people.reader, note, "after it was unshared")


class TestSharedWithATeam:
    def test_only_its_members_reach_it_until_it_is_unshared(
        self, pipeshub_client: PipeshubClient, people: People, note: Note
    ) -> None:
        team_id = kb_sharing.create_team(pipeshub_client, [people.reader.user_id])
        try:
            kb_sharing.grant(pipeshub_client, note.kb_id, team_ids=[team_id])

            assert_reaches(people.reader, note, "shared with their team")
            assert_chat_cites(people.reader, note, "shared with their team")
            assert_cannot_reach(people.colleague, note, "shared with a team they are not in")
            assert_chat_does_not_cite(people.colleague, note, "shared with a team they are not in")

            kb_sharing.revoke(pipeshub_client, note.kb_id, team_ids=[team_id])
            assert_cannot_reach(people.reader, note, "after it was unshared from their team")
        finally:
            kb_sharing.delete_team(pipeshub_client, team_id, strict=False)


def _source_permission(entity_type: EntityType, **kw: str) -> Permission:
    return Permission(type=PermissionType.READ, entity_type=entity_type, **kw)


class LeakedAccess(AssertionError):
    """Raised only by the final check of the strict-xfail test, so a failed
    setup step there is reported as a failure rather than as the known gap."""


@pytest.mark.asyncio(loop_scope="session")
class TestSharesThatGrantNothing:
    async def test_domain_anyone_and_link_shares_reach_nobody(
        self, graph_provider, config_service, pipeshub_client: PipeshubClient,
        people: People, note: Note,
    ) -> None:
        """What a connector sync writes for a domain, "anyone" or link share: nothing.

        The same call also carries a plain share with one person (the control),
        so an edge count that did not move, or a colleague still refused, cannot
        come from the call having done nothing at all.
        """
        from app.connectors.core.base.data_processor.data_source_entities_processor import (
            DataSourceEntitiesProcessor,
        )
        from app.connectors.core.base.data_store.graph_data_store import GraphDataStore

        record = await graph_provider.get_record_by_id(note.record_id)
        assert record is not None, f"the note's record {note.record_id} is not in the graph"
        record_node = f"{CollectionNames.RECORDS.value}/{note.record_id}"
        before = await graph_provider.get_edges_to_node(record_node, CollectionNames.PERMISSION.value)

        store = GraphDataStore(logger, graph_provider)
        processor = DataSourceEntitiesProcessor(logger, store, config_service)
        processor.org_id = pipeshub_client.org_id
        shares = [
            _source_permission(EntityType.DOMAIN, external_id=people.colleague.email.split("@", 1)[1]),
            _source_permission(EntityType.ANYONE),
            _source_permission(EntityType.ANYONE_WITH_LINK),
            _source_permission(EntityType.USER, email=people.control.email),
        ]
        async with store.transaction() as tx_store:
            await processor._handle_record_permissions(record, shares, tx_store)

        after = await graph_provider.get_edges_to_node(record_node, CollectionNames.PERMISSION.value)
        assert len(after) == len(before) + 1, (
            f"Expected exactly one new permission edge (the control's), got "
            f"{len(after) - len(before)}: domain, anyone and link shares must write none."
        )

        assert open_status(people.control, note.record_id) == 200, (
            "The control, given a plain share in the same call, cannot open the note, so "
            "the call proves nothing about the other shares."
        )
        assert_cannot_reach(people.colleague, note, "after domain, anyone and link shares")
        assert_chat_does_not_cite(people.colleague, note, "after domain, anyone and link shares")

    @pytest.mark.xfail(
        strict=True,
        raises=LeakedAccess,
        reason=(
            "The read path still honours 'anyone' nodes: check_record_access_with_details "
            "on Neo4j and ArangoDB, and the accessible-records query on Neo4j ('Path 8'), "
            "grant everyone in the org a record that has an Anyone node for it. Nothing "
            "writes those nodes today (the processor's ANYONE branch is commented out and "
            "process_file_permissions has no caller), so only data written by an older "
            "version reaches it, but it contradicts the decision that 'anyone' shares "
            "grant no access."
        ),
    )
    async def test_an_anyone_node_left_from_older_data_reaches_nobody(
        self, graph_provider, pipeshub_client: PipeshubClient, people: People, note: Note,
    ) -> None:
        assert_cannot_reach(people.colleague, note, "before the anyone node exists")
        anyone_id = f"anyone_{note.record_id}"
        await graph_provider.batch_upsert_nodes(
            [{
                "id": anyone_id,
                "type": "anyone",
                "file_key": note.record_id,
                "organization": pipeshub_client.org_id,
                "role": "READER",
                "active": True,
            }],
            CollectionNames.ANYONE.value,
        )
        try:
            status = open_status(people.colleague, note.record_id)
            if status == 200:
                raise LeakedAccess(
                    "A colleague with no share opened the note because an 'anyone' node "
                    "exists for it."
                )
            assert status in NO_ACCESS_STATUSES, f"opening the note returned HTTP {status}"
        finally:
            await graph_provider.delete_nodes([anyone_id], CollectionNames.ANYONE.value)
