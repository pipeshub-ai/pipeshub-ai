# pyright: ignore-file

"""Salesforce connector: full lifecycle against the simulator, read-only against a live org.

Every check that involves access asks PipesHub's own permission query (the one search uses,
``filter_accessible_record_ids``) for every user and every record of the connector, and
compares the answer with the simulator's oracle (``GET /_sim/salesforce/expected``). The cases
run in order on one org; each leaves it as the next one expects (DESIGN.md has the table).

  TC-SF-001  records in PipesHub match Salesforce, type by type
  TC-SF-002  every user's access to every record equals Salesforce's
  TC-SF-003  the hierarchy: groups, parents, a file under its record and inheriting from it
  TC-SF-004  an incremental sync picks up an edit and a new case
  TC-SF-005  a new post, an edited post and an edited reply send the deal for indexing again
  TC-SF-006  sharing a deal and removing the share
  TC-SF-007  an account owner change moves access
  TC-SF-008  a deactivated user loses access and regains it when reactivated
  TC-SF-009  a frozen user loses access and regains it when unfrozen
  TC-SF-010  a file taken off a record leaves; a new version replaces the content
  TC-SF-011  a deleted deal leaves with its task and file; restored, it comes back
  TC-SF-012  a deleted case, task, product and account leave with their children
  TC-SF-013  narrowing then clearing the date filter (each a full sync)
  TC-SF-014  a second full sync keeps every file readable
  TC-SF-015  a failed Salesforce read fails the sync and removes nothing; the next one recovers
  TC-SF-016  a record with more than one page of Chatter streams in full
  TC-SF-017  with more than 1,000 records every user still gets access, and revocations apply
"""

import logging
import time
from typing import Any, Awaitable, Callable

import pytest

from connectors.salesforce.constants import SIM_USERS
from connectors.salesforce.scenario import email
from helper.graph_provider import GraphProviderProtocol
from helper.graph_provider_utils import async_poll_until, wait_for_sync_completion
from pipeshub_client import PipeshubClient

logger = logging.getLogger("salesforce-it")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.salesforce,
    pytest.mark.asyncio(loop_scope="session"),
]

RECORD_TYPES = ("DEAL", "CASE", "TASK", "PRODUCT", "FILE")
# Salesforce's getDeleted ignores seconds: a deletion is listed from the next whole minute.
SYNC_ATTEMPTS = 5
SYNC_GAP_SEC = 20


def _ext(record: dict) -> str:
    return record.get("externalRecordId") or record.get("external_record_id") or ""


def _group(record: dict) -> str:
    return record.get("externalGroupId") or record.get("external_record_group_id") or ""


def _parent(record: dict) -> str:
    return record.get("externalParentId") or record.get("parent_external_record_id") or ""


def _id(record: dict) -> str:
    return record.get("id") or record.get("_key") or ""


def _simulated_only(salesforce_source) -> None:
    if not salesforce_source.simulated:
        pytest.skip("changes the org: runs against the simulator only (the live org is read only)")


