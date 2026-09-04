# pyright: ignore-file

"""GitLab connector fixtures.

- session-scoped ``gitlab_rest`` (skips when the PAT is missing)
- module-scoped ``gitlab_connector``: discovers fixture shapes read-only against the
  primary project, registers a PipesHub connector scoped to exactly that project,
  waits for one sync, snapshots baselines, then tears the connector down.

Setup **never writes to the primary project**. Its only writes are to the mutation
project, and only to reap artifacts leaked by runs that no longer exist. The shared
connector syncs exactly once, here — no test resyncs it — so the baselines
snapshotted below cannot be moved by anything a concurrent run does.

Auth note: the connector registers only ``AuthType.OAUTH``, but the OAuth gate on
``toggle_sync`` checks nothing beyond the presence of ``credentials.access_token`` in
the KV config, and the GitLab client hands whatever it finds to ``python-gitlab`` as a
bearer token. A Personal Access Token is therefore indistinguishable from an OAuth
access token to the connector, which is what lets this suite authenticate
non-interactively without any connector change.
"""

import logging
import os
import uuid
from typing import Any, AsyncGenerator

import pytest
import pytest_asyncio

from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]
from helper.graph_provider_utils import wait_for_sync_completion  # type: ignore[import-not-found]
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

from connectors.gitlab.constants import (  # type: ignore[import-not-found]
    DEFAULT_INSTANCE_URL,
    ENV_GROUP,
    ENV_INSTANCE_URL,
    ENV_MUTATION_PROJECT,
    ENV_PRIMARY_PROJECT,
    ENV_SUBGROUP,
    ENV_TOKEN,
    GL_BLOCKS_ISSUE_IID,
    GL_BLOCKS_MR_IID,
    GL_INCR_MR_IID,
    GL_IT_RUN_ID,
    GL_SYNC_WAIT_SEC,
)
from connectors.gitlab.gitlab_test_utils import (  # type: ignore[import-not-found]
    GitLabRestClient,
    create_gitlab_connector,
    deepest_blob_path,
    discover_attachment,
    discover_issue_of_type,
    discover_merged_mr,
    discover_reference_issue,
    extensionless_blob_path,
    get_group,
    get_issue,
    get_merge_request,
    get_project,
    get_tree,
    list_filter,
    list_issues,
    list_merge_requests,
    list_project_members,
    reap_own_artifacts,
    resolve_app_user_emails,
    sweep_pinned_mr_comments,
    sweep_stale_artifacts,
    sync_filters,
    syncable_blob_paths,
    teardown_connector,
    tree_dirs,
)

logger = logging.getLogger("gitlab-conftest")


def _require_env() -> dict[str, str]:
    """Tenant config, or skip. There is no useful partial run — every case needs
    either the primary or the mutation project."""
    values = {
        "token": os.getenv(ENV_TOKEN, ""),
        "group": os.getenv(ENV_GROUP, ""),
        "primary": os.getenv(ENV_PRIMARY_PROJECT, ""),
        "mutation": os.getenv(ENV_MUTATION_PROJECT, ""),
    }
    missing = [key for key, value in values.items() if not value]
    if missing:
        pytest.skip(
            f"GitLab credentials/config not set (missing: {', '.join(sorted(missing))}). "
            f"Required: {ENV_TOKEN}, {ENV_GROUP}, {ENV_PRIMARY_PROJECT}, "
            f"{ENV_MUTATION_PROJECT}."
        )
    values["subgroup"] = os.getenv(ENV_SUBGROUP, "")
    values["instance_url"] = os.getenv(ENV_INSTANCE_URL) or DEFAULT_INSTANCE_URL
    return values


