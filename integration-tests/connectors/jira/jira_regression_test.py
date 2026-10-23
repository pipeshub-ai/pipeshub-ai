# pyright: ignore-file

"""
Jira Cloud connector – regression tests for the sync, deletion, content and access fixes
=========================================================================================

Each test pins one behaviour a past bug broke, end to end through the product (connector
service, processor, graph, record stream). Same contract as ``jira_integration_test.py``:
every test that writes runs its own throw-away connector scoped to the mutation project,
creates ``PHIT-<run>-<Kind>-<hex>`` tickets there, asserts by external id and deletes what
it made; the primary project is only ever read.

  TC-JIRA-REG-001  a comment restricted to a role, and a file shown only in it, are not served
  TC-JIRA-REG-002  every comment of a discussion longer than one page (100) is served
  TC-JIRA-REG-003  an image shown inline in the description is not a separate file record
  TC-JIRA-REG-004  an issue deleted in Jira is removed by the next incremental sync
  TC-JIRA-REG-005  an attachment removed further back than the changelog page is removed
  TC-JIRA-REG-006  an issue moved out of the selected projects is removed       (move target)
  TC-JIRA-REG-007  a parent in an unselected project is not stored with its title (move target)
  TC-JIRA-REG-008  a project taken out of the filter is removed with its records
  TC-JIRA-REG-009  narrowing the created-date filter removes what falls outside it
  TC-JIRA-REG-010  a selected project keeps syncing after its key changes        (rename key)
  TC-JIRA-REG-011  a base URL that is not an Atlassian site is refused before any request
  TC-JIRA-REG-012  a second full sync of unchanged Jira changes nothing

Optional settings (the tests that need them skip without them):

  JIRA_IT_MOVE_TARGET_PROJECT_KEY  a project the account may create and delete issues in,
                                   never selected by these connectors (moves and the parent)
  JIRA_IT_RENAME_PROJECT_KEY       a throw-away project whose key the run may change and
                                   change back (needs Administer Projects)

A setup step the source refuses (a site or simulator without comment visibility, bulk move
or project rename) skips the test instead of failing it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

import aiohttp
import pytest
import pytest_asyncio

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.sources.external.jira.jira import JiraDataSource  # type: ignore[import-not-found]  # noqa: E402
from helper.graph_provider import GraphProviderProtocol  # noqa: E402
from helper.graph_provider_utils import async_poll_until, wait_for_sync_completion  # noqa: E402
from pipeshub_client import PipeshubClient, PipeshubClientError  # type: ignore[import-not-found]  # noqa: E402

from connectors.jira.constants import (  # noqa: E402
    JIRA_FILTER_DATE_CUT_MS,
    JIRA_IT_RUN_ID,
    artifact_summary,
)
from connectors.jira.jira_integration_test import (  # noqa: E402
    _adf,
    _dedicated_connector,
    _delete_artifact_issue,
    _jira_auth_config,
    _pk,
    _record_present,
    _sync_filters,
    _sync_until,
)
from connectors.jira.jira_test_utils import (  # noqa: E402
    can_delete_issues_in,
    jira_api_call_with_retry,
    jira_artifacts,
    pick_mutation_project,
)

logger = logging.getLogger("jira-regression-test")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.jira,
    pytest.mark.asyncio(loop_scope="session"),
]

MOVE_TARGET_KEY = os.getenv("JIRA_IT_MOVE_TARGET_PROJECT_KEY", "").strip()
RENAME_KEY = os.getenv("JIRA_IT_RENAME_PROJECT_KEY", "").strip()


# =============================================================================
# Jira calls the generated datasource does not cover well
# =============================================================================


class JiraRaw:
    """Basic-auth calls on the test's own URL: v2 wiki markup (the only way text can name an
    attachment), multipart uploads, bulk move and project key changes."""

    def __init__(self) -> None:
        self.base = (os.getenv("JIRA_TEST_BASE_URL") or "").rstrip("/")
        self.auth = aiohttp.BasicAuth(os.getenv("JIRA_TEST_EMAIL") or "", os.getenv("JIRA_TEST_API_TOKEN") or "")

    async def call(self, method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
        headers = {"Accept": "application/json", **kwargs.pop("headers", {})}
        for attempt in range(5):
            async with aiohttp.ClientSession(auth=self.auth) as session:
                async with session.request(method, f"{self.base}{path}", headers=headers, **kwargs) as resp:
                    if resp.status == 429 and attempt < 4:
                        await asyncio.sleep(float(resp.headers.get("Retry-After") or 2 ** attempt))
                        continue
                    body = await resp.json(content_type=None) if resp.content_length != 0 else None
                    return resp.status, body
        return 429, None

    async def comment(self, key: str, text: str, *, role: Optional[str] = None) -> tuple[int, Any]:
        payload: dict[str, Any] = {"body": text}
        if role:
            payload["visibility"] = {"type": "role", "value": role}
        return await self.call("POST", f"/rest/api/2/issue/{key}/comment", json=payload)

    async def describe(self, key: str, wiki: str) -> int:
        status, _ = await self.call("PUT", f"/rest/api/2/issue/{key}", json={"fields": {"description": wiki}})
        return status

    async def attach(self, key: str, name: str, content: bytes, mime: str = "text/plain") -> Optional[str]:
        form = aiohttp.FormData()
        form.add_field("file", content, filename=name, content_type=mime)
        status, body = await self.call(
            "POST", f"/rest/api/3/issue/{key}/attachments", data=form, headers={"X-Atlassian-Token": "no-check"},
        )
        if status != 200 or not body:
            return None
        return str(body[0]["id"])

    async def move(self, key: str, target_key: str, issue_type: str) -> Optional[str]:
        """Move with the bulk move API; the issue's new key, or None when the site refuses."""
        status, project = await self.call("GET", f"/rest/api/3/project/{target_key}", params={"expand": "issueTypes"})
        if status != 200:
            return None
        type_id = next((t["id"] for t in project.get("issueTypes", []) if t.get("name") == issue_type), None)
        if not type_id:
            return None
        status, task = await self.call("POST", "/rest/api/3/bulk/issues/move", json={
            "sendBulkNotification": False,
            "targetToSourcesMapping": {f"{target_key},{type_id}": {
                "issueIdsOrKeys": [key], "inferClassificationDefaults": True, "inferFieldDefaults": True,
                "inferStatusDefaults": True, "inferSubtaskTypeDefault": True}},
        })
        if status not in (200, 201) or not task or not task.get("taskId"):
            return None
        for _ in range(60):
            status, progress = await self.call("GET", f"/rest/api/3/bulk/queue/{task['taskId']}")
            if progress and progress.get("status") in ("COMPLETE", "FAILED", "CANCELLED"):
                break
            await asyncio.sleep(2)
        if not progress or progress.get("status") != "COMPLETE":
            return None
        status, issue = await self.call("GET", f"/rest/api/3/issue/{key}", params={"fields": "project"})
        return issue.get("key") if status == 200 and issue else None