async def _records(graph_provider: GraphProviderProtocol, connector_id: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for record_type in RECORD_TYPES:
        for record in await graph_provider.fetch_records_by_type(connector_id, record_type):
            if not record.get("isDeleted"):
                out[_ext(record)] = record
    return out


async def _readable(graph_provider, connector_id: str, user_id: str, org_id: str) -> set[str]:
    """External ids of the connector's records PipesHub lets the user read."""
    records = await _records(graph_provider, connector_id)
    by_id = {_id(r): ext for ext, r in records.items()}
    allowed = await graph_provider.filter_accessible_record_ids(list(by_id), user_id, org_id)
    return {by_id[i] for i in allowed if i in by_id}


def _doc(ext: str) -> str:
    """The document of a file copy (``<document>-<record>``)."""
    return ext.split("-", 1)[0]


async def _access_diff(source, graph_provider, connector_id: str, users: dict[str, str], org_id: str,
                       filters: dict | None = None) -> list[str]:
    """Every difference between PipesHub and Salesforce: records in scope missing or out of
    scope still present, and per user records read that should not be, or not read that should.

    A file is one record per record it is attached to, readable by that record's readers. Who
    may read the file is compared per document: a user reads some copy exactly when Salesforce
    lets them read the file. A copy readable by someone who cannot read its record is a leak."""
    sim = source.sim
    problems: list[str] = []
    everything = sim.expected(source.admin_email, filters)
    present = await _records(graph_provider, connector_id)
    for ext, record_type in everything.items():
        if ext not in present:
            problems.append(f"missing {record_type} {ext}")
    readable = {mail: await _readable(graph_provider, connector_id, user_id, org_id) for mail, user_id in users.items()}
    # A full sync unlinks what it no longer sees (no access edge is left); the node may stay.
    reachable = set().union(*readable.values()) if readable else set()
    for ext, record in present.items():
        if ext not in everything and ext in reachable:
            who = sorted(m for m, r in readable.items() if ext in r)
            problems.append(f"out of scope but readable by {who}: {record.get('recordType')} {record.get('recordName')}")
    for mail, user_id in users.items():
        expected = sim.expected(mail, filters)
        have = readable[mail]
        want = {e for e, t in expected.items() if t != "FILE"}
        got = {e for e in have if present.get(e, {}).get("recordType") != "FILE"}
        for ext in sorted(got - want):
            problems.append(f"{mail} reads {present.get(ext, {}).get('recordName', ext)} but may not")
        for ext in sorted(want - got):
            problems.append(f"{mail} cannot read {expected.get(ext)} {ext} but may")
        want_docs = {_doc(e) for e, t in expected.items() if t == "FILE"}
        got_docs = {_doc(e) for e in have if present.get(e, {}).get("recordType") == "FILE"}
        for doc in sorted(got_docs - want_docs):
            problems.append(f"{mail} reads file {doc} but may not")
        for doc in sorted(want_docs - got_docs):
            problems.append(f"{mail} cannot read file {doc} but may")
        for ext in have:
            record = present.get(ext, {})
            parent = _parent(record)
            if record.get("recordType") == "FILE" and parent in present and parent not in have:
                problems.append(f"{mail} reads {record.get('recordName')} on a record they cannot read")
    return problems


async def _sync_until(pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol,
                      connector_id: str, condition: Callable[[], Awaitable[bool]], what: str,
                      *, full: bool = False) -> None:
    """Incremental (or full) syncs, minutes apart as in the product, until ``condition`` holds."""
    for attempt in range(SYNC_ATTEMPTS):
        pipeshub_client.resync_connector(connector_id, full_sync=full, connector_name="Salesforce")
        await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id, timeout=300)
        try:
            await async_poll_until(condition, timeout=20, interval=5, description=what)
            return
        except TimeoutError:
            logger.info("%s not yet seen after sync %d", what, attempt + 1)
            time.sleep(SYNC_GAP_SEC)
    raise AssertionError(f"{what} not seen after {SYNC_ATTEMPTS} syncs")


@pytest.fixture(scope="module")
def org_id(pipeshub_client: PipeshubClient) -> str:
    return pipeshub_client.org_id