@pytest.fixture(scope="session")
def gitlab_env() -> dict[str, str]:
    return _require_env()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def gitlab_rest(gitlab_env: dict[str, str]) -> AsyncGenerator[GitLabRestClient, None]:
    """Raw REST client — used for both source-of-truth reads and mutations."""
    client = GitLabRestClient(gitlab_env["token"], gitlab_env["instance_url"])
    try:
        yield client
    finally:
        await client.aclose()


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def gitlab_connector(
    gitlab_env: dict[str, str],
    gitlab_rest: GitLabRestClient,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    """Module-scoped read-only connector over the primary project.

    Yields a state dict of project metadata, discovered fixture shapes and the
    connector id. Discovery is read-only: nothing in the primary project is created,
    edited or deleted, here or in any test.
    """
    token = gitlab_env["token"]
    instance_url = gitlab_env["instance_url"]
    primary_path = gitlab_env["primary"]
    mutation_path = gitlab_env["mutation"]

    connector_name = f"gitlab-it-{uuid.uuid4().hex[:8]}"
    state: dict[str, Any] = {
        "run_id": GL_IT_RUN_ID,
        # The session REST client, so a test can re-read a fixture at full fidelity
        # (the listing payloads cached below omit fields like merged_by).
        "_rest": gitlab_rest,
        "connector_id": None,
        "connector_name": connector_name,
        "token": token,
        "instance_url": instance_url,
        "primary_path": primary_path,
        "mutation_path": mutation_path,
        "group_path": gitlab_env["group"],
        "subgroup_path": gitlab_env["subgroup"],
    }

    # ---------- SETUP: read-only discovery ----------

    logger.info("SETUP: resolving GitLab fixtures (run_id=%s)", GL_IT_RUN_ID)
    primary = await get_project(gitlab_rest, primary_path)
    mutation = await get_project(gitlab_rest, mutation_path)
    state["primary"] = primary
    state["mutation"] = mutation
    state["primary_branch"] = primary.get("default_branch") or "main"
    state["mutation_branch"] = mutation.get("default_branch") or "main"

    # The namespace the connector will materialise as a group record group. With a
    # bare project_ids filter it derives exactly one path per project — the project's
    # own namespace — so this is the group node the tests assert on.
    namespace = primary.get("namespace") or {}
    state["primary_namespace_path"] = namespace.get("full_path")
    state["primary_namespace"] = (
        await get_group(gitlab_rest, namespace["full_path"])
        if namespace.get("full_path") and namespace.get("kind") != "user"
        else None
    )

    # An empty member list is not an error to GitLab: it returns 200 + [] when the
    # token's role is too low to read members on a private project, and the connector
    # then silently falls back to creator-only permissions. Every permission
    # assertion would be checking that fallback instead of the real ACL split, so
    # fail loudly here rather than pass vacuously later.
    members = await list_project_members(gitlab_rest, primary_path)
    if not members:
        raise RuntimeError(
            f"SETUP: GET /projects/{primary_path}/members/all returned an empty list. "
            "GitLab answers 200 + [] rather than 403 when the token's role is below "
            "Reporter on a private project, and the connector then applies "
            "creator-only permissions — so the permission cases would assert nothing. "
            "Give the IT token at least Reporter on the fixture group."
        )
    state["primary_members"] = members

    issues = await list_issues(gitlab_rest, primary_path)
    mrs = await list_merge_requests(gitlab_rest, primary_path)
    tree = await get_tree(gitlab_rest, primary_path, state["primary_branch"])
    state["primary_issues"] = issues
    state["primary_mrs"] = mrs
    state["primary_tree"] = tree
    state["primary_blob_paths"] = syncable_blob_paths(tree)
    state["primary_dir_paths"] = tree_dirs(tree)

    if not issues:
        raise RuntimeError(
            f"SETUP: {primary_path} has no issues. Re-run the fixture seed before "
            "running this suite."
        )

    issue_attachment = await discover_attachment(
        gitlab_rest, primary_path, issues, "issues",
    )
    state.update({
        "reference_issue": discover_reference_issue(issues),
        "incident_issue": discover_issue_of_type(issues, "incident"),
        "task_issue": discover_issue_of_type(issues, "task"),
        "merged_mr": discover_merged_mr(mrs),
        "nested_code_path": deepest_blob_path(tree),
        "extensionless_code_path": extensionless_blob_path(tree),
        "attachment_issue": issue_attachment[0] if issue_attachment else None,
        "attachment_href": issue_attachment[1] if issue_attachment else None,
    })

    # The pinned blocks fixtures are addressed by iid because their content is
    # compared byte-for-byte; resolve them now so a mis-set env var fails at setup.
    state["blocks_issue"] = await get_issue(
        gitlab_rest, primary_path, GL_BLOCKS_ISSUE_IID,
    )
    state["blocks_mr"] = await get_merge_request(
        gitlab_rest, primary_path, GL_BLOCKS_MR_IID,
    )
    _log_discovery(state)

    # Reap artifacts left by crashed runs. Age-gated and shape-gated, so a run still
    # asserting on its own fixtures is never touched.
    await sweep_stale_artifacts(
        gitlab_rest, mutation_path, state["mutation_branch"],
    )
    # The pinned incremental MR is never deleted, so comments stranded on it by a run
    # that died before cleanup would accumulate one per crashed run.
    await sweep_pinned_mr_comments(gitlab_rest, mutation_path, GL_INCR_MR_IID)

    # ---------- SETUP: connector ----------

    connector_id = create_gitlab_connector(
        pipeshub_client,
        token=token,
        name=connector_name,
        instance_url=instance_url,
        filters=sync_filters(project_ids=list_filter("in", [primary_path])),
    )
    state["connector_id"] = connector_id
    logger.info("SETUP: connector %s scoped to %s", connector_id, primary_path)

    try:
        pipeshub_client.toggle_sync(connector_id, enable=True)

        instance = pipeshub_client.get_connector(connector_id)
        assert (instance.get("connector") or instance).get("isAuthenticated") is not False, (
            "Connector did not authenticate. The injected token was rejected — check "
            "the PAT's scopes (read_api, read_user, read_repository) and that "
            "SECRET_KEY matches the backend's."
        )

        state["full_sync_count"] = await wait_for_sync_completion(
            pipeshub_client, graph_provider, connector_id,
            min_records=1, timeout=GL_SYNC_WAIT_SEC,
        )

        # Which principals the connector actually bound to PipesHub identities.
        # GitLab hides emails unless a member sets public_email, so this is not
        # predictable from the source side and has to be read back.
        candidate_ids = {
            str(m["id"]) for m in members if m.get("id") is not None
        }
        state["member_ids"] = sorted(candidate_ids)
        state["app_user_emails"] = await resolve_app_user_emails(
            graph_provider, connector_id, sorted(candidate_ids),
        )
        # Whose ACL the streaming cases are subject to: stream_record enforces the
        # record permission of the account the harness logs in as, which is only
        # granted if a GitLab member published that same address as public_email.
        state["stream_user_email"] = os.getenv("PIPESHUB_TEST_USER_EMAIL", "")
        logger.info(
            "SETUP: %d/%d members resolved to PipesHub identities (the rest become "
            "pseudo-groups)", len(state["app_user_emails"]), len(candidate_ids),
        )

        yield state
    finally:
        # Nested, not sequential: teardown_connector asserts the graph drained and
        # raises when it does not, which would skip the GitLab-side cleanup entirely
        # and strand this run's artifacts on the shared mutation project until the
        # two-hour stale sweep.
        try:
            await teardown_connector(pipeshub_client, graph_provider, connector_id)
        finally:
            await reap_own_artifacts(
                gitlab_rest, mutation_path, state["mutation_branch"],
            )


def _log_discovery(state: dict[str, Any]) -> None:
    """One line per discovered shape, so a skipped test is explainable from the log
    without re-running with ``-s``."""
    for key, describe in (
        ("reference_issue", lambda v: f"#{v['iid']} {v['title']!r}"),
        ("incident_issue", lambda v: f"#{v['iid']}"),
        ("task_issue", lambda v: f"#{v['iid']}"),
        ("merged_mr", lambda v: f"!{v['iid']}"),
        ("attachment_issue", lambda v: f"#{v['iid']}"),
        ("attachment_href", str),
        ("nested_code_path", str),
        ("extensionless_code_path", str),
        ("primary_namespace_path", str),
    ):
        value = state.get(key)
        logger.info(
            "SETUP: %s = %s", key, describe(value) if value else "NOT FOUND (tests will skip)",
        )
    logger.info(
        "SETUP: %d syncable blob(s), %d folder(s), %d member(s)",
        len(state["primary_blob_paths"]), len(state["primary_dir_paths"]),
        len(state["primary_members"]),
    )
