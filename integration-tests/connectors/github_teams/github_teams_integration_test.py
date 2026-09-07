# pyright: ignore-file

"""
GitHub Teams Connector – Integration Tests
==========================================

Tests receive a fully set-up connector via the ``github_teams_connector``
fixture (defined in conftest.py), which authenticates with the configured
personal access token, runs a full sync against the configured organisation, and
removes the connector afterwards.

Scope
-----
Read-only and content-agnostic. The organisation and its repositories are shared
fixtures this suite does not own: nothing is created there, nothing is deleted,
and no assertion names a particular issue or file. A test that hard-coded the
organisation's contents would fail whenever somebody pushed to it, which is
noise rather than a connector regression.

What that leaves is the failure mode that matters most here. This connector
breaks when GitHub changes something — a token expires, a scope is withdrawn, an
API is retired, a permissions endpoint changes shape. None of that involves a
change to this repository, and nothing else would notice.

Test cases:
  TC-AUTH-001  — Token authentication succeeds and the sync produces records
  TC-GRAPH-001 — The synced graph is coherent: groups, edges, no orphans
  TC-PERM-001  — Permissions are synced, not only content
  TC-REPOS-001 — The configured repositories are among what was synced
"""

import logging
import sys
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from helper.graph_provider import GraphProviderProtocol

logger = logging.getLogger("github-teams-lifecycle-test")


@pytest.mark.integration
@pytest.mark.github_teams
@pytest.mark.asyncio(loop_scope="session")
class TestGitHubTeamsConnector:
    """Read-only coverage for the GitHub Teams connector."""

    @pytest.mark.order(1)
    async def test_tc_auth_001_token_auth_and_sync(
        self,
        github_teams_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-AUTH-001: The connector authenticates and syncs something.

        Reaching this point proves more than it looks: the token was accepted,
        its scopes still cover what the connector reads, and the GitHub APIs it
        calls still answer in the shape it expects.
        """
        connector_id = github_teams_connector["connector_id"]
        full_count = github_teams_connector["full_sync_count"]

        assert full_count > 0, (
            "TC-AUTH-001: the sync produced no records. Either the token was "
            "rejected, its scopes were reduced, or the organisation is empty."
        )
        await graph_provider.assert_min_records(connector_id, 1)
        logger.info(
            "TC-AUTH-001 passed: %d records synced from %s",
            full_count,
            github_teams_connector["org"],
        )

    @pytest.mark.order(2)
    async def test_tc_graph_001_synced_graph_is_coherent(
        self,
        github_teams_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-GRAPH-001: Records are attached to groups, with nothing orphaned.

        A record with no group or app edge is invisible to search even though it
        indexed, which a user experiences as the document never having synced.
        """
        connector_id = github_teams_connector["connector_id"]

        await graph_provider.assert_record_groups_and_edges(
            connector_id, min_groups=1, min_record_edges=1
        )
        await graph_provider.assert_app_record_group_edges(connector_id, min_edges=1)
        await graph_provider.assert_no_orphan_records(connector_id)
        logger.info("TC-GRAPH-001 passed: graph is coherent for %s", connector_id)

    @pytest.mark.order(3)
    async def test_tc_perm_001_permissions_are_synced(
        self,
        github_teams_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-PERM-001: Permission edges exist.

        Private repositories are access-controlled and this connector maps that
        into the graph. If permission sync stopped, content would still index and
        search would still return results while access decisions were made
        against nothing.

        Worth reading with the setup note in env.local.template: the token owner
        needs push access on the repositories, because the connector resolves
        permissions through /collaborators and falls back to the repository's
        visibility without it. A sudden drop to zero edges here can mean the
        token lost push access rather than that the code broke.
        """
        connector_id = github_teams_connector["connector_id"]

        edges = await graph_provider.count_permission_edges(connector_id)
        assert edges > 0, (
            "TC-PERM-001: no permission edges were created. Content synced but "
            "its access control did not, so permissions are being evaluated "
            "against nothing."
        )
        logger.info("TC-PERM-001 passed: %d permission edges", edges)

    @pytest.mark.order(4)
    async def test_tc_repos_001_configured_repositories_were_synced(
        self,
        github_teams_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-REPOS-001: The configured repositories appear in the sync.

        Names are matched case-insensitively against record names and paths,
        because a repository surfaces in the graph through the records it
        contains rather than as a record of its own.
        """
        expected = github_teams_connector["expected_repos"]
        if not expected:
            pytest.skip("no repositories configured")

        connector_id = github_teams_connector["connector_id"]
        names = await graph_provider.fetch_record_names(connector_id)
        paths = await graph_provider.fetch_record_paths(connector_id)
        haystack = " ".join(
            names + [str(p) for pair in paths for p in pair if p]
        ).lower()

        missing = [repo for repo in expected if repo.lower() not in haystack]
        assert not missing, (
            f"TC-REPOS-001: configured repositories {missing} produced no "
            f"records. {len(names)} records synced from elsewhere, so the token "
            "worked but these repositories were not reached — check that it has "
            "access to them."
        )
        logger.info("TC-REPOS-001 passed: repositories %s are present", expected)
