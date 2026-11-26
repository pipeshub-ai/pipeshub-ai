# pyright: ignore-file

"""
Zendesk on the simulator: a full sync that reads only part of Zendesk
=====================================================================

A full sync ends with the platform's unlink of every connector node it did not write or note kept. If the sync skipped
part of Zendesk after a fault, what it did not reach is still in Zendesk and must keep its links: nobody may lose access
(no DENIED, no MISSING). What is really gone at the source (the controls below) may stay linked until the retry, and must
be gone after it.

Each case: a first sync, checked strictly; the controls removed at the source; faults; a full sync, whose DENIED and
MISSING fail the case and whose LEAK and STALE are tolerated (kept until the retry); the faults cleared; the next sync
(the full sync owed, or a full sync when none is owed), checked strictly. Every fault must fire.

  TC-ZDSIM-FS-001  No fault: the full sync unlinks what is gone (the controls) and nothing else [F]
  TC-ZDSIM-FS-002  GET users answers 503 every time: agents, memberships and tickets are skipped [F, I]
  TC-ZDSIM-FS-003  GET group_memberships answers 503 every time [F, I]
  TC-ZDSIM-FS-004  The organization export answers 503 every time [F, I]
  TC-ZDSIM-FS-005  Pages of two; the second page of the ticket export answers 503 every time [F, I, H]
  TC-ZDSIM-FS-006  The comments of one ticket answer 503: the ticket is set aside, the rest is read [F, I]
  TC-ZDSIM-FS-007  Help Center categories answer 503 [F, I]
  TC-ZDSIM-FS-008  Help Center sections answer 503 [F, I]
  TC-ZDSIM-FS-009  The Help Center article export answers 503 [F, I]
  TC-ZDSIM-FS-010  The organization export is complete but leaves out Shared Org (still in Zendesk) and its lookup answers
                   503: its group and its tickets' org grant must stay [F, I]
  TC-ZDSIM-FS-011  Every comment list answers 503 (no ticket's content can be read): nothing is unlinked [F, I]
"""

from typing import Any

import pytest
from connectors.zendesk.sim_instances import desk
from connectors.zendesk.sim_world import KEEPS_FAILING, KNOWN_GAPS, ZendeskWorld, fault, succeeded

CONTROLS: list[dict[str, Any]] = [
    {"op": "delete_ticket", "ticket": "t_unassigned"},
    {"op": "delete_article", "article": "a_sub"},
    {"op": "remove_membership", "user": "agent_billing", "group": "billing"},
]


def always(path_regex: str, code: int = 503) -> dict[str, Any]:
    return fault(path_regex, code, message="injected", max_times=KEEPS_FAILING)


async def run_case(w: ZendeskWorld, faults: list[dict[str, Any]], *, behaviours: dict[str, Any] | None = None,
                   before_faults: Any = None, expect_incomplete: bool = True) -> None:
    assert succeeded(await w.start(desk(), behaviours=behaviours))
    await w.assert_matches_source("first sync")
    w.ops(*CONTROLS)
    rules = await before_faults(w) if before_faults else faults
    if rules:
        w.faults(*rules)
    faulted = await w.sync(full=True)
    owed = await w.pending_full_sync()
    problems = await w.differences()
    fired = w.clear_faults() if rules else 0
    failing = [p for p in problems if p["type"] not in KNOWN_GAPS
               and (p["type"] == "MISSING" or p["type"].startswith("DENIED") or p["type"].startswith("SIM_")
                    or p["type"] in ("DUPLICATE", "PARENT"))]
    assert not failing, f"a full sync that read part of Zendesk lost or misfiled: {failing[:10]} (outcome {faulted})"
    if expect_incomplete and rules:
        assert owed or not succeeded(faulted), f"the incomplete full sync owes no other full sync"
    assert fired >= (1 if rules else 0)
    assert succeeded(await w.sync(full=not owed)), "the clean retry did not succeed"
    await w.assert_matches_source("clean retry")


