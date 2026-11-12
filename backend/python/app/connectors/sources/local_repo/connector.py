"""Local Repository connector: a repository a client pushed to the server.

Everything is the Folder connector's (walk, .gitignore, manifest reconcile, deletes, records, content read-back) except where the
root comes from. A Folder root is a host path an administrator allowed; here the root is the server's own mirror of the pushed
files, derived from the org and user of the connector's owner and the repository key in its config (``store.tree_of``). The key
can only ever select one of the owner's own mirrors, and the whole root is one repository, so its code files are filed under
the repository group the code graph lists.
"""
import asyncio  # noqa: I001
import os
from pathlib import Path
from typing import Any

from app.config.constants.arangodb import Connectors, PermissionModel, ProgressStatus
from app.connectors.core.registry.connector_builder import (
    CommonFields,
    ConnectorBuilder,
    ConnectorScope,
    CustomField,
    SyncStrategy,
)
from app.connectors.sources.local_folder import scan
from app.connectors.sources.local_folder.connector import FolderConnector, _cfg
from app.connectors.sources.local_repo import code_rule, store
from app.connectors.sources.local_repo.store import LOCAL_REPO_CONNECTOR_NAME, REPO_KEY



@(
    ConnectorBuilder(LOCAL_REPO_CONNECTOR_NAME)
    .in_group("Local Storage")
    .with_supported_auth_types("NONE")
    .with_description("A repository pushed from a developer's machine (pipeshub index). Code is indexed with its language.")
    .with_info("Created by pipeshub index. Only the person who pushed the repository can see it.")
    .with_categories(["Local", "Code"])
    .with_scopes([ConnectorScope.PERSONAL.value])
    .with_permission_model(PermissionModel.APP_LEVEL)
    .configure(
        lambda builder: builder.with_icon("/icons/connectors/folder.svg")
        .with_realtime_support(False)
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL], selected=SyncStrategy.SCHEDULED)
        .with_scheduled_config(True, 15)
        .with_sync_support(True)
        .with_agent_support(False)
        .with_hide_connector(True)  # not offered in the "add a connector" list: pipeshub index creates it
        .add_sync_custom_field(
            CustomField(name=REPO_KEY, display_name="Repository key", field_type="TEXT", required=False,
                        description="Set by pipeshub index. Do not change.")
        )
        .add_sync_custom_field(CommonFields.batch_size_field())
        .add_filter_field(CommonFields.file_extension_filter())
    )
    .build_decorator()
)
class LocalRepoConnector(FolderConnector):
    CONNECTOR = Connectors.LOCAL_REPO
    FILTER_KEY = "localrepository"
    DEFAULT_IGNORES = scan.PUSHED_REPO_IGNORES  # the client already applied .gitignore; see scan.PUSHED_REPO_IGNORES

    MAX_LINKS = 8  # a generation shares the inodes of the files it has in common with the next one and with the staging area (all server-made)

    async def run_sync(self) -> None:
        """One generation is read for the whole sync: pinned (marked, so no commit removes it) when the sync first needs the root."""
        self._pinned: tuple[Path, Any] | None = None
        self._syncing = True
        try:
            await super().run_sync()
        finally:
            self._syncing = False
            pinned, self._pinned = self._pinned, None
            if pinned is not None:
                await asyncio.to_thread(pinned[1])  # removing the mark touches the disk

    def _resolve_root(self, sync_cfg: Any) -> Path:  # noqa: ANN401
        # The refusals of the syncs so far, by path. Not reset here: the indexer's content reads call this too, in the middle of a sync, and a reset
        # then forgot every file refused before it. A stale entry is harmless: _sync_finished drops paths that are gone or have a record.
        self.__dict__.setdefault("_excluded", {})
        key = self._repo_key = str(_cfg(sync_cfg, REPO_KEY, "")).strip()
        try:
            repo = store.Repo(self.data_entities_processor.org_id, self.created_by, key)
            if getattr(self, "_syncing", False):
                # Content reads during the sync come through here too: they get the generation the sync is reading, not a newer one.
                if getattr(self, "_pinned", None) is None:
                    self._pinned = repo.pin()
                root: Path | None = self._pinned[0]
            else:
                root = repo.current_tree()
        except store.StoreError as exc:
            if exc.reason == "not_pushed":
                raise scan.PathRefused("nothing has been pushed to this repository yet") from exc
            raise scan.PathRefused(f"repository is not available ({exc.reason})") from exc
        if root is None:
            raise scan.PathRefused("nothing has been pushed to this repository yet")
        self._repo_dir = str(repo.dir)
        self.repo_name = repo.meta().get("name")
        self.web_base = f"local-repo://{self.connector_id}"  # not the repository key: the key is the client's handle and does not belong in a search result
        return root

    def _root_identity(self) -> str:
        # The repository, not the generation: every commit is a new generation, and a changed identity would re-index every file.
        return self._repo_dir

    def _may_index_code(self, entry: scan.Entry, name: str) -> tuple[bool, Any]:  # noqa: ANN401, RUF100
        """The shared rule (``code_rule.decide``) on the file as the sync reads it; a refusal is remembered with its reason."""
        ok, role, why = code_rule.decide(entry.rel, lambda: self._head(entry))
        self._excluded.pop(entry.rel, None)
        if why:
            self._excluded[entry.rel] = why
        return ok, role

    def _head(self, entry: scan.Entry) -> bytes | None:
        try:
            fd = scan.safe_open(self.root, entry.rel, self.MAX_LINKS)
        except (scan.PathRefused, OSError):
            return None
        try:
            return os.pread(fd, code_rule.HEAD_BYTES, 0)
        finally:
            os.close(fd)

    async def _sync_finished(self, live: dict[str, int], walked: set[str] | None = None) -> None:
        # What `status` shows: the files left out altogether, by reason.
        repo = store.Repo(self.data_entities_processor.org_id, self.created_by, self._repo_key)
        excluded = {rel: why for rel, why in {**repo.meta().get("excludedFiles", {}), **self._excluded}.items() if walked is None or rel in walked}
        # a file that has a record now is no longer excluded
        excluded = {rel: why for rel, why in excluded.items() if rel not in live}
        await asyncio.to_thread(repo.save_meta, excludedFiles=excluded)

    def _find_repos(self, entries: list[scan.Entry]) -> tuple[str, ...]:
        return ("",)  # the mirror is one repository, with no .git of its own

    def _file_record(self, entry: scan.Entry, sha: str, manual_only: bool):  # noqa: ANN202, FBT001
        rec = super()._file_record(entry, sha, manual_only)
        # An image (SVG included) is described by a vision model; with none set up (a fresh standalone) its record would fail and with it the
        # repository. It is left unindexed (not published to the indexer) rather than failed.
        tabular = str(getattr(rec, "extension", "") or "").lower() in ("csv", "tsv")  # read through a model, like an image
        if rec is not None and (str(getattr(rec, "mime_type", "")).startswith("image/") or tabular) and not manual_only:
            rec.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
        return rec