async def _create_issue(
    jira: JiraDataSource, project_key: str, issue_type: str, kind: str, description: str,
    parent_key: Optional[str] = None,
) -> tuple[str, str]:
    """(id, key) of a new ticket, registered for the teardown reap."""
    fields: dict[str, Any] = {
        "project": {"key": project_key}, "summary": artifact_summary(kind),
        "issuetype": {"name": issue_type}, "description": _adf(description),
    }
    if parent_key:
        fields["parent"] = {"key": parent_key}
    resp = await jira_api_call_with_retry(
        jira.create_issue, fields=fields, context=f"{kind} create_issue", retry_server_errors=False,
    )
    if resp.status not in (200, 201):
        raise AssertionError(f"{kind}: create_issue in {project_key} failed: HTTP {resp.status}")
    data = resp.json()
    jira_artifacts.register(str(data["id"]), data["key"])
    return str(data["id"]), data["key"]


def _marker(label: str) -> str:
    return f"REG{JIRA_IT_RUN_ID}{label}{uuid.uuid4().hex[:6]}".upper()


async def _served_text(
    pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol, connector_id: str, external_id: str,
) -> str:
    """What the product streams to the indexer and to chat for this record."""
    record = await graph_provider.get_record_by_external_id(connector_id, external_id)
    assert record is not None, f"record {external_id} is not synced"
    resp = pipeshub_client.stream_record(record.id)
    assert resp.status_code == 200, f"stream of {external_id}: HTTP {resp.status_code}"
    return resp.text


