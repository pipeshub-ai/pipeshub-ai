"""Rate limits, 5xx, bad gateways, timeouts, dropped connections, revoked tokens, store
and config failures. Nothing may be lost for good, and a sync that did not work must
never report success.

Every test runs on Neo4j and ArangoDB (NEO4J_IT_URI / ARANGO_IT_URL) against the simulated
GitHub in ``sim/``; see README.md.
"""

import pytest

from tests.integration.github_teams.helpers import fail, issues_listing, on
from tests.integration.github_teams.sim.model import Fault

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]


async def test_one_bad_gateway_is_retried(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    gh.add_issue(repo, "Issue")
    fail(world, issues_listing(repo), 502, times=1)
    await world.sync()
    assert await world.names("TICKET") == ["Issue"]


async def test_one_dropped_connection_is_retried(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    gh.add_issue(repo, "Issue")
    gh.faults.append(Fault(on(issues_listing(repo)), kind="drop", times=1))
    await world.sync()
    assert await world.names("TICKET") == ["Issue"]


async def test_timeout_is_retried(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    gh.add_issue(repo, "Issue")
    world.http_timeout = 0.5
    gh.faults.append(Fault(on(issues_listing(repo)), kind="slow", seconds=2, times=1))
    await world.sync()
    assert await world.names("TICKET") == ["Issue"]


async def test_secondary_rate_limit_is_waited_out(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    gh.add_issue(repo, "Issue")
    gh.faults.append(Fault(on(issues_listing(repo)), kind="secondary", seconds=30, times=1))
    await world.sync()
    assert await world.names("TICKET") == ["Issue"]
    assert any(s >= 30 for s in world.sleeps)


async def test_exhausted_hourly_limit_fails_the_sync_and_loses_nothing(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    gh.add_issue(repo, "Before")
    await world.sync()
    gh.add_issue(repo, "During")
    gh.faults.append(Fault(on(issues_listing(repo)), kind="primary", seconds=1800, times=None))
    await world.sync(expect_error=True)
    gh.faults.clear()
    await world.sync()
    assert await world.names("TICKET") == ["Before", "During"]


async def test_revoked_token_fails_the_sync(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    gh.add_issue(repo, "Issue")
    await world.sync()
    before = sorted((await world.live_docs()).keys())
    gh.sync_token.revoked = True
    await world.sync(expect_error=True)
    assert sorted((await world.live_docs()).keys()) == before


async def test_persistent_5xx_on_pull_requests_fails_the_sync(world, org) -> None:
    gh, repo = world.gh, org["repo"]
    gh.add_pull(repo, "PR")
    fail(world, f"api.github.com/repos/{repo.full_name}/pulls", 503)
    await world.sync(expect_error=True)
    gh.faults.clear()
    await world.sync()
    assert await world.names("PULL_REQUEST") == ["PR"]


async def test_store_failure_fails_the_sync_and_is_redone(world, org, monkeypatch) -> None:
    gh, repo = world.gh, org["repo"]
    gh.add_issue(repo, "Issue")
    original = world.processor.on_new_records
    calls = {"n": 0}

    async def flaky(records: list) -> None:
        if any(getattr(r, "record_type", None) == "TICKET" for r, _ in records) and calls["n"] == 0:
            calls["n"] += 1
            raise RuntimeError("graph store unavailable")
        return await original(records)

    monkeypatch.setattr(world.processor, "on_new_records", flaky)
    await world.sync(expect_error=True)
    await world.sync()
    assert await world.names("TICKET") == ["Issue"]


async def test_config_store_failure_does_not_widen_the_scope(world, org) -> None:
    """The sync filter names one repository. If the config store cannot be read when the
    filters are loaded, the sync must not fall back to every repository the token can
    see (it would index and grant repositories the admin never selected)."""
    gh = world.gh
    other = gh.add_repo(org["org"], "payroll")
    gh.add_issue(other, "Salaries 2026")
    gh.add_issue(org["repo"], "Issue")
    await world.start()
    world.config_service.fail_reads = 2  # the token refresh check and the filter load
    await world.sync(expect_error=True)
    assert "Salaries 2026" not in await world.names("TICKET")
