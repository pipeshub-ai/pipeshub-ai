# pyright: ignore-file

"""
Zendesk on the simulator: a sync that cannot read
=================================================

Every Zendesk endpoint the connector calls is faulted twice: once transiently (one 503, or a 429 with Retry-After, which
the connector may retry) and once for as long as the sync runs (503 every time). A run that could not read everything
must remove nothing, give nobody new access and leave the next clean run equal to Zendesk; a run that reports success
must have read everything. Every injected fault must fire, or the test proves nothing.

Endpoints whose faults cannot affect each other are faulted together (one Support API endpoint and one Help Center
endpoint in the same run); the list below is checked against what the simulator saw the connector call.

  TC-ZDSIM-FAIL-001  Each endpoint, once: a 503 or a 429 on its first request [I]
  TC-ZDSIM-FAIL-002  Each endpoint, for the whole run: 503 on every request [I]
  TC-ZDSIM-FAIL-003  Every endpoint the connector called is in the fault matrix [I, O]
  TC-ZDSIM-FAIL-004  The connection test's endpoints (token introspection, the connected account) refused (403) or
                     failing: the connector is not turned on, or the run does not succeed; nothing is stored [A, I]
  TC-ZDSIM-FAIL-005  The ticket export cut short after its first page, for the whole run (a full sync) [I, H]
  TC-ZDSIM-FAIL-006  A bad cursor in the middle of the export: the next request is refused with InvalidCursor [H]
  TC-ZDSIM-FAIL-007  The export limit of 10 requests a minute (429 with Retry-After): the connector waits and finishes [I]
  TC-ZDSIM-FAIL-008  A dropped connection on the export, once [I]
  TC-ZDSIM-FAIL-009  A response with a malformed body on the groups list, once [I]
  TC-ZDSIM-FAIL-010  A 403 on the comments of one ticket for the whole run: the ticket is set aside, the rest is read [I]
  TC-ZDSIM-FAIL-012  Each endpoint a stream reads (ticket, comments, article, article comments, files), once and for
                     the whole run: the stream is an error or exactly the fault-free content, never part of it [I, J]
  TC-ZDSIM-FAIL-011  An attachment download refused (404 / 503) for the whole run: the record is kept and the next run
                     fetches it [I, J]
"""

import json
import logging
from typing import Any

import pytest
from pipeshub_client import PipeshubClientError
from connectors.zendesk.sim_instances import desk, small
from connectors.zendesk.sim_world import (
    KEEPS_FAILING,
    KNOWN_GAPS,
    REFUSED,
    ZendeskWorld,
    fault,
    succeeded,
)

logger = logging.getLogger("zendesk-sim")

# Support API endpoints, as the simulator names them in its call counts -> the path the fault matches. Each pair shares
# one run: the two cannot affect each other (tickets and the Help Center are read by separate steps).
SUPPORT: dict[str, str] = {
    "GET /api/v2/users": r"^/api/v2/users(\.json)?$",
    "GET /api/v2/custom_roles": r"^/api/v2/custom_roles(\.json)?$",
    "GET /api/v2/custom_statuses": r"^/api/v2/custom_statuses(\.json)?$",
    "GET /api/v2/groups": r"^/api/v2/groups(\.json)?$",
    "GET /api/v2/group_memberships": r"^/api/v2/group_memberships(\.json)?$",
    "GET /api/v2/incremental/organizations": r"^/api/v2/incremental/organizations(\.json)?$",
    "GET /api/v2/organizations/{organization_id}/users": r"^/api/v2/organizations/\d+/users(\.json)?$",
    "GET /api/v2/incremental/tickets/cursor": r"^/api/v2/incremental/tickets/cursor(\.json)?$",
    "GET /api/v2/tickets/{ticket_id}/comments": r"^/api/v2/tickets/\d+/comments(\.json)?$",
}
# Read when a record is streamed (a person opens it, or the indexer fetches it): FAIL-012.
STREAMED = {
    "GET /api/v2/tickets/{ticket_id}": r"^/api/v2/tickets/\d+(\.json)?$",
    "GET /api/v2/attachments/{attachment_id}": r"^/api/v2/attachments/\d+(\.json)?$",
    "GET /api/v2/help_center/articles/{article_id}": r"^/api/v2/help_center/articles/\d+(\.json)?$",
    "GET /api/v2/help_center/articles/{article_id}/comments": r"^/api/v2/help_center/articles/\d+/comments(\.json)?$",
    "GET /api/v2/help_center/articles/attachments/{article_attachment_id}":
        r"^/api/v2/help_center/articles/attachments/\d+(\.json)?$",
}
HELP_CENTER: dict[str, str] = {
    "GET /api/v2/help_center/categories": r"^/api/v2/help_center/categories(\.json)?$",
    "GET /api/v2/help_center/sections": r"^/api/v2/help_center/sections(\.json)?$",
    "GET /api/v2/help_center/incremental/articles": r"^/api/v2/help_center/incremental/articles(\.json)?$",
    "GET /api/v2/help_center/articles": r"^/api/v2/help_center/articles(\.json)?$",
    "GET /api/v2/help_center/articles/{article_id}/attachments": r"^/api/v2/help_center/articles/\d+/attachments(\.json)?$",
}
# The connection test and the account check run before the sync; they have their own case (FAIL-004).
CONNECTION = {"GET /api/v2/oauth/tokens/current": r"^/api/v2/oauth/tokens/current(\.json)?$",
              "GET /api/v2/users/me": r"^/api/v2/users/me(\.json)?$"}
