from __future__ import annotations

from dataclasses import dataclass, field

import aiohttp
import pytest

from app.exceptions.storage_exceptions import StorageRequestRejectedError
from app.harness.adapters.pipeshub_object_store import PipesHubBinaryObjectStore
from app.harness.contracts.workspace import ObjectRef, ObjectScope
from app.modules.transformers.blob_storage import (
    StorageDocumentNotFoundError,
    TransientStorageError,
)
from harness_core.testing.contracts.workspace import BinaryObjectStoreContract
from harness_core.workspace import ObjectStoreUnavailable


class FakeGateway:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.writes: list[tuple[str, str, str, str, str, bytes]] = []

    async def save_binary_to_storage(
        self,
        org_id: str,
        record_id: str,
        file_name: str,
        extension: str,
        content_type: str,
        binary_data: bytes,
    ) -> tuple[str, int]:
        self.writes.append((org_id, record_id, file_name, extension, content_type, binary_data))
        document_id = f"document-{len(self.writes)}"
        self.objects[(org_id, document_id)] = bytes(binary_data)
        return document_id, len(binary_data)

    async def read_binary_from_storage(
        self,
        org_id: str,
        document_id: str,
        *,
        version: int | None = None,
    ) -> bytes:
        del version
        try:
            return self.objects[(org_id, document_id)]
        except KeyError as exc:
            raise FileNotFoundError(document_id) from exc


async def test_round_trips_binary_data_through_tenant_scoped_gateway() -> None:
    gateway = FakeGateway()
    store = PipesHubBinaryObjectStore(gateway)
    scope = ObjectScope("org-1")

    ref = await store.put(scope, b"\x89PNG\x00\xff")

    assert await store.get(scope, ref) == b"\x89PNG\x00\xff"
    assert gateway.writes[0][0] == "org-1"
    assert gateway.writes[0][4] == "application/octet-stream"


async def test_reference_cannot_be_read_under_another_tenant() -> None:
    gateway = FakeGateway()
    store = PipesHubBinaryObjectStore(gateway)
    ref = await store.put(ObjectScope("org-1"), b"secret")

    with pytest.raises(FileNotFoundError):
        await store.get(ObjectScope("org-2"), ref)


@pytest.mark.parametrize(
    ("replacement", "message"),
    [(b"too long", "size"), (b"xxxxxx", "hash")],
)
async def test_rejects_storage_content_that_does_not_match_reference(
    replacement: bytes,
    message: str,
) -> None:
    gateway = FakeGateway()
    store = PipesHubBinaryObjectStore(gateway)
    scope = ObjectScope("org-1")
    ref = await store.put(scope, b"secret")
    gateway.objects[(scope.tenant_id, ref.object_id)] = replacement

    with pytest.raises(ValueError, match=message):
        await store.get(scope, ref)


async def test_rejects_incomplete_storage_write() -> None:
    class FailedGateway(FakeGateway):
        async def save_binary_to_storage(self, *args: object) -> tuple[None, None]:
            del args
            return None, None

    with pytest.raises(ObjectStoreUnavailable, match="did not persist"):
        await PipesHubBinaryObjectStore(FailedGateway()).put(ObjectScope("org-1"), b"data")


@pytest.mark.parametrize(
    "failure",
    [TransientStorageError("503"), aiohttp.ClientConnectionError("refused"), TimeoutError()],
    ids=["5xx", "connection", "timeout"],
)
async def test_ch09_a_read_the_storage_service_could_not_answer_is_an_outage(failure: Exception) -> None:
    """CH-09: an outage is typed, so the engine run waits for the store instead of crashing on it."""

    class DownGateway(FakeGateway):
        async def read_binary_from_storage(self, *args: object, **kwargs: object) -> bytes:
            raise failure

    store = PipesHubBinaryObjectStore(DownGateway())
    with pytest.raises(ObjectStoreUnavailable):
        await store.get(ObjectScope("org-1"), ObjectRef("document-1", "sha256:x", 1))


@pytest.mark.parametrize(
    "failure",
    [StorageDocumentNotFoundError("404"), StorageRequestRejectedError("400", 400)],
    ids=["missing", "rejected"],
)
async def test_ch09_a_missing_or_refused_object_is_not_an_outage(failure: Exception) -> None:
    """Waiting cannot bring back a deleted object or change a refusal: those stay what they are."""

    class RefusingGateway(FakeGateway):
        async def read_binary_from_storage(self, *args: object, **kwargs: object) -> bytes:
            raise failure

    store = PipesHubBinaryObjectStore(RefusingGateway())
    with pytest.raises(type(failure)):
        await store.get(ObjectScope("org-1"), ObjectRef("document-1", "sha256:x", 1))


async def test_rejects_caller_supplied_invalid_reference() -> None:
    gateway = FakeGateway()
    gateway.objects[("org-1", "document-1")] = b"content"
    store = PipesHubBinaryObjectStore(gateway)

    with pytest.raises(ValueError, match="hash"):
        await store.get(
            ObjectScope("org-1"),
            ObjectRef("document-1", "sha256:not-the-content", len(b"content")),
        )


async def test_md5_content_hash_matches_put_without_writing() -> None:
    gateway = FakeGateway()
    store = PipesHubBinaryObjectStore(gateway)
    scope = ObjectScope("org-1")

    expected = await store.content_hash(scope, b"bytes")

    assert gateway.writes == []
    assert (await store.put(scope, b"bytes")).content_hash == expected


@dataclass
class _Store:
    gateway: FakeGateway = field(default_factory=FakeGateway)
    scope: ObjectScope = field(default_factory=lambda: ObjectScope("org-1"))
    other_tenant: ObjectScope = field(default_factory=lambda: ObjectScope("org-2"))

    @property
    def objects(self) -> PipesHubBinaryObjectStore:
        return PipesHubBinaryObjectStore(self.gateway)


class TestPipesHubBinaryObjectStore(BinaryObjectStoreContract):
    """QA19-9: the worker's production object store, over a gateway keyed by org as PipesHub's storage is."""

    @pytest.fixture
    def harness(self) -> _Store:
        return _Store()