async def org_missing_rules(w: ZendeskWorld) -> list[dict[str, Any]]:
    """The organization export with Shared Org left out, and Shared Org's lookup answering 503."""
    import requests  # noqa: PLC0415
    oid = w.key("organizations", "org_shared")
    resp = requests.get(f"{w.sim.base_url}/api/v2/incremental/organizations", params={"start_time": 0}, timeout=30,
                        headers={"Host": "pipeshubsim.zendesk.com", "Authorization": f"Bearer {w.token_for()}"})
    kept = [o for o in resp.json()["organizations"] if o["id"] != oid]
    assert kept, "the shaped export still lists another organization"
    return [{"match": {"path_regex": r"^/api/v2/incremental/organizations(\.json)?$"},
             "effect": {"type": "shape", "ops": [{"path": "organizations", "value": kept}]}},
            always(rf"^/api/v2/organizations/{oid}(\.json)?$")]


@pytest.mark.integration
@pytest.mark.zendesk_sim
@pytest.mark.asyncio(loop_scope="session")
class TestZendeskSimFullSync:

    async def test_tc_zdsim_fs_001_clean_control(self, zendesk_world: ZendeskWorld) -> None:
        await run_case(zendesk_world, [], expect_incomplete=False)

    async def test_tc_zdsim_fs_002_users_export_fails(self, zendesk_world: ZendeskWorld) -> None:
        await run_case(zendesk_world, [always(r"^/api/v2/users(\.json)?$")])

    async def test_tc_zdsim_fs_003_memberships_fail(self, zendesk_world: ZendeskWorld) -> None:
        await run_case(zendesk_world, [always(r"^/api/v2/group_memberships(\.json)?$")])

    async def test_tc_zdsim_fs_004_organizations_export_fails(self, zendesk_world: ZendeskWorld) -> None:
        await run_case(zendesk_world, [always(r"^/api/v2/incremental/organizations(\.json)?$")])

    async def test_tc_zdsim_fs_005_ticket_export_cut_short(self, zendesk_world: ZendeskWorld) -> None:
        await run_case(zendesk_world, [fault(r"^/api/v2/incremental/tickets/cursor(\.json)?$", 503, message="injected",
                                             after_n=1, max_times=KEEPS_FAILING)],
                       behaviours={"incremental_page_size": 2})

    async def test_tc_zdsim_fs_006_ticket_set_aside(self, zendesk_world: ZendeskWorld) -> None:
        async def rules(w: ZendeskWorld) -> list[dict[str, Any]]:
            return [always(rf"^/api/v2/tickets/{w.key('tickets', 't_billing')}/comments(\.json)?$")]
        await run_case(zendesk_world, [], before_faults=rules)

    async def test_tc_zdsim_fs_007_hc_categories_fail(self, zendesk_world: ZendeskWorld) -> None:
        await run_case(zendesk_world, [always(r"^/api/v2/help_center/categories(\.json)?$")])

    async def test_tc_zdsim_fs_008_hc_sections_fail(self, zendesk_world: ZendeskWorld) -> None:
        await run_case(zendesk_world, [always(r"^/api/v2/help_center/sections(\.json)?$")])

    async def test_tc_zdsim_fs_009_hc_articles_export_fails(self, zendesk_world: ZendeskWorld) -> None:
        await run_case(zendesk_world, [always(r"^/api/v2/help_center/incremental/articles(\.json)?$")])

    async def test_tc_zdsim_fs_010_org_left_out_and_lookup_fails(self, zendesk_world: ZendeskWorld) -> None:
        await run_case(zendesk_world, [], before_faults=org_missing_rules, expect_incomplete=False)

    async def test_tc_zdsim_fs_011_every_comment_list_fails(self, zendesk_world: ZendeskWorld) -> None:
        await run_case(zendesk_world, [always(r"^/api/v2/tickets/\d+/comments(\.json)?$")])
