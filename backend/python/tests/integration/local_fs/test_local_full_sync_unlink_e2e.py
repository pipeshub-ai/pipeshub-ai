"""A full sync of Local Repository and Local FS through the real processor and the sync runner's unlink.

The runner ends a full sync by unlinking every stored node the run neither
wrote nor noted kept, unless the connector says it did not read the whole
source. Here the connector, ``DataSourceEntitiesProcessor`` and
``run_sync_task`` all run for real over the in-memory graph of the Local FS
end-to-end tests; only the graph provider the runner unlinks through is faked,
so the nodes it would unlink are known. A file the run could not read is still
on disk: it and its folder keep their links. A file gone from disk does not.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.local_folder import connector as folder_module
from app.connectors.sources.local_folder import scan
from app.connectors.sources.local_fs.connector import (
    SYNC_ROOT_PATH_KEY,
    LocalFsConnector,
)
from app.connectors.sources.local_fs.models import LocalFsFileEvent, LocalFsPullBatch
from app.connectors.sources.local_repo import store as repo_store
from app.connectors.sources.local_repo.connector import LocalRepoConnector
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.local_fs.test_local_fs_sync_e2e import (
    DEVICE_ID,
    ORG_ID,
    OWNER,
    LocalFsDataStoreProvider,
)
from tests.support.sql_full_sync import full_sync_losses, run_full_sync
from tests.unit.connectors.sources.test_connector_workflow_integration import (
    MockArangoProvider,
    MockNeo4jProvider,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterator

    from app.connectors.core.base.connector.connector_service import BaseConnector

pytestmark = pytest.mark.asyncio

REPO_ID = "local-repo-e2e-1"
FS_ID = "local-fs-e2e-1"
REPO_KEY = "repo-key-e2e-0001"
NO_FILTERS = (FilterCollection(filters=[]), FilterCollection(filters=[]))


@pytest.fixture(params=["arango", "neo4j"])
def graph_store(request) -> MockArangoProvider | MockNeo4jProvider:
    store = MockArangoProvider() if request.param == "arango" else MockNeo4jProvider()
    for connector_id in (REPO_ID, FS_ID):
        store.upsert_node(CollectionNames.APPS.value, {
            "_key": connector_id, "isActive": True, "createdBy": OWNER.id, "ownerDeviceId": DEVICE_ID,
        })
    store.upsert_node(CollectionNames.USERS.value, {
        "_key": OWNER.id, "email": OWNER.email, "orgId": ORG_ID, "isActive": True, "fullName": OWNER.email,
    })
    return store


@pytest.fixture
def data_store_provider(graph_store) -> LocalFsDataStoreProvider:
    return LocalFsDataStoreProvider(graph_store)


@pytest.fixture
def processor(data_store_provider) -> DataSourceEntitiesProcessor:
    proc = DataSourceEntitiesProcessor(MagicMock(), data_store_provider, AsyncMock())
    proc.org_id = ORG_ID
    proc.messaging_producer = AsyncMock()
    proc.messaging_producer.send_messages = AsyncMock(side_effect=lambda _topic, items: [True] * len(items))
    proc.messaging_producer.send_message = AsyncMock(return_value=True)
    return proc


def stored(graph_store, connector_id: str) -> dict[str, str]:
    """Each stored record of the connector: external id -> record id."""
    return {
        doc["externalRecordId"]: key
        for key, doc in graph_store.collections.get(CollectionNames.RECORDS.value, {}).items()
        if doc.get("connectorId") == connector_id
    }


async def losses(
    connector: BaseConnector, connector_id: str, graph_store, at_source: Collection[str], *, unlinks: bool = True
) -> tuple[set[str], set[str]]:
    """Run a full sync through the runner, which ``unlinks`` or not; return the records still at the source
    it lost, and the gone ones it removed."""
    before = stored(graph_store, connector_id)
    seen = await run_full_sync(connector, connector_id)
    after = stored(graph_store, connector_id)
    assert (seen is not None) == unlinks
    seen = set(before.values()) if seen is None else seen
    return full_sync_losses(before, after, seen, connector.full_sync_coverage(), at_source, at_source)


# ---------------------------------------------------------------------------
# Local Repository: FolderConnector.run_sync over the pushed mirror
# ---------------------------------------------------------------------------


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
def repo(tmp_path, monkeypatch) -> repo_store.Repo:
    monkeypatch.setenv(repo_store.BASE_ENV, str(tmp_path))
    mirror = repo_store.Repo(ORG_ID, OWNER.id, REPO_KEY)
    mirror.save_meta(name="shop")
    files = {"src/app.py": "def run():\n    return 1\n", "lib/util.py": "u = 1\n", "gone.py": "g = 1\n"}
    mirror.store([{"path": p, "sha256": _sha(t), "content": t} for p, t in files.items()])
    mirror.commit()
    return mirror


@pytest.fixture
def repo_connector(processor, data_store_provider) -> LocalRepoConnector:
    config = AsyncMock()
    config.get_config = AsyncMock(return_value={"sync": {"customValues": {"repo_key": REPO_KEY}}})
    c = LocalRepoConnector(MagicMock(), processor, data_store_provider, config, REPO_ID, "personal", OWNER.id)
    c.record_sync_point.read_sync_point = AsyncMock(return_value={})  # event_service deleted the sync points
    c.record_sync_point.update_sync_point = AsyncMock()
    return c


def refusing(rel: str) -> Callable[..., int]:
    """``scan.safe_open`` that refuses ``rel`` as a file the process may not read."""
    real = scan.safe_open

    def open_(root, path, max_links=1) -> int:
        if path == rel:
            raise scan.PathRefused("cannot open: Permission denied")
        return real(root, path, max_links)

    return open_


@pytest.fixture
def no_folder_filters() -> Iterator[None]:
    with patch.object(folder_module, "load_connector_filters", AsyncMock(return_value=NO_FILTERS)):
        yield


async def test_a_local_repository_full_sync_keeps_an_unreadable_file_and_its_folder_and_unlinks_what_is_gone(
    repo, repo_connector, graph_store, no_folder_filters
) -> None:
    await repo_connector.run_sync()
    assert set(stored(graph_store, REPO_ID)) == {"src", "src/app.py", "lib", "lib/util.py", "gone.py"}
    repo.diff([], False, ["gone.py"])
    repo.commit()

    with patch.object(scan, "safe_open", refusing("lib/util.py")):
        lost, removed = await losses(repo_connector, REPO_ID, graph_store, {"src", "src/app.py", "lib", "lib/util.py"})

    assert lost == set()  # still on disk: unreadable this run is not gone
    assert removed == {"gone.py"}


async def test_a_local_repository_full_sync_that_cannot_list_stored_records_unlinks_nothing(
    repo, repo_connector, graph_store, processor, no_folder_filters
) -> None:
    await repo_connector.run_sync()

    with patch.object(scan, "safe_open", refusing("lib/util.py")), patch.object(
        processor, "get_records_by_status", AsyncMock(side_effect=RuntimeError("graph unavailable"))
    ):
        lost, _removed = await losses(
            repo_connector, REPO_ID, graph_store, {"src", "src/app.py", "lib", "lib/util.py", "gone.py"}, unlinks=False,
        )

    assert lost == set()


# ---------------------------------------------------------------------------
# Local FS: the desktop's full walk
# ---------------------------------------------------------------------------


@pytest.fixture
def fs_connector(processor, data_store_provider, tmp_path) -> LocalFsConnector:
    config = AsyncMock()
    config.get_config = AsyncMock(return_value={"sync": {SYNC_ROOT_PATH_KEY: str(tmp_path), "batchSize": "50"}})
    c = LocalFsConnector(MagicMock(), processor, data_store_provider, config, FS_ID, "personal", OWNER.id)
    c.record_sync_point.read_sync_point = AsyncMock(return_value={})  # event_service deleted the sync points
    c.record_sync_point.update_sync_point = AsyncMock()
    return c


def _file(path: str, sha256: str | None) -> LocalFsFileEvent:
    return LocalFsFileEvent(
        type="CREATED", path=path, isDirectory=False, timestamp=get_epoch_timestamp_in_ms(),
        mimeType="text/plain", size=len(path), sha256=sha256,
    )


def desktop_walk(*events: LocalFsFileEvent) -> AsyncMock:
    """``_pull_with_retry`` answering one page with these events, as the desktop's walk."""

    async def pull(*, run_id, batch_index, cursor, mode, session, expected_device_id) -> LocalFsPullBatch:
        return LocalFsPullBatch(
            connectorId=FS_ID, runId=run_id, batchIndex=batch_index, deviceId=DEVICE_ID,
            cursor="c0", hasMore=False, events=list(events),
        )

    return AsyncMock(side_effect=pull)


