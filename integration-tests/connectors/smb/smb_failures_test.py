# pyright: ignore-file

"""SMB connector: what a failed or partial sync must leave behind, through the deployed product.

Each test creates a personal connector limited to its own folder of the ``smb-source`` share, syncs, breaks
something, syncs again and then checks, as the person who created the connector, that every record still
opens and that the run was not recorded as a success. The cases (IDs in the audit's IT_CASES.md):

  TC-SMB-IT-001  first sync: one live record per file, and its creator opens every one
  TC-SMB-IT-002  the share is gone during a full sync: every record and who may open it stays
  TC-SMB-IT-003  the file server stops during a full sync: the same, and the run is not a success
  TC-SMB-IT-004  a wrong password: the run is not a success and nothing is removed
  TC-SMB-IT-005  a folder the account may no longer list leaves; nothing else does
  TC-SMB-IT-006  deleted files and folders leave after the next sync

TC-SMB-IT-002, TC-SMB-IT-003 and TC-SMB-IT-005 change the ``smb-source`` container
(``helper/compose_control.py``) and skip when the stack cannot be controlled from here. Inside that container
(the dockurr/samba image) the share's folder is ``SMB_SOURCE_PATH`` (default ``/shared``) and smbd reads
``SMB_SOURCE_CONFIG`` (default ``/etc/samba/smb.tmp``).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from collections.abc import AsyncGenerator, Callable
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from helper.compose_control import ComposeStack, ComposeUnavailable
from helper.record_access import record_access_status
from helper.run_folder import folder_filter, new_run_folder
from pipeshub_client import PipeshubClient, PipeshubClientError  # type: ignore[import-not-found]

from connectors.smb.conftest import _require_smb_creds, smb_connector_config
from connectors.smb.smb_storage_helper import SmbStorageHelper

logger = logging.getLogger("smb-failures-test")

SYNC_TIMEOUT_SEC = int(os.getenv("SMB_SYNC_TIMEOUT_SEC", "300"))
SYNC_POLL_SEC = 3
SERVICE = "smb-source"
SOURCE_PATH = os.getenv("SMB_SOURCE_PATH", "/shared")
SOURCE_CONFIG = os.getenv("SMB_SOURCE_CONFIG", "/etc/samba/smb.tmp")
RUNNING = {"SYNCING", "FULL_SYNCING", "QUEUED"}

FILES = {
    "Engineering/onboarding.md": b"Welcome aboard.\n",
    "Engineering/Runbooks/failover.md": b"Fail over the database.\n",
    "Finance/expense-guidelines.txt": b"Keep receipts.\n",
    "HR/Policies/leave.md": b"Annual leave rules.\n",
    "README.txt": b"Departments share.\n",
}


def succeeded(outcome: str | None) -> bool:
    return (outcome or "").upper() == "SUCCEEDED"


class SmbWorld:
    """One personal connector on one folder of the share; its creator must be able to open its records."""

    def __init__(self, client: PipeshubClient, storage: SmbStorageHelper) -> None:
        self.client = client
        self.storage = storage
        self.share = _require_smb_creds()["share"]
        # "it-<run>-<attempt>-<hex>/": the only kind of folder the storage helper will clear.
        self.run_folder = new_run_folder()
        self.folder = self.run_folder.rstrip("/")
        self.connector_id: str | None = None

    # ---- the share -------------------------------------------------------------------------------------
    def write(self, rel: str, data: bytes) -> None:
        self.storage.write_file(self.share, f"{self.folder}/{rel}", data)

    def delete(self, rel: str) -> None:
        self.storage.delete_file(self.share, f"{self.folder}/{rel}")

    def on_share(self) -> set[str]:
        """External ids of the files the share holds in this test's folder, as the connector names them."""
        return {f"{self.share}/{key}" for key in self.storage.list_objects(self.share, self.folder)}

    # ---- PipesHub --------------------------------------------------------------------------------------
    async def start(self) -> str | None:
        for rel, data in FILES.items():
            self.write(rel, data)
        config = smb_connector_config()
        created = self.client.create_connector(
            connector_type="SMB", instance_name=f"smb-it-{uuid.uuid4().hex[:6]}", scope="personal",
            config=config, auth_type="BASIC_AUTH",
        )
        self.connector_id = created.connector_id
        self.client.update_connector_filters_sync_config(self.connector_id, filters=folder_filter(self.run_folder))
        return await self._run(lambda: self.client.toggle_sync(self.connector_id, enable=True))

    def set_auth(self, **changes: Any) -> None:
        config = smb_connector_config()
        config["auth"].update(changes)
        self.client.toggle_sync(self.connector_id, enable=False)
        self.client.update_connector_config(self.connector_id, config)

    async def enable(self) -> str | None:
        try:
            return await self._run(lambda: self.client.toggle_sync(self.connector_id, enable=True))
        except PipeshubClientError as exc:
            # A connector whose settings do not work may be refused at enable time; that is not a success.
            logger.info("Enable refused: %s", exc)
            return "REFUSED"

    async def sync(self, *, full: bool = False) -> str | None:
        return await self._run(lambda: self.client.resync_connector(self.connector_id, full_sync=full))

    def _connector(self) -> dict[str, Any]:
        return self.client.get_connector(self.connector_id)

    async def _run(self, trigger: Callable[[], Any]) -> str | None:
        deadline = time.monotonic() + SYNC_TIMEOUT_SEC
        while self._connector().get("status") in RUNNING:
            assert time.monotonic() < deadline, "connector never went idle"
            await asyncio.sleep(SYNC_POLL_SEC)
        before = self._connector().get("lastSyncFinishedAtTimestamp") or 0
        trigger()
        while True:
            doc = self._connector()
            if (doc.get("lastSyncFinishedAtTimestamp") or 0) > before and doc.get("status") not in RUNNING:
                return doc.get("lastSyncOutcome")
            if time.monotonic() >= deadline:
                raise TimeoutError(f"no sync of {self.connector_id} ended within {SYNC_TIMEOUT_SEC}s")
            await asyncio.sleep(SYNC_POLL_SEC)

    async def file_records(self, graph_provider: Any) -> dict[str, str]:
        """External id -> record id of the live file records under this test's folder."""
        params = {"c": self.connector_id}
        if os.getenv("TEST_GRAPH_DB_TYPE", "neo4j").lower() == "arango":
            rows = await graph_provider.execute_query(
                "FOR r IN records FILTER r.connectorId == @c RETURN {id: r._key, ext: r.externalRecordId, "
                "deleted: r.isDeleted, mime: r.mimeType}", bind_vars=params)
        else:
            rows = await graph_provider.client.execute_query(
                "MATCH (r:Record {connectorId: $c}) RETURN r.id AS id, r.externalRecordId AS ext, "
                "r.isDeleted AS deleted, r.mimeType AS mime", params)
        prefix = f"{self.share}/{self.folder}/"
        return {r["ext"]: r["id"] for r in rows or []
                if not r.get("deleted") and r.get("mime") != "text/directory" and (r.get("ext") or "").startswith(prefix)}

    def _creator(self) -> SimpleNamespace:
        """The connector's creator, in the shape ``record_access_status`` reads, with a fresh session."""
        return SimpleNamespace(
            base_url=self.client.base_url, headers=self.client._headers(), timeout=self.client.timeout_seconds
        )

    async def assert_matches_share(self, graph_provider: Any, label: str) -> None:
        """Every file on the share has one live record, and the connector's creator opens each one."""
        want = self.on_share()
        records = await self.file_records(graph_provider)
        missing = sorted(want - set(records))
        stale = sorted(set(records) - want)
        creator = self._creator()
        denied = []
        for ext, rid in sorted(records.items()):
            if await asyncio.to_thread(record_access_status, creator, rid) != 200:
                denied.append(ext)
        assert not (missing or stale or denied), f"{label}: missing={missing} stale={stale} denied={denied}"

    def remove(self) -> None:
        try:
            self.storage.clear_objects(self.share, self.run_folder)
        except Exception:  # noqa: BLE001 - teardown must go on
            logger.exception("Could not clear %s", self.folder)
        if self.connector_id:
            for action in (lambda: self.client.toggle_sync(self.connector_id, enable=False),
                           lambda: self.client.delete_connector(self.connector_id)):
                try:
                    action()
                except Exception:  # noqa: BLE001 - teardown must go on
                    logger.exception("Teardown of connector %s failed", self.connector_id)


