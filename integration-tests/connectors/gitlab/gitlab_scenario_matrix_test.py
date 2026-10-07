# pyright: ignore-file

"""GitLab in the shared scenario matrix (``connectors/scenario_matrix.py``).

A connector of its own over the mutation project (``GITLAB_TEST_MUTATION_PROJECT``).
Items are Markdown files this run commits under ``it/<run_id>/mx/`` on the
project's default branch. Code files are the item because they are the one kind
the connector follows through every change: its incremental sync compares the
stored commit with the branch head and applies added, modified, renamed (in place,
``on_records_moved``) and deleted paths. Issues are not used: no GitLab issue
deletion ever reaches the graph (``sources/gitlab`` deletes only code files).

Issue and merge-request indexing is switched off for this connector: the matrix
never reads them, and indexing the project's tickets would only cost time.
Search runs as the admin that created the connector, which the connector links to
the token's GitLab account.
"""

from __future__ import annotations

import logging
import posixpath
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio

from connectors.gitlab.constants import GL_IT_RUN_ID, GL_SYNC_WAIT_SEC, it_path
from connectors.gitlab.gitlab_test_utils import (
    GitLabRestClient,
    bool_filter,
    commit_actions,
    create_gitlab_connector,
    get_project,
    indexing_filters,
    list_filter,
    sync_filters,
    teardown_connector,
)
from connectors.scenario_matrix import (
    Action,
    ConnectorScenarioMatrix,
    Role,
    ScenarioAdapter,
    SourceItem,
)
from helper.graph_provider import GraphProviderProtocol
from helper.graph_provider_utils import wait_for_sync_completion
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

logger = logging.getLogger("gitlab-matrix")


class GitLabCodeAdapter(ScenarioAdapter):
    source = "GitLab"

    def __init__(self, *, rest: GitLabRestClient, project: str, branch: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.rest = rest
        self.project = project
        self.branch = branch
        self.live_paths: set[str] = set()

    async def _commit(self, actions: list[dict[str, Any]], message: str) -> None:
        await commit_actions(self.rest, self.project, self.branch, message, actions)

    async def create_item(self, role: Role, text: str, token: str) -> SourceItem:
        path = it_path("mx", f"{role.value}-{token}.md")
        await self._commit([{"action": "create", "file_path": path, "content": text}],
                           f"matrix: add {role.value} ({GL_IT_RUN_ID})")
        self.live_paths.add(path)
        return SourceItem(role=role, key=path, record_name=posixpath.basename(path),
                          text=text, token=token)

    async def update_content(self, item: SourceItem, text: str, token: str) -> SourceItem:
        await self._commit([{"action": "update", "file_path": item.key, "content": text}],
                           f"matrix: edit ({GL_IT_RUN_ID})")
        return SourceItem(role=item.role, key=item.key, record_name=item.record_name, text=text,
                          token=token, extra=dict(item.extra))

    async def update_metadata(self, item: SourceItem) -> SourceItem:
        new_path = it_path("mx", f"renamed-{item.token}.md")
        await self._commit([{"action": "move", "file_path": new_path, "previous_path": item.key}],
                           f"matrix: rename ({GL_IT_RUN_ID})")
        self.live_paths.discard(item.key)
        self.live_paths.add(new_path)
        return SourceItem(role=item.role, key=new_path, record_name=posixpath.basename(new_path),
                          text=item.text, token=item.token, extra=dict(item.extra))

    async def delete_item(self, item: SourceItem) -> None:
        await self._commit([{"action": "delete", "file_path": item.key}],
                           f"matrix: delete ({GL_IT_RUN_ID})")
        self.live_paths.discard(item.key)

    async def cleanup(self) -> None:
        if not self.live_paths:
            return
        try:
            await self._commit(
                [{"action": "delete", "file_path": p} for p in sorted(self.live_paths)],
                f"matrix: teardown ({GL_IT_RUN_ID})",
            )
            self.live_paths.clear()
        except Exception as e:  # noqa: BLE001 - the suite's stale-namespace sweep catches leaks
            logger.warning("TEARDOWN: could not delete %s: %s", sorted(self.live_paths), e)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def scenario_adapter(
    gitlab_env: dict[str, str],
    gitlab_rest: GitLabRestClient,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[GitLabCodeAdapter, None]:
    project_path = gitlab_env["mutation"]
    project = await get_project(gitlab_rest, project_path)
    filters = sync_filters(project_ids=list_filter("in", [project_path]))
    filters.update(indexing_filters(issues=bool_filter(False), merge_requests=bool_filter(False)))
    connector_id = create_gitlab_connector(
        pipeshub_client,
        token=gitlab_env["token"],
        name=f"gitlab-matrix-{GL_IT_RUN_ID}",
        instance_url=gitlab_env["instance_url"],
        filters=filters,
    )
    adapter = GitLabCodeAdapter(
        rest=gitlab_rest,
        project=project_path,
        branch=project.get("default_branch") or "main",
        client=pipeshub_client,
        graph=graph_provider,
        connector_id=connector_id,
    )
    try:
        pipeshub_client.toggle_sync(connector_id, enable=True)
        await wait_for_sync_completion(
            pipeshub_client, graph_provider, connector_id, min_records=1, timeout=GL_SYNC_WAIT_SEC,
        )
        yield adapter
    finally:
        await adapter.cleanup()
        try:
            await teardown_connector(pipeshub_client, graph_provider, connector_id)
        except Exception as e:  # noqa: BLE001 - teardown must not mask the test result
            logger.warning("TEARDOWN: delete/clean failed for %s: %s", connector_id, e)


@pytest.mark.integration
@pytest.mark.gitlab
class TestGitLabScenarioMatrix(ConnectorScenarioMatrix):
    SOURCE = "GitLab"
    UNSUPPORTED = {
        Action.CHANGE_PERMISSION.value: (
            "GitLab has no per-file share: every file in a project carries the project's "
            "membership, so there is no item-level permission to change"
        ),
        Action.SET_FILTER.value: (
            "the connector syncs exactly one project (project_ids is a required "
            "single-repository picker) and its date filters apply to issues and merge "
            "requests only, so no filter leaves out one file of that project"
        ),
    }