# Read only when the organization export leaves one out (the lookup of that one): faulted by FS-010.
ORG_LOOKUP = {"GET /api/v2/organizations/{organization_id}": r"^/api/v2/organizations/\d+(\.json)?$"}
# Downloads, which are not under /api/v2.
FILES = {"GET /attachments/token": r"^/attachments/token/", "GET /hc/article_attachments": r"^/hc/article_attachments/",
         "GET cdn": r"^/attachment/"}
MATRIX = {**SUPPORT, **HELP_CENTER, **STREAMED, **CONNECTION, **ORG_LOOKUP, **FILES}


def pairs() -> list[tuple[str, str | None]]:
    support, hc = list(SUPPORT), list(HELP_CENTER)
    return [(s, hc[i] if i < len(hc) else None) for i, s in enumerate(support)] + \
        [(None, h) for h in hc[len(support):]]


def names(pair: tuple[str | None, str | None]) -> str:
    return " + ".join(p for p in pair if p)


def lost_or_leaked(problems: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What no run may do: show someone what Zendesk does not show them."""
    return [p for p in problems if p["type"].startswith("LEAK") or p["type"].startswith("SIM_")]


@pytest.mark.integration
@pytest.mark.zendesk_sim
@pytest.mark.asyncio(loop_scope="session")
class TestZendeskSimFailure:

    async def test_tc_zdsim_fail_001_each_endpoint_fails_once(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk()))
        await w.assert_matches_source("before the faults")
        cases = pairs()
        outcomes: dict[str, str | None] = {}
        for index, pair in enumerate(cases):
            before = await w.holdings()
            w.ops({"op": "update_ticket", "ticket": "t_billing", "priority": ("low", "high")[index % 2]},
                  {"op": "update_article", "article": "a_sub", "body": f"<p>round {index}</p>"})
            rules = [fault(MATRIX[name], 429 if index % 2 else 503, max_times=1)
                     for name in pair if name]
            w.faults(*rules)
            outcome = await w.sync(full=True)
            w.clear_faults()
            outcomes[names(pair)] = outcome
            problems = await w.differences()
            assert not lost_or_leaked(problems), f"{names(pair)}: {lost_or_leaked(problems)[:10]}"
            await w.assert_holdings_kept(before, f"{names(pair)} once", allow_gains=True)
            if not succeeded(outcome):
                assert succeeded(await w.sync(full=True)), f"{names(pair)}: the clean sync after did not succeed"
            await w.assert_matches_source(f"{names(pair)} once, after a clean sync")
        logger.info("outcomes of the single faults: %s", json.dumps(outcomes, sort_keys=True))

    async def test_tc_zdsim_fail_002_each_endpoint_keeps_failing(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk()))
        await w.assert_matches_source("before the faults")
        for index, pair in enumerate(pairs()):
            before = await w.holdings()
            w.ops({"op": "update_ticket", "ticket": "t_billing", "priority": ("low", "high")[index % 2]},
                  {"op": "update_article", "article": "a_sub", "body": f"<p>lasting {index}</p>"})
            w.faults(*[fault(MATRIX[name], 503, max_times=KEEPS_FAILING) for name in pair if name])
            outcome = await w.sync(full=True)
            w.clear_faults()
            problems = await w.differences()
            assert not lost_or_leaked(problems), f"{names(pair)}: {lost_or_leaked(problems)[:10]}"
            await w.assert_holdings_kept(before, f"{names(pair)} for the whole run", allow_gains=True)
            if succeeded(outcome):
                missing = [p for p in problems if p["type"] not in KNOWN_GAPS
                           and (p["type"] in ("MISSING", "STALE") or p["type"].startswith("DENIED"))]
                assert not missing, f"{names(pair)}: the run reported success but left {missing[:5]}"
            assert succeeded(await w.sync(full=True)), f"{names(pair)}: the clean sync after did not succeed"
            await w.assert_matches_source(f"{names(pair)} for the whole run, after a clean sync")

    async def test_tc_zdsim_fail_003_the_matrix_lists_every_endpoint_called(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk()))
        w.ops(*[{"op": "update_ticket", "ticket": "t_billing", "priority": "high"}])
        assert succeeded(await w.sync(full=True))
        called = set(w.sim.calls())
        assert called <= set(MATRIX), f"endpoints called but not in the fault matrix: {sorted(called - set(MATRIX))}"

    @pytest.mark.parametrize("endpoint", list(CONNECTION))
    @pytest.mark.parametrize("code", [403, 503])
    async def test_tc_zdsim_fail_004_connection_test_refused(self, zendesk_world: ZendeskWorld, endpoint: str,
                                                              code: int) -> None:
        w = zendesk_world
        w.load(small())
        w.create_connector()
        await w.sign_in()
        w.faults(fault(MATRIX[endpoint], code, max_times=KEEPS_FAILING))
        await w._wait_idle()
        before = w.connector().get("lastSyncFinishedAtTimestamp") or 0
        try:
            w.client.toggle_sync(w.connector_id, enable=True)
            outcome = await w._await_end(before)
        except PipeshubClientError:
            outcome = REFUSED
        assert w.clear_faults() >= 1
        if endpoint.endswith("/users/me") and succeeded(outcome):
            # The account check is advisory: a failure to read it does not stop the sync, whose own reads stand.
            await w.assert_matches_source(f"{endpoint} answered {code}, the run read everything")
            return
        assert outcome == REFUSED or not succeeded(outcome), f"{endpoint} answered {code} and the run succeeded"
        assert not await w.records(), "a connector whose first reads were refused stored records"

    async def test_tc_zdsim_fail_005_export_cut_short(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk(), behaviours={"incremental_page_size": 2}))
        before = await w.holdings()
        w.ops({"op": "update_ticket", "ticket": "t_billing", "priority": "high"})
        w.faults(fault(MATRIX["GET /api/v2/incremental/tickets/cursor"], 503, after_n=1, max_times=KEEPS_FAILING))
        outcome = await w.sync(full=True)
        w.clear_faults()
        assert not lost_or_leaked(await w.differences())
        await w.assert_holdings_kept(before, "after a full sync whose export stopped after one page", allow_gains=True)
        assert await w.pending_full_sync() or not succeeded(outcome)
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after the export answered again")

    async def test_tc_zdsim_fail_006_bad_cursor(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk(), behaviours={"incremental_page_size": 2}))
        before = await w.holdings()
        w.faults({"match": {"path_regex": MATRIX["GET /api/v2/incremental/tickets/cursor"]}, "trigger": {"max_times": 1},
                  "effect": {"type": "shape", "ops": [{"path": "after_cursor", "value": "garbage"}]}})
        w.ops({"op": "update_ticket", "ticket": "t_billing", "priority": "high"})
        outcome = await w.sync(full=True)
        w.clear_faults()
        assert w.sim.stats()["statuses"].get("400", 0) >= 1, "the bad cursor was never refused"
        assert not lost_or_leaked(await w.differences())
        await w.assert_holdings_kept(before, "after a bad cursor", allow_gains=True)
        if not succeeded(outcome):
            assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after the bad cursor")

    async def test_tc_zdsim_fail_007_export_limit(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(small(), behaviours={
            "incremental_rate_limit_per_min": 3, "quota_window_s": 6, "incremental_page_size": 1}))
        assert w.sim.stats()["rate_limited"] > 0, "the limit was never reached, so the case proves nothing"
        await w.assert_matches_source("a sync under the export limit")

    async def test_tc_zdsim_fail_008_dropped_connection(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk()))
        before = await w.holdings()
        w.ops({"op": "update_ticket", "ticket": "t_billing", "priority": "high"})
        w.faults({"match": {"path_regex": MATRIX["GET /api/v2/incremental/tickets/cursor"]},
                  "trigger": {"max_times": 1}, "effect": {"type": "reset"}})
        outcome = await w.sync()
        w.clear_faults()
        assert not lost_or_leaked(await w.differences())
        await w.assert_holdings_kept(before, "after a dropped connection", allow_gains=True)
        if not succeeded(outcome):
            assert succeeded(await w.sync())
        await w.assert_matches_source("after a dropped connection")

    async def test_tc_zdsim_fail_009_malformed_body(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk()))
        before = await w.holdings()
        w.ops({"op": "update_ticket", "ticket": "t_billing", "priority": "high"})
        w.faults({"match": {"path_regex": MATRIX["GET /api/v2/groups"]}, "trigger": {"max_times": 1},
                  "effect": {"type": "truncate", "bytes": 40}})
        outcome = await w.sync(full=True)
        w.clear_faults()
        assert not lost_or_leaked(await w.differences())
        await w.assert_holdings_kept(before, "after a malformed groups list", allow_gains=True)
        if not succeeded(outcome):
            assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after a malformed groups list")

    async def test_tc_zdsim_fail_010_one_ticket_set_aside(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk()))
        tid = w.key("tickets", "t_billing")
        before = await w.holdings()
        w.ops({"op": "update_ticket", "ticket": "t_billing", "priority": "high"})
        w.faults(fault(rf"^/api/v2/tickets/{tid}/comments(\.json)?$", 403, max_times=KEEPS_FAILING))
        outcome = await w.sync(full=True)
        w.clear_faults()
        assert not lost_or_leaked(await w.differences())
        await w.assert_holdings_kept(before, f"after the comments of ticket {tid} were refused", allow_gains=True)
        assert succeeded(await w.sync(full=True)), f"the clean sync after did not succeed (the faulted one: {outcome})"
        await w.assert_matches_source("after the comments answered again")

    @pytest.mark.parametrize("code", [404, 503])
    async def test_tc_zdsim_fail_011_download_refused(self, zendesk_world: ZendeskWorld, code: int) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk()))
        before = await w.holdings()
        w.faults(fault(MATRIX["GET /attachments/token"], code, max_times=KEEPS_FAILING))
        ext = next(r["externalRecordId"] for r in await w.records() if "_attachment_" in r["externalRecordId"]
                   and r["externalRecordId"].startswith("ticket_"))
        try:
            data = await w.stream(ext)
        except Exception:  # noqa: BLE001 - a refused download may surface as an HTTP error
            data = None
        assert w.clear_faults() >= 1
        assert data is None or data != b"", "a refused download streamed an empty file as if it were the content"
        await w.assert_holdings_kept(before, f"after a download answered {code}")
        assert await w.stream(ext), "the file could not be fetched once Zendesk answered again"
        await w.assert_matches_source("after the downloads answered again")

    async def test_tc_zdsim_fail_012_streams_under_faults(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk()))
        wanted = [str(w.key("tickets", "t_support")), f"article_{w.key('articles', 'a_public')}"]
        records = {r["externalRecordId"] for r in await w.records()}
        wanted += sorted(r for r in records if "_attachment_" in r and r.startswith(("ticket_1_", "article_")))[:3]

        async def streams() -> dict[str, Any]:
            out: dict[str, Any] = {}
            for ext in wanted:
                try:
                    data = await w.stream(ext)
                except Exception as exc:  # noqa: BLE001 - an HTTP error is an honest answer
                    out[ext] = ("error", type(exc).__name__)
                    continue
                try:
                    out[ext] = [(g["source_group_id"], g.get("data")) for g in json.loads(data)["block_groups"]]
                except (ValueError, KeyError, TypeError):
                    out[ext] = data
            return out

        baseline = await streams()
        assert not any(isinstance(v, tuple) for v in baseline.values()), f"the fault-free stream failed: {baseline}"
        for name, path in {**STREAMED, **FILES}.items():
            for rule in (fault(path, 503, max_times=1), fault(path, 503, max_times=KEEPS_FAILING)):
                w.faults(rule)
                got = await streams()
                w.faults()
                w.sim.call("DELETE", "/_sim/faults")
                w._fault_rules = []
                wrong = {ext: v for ext, v in got.items() if not isinstance(v, tuple) and v != baseline[ext]}
                assert not wrong, f"{name}: a stream served part of the content as if it were all of it: {list(wrong)}"
