"""Three rules found by running the connector on a real PipesHub stack against the Zammad simulator.

* A Zammad user who is only a customer is registered as a guest: a synced source user
  with no PipesHub account is otherwise made a member of the organisation and, once
  active, handed every organisation-wide grant (docs/connectors/06-permissions.md).
* A link to a ticket in a group the sync leaves out writes nothing for that ticket
  (a placeholder record named after it).
* After a ticket is read in full, attachment records it no longer holds (a merge moved
  the article to another ticket) are removed; a ticket not read this sync keeps them.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

from zammad_behaviour_fakes import (
    FakeConfigService,
    FakeDirectory,
    FakeStore,
    FakeZammadPeople,
    agent_role,
)

from app.connectors.sources.zammad.connector import ZammadConnector
from app.models.entities import FileRecord, RecordType, TicketRecord
from app.sources.client.zammad.zammad import ZammadResponse
from app.sources.external.zammad.zammad import ZammadDataSource

SUPPORT, BILLING = 1, 2
CUSTOMER_ROLE = 3


class World:
    def __init__(self) -> None:
        self.zammad = FakeZammadPeople({SUPPORT: "Support", BILLING: "Billing"})
        self.zammad.roles[CUSTOMER_ROLE] = agent_role(CUSTOMER_ROLE, name="Customer", permissions=("ticket.customer",))
        self.directory = FakeDirectory()
        self.app_users: list = []
        self.directory.on_new_app_users = AsyncMock(side_effect=lambda users: self.app_users.extend(users))
        with patch("app.connectors.sources.zammad.connector.ZammadApp"):
            self.connector = ZammadConnector(
                logger=logging.getLogger("test.zammad.guests"),
                data_entities_processor=self.directory,
                data_store_provider=FakeStore(),
                config_service=FakeConfigService(),
                connector_id="zm-guests",
                scope="team",
                created_by="admin",
            )
        client = MagicMock()
        client.get_client.return_value = self.zammad
        client.get_base_url.return_value = "https://zammad.test"
        self.connector.external_client = client
        self.connector.base_url = "https://zammad.test"
        self.connector._get_fresh_datasource = AsyncMock(return_value=ZammadDataSource(client))
        self.connector._sync_tickets_for_groups = AsyncMock()
        self.connector._remove_tickets_in_excluded_groups = AsyncMock()
        self.connector._sync_knowledge_bases = AsyncMock()

    def guests(self) -> dict[str, bool]:
        return {u.email: u.is_guest for u in self.app_users}


async def test_a_customer_only_user_is_registered_as_a_guest_and_an_agent_is_not() -> None:
    world = World()
    world.zammad.add_user(1)
    world.zammad.add_user(2, groups=(), roles=(CUSTOMER_ROLE,), access=())

    await world.connector.run_sync()

    assert world.guests() == {"u1@example.com": False, "u2@example.com": True}


async def test_a_user_holding_any_staff_role_is_not_a_guest() -> None:
    world = World()
    world.zammad.roles[9] = agent_role(9, name="KB editors", permissions=("knowledge_base.editor",))
    world.zammad.roles[10] = agent_role(10, name="Admins", permissions=("admin.user",))
    world.zammad.add_user(4, groups=(), roles=(CUSTOMER_ROLE, 9), access=())
    world.zammad.add_user(5, groups=(), roles=(10,), access=())

    await world.connector.run_sync()

    assert world.guests() == {"u4@example.com": False, "u5@example.com": False}


async def test_a_customer_whose_only_staff_role_is_inactive_is_a_guest() -> None:
    world = World()
    world.zammad.roles[9] = {**agent_role(9, name="Old agents"), "active": False}
    world.zammad.add_user(6, groups=(), roles=(CUSTOMER_ROLE, 9), access=())

    await world.connector.run_sync()

    assert world.guests() == {"u6@example.com": True}


def _connector() -> ZammadConnector:
    processor = MagicMock()
    processor.org_id = "org-1"
    with patch("app.connectors.sources.zammad.connector.ZammadApp"):
        connector = ZammadConnector(
            logger=logging.getLogger("test.zammad.links"),
            data_entities_processor=processor,
            data_store_provider=FakeStore(),
            config_service=FakeConfigService(),
            connector_id="zm-links",
            scope="team",
            created_by="admin",
        )
    connector.sync_filters = None
    return connector


def _links_answer(*targets: tuple[int, int | None]) -> ZammadResponse:
    links = [{"link_type": "normal", "link_object": "Ticket", "link_object_value": t} for t, _ in targets]
    tickets = {str(t): {"id": t, "group_id": g} for t, g in targets if g is not None}
    return ZammadResponse(success=True, data={"links": links, "assets": {"Ticket": tickets}}, status_code=200)


async def test_a_link_to_a_ticket_in_a_group_the_sync_leaves_out_is_not_written() -> None:
    connector = _connector()
    connector._synced_group_ids = {str(SUPPORT)}
    datasource = MagicMock()
    datasource.list_links = AsyncMock(return_value=_links_answer((5, SUPPORT), (6, BILLING), (7, None)))
    connector._get_fresh_datasource = AsyncMock(return_value=datasource)

    related = await connector._fetch_ticket_links(1)

    # Ticket 6 is in Billing, which is not synced; ticket 7's group is not in the assets, so it is kept.
    assert sorted(r.external_record_id for r in related) == ["5", "7"]


async def test_links_are_kept_while_the_synced_groups_are_unknown() -> None:
    connector = _connector()
    connector._synced_group_ids = None
    datasource = MagicMock()
    datasource.list_links = AsyncMock(return_value=_links_answer((5, SUPPORT), (6, BILLING)))
    connector._get_fresh_datasource = AsyncMock(return_value=datasource)

    related = await connector._fetch_ticket_links(1)

    assert sorted(r.external_record_id for r in related) == ["5", "6"]


async def test_attachments_a_ticket_no_longer_holds_are_removed_and_others_kept() -> None:
    connector = _connector()
    connector._delete_ticket_records = AsyncMock(return_value=True)
    connector._incomplete = []
    # Ticket 2 was read this sync and holds no attachment now (a merge moved its article to ticket 1);
    # ticket 1 holds its own and the moved one; ticket 3 was not read this sync.
    connector._written_attachments = {"1": {"1_10_100", "1_30_300"}, "2": set()}
    by_ticket = {"1": ["a100", "t1"], "2": ["a300-old", "t2"], "3": ["a500", "t3"]}
    held = {"1": [("a100", "1_10_100")], "2": [("a300-old", "2_30_300")], "3": [("a500", "3_50_500")]}

    await connector._remove_attachments_no_longer_on_their_ticket("Support", by_ticket, held)

    connector._delete_ticket_records.assert_awaited_once()
    assert connector._delete_ticket_records.await_args.args[0] == ["a300-old"]
    assert by_ticket == {"1": ["a100", "t1"], "2": ["t2"], "3": ["a500", "t3"]}
    assert connector._incomplete == []


async def test_an_attachment_that_fails_to_go_marks_the_sync_incomplete_and_stays_listed() -> None:
    connector = _connector()
    connector._delete_ticket_records = AsyncMock(return_value=False)
    connector._incomplete = []
    connector._written_attachments = {"2": set()}
    by_ticket = {"2": ["a300-old", "t2"]}

    await connector._remove_attachments_no_longer_on_their_ticket(
        "Support", by_ticket, {"2": [("a300-old", "2_30_300")]},
    )

    assert by_ticket == {"2": ["a300-old", "t2"]}
    assert connector._incomplete == ["the removal of attachment 2_30_300 in group 'Support'"]


async def test_the_attachments_written_for_a_ticket_are_remembered_for_the_group() -> None:
    connector = _connector()
    ticket = MagicMock(spec=TicketRecord, record_type=RecordType.TICKET, external_record_id="4")
    attachment = MagicMock(spec=FileRecord, record_type=RecordType.FILE, external_record_id="4_40_400")
    connector._ticket_records = AsyncMock(return_value=[(ticket, []), (attachment, [])])
    connector._written_attachments = {}
    from app.connectors.sources.zammad.connector import TicketReadFailures

    batches = [b async for b in connector._ticket_batches(
        [{"id": 4, "updated_at": "2026-01-01T00:00:00Z"}], {}, TicketReadFailures(), "Support", 50,
    )]

    assert len(batches) == 1
    assert connector._written_attachments == {"4": {"4_40_400"}}


def test_an_attachment_type_comes_from_content_type_then_mime_type_then_the_file_name() -> None:
    mime = ZammadConnector._attachment_mime_type
    # By e-mail: Content-Type (its parameters dropped).
    assert mime({"Content-Type": "text/html; charset=UTF-8", "Mime-Type": "text/html"}, "a.html") == "text/html"
    # Through the API or the agent screen, seen on real 6.4.1 and 7.2.2: Mime-Type only.
    assert mime({"Mime-Type": "application/pdf", "Charset": "UTF-8"}, "report") == "application/pdf"
    # Nothing stated: the file name, then the generic type.
    assert mime({}, "notes.txt") == "text/plain"
    assert mime(None, "blob") == "application/octet-stream"


def test_an_attachment_size_is_read_from_zammads_string() -> None:
    size = ZammadConnector._attachment_size
    assert size("24") == 24
    assert size(3000) == 3000
    assert size(None) == 0
    assert size("not a number") == 0
