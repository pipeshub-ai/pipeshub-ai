"""The GitLab connector built through its production path, against ``FakeGitLab`` and the fake stores.

A module of its own, not conftest.py, so tests can import it for their annotations.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest
from gitlab_server_fake import BASE_URL, FakeGitLab
from gitlab_store_fakes import (
    FakeCheckpointStore,
    FakeConfigService,
    FakeDataStore,
    FakeRecordsDb,
)
from gitlab_world import CONFIG_PATH, CONNECTOR_ID

from app.connectors.sources.gitlab.connector import (
    GitLabConnector,
    GitLabSyncIncompleteError,
)


class GitLabHarness:
    def __init__(self, server: FakeGitLab, db: FakeRecordsDb, checkpoints: FakeCheckpointStore) -> None:
        self.server = server
        self.db = db
        self.checkpoints = checkpoints
        self.config = FakeConfigService({CONFIG_PATH: {
            "auth": {"authType": "OAUTH", "instanceUrl": BASE_URL},
            "credentials": {"access_token": "token-1", "refresh_token": "refresh-0"},
        }})
        self.connectors: list[GitLabConnector] = []

    @property
    def stored(self) -> dict[str, Any]:
        return self.config.configs[CONFIG_PATH]

    def use_personal_access_token(self, token: str = "token-1") -> None:
        self.stored["auth"] = {"authType": "API_TOKEN", "token": token, "instanceUrl": BASE_URL}
        self.stored.pop("credentials", None)

    def set_sync_filter(self, key: str, operator: str, value: object, filter_type: str = "multiselect") -> None:
        values = self.stored.setdefault("filters", {}).setdefault("sync", {}).setdefault("values", {})
        values[key] = {"operator": operator, "type": filter_type, "value": value}

    def set_indexing_filter(self, key: str, enabled: bool) -> None:
        values = self.stored.setdefault("filters", {}).setdefault("indexing", {}).setdefault("values", {})
        values[key] = {"operator": "is", "type": "boolean", "value": enabled}

    async def connector(self, *, created_by: str = "creator-1") -> GitLabConnector:
        connector = GitLabConnector(
            logging.getLogger("gitlab-behaviour"), self.db, FakeDataStore(self.db),
            self.config, CONNECTOR_ID, "team", created_by,
        )
        connector.record_sync_point = self.checkpoints
        self.connectors.append(connector)
        assert await connector.init(), "connector failed to initialise against the fake GitLab"
        connector.data_source._http_client = httpx.AsyncClient(
            transport=self.server.httpx_transport(), follow_redirects=True,
        )
        return connector

    async def sync(self, connector: GitLabConnector | None = None) -> GitLabConnector:
        connector = connector or await self.connector()
        await connector.run_sync()
        task = connector._code_file_timestamp_backfill_task
        if task is not None:
            await task
        return connector

    async def sync_incomplete(self, connector: GitLabConnector | None = None) -> GitLabConnector:
        """A sync that could not read part of GitLab: it does the rest, then reports failure."""
        connector = connector or await self.connector()
        with pytest.raises(GitLabSyncIncompleteError):
            await connector.run_sync()
        task = connector._code_file_timestamp_backfill_task
        if task is not None:
            await task
        return connector

    async def close(self) -> None:
        for connector in self.connectors:
            await connector.cleanup()
