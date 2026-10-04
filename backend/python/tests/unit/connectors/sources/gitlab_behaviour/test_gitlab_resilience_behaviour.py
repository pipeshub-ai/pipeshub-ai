"""Tokens, rate limits, transient errors and partial failures, through the real SDK stack."""

from __future__ import annotations

import asyncio
import logging
import sys
import types
from unittest.mock import MagicMock

import pytest
from gitlab_world import ALICE, API, BOB, CONNECTOR_ID, WEB, build_acme


def test_the_gitlab_sdk_and_http_clients_are_real_imports_not_test_doubles() -> None:
    import gitlab
    import gitlab.v4.objects
    import httpx
    import requests

    import app.sources.client.gitlab.gitlab as client_module
    import app.sources.external.gitlab.gitlab_data_source as data_source_module

    for module in (gitlab, gitlab.client, gitlab.v4.objects, requests, httpx, client_module, data_source_module):
        assert isinstance(module, types.ModuleType) and not isinstance(module, MagicMock), module
        assert (module.__file__ or "").endswith(".py"), module
    assert sys.modules["gitlab"] is gitlab
    assert client_module.gitlab.Gitlab is gitlab.Gitlab
    assert issubclass(client_module._TokenSafeSession, requests.Session)


async def test_the_connector_runs_on_the_real_sdk(harness, gitlab) -> None:
    import gitlab as python_gitlab

    import app.sources.client.gitlab.gitlab as client_module

    build_acme(gitlab)
    connector = await harness.connector()

    sdk = connector.data_source._sdk
    assert type(sdk) is python_gitlab.Gitlab
    assert isinstance(sdk.session, client_module._TokenSafeSession)
    assert await connector.test_connection_and_access()


async def test_an_expired_oauth_token_is_refreshed_once_and_the_sync_carries_on(harness, gitlab, db, token_refresher) -> None:
    build_acme(gitlab)
    gitlab.token_for(1, "token-2")
    connector = await harness.connector()
    gitlab.valid_tokens.discard("token-1")
    gitlab.valid_tokens.discard("token-2")  # becomes valid only once the refresher rotates it

    await harness.sync(connector)

    assert len(token_refresher.calls) == 1
    assert token_refresher.calls[0][2] == "refresh-0"
    assert "/acme/web/-/blob/HEAD/README.md" in db.records
    graphql_tokens = {r.token for r in gitlab.calls("POST", r"^/api/graphql$")}
    assert graphql_tokens == {"token-2"}


async def test_a_revoked_personal_access_token_fails_the_sync_and_leaves_stored_access_alone(harness, gitlab, db,
                                                                                            token_refresher) -> None:
    build_acme(gitlab)
    harness.use_personal_access_token()
    connector = await harness.sync()
    access_before = {g: db.group_access(g) for g in db.record_groups}
    records_before = set(db.records)

    gitlab.valid_tokens.clear()
    with pytest.raises(Exception, match="fetching projects"):
        await harness.sync(connector)

    assert token_refresher.calls == []
    assert {g: db.group_access(g) for g in db.record_groups} == access_before
    assert set(db.records) == records_before


async def test_a_rate_limited_call_waits_for_retry_after_and_then_succeeds(harness, gitlab, db, sdk_sleeps) -> None:
    build_acme(gitlab)
    gitlab.add_issue(WEB, 1, "Issue", "2026-09-01T10:00:00Z")
    gitlab.fail("GET", rf"^/api/v4/projects/{WEB}/issues$", 429, times=2, headers={"Retry-After": "3"})

    await harness.sync()

    assert sdk_sleeps == [3, 3]
    assert "11001" in db.records


async def test_an_unreasonable_retry_after_is_capped(harness, gitlab, db, sdk_sleeps) -> None:
    build_acme(gitlab)
    gitlab.add_issue(WEB, 1, "Issue", "2026-09-01T10:00:00Z")
    gitlab.fail("GET", rf"^/api/v4/projects/{WEB}/issues$", 429, times=1, headers={"Retry-After": "3600"})

    await harness.sync()

    assert sdk_sleeps == [60]
    assert "11001" in db.records


