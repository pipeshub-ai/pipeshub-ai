# pyright: ignore-file

"""
ServiceNow on the simulator – failures, retries and rate limits
===============================================================

  TC-SNSIM-FAIL-001  503s on the article listing are retried: the sync succeeds and loses nothing
  TC-SNSIM-FAIL-002  429s with Retry-After, from a bucket smaller than a sync, are waited out: nothing is lost
  TC-SNSIM-FAIL-003  An hourly rate-limit rule stops the run with a "rate limit" notice, and a sync before the reset
                     stops without calling the instance; once the rule is gone, turning the connector off and on
                     syncs to the end
  TC-SNSIM-FAIL-004  The instance refusing the sign-in (401) stops the run with a re-authorize notice
"""

import pytest
from connectors.servicenow.servicenow_helpers import notices, poll
from connectors.servicenow.sim_world import ServiceNowWorld, fault, succeeded


def _titles(w: ServiceNowWorld) -> list[str]:
    name = str(w.connector().get("name") or "")
    return [str(n.get("title", "")) for n in notices(w.client, name)] if name else []


@pytest.mark.integration
@pytest.mark.servicenow_sim
@pytest.mark.asyncio(loop_scope="session")
class TestServiceNowSimFailures:

    async def test_tc_snsim_fail_001_server_errors_are_retried(self, servicenow_world: ServiceNowWorld) -> None:
        w = servicenow_world
        await w.start(enable=False)
        w.faults(fault(r"^/api/now/table/kb_knowledge$", {"type": "status", "code": 503}, first_n=2))
        assert succeeded(await w.enable())
        w.clear_faults()
        await w.assert_matches_source("after a sync that met 503s")

    async def test_tc_snsim_fail_002_429s_are_waited_out(self, servicenow_world: ServiceNowWorld) -> None:
        w = servicenow_world
        await w.start(enable=False)
        w.faults(fault(r"^/api/now/", {"type": "rate_limit", "capacity": 5, "refill_per_s": 5}, method=None))
        assert succeeded(await w.enable())
        w.clear_faults()
        await w.assert_matches_source("after a sync that met 429s")

    async def test_tc_snsim_fail_003_hourly_rule_stops_then_resumes(self, servicenow_world: ServiceNowWorld) -> None:
        w = servicenow_world
        assert succeeded(await w.start())
        # A rule the instance counts from the start of the hour: a few calls past what the first sync used, so
        # the next run starts and is stopped part way.
        used = w.sim.call("GET", "/_sim/calls")["total"]
        rule = w.sim.mutate({"op": "record_create", "args": {
            "table": "sys_rate_limit_rules", "name": "PipesHub IT", "active": "true", "requests": str(used + 5),
            "_path_regex": "^/api/now/"}})[0]["record"]
        w.sim.advance(counts={"article_edit": 3, "article_create": 2})
        assert not succeeded(await w.sync()), "a sync over the hourly limit cannot have read the whole instance"
        await poll(lambda: _any_title(w, "rate limit"), "no rate-limit notice after the run stopped", 120)
        # Until the reset the connector sends nothing: a sync before it stops again, unsent.
        sent = w.sim.call("GET", "/_sim/calls")["total"]
        assert not succeeded(await w.sync())
        assert w.sim.call("GET", "/_sim/calls")["total"] == sent, "a sync before the reset called the instance"
        # The control API, not the Table API: the rule limits this process's calls too. Turning the connector
        # off and on starts it afresh, without the reset it was waiting for.
        w.sim.mutate({"op": "record_set", "args": {"table": "sys_rate_limit_rules", "sys_id": rule, "active": "false"}})
        assert succeeded(await w.restart())
        await w.assert_matches_source("after the rate-limit rule was removed")

    async def test_tc_snsim_fail_004_refused_sign_in(self, servicenow_world: ServiceNowWorld) -> None:
        w = servicenow_world
        assert succeeded(await w.start())
        w.faults(fault(r"^/api/now/", {"type": "status", "code": 401}, method=None))
        assert not succeeded(await w.sync())
        w.clear_faults()
        await poll(lambda: _any_title(w, "re-authorized"), "no re-authorize notice after a 401", 120)


async def _any_title(w: ServiceNowWorld, words: str) -> bool:
    return any(words in t for t in _titles(w))