class TestSalesforceConnector:

    async def _clean(self, source, graph_provider, connector_id, users, org_id, filters=None) -> Callable:
        async def clean() -> bool:
            problems = await _access_diff(source, graph_provider, connector_id, users, org_id, filters)
            if problems:
                logger.info("still different from Salesforce: %s", problems[:8])
            return not problems
        return clean

    async def _settle(self, source, pipeshub_client, graph_provider, connector_id, users, org_id, what,
                      *, full=False, filters=None) -> None:
        clean = await self._clean(source, graph_provider, connector_id, users, org_id, filters)
        try:
            await _sync_until(pipeshub_client, graph_provider, connector_id, clean, what, full=full)
        except AssertionError:
            problems = await _access_diff(source, graph_provider, connector_id, users, org_id, filters)
            raise AssertionError(f"{what}: PipesHub differs from Salesforce: {problems[:20]}") from None

    # ---------------------------------------------------------------- first sync

    @pytest.mark.order(1)
    async def test_tc_sf_001_records_match_salesforce(self, salesforce_source, salesforce_connector, graph_provider):
        counts = {t: await graph_provider.count_records_by_type(salesforce_connector, t) for t in RECORD_TYPES}
        logger.info("Salesforce records in PipesHub: %s", counts)
        if salesforce_source.simulated:
            everything = salesforce_source.sim.expected(salesforce_source.admin_email)
            for record_type in RECORD_TYPES:
                want = sum(1 for t in everything.values() if t == record_type)
                assert counts[record_type] == want, (record_type, counts[record_type], want)
            return
        for sobject, record_type in (("Opportunity", "DEAL"), ("Case", "CASE"), ("Product2", "PRODUCT")):
            want = len(salesforce_source.soql(f"SELECT Id FROM {sobject}"))
            assert counts[record_type] == want, (record_type, counts[record_type], want)

    @pytest.mark.order(2)
    async def test_tc_sf_002_access_equals_salesforce(self, salesforce_source, salesforce_connector,
                                                       salesforce_users, graph_provider, org_id):
        _simulated_only(salesforce_source)
        problems = await _access_diff(salesforce_source, graph_provider, salesforce_connector, salesforce_users, org_id)
        assert not problems, problems[:20]
        # The fixture's people cover each kind of reader: owners, a reader through the role
        # hierarchy, a user who may read only the product catalogue, and an inactive one.
        records = await _records(graph_provider, salesforce_connector)
        olga = await _readable(graph_provider, salesforce_connector, salesforce_users[email("olga")], org_id)
        assert {records[e].get("recordType") for e in olga} <= {"PRODUCT"}, "olga reads more than the catalogue"
        assert not await _readable(graph_provider, salesforce_connector, salesforce_users[email("gone")], org_id)

    @pytest.mark.order(3)
    async def test_tc_sf_003_hierarchy(self, salesforce_source, salesforce_connector, graph_provider):
        records = await _records(graph_provider, salesforce_connector)
        checked = 0
        for ext, record in records.items():
            parent = records.get(_parent(record))
            if record.get("recordType") == "FILE" and parent is not None:
                checked += 1
                assert _group(record) == _group(parent), (
                    f"{record.get('recordName')} sits in {_group(record)!r}, its record in {_group(parent)!r}")
                assert await graph_provider.record_inherits_permissions(salesforce_connector, ext)
            if record.get("recordType") == "TASK" and parent is not None:
                assert _group(record) == _group(parent), f"task {record.get('recordName')} is not with its record"
        assert checked, "no file attached to a deal, a case or a task"
        if not salesforce_source.simulated:
            return
        ids = salesforce_source.ids
        assert _group(records[ids["renewal"]]) == ids["acme"], "the deal is not under its account"
        assert _parent(records[ids["call"]]) == ids["renewal"], "the task is not under its deal"
        assert _group(records[ids["login"]]) == ids["acme"], "the case is not under its account"
        brochure = next(r for e, r in records.items() if e.startswith(ids["brochure"]))
        assert _group(brochure) == ids["acme"] and not _parent(brochure), "the account's file is not in its group"
        assert records[ids["reminder"]] is not None and not _parent(records[ids["reminder"]])

    # ---------------------------------------------------------------- incremental

    @pytest.mark.order(4)
    async def test_tc_sf_004_incremental_sync_picks_up_changes(self, salesforce_source, salesforce_connector,
                                                                pipeshub_client, graph_provider):
        _simulated_only(salesforce_source)
        _, salesforce_source.ids["outage"] = salesforce_source.sim.ops(
            {"op": "update", "key": "renewal", "fields": {"Name": "Acme renewal 2027"}},
            {"op": "add_case", "key": "outage", "subject": "New outage", "owner": "admin", "account": "acme"})

        async def seen() -> bool:
            names = set(await graph_provider.fetch_record_names(salesforce_connector))
            return {"Acme renewal 2027", "New outage"} <= names
        await _sync_until(pipeshub_client, graph_provider, salesforce_connector, seen, "the edit and the new case")

    @pytest.mark.order(5)
    async def test_tc_sf_005_chatter_changes_send_the_deal_again(self, salesforce_source, salesforce_connector,
                                                                  pipeshub_client, graph_provider):
        _simulated_only(salesforce_source)
        sim, deal = salesforce_source.sim, salesforce_source.ids["renewal"]

        async def revision() -> str:
            record = await graph_provider.get_record_by_external_id(salesforce_connector, deal)
            return str(getattr(record, "external_revision_id", None))

        for step, ops in (
            ("a new post", [{"op": "post", "key": "pricing", "parent": "renewal", "text": "Price agreed",
                             "author": "maya"}]),
            ("an edited post", [{"op": "edit_post", "key": "pricing", "text": "Price agreed at 40k"}]),
            ("a new reply", [{"op": "comment", "key": "pricing_reply", "post": "pricing", "text": "Signed",
                              "author": "raj"}]),
            ("an edited reply", [{"op": "edit_comment", "key": "pricing_reply", "text": "Signed by both"}]),
        ):
            before = await revision()
            time.sleep(1.1)  # a new SystemModstamp second
            sim.ops(*ops)

            async def moved() -> bool:
                return await revision() != before
            await _sync_until(pipeshub_client, graph_provider, salesforce_connector, moved,
                              f"{step} changing the deal's revision")

    # ---------------------------------------------------------------- access changes

    @pytest.mark.order(6)
    async def test_tc_sf_006_sharing_and_unsharing(self, salesforce_source, salesforce_connector, salesforce_users,
                                                    pipeshub_client, graph_provider, org_id):
        _simulated_only(salesforce_source)
        sim, deal = salesforce_source.sim, salesforce_source.ids["expansion"]
        maya = salesforce_users[email("maya")]
        assert deal not in await _readable(graph_provider, salesforce_connector, maya, org_id)
        sim.ops({"op": "share", "record": "expansion", "to": "maya", "level": "Read"})
        await self._settle(salesforce_source, pipeshub_client, graph_provider, salesforce_connector,
                           salesforce_users, org_id, "the share")
        assert deal in await _readable(graph_provider, salesforce_connector, maya, org_id)
        sim.ops({"op": "unshare", "record": "expansion", "to": "maya"})
        await self._settle(salesforce_source, pipeshub_client, graph_provider, salesforce_connector,
                           salesforce_users, org_id, "the share's removal")
        assert deal not in await _readable(graph_provider, salesforce_connector, maya, org_id)

    @pytest.mark.order(7)
    async def test_tc_sf_007_owner_change_moves_access(self, salesforce_source, salesforce_connector,
                                                        salesforce_users, pipeshub_client, graph_provider, org_id):
        _simulated_only(salesforce_source)
        ids = salesforce_source.ids
        salesforce_source.sim.ops({"op": "update", "key": "expansion", "fields": {"OwnerId": ids["maya"]}})
        await self._settle(salesforce_source, pipeshub_client, graph_provider, salesforce_connector,
                           salesforce_users, org_id, "the new owner's access")
        salesforce_source.sim.ops({"op": "update", "key": "expansion", "fields": {"OwnerId": ids["raj"]}})
        await self._settle(salesforce_source, pipeshub_client, graph_provider, salesforce_connector,
                           salesforce_users, org_id, "the owner change back")

    @pytest.mark.order(8)
    async def test_tc_sf_008_deactivated_user(self, salesforce_source, salesforce_connector, salesforce_users,
                                               pipeshub_client, graph_provider, org_id):
        _simulated_only(salesforce_source)
        sim = salesforce_source.sim
        raj = salesforce_users[email("raj")]
        assert await _readable(graph_provider, salesforce_connector, raj, org_id)
        sim.ops({"op": "deactivate", "user": "raj"})
        await self._settle(salesforce_source, pipeshub_client, graph_provider, salesforce_connector,
                           salesforce_users, org_id, "the deactivation")
        assert not await _readable(graph_provider, salesforce_connector, raj, org_id)
        sim.ops({"op": "activate", "user": "raj"})
        await self._settle(salesforce_source, pipeshub_client, graph_provider, salesforce_connector,
                           salesforce_users, org_id, "the reactivation")
        assert await _readable(graph_provider, salesforce_connector, raj, org_id)

    @pytest.mark.order(9)
    async def test_tc_sf_009_frozen_user(self, salesforce_source, salesforce_connector, salesforce_users,
                                         pipeshub_client, graph_provider, org_id):
        _simulated_only(salesforce_source)
        sim = salesforce_source.sim
        maya = salesforce_users[email("maya")]
        sim.ops({"op": "freeze", "user": "maya", "frozen": True})
        await self._settle(salesforce_source, pipeshub_client, graph_provider, salesforce_connector,
                           salesforce_users, org_id, "the freeze")
        assert not await _readable(graph_provider, salesforce_connector, maya, org_id)
        sim.ops({"op": "freeze", "user": "maya", "frozen": False})
        await self._settle(salesforce_source, pipeshub_client, graph_provider, salesforce_connector,
                           salesforce_users, org_id, "the unfreeze")
        assert await _readable(graph_provider, salesforce_connector, maya, org_id)

    # ---------------------------------------------------------------- files and deletions

    @pytest.mark.order(10)
    async def test_tc_sf_010_file_unlinked_and_new_version(self, salesforce_source, salesforce_connector,
                                                           salesforce_users, pipeshub_client, graph_provider, org_id):
        _simulated_only(salesforce_source)
        sim, ids = salesforce_source.sim, salesforce_source.ids
        sim.ops({"op": "unlink", "file": "notes", "record": "expansion"})
        await self._settle(salesforce_source, pipeshub_client, graph_provider, salesforce_connector,
                           salesforce_users, org_id, "the file taken off the deal")
        records = await _records(graph_provider, salesforce_connector)
        assert not any(e.startswith(ids["notes"]) and e.endswith(ids["expansion"]) for e in records)
        assert any(e.startswith(ids["notes"]) and e.endswith(ids["login"]) for e in records), \
            "the same file on the case left too"

        sim.ops({"op": "new_version", "key": "contract", "text": "%PDF-1.4 renewal terms, second edition"})
        contract = next(r for e, r in records.items() if e.startswith(ids["contract"]))

        async def new_content() -> bool:
            resp = pipeshub_client.stream_record(_id(contract), timeout=120)
            return resp.ok and b"second edition" in resp.content
        await _sync_until(pipeshub_client, graph_provider, salesforce_connector, new_content,
                          "the file's new version")

    @pytest.mark.order(11)
    async def test_tc_sf_011_deleted_deal_and_restore(self, salesforce_source, salesforce_connector,
                                                       salesforce_users, pipeshub_client, graph_provider, org_id):
        _simulated_only(salesforce_source)
        sim, ids = salesforce_source.sim, salesforce_source.ids
        sim.ops({"op": "delete", "key": "renewal"})

        async def removed() -> bool:
            # Unreadable at once (Salesforce no longer lists it as readable); removed once
            # getDeleted lists it, from the next whole minute on.
            records = await _records(graph_provider, salesforce_connector)
            return not ({ids["renewal"], ids["call"]} & set(records)) and \
                not any(e.endswith(ids["renewal"]) for e in records)
        await _sync_until(pipeshub_client, graph_provider, salesforce_connector, removed,
                          "the deleted deal, its task and its file removed")
        await self._settle(salesforce_source, pipeshub_client, graph_provider, salesforce_connector,
                           salesforce_users, org_id, "access after the deletion")
        sim.ops({"op": "undelete", "key": "renewal"})
        await self._settle(salesforce_source, pipeshub_client, graph_provider, salesforce_connector,
                           salesforce_users, org_id, "the restored deal back")
        assert ids["renewal"] in await _records(graph_provider, salesforce_connector)

    @pytest.mark.order(12)
    async def test_tc_sf_012_deletions_of_every_kind(self, salesforce_source, salesforce_connector,
                                                     salesforce_users, pipeshub_client, graph_provider, org_id):
        _simulated_only(salesforce_source)
        sim, ids = salesforce_source.sim, salesforce_source.ids
        sim.ops({"op": "delete", "key": "outage"}, {"op": "delete", "key": "reminder"},
                {"op": "delete", "key": "gadget"}, {"op": "delete", "key": "globex"})

        async def removed() -> bool:
            records = await _records(graph_provider, salesforce_connector)
            return not ({ids[k] for k in ("outage", "reminder", "gadget", "expansion", "followup")} & set(records))
        await _sync_until(pipeshub_client, graph_provider, salesforce_connector, removed,
                          "the deleted case, task, product and account removed")
        await self._settle(salesforce_source, pipeshub_client, graph_provider, salesforce_connector,
                           salesforce_users, org_id, "access after the deletions")
        assert await graph_provider.get_record_group_by_external_id(salesforce_connector, ids["globex"]) is None, \
            "the deleted account's group stayed"

    # ---------------------------------------------------------------- full syncs and filters

    @pytest.mark.order(13)
    async def test_tc_sf_013_date_filter_narrowed_then_cleared(self, salesforce_source, salesforce_connector,
                                                               salesforce_users, pipeshub_client, graph_provider,
                                                               org_id):
        _simulated_only(salesforce_source)
        sim = salesforce_source.sim
        # Everything synced so far was last changed before the cut; only the fresh case after it.
        cut = int(time.time() * 1000)
        time.sleep(2)
        (salesforce_source.ids["fresh"],) = sim.ops(
            {"op": "add_case", "key": "fresh", "subject": "Fresh case", "owner": "admin", "account": "acme"})
        modified = {"modified": {"operator": "is_after", "type": "datetime", "value": {"start": cut, "end": None}}}
        pipeshub_client.update_connector_filters_sync_safe(salesforce_connector, filters={"sync": {"values": modified}})
        await wait_for_sync_completion(pipeshub_client, graph_provider, salesforce_connector, timeout=300)
        await self._settle(salesforce_source, pipeshub_client, graph_provider, salesforce_connector,
                           salesforce_users, org_id, "the narrowed filter", filters=modified)
        admin_reads = await _readable(graph_provider, salesforce_connector,
                                      salesforce_users[salesforce_source.admin_email], org_id)
        assert salesforce_source.ids["fresh"] in admin_reads
        assert salesforce_source.ids["login"] not in admin_reads, "a case outside the filter is still readable"
        pipeshub_client.update_connector_filters_sync_safe(salesforce_connector, filters={"sync": {"values": {}}})
        await wait_for_sync_completion(pipeshub_client, graph_provider, salesforce_connector, timeout=300)
        await self._settle(salesforce_source, pipeshub_client, graph_provider, salesforce_connector,
                           salesforce_users, org_id, "the cleared filter")

    @pytest.mark.order(14)
    async def test_tc_sf_014_second_full_sync_keeps_files_readable(self, salesforce_source, salesforce_connector,
                                                                    salesforce_users, pipeshub_client,
                                                                    graph_provider, org_id):
        _simulated_only(salesforce_source)
        for n in (1, 2):
            await self._settle(salesforce_source, pipeshub_client, graph_provider, salesforce_connector,
                               salesforce_users, org_id, f"full sync {n}", full=True)
        files = {e for e, r in (await _records(graph_provider, salesforce_connector)).items()
                 if r.get("recordType") == "FILE"}
        assert files
        admin_reads = await _readable(graph_provider, salesforce_connector,
                                      salesforce_users[salesforce_source.admin_email], org_id)
        assert files <= admin_reads, f"files nobody can read after two full syncs: {sorted(files - admin_reads)}"

    # ---------------------------------------------------------------- resilience

    @pytest.mark.order(15)
    async def test_tc_sf_015_failed_read_removes_nothing(self, salesforce_source, salesforce_connector,
                                                          salesforce_users, pipeshub_client, graph_provider, org_id):
        _simulated_only(salesforce_source)
        sim = salesforce_source.sim
        before = set(await _records(graph_provider, salesforce_connector))
        sim.ops({"op": "update", "key": "renewal", "fields": {"Name": "Acme renewal (after the outage)"}})
        sim.fail_queries("FROM Opportunity", status=503, times=2)
        try:
            pipeshub_client.resync_connector(salesforce_connector, full_sync=False, connector_name="Salesforce")
            await wait_for_sync_completion(pipeshub_client, graph_provider, salesforce_connector, timeout=300)
            assert set(await _records(graph_provider, salesforce_connector)) == before, \
                "a failed sync removed records"
        finally:
            sim.clear_faults()

        async def renamed() -> bool:
            return "Acme renewal (after the outage)" in set(await graph_provider.fetch_record_names(salesforce_connector))
        await _sync_until(pipeshub_client, graph_provider, salesforce_connector, renamed, "the edit after the outage")

    # ---------------------------------------------------------------- content and load

    @pytest.mark.order(16)
    async def test_tc_sf_016_long_chatter_streams_in_full(self, salesforce_source, salesforce_connector,
                                                           pipeshub_client, graph_provider):
        _simulated_only(salesforce_source)
        sim, deal = salesforce_source.sim, salesforce_source.ids["renewal"]
        sim.ops(*[{"op": "post", "parent": "renewal", "text": f"update {i:02d}", "author": "maya"} for i in range(30)])

        async def synced() -> bool:
            return await graph_provider.get_record_by_external_id(salesforce_connector, deal) is not None
        await _sync_until(pipeshub_client, graph_provider, salesforce_connector, synced, "the deal")
        record = await graph_provider.get_record_by_external_id(salesforce_connector, deal)
        started = time.monotonic()
        resp = pipeshub_client.stream_record(record.id, timeout=120)
        assert resp.ok, resp.status_code
        text = resp.content.decode(errors="replace")
        missing = [i for i in range(30) if f"update {i:02d}" not in text]
        assert not missing, f"posts missing from the deal's content: {missing}"
        assert time.monotonic() - started < 60, "reading one deal took over a minute"

    @pytest.mark.order(17)
    async def test_tc_sf_017_more_than_a_thousand_records(self, salesforce_source, salesforce_connector,
                                                          salesforce_users, pipeshub_client, graph_provider,
                                                          org_id):
        _simulated_only(salesforce_source)
        sim = salesforce_source.sim
        sim.ops({"op": "add_tasks", "prefix": "Bulk", "owner": "maya", "count": 1050})
        await self._settle(salesforce_source, pipeshub_client, graph_provider, salesforce_connector,
                           salesforce_users, org_id, "1,050 more tasks with their readers")
        sim.ops({"op": "share", "record": "login", "to": "raj", "level": "Read"})
        await self._settle(salesforce_source, pipeshub_client, graph_provider, salesforce_connector,
                           salesforce_users, org_id, "a share in a large org")
        sim.ops({"op": "unshare", "record": "login", "to": "raj"})
        await self._settle(salesforce_source, pipeshub_client, graph_provider, salesforce_connector,
                           salesforce_users, org_id, "a revocation in a large org")