@pytest_asyncio.fixture(loop_scope="session")
async def smb_world(
    smb_storage: SmbStorageHelper, pipeshub_client: PipeshubClient
) -> AsyncGenerator[SmbWorld, None]:
    world = SmbWorld(pipeshub_client, smb_storage)
    try:
        yield world
    finally:
        world.remove()


def _rename_share(stack: ComposeStack, old: str, new: str) -> None:
    """Rename a share on the running server: the old name stops resolving, as when an admin renames it.

    A reload leaves connections already open on the old name working, so they are closed too, and the next
    sync has to open the share by name.
    """
    stack.exec(SERVICE, ["sed", "-i", f"s/^\\[{old}\\]/[{new}]/", SOURCE_CONFIG])
    stack.exec(SERVICE, ["smbcontrol", "all", "reload-config"])
    stack.exec(SERVICE, ["smbcontrol", "all", "close-share", old])


def _stack() -> ComposeStack:
    try:
        stack = ComposeStack.from_env()
        stack.require(SERVICE)
    except ComposeUnavailable as exc:
        pytest.skip(f"the {SERVICE} container cannot be controlled from here: {exc}")
    return stack


@pytest.mark.integration
@pytest.mark.smb
@pytest.mark.asyncio(loop_scope="session")
class TestSmbFailures:
    async def test_tc_smb_it_001_first_sync_matches_the_share(self, smb_world: SmbWorld, graph_provider) -> None:
        assert succeeded(await smb_world.start())
        await smb_world.assert_matches_share(graph_provider, "after the first sync")

    async def test_tc_smb_it_002_share_gone_during_a_full_sync_keeps_records_and_access(
        self, smb_world: SmbWorld, graph_provider
    ) -> None:
        stack = _stack()
        assert succeeded(await smb_world.start())
        hidden = f"{smb_world.share}-renamed"
        _rename_share(stack, smb_world.share, hidden)
        try:
            outcome = await smb_world.sync(full=True)
        finally:
            _rename_share(stack, hidden, smb_world.share)
            smb_world.storage.reconnect()
        await smb_world.assert_matches_share(graph_provider, "after a full sync with the share gone")
        assert not succeeded(outcome), "a full sync that could not open the share must not be a success"

    async def test_tc_smb_it_003_server_down_during_a_full_sync(self, smb_world: SmbWorld, graph_provider) -> None:
        stack = _stack()
        assert succeeded(await smb_world.start())
        stack.stop(SERVICE)
        try:
            outcome = await smb_world.sync(full=True)
        finally:
            stack.start(SERVICE)
            stack.wait_ready(SERVICE)
        await smb_world.assert_matches_share(graph_provider, "after a full sync with the server down")
        assert not succeeded(outcome), "a full sync that could not reach the server must not be a success"

    async def test_tc_smb_it_004_wrong_password(self, smb_world: SmbWorld, graph_provider) -> None:
        assert succeeded(await smb_world.start())
        smb_world.set_auth(password="not-the-password")
        outcome = await smb_world.enable()
        await smb_world.assert_matches_share(graph_provider, "after the server refused the password")
        assert not succeeded(outcome), "a sync the server refused must not be a success"

    async def test_tc_smb_it_005_a_folder_the_account_cannot_list_leaves(
        self, smb_world: SmbWorld, graph_provider
    ) -> None:
        stack = _stack()
        assert succeeded(await smb_world.start())
        locked = f"{SOURCE_PATH}/{smb_world.folder}/HR"
        stack.exec(SERVICE, ["chmod", "000", locked])
        try:
            outcome = await smb_world.sync()
            assert succeeded(outcome), "a folder the account may not list is a definite answer, not a failure"
            records = await smb_world.file_records(graph_provider)
            assert not [ext for ext in records if "/HR/" in ext], "records under the locked folder must leave"
            for rel in FILES:
                if not rel.startswith("HR/"):
                    assert f"{smb_world.share}/{smb_world.folder}/{rel}" in records, f"{rel} must stay"
        finally:
            stack.exec(SERVICE, ["chmod", "775", locked])

    async def test_tc_smb_it_006_deletions_leave(self, smb_world: SmbWorld, graph_provider) -> None:
        assert succeeded(await smb_world.start())
        smb_world.delete("README.txt")
        for rel in [r for r in FILES if r.startswith("Finance/")]:
            smb_world.delete(rel)
        assert succeeded(await smb_world.sync())
        await smb_world.assert_matches_share(graph_provider, "after deleting a file and a folder")