def _doc(d: Any, *names: str) -> Any:
    for name in names:
        value = d.get(name) if isinstance(d, dict) else getattr(d, name, None)
        if value is not None:
            return value
    return None


async def _files_of(graph_provider: GraphProviderProtocol, connector_id: str, issue_id: str) -> dict[str, str]:
    """File name -> external id of the FILE records under one ticket."""
    files = await graph_provider.fetch_records_by_type(connector_id, "FILE")
    return {
        str(_doc(f, "recordName", "record_name")): str(_doc(f, "externalRecordId", "external_record_id"))
        for f in files
        if str(_doc(f, "externalParentId", "parent_external_record_id")) == issue_id
    }


async def _external_ids(graph_provider: GraphProviderProtocol, connector_id: str, record_type: str) -> set[str]:
    return {
        str(_doc(r, "externalRecordId", "external_record_id"))
        for r in await graph_provider.fetch_records_by_type(connector_id, record_type)
        if not _doc(r, "isPlaceholder", "is_placeholder")
    }


async def _poll(condition: Any, description: str, timeout: int = 120) -> None:
    await async_poll_until(condition, timeout=timeout, interval=5, description=description)


# =============================================================================
# Scope: where these tests may write (no sync of the shared projects)
# =============================================================================


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def reg_scope(jira_datasource: JiraDataSource) -> dict[str, Any]:
    keys = [k.strip() for k in os.getenv("JIRA_TEST_PROJECT_KEYS", "").split(",") if k.strip()]
    if not keys:
        pytest.skip("JIRA_TEST_PROJECT_KEYS is not set")
    issue_types: dict[str, Optional[str]] = {}
    deletable: dict[str, bool] = {}
    for key in keys:
        resp = await jira_api_call_with_retry(
            jira_datasource.get_create_issue_meta, projectKeys=[key], expand="projects.issuetypes",
            context=f"createmeta {key}",
        )
        names = [
            str(t.get("name")) for p in ((resp.json() or {}).get("projects") or []) if resp.status == 200
            for t in p.get("issuetypes") or [] if not t.get("subtask")
        ]
        issue_types[key] = next((n for n in ("Task", "Story", "Bug") if n in names), names[0] if names else None)
        deletable[key] = await can_delete_issues_in(jira_datasource, key)
    mutation_key, issue_type = pick_mutation_project(keys, issue_types, deletable)
    if not deletable.get(mutation_key):
        pytest.skip(f"the IT account cannot delete issues in {mutation_key}; these tests would leak tickets")
    # A test writes in an optional project only when it can delete there what it made.
    can_delete_in = {key: await can_delete_issues_in(jira_datasource, key) for key in {MOVE_TARGET_KEY, RENAME_KEY} if key}
    return {"primary_key": keys[0], "keys": keys, "mutation_key": mutation_key, "issue_type": issue_type,
            "can_delete_in": can_delete_in}


def _require_deletable(reg_scope: dict[str, Any], project_key: str) -> None:
    if not reg_scope["can_delete_in"].get(project_key):
        pytest.skip(f"the IT account cannot delete issues in {project_key}; the test would leak tickets")


