"""``NodeArtifactStore`` against a local stand-in for the storage service."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

import aiohttp
import pytest

from app.modules.artifacts import store as store_module
from app.modules.artifacts.models import SignedFileUrl
from app.modules.artifacts.store import (
    MAX_FILES_PER_CALL,
    ArtifactRoutesUnavailable,
    ArtifactStoreError,
    NodeArtifactStore,
    artifact_writes_available,
)
from tests.unit.modules.artifacts.fake_storage import SIGNATURE, FakeStorage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

REV = "0123456789abcdef"


@pytest.fixture(autouse=True)
def _routes_available() -> None:
    store_module.ARTIFACT_ROUTES.reset()


@pytest.fixture
async def storage() -> AsyncIterator[FakeStorage]:
    fake = FakeStorage()
    await fake.start()
    yield fake
    await fake.stop()


def _store(fake: FakeStorage, logger: logging.Logger | None = None) -> NodeArtifactStore:
    return NodeArtifactStore(fake.blob_storage(), logger or logging.getLogger("artifact-store-test"))


def _images(n: int) -> list[tuple[str, str]]:
    return [(f"images/{i:064x}.png", "image/png") for i in range(n)]


async def test_presign_splits_at_the_per_call_cap_on_one_document(storage: FakeStorage) -> None:
    files = _images(MAX_FILES_PER_CALL * 2 + 1)

    plan = await _store(storage).presign_revision("org-1", "vr-1", REV, files)

    assert [len(c["files"]) for c in storage.presign_calls] == [1000, 1000, 1]
    assert {c["rev"] for c in storage.presign_calls} == {REV}
    assert plan.document_id == FakeStorage.document_id("vr-1", REV)
    assert plan.root == FakeStorage.root("vr-1", REV)
    assert set(plan.files) == {p for p, _ in files}


async def test_a_missing_presign_route_turns_writes_off_with_one_warning(
    storage: FakeStorage, caplog: pytest.LogCaptureFixture,
) -> None:
    storage.presign_status = 404
    store = _store(storage)

    with caplog.at_level(logging.WARNING, logger="artifact-store-test"):
        for _ in range(3):
            with pytest.raises(ArtifactRoutesUnavailable):
                await store.presign_revision("org-1", "vr-1", REV, _images(1))

    assert artifact_writes_available() is False
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "no artifact routes" in warnings[0].getMessage()


async def test_other_presign_errors_leave_writes_on(storage: FakeStorage) -> None:
    storage.presign_status = 409

    with pytest.raises(aiohttp.ClientError):
        await _store(storage).presign_revision("org-1", "vr-1", REV, _images(1))

    assert artifact_writes_available() is True


@pytest.mark.parametrize("vendor", ["azure", "s3", "local"])
async def test_put_sends_exactly_the_signed_headers(storage: FakeStorage, vendor: str) -> None:
    storage.vendor = vendor
    store = _store(storage)
    plan = await store.presign_revision("org-1", "vr-1", REV, [("record.json", "application/json")])

    await store.put(plan.files["record.json"], b'{"a":1}', "application/json")

    stored = storage.puts[f"{FakeStorage.root('vr-1', REV)}/record.json"]
    assert stored.data == b'{"a":1}'
    signed = {k.lower(): v for k, v in plan.files["record.json"].headers.items()}
    for name, value in signed.items():
        assert stored.headers[name] == value
    assert ("x-ms-blob-type" in stored.headers) is (vendor == "azure")
    assert stored.headers["content-type"] == signed["content-type"]


@pytest.mark.parametrize("status", [200, 201, 204])
async def test_any_2xx_put_is_success(storage: FakeStorage, status: int) -> None:
    storage.put_status = status
    store = _store(storage)
    plan = await store.presign_revision("org-1", "vr-1", REV, [("groups.json", "application/json")])

    await store.put(plan.files["groups.json"], b"[]", "application/json")


@pytest.mark.parametrize("status", [301, 400, 403, 500])
async def test_a_non_2xx_put_raises_without_the_url(storage: FakeStorage, status: int) -> None:
    storage.put_status = status
    store = _store(storage)
    plan = await store.presign_revision("org-1", "vr-1", REV, [("groups.json", "application/json")])

    with pytest.raises(aiohttp.ClientError) as raised:
        await store.put(plan.files["groups.json"], b"[]", "application/json")

    assert SIGNATURE not in str(raised.value)
    assert "127.0.0.1" not in str(raised.value)


async def test_read_urls_get_and_ranges_list_and_delete(storage: FakeStorage) -> None:
    store = _store(storage)
    plan = await store.presign_revision("org-1", "vr-1", REV, [("text/part-0000.jsonl", "application/x-ndjson")])
    await store.put(plan.files["text/part-0000.jsonl"], b"0123456789", "application/x-ndjson")

    urls = await store.read_urls("org-1", plan.document_id, ["text/part-0000.jsonl"])
    assert await store.get(urls["text/part-0000.jsonl"]) == b"0123456789"
    assert await store.get(urls["text/part-0000.jsonl"], byte_range=(2, 4)) == b"234"

    revisions = await store.list_revisions("org-1", "vr-1")
    assert [r["rev"] for r in revisions] == [REV]
    assert await store.delete_revision("org-1", plan.document_id) == 1
    assert await store.list_revisions("org-1", "vr-1") == []


async def test_delete_sends_the_listed_stamp_and_deletes_when_unchanged(storage: FakeStorage) -> None:
    store = _store(storage)
    plan = await store.presign_revision("org-1", "vr-1", REV, [("record.json", "application/json")])
    await store.put(plan.files["record.json"], b"{}", "application/json")
    storage.stamps[plan.document_id] = 500

    assert await store.delete_revision("org-1", plan.document_id, if_unchanged_since=500) == 1

    assert storage.delete_queries == [{"ifUnchangedSince": "500"}]
    assert await store.list_revisions("org-1", "vr-1") == []


async def test_a_delete_refused_as_changed_returns_none_and_keeps_the_revision(storage: FakeStorage) -> None:
    store = _store(storage)
    plan = await store.presign_revision("org-1", "vr-1", REV, [("record.json", "application/json")])
    await store.put(plan.files["record.json"], b"{}", "application/json")
    storage.stamps[plan.document_id] = 501

    assert await store.delete_revision("org-1", plan.document_id, if_unchanged_since=500) is None

    assert [r["rev"] for r in await store.list_revisions("org-1", "vr-1")] == [REV]


async def test_a_delete_without_the_stamp_sends_none(storage: FakeStorage) -> None:
    await _store(storage).delete_revision("org-1", "doc-x")

    assert storage.delete_queries == [{}]


async def test_a_conflict_that_is_not_changed_is_an_error(storage: FakeStorage) -> None:
    storage.delete_conflict_body = {"error": {"message": "something else"}}

    with pytest.raises(ArtifactStoreError):
        await _store(storage).delete_revision("org-1", "doc-x", if_unchanged_since=1)



async def test_a_bounded_delete_is_one_attempt_that_stops_at_its_timeout(storage: FakeStorage) -> None:
    storage.delete_delay = 1.0
    started = time.monotonic()

    with pytest.raises(asyncio.TimeoutError):
        await _store(storage).delete_revision("org-1", "doc-x", if_unchanged_since=1, timeout_seconds=0.2)

    assert time.monotonic() - started < 0.9
    assert storage.delete_calls == 1


async def test_a_bounded_listing_is_one_attempt_that_stops_at_its_timeout(storage: FakeStorage) -> None:
    storage.list_delay = 1.0
    started = time.monotonic()

    with pytest.raises(asyncio.TimeoutError):
        await _store(storage).list_revisions("org-1", "vr-1", timeout_seconds=0.2)

    assert time.monotonic() - started < 0.9
    assert storage.list_calls == 1


async def test_a_bounded_listing_is_not_retried_on_a_transient_status(storage: FakeStorage) -> None:
    storage.list_status = 503

    with pytest.raises(aiohttp.ClientError):
        await _store(storage).list_revisions("org-1", "vr-1", timeout_seconds=5.0)

    assert storage.list_calls == 1


async def test_an_unbounded_listing_still_retries_a_transient_status(storage: FakeStorage) -> None:
    storage.list_status = 503

    with pytest.raises(aiohttp.ClientError):
        await _store(storage).list_revisions("org-1", "vr-1")

    assert storage.list_calls == 3


def test_signed_urls_stay_out_of_reprs() -> None:
    entry = SignedFileUrl(url=f"https://x/y?sig={SIGNATURE}", method="PUT", headers={"Authorization": "Bearer t"})
    assert SIGNATURE not in repr(entry) and SIGNATURE not in str(entry)
    assert "Bearer t" not in repr(entry)


async def test_a_delete_answered_lost_is_an_error_not_a_change(storage: FakeStorage) -> None:
    """`lost`: the files may be gone although the document stayed; it is never `changed`."""
    storage.delete_conflict_body = {"purged": 0, "reason": "lost"}

    with pytest.raises(ArtifactStoreError):
        await _store(storage).delete_revision("org-1", "doc-x", if_unchanged_since=1)
