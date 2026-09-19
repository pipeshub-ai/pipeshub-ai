# pyright: ignore-file

"""
GitHub (personal) Connector – Integration Tests (read-only)
===========================================================

The personal connector reuses the GitHub Teams sync code (repos, issues, PRs, code
files, streaming), which ``connectors/github_teams`` covers in depth. This suite covers
what the personal connector does differently, plus a baseline proving the shared sync
still runs under it:

- repo discovery is by account and the one required REPO_IDS filter;
- every repo is granted to a single internal ``ConnectorGroup`` whose only member is
  the creator — no per-collaborator edges, and no visibility-derived ORG grant even for
  a public repo;
- no GitHub user directory is synced.

It reuses the GitHub Teams tenant (same PAT and repos) and never writes to GitHub.

  order 1 TC-GHP-SYNC-001   — full sync baseline: every issue, PR and blob, wired correctly
  order 2 TC-GHP-PERM-001   — ConnectorGroup: one member, one GROUP grant on the repo group
  order 3 TC-GHP-USER-001   — only the creator is linked to the app; no GitHub users
  order 4 TC-GHP-STREAM-001 — the creator can stream an issue through the group grant
  order 5 TC-GHP-FILTER-001 — REPO_IDS scoping: the unlisted public repo does not sync
  order 6 TC-GHP-PERM-002   — a public repo still gets no ORG grant (its own connector)
"""

import logging
import sys
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.config.constants.arangodb import CollectionNames  # type: ignore[import-not-found]  # noqa: E402
from app.models.entities import RecordType  # type: ignore[import-not-found]  # noqa: E402
from helper.graph_provider import GraphProviderProtocol  # noqa: E402
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]  # noqa: E402

from connectors.github_personal.github_personal_utils import connector_name, synced_personal_connector  # noqa: E402
from connectors.github_teams.github_block_utils import (  # noqa: E402
    parse_connector_blocks_via_processor,
)

logger = logging.getLogger("github-personal-it")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.github,
    pytest.mark.asyncio(loop_scope="session"),
]

_CHILD_KINDS = ("work-items", "pull-requests", "code-repository")


def _connector_group_id(connector_id: str) -> str:
    """External id of the internal ConnectorGroup (``_connector_group_external_id``)."""
    return f"internal-{connector_id}"


async def _assert_repo_granted_to_connector_group_only(
    graph_provider: GraphProviderProtocol,
    pipeshub_client: PipeshubClient,
    connector_id: str,
    repo: dict[str, Any],
) -> None:
    """The repo group's whole ACL is one GROUP edge from the ConnectorGroup."""
    repo_group = await graph_provider.get_record_group_by_external_id(connector_id, str(repo["id"]))
    assert repo_group is not None, f"record group for {repo['full_name']} missing"
    group = await graph_provider.get_user_group_by_external_id(
        connector_id, _connector_group_id(connector_id),
    )
    assert group is not None, "ConnectorGroup was not created"

    grants = await graph_provider.count_permission_edges_to_record_groups(connector_id, str(repo["id"]))
    assert grants == 1, (
        f"{repo['full_name']}: repo group has {grants} PERMISSION edge(s); a personal "
        "connector grants access through the ConnectorGroup alone"
    )
    edges = await graph_provider.find_edges_between(
        CollectionNames.GROUPS.value, group.id,
        CollectionNames.RECORD_GROUPS.value, repo_group.id,
        CollectionNames.PERMISSION.value,
    )
    assert len(edges) == 1, f"expected one ConnectorGroup -> repo group edge, found {len(edges)}"
    assert edges[0].get("type") == "GROUP", f"grant must be a GROUP permission, got {edges[0].get('type')!r}"

    org_edges = await graph_provider.find_edges_between(
        CollectionNames.ORGS.value, pipeshub_client.org_id,
        CollectionNames.RECORD_GROUPS.value, repo_group.id,
        CollectionNames.PERMISSION.value,
    )
    assert not org_edges, (
        f"{repo['full_name']} ({repo.get('visibility')}) carries an org-wide grant; a "
        "personal connector must never grant beyond its ConnectorGroup"
    )

    for kind in _CHILD_KINDS:
        child_grants = await graph_provider.count_permission_edges_to_record_groups(
            connector_id, f"{repo['id']}-{kind}",
        )
        assert child_grants == 0, (
            f"child group {kind} must carry no ACL of its own and inherit from the repo "
            f"group, but has {child_grants} PERMISSION edge(s)"
        )


