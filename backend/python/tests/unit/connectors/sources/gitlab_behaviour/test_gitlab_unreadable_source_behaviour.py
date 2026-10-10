"""A GitLab that could not be read is never taken for one with nothing in it (N4GIT-01).

Transport failures (a read timeout on a dropped keep-alive connection, a reset
connection) and persistent server errors are not "not found": the request
never got an answer. Only GitLab's own 404/403 means the object is gone.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest
import requests
from gitlab_world import CONNECTOR_ID, WEB, blob, build_acme

from app.connectors.core.sync.sync_runner import run_sync_task
from app.connectors.sources.gitlab.runtime import GitLabReadError

WEB_PROJECT = r"^/api/v4/projects/acme/web$"
SWEEP_GENERATION = 1_760_000_000_000


def _graph_provider() -> AsyncMock:
    gp = AsyncMock()
    gp.sweep_connector_sync_edges = AsyncMock(return_value=(3, True))
    return gp


def _snapshot(db) -> tuple[dict[str, set[str]], set[str]]:
    return {g: db.group_access(g) for g in db.record_groups}, set(db.records)


class TestTheProjectFilter:
    @pytest.mark.parametrize("error", [requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError])
    async def test_a_project_read_that_fails_once_in_transport_is_retried_and_synced(
        self, harness, gitlab, db, error
    ) -> None:
        build_acme(gitlab)
        harness.set_sync_filter("project_ids", "in", ["acme/web"], "select")
        fault = gitlab.break_transport("GET", WEB_PROJECT, error, times=1)

        connector = await harness.sync()

        assert fault.fired == 1
        assert blob("README.md") in db.records
        assert connector.stored_access_kept is None

    async def test_a_project_that_keeps_timing_out_fails_the_sync_and_keeps_stored_access(
        self, harness, gitlab, db
    ) -> None:
        build_acme(gitlab)
        harness.set_sync_filter("project_ids", "in", ["acme/web"], "select")
        connector = await harness.sync()
        before = _snapshot(db)

        gitlab.break_transport("GET", WEB_PROJECT, requests.exceptions.ReadTimeout)
        with pytest.raises(GitLabReadError, match="acme/web"):
            await harness.sync(connector)

        assert _snapshot(db) == before

    async def test_a_project_that_keeps_answering_5xx_fails_the_sync(self, harness, gitlab, db) -> None:
        build_acme(gitlab)
        harness.set_sync_filter("project_ids", "in", ["acme/web"], "select")
        gitlab.fail("GET", WEB_PROJECT, 502)

        with pytest.raises(GitLabReadError, match="HTTP 502"):
            await harness.sync()

    async def test_a_project_gitlab_says_is_gone_is_skipped_and_the_sync_succeeds(
        self, harness, gitlab, db
    ) -> None:
        build_acme(gitlab)
        harness.set_sync_filter("project_ids", "in", ["acme/web"], "select")
        gitlab.fail("GET", WEB_PROJECT, 404)

        connector = await harness.sync()

        assert f"{WEB}-code-repository" not in db.record_groups
        assert connector.stored_access_kept is None


async def test_a_group_whose_projects_cannot_be_listed_fails_the_sync(harness, gitlab) -> None:
    build_acme(gitlab)
    harness.set_sync_filter("group_ids", "in", ["acme/platform"])
    gitlab.break_transport("GET", r"^/api/v4/groups/acme/platform/projects$", requests.exceptions.ReadTimeout)

    with pytest.raises(GitLabReadError, match="acme/platform"):
        await harness.sync()


async def test_project_members_that_time_out_keep_the_stored_access(harness, gitlab, db) -> None:
    build_acme(gitlab)
    connector = await harness.sync()
    before = _snapshot(db)

    gitlab.break_transport("GET", rf"^/api/v4/projects/{WEB}/members/all$", requests.exceptions.ReadTimeout)
    await harness.sync(connector)

    assert _snapshot(db) == before
    assert "members_all" in (connector.stored_access_kept or "")


async def test_an_issue_listing_that_times_out_marks_stored_access_as_kept(harness, gitlab) -> None:
    build_acme(gitlab)
    gitlab.add_issue(WEB, 1, "Issue", "2026-09-01T10:00:00Z")
    gitlab.break_transport("GET", rf"^/api/v4/projects/{WEB}/issues$", requests.exceptions.ReadTimeout)

    connector = await harness.sync()

    assert "list_issues" in (connector.stored_access_kept or "")


class TestTheFullSyncSweep:
    """Through the runner: a full sync that did not read everything removes nothing."""

    async def _full_sync(self, harness, gp: AsyncMock) -> None:
        connector = await harness.connector()
        await run_sync_task(
            connector, CONNECTOR_ID, gp, logging.getLogger("gitlab-behaviour"), sweep_generation=SWEEP_GENERATION,
        )

    async def test_a_sync_that_read_everything_sweeps(self, harness, gitlab) -> None:
        build_acme(gitlab)
        gp = _graph_provider()

        await self._full_sync(harness, gp)

        gp.sweep_connector_sync_edges.assert_awaited_once_with(CONNECTOR_ID, SWEEP_GENERATION)

    async def test_a_project_that_could_not_be_read_sweeps_nothing(self, harness, gitlab) -> None:
        build_acme(gitlab)
        harness.set_sync_filter("project_ids", "in", ["acme/web"], "select")
        gitlab.break_transport("GET", WEB_PROJECT, requests.exceptions.ReadTimeout)
        gp = _graph_provider()

        with pytest.raises(GitLabReadError):
            await self._full_sync(harness, gp)

        gp.sweep_connector_sync_edges.assert_not_awaited()

    async def test_members_that_could_not_be_read_sweep_nothing(self, harness, gitlab) -> None:
        build_acme(gitlab)
        gitlab.break_transport("GET", rf"^/api/v4/projects/{WEB}/members/all$", requests.exceptions.ReadTimeout)
        gp = _graph_provider()

        await self._full_sync(harness, gp)

        gp.sweep_connector_sync_edges.assert_not_awaited()
