# pyright: ignore-file

"""Scenario-matrix adapter for the GitHub connectors (Teams and personal).

Both connectors sync one repository's code through the same ``repos.py``: an
incremental sync compares the stored commit with the branch head, so an added,
modified, renamed or removed file each reach the graph by their own path
(``status == "renamed"`` is applied in place with ``on_records_moved``). Items are
Markdown files under this run's ``it/<run_id>/mx/`` namespace in the mutation
repo, written as single commits the way a person would push them.
"""

from __future__ import annotations

import logging
import posixpath
from typing import Any

from connectors.github_teams.constants import it_path
from connectors.github_teams.github_test_utils import FileChange, commit_changes
from connectors.scenario_matrix import Action, Role, ScenarioAdapter, SourceItem

logger = logging.getLogger("github-matrix")

UNSUPPORTED = {
    Action.CHANGE_PERMISSION.value: (
        "GitHub has no per-file share: every file in a repository carries the repository's "
        "access, so there is no item-level permission to change"
    ),
    Action.SET_FILTER.value: (
        "the only sync filters are the organisation and the single repository the "
        "connector syncs (repo_ids is a one-repository picker), so no filter leaves out "
        "one file of that repository"
    ),
}


class GitHubCodeAdapter(ScenarioAdapter):
    source = "GitHub"

    def __init__(self, *, rest: Any, owner: str, repo: str, branch: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.rest = rest
        self.owner = owner
        self.repo = repo
        self.branch = branch
        self.live_paths: set[str] = set()

    async def _commit(self, changes: list[FileChange], message: str) -> None:
        await commit_changes(self.rest, self.owner, self.repo, self.branch, changes, message)

    async def create_item(self, role: Role, text: str, token: str) -> SourceItem:
        path = it_path("mx", f"{role.value}-{token}.md")
        await self._commit([FileChange.upsert(path, text)], f"matrix: add {role.value}")
        self.live_paths.add(path)
        return SourceItem(role=role, key=path, record_name=posixpath.basename(path),
                          text=text, token=token)

    async def update_content(self, item: SourceItem, text: str, token: str) -> SourceItem:
        await self._commit([FileChange.upsert(item.key, text)], "matrix: edit content")
        return SourceItem(role=item.role, key=item.key, record_name=item.record_name, text=text,
                          token=token, extra=dict(item.extra))

    async def update_metadata(self, item: SourceItem) -> SourceItem:
        new_path = it_path("mx", f"renamed-{item.token}.md")
        # One commit with the same content, so compare reports it as a rename.
        await self._commit(
            [FileChange(item.key, None), FileChange.upsert(new_path, item.text)], "matrix: rename",
        )
        self.live_paths.discard(item.key)
        self.live_paths.add(new_path)
        return SourceItem(role=item.role, key=new_path, record_name=posixpath.basename(new_path),
                          text=item.text, token=item.token, extra=dict(item.extra))

    async def delete_item(self, item: SourceItem) -> None:
        await self._commit([FileChange(item.key, None)], "matrix: delete")
        self.live_paths.discard(item.key)

    async def cleanup(self) -> None:
        if not self.live_paths:
            return
        try:
            await self._commit([FileChange(p, None) for p in sorted(self.live_paths)],
                               "matrix: teardown")
            self.live_paths.clear()
        except Exception as e:  # noqa: BLE001 - the suite's stale-namespace sweep catches leaks
            logger.warning("TEARDOWN: could not delete %s: %s", sorted(self.live_paths), e)
