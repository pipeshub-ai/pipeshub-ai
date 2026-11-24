"""Folder patterns on a real Samba share, walked by the SMB connector's own walker and client.

Needs a Samba share of its own: the tests write ``customer/`` at its top and
remove it afterwards. For example::

    docker run -d --name samba-folder-patterns -p 127.0.0.1:1446:445 dperson/samba -p \\
        -u "user;password" -s "patterns;/share;yes;no;no;user"
    SMB_IT_HOST=127.0.0.1 SMB_IT_PORT=1446 SMB_IT_SHARE=patterns SMB_IT_USERNAME=user \\
        SMB_IT_PASSWORD=password pytest tests/integration/network_share -m integration

Only the graph store is in memory; listing, stat and names come from the server.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import Connectors
from app.connectors.core.registry.filters import (
    Filter,
    FilterCollection,
    FilterType,
    ListOperator,
    SyncFilterKey,
)
from app.connectors.sources.network_share.operations import ShareWalkReport, walk_shares
from app.connectors.sources.network_share.record_mapper import RecordMapper

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.models.entities import FileRecord, RecordType
    from app.sources.external.smb.smb import SmbDataSource

    Samba = tuple[SmbDataSource, str]

pytestmark = [pytest.mark.integration, pytest.mark.docker]

_ENV = ("SMB_IT_HOST", "SMB_IT_PORT", "SMB_IT_SHARE", "SMB_IT_USERNAME", "SMB_IT_PASSWORD")
CUSTOMERS = ("acme", "beta", "gamma")
FOLDERS = ("quotes", "invoices", "Archive")


def _env() -> dict[str, str]:
    missing = [name for name in _ENV if not os.environ.get(name)]
    if missing:
        pytest.skip(f"no Samba share for this test: set {', '.join(missing)}")
    return {name: os.environ[name] for name in _ENV}


class _Store:
    """The records and sync points the walk writes, in memory."""

    def __init__(self) -> None:
        self.org_id = "org-1"
        self.records: dict[str, FileRecord] = {}
        self.points: dict[str, dict] = {}

    async def get_record_by_external_id(self, _connector_id: str, ext_id: str) -> FileRecord | None:
        return self.records.get(ext_id)

    async def get_record_by_external_revision_id(self, _connector_id: str, rev: str) -> FileRecord | None:
        return next((r for r in self.records.values() if r.external_revision_id == rev), None)

    async def on_new_records(self, batch: list) -> None:
        for record, _perms in batch:
            self.records[record.external_record_id] = record

    async def on_records_moved(self, batch: list) -> None:
        for old_id, record, _perms in batch:
            self.records.pop(old_id, None)
            self.records[record.external_record_id] = record

    async def get_records_by_record_type(self, _connector_id: str, _record_type: RecordType) -> list:
        return list(self.records.values())

    async def get_records_in_record_group(
        self, _connector_id: str, share: str, limit: int, after: str | None, **_kw: object
    ) -> list:
        ordered = sorted(self.records.values(), key=lambda r: r.id)
        if after is not None:
            ordered = [r for r in ordered if r.id > after]
        return [r for r in ordered if r.external_record_group_id == share][:limit]

    async def on_record_deleted(self, record_id: str) -> None:
        for ext_id, record in list(self.records.items()):
            if record.id == record_id:
                del self.records[ext_id]

    # The record sync point.
    async def read_sync_point(self, key: str) -> dict | None:
        return self.points.get(key)

    async def update_sync_point(self, key: str, value: dict) -> None:
        self.points[key] = value


@pytest.fixture(scope="module")
def samba() -> Iterator[Samba]:
    import smbclient

    from app.sources.client.smb.smb import SmbClient
    from app.sources.external.smb.smb import SmbDataSource

    env = _env()
    # The SMB client vets every session smbclient opens, and never dials loopback,
    # where a test's own Samba usually listens.
    patch = pytest.MonkeyPatch()
    patch.setattr("app.sources.client.smb.smb.dial_address", lambda server, **_: server)
    host, port, share = env["SMB_IT_HOST"], int(env["SMB_IT_PORT"]), env["SMB_IT_SHARE"]
    cache: dict = {}
    smbclient.register_session(
        host, username=env["SMB_IT_USERNAME"], password=env["SMB_IT_PASSWORD"], port=port, connection_cache=cache
    )
    root = rf"\\{host}\{share}"

    def unc(path: str) -> str:
        return root + "\\" + path.replace("/", "\\")

    def remove(path: str) -> None:
        if not smbclient.path.exists(unc(path), port=port, connection_cache=cache):
            return
        for dirpath, dirnames, filenames in smbclient.walk(unc(path), topdown=False, port=port, connection_cache=cache):
            for name in filenames:
                smbclient.remove(f"{dirpath}\\{name}", port=port, connection_cache=cache)
            for name in dirnames:
                smbclient.rmdir(f"{dirpath}\\{name}", port=port, connection_cache=cache)
        smbclient.rmdir(unc(path), port=port, connection_cache=cache)

    remove("customer")
    for customer in CUSTOMERS:
        for folder in FOLDERS:
            smbclient.makedirs(unc(f"customer/{customer}/{folder}"), exist_ok=True, port=port, connection_cache=cache)
            with smbclient.open_file(
                unc(f"customer/{customer}/{folder}/{folder.lower()}-{customer}.txt"), mode="w",
                port=port, connection_cache=cache,
            ) as handle:
                handle.write(f"{customer} {folder}\n")
    client = SmbClient(
        server=host, username=env["SMB_IT_USERNAME"], password=env["SMB_IT_PASSWORD"], port=port, share=share,
        logger=logging.getLogger("test.samba"),
    )
    yield SmbDataSource(client), share
    client.close()
    remove("customer")
    smbclient.reset_connection_cache(fail_on_error=False, connection_cache=cache)
    patch.undo()


async def _walk(samba: Samba, store: _Store, *values: str, exclude: bool = False) -> ShareWalkReport:
    data_source, share = samba
    filters = FilterCollection(filters=[Filter(
        key=SyncFilterKey.FOLDER_PATHS.value, value=list(values), type=FilterType.LIST,
        operator=ListOperator.NOT_IN if exclude else ListOperator.IN,
    )])
    report = await walk_shares(
        data_source=data_source, processor=store, mapper=RecordMapper(), logger=logging.getLogger("test.samba"),
        connector_name=Connectors.SMB, connector_id="smb-it", batch_size=50, scope="team", created_by="user-1",
        creator_email=None, shares=[share], sync_filters=filters, indexing_filters=FilterCollection(),
        record_sync_point=store, prune=True,
    )
    assert report.complete, report
    return report


def _paths(store: _Store, share: str) -> set[str]:
    # Only what the tests seeded: the share may hold other folders.
    return {
        i[len(share) + 1:] for i in store.records
        if i.startswith(f"{share}/customer") or i == f"{share}/customer"
    }


def _quotes(*customers: str) -> set[str]:
    out = {"customer"}
    for name in customers:
        out |= {f"customer/{name}", f"customer/{name}/quotes", f"customer/{name}/quotes/quotes-{name}.txt"}
    return out


def _parents_resolve(store: _Store) -> None:
    for record in store.records.values():
        assert record.parent_external_record_id is None or record.parent_external_record_id in store.records, (
            record.external_record_id
        )


async def test_the_quotes_folder_of_every_customer(samba: Samba) -> None:
    store = _Store()
    report = await _walk(samba, store, "customer/*/quotes")
    assert _paths(store, samba[1]) == _quotes(*CUSTOMERS)
    assert report.unmatched_patterns_item() is None
    _parents_resolve(store)


async def test_backslashes_separate_folders(samba: Samba) -> None:
    store = _Store()
    await _walk(samba, store, "customer\\*\\quotes")
    assert _paths(store, samba[1]) == _quotes(*CUSTOMERS)


async def test_every_quotes_folder_at_any_depth_and_no_other_folder(samba: Samba) -> None:
    store = _Store()
    await _walk(samba, store, "**/quotes")
    assert _paths(store, samba[1]) == _quotes(*CUSTOMERS)


async def test_not_in_any_archive_whatever_its_case(samba: Samba) -> None:
    store = _Store()
    await _walk(samba, store, "**/archive", exclude=True)
    expected = {"customer"}
    for name in CUSTOMERS:
        expected |= {f"customer/{name}"}
        for folder in ("quotes", "invoices"):
            expected |= {f"customer/{name}/{folder}", f"customer/{name}/{folder}/{folder}-{name}.txt"}
    assert _paths(store, samba[1]) == expected
    _parents_resolve(store)


async def test_narrowing_a_pattern_removes_exactly_what_it_leaves_out(samba: Samba) -> None:
    store = _Store()
    await _walk(samba, store, "customer/*/quotes")
    # A filter edit clears the sync points (a full sync follows), so the narrower scope is cleaned up.
    store.points.clear()
    await _walk(samba, store, "customer/acme/quotes")
    assert _paths(store, samba[1]) == _quotes("acme")
    _parents_resolve(store)


async def test_a_misspelled_pattern_is_reported(samba: Samba) -> None:
    store = _Store()
    report = await _walk(samba, store, "custmer/*/quotes")
    item = report.unmatched_patterns_item()
    assert item is not None and item.params["values"] == '"custmer/*/quotes"'
    assert _paths(store, samba[1]) == set()


async def test_adding_a_pattern_keeps_the_ids_of_a_saved_folder(samba: Samba) -> None:
    store = _Store()
    await _walk(samba, store, "Customer/acme")
    before = {i: (r.id, r.parent_external_record_id) for i, r in store.records.items()}
    assert f"{samba[1]}/Customer/acme/quotes/quotes-acme.txt" in before
    store.points.clear()
    await _walk(samba, store, "Customer/acme", "*/archive")
    after = {i: (r.id, r.parent_external_record_id) for i, r in store.records.items()}
    assert {i: after[i] for i in before} == before
    _parents_resolve(store)