class TestGitHubPersonalConnector:

    @pytest.mark.order(1)
    async def test_tc_ghp_sync_001_full_sync_baseline(
        self,
        github_personal_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-GHP-SYNC-001: the primary repo's issues, PRs and blobs are all synced.

        Structural invariants plus presence by external id, as in the Teams suite, so a
        fixture edit doesn't break the test while a dropped record still does.
        """
        state = github_personal_connector
        connector_id = state["connector_id"]

        total = await graph_provider.count_records(connector_id, scoped=True)
        by_type = {
            rt: await graph_provider.count_records_by_type(connector_id, rt, scoped=True)
            for rt in (
                RecordType.TICKET.value,
                RecordType.PULL_REQUEST.value,
                RecordType.CODE_FILE.value,
                RecordType.FILE.value,
            )
        }
        assert total > 0, "full sync produced no records"
        assert total == sum(by_type.values()), f"records {total} != sum of known types {by_type}"
        assert await graph_provider.count_record_group_edges(connector_id) == total, (
            "every record needs one BELONGS_TO edge to a record group"
        )
        assert await graph_provider.count_inherit_permissions_edges(connector_id) == total, (
            "every record must inherit permissions from its record group"
        )

        repo_id = state["primary_repo"]["id"]
        missing = [
            f"issue #{i['number']}" for i in state["primary_issues"]
            if not await graph_provider.get_record_by_external_id(connector_id, f"{repo_id}/issues/{i['number']}")
        ]
        missing += [
            f"PR #{p['number']}" for p in state["primary_pulls"]
            if not await graph_provider.get_record_by_external_id(connector_id, f"{repo_id}/pull/{p['number']}")
        ]
        missing += [
            e["path"] for e in state["primary_tree"]
            if e.get("type") == "blob"
            and not await graph_provider.get_record_by_external_id(connector_id, f"/{repo_id}/blob/{e['path']}")
        ]
        assert not missing, f"not synced from {state['primary_repo']['full_name']}: {missing}"
        logger.info("TC-GHP-SYNC-001 passed: %d records %s", total, by_type)

    @pytest.mark.order(2)
    async def test_tc_ghp_perm_001_connector_group_grant(
        self,
        github_personal_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
        pipeshub_client: PipeshubClient,
    ) -> None:
        """TC-GHP-PERM-001: access flows through one ConnectorGroup with one member.

        The creator is the group's sole member, and the private repo's whole ACL is a
        single GROUP edge from that group — no collaborator edges and no ORG grant.
        """
        connector_id = github_personal_connector["connector_id"]
        members = await graph_provider.count_user_to_group_permission_edges(
            connector_id, _connector_group_id(connector_id),
        )
        assert members == 1, f"ConnectorGroup should have exactly one member (the creator), has {members}"
        await _assert_repo_granted_to_connector_group_only(
            graph_provider, pipeshub_client, connector_id, github_personal_connector["primary_repo"],
        )
        logger.info("TC-GHP-PERM-001 passed")

    @pytest.mark.order(3)
    async def test_tc_ghp_user_001_no_github_users(
        self,
        github_personal_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
        pipeshub_client: PipeshubClient,
    ) -> None:
        """TC-GHP-USER-001: only the creator is linked to the app; no GitHub users or teams."""
        connector_id = github_personal_connector["connector_id"]
        app_users = await graph_provider.count_user_app_relation_edges(connector_id)
        assert app_users == 1, (
            f"{app_users} users linked to the app; the personal connector syncs no GitHub "
            "user directory, so only the creator should be"
        )
        creator = await graph_provider.graph_find_user_by_user_id(pipeshub_client.user_id)
        assert creator is not None, "the connector's creator has no user node in the graph"
        creator_key = creator.get("_key") or creator.get("id")
        creator_edges = await graph_provider.find_edges_between(
            CollectionNames.USERS.value, creator_key,
            CollectionNames.APPS.value, connector_id,
            CollectionNames.USER_APP_RELATION.value,
        )
        assert len(creator_edges) == 1, (
            f"the one user linked to the app should be the creator; found "
            f"{len(creator_edges)} creator -> app edge(s)"
        )
        groups = await graph_provider.count_user_groups(connector_id)
        assert groups == 1, f"expected only the ConnectorGroup, found {groups} user groups"

    @pytest.mark.order(4)
    async def test_tc_ghp_stream_001_creator_can_stream(
        self,
        github_personal_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
        pipeshub_client: PipeshubClient,
    ) -> None:
        """TC-GHP-STREAM-001: an issue streams for the creator.

        Streaming checks the caller's access, so this proves the creator reaches synced
        content through ConnectorGroup -> repo group -> child group -> record.
        """
        state = github_personal_connector
        issue = state["primary_issues"][0]
        external_id = f"{state['primary_repo']['id']}/issues/{issue['number']}"
        record = await graph_provider.get_record_by_external_id(state["connector_id"], external_id)
        assert record is not None, f"issue #{issue['number']} not synced"

        resp = pipeshub_client.stream_record(record.id)
        assert resp.status_code == 200, f"stream_record HTTP {resp.status_code}: {resp.text[:300]}"
        content_type = (resp.headers.get("content-type") or "").lower()
        assert "application/blocks" in content_type, f"unexpected content-type {content_type!r}"
        parsed = await parse_connector_blocks_via_processor(resp.content)
        assert parsed.get("blocks") or parsed.get("block_groups"), f"issue #{issue['number']} streamed no blocks"

    @pytest.mark.order(5)
    async def test_tc_ghp_filter_001_unlisted_repo_not_synced(
        self,
        github_personal_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-GHP-FILTER-001: REPO_IDS names only the primary repo, so the public repo
        the token can also read must not be synced."""
        public = github_personal_connector["public_repo"]
        group = await graph_provider.get_record_group_by_external_id(
            github_personal_connector["connector_id"], str(public["id"]),
        )
        assert group is None, f"{public['full_name']} synced although REPO_IDS does not list it"

    @pytest.mark.order(6)
    async def test_tc_ghp_perm_002_public_repo_no_org_grant(
        self,
        github_personal_connector: dict[str, Any],
        graph_provider: GraphProviderProtocol,
        pipeshub_client: PipeshubClient,
    ) -> None:
        """TC-GHP-PERM-002: a public repo is still granted to the ConnectorGroup only.

        The Teams connector mirrors GitHub, so a public repo becomes readable org-wide.
        The personal connector must not: removing someone from the ConnectorGroup has to
        revoke their access, which an ORG grant would silently undo. An instance syncs
        one repository, so the public repo gets its own connector.
        """
        public = github_personal_connector["public_repo"]
        async with synced_personal_connector(
            pipeshub_client, graph_provider,
            token=github_personal_connector["token"], name=connector_name("public"),
            repo_full_name=public["full_name"],
        ) as connector_id:
            await _assert_repo_granted_to_connector_group_only(
                graph_provider, pipeshub_client, connector_id, public,
            )
        logger.info("TC-GHP-PERM-002 passed")