@asynccontextmanager
async def _connector(
    pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol, kind: str, project_keys: list[str],
    **filters: Any,
) -> AsyncIterator[str]:
    async with _dedicated_connector(
        pipeshub_client, graph_provider,
        name=f"jira-reg-{kind}-{JIRA_IT_RUN_ID}-{uuid.uuid4().hex[:6]}",
        filters=_sync_filters(project_keys=_pk("in", project_keys), **filters),
    ) as connector_id:
        yield connector_id


async def _cleanup(jira: JiraDataSource, made: list[tuple[str, str]], context: str) -> None:
    for issue_id, issue_key in reversed(made):
        await _delete_artifact_issue(jira, issue_id=issue_id, issue_key=issue_key, context=context)


# =============================================================================
# Content: what the product serves for an issue
# =============================================================================


class TestJiraServedContent:
    async def test_tc_jira_reg_001_restricted_comment_and_its_file_are_not_served(
        self, reg_scope: dict[str, Any], jira_datasource: JiraDataSource,
        pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-JIRA-REG-001: a comment visible only to a project role is not in the issue's
        content, and a file shown only in that comment gets no record; public ones do."""
        raw, made = JiraRaw(), []
        public, secret = _marker("PUB"), _marker("SEC")
        try:
            issue_id, key = await _create_issue(
                jira_datasource, reg_scope["mutation_key"], reg_scope["issue_type"], "RegComment", "restricted comment")
            made.append((issue_id, key))
            public_file = await raw.attach(key, f"{public}.txt", b"public file")
            secret_file = await raw.attach(key, f"{secret}.txt", b"secret file")
            if not public_file or not secret_file:
                pytest.skip("the source does not accept attachments")
            status, _ = await raw.comment(key, f"{public} see [^{public}.txt]")
            assert status in (200, 201), f"public comment: HTTP {status}"
            status, _ = await raw.comment(key, f"{secret} see [^{secret}.txt]", role="Administrators")
            if status not in (200, 201):
                pytest.skip(f"the source refuses a role-restricted comment (HTTP {status})")

            async with _connector(pipeshub_client, graph_provider, "comment", [reg_scope["mutation_key"]]) as cid:
                await _poll(lambda: _record_present(graph_provider, cid, issue_id), f"{key} synced")
                text = await _served_text(pipeshub_client, graph_provider, cid, issue_id)
                assert public in text, "the public comment is missing from the served content"
                assert secret not in text, "a role-restricted comment is served to everyone who can open the issue"
                files = await _files_of(graph_provider, cid, issue_id)
                assert f"{public}.txt" in files
                assert f"{secret}.txt" not in files, "a file shown only in a restricted comment has a record"
        finally:
            await _cleanup(jira_datasource, made, "TC-JIRA-REG-001")

    async def test_tc_jira_reg_002_every_comment_of_a_long_discussion_is_served(
        self, reg_scope: dict[str, Any], jira_datasource: JiraDataSource,
        pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-JIRA-REG-002: comments past the first page of 100 are served too."""
        raw, made, tag, count = JiraRaw(), [], _marker("LONG"), 105
        try:
            issue_id, key = await _create_issue(
                jira_datasource, reg_scope["mutation_key"], reg_scope["issue_type"], "RegLong", "long discussion")
            made.append((issue_id, key))
            for n in range(count):
                status, _ = await raw.comment(key, f"{tag}C{n:03d}")
                assert status in (200, 201), f"comment {n}: HTTP {status}"
            async with _connector(pipeshub_client, graph_provider, "long", [reg_scope["mutation_key"]]) as cid:
                await _poll(lambda: _record_present(graph_provider, cid, issue_id), f"{key} synced")
                text = await _served_text(pipeshub_client, graph_provider, cid, issue_id)
                missing = [n for n in range(count) if f"{tag}C{n:03d}" not in text]
                assert not missing, f"{len(missing)} comment(s) not served, first: {missing[:5]}"
        finally:
            await _cleanup(jira_datasource, made, "TC-JIRA-REG-002")

    async def test_tc_jira_reg_003_an_inline_image_is_not_a_separate_file(
        self, reg_scope: dict[str, Any], jira_datasource: JiraDataSource,
        pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-JIRA-REG-003: an image embedded in the description is part of the ticket, while
        a file only linked from it stays a FILE record of its own."""
        raw, made, tag = JiraRaw(), [], _marker("IMG")
        png = bytes.fromhex(
            "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
            "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")
        try:
            issue_id, key = await _create_issue(
                jira_datasource, reg_scope["mutation_key"], reg_scope["issue_type"], "RegImage", "inline image")
            made.append((issue_id, key))
            if not await raw.attach(key, f"{tag}.png", png, "image/png") or not await raw.attach(key, f"{tag}.txt", b"report"):
                pytest.skip("the source does not accept attachments")
            if await raw.describe(key, f"{tag} the chart\n\n!{tag}.png!\n\nand [^{tag}.txt]") not in (200, 204):
                pytest.skip("the source does not take a v2 wiki description")
            async with _connector(pipeshub_client, graph_provider, "image", [reg_scope["mutation_key"]]) as cid:
                await _poll(lambda: _record_present(graph_provider, cid, issue_id), f"{key} synced")
                await _poll(lambda: _has_file(graph_provider, cid, issue_id, f"{tag}.txt"), "the linked file")
                files = await _files_of(graph_provider, cid, issue_id)
                assert f"{tag}.png" not in files, "an inline image was also synced as a separate file"
        finally:
            await _cleanup(jira_datasource, made, "TC-JIRA-REG-003")


async def _has_file(graph_provider: GraphProviderProtocol, connector_id: str, issue_id: str, name: str) -> bool:
    return name in await _files_of(graph_provider, connector_id, issue_id)


# =============================================================================
# Deletion and scope: what leaves Jira or the selection leaves PipesHub
# =============================================================================


class TestJiraRemovals:
    async def test_tc_jira_reg_004_a_deleted_issue_is_removed_by_an_incremental_sync(
        self, reg_scope: dict[str, Any], jira_datasource: JiraDataSource,
        pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-JIRA-REG-004: also without audit-log access (the connector compares issue ids)."""
        made: list[tuple[str, str]] = []
        try:
            issue_id, key = await _create_issue(
                jira_datasource, reg_scope["mutation_key"], reg_scope["issue_type"], "RegDelete", "to be deleted")
            made.append((issue_id, key))
            async with _connector(pipeshub_client, graph_provider, "delete", [reg_scope["mutation_key"]]) as cid:
                await _poll(lambda: _record_present(graph_provider, cid, issue_id), f"{key} synced")
                await _delete_artifact_issue(jira_datasource, issue_id=issue_id, issue_key=key, context="TC-JIRA-REG-004")
                made.clear()

                async def gone() -> bool:
                    return not await _record_present(graph_provider, cid, issue_id)
                await _sync_until(pipeshub_client, graph_provider, cid, gone, f"TC-JIRA-REG-004 {key} removed")
        finally:
            await _cleanup(jira_datasource, made, "TC-JIRA-REG-004")

    async def test_tc_jira_reg_005_an_attachment_removed_long_ago_is_removed(
        self, reg_scope: dict[str, Any], jira_datasource: JiraDataSource,
        pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-JIRA-REG-005: the issue search returns only the latest 40 changelog entries, so a
        removal followed by 45 edits is found by comparing attachment ids instead."""
        raw, made, tag = JiraRaw(), [], _marker("HIST")
        try:
            issue_id, key = await _create_issue(
                jira_datasource, reg_scope["mutation_key"], reg_scope["issue_type"], "RegHistory", "long history")
            made.append((issue_id, key))
            keep = await raw.attach(key, f"{tag}-keep.txt", b"keep")
            drop = await raw.attach(key, f"{tag}-drop.txt", b"drop")
            if not keep or not drop:
                pytest.skip("the source does not accept attachments")
            async with _connector(pipeshub_client, graph_provider, "history", [reg_scope["mutation_key"]]) as cid:
                await _poll(lambda: _record_present(graph_provider, cid, f"attachment_{drop}"), "both files synced")
                status, _ = await raw.call("DELETE", f"/rest/api/3/attachment/{drop}")
                assert status in (200, 204), f"remove attachment: HTTP {status}"
                for n in range(45):
                    resp = await jira_api_call_with_retry(
                        jira_datasource.edit_issue, issueIdOrKey=key,
                        fields={"description": _adf(f"{tag} edit {n + 1}")}, context="TC-JIRA-REG-005 edit",
                    )
                    assert resp.status in (200, 204), f"edit {n + 1}: HTTP {resp.status}"

                async def removed() -> bool:
                    return not await _record_present(graph_provider, cid, f"attachment_{drop}")
                await _sync_until(pipeshub_client, graph_provider, cid, removed, "TC-JIRA-REG-005 file removed")
                assert await _record_present(graph_provider, cid, f"attachment_{keep}"), "the file still in Jira was removed too"
        finally:
            await _cleanup(jira_datasource, made, "TC-JIRA-REG-005")

    @pytest.mark.skipif(not MOVE_TARGET_KEY, reason="JIRA_IT_MOVE_TARGET_PROJECT_KEY is not set")
    async def test_tc_jira_reg_006_an_issue_moved_out_of_the_selection_is_removed(
        self, reg_scope: dict[str, Any], jira_datasource: JiraDataSource,
        pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-JIRA-REG-006: the record does not stay under its old project serving the text it
        has in the project it moved to."""
        _require_deletable(reg_scope, MOVE_TARGET_KEY)
        raw, made, secret = JiraRaw(), [], _marker("MOVED")
        try:
            issue_id, key = await _create_issue(
                jira_datasource, reg_scope["mutation_key"], reg_scope["issue_type"], "RegMove", "moves out")
            made.append((issue_id, key))
            async with _connector(pipeshub_client, graph_provider, "move", [reg_scope["mutation_key"]]) as cid:
                await _poll(lambda: _record_present(graph_provider, cid, issue_id), f"{key} synced")
                new_key = await raw.move(key, MOVE_TARGET_KEY, reg_scope["issue_type"])
                if not new_key:
                    pytest.skip("the source refused the bulk move")
                made[-1] = (issue_id, new_key)
                await jira_api_call_with_retry(
                    jira_datasource.edit_issue, issueIdOrKey=new_key, fields={"description": _adf(secret)},
                    context="TC-JIRA-REG-006 edit after move",
                )

                async def gone() -> bool:
                    return not await _record_present(graph_provider, cid, issue_id)
                await _sync_until(pipeshub_client, graph_provider, cid, gone, f"TC-JIRA-REG-006 {new_key} removed")
        finally:
            await _cleanup(jira_datasource, made, "TC-JIRA-REG-006")

    @pytest.mark.skipif(not MOVE_TARGET_KEY, reason="JIRA_IT_MOVE_TARGET_PROJECT_KEY is not set")
    async def test_tc_jira_reg_007_a_parent_in_an_unselected_project_has_no_title(
        self, reg_scope: dict[str, Any], jira_datasource: JiraDataSource,
        pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-JIRA-REG-007: the child is synced and readable; its parent from outside the
        selection is not stored, or at most as a placeholder without its title."""
        _require_deletable(reg_scope, MOVE_TARGET_KEY)
        made: list[tuple[str, str]] = []
        try:
            try:
                epic_id, epic_key = await _create_issue(
                    jira_datasource, MOVE_TARGET_KEY, "Epic", "RegEpic", "outside epic body")
            except AssertionError as e:
                pytest.skip(f"no Epic in {MOVE_TARGET_KEY}: {e}")
            made.append((epic_id, epic_key))
            try:
                child_id, child_key = await _create_issue(
                    jira_datasource, reg_scope["mutation_key"], reg_scope["issue_type"], "RegChild", "child",
                    parent_key=epic_key)
            except AssertionError as e:
                pytest.skip(f"the source does not allow a parent in another project: {e}")
            made.append((child_id, child_key))
            async with _connector(pipeshub_client, graph_provider, "parent", [reg_scope["mutation_key"]]) as cid:
                await _poll(lambda: _record_present(graph_provider, cid, child_id), f"{child_key} synced")
                stubs = {str(_doc(s, "external_record_id", "externalRecordId")): s
                         for s in await graph_provider.get_placeholder_records(cid)}
                stub = stubs.get(epic_id)
                assert stub is not None or not await _record_present(graph_provider, cid, epic_id), \
                    "the outside parent was synced as a real record"
                if stub is not None:
                    assert "RegEpic" not in str(_doc(stub, "record_name", "recordName")), \
                        "the title of a parent in an unselected project was stored"
        finally:
            await _cleanup(jira_datasource, made, "TC-JIRA-REG-007")

    async def test_tc_jira_reg_008_a_project_taken_out_of_the_filter_is_removed(
        self, reg_scope: dict[str, Any], pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-JIRA-REG-008: read-only; the dropped project's records and group leave the graph."""
        if len(reg_scope["keys"]) < 2:
            pytest.skip("needs two IT projects")
        keep, drop = reg_scope["keys"][1], reg_scope["keys"][0]
        async with _connector(pipeshub_client, graph_provider, "deselect", [keep, drop]) as cid:
            names = set(await graph_provider.fetch_record_group_names(cid, "PROJECT"))
            assert len(names) >= 2, names
            before = await _external_ids(graph_provider, cid, "TICKET")
            pipeshub_client.update_connector_filters_sync_safe(cid, filters=_sync_filters(project_keys=_pk("in", [keep])))
            await wait_for_sync_completion(pipeshub_client, graph_provider, cid, timeout=600)

            async def narrowed() -> bool:
                return len(await graph_provider.fetch_record_group_names(cid, "PROJECT")) == 1
            await _poll(narrowed, "the dropped project removed", timeout=300)
            after = await _external_ids(graph_provider, cid, "TICKET")
            assert after < before, "no issue left the graph with its project"

    async def test_tc_jira_reg_009_a_narrowed_date_filter_removes_what_falls_outside(
        self, reg_scope: dict[str, Any], jira_datasource: JiraDataSource,
        pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-JIRA-REG-009: read-only on the primary project, split at ``JIRA_FILTER_DATE_CUT_MS``."""
        primary = reg_scope["primary_key"]
        async with _connector(pipeshub_client, graph_provider, "dates", [primary]) as cid:
            before = await _external_ids(graph_provider, cid, "TICKET")
            created_after = {"operator": "is_after", "type": "datetime", "value": {"start": JIRA_FILTER_DATE_CUT_MS, "end": None}}
            pipeshub_client.update_connector_filters_sync_safe(
                cid, filters=_sync_filters(project_keys=_pk("in", [primary]), created=created_after))
            await wait_for_sync_completion(pipeshub_client, graph_provider, cid, timeout=600)
            resp = await jira_api_call_with_retry(
                jira_datasource.search_and_reconsile_issues_using_jql_post,
                jql=f'project = "{primary}" AND created < {JIRA_FILTER_DATE_CUT_MS}', maxResults=50, fields=["created"],
                context="TC-JIRA-REG-009 issues before the cut",
            )
            older = {str(i["id"]) for i in (resp.json() or {}).get("issues", [])}
            if not older & before:
                pytest.skip("no synced issue was created before the cut")

            async def removed() -> bool:
                return not (older & await _external_ids(graph_provider, cid, "TICKET"))
            await _poll(removed, "issues created before the cut removed", timeout=300)


# =============================================================================
# Identity of the site and of a project
# =============================================================================


class TestJiraSiteAndProjectIdentity:
    @pytest.mark.skipif(not RENAME_KEY, reason="JIRA_IT_RENAME_PROJECT_KEY is not set")
    async def test_tc_jira_reg_010_a_project_keeps_syncing_after_its_key_changes(
        self, reg_scope: dict[str, Any], jira_datasource: JiraDataSource,
        pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-JIRA-REG-010: the filter still names the old key; new issues under the new key
        arrive and nothing is duplicated. The key is changed back at the end."""
        _require_deletable(reg_scope, RENAME_KEY)
        raw, made, new_key = JiraRaw(), [], f"{RENAME_KEY[:7]}R"
        renamed = False
        try:
            async with _connector(pipeshub_client, graph_provider, "rename", [RENAME_KEY]) as cid:
                before = await _external_ids(graph_provider, cid, "TICKET")
                status, _ = await raw.call("PUT", f"/rest/api/3/project/{RENAME_KEY}", json={"key": new_key})
                if status != 200:
                    pytest.skip(f"the source refused the key change (HTTP {status})")
                renamed = True
                issue_id, key = await _create_issue(
                    jira_datasource, new_key, reg_scope["issue_type"], "RegRename", "after the rename")
                made.append((issue_id, key))
                await _sync_until(
                    pipeshub_client, graph_provider, cid, lambda: _record_present(graph_provider, cid, issue_id),
                    "TC-JIRA-REG-010 an issue created under the new key")
                after = await _external_ids(graph_provider, cid, "TICKET")
                assert before <= after, "issues synced under the old key were lost"
                assert len(await graph_provider.fetch_record_group_names(cid, "PROJECT")) == 1, "the project was duplicated"
        finally:
            await _cleanup(jira_datasource, made, "TC-JIRA-REG-010")
            if renamed:
                await raw.call("PUT", f"/rest/api/3/project/{new_key}", json={"key": RENAME_KEY})

    @pytest.mark.parametrize("base_url", ["http://169.254.169.254", "https://jira.internal.example"])
    async def test_tc_jira_reg_011_a_base_url_that_is_not_an_atlassian_site_is_refused(
        self, pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol, base_url: str,
    ) -> None:
        """TC-JIRA-REG-011: the email and API token are sent to the base URL itself, so a
        connector pointed anywhere but the Atlassian site must not start."""
        auth = {**_jira_auth_config(), "baseUrl": base_url}
        instance = pipeshub_client.create_connector(
            connector_type="Jira", instance_name=f"jira-reg-url-{JIRA_IT_RUN_ID}-{uuid.uuid4().hex[:6]}",
            scope="team", config={"auth": auth, "filters": _sync_filters()}, auth_type="API_TOKEN",
        )
        connector_id = instance.connector_id
        try:
            refused: Optional[str] = None
            try:
                pipeshub_client.toggle_sync(connector_id, enable=True)
            except PipeshubClientError as e:
                refused = str(e)
            if refused is not None:
                assert "atlassian.net" in refused, f"refused for another reason: {refused}"
            pipeshub_client.wait(20)
            assert await graph_provider.count_records(connector_id) == 0, "the connector synced from that URL"
        finally:
            try:
                pipeshub_client.toggle_sync(connector_id, enable=False)
            except PipeshubClientError:
                pass
            pipeshub_client.delete_connector(connector_id)

    async def test_tc_jira_reg_012_a_second_full_sync_changes_nothing(
        self, reg_scope: dict[str, Any], pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-JIRA-REG-012: read-only; a full resync of unchanged issues keeps every record,
        adds none and leaves the project and its access as they were."""
        primary = reg_scope["primary_key"]
        async with _connector(pipeshub_client, graph_provider, "stable", [primary]) as cid:
            tickets, files = await _external_ids(graph_provider, cid, "TICKET"), await _external_ids(graph_provider, cid, "FILE")
            grants = await graph_provider.count_permission_edges_to_record_groups(cid)
            pipeshub_client.resync_connector(cid, full_sync=True)
            await wait_for_sync_completion(pipeshub_client, graph_provider, cid, timeout=600)
            assert await _external_ids(graph_provider, cid, "TICKET") == tickets
            assert await _external_ids(graph_provider, cid, "FILE") == files
            assert await graph_provider.count_permission_edges_to_record_groups(cid) == grants
