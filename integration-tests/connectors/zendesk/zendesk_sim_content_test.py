# pyright: ignore-file

"""
Zendesk on the simulator: content, reindex, filters and the connector's life
============================================================================

  TC-ZDSIM-CNT-001  The bytes of every ticket file and article file equal the source's; no bearer token reached the CDN;
                    an internal note is never in a ticket's stream [J, N]
  TC-ZDSIM-CNT-002  Odd file names, an empty file and an embedded image: the records and bytes are right, the embedded
                    image is not a record [J]
  TC-ZDSIM-CNT-003  A file made internal (its comment turned private) is no longer served [J, E]
  TC-ZDSIM-CNT-004  A ticket gone at the source answers an error when streamed, and never another ticket's content [J]
  TC-ZDSIM-CNT-005  Markup and a path in a ticket's subject, a file's name and an article's title are data, not code:
                    nothing is executed and the names are kept as they are [N]
  TC-ZDSIM-CNT-006  Reindex of a record, after a change of the ticket, after the access changed [K]
  TC-ZDSIM-CNT-007  Reindex of a record whose ticket is gone at the source [K]
  TC-ZDSIM-CNT-008  Another person never reads by a guessed record id (the permission query for a record they may not read
                    is empty) [N]
  TC-ZDSIM-FLT-001  A group filter "in", then "not in", then none (set while the connector is off): each time the records
                    equal the account within the filter [G]
  TC-ZDSIM-FLT-002  The Unassigned choice of the group filter keeps the tickets with no group [G]
  TC-ZDSIM-FLT-003  A created-date filter keeps out the tickets and articles created before it [G]
  TC-ZDSIM-FLT-004  A modified-date filter keeps out what was last changed before it [G]
  TC-ZDSIM-FLT-005  Indexing filters switched off keep the records and stop their indexing [G]
  TC-ZDSIM-FLT-006  A filter changed later: what the new filter leaves out is removed, what it brings in is read [G]
  TC-ZDSIM-LIFE-001 Disable and enable: the records stay, nothing is duplicated [L]
  TC-ZDSIM-LIFE-002 Deleting the connector leaves no record, group or permission of it [L]
  TC-ZDSIM-LIFE-003 Two connectors on one account hold the same records, each its own [L]
  TC-ZDSIM-LIFE-004 A sync asked for while one runs does not start a second one or lose a change [L]
  TC-ZDSIM-LIFE-005 A manual sync after the connector has been idle picks up every change [L]
"""

import asyncio
from typing import Any

import pytest
import requests
from connectors.zendesk.sim_instances import desk, it_seed, small
from connectors.zendesk.sim_world import (
    ZendeskWorld,
    date_filter,
    group_filter,
    succeeded,
    wanted_records,
)
from pipeshub_client import PipeshubClientError  # type: ignore[import-not-found]



async def content_problems(w: ZendeskWorld) -> list[dict[str, Any]]:
    """What a download returns: a file's bytes as the simulator holds them, never an internal note."""
    problems: list[dict[str, Any]] = []
    expected = w.sim.expected()
    for ext, want in wanted_records(expected, w.scope).items():
        if want.kind not in ("ticket-file", "article-file", "ticket"):
            continue
        try:
            data = await w.stream(ext)
        except Exception as exc:  # noqa: BLE001 - a failed download is a finding
            problems.append({"type": "DOWNLOAD", "record": ext, "error": repr(exc)[:200]})
            continue
        if want.kind == "ticket":
            if b"SECRET-INTERNAL-NOTE" in data:
                problems.append({"type": "PRIVATE", "record": ext})
            continue
        attachment = ext.rsplit("_attachment_", 1)[1]
        kind = "ticket" if want.kind == "ticket-file" else "article"
        truth = w.sim.http.get(f"{w.sim.base_url}/_sim/zendesk/content/{kind}/{attachment}", timeout=30)
        if data != truth.content:
            problems.append({"type": "BYTES", "record": ext, "got": len(data), "want": len(truth.content)})
    return problems