async def test_a_local_fs_full_sync_keeps_a_file_sent_without_a_hash_and_unlinks_what_is_gone(
    fs_connector, graph_store
) -> None:
    ext = fs_connector._external_record_id_for_rel_path
    docs = LocalFsFileEvent(type="DIR_CREATED", path="docs", isDirectory=True, timestamp=get_epoch_timestamp_in_ms())
    with patch("app.connectors.sources.local_fs.connector.load_connector_filters", AsyncMock(return_value=NO_FILTERS)):
        fs_connector._pull_with_retry = desktop_walk(
            docs, _file("docs/a.txt", _sha("a")), _file("docs/locked.pdf", _sha("locked")), _file("gone.txt", _sha("g")),
        )
        await fs_connector.run_sync()
        assert {ext(p) for p in ("docs", "docs/a.txt", "docs/locked.pdf", "gone.txt")} <= set(stored(graph_store, FS_ID))

        # The next full walk: gone.txt was deleted, and docs/locked.pdf changed but could not be read, so it comes without a hash.
        fs_connector._pull_with_retry = desktop_walk(docs, _file("docs/a.txt", _sha("a")), _file("docs/locked.pdf", None))
        lost, removed = await losses(
            fs_connector, FS_ID, graph_store, {ext(p) for p in ("docs", "docs/a.txt", "docs/locked.pdf")},
        )

    assert lost == set()
    assert removed == {ext("gone.txt")}
