"""Read a record's current content revision from its artifact set.

The inverse of ``writer.py``: the files the manifest lists are fetched, checked
against the manifest's sizes and sha256s, and put back together as the dict the
stored record would be read as, images inlined again as data URIs. The
classification comes from the classify stage's sidecar when there is one (it
is written after indexing, beside the stored record's own update), else from
``metadata.json``.

A set that cannot be served raises ``ArtifactUnavailable``, never a partial
record, so the caller can read the stored record instead.
"""

import asyncio
import base64
import functools
import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from app.modules.artifacts.models import (
    ASSET_MEDIA_TYPE,
    MANIFEST_PATH,
    WRITER_VERSION,
    ArtifactFile,
    Manifest,
    SignedFileUrl,
)
from app.modules.artifacts.store import NodeArtifactStore
from app.modules.transformers.blob_storage import (
    StorageDocumentNotFoundError,
    clean_stored_record,
    max_decoded_record_bytes,
)
from app.telemetry.modules.record_storage_metrics import record_artifact_read

if TYPE_CHECKING:
    from app.modules.transformers.blob_storage import BlobStorage

#: Files of one record fetched at once.
READ_CONCURRENCY = 4

RECORD_PATH = "record.json"
GROUPS_PATH = "groups.json"
METADATA_PATH = "metadata.json"
_FIRST_BLOCKS_PART = "blocks/part-0000.jsonl"
_MEDIA_TYPES = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}

logger = logging.getLogger(__name__)


class ArtifactUnavailable(Exception):
    """The artifact set cannot be served; ``outcome`` says why (a metric label)."""

    def __init__(self, outcome: str, detail: str = "") -> None:
        super().__init__(f"{outcome}: {detail}" if detail else outcome)
        self.outcome = outcome


@functools.cache
def classification_sidecar_path() -> str:
    # Imported late: the stage module pulls in the extraction client and the
    # pipeline, which the query service otherwise never loads.
    from app.modules.pipeline.stages.classify import ClassifyStage

    return f"enrich/classification@{ClassifyStage.version}.json"


def _with_classification(index_time: dict[str, Any] | None, sidecar: dict[str, Any]) -> dict[str, Any]:
    from app.modules.pipeline.stages.record_io import with_classification

    return with_classification(index_time, sidecar)


def _json_object(data: bytes, what: str) -> dict[str, Any]:
    try:
        value = json.loads(data)
    except ValueError:
        raise ArtifactUnavailable("invalid", f"{what} is not JSON") from None
    if not isinstance(value, dict):
        raise ArtifactUnavailable("invalid", f"{what} is not an object")
    return value


def _verify(path: str, data: bytes, expected: ArtifactFile) -> None:
    if len(data) != expected.size or hashlib.sha256(data).hexdigest() != expected.sha256:
        raise ArtifactUnavailable("integrity", path)


