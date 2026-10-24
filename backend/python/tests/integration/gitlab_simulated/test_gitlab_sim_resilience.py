"""Rate limits, 5xx, dropped connections, timeouts, truncated bodies, revoked tokens and
store failures. Nothing may be lost for good, and a sync that did not work must never
report success."""

import contextlib

import pytest

from tests.integration.gitlab_simulated.helpers import ends, fail, on, pid


@pytest.mark.behaviour(rate_limit_per_minute=40)
async def test_rate_limited_sync_completes(world, org) -> None:
    gl, p = world.gl, org["project"]
    for n in range(60):
        gl.add_issue(p, f"Issue {n:02}", author=org["dev"])
    for n in range(10):
        gl.add_mr(p, f"MR {n}", author=org["dev"])
    await world.sync()
    assert any(c.status == 429 for c in gl.calls), "the scenario did not hit the limit"
    diff = await world.diff()
    assert diff.clean(), diff


async def test_transient_5xx_and_dropped_connections_are_retried(world, org) -> None:
    gl, p = world.gl, org["project"]
    gl.add_issue(p, "Issue", author=org["dev"])
    gl.add_mr(p, "MR", author=org["dev"])
    fail(world, ends(f"{pid(p)}/issues"), status=502, times=2)
    fail(world, ends(f"{pid(p)}/merge_requests"), status="drop", times=1)
    fail(world, ends(f"{pid(p)}/members/all"), status="truncate", times=1)
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff


async def test_graphql_tree_page_failing_briefly_is_retried(world, org) -> None:
    fail(world, on("/api/graphql", method="POST"), status=503, times=1)
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff


async def test_issue_listing_down_for_the_whole_sync_does_not_report_success(world, org) -> None:
    gl, p = world.gl, org["project"]
    gl.add_issue(p, "Issue", author=org["dev"])
    fail(world, ends(f"{pid(p)}/issues"), status=502, times=100)
    await world.sync(expect_error=True)
    gl.faults.clear()
    await world.sync()
    assert await world.names("TICKET") == ["Issue"]


async def test_repository_walk_failing_does_not_report_success(world, org) -> None:
    gl = world.gl
    fail(world, on("/api/graphql", method="POST"), status=502, times=100)
    await world.sync(expect_error=True)
    gl.faults.clear()
    await world.sync()
    diff = await world.diff()
    assert diff.clean(), diff


async def test_read_timeout_is_not_reported_as_success(world, org) -> None:
    gl, p = world.gl, org["project"]
    gl.add_issue(p, "Issue", author=org["dev"])
    world.config["auth"]["timeout"] = 1
    await world.restart()
    f = fail(world, ends(f"{pid(p)}/issues"), status="hang", times=3)
    f.headers["seconds"] = "2"
    await world.sync(expect_error=True)
    gl.faults.clear()
    await world.sync()
    assert await world.names("TICKET") == ["Issue"]


async def test_revoked_token_fails_the_sync_and_the_connection_test(world, org) -> None:
    gl, p = world.gl, org["project"]
    gl.add_issue(p, "Issue", author=org["dev"])
    await world.sync()
    gl.sync_token.revoked = True
    assert await world.connector.test_connection_and_access() is False
    await world.sync(expect_error=True)
    diff = await world.diff()
    assert not diff.under, diff


async def test_secrets_never_reach_the_logs(world, org) -> None:
    gl, p = world.gl, org["project"]
    gl.add_issue(p, "Issue", author=org["dev"])
    fail(world, ends(f"{pid(p)}/issues"), status=401, times=3)
    with contextlib.suppress(Exception):
        await world.sync()
    assert gl.sync_token.value not in world.logs.text()
