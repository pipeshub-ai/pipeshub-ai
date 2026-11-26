# pyright: ignore-file

"""
Zoom on the simulator: who can find which meeting
=================================================

Each test changes who a meeting names, syncs, and checks that every person reads in PipesHub exactly what Zoom's rule
(the simulator's oracle) gives them: the host owns the record; alternative hosts, participants with an address and
invitees read it; nobody outside the account's active users reads anything (the connector gate). A grant that Zoom
removed must be gone after the next sync, not kept.

  TC-ZMSIM-ACL-001  Access matrix of the default account: guests, a partner and a deactivated or pending user read
                    nothing; participants without an address give nobody access [E]
  TC-ZMSIM-ACL-002  Alternative hosts listed by user id, by address and mixed, with spaces between the entries [E]
  TC-ZMSIM-ACL-003  An alternative host is replaced and an invitee added: the new ones read, the replaced one stops [E/D]
  TC-ZMSIM-ACL-004  An invitee removed through Zoom's API stops reading; a participant who joined a meeting of
                    yesterday after the first sync reads it [E/D]
  TC-ZMSIM-ACL-005  A deleted meeting: Zoom no longer says who its alternative hosts and invitees were, so they stop
                    reading; the past instances stay [E/D]
  TC-ZMSIM-ACL-006  The user who signed the connector in is deactivated: the run fails and changes nothing; back, it
                    matches [E]
  TC-ZMSIM-ACL-007  A reader (not a host) deactivated reads nothing any more, reactivated reads again [E]
  TC-ZMSIM-ACL-008  A user whose address is empty and a participant with no PipesHub account: the sync succeeds, the
                    others are unaffected, and no PipesHub account is made for the stranger [E]
  TC-ZMSIM-ACL-011  A person with a PipesHub account and no Zoom account, given a record's id, cannot stream it or find
                    it by search [N]
  TC-ZMSIM-ACL-010  Alternative hosts listed as " bob ;  Cara ; " read like any other [E]
  TC-ZMSIM-ACL-009  Several instances of one meeting number: each has its own record and its own participants [E/B]
"""

import pytest

from connectors.zoom.sim_instances import DOMAIN, GUEST, PARTNER, small, users
from connectors.zoom.sim_world import ZoomWorld, succeeded


def _inst(key: str, days_ago: float, who: list[dict]) -> dict:
    return {"key": key, "days_ago": days_ago, "participants": who}


# Known defects of the connector, no open pull request: the fix is its own change. Strict: fixing one fails these tests.
LATER_ACCESS_IS_NOT_READ = pytest.mark.xfail(
    strict=True, raises=AssertionError,
    reason="a meeting's alternative hosts, invitees and title are read only when Zoom's report lists it to a run: its "
           "first sync, the overlap with the next run, a full sync; an incremental run misses a later change: "
           "zoom/connector.py _sync_meetings_for_user")
DELETED_MEETING_KEEPS_ITS_READERS = pytest.mark.xfail(
    strict=True, raises=AssertionError,
    reason="a deleted meeting's alternative hosts and invitees keep reading its instances, even after a full sync: Zoom "
           "answers its detail read 3001, which the connector takes for a failed read, so it adds the grants it read to "
           "the stored ones instead of replacing them; an incremental run does not re-read an instance older than the "
           "overlap at all: zoom/connector.py _process_one_meeting")
LEAVERS_KEEP_ACCESS = pytest.mark.xfail(
    strict=True, raises=AssertionError,
    reason="a user Zoom no longer lists as active keeps the connector and what it granted: zoom/connector.py run_sync")