class ArtifactRecordBackend:
    """``RecordArtifactReader`` over the artifact set the mapping's ``currentRev`` names."""

    def __init__(
        self,
        blob_store: "BlobStorage",
        store: NodeArtifactStore | None = None,
        *,
        read_concurrency: int = READ_CONCURRENCY,
    ) -> None:
        self._blob_store = blob_store
        self._store = store or NodeArtifactStore(blob_store)
        self._read_concurrency = max(1, read_concurrency)

    async def lookup_many(self, virtual_record_ids: list[str]) -> dict[str, dict[str, Any]]:
        return await self._blob_store.get_document_ids_by_virtual_record_ids(virtual_record_ids)

    async def read_record(
        self,
        virtual_record_id: str,
        org_id: str,
        *,
        lookup: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """The record, or None when its artifact set cannot be served."""
        try:
            return await self.read(virtual_record_id, org_id, lookup=lookup)
        except ArtifactUnavailable:
            return None

    async def read(
        self,
        virtual_record_id: str,
        org_id: str,
        *,
        lookup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """The record in the stored-record shape. Raises ``ArtifactUnavailable``."""
        try:
            record = await self._read(virtual_record_id, org_id, lookup)
        except ArtifactUnavailable as unavailable:
            outcome, detail = unavailable.outcome, str(unavailable)
        except StorageDocumentNotFoundError:
            outcome, detail = "missing", "storage has no such file or set"
        except Exception as exc:
            # Only the type: an aiohttp error can carry a signed URL.
            outcome, detail = "failed", type(exc).__name__
        else:
            record_artifact_read("served")
            return record
        record_artifact_read(outcome)
        logger.debug("Artifact set of %s not served: %s", virtual_record_id, detail)
        raise ArtifactUnavailable(outcome, detail)

    async def _read(
        self, virtual_record_id: str, org_id: str, lookup: dict[str, Any] | None
    ) -> dict[str, Any]:
        if lookup is None:
            lookup = await self._blob_store.get_document_id_by_virtual_record_id(virtual_record_id)
        rev = (lookup or {}).get("currentRev")
        document_id = (lookup or {}).get("manifestDocId")
        if not rev or not document_id:
            raise ArtifactUnavailable("no_pointer")

        limit = asyncio.Semaphore(self._read_concurrency)
        sidecar_path = classification_sidecar_path()
        # Every revision has these, or may: signed and fetched with the manifest
        # in one round, so a record without images or a second blocks part needs
        # one read-urls call and one round of downloads.
        first = [MANIFEST_PATH, RECORD_PATH, GROUPS_PATH, _FIRST_BLOCKS_PART, METADATA_PATH, sidecar_path]
        urls = await self._store.read_urls(org_id, document_id, first)
        fetched = await self._fetch(urls, first, limit, optional=True)

        manifest = self._manifest(fetched.get(MANIFEST_PATH), virtual_record_id, rev, org_id)
        wanted = self._wanted_files(manifest)
        total = sum(f.size for f in wanted.values())
        limit_bytes = max_decoded_record_bytes()
        if limit_bytes is not None and total > limit_bytes:
            raise ArtifactUnavailable("too_large")

        rest = [path for path in wanted if fetched.get(path) is None]
        if rest:
            missing_urls = [path for path in rest if path not in urls]
            if missing_urls:
                urls = {**urls, **await self._store.read_urls(org_id, document_id, missing_urls)}
            fetched.update(await self._fetch(urls, rest, limit, optional=False))
        for path, expected in wanted.items():
            _verify(path, fetched[path], expected)

        return self._assemble(manifest, fetched, wanted, fetched.get(sidecar_path))

    async def _fetch(
        self,
        urls: dict[str, SignedFileUrl],
        paths: list[str],
        limit: asyncio.Semaphore,
        *,
        optional: bool,
    ) -> dict[str, bytes | None]:
        async def one(path: str) -> bytes | None:
            entry = urls.get(path)
            if entry is None:
                raise ArtifactUnavailable("invalid", f"storage signed no URL for {path}")
            async with limit:
                if optional:
                    return await self._store.get_if_present(entry)
                return await self._store.get(entry)

        # A TaskGroup cancels the other downloads on the first failure.
        try:
            async with asyncio.TaskGroup() as group:
                tasks = {path: group.create_task(one(path)) for path in paths}
        except ExceptionGroup as failed:
            raise failed.exceptions[0] from None
        return {path: task.result() for path, task in tasks.items()}

    @staticmethod
    def _manifest(data: bytes | None, virtual_record_id: str, rev: str, org_id: str) -> Manifest:
        if data is None:
            raise ArtifactUnavailable("missing", "no manifest")
        try:
            manifest = Manifest.model_validate_json(data)
        except ValidationError:
            raise ArtifactUnavailable("invalid", "manifest") from None
        if (manifest.virtualRecordId, manifest.rev, manifest.orgId) != (virtual_record_id, rev, org_id):
            raise ArtifactUnavailable("invalid", "manifest is another revision's")
        # Sets from an earlier layout lose what the stored record keeps (an image
        # URI's spelling, a code parse's fact version): the stored record serves them.
        if manifest.writerVersion != WRITER_VERSION:
            raise ArtifactUnavailable("invalid", f"writer version {manifest.writerVersion}")
        return manifest

    @staticmethod
    def _wanted_files(manifest: Manifest) -> dict[str, ArtifactFile]:
        artifacts = manifest.artifacts
        wanted: dict[str, ArtifactFile] = {}
        for kind, path in (("record", RECORD_PATH), ("groups", GROUPS_PATH), ("metadata", METADATA_PATH)):
            entry = artifacts.get(kind)
            if entry is None:
                if kind == "metadata":
                    continue
                raise ArtifactUnavailable("invalid", f"manifest lists no {kind}")
            if entry.path != path or entry.sha256 is None:
                raise ArtifactUnavailable("invalid", f"manifest {kind} entry")
            wanted[path] = ArtifactFile(path=path, size=entry.size, sha256=entry.sha256)
        blocks, images = artifacts.get("blocks"), artifacts.get("images")
        if blocks is None or blocks.parts is None or images is None or images.files is None:
            raise ArtifactUnavailable("invalid", "manifest lists no blocks or images")
        for file in [*blocks.parts, *images.files]:
            wanted[file.path] = file
        return wanted

    @staticmethod
    def _assemble(
        manifest: Manifest,
        fetched: dict[str, bytes | None],
        wanted: dict[str, ArtifactFile],
        sidecar: bytes | None,
    ) -> dict[str, Any]:
        def data(path: str) -> bytes:
            raw = fetched[path]
            if raw is None:
                raise ArtifactUnavailable("missing", path)
            return raw

        record = _json_object(data(RECORD_PATH), RECORD_PATH)
        container_fields = record.pop("block_containers", None)

        blocks: list[Any] = []
        for part in manifest.artifacts["blocks"].parts or []:
            blocks.extend(_json_object(line, part.path) for line in data(part.path).split(b"\n") if line)
        try:
            groups = json.loads(data(GROUPS_PATH))
        except ValueError:
            raise ArtifactUnavailable("invalid", GROUPS_PATH) from None
        if not isinstance(groups, list):
            raise ArtifactUnavailable("invalid", GROUPS_PATH)
        if len(blocks) != manifest.artifacts["blocks"].count or len(groups) != manifest.artifacts["groups"].count:
            raise ArtifactUnavailable("integrity", "item count")

        images = {f.path: data(f.path) for f in manifest.artifacts["images"].files or []}
        for block in blocks:
            block_data = block.get("data")
            if isinstance(block_data, dict) and isinstance(block_data.get("asset"), str):
                block["data"] = ArtifactRecordBackend._inline_image(block_data, images)

        containers: dict[str, Any] = {"block_groups": groups, "blocks": blocks}
        if isinstance(container_fields, dict):
            containers.update(container_fields)
        record["block_containers"] = containers

        index_time = _json_object(data(METADATA_PATH), METADATA_PATH) if METADATA_PATH in wanted else None
        if sidecar is not None:
            record["semantic_metadata"] = _with_classification(
                index_time, _json_object(sidecar, classification_sidecar_path())
            )
        elif index_time is not None:
            record["semantic_metadata"] = index_time
        return clean_stored_record(record)

    @staticmethod
    def _inline_image(block_data: dict[str, Any], images: dict[str, bytes]) -> dict[str, Any]:
        path = block_data["asset"]
        raw = images.get(path)
        media_type = block_data.get(ASSET_MEDIA_TYPE) or _MEDIA_TYPES.get(path.rsplit(".", 1)[-1])
        if raw is None or not media_type:
            raise ArtifactUnavailable("invalid", "an image block names no stored image")
        inlined = {k: v for k, v in block_data.items() if k not in ("asset", ASSET_MEDIA_TYPE)}
        inlined["uri"] = f"data:{media_type};base64,{base64.b64encode(raw).decode('ascii')}"
        return inlined
