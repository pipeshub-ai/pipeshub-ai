"""Records joined across connectors by the web address one of them pastes.

A Slack message that pastes a pull request's, ticket's or page's address is
joined to that record, whichever connector holds it, and never to a placeholder.
When the record is indexed after the message, the join is made from its side.

Built only from the processor and the models, so it runs against the code
before this change as well.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    MimeTypes,
    OriginTypes,
    RecordRelations,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.models.entities import (
    LinkPublicStatus,
    LinkRecord,
    MessageRecord,
    PullRequestRecord,
    RecordType,
    RelatedExternalRecord,
)

PR = "https://github.com/acme/api/pull/3081"
JIRA = "https://acme.atlassian.net/browse/PA-1718"


def _by_address(*urls: str) -> list[RelatedExternalRecord]:
    return [
        RelatedExternalRecord(external_record_id=url, record_type=RecordType.WEBPAGE, weburl=url)
        for url in urls
    ]


# ---------------------------------------------------------------------------
# Joining on save
# ---------------------------------------------------------------------------


def _processor() -> DataSourceEntitiesProcessor:
    proc = DataSourceEntitiesProcessor(MagicMock(), MagicMock(), AsyncMock())
    proc.org_id = "org-1"
    proc.messaging_producer = AsyncMock()
    return proc


def _tx_store(at_address: list[dict] | None = None, holders: list[str] | None = None) -> AsyncMock:
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=None)
    tx.get_record_group_by_external_id = AsyncMock(return_value=None)
    tx.delete_edges_by_relationship_types = AsyncMock(return_value=0)
    tx.batch_upsert_node_relations = AsyncMock()
    tx.batch_upsert_records = AsyncMock()
    tx.get_records_by_weburls = AsyncMock(return_value=at_address or [])
    tx.get_records_linking_to_weburls = AsyncMock(return_value=holders or [])
    tx.get_org_apps = AsyncMock(return_value=[{"type": "Slack Workspace"}])
    tx.get_issue_sites = AsyncMock(return_value=[])
    return tx


def _message(related: list[RelatedExternalRecord] | None = None, msg_id: str = "msg-1") -> MessageRecord:
    rec = MessageRecord(
        org_id="org-1",
        record_name="eng_2026-09-26",
        record_type=RecordType.MESSAGE,
        external_record_id="burst_C1_abc",
        version=1,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.SLACK_WORKSPACE,
        connector_id="slack-conn",
        mime_type=MimeTypes.BLOCKS.value,
        weburl="https://acme.slack.com/archives/C1/p1",
        related_external_records=related or [],
    )
    rec.id = msg_id
    return rec


def _pull_request(pr_id: str = "pr-1") -> PullRequestRecord:
    rec = PullRequestRecord(
        org_id="org-1",
        record_name="Fix the export",
        record_type=RecordType.PULL_REQUEST,
        external_record_id="42/pull/3081",
        version=0,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.GITHUB,
        connector_id="gh-conn",
        mime_type=MimeTypes.BLOCKS.value,
        weburl=PR,
    )
    rec.id = pr_id
    return rec


def _saved_ids(tx: AsyncMock) -> set[str]:
    """Ids of the records written; a placeholder would show up here."""
    return {r.id for call in tx.batch_upsert_records.await_args_list for r in call.args[0]}


def _edges(tx: AsyncMock) -> list[dict]:
    out = []
    for call in tx.batch_upsert_node_relations.await_args_list:
        out.extend(call.args[0])
    return out


class TestMessageJoinedToLinkedRecords:
    @pytest.mark.asyncio
    async def test_slack_message_joins_the_real_jira_issue_and_pull_request(self) -> None:
        proc = _processor()
        tx = _tx_store(at_address=[
            {"id": "jira-ticket", "webUrl": JIRA, "recordType": "TICKET", "isDependentNode": False},
            {"id": "pr-1", "webUrl": PR, "recordType": "PULL_REQUEST", "isDependentNode": False},
        ])
        message = _message(_by_address(JIRA + "?focusedCommentId=1", PR + "/files"))

        await proc._process_record(message, [], tx)

        edges = _edges(tx)
        assert {(e["_from"], e["_to"]) for e in edges} == {
            (f"{CollectionNames.RECORDS.value}/msg-1", f"{CollectionNames.RECORDS.value}/jira-ticket"),
            (f"{CollectionNames.RECORDS.value}/msg-1", f"{CollectionNames.RECORDS.value}/pr-1"),
        }
        assert all(e["relationshipType"] == RecordRelations.LINKED_TO.value for e in edges)
        assert all(e["matchedBy"] == "url" for e in edges)
        # Looked up across connectors by address, never by key in Slack's own connector.
        looked_up = tx.get_records_by_weburls.await_args.args[0]
        assert JIRA in looked_up and PR in looked_up
        assert _saved_ids(tx) <= {"msg-1"}

    @pytest.mark.asyncio
    async def test_address_not_indexed_yet_makes_no_placeholder(self) -> None:
        proc = _processor()
        tx = _tx_store(at_address=[])
        message = _message(_by_address(JIRA))

        await proc._process_record(message, [], tx)

        assert _edges(tx) == []
        assert _saved_ids(tx) <= {"msg-1"}

    @pytest.mark.asyncio
    async def test_ticket_is_preferred_over_an_attachment_sharing_its_address(self) -> None:
        proc = _processor()
        tx = _tx_store(at_address=[
            {"id": "attachment", "webUrl": JIRA, "isDependentNode": True},
            {"id": "jira-ticket", "webUrl": JIRA, "isDependentNode": False},
        ])
        message = _message(_by_address(JIRA))

        await proc._process_record(message, [], tx)

        assert [e["_to"] for e in _edges(tx)] == [f"{CollectionNames.RECORDS.value}/jira-ticket"]

    @pytest.mark.asyncio
    async def test_message_without_links_drops_the_ones_an_edit_removed(self) -> None:
        proc = _processor()
        tx = _tx_store()

        await proc._process_record(_message([]), [], tx)

        tx.delete_edges_by_relationship_types.assert_awaited()
        kwargs = tx.delete_edges_by_relationship_types.await_args.kwargs
        assert kwargs["from_id"] == "msg-1"
        assert RecordRelations.LINKED_TO.value in kwargs["relationship_types"]

    @pytest.mark.asyncio
    async def test_key_named_related_records_still_use_their_own_connector(self) -> None:
        """Issue links inside one connector keep working as before."""
        proc = _processor()
        tx = _tx_store()
        found = _pull_request("in-connector")
        tx.get_record_by_external_id = AsyncMock(return_value=found)
        message = _message([RelatedExternalRecord(external_record_id="X-1", record_type=RecordType.TICKET)])

        await proc._handle_related_external_records(message, message.related_external_records, tx)

        tx.get_record_by_external_id.assert_awaited_with(connector_id="slack-conn", external_id="X-1")
        assert [e["_to"] for e in _edges(tx)] == [f"{CollectionNames.RECORDS.value}/in-connector"]
        assert "matchedBy" not in _edges(tx)[0]


class TestJoinedWhenTheLinkedRecordArrivesLater:
    @pytest.mark.asyncio
    async def test_pull_request_saved_after_the_message_gets_the_edge(self) -> None:
        proc = _processor()
        tx = _tx_store(holders=["msg-1", "msg-2"])

        await proc._process_record(_pull_request(), [], tx)

        args = tx.get_records_linking_to_weburls.await_args.args
        assert PR in args[0] and PR + "/files" in args[0]
        assert args[1] == "org-1"
        assert Connectors.SLACK_WORKSPACE.value in args[2]
        edges = _edges(tx)
        assert {(e["_from"], e["_to"]) for e in edges} == {
            (f"{CollectionNames.RECORDS.value}/msg-1", f"{CollectionNames.RECORDS.value}/pr-1"),
            (f"{CollectionNames.RECORDS.value}/msg-2", f"{CollectionNames.RECORDS.value}/pr-1"),
        }
        assert all(e["matchedBy"] == "url" for e in edges)

    @pytest.mark.asyncio
    async def test_link_records_and_records_without_an_address_are_not_targets(self) -> None:
        proc = _processor()
        tx = _tx_store(holders=["msg-1"])
        link = LinkRecord(
            org_id="org-1",
            record_name="PR",
            record_type=RecordType.LINK,
            external_record_id="md5",
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.SLACK_WORKSPACE,
            connector_id="slack-conn",
            mime_type=MimeTypes.MARKDOWN.value,
            weburl=PR,
            url=PR,
            is_public=LinkPublicStatus.UNKNOWN,
        )
        await proc._join_records_linking_here(link, tx)
        no_address = _pull_request()
        no_address.weburl = None
        await proc._join_records_linking_here(no_address, tx)

        tx.get_records_linking_to_weburls.assert_not_awaited()


class TestFailedLookupsLoseNothing:
    @pytest.mark.asyncio
    async def test_failed_address_lookup_keeps_the_existing_edges(self) -> None:
        proc = _processor()
        tx = _tx_store()
        tx.get_records_by_weburls = AsyncMock(side_effect=RuntimeError("graph down"))
        message = _message(_by_address(JIRA))

        # The failure still fails the save, so the connector retries it.
        with pytest.raises(RuntimeError, match="graph down"):
            await proc._process_record(message, [], tx)

        tx.delete_edges_by_relationship_types.assert_not_awaited()
        assert _edges(tx) == []

    @pytest.mark.asyncio
    async def test_failed_backfill_lookup_fails_the_save_so_it_runs_again(self) -> None:
        """The backfill runs only while an address is new, so a read that failed
        must not let the save commit: the next attempt still sees a new address."""
        proc = _processor()
        tx = _tx_store()
        tx.get_records_linking_to_weburls = AsyncMock(side_effect=RuntimeError("graph down"))

        with pytest.raises(RuntimeError, match="graph down"):
            await proc._process_record(_pull_request(), [], tx)


class TestBackfillReadsEveryHolder:
    @pytest.mark.asyncio
    async def test_holders_past_the_first_page_are_joined(self) -> None:
        proc = _processor()
        proc.LINK_HOLDER_PAGE = 2
        tx = _tx_store()
        pages = {None: ["msg-1", "msg-2"], "msg-2": ["msg-3", "msg-4"], "msg-4": ["msg-5"]}
        tx.get_records_linking_to_weburls = AsyncMock(
            side_effect=lambda urls, org, names, limit, after: pages[after]
        )

        await proc._join_records_linking_here(_pull_request(), tx)

        assert sorted(e["_from"] for e in _edges(tx)) == [
            f"{CollectionNames.RECORDS.value}/msg-{n}" for n in range(1, 6)
        ]
        assert tx.get_records_linking_to_weburls.await_count == 3


class TestBackfillRunsOnEverySave:
    @pytest.mark.asyncio
    async def test_a_failed_read_is_repaired_by_the_next_save_at_the_same_address(self) -> None:
        """On Neo4j's auto-commit sessions the record row is stored before the
        backfill read runs, so the next save sees the same address: it must read
        again, not skip."""
        proc = _processor()
        tx = _tx_store()
        tx.get_records_linking_to_weburls = AsyncMock(side_effect=[RuntimeError("graph down"), ["msg-1"]])
        with pytest.raises(RuntimeError):
            await proc._process_record(_pull_request(), [], tx)

        tx.get_record_by_external_id = AsyncMock(return_value=_pull_request())
        await proc._process_record(_pull_request(), [], tx)

        assert [(e["_from"], e["_to"]) for e in _edges(tx)] == [
            (f"{CollectionNames.RECORDS.value}/msg-1", f"{CollectionNames.RECORDS.value}/pr-1"),
        ]


class TestBackfillJoinsOnlyThePickedRecord:
    @pytest.mark.asyncio
    async def test_an_attachment_at_the_tickets_address_is_not_joined(self) -> None:
        proc = _processor()
        tx = _tx_store(holders=["msg-1"], at_address=[
            {"id": "ticket-1", "webUrl": JIRA, "isDependentNode": False},
        ])
        attachment = _pull_request("attachment-1")
        attachment.weburl = JIRA
        attachment.is_dependent_node = True

        await proc._join_records_linking_here(attachment, tx)

        assert _edges(tx) == []

    @pytest.mark.asyncio
    async def test_the_ticket_itself_is_joined(self) -> None:
        proc = _processor()
        tx = _tx_store(holders=["msg-1"], at_address=[
            {"id": "attachment-1", "webUrl": JIRA, "isDependentNode": True},
        ])
        ticket = _pull_request("ticket-1")
        ticket.weburl = JIRA

        await proc._join_records_linking_here(ticket, tx)

        assert [e["_to"] for e in _edges(tx)] == [f"{CollectionNames.RECORDS.value}/ticket-1"]


class TestATicketAndItsAttachmentBothSaved:
    @pytest.mark.asyncio
    async def test_the_only_edge_from_the_message_goes_to_the_ticket(self) -> None:
        """A message pasted the issue's address before either was indexed. The
        ticket and its attachment (stored at the same address) are then both
        saved, in either order: only the ticket is joined."""
        stored = [
            {"id": "ticket-1", "webUrl": JIRA, "isDependentNode": False},
            {"id": "attachment-1", "webUrl": JIRA, "isDependentNode": True},
        ]
        ticket = _pull_request("ticket-1")
        ticket.weburl = JIRA
        attachment = _pull_request("attachment-1")
        attachment.weburl = JIRA
        attachment.is_dependent_node = True

        for order in ((attachment, ticket), (ticket, attachment)):
            proc = _processor()
            tx = _tx_store(holders=["msg-1"], at_address=stored)
            for record in order:
                await proc._join_records_linking_here(record, tx)
            assert [(e["_from"], e["_to"]) for e in _edges(tx)] == [
                (f"{CollectionNames.RECORDS.value}/msg-1", f"{CollectionNames.RECORDS.value}/ticket-1"),
            ]


class TestBackfillOnlyWhereLinksAreKept:
    @pytest.mark.asyncio
    async def test_an_org_without_slack_never_reads_for_holders(self) -> None:
        proc = _processor()
        tx = _tx_store(holders=["msg-1"])
        tx.get_org_apps = AsyncMock(return_value=[{"type": "Jira"}, {"type": "Github"}])

        await proc._join_records_linking_here(_pull_request(), tx)
        await proc._join_records_linking_here(_pull_request("pr-2"), tx)

        tx.get_records_linking_to_weburls.assert_not_awaited()
        assert tx.get_org_apps.await_count == 1

    @pytest.mark.asyncio
    async def test_an_org_with_slack_reads_for_holders(self) -> None:
        proc = _processor()
        tx = _tx_store(holders=["msg-1"])
        tx.get_org_apps = AsyncMock(return_value=[{"type": "Slack"}])

        await proc._join_records_linking_here(_pull_request(), tx)

        tx.get_records_linking_to_weburls.assert_awaited()

    @pytest.mark.asyncio
    async def test_a_failed_app_read_still_reads_for_holders(self) -> None:
        proc = _processor()
        tx = _tx_store(holders=["msg-1"])
        tx.get_org_apps = AsyncMock(side_effect=RuntimeError("graph down"))

        await proc._join_records_linking_here(_pull_request(), tx)

        tx.get_records_linking_to_weburls.assert_awaited()


class TestAReindexKeepsTheMessagesEdges:
    @pytest.mark.asyncio
    async def test_a_message_saved_without_reading_its_links_keeps_its_edges(self) -> None:
        """A Slack reindex rebuilds a message from Slack but leaves its link
        records alone, so it must leave the edges alone too, in step with them."""
        proc = _processor()
        tx = _tx_store()
        message = _message()
        message.related_external_records = None

        await proc._process_record(message, [], tx)

        tx.delete_edges_by_relationship_types.assert_not_awaited()


class TestAnEmptyAppListIsNotTrusted:
    @pytest.mark.asyncio
    async def test_no_apps_at_all_still_reads_and_is_asked_again(self) -> None:
        """get_org_apps reports a query failure as []; the saving connector's own
        app always exists, so an empty list is never read as "no Slack"."""
        proc = _processor()
        tx = _tx_store(holders=["msg-1"])
        tx.get_org_apps = AsyncMock(return_value=[])

        await proc._join_records_linking_here(_pull_request(), tx)
        await proc._join_records_linking_here(_pull_request("pr-2"), tx)

        assert tx.get_records_linking_to_weburls.await_count == 2
        assert tx.get_org_apps.await_count == 2


def _by_key(*keys: str) -> list[RelatedExternalRecord]:
    return [
        RelatedExternalRecord(external_record_id=key, record_type=RecordType.TICKET, issue_key=key)
        for key in keys
    ]


class TestIssueKeysJoinTheIssue:
    @pytest.mark.asyncio
    async def test_pull_request_naming_a_key_joins_the_jira_issue(self) -> None:
        proc = _processor()
        tx = _tx_store(at_address=[{"id": "jira-ticket", "webUrl": JIRA, "isDependentNode": False}])
        tx.get_issue_sites = AsyncMock(return_value=["https://acme.atlassian.net", "https://other.example"])
        pr = _pull_request()
        pr.related_external_records = _by_key("PA-1718", "UTF-8")

        await proc._process_record(pr, [], tx)

        looked_up = tx.get_records_by_weburls.await_args.args[0]
        assert JIRA in looked_up and "https://other.example/browse/PA-1718" in looked_up
        edges = [e for e in _edges(tx) if e["_from"] == f"{CollectionNames.RECORDS.value}/pr-1"]
        assert [(e["_to"], e["matchedBy"]) for e in edges] == [
            (f"{CollectionNames.RECORDS.value}/jira-ticket", "key"),
        ]
        assert _saved_ids(tx) <= {"pr-1"}

    @pytest.mark.asyncio
    async def test_sites_are_read_once_per_org_while_fresh(self) -> None:
        proc = _processor()
        tx = _tx_store()
        tx.get_issue_sites = AsyncMock(return_value=["https://acme.atlassian.net"])
        for n in range(3):
            pr = _pull_request(f"pr-{n}")
            pr.related_external_records = _by_key("PA-1")
            await proc._handle_related_external_records(pr, pr.related_external_records, tx)

        assert tx.get_issue_sites.await_count == 1

    @pytest.mark.asyncio
    async def test_no_jira_site_means_no_lookup(self) -> None:
        proc = _processor()
        tx = _tx_store()
        pr = _pull_request()
        pr.related_external_records = _by_key("PA-1")

        await proc._handle_related_external_records(pr, pr.related_external_records, tx)

        tx.get_records_by_weburls.assert_not_awaited()
        assert _edges(tx) == []

    @pytest.mark.asyncio
    async def test_pull_request_without_mentions_drops_old_ones(self) -> None:
        proc = _processor()
        tx = _tx_store()

        await proc._process_record(_pull_request(), [], tx)

        kwargs = tx.delete_edges_by_relationship_types.await_args.kwargs
        assert kwargs["from_id"] == "pr-1"


class TestIssueSitesCache:
    @pytest.mark.asyncio
    async def test_a_failed_read_is_not_cached_and_keeps_the_edges(self) -> None:
        proc = _processor()
        tx = _tx_store()
        tx.get_issue_sites = AsyncMock(side_effect=[RuntimeError("graph down"), ["https://acme.atlassian.net"]])
        pr = _pull_request()
        pr.related_external_records = _by_key("PA-1718")

        with pytest.raises(RuntimeError, match="graph down"):
            await proc._handle_related_external_records(pr, pr.related_external_records, tx)
        tx.delete_edges_by_relationship_types.assert_not_awaited()

        await proc._handle_related_external_records(pr, pr.related_external_records, tx)
        assert tx.get_issue_sites.await_count == 2
        tx.get_records_by_weburls.assert_awaited()

    @pytest.mark.asyncio
    async def test_no_site_yet_is_asked_again_after_a_minute(self) -> None:
        proc = _processor()
        tx = _tx_store()
        tx.get_issue_sites = AsyncMock(side_effect=[[], ["https://acme.atlassian.net"]])
        clock = iter([0, 30_000, 61_000, 62_000])
        pr = _pull_request()
        pr.related_external_records = _by_key("PA-1718")
        with patch(
            "app.connectors.core.base.data_processor.data_source_entities_processor.get_epoch_timestamp_in_ms",
            side_effect=lambda: next(clock),
        ):
            assert await proc._issue_sites("org-1", tx) == []
            assert await proc._issue_sites("org-1", tx) == []
            assert await proc._issue_sites("org-1", tx) == ["https://acme.atlassian.net"]
        assert tx.get_issue_sites.await_count == 2


class TestAKeyOnTwoSites:
    def _tx(self) -> AsyncMock:
        tx = _tx_store(at_address=[
            {"id": "cloud-issue", "webUrl": "https://acme.atlassian.net/browse/OPS-9", "isDependentNode": False},
            {"id": "server-issue", "webUrl": "https://jira.corp.example/browse/OPS-9", "isDependentNode": False},
        ])
        tx.get_issue_sites = AsyncMock(return_value=["https://acme.atlassian.net", "https://jira.corp.example"])
        return tx

    @pytest.mark.asyncio
    async def test_an_ambiguous_key_joins_nothing(self) -> None:
        proc = _processor()
        tx = self._tx()
        pr = _pull_request()

        await proc._handle_related_external_records(pr, _by_key("OPS-9"), tx)

        assert _edges(tx) == []

    @pytest.mark.asyncio
    async def test_a_pull_request_naming_only_the_key_keeps_its_edge_across_saves(self) -> None:
        """Saved twice, it joins the same issue both times (here none, since two
        sites have OPS-9), whatever order the sites come back in."""
        proc = _processor()
        results = []
        for sites in (["https://acme.atlassian.net", "https://jira.corp.example"],
                      ["https://jira.corp.example", "https://acme.atlassian.net"]):
            tx = self._tx()
            tx.get_issue_sites = AsyncMock(return_value=sites)
            proc.__dict__.pop("_issue_sites_cache", None)
            await proc._handle_related_external_records(_pull_request(), _by_key("OPS-9"), tx)
            results.append([e["_to"] for e in _edges(tx)])
        assert results == [[], []]

    @pytest.mark.asyncio
    async def test_a_ticket_resolves_a_key_on_its_own_site(self) -> None:
        proc = _processor()
        tx = self._tx()
        ticket = _pull_request("ticket-1")
        ticket.weburl = "https://jira.corp.example/browse/OPS-1"

        await proc._handle_related_external_records(ticket, _by_key("OPS-9"), tx)

        assert [e["_to"] for e in _edges(tx)] == [f"{CollectionNames.RECORDS.value}/server-issue"]


class TestATicketNamingItsOwnKey:
    @pytest.mark.asyncio
    async def test_its_own_key_never_joins_another_sites_ticket_or_an_attachment(self) -> None:
        """'OPS-12: fix export' on the server site: the server site has OPS-12
        (this ticket), so the cloud site's OPS-12 is not joined, nor is the
        attachment sharing this ticket's address, nor the ticket itself."""
        proc = _processor()
        own = "https://jira.corp.example/browse/OPS-12"
        tx = _tx_store(at_address=[
            {"id": "ticket-1", "webUrl": own, "isDependentNode": False},
            {"id": "attachment", "webUrl": own, "isDependentNode": True},
            {"id": "cloud-ops-12", "webUrl": "https://acme.atlassian.net/browse/OPS-12", "isDependentNode": False},
        ])
        tx.get_issue_sites = AsyncMock(return_value=["https://acme.atlassian.net", "https://jira.corp.example"])
        ticket = _pull_request("ticket-1")
        ticket.weburl = own

        await proc._handle_related_external_records(ticket, _by_key("OPS-12"), tx)

        assert _edges(tx) == []

    @pytest.mark.asyncio
    async def test_an_attachment_alone_does_not_give_a_site_the_key(self) -> None:
        proc = _processor()
        tx = _tx_store(at_address=[
            {"id": "attachment", "webUrl": "https://jira.corp.example/browse/OPS-9", "isDependentNode": True},
            {"id": "cloud-issue", "webUrl": "https://acme.atlassian.net/browse/OPS-9", "isDependentNode": False},
        ])
        tx.get_issue_sites = AsyncMock(return_value=["https://acme.atlassian.net", "https://jira.corp.example"])

        await proc._handle_related_external_records(_pull_request(), _by_key("OPS-9"), tx)

        assert [e["_to"] for e in _edges(tx)] == [f"{CollectionNames.RECORDS.value}/cloud-issue"]


class TestARecordLinkingItsOwnAddress:
    @pytest.mark.asyncio
    async def test_a_ticket_linking_its_own_page_joins_nothing(self) -> None:
        proc = _processor()
        tx = _tx_store(at_address=[
            {"id": "ticket-1", "webUrl": JIRA, "isDependentNode": False},
            {"id": "attachment", "webUrl": JIRA, "isDependentNode": True},
        ])
        ticket = _pull_request("ticket-1")
        ticket.weburl = JIRA

        await proc._handle_related_external_records(ticket, _by_address(JIRA), tx)

        assert _edges(tx) == []
