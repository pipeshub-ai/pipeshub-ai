"""Fixtures for the GitLab connector behaviour tests.

A real ``GitLabConnector`` is built and initialised through its production
path (config lookup, ``GitLabClient.build_from_services``, python-gitlab,
``GitLabDataSource``). The only substitutions are the network, answered by
``FakeGitLab``, and our own stores (records, checkpoints, etcd config and the
token refresh service).
"""

from __future__ import annotations

import asyncio
import logging
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from gitlab_server_fake import BASE_URL, FakeGitLab
from gitlab_store_fakes import (
    FakeCheckpointStore,
    FakeConfigService,
    FakeDataStore,
    FakeRecordsDb,
    FakeTokenRefresher,
)
from gitlab_world import CONFIG_PATH, CONNECTOR_ID

import app.connectors.sources.gitlab.repos as repos_module
import app.sources.client.gitlab.gitlab as gitlab_client_module
from app.connectors.core.base.token_service.startup_service import startup_service
from app.connectors.sources.gitlab.connector import GitLabConnector

if TYPE_CHECKING:
    from collections.abc import AsyncIterator



class _RecordingAsyncio:
    """``asyncio`` for the repos module, with backoff sleeps recorded instead of waited out."""

    def __init__(self, slept: list[float]) -> None:
        self._slept = slept

    async def sleep(self, delay: float, *_: object) -> None:
        self._slept.append(delay)

    def __getattr__(self, name: str) -> object:
        return getattr(asyncio, name)


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

    async def close(self) -> None:
        for connector in self.connectors:
            await connector.cleanup()


@pytest.fixture
def gitlab() -> FakeGitLab:
    return FakeGitLab()


@pytest.fixture
def db() -> FakeRecordsDb:
    return FakeRecordsDb()


@pytest.fixture
def checkpoints() -> FakeCheckpointStore:
    return FakeCheckpointStore()


@pytest.fixture
async def harness(gitlab: FakeGitLab, db: FakeRecordsDb, checkpoints: FakeCheckpointStore) -> AsyncIterator[GitLabHarness]:
    h = GitLabHarness(gitlab, db, checkpoints)
    yield h
    await h.close()


@pytest.fixture(autouse=True)
def fake_network(monkeypatch: pytest.MonkeyPatch, gitlab: FakeGitLab) -> None:
    """Every ``requests`` session python-gitlab is given talks to the fake GitLab."""
    real_secure_session = gitlab_client_module._secure_session

    def secure_session(logger: logging.Logger | None = None):  # noqa: ANN202
        session = real_secure_session(logger)
        session.mount("https://", gitlab.requests_adapter())
        session.mount("http://", gitlab.requests_adapter())
        return session

    monkeypatch.setattr(gitlab_client_module, "_secure_session", secure_session)


@pytest.fixture(autouse=True)
def sdk_sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """python-gitlab's rate-limit and retry waits, recorded instead of slept."""
    import gitlab.utils as gitlab_utils

    slept: list[float] = []
    monkeypatch.setattr(gitlab_utils, "time", SimpleNamespace(sleep=slept.append, time=time.time))
    return slept


@pytest.fixture(autouse=True)
def repo_backoff(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    slept: list[float] = []
    monkeypatch.setattr(repos_module, "asyncio", _RecordingAsyncio(slept))
    return slept


@pytest.fixture(autouse=True)
def token_refresher(monkeypatch: pytest.MonkeyPatch, gitlab: FakeGitLab, harness: GitLabHarness) -> FakeTokenRefresher:
    refresher = FakeTokenRefresher(harness.config, CONFIG_PATH, gitlab, new_token="token-2")
    monkeypatch.setattr(startup_service, "_token_refresh_service", refresher)
    return refresher