@pytest.mark.integration
@pytest.mark.zendesk_sim
@pytest.mark.asyncio(loop_scope="session")
class TestZendeskSimContent:

    async def test_tc_zdsim_cnt_001_every_download_equals_the_source(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk()))
        assert not await content_problems(w)
        stats = w.sim.stats()
        assert stats["downloads"] > 0 and not stats["cdn_auth_leaks"], stats

    @pytest.mark.parametrize("name,content,kind,inline", [
        ('report "Q1" #2 <é>.txt', "ODD-ONE", "text/plain", False),
        pytest.param("空 file.txt", "ODD-TWO", "text/plain", False, marks=pytest.mark.xfail(strict=True, reason=(
            "a file name with a space and non-ASCII letters: the simulator writes content_url with the name as typed "
            "(?name=空 file.txt) and the download answers 502; whether Zendesk percent-encodes it is not documented"))),
        ("empty.txt", "", "text/plain", False),
        ("pic.png", "PNG-BYTES", "image/png", True)], ids=["quotes", "unicode", "empty", "embedded-image"])
    async def test_tc_zdsim_cnt_002_odd_names_empty_file_embedded_image(
            self, zendesk_world: ZendeskWorld, name: str, content: str, kind: str, inline: bool) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk()))
        w.ops({"op": "add_attachment", "key": "odd", "name": name, "content": content, "type": kind, "inline": inline},
              {"op": "add_comment", "ticket": "t_billing", "author": "agent_billing", "body": "files", "public": True,
               "attachments": ["odd"]})
        assert succeeded(await w.sync())
        await w.assert_matches_source(f"a file named {name!r}")
        assert not await content_problems(w)

    async def test_tc_zdsim_cnt_003_file_made_internal(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk()))
        w.ops({"op": "update_comment", "comment": "c_public", "public": False})
        assert succeeded(await w.sync())
        await w.assert_matches_source("a comment with a file made internal")

    async def test_tc_zdsim_cnt_004_gone_ticket_streams_an_error(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk()))
        tid = str(w.key("tickets", "t_billing"))
        w.ops({"op": "delete_ticket", "ticket": "t_billing"})
        with pytest.raises((PipeshubClientError, requests.RequestException)):
            await w.stream(tid)

    async def test_tc_zdsim_cnt_005_markup_in_names_is_data(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk()))
        subject = '<img src=x onerror=alert(1)> ../../etc/passwd'
        w.ops({"op": "add_ticket", "key": "t_x", "subject": subject, "requester": "cust_none", "group": "support"},
              {"op": "add_article", "key": "a_x", "title": "<script>alert(2)</script>", "section": "sec"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("markup in names")
        names = {r["recordName"] for r in await w.records()}
        assert subject in names and "<script>alert(2)</script>" in names

    async def test_tc_zdsim_cnt_006_reindex(self, zendesk_world: ZendeskWorld, graph_provider) -> None:
        w = zendesk_world
        assert succeeded(await w.start(it_seed()))
        record = await w.record_for("4")
        w.client.reindex_record(record["id"])
        w.ops({"op": "update_ticket", "ticket": "seed", "subject": "Seed ticket (changed)"})
        assert succeeded(await w.sync())
        record = await w.record_for("4")
        assert record["recordName"] == "Seed ticket (changed)"
        w.client.reindex_record(record["id"])
        await w.assert_matches_source("after a reindex")

    async def test_tc_zdsim_cnt_007_reindex_of_a_gone_record(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(it_seed()))
        record = await w.record_for("1")
        w.ops({"op": "delete_ticket", "ticket": 1})
        try:
            w.client.reindex_record(record["id"])
        except Exception:  # noqa: BLE001 - the product may refuse a gone record
            pass
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a reindex of a ticket gone at the source")

    async def test_tc_zdsim_cnt_008_guessed_record_id(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk()))
        secret = await w.record_for(str(w.key("tickets", "t_secret")))
        agent = w.people["agent-billing@corp.example"]
        got = await w.graph.filter_accessible_record_ids([secret["id"]], agent, w.client.org_id)
        assert not got, "an agent of another group read a ticket by its record id"


@pytest.mark.integration
@pytest.mark.zendesk_sim
@pytest.mark.asyncio(loop_scope="session")
class TestZendeskSimFilters:

    async def test_tc_zdsim_flt_001_group_filter_both_ways(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        account = desk()
        w.load(account)
        support, billing = w.key("groups", "support"), w.key("groups", "billing")
        assert succeeded(await w.start(account, filters=group_filter("in", [support])))
        await w.assert_matches_source("group filter in support")
        assert succeeded(await w.restart(group_filter("not_in", [support])))
        await w.assert_matches_source("group filter not in support")
        assert succeeded(await w.restart(group_filter("in", [support, billing])))
        await w.assert_matches_source("group filter in support and billing")

    async def test_tc_zdsim_flt_002_unassigned_choice(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        account = desk()
        assert succeeded(await w.start(account, filters=group_filter("in", ["unassigned_tickets"])))
        await w.assert_matches_source("only Unassigned")
        assert any(r["externalRecordId"] == str(w.key("tickets", "t_unassigned")) for r in await w.records())

    async def test_tc_zdsim_flt_003_created_date_filter(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        account = small()
        old, new = 1_600_000_000, 1_790_000_000
        account["tickets"][0].update({"at": old, "updated": old})
        for t in account["tickets"][1:]:
            t.update({"at": new, "updated": new})
        account["articles"][0].update({"at": old, "updated": old})
        w.load(account)
        cutoff = 1_700_000_000_000
        assert succeeded(await w.start(account, filters=date_filter("created", cutoff, None)))
        w.scope.left_out = {str(w.key("tickets", "t1")), f"article_{w.key('articles', 'a1')}"}
        await w.assert_matches_source("created-date filter")
        held = {r["externalRecordId"] for r in await w.records()}
        assert str(w.key("tickets", "t2")) in held and str(w.key("tickets", "t1")) not in held

    async def test_tc_zdsim_flt_004_modified_date_filter(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        account = small()
        old, new = 1_600_000_000, 1_790_000_000
        account["tickets"][0].update({"at": old, "updated": old})
        for t in account["tickets"][1:]:
            t.update({"at": old, "updated": new})
        w.load(account)
        assert succeeded(await w.start(account, filters=date_filter("modified", 1_700_000_000_000, None)))
        w.scope.left_out = {str(w.key("tickets", "t1"))}
        await w.assert_matches_source("modified-date filter")

    async def test_tc_zdsim_flt_005_indexing_filters(self, zendesk_world: ZendeskWorld, graph_provider) -> None:
        w = zendesk_world
        off = {"operator": "is", "type": "boolean", "value": False}
        filters = {"indexing": {"values": {"tickets": off, "attachments": off, "knowledge_base": off}}}
        assert succeeded(await w.start(desk(), filters=filters))
        await w.assert_matches_source("indexing switched off")
        record = await graph_provider.get_record_by_external_id(w.connector_id, str(w.key("tickets", "t_support")))
        assert str(record.indexing_status).upper().endswith("AUTO_INDEX_OFF"), record.indexing_status

    async def test_tc_zdsim_flt_006_filter_changed_later(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        account = desk()
        w.load(account)
        billing = w.key("groups", "billing")
        assert succeeded(await w.start(account))
        await w.assert_matches_source("no filter")
        assert succeeded(await w.restart(group_filter("in", [billing])))
        await w.assert_matches_source("the filter now keeps billing only")
        assert succeeded(await w.restart({"sync": {"values": {}}}))
        await w.assert_matches_source("the filter removed again")


@pytest.mark.integration
@pytest.mark.zendesk_sim
@pytest.mark.asyncio(loop_scope="session")
class TestZendeskSimLifecycle:

    async def test_tc_zdsim_life_001_disable_and_enable(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk()))
        before = {r["externalRecordId"]: r["id"] for r in await w.records()}
        assert succeeded(await w.restart())
        assert before == {r["externalRecordId"]: r["id"] for r in await w.records()}
        await w.assert_matches_source("after turning the connector off and on")

    async def test_tc_zdsim_life_002_delete_leaves_nothing(self, zendesk_world: ZendeskWorld, graph_provider) -> None:
        w = zendesk_world
        assert succeeded(await w.start(small()))
        w.client.toggle_sync(w.connector_id, enable=False)
        w.client.delete_connector(w.connector_id)
        await graph_provider.assert_all_records_cleaned(w.connector_id, timeout=300)
        w.connector_id = None

    async def test_tc_zdsim_life_003_two_connectors_one_account(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(small()))
        first = w.connector_id
        held_first = {r["externalRecordId"] for r in await w.records()}
        second = w.create_connector()
        await w.sign_in()
        assert succeeded(await w.enable())
        assert {r["externalRecordId"] for r in await w.records()} == held_first
        await w.assert_matches_source("the second connector")
        w.connector_id = first
        assert {r["externalRecordId"] for r in await w.records()} == held_first, "the first connector lost records"
        w.client.toggle_sync(second, enable=False)
        w.client.delete_connector(second)

    async def test_tc_zdsim_life_004_sync_while_one_runs(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk()))
        w.ops({"op": "update_ticket", "ticket": "t_billing", "group": "support"})
        before = w.connector().get("lastSyncFinishedAtTimestamp") or 0
        for _ in range(3):
            try:
                w.client.resync_connector(w.connector_id, full_sync=False, connector_name="Zendesk")
            except Exception:  # noqa: BLE001 - the product may refuse a sync while one runs
                pass
        await w._await_end(before)
        await asyncio.sleep(0)
        assert succeeded(await w.sync())
        await w.assert_matches_source("after three sync requests in a row")

    async def test_tc_zdsim_life_005_manual_sync_after_idle(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(small()))
        w.ops({"op": "add_ticket", "key": "t_new", "subject": "While idle", "requester": "alice", "group": "support"},
              {"op": "add_article", "key": "a_new", "title": "While idle", "section": "sec"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("a manual sync after the connector was idle")
