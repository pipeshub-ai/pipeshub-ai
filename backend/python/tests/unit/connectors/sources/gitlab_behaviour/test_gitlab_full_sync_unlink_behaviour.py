"""What a full sync leaves unlinked: only what is gone from GitLab, never what it skipped.

After a full sync that returns normally, ``run_sync_task`` unlinks every stored node
the sync neither wrote nor noted as kept, and the walk itself deletes the code files
it did not meet. A node GitLab still has must survive both.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from gitlab_server_fake import GUEST
from gitlab_world import API, DAVE, WEB, blob, build_acme

from app.connectors.core.sync.full_sync_coverage import FullSyncCoverage
from app.connectors.sources.gitlab.connector import GitLabSyncIncompleteError

if TYPE_CHECKING:
    from gitlab_harness import GitLabHarness
    from gitlab_store_fakes import FakeCheckpointStore, FakeRecordsDb

ERIN = 6
API_MAIN = blob("main.go", "acme/platform/api")


async def full_sync(harness: GitLabHarness, checkpoints: FakeCheckpointStore, db: FakeRecordsDb) -> set[str]:
    """A full sync as ``run_sync_task`` runs it: sync points reset first, the unlink after.

    Returns the external ids it removes: deleted by the connector during the run, or
    unlinked after it. A run that fails or reports its coverage incomplete unlinks nothing.
    """
    checkpoints.points.clear()
    ids_before = {r.id: ext for ext, r in db.records.items()}
    deleted_before = len(db.deleted)
    db.begin_full_sync()
    connector = await harness.connector()
    failed = False
    try:
        await connector.run_sync()
    except GitLabSyncIncompleteError:
        failed = True
    task = connector._code_file_timestamp_backfill_task
    if task is not None:
        await task
    removed = {ids_before[i] for i in db.deleted[deleted_before:] if i in ids_before}
    coverage = getattr(connector, "full_sync_coverage", None)
    coverage = coverage() if callable(coverage) else FullSyncCoverage()
    if failed or not coverage.complete or not coverage.lists_everything:
        return removed
    missed = db.missed_by_full_sync()
    return removed | missed["records"] | missed["recordGroups"] | {f"group:{g}" for g in missed["groups"]}


def code_of(db: FakeRecordsDb, project_path: str) -> set[str]:
    return {ext for ext in db.records if ext.startswith(f"/{project_path}/-/")}


async def test_a_full_sync_unlinks_the_placeholder_of_a_member_gitlab_still_lists_only_if_gone(
    harness, gitlab, db, checkpoints,
) -> None:
    # dave and erin have no public email, so each one's access rides on a placeholder group.
    build_acme(gitlab)
    gitlab.add_user(ERIN, "erin")
    gitlab.projects[API].members[ERIN] = GUEST
    await harness.sync()
    assert {str(DAVE), str(ERIN)} <= set(db.user_groups)

    del gitlab.projects[API].members[ERIN]
    removed = await full_sync(harness, checkpoints, db)

    assert removed & {f"group:{DAVE}"} == set(), "dave is still a Developer of acme/web"
    assert removed & {f"group:{ERIN}"} == {f"group:{ERIN}"}, "erin left acme/platform/api"


async def test_a_full_sync_with_no_change_at_gitlab_removes_nothing(harness, gitlab, db, checkpoints) -> None:
    build_acme(gitlab)
    gitlab.add_user(ERIN, "erin")
    gitlab.projects[API].members[ERIN] = GUEST
    await harness.sync()

    removed = await full_sync(harness, checkpoints, db)

    assert removed == set()


async def test_a_branch_answer_without_a_head_commit_leaves_the_code_linked(harness, gitlab, db, checkpoints) -> None:
    build_acme(gitlab)
    await harness.sync()
    web_code = code_of(db, "acme/web")
    assert web_code

    gitlab.fail("GET", rf"^/api/v4/projects/{WEB}/repository/branches/main$", 200, body={"name": "main", "commit": None})
    gitlab.change_files(API, delete=("main.go",), write={"cmd/run.go": "package main\n"})
    removed = await full_sync(harness, checkpoints, db)

    assert removed & web_code == set()
    assert removed & {API_MAIN} == {API_MAIN}


async def test_a_tree_walk_that_finds_no_tree_on_a_branch_with_a_commit_leaves_the_code_linked(
    harness, gitlab, db, checkpoints,
) -> None:
    build_acme(gitlab)
    await harness.sync()
    web_code = code_of(db, "acme/web")
    assert web_code

    # GraphQL answers 200 with no project (a token without the scope, a path GraphQL does not resolve).
    gitlab.fail("POST", r"^/api/graphql$", 200, body={"data": {"project": None}}, body_contains='"acme/web"')
    gitlab.change_files(API, delete=("main.go",), write={"cmd/run.go": "package main\n"})
    removed = await full_sync(harness, checkpoints, db)

    assert removed & web_code == set()
    assert removed & {API_MAIN} == {API_MAIN}


async def test_an_empty_tree_page_that_says_more_follow_does_not_end_the_walk(harness, gitlab, db, checkpoints) -> None:
    build_acme(gitlab)
    gitlab.graphql_page_size = 2
    await harness.sync()
    web_code = code_of(db, "acme/web")
    assert web_code

    empty_page = {"data": {"project": {"name": "web", "repository": {"rootRef": "main", "paginatedTree": {
        "nodes": [], "pageInfo": {"endCursor": "4", "hasNextPage": True}}}}}}
    gitlab.fail("POST", r"^/api/graphql$", 200, body=empty_page, body_contains='"acme/web"', skip=1, times=1)
    gitlab.change_files(API, delete=("main.go",), write={"cmd/run.go": "package main\n"})
    removed = await full_sync(harness, checkpoints, db)

    assert removed & web_code == set()
    assert removed & {API_MAIN} == {API_MAIN}


async def test_an_empty_tree_page_that_keeps_saying_more_follow_aborts_without_deleting(
    harness, gitlab, db, checkpoints, repo_backoff,
) -> None:
    build_acme(gitlab)
    gitlab.graphql_page_size = 2
    await harness.sync()
    web_ids = {db.records[ext].id for ext in code_of(db, "acme/web")}
    assert web_ids

    empty_page = {"data": {"project": {"name": "web", "repository": {"rootRef": "main", "paginatedTree": {
        "nodes": [], "pageInfo": {"endCursor": "4", "hasNextPage": True}}}}}}
    gitlab.fail("POST", r"^/api/graphql$", 200, body=empty_page, body_contains='"acme/web"', skip=1)
    checkpoints.points.clear()
    deleted_before = len(db.deleted)
    connector = await harness.connector()
    with pytest.raises(GitLabSyncIncompleteError, match="code of project acme/web: the repository walk did not finish"):
        await connector.run_sync()

    assert set(db.deleted[deleted_before:]) & web_ids == set()
    assert repo_backoff == [2.0, 4.0]
    assert checkpoints.code_checkpoint(WEB) is None


async def test_a_default_branch_gitlab_no_longer_finds_leaves_the_code_linked_and_an_empty_repository_unlinks(
    harness, gitlab, db, checkpoints,
) -> None:
    build_acme(gitlab)
    await harness.sync()
    web_code = code_of(db, "acme/web")
    assert web_code

    # acme/web's default branch was renamed after the projects were listed; acme/platform/api is empty now.
    gitlab.fail("GET", rf"^/api/v4/projects/{WEB}/repository/branches/main$", 404, body={"message": "404 Branch Not Found"})
    gitlab.projects[API].commits.clear()
    gitlab.projects[API].default_branch = None
    removed = await full_sync(harness, checkpoints, db)

    assert removed & web_code == set()
    assert removed & {API_MAIN} == {API_MAIN}


async def test_a_repository_emptied_at_gitlab_unlinks_its_code(harness, gitlab, db, checkpoints) -> None:
    build_acme(gitlab)
    await harness.sync()
    web_code = code_of(db, "acme/web")
    assert web_code

    branch_reads = rf"^/api/v4/projects/{WEB}/repository/branches"
    reads_before = len(gitlab.calls("GET", branch_reads))
    gitlab.projects[WEB].commits.clear()
    gitlab.projects[WEB].default_branch = None
    removed = await full_sync(harness, checkpoints, db)

    assert removed & web_code == web_code
    assert len(gitlab.calls("GET", branch_reads)) == reads_before


async def test_a_default_branch_missing_for_one_run_keeps_the_code_for_that_run_only(
    harness, gitlab, db, checkpoints,
) -> None:
    build_acme(gitlab)
    await harness.sync()
    web_code = code_of(db, "acme/web")
    assert web_code

    gitlab.fail("GET", rf"^/api/v4/projects/{WEB}/repository/branches/main$", 404, body={"message": "404 Branch Not Found"})
    assert await full_sync(harness, checkpoints, db) & web_code == set()

    gitlab.clear_faults()
    gitlab.projects[WEB].commits.clear()
    gitlab.projects[WEB].default_branch = None
    assert await full_sync(harness, checkpoints, db) & web_code == web_code