@pytest.mark.integration
@pytest.mark.zoom_sim
@pytest.mark.asyncio(loop_scope="session")
class TestZoomSimAccess:

    async def test_tc_zmsim_acl_001_access_matrix(self, zoom_world: ZoomWorld) -> None:
        from connectors.zoom.sim_instances import base  # noqa: PLC0415
        w = zoom_world
        assert succeeded(await w.start(base()))
        await w.assert_matches_source("after the first sync")
        held = await w.holdings()
        for outsider in (GUEST, PARTNER, f"gus@{DOMAIN}", f"hal@{DOMAIN}", f"dan@{DOMAIN}"):
            if outsider in held:
                assert not held[outsider], f"{outsider} is outside the account's active users and reads {held[outsider]}"

    async def test_tc_zmsim_acl_002_alternative_hosts_by_id(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        doc = small(meetings=[
            {"key": "byid", "host": "ann", "topic": "By id", "alt_hosts_format": "id", "alt_hosts": ["bob", "cara"],
             "instances": [_inst("i1", 2, [{"user": "bob"}])]},
            {"key": "mixed", "host": "bob", "topic": "Mixed", "alt_hosts_format": "mixed",
             "alt_hosts": ["ann", "cara"], "instances": [_inst("i2", 3, [{"user": "ann"}])]},
            {"key": "mail", "host": "cara", "topic": "By address", "alt_hosts_format": "email",
             "alt_hosts": ["ann"], "instances": [_inst("i3", 4, [{"user": "bob"}])]}])
        assert succeeded(await w.start(doc))
        await w.assert_matches_source("after a first sync with alternative hosts named by id, address and both")
        held = await w.holdings()
        assert w.instance("i1")["uuid"] in held[f"bob@{DOMAIN}"] and w.instance("i1")["uuid"] in held[f"cara@{DOMAIN}"]

    @LATER_ACCESS_IS_NOT_READ
    async def test_tc_zmsim_acl_003_alt_host_replaced_invitee_added(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        assert succeeded(await w.start(small()))
        await w.assert_matches_source("after the first sync")
        w.ops({"op": "set_alt_hosts", "meeting": "standup", "users": ["cara"]},
              {"op": "add_invitee", "meeting": "review", "email": f"cara@{DOMAIN}"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after Bob stopped being an alternative host and Cara was invited")
        held = await w.holdings()
        assert w.instance("s2")["uuid"] not in held[f"bob@{DOMAIN}"] or w.instance("s2")["host"] == f"bob@{DOMAIN}"
        assert w.instance("r1")["uuid"] in held[f"cara@{DOMAIN}"]

    @LATER_ACCESS_IS_NOT_READ
    async def test_tc_zmsim_acl_004_invitee_removed_participant_joined(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        doc = small(meetings=[{"key": "m", "host": "ann", "topic": "Weekly", "type": 8,
                               "invitees": ["cara"],
                               "instances": [_inst("m1", 1, [{"user": "bob"}]), _inst("m2", 12, [{"user": "bob"}])]}])
        assert succeeded(await w.start(doc))
        await w.assert_matches_source("after the first sync")
        number = w.sim.expected()["keys"]["m"]
        w.api("PATCH", f"/meetings/{number}", {"settings": {"meeting_invitees": []}})
        w.ops({"op": "participant_joined", "instance": "m1", "participant": {"user": "cara"}})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the invitee was removed and Cara joined yesterday's meeting")
        held = await w.holdings()
        assert w.instance("m2")["uuid"] not in held[f"cara@{DOMAIN}"] or w.instance("m2")["host"] == f"cara@{DOMAIN}"
        assert w.instance("m1")["uuid"] in held[f"cara@{DOMAIN}"]

    @DELETED_MEETING_KEEPS_ITS_READERS
    async def test_tc_zmsim_acl_005_deleted_meeting(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        assert succeeded(await w.start(small()))
        await w.assert_matches_source("after the first sync")
        w.ops({"op": "meeting_deleted", "meeting": "standup"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the series was deleted")
        assert w.instance("s1")["uuid"] in {r["externalRecordId"] for r in await w.records()}

    async def test_tc_zmsim_acl_006_token_owner_deactivated_and_back(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        assert succeeded(await w.start(small()))
        before = await w.holdings()
        # Ann signed the connector in, so her token stops working with her account: the run reads nothing.
        w.ops({"op": "user_deactivated", "user": "ann"})
        assert not succeeded(await w.sync()), "a sync whose every read was refused (401) reported success"
        await w.assert_holdings_kept(before, "after the token's owner was deactivated")
        w.ops({"op": "user_activated", "user": "ann"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the token's owner was active again")

    @LEAVERS_KEEP_ACCESS
    async def test_tc_zmsim_acl_007_reader_deactivated(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        doc = small(users=users()[:3] + [{"key": "fay", "first_name": "Fay"}],
                    meetings=[{"key": "m", "host": "ann", "topic": "Weekly", "type": 8, "invitees": ["fay"],
                               "instances": [_inst("m1", 2, [{"user": "bob"}, {"user": "fay"}])]}])
        assert succeeded(await w.start(doc))
        await w.assert_matches_source("after the first sync")
        assert w.instance("m1")["uuid"] in (await w.holdings())[f"fay@{DOMAIN}"]
        w.ops({"op": "user_deactivated", "user": "fay"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after Fay was deactivated")
        assert not (await w.holdings())[f"fay@{DOMAIN}"]
        w.ops({"op": "user_activated", "user": "fay"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after Fay was active again")
        assert w.instance("m1")["uuid"] in (await w.holdings())[f"fay@{DOMAIN}"]

    async def test_tc_zmsim_acl_008_no_address_and_no_account(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        stranger = "stranger@nowhere.example"
        doc = small(users=users()[:3] + [{"key": "nomail", "first_name": "No", "last_name": "Mail", "email": ""}],
                    meetings=[{"key": "m", "host": "ann", "topic": "Weekly", "type": 2, "invitees": [stranger],
                               "instances": [_inst("m1", 2, [{"user": "bob"}, {"user": "nomail"},
                                                             {"email": stranger, "name": "Stranger"}])]}])
        assert succeeded(await w.start(doc))
        await w.assert_matches_source("after the first sync")
        assert await w.graph.graph_find_user_by_email(stranger) is None, "a guest must not get a PipesHub account"

    async def test_tc_zmsim_acl_009_instances_of_one_meeting_number(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        doc = small(meetings=[{"key": "m", "host": "ann", "topic": "Daily", "type": 8,
                               "instances": [_inst("d1", 1, [{"user": "bob"}]), _inst("d2", 2, [{"user": "cara"}]),
                                             _inst("d3", 3, [{"user": "bob"}, {"user": "cara"}])]}])
        assert succeeded(await w.start(doc))
        await w.assert_matches_source("after the first sync")
        held = await w.holdings()
        assert w.instance("d1")["uuid"] in held[f"bob@{DOMAIN}"] and w.instance("d1")["uuid"] not in held[f"cara@{DOMAIN}"]
        assert len({w.instance(k)["meeting_id"] for k in ("d1", "d2", "d3")}) == 1

    async def test_tc_zmsim_acl_010_alternative_host_list_with_spaces(self, zoom_world: ZoomWorld) -> None:
        """Zoom's alternative_hosts is "a; b" in its documentation's examples: spaces around the separators, and a
        trailing one, give the same readers."""
        w = zoom_world
        doc = small(meetings=[{"key": "m", "host": "ann", "topic": "Spaced", "type": 2, "alt_hosts": ["bob", "cara"],
                               "instances": [_inst("m1", 2, [{"user": "ann"}, {"user": "fay"}])]}])
        doc["users"] = users()[:3] + [{"key": "fay", "first_name": "Fay"}]
        w.load(doc)
        w.faults({"match": {"path_regex": r"^/v2/meetings/\d+$"},
                  "effect": {"type": "shape", "ops": [{"path": "settings.alternative_hosts",
                                                       "value": f" bob@{DOMAIN} ;  Cara@{DOMAIN} ; "}]}})
        w.create_connector()
        await w.sign_in()
        assert succeeded(await w.enable())
        w.clear_faults()
        await w.assert_matches_source("after alternative hosts listed with spaces")

    async def test_tc_zmsim_acl_011_a_guessed_record_id_by_another_person(self, zoom_world: ZoomWorld, second_user) -> None:
        """A PipesHub user with no Zoom account who has a record's id (a guess, a leaked link) cannot stream it, and
        searching the connector finds nothing for them: the permission query's answer holds on the HTTP routes."""
        w = zoom_world
        assert succeeded(await w.start(small()))
        record = await w.record_for(w.instance("s1")["uuid"])
        assert w.client.stream_record(record["id"]).status_code == 200, "the connector's creator streams it"
        refused = second_user.get(w.client._stream_record_path(record["id"]))
        assert refused.status_code in (401, 403, 404), (
            f"another person streamed a meeting by its record id: HTTP {refused.status_code} {refused.text[:200]}")
        assert "Transcript" not in refused.text and "Participants" not in refused.text
        found = second_user.search_filtered("Standup", {"apps": [w.connector_id]})
        assert found.status_code in (200, 403, 404)
        if found.status_code == 200:
            hits = (found.json().get("searchResponse") or found.json()).get("searchResults") or []
            assert not hits, f"search showed another person {len(hits)} meetings"

