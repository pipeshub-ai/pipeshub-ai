# pyright: ignore-file

"""GitHub Teams connector fixtures.

- session-scoped ``github_rest`` (skips when the PAT is missing)
- module-scoped ``github_connector``: discovers fixture shapes read-only against the
  primary + public repos, registers a PipesHub connector scoped to exactly those two,
  waits for one sync, snapshots baselines, then tears the connector down.

Setup **never writes to the primary or public repos**. Its only writes are to the
mutation repo, and only to reap artifacts leaked by runs that no longer exist. The
shared connector syncs exactly once, here — no test resyncs it — so the baselines
snapshotted below cannot be moved by anything another concurrent run does.

Auth note: the connector registers only ``AuthType.OAUTH``, but the OAuth gate on
``toggle_sync`` checks nothing more than the presence of ``credentials.access_token``
in the KV config, and ``GitHubClientViaToken`` passes whatever it finds to
``Auth.Token``. A classic PAT is therefore indistinguishable from an OAuth access
token to the connector, which is what lets this suite authenticate non-interactively
without any connector change. See ``README.md``.
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

from connectors.github_teams.constants import (  # type: ignore[import-not-found]
    ENV_MUTATION_REPO,
    ENV_ORG,
    ENV_PRIMARY_REPO,
    ENV_PUBLIC_REPO,
    ENV_TOKEN,
    GH_BLOCKS_ISSUE_NUMBER,
    GH_BLOCKS_PR_NUMBER,
    GH_INCR_PR_NUMBER,
    GH_IT_RUN_ID,
    GH_SYNC_WAIT_SEC,
)
from connectors.github_teams.github_test_utils import (  # type: ignore[import-not-found]
    build_rest_client,
    create_github_connector,
    deepest_blob_path,
    discover_blocking_issue,
    discover_merged_pr,
    discover_multi_assignee_issue,
    discover_non_image_attachment_issue,
    discover_reference_issue,
    discover_subissue_pair,
    extensionless_blob_path,
    get_repo,
    get_tree,
    list_collaborators,
    list_issues,
    list_pulls,
    list_filter,
    reap_own_artifacts,
    resolve_app_user_emails,
    sweep_pinned_pr_comments,
    sweep_stale_artifacts,
    sync_filters,
    teardown_connector,
)

logger = logging.getLogger("github-teams-conftest")


def _require_env() -> dict[str, str]:
    """Tenant config, or skip. All five are required — there is no useful partial run."""
    values = {
        "token": os.getenv(ENV_TOKEN, ""),
        "org": os.getenv(ENV_ORG, ""),
        "primary": os.getenv(ENV_PRIMARY_REPO, ""),
        "public": os.getenv(ENV_PUBLIC_REPO, ""),
        "mutation": os.getenv(ENV_MUTATION_REPO, ""),
    }
    missing = [k for k, v in values.items() if not v]
    if missing:
        pytest.skip(
            f"GitHub Teams credentials/config not set (missing: {', '.join(sorted(missing))}). "
            f"Required: {ENV_TOKEN}, {ENV_ORG}, {ENV_PRIMARY_REPO}, {ENV_PUBLIC_REPO}, "
            f"{ENV_MUTATION_REPO}."
        )
    return values


@pytest.fixture(scope="session")
def github_env() -> dict[str, str]:
    return _require_env()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def github_rest(github_env: dict[str, str]) -> AsyncGenerator[Any, None]:
    """Raw REST client — used for both source-of-truth reads and mutations."""
    client = build_rest_client(github_env["token"])
    try:
        yield client
    finally:
        await client.aclose()


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def github_connector(
    github_env: dict[str, str],
    github_rest: Any,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    """Module-scoped read-only connector over the primary + public repos.

    Yields a state dict of repo metadata, discovered fixture shapes, and the
    connector id. Discovery is read-only: nothing in the primary or public repo is
    created, edited or deleted, here or in any test.
    """
    org = github_env["org"]
    token = github_env["token"]
    primary_name = github_env["primary"]
    public_name = github_env["public"]
    mutation_name = github_env["mutation"]

    connector_name = f"github-teams-it-{uuid.uuid4().hex[:8]}"
    state: dict[str, Any] = {
        "org": org,
        "run_id": GH_IT_RUN_ID,
        "connector_id": None,
        "connector_name": connector_name,
        "token": token,
        "mutation_repo_name": mutation_name,
    }

    # ---------- SETUP: read-only discovery ----------

    logger.info("SETUP: resolving repos in org %s (run_id=%s)", org, GH_IT_RUN_ID)
    primary = await get_repo(github_rest, org, primary_name)
    public = await get_repo(github_rest, org, public_name)
    mutation = await get_repo(github_rest, org, mutation_name)
    state["primary_repo"] = primary
    state["public_repo"] = public
    state["mutation_repo"] = mutation
    state["org_id"] = primary["owner"]["id"]

    if public.get("visibility") != "public":
        raise RuntimeError(
            f"SETUP: {public['full_name']} has visibility "
            f"{public.get('visibility')!r}, but TC-GH-PERM-002 exists to assert the "
            "public → Permission(READ, ORG) branch. Point "
            f"{ENV_PUBLIC_REPO} at a genuinely public repo."
        )
    if primary.get("visibility") == "public":
        raise RuntimeError(
            f"SETUP: {primary['full_name']} is public, but TC-GH-PERM-001 asserts a "
            "private repo's ACL has no ORG grant. Point "
            f"{ENV_PRIMARY_REPO} at a private repo."
        )

    # Collaborator listing needs push access. Without it the connector silently falls
    # back to the visibility floor, and every permission assertion would be checking
    # the wrong branch — so fail loudly at setup instead.
    try:
        state["primary_collaborators"] = await list_collaborators(
            github_rest, org, primary_name,
        )
    except Exception as e:
        raise RuntimeError(
            f"SETUP: cannot list collaborators on {primary['full_name']} ({e}). The IT "
            "token needs push access; without it the connector takes the "
            "visibility-floor path and the permission tests assert nothing real."
        ) from e

    issues = await list_issues(github_rest, org, primary_name)
    pulls = await list_pulls(github_rest, org, primary_name)
    tree = await get_tree(github_rest, org, primary_name, primary["default_branch"])
    state["primary_issues"] = issues
    state["primary_pulls"] = pulls
    state["primary_tree"] = tree

    if not issues:
        raise RuntimeError(
            f"SETUP: {primary['full_name']} has no issues. See README.md for the "
            "fixture seed this suite expects."
        )

    parent, child = discover_subissue_pair(issues)
    attachment = await discover_non_image_attachment_issue(
        github_rest, org, primary_name, issues,
    )
    state.update({
        "reference_issue": discover_reference_issue(issues),
        "multi_assignee_issue": discover_multi_assignee_issue(issues),
        "subissue_parent": parent,
        "subissue_child": child,
        "blocking_issue": discover_blocking_issue(issues),
        "merged_pr": discover_merged_pr(pulls),
        "nested_code_path": deepest_blob_path(tree),
        "extensionless_code_path": extensionless_blob_path(tree),
        "attachment_issue": attachment[0] if attachment else None,
        "attachment_url": attachment[1] if attachment else None,
        "blocks_issue_number": GH_BLOCKS_ISSUE_NUMBER,
        "blocks_pr_number": GH_BLOCKS_PR_NUMBER,
    })
    if state["reference_issue"] is None:
        raise RuntimeError(
            f"SETUP: {primary['full_name']} has no issue without a sub-issue parent or "
            "blocking dependency. The TICKET property assertions need one plain issue; "
            "see README.md for the fixture seed."
        )
    _log_discovery(state)

    # Reap artifacts leaked by crashed runs. Age-gated and shape-gated, so a run still
    # asserting on its own fixtures is never touched.
    await sweep_stale_artifacts(
        github_rest, org, mutation_name, mutation["default_branch"],
    )
    # The pinned incremental PR is never deleted, so comments stranded on it by a run
    # that died before its cleanup would accumulate one per crashed run.
    await sweep_pinned_pr_comments(github_rest, org, mutation_name, GH_INCR_PR_NUMBER)

    # ---------- SETUP: connector ----------

    connector_id = create_github_connector(
        pipeshub_client,
        token=token,
        name=connector_name,
        filters=sync_filters(
            repo_ids=list_filter("in", [primary["full_name"], public["full_name"]]),
        ),
    )
    state["connector_id"] = connector_id
    logger.info("SETUP: connector %s scoped to %s + %s",
                connector_id, primary["full_name"], public["full_name"])

    try:
        pipeshub_client.toggle_sync(connector_id, enable=True)

        instance = pipeshub_client.get_connector(connector_id)
        assert (instance.get("connector") or instance).get("isAuthenticated") is not False, (
            "Connector did not authenticate. The injected token was rejected by "
            "test_connection_and_access (GET /user) — check the PAT's scopes "
            "(repo, read:org, user:email) and that SECRET_KEY matches the backend."
        )

        await wait_for_sync_completion(
            pipeshub_client, graph_provider, connector_id,
            min_records=1, timeout=GH_SYNC_WAIT_SEC,
        )

        # Which principals the connector actually bound to PipesHub identities.
        candidate_ids = {
            str(c["id"]) for c in state["primary_collaborators"] if c.get("id") is not None
        }
        for issue in issues:
            for person in [issue.get("user"), *(issue.get("assignees") or [])]:
                if person and person.get("id") is not None:
                    candidate_ids.add(str(person["id"]))
        state["app_user_emails"] = await resolve_app_user_emails(
            graph_provider, connector_id, sorted(candidate_ids),
        )
        logger.info("SETUP: %d/%d principals resolved to PipesHub identities",
                    len(state["app_user_emails"]), len(candidate_ids))

        yield state
    finally:
        await teardown_connector(pipeshub_client, graph_provider, connector_id)
        await reap_own_artifacts(
            github_rest, org, mutation_name, mutation["default_branch"],
        )


def _log_discovery(state: dict[str, Any]) -> None:
    """One line per discovered shape, so a skipped test is explainable from the log
    without re-running with -s."""
    for key, describe in (
        ("multi_assignee_issue", lambda v: f"#{v['number']} ({len(v['assignees'])} assignees)"),
        ("subissue_child", lambda v: f"#{v['number']}"),
        ("blocking_issue", lambda v: f"#{v['number']}"),
        ("merged_pr", lambda v: f"#{v['number']}"),
        ("attachment_issue", lambda v: f"#{v['number']}"),
        ("nested_code_path", str),
        ("extensionless_code_path", str),
    ):
        value = state.get(key)
        logger.info(
            "SETUP: %s = %s", key, describe(value) if value else "NOT FOUND (tests will skip)",
        )
