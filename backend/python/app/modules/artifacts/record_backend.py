"""Read a record's current content revision from its artifact set.

The inverse of ``writer.py``: the files the manifest lists are fetched, checked
against the manifest's sizes and sha256s, and put back together as the dict the
stored record would be read as, images inlined again as data URIs. The
classification is the one sidecar the mapping's ``storedMeta`` names, when it
names one (a metadata write filed it before it switched the mapping to the stored
record that has the same classification), else ``metadata.json``. A sidecar the
mapping does not name is never read.

A set that cannot be served raises ``ArtifactUnavailable``, never a partial
record, so the caller can read the stored record instead.
"""

import asyncio
import base64
import hashlib
import json
import logging
import re
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
from app.modules.transformers.stored_write import is_none_run, run_generation
from app.services.featureflag.platform_settings import artifact_read_state
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


#: A sidecar path the storage service accepts: ``enrich/<name>@<version>.json``.
_SIDECAR_PATH = re.compile(r"^enrich/[a-z0-9_.-]+@\d+\.json$")


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


def check_stored_write(lookup: dict[str, Any], generation: str | None) -> None:
    """Raise ``ArtifactUnavailable("stale")`` unless the set is of the stored write the mapping names.

    The pointer moves only after a revision's set is written, and a stored-record write whose set was
    not (a failed write, the write flag off, an in-place write) leaves it on an older one. The run says
    which write: the set is served only while the pointer's run is the stored record's run, the run is
    servable (not a ``none`` run) and of the generation in force, and the revisions agree. Every field is
    required: a mapping from before runs reads as stale.
    """
    current_run = lookup.get("currentRun")
    if not isinstance(current_run, str) or lookup.get("storedRun") != current_run:
        raise ArtifactUnavailable("stale", "the stored record is not the write this set was built from")
    if is_none_run(current_run):
        raise ArtifactUnavailable("stale", "the stored write is not servable")
    if generation is None or run_generation(current_run) != generation:
        raise ArtifactUnavailable("stale", "the run is of another generation")
    if lookup.get("storedRev") != lookup.get("currentRev"):
        raise ArtifactUnavailable("stale", "the stored record holds another revision")


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
        lookup = lookup or {}
        rev = lookup.get("currentRev")
        document_id = lookup.get("manifestDocId")
        if not rev or not document_id:
            raise ArtifactUnavailable("no_pointer")
        # One read of the settings per artifact read: a run is served only under the generation in force.
        state = await artifact_read_state(getattr(self._blob_store, "config_service", None))
        check_stored_write(lookup, state.generation)

        limit = asyncio.Semaphore(self._read_concurrency)
        stored_meta = lookup.get("storedMeta") or None
        if stored_meta is not None and not _SIDECAR_PATH.match(str(stored_meta)):
            raise ArtifactUnavailable("stale_sidecar", "the mapping names no sidecar path a set can hold")
        # Every revision has these, or may: signed and fetched with the manifest
        # in one round, so a record without images or a second blocks part needs
        # one read-urls call and one round of downloads. The classification is exactly the sidecar the
        # mapping names, when it names one: it is known from the lookup, so it costs no extra round.
        first = [MANIFEST_PATH, RECORD_PATH, GROUPS_PATH, _FIRST_BLOCKS_PART, METADATA_PATH]
        if stored_meta is not None:
            first.append(stored_meta)
        urls = await self._store.read_urls(org_id, document_id, first)
        fetched = await self._fetch(urls, first, limit, optional=True)

        manifest = self._manifest(fetched.get(MANIFEST_PATH), virtual_record_id, rev, org_id)
        if manifest.storedRun != lookup["currentRun"]:
            raise ArtifactUnavailable("stale", "the set was built from another stored write")
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

        classification = (
            self._classification(fetched.get(stored_meta), lookup, str(stored_meta)) if stored_meta is not None else None
        )
        return self._assemble(manifest, fetched, wanted, classification)

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
    def _classification(data: bytes | None, lookup: dict[str, Any], path: str) -> dict[str, Any]:
        """The classification of the sidecar the mapping names, if that file is the one it names.

        The envelope must say it was written for the stored write the mapping holds (``storedRun``)
        and at this very path (``storedMeta``): a sidecar of another write, or one copied to another
        path, is never served over this stored record.
        """
        if data is None:
            raise ArtifactUnavailable("stale_sidecar", "the sidecar the mapping names is not stored")
        try:
            envelope = json.loads(data)
        except ValueError:
            raise ArtifactUnavailable("stale_sidecar", "the sidecar is not JSON") from None
        classification = envelope.get("classification") if isinstance(envelope, dict) else None
        if (
            not isinstance(classification, dict)
            or envelope.get("storedRun") != lookup.get("currentRun")
            or envelope.get("storedMeta") != path
        ):
            raise ArtifactUnavailable("stale_sidecar", "the sidecar is not of this stored write")
        return classification

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
        classification: dict[str, Any] | None,
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
        if classification is not None:
            record["semantic_metadata"] = _with_classification(index_time, classification)
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