async def test_transient_server_errors_are_retried_with_backoff(harness, gitlab, db, sdk_sleeps) -> None:
    build_acme(gitlab)
    gitlab.fail("GET", rf"^/api/v4/projects/{WEB}/members/all$", 502, times=2)

    await harness.sync()

    assert sdk_sleeps[:2] == [0.1, 0.2]
    assert "bob@example.com" in db.group_access(f"{WEB}-work-items")


async def test_one_project_that_cannot_be_read_does_not_stop_the_others(harness, gitlab, db, checkpoints) -> None:
    build_acme(gitlab)
    gitlab.add_issue(WEB, 1, "Web issue", "2026-09-01T10:00:00Z")
    gitlab.add_issue(API, 1, "Api issue", "2026-09-01T10:00:00Z")
    gitlab.fail("GET", rf"^/api/v4/projects/{WEB}/issues$", 403)

    await harness.sync()

    assert "12001" in db.records
    assert "11001" not in db.records
    assert checkpoints.issues_checkpoint(WEB) is None
    assert "/acme/web/-/blob/HEAD/README.md" in db.records


async def test_an_insecure_instance_url_is_refused_before_any_token_is_sent(harness, gitlab) -> None:
    build_acme(gitlab)
    harness.stored["auth"]["instanceUrl"] = "http://gitlab.internal.example.com"
    from app.connectors.sources.gitlab.connector import GitLabConnector

    connector = GitLabConnector(
        logging.getLogger("gitlab-behaviour"), harness.db, None, harness.config, CONNECTOR_ID, "team", "creator-1",
    )
    harness.connectors.append(connector)

    assert await connector.init() is False
    assert gitlab.requests == []


async def test_the_token_is_not_forwarded_when_gitlab_redirects_to_another_host(harness, gitlab, db) -> None:
    build_acme(gitlab)
    harness.use_personal_access_token()
    gitlab.fail("GET", rf"^/api/v4/projects/{WEB}$", 302, times=1,
                headers={"Location": f"https://elsewhere.example.net/api/v4/projects/{WEB}"})

    await harness.sync()

    offsite = [r for r in gitlab.requests if r.host == "elsewhere.example.net"]
    assert offsite, "the redirect was never followed"
    assert all(r.token is None for r in offsite)


async def test_a_call_that_fails_with_the_old_token_after_a_refresh_reuses_the_new_one(harness, gitlab,
                                                                                         token_refresher) -> None:
    build_acme(gitlab)
    gitlab.token_for(1, "token-2")
    connector = await harness.connector()
    gitlab.valid_tokens.discard("token-1")
    gitlab.valid_tokens.discard("token-2")
    # bob's lookup leaves with the old token but only comes back after alice's 401 has refreshed it.
    gitlab.hold("GET", rf"^/api/v4/users/{BOB}$",
                until=lambda: connector.external_client.get_client().get_token() == "token-2")

    results = await asyncio.gather(*(
        connector.runtime.ds_call(connector.data_source.get_user, uid) for uid in (ALICE, BOB)
    ))

    assert all(r.success for r in results)
    assert len(token_refresher.calls) == 1


async def test_a_project_listing_that_fails_part_way_stops_the_sync_and_keeps_stored_access(harness, gitlab,
                                                                                           db) -> None:
    build_acme(gitlab)
    gitlab.max_per_page = 1
    await harness.sync()
    access_before = {g: db.group_access(g) for g in db.record_groups}

    gitlab.fail("GET", r"^/api/v4/projects$", 500, skip=1)
    with pytest.raises(Exception, match="fetching projects"):
        await harness.sync()

    assert {g: db.group_access(g) for g in db.record_groups} == access_before
