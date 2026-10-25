"""Write one content revision of a record as an artifact set.

Files of a revision (paths are relative to its root):

- ``record.json``: the stored copy's fields (the owner's identity for shared
  content), without its blocks or classification;
  ``block_containers`` holds only the container's other fields, when it has any.
- ``blocks/part-NNNN.jsonl``: one block per line, in block order, cut into
  parts of at most ``PART_MAX_BYTES`` (a single larger block gets a part of its
  own). Image data URIs are replaced by ``{"asset": "images/<sha256>.<ext>"}``,
  plus ``"asset_media_type"`` when the URI spelled its type otherwise
  (``image/jpg`` for a ``.jpg``), so the reader rebuilds the URI exactly.
- ``groups.json``: the block groups.
- ``images/<sha256>.<ext>``: each distinct image, decoded.
- ``text/part-NNNN.jsonl``: ``{"i", "g", "page", "text"}`` for every block with
  text: its index, its group's index, its page, its text. Same cut as blocks.
- ``metadata.json``: the classification, when there is one.
- ``reconciliation.json``: block hashes to ids and ids to indexes.
- ``properties.json``: the record's provided property values
  (``RecordMetadata``: ``{"schemaId", "values"}``), for a ticket that sets any
  of the built-in ticket schema's properties. Optional: a storage service that
  does not take the file yet gets the set without it.
- ``manifest.json``: what was written, with sizes and sha256s. Written last,
  only once every other file is stored, so a revision without it is incomplete.

Every JSON file is written with sorted keys, no whitespace and UTF-8 text, so
the same content always gives the same bytes and the same parts.
"""

import asyncio
import base64
import binascii
import hashlib
import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from app.config.constants.http_status_code import HttpStatusCode
from app.exceptions.storage_exceptions import StorageRequestRejectedError
from app.models.blocks import BlocksContainer, BlockType
from app.models.entities import Record
from app.models.record_metadata import RecordMetadata
from app.modules.artifacts.models import (
    ASSET_MEDIA_TYPE,
    MANIFEST_PATH,
    PROPERTIES_PATH,
    ArtifactEntry,
    ArtifactFile,
    ArtifactRef,
    Manifest,
    RevisionUploadPlan,
    WrittenRevision,
)
from app.modules.artifacts.store import (
    PROPERTIES_FILE,
    ArtifactRoutesUnavailable,
    NodeArtifactStore,
    artifact_writes_available,
)
from app.modules.record_metadata.builtin import ticket_metadata
from app.modules.transformers.blob_storage import SHARED_CONTENT_FIELDS
from app.utils.time_conversion import get_epoch_timestamp_in_ms

PART_MAX_BYTES = 8 * 1024 * 1024
# Part names carry four digits.
_MAX_PARTS = 10_000
UPLOAD_CONCURRENCY = 8

_JSON = "application/json"
_JSONL = "application/x-ndjson"
_IMAGE_TYPES = {"png": ("png", "image/png"), "jpeg": ("jpg", "image/jpeg"),
                "jpg": ("jpg", "image/jpeg"), "webp": ("webp", "image/webp")}
_DATA_URI = re.compile(r"^data:image/([a-z0-9.+-]+);base64,", re.IGNORECASE)
# The storage service accepts a revision id of 8 to 64 lowercase hex digits.
REV_PATTERN = re.compile(r"^[0-9a-f]{8,64}$")


def canonical_json(obj: Any) -> bytes:  # noqa: ANN401 - free-form content
    """Sorted keys, no whitespace, UTF-8 text: equal content, equal bytes."""
    try:
        return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        # A lone surrogate (broken text extraction) cannot be UTF-8; escape it
        # rather than lose the revision, as the stored record does.
        return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class BuiltFile:
    data: bytes
    content_type: str


@dataclass
class BuiltRevision:
    files: dict[str, BuiltFile] = field(default_factory=dict)
    artifacts: dict[str, ArtifactEntry] = field(default_factory=dict)

    def add(self, path: str, data: bytes, content_type: str) -> ArtifactFile:
        self.files[path] = BuiltFile(data, content_type)
        return ArtifactFile(path=path, size=len(data), sha256=_sha256(data))


def shard_lines(lines: Iterable[bytes], max_bytes: int = PART_MAX_BYTES) -> list[bytes]:
    """Cut newline-terminated lines into parts of at most ``max_bytes``.

    Lines are never split, so a line longer than ``max_bytes`` is a part of
    its own. The cut depends only on the lines, never on timing.
    """
    parts: list[bytes] = []
    current: list[bytes] = []
    size = 0
    for line in lines:
        if current and size + len(line) > max_bytes:
            parts.append(b"".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line)
    if current:
        parts.append(b"".join(current))
    if len(parts) > _MAX_PARTS:
        raise ValueError(f"{len(parts)} parts is more than a revision can name")
    return parts


def _externalise_image(block: dict[str, Any], images: dict[str, bytes]) -> None:
    """Move an image block's base64 data URI into ``images`` and point at it.

    Formats the storage service does not take (gif, svg, ...) and URIs that
    are not base64 data stay inline.
    """
    data = block.get("data")
    if not isinstance(data, dict):
        return
    uri = data.get("uri")
    if not isinstance(uri, str):
        return
    match = _DATA_URI.match(uri)
    if not match or match.group(1).lower() not in _IMAGE_TYPES:
        return
    encoded = uri[match.end():]
    try:
        raw = base64.b64decode(encoded, validate=False)
    except (binascii.Error, ValueError):
        return
    # A reader re-encodes the bytes; a URI that would not come back the same
    # (unpadded, wrapped) stays inline.
    if not raw or base64.b64encode(raw).decode("ascii") != encoded:
        return
    ext, canonical_type = _IMAGE_TYPES[match.group(1).lower()]
    path = f"images/{_sha256(raw)}.{ext}"
    images.setdefault(path, raw)
    externalised = {**{k: v for k, v in data.items() if k != "uri"}, "asset": path}
    media_type = uri[len("data:"):match.end() - len(";base64,")]
    if media_type != canonical_type:
        externalised[ASSET_MEDIA_TYPE] = media_type
    block["data"] = externalised


def _block_text(block: dict[str, Any]) -> str:
    data = block.get("data")
    if block.get("type") == BlockType.IMAGE.value:
        meta = block.get("image_metadata") or {}
        text = meta.get("description") if isinstance(meta, dict) else None
        return text if isinstance(text, str) else ""
    if isinstance(data, dict):
        key = "row_natural_language_text" if block.get("type") == BlockType.TABLE_ROW.value else "text"
        data = data.get(key)
    if data is None:
        return ""
    return data if isinstance(data, str) else canonical_json(data).decode("utf-8")


def _dump(value: BaseModel | dict[str, Any]) -> dict[str, Any]:
    return value.model_dump(mode="json", exclude_none=True) if isinstance(value, BaseModel) else dict(value)


def _add_parts(built: BuiltRevision, kind: str, folder: str, lines: list[bytes], max_bytes: int) -> None:
    files = [
        built.add(f"{folder}/part-{n:04d}.jsonl", part, _JSONL)
        for n, part in enumerate(shard_lines(lines, max_bytes))
    ]
    built.artifacts[kind] = ArtifactEntry(parts=files, count=len(lines), size=sum(f.size for f in files))


def _add_single(built: BuiltRevision, kind: str, path: str, value: Any, count: int) -> None:  # noqa: ANN401
    file = built.add(path, canonical_json(value), _JSON)
    built.artifacts[kind] = ArtifactEntry(path=path, count=count, size=file.size, sha256=file.sha256)


def build_revision(
    record: Record,
    block_containers: BlocksContainer,
    *,
    semantic_metadata: BaseModel | dict[str, Any] | None = None,
    reconciliation_metadata: dict[str, Any] | None = None,
    stored_record_fields: dict[str, Any] | None = None,
    properties: RecordMetadata | None = None,
    part_max_bytes: int = PART_MAX_BYTES,
) -> BuiltRevision:
    """Every file of the revision but the manifest, and the manifest's entries. Pure.

    ``stored_record_fields``: the stored copy's fields outside the shared content
    (``BlobStorage.apply``'s ``STORED_RECORD_FIELDS``); record.json then holds
    them instead of ``record``'s own.
    ``properties``: the record's provided values; left out, they are
    ``ticket_metadata(record)``.
    """
    built = BuiltRevision()
    snapshot = record.model_dump(
        mode="json", exclude_none=True, exclude={"block_containers", "semantic_metadata"}
    )
    if stored_record_fields is not None:
        # The stored copy's identity (its owner's, for shared content) with this
        # revision's content fields, so the set reads back as the stored copy.
        snapshot = {
            **{k: v for k, v in stored_record_fields.items() if k not in SHARED_CONTENT_FIELDS},
            **{k: v for k, v in snapshot.items() if k in SHARED_CONTENT_FIELDS},
        }
    # What the container holds besides its blocks and groups (a code parse's fact version).
    container_fields = block_containers.model_dump(
        mode="json", exclude_none=True, exclude={"blocks", "block_groups"}
    )
    if container_fields:
        snapshot["block_containers"] = container_fields
    _add_single(built, "record", "record.json", snapshot, 1)

    images: dict[str, bytes] = {}
    block_lines: list[bytes] = []
    text_lines: list[bytes] = []
    for position, block in enumerate(block_containers.blocks):
        dumped = block.model_dump(mode="json", exclude_none=True)
        _externalise_image(dumped, images)
        block_lines.append(canonical_json(dumped) + b"\n")
        text = _block_text(dumped)
        if text:
            page = (dumped.get("citation_metadata") or {}).get("page_number")
            index = dumped.get("index")
            text_lines.append(canonical_json({
                "i": index if isinstance(index, int) else position,
                "g": dumped.get("parent_index"),
                "page": page,
                "text": text,
            }) + b"\n")
    _add_parts(built, "blocks", "blocks", block_lines, part_max_bytes)
    _add_parts(built, "text", "text", text_lines, part_max_bytes)

    groups = [g.model_dump(mode="json", exclude_none=True) for g in block_containers.block_groups]
    _add_single(built, "groups", "groups.json", groups, len(groups))

    image_files = [
        built.add(path, raw, _IMAGE_TYPES[path.rsplit(".", 1)[1]][1])
        for path, raw in sorted(images.items())
    ]
    built.artifacts["images"] = ArtifactEntry(
        files=image_files, count=len(image_files), size=sum(f.size for f in image_files)
    )

    if semantic_metadata is not None:
        _add_single(built, "metadata", "metadata.json", _dump(semantic_metadata), 1)
    if reconciliation_metadata:
        entries = reconciliation_metadata.get("block_id_to_index")
        _add_single(
            built, "reconciliation", "reconciliation.json", reconciliation_metadata,
            len(entries) if isinstance(entries, dict) else 0,
        )
    # The values are this record's; when record.json is another record's (it
    # stored the shared content first), they would not describe it.
    if properties is None:
        properties = ticket_metadata(record)
    if (
        properties is not None
        and properties.values
        and (stored_record_fields or {}).get("id") in (None, record.id)
    ):
        _add_single(built, "properties", PROPERTIES_PATH, properties.to_json_dict(), len(properties.values))
    return built


#: What the storage service's validation says of a path an artifact set cannot hold.
PATH_NOT_HELD_REFUSAL = "Not a file an artifact set can hold"


def _drop_properties(built: BuiltRevision) -> None:
    built.files.pop(PROPERTIES_PATH, None)
    built.artifacts.pop("properties", None)


class ArtifactWriter:
    def __init__(
        self,
        store: NodeArtifactStore,
        logger: logging.Logger | None = None,
        *,
        part_max_bytes: int = PART_MAX_BYTES,
        upload_concurrency: int = UPLOAD_CONCURRENCY,
        producer_versions: dict[str, str] | None = None,
    ) -> None:
        self.store = store
        self.logger = logger or logging.getLogger(__name__)
        self.part_max_bytes = part_max_bytes
        self.upload_concurrency = upload_concurrency
        self.producer_versions = dict(producer_versions or {})

    async def _presign(
        self, built: BuiltRevision, org_id: str, virtual_record_id: str, rev: str,
    ) -> RevisionUploadPlan:
        def files() -> list[tuple[str, str]]:
            return [(p, built.files[p].content_type) for p in sorted(built.files)] + [(MANIFEST_PATH, _JSON)]

        try:
            return await self.store.presign_revision(org_id, virtual_record_id, rev, files())
        except StorageRequestRejectedError as refused:
            # A storage service older than properties.json refuses the whole set
            # (400) over that one path; the rest of the revision is still worth
            # storing. It names no path, only one line per path it does not know,
            # so exactly one such line, with the file in the set, is the sign;
            # any other 400 (a bad revision, a duplicate path, two bad paths) is
            # raised as before and never turns the file off for the process.
            if (
                refused.status != HttpStatusCode.BAD_REQUEST.value
                or PROPERTIES_PATH not in built.files
                or (refused.detail or "").splitlines() != [PATH_NOT_HELD_REFUSAL]
            ):
                raise
            _drop_properties(built)
            plan = await self.store.presign_revision(org_id, virtual_record_id, rev, files())
            if PROPERTIES_FILE.mark_unavailable(refused.status):
                self.logger.warning(
                    "Storage refuses %s (HTTP %d); revisions are stored without it until this "
                    "service restarts", PROPERTIES_PATH, refused.status,
                )
            return plan

    async def write_revision(
        self,
        record: Record,
        org_id: str,
        virtual_record_id: str,
        rev: str,
        block_containers: BlocksContainer,
        semantic_metadata: BaseModel | dict[str, Any] | None = None,
        reconciliation_metadata: dict[str, Any] | None = None,
        stored_record_fields: dict[str, Any] | None = None,
        properties: RecordMetadata | None = None,
    ) -> WrittenRevision | None:
        """Store every file of revision ``rev``, the manifest last.

        None when nothing was written because artifact writes are unavailable
        (a storage service without the routes). Raises when a file could not
        be stored; the manifest is then not written.
        """
        if not artifact_writes_available():
            return None
        if not REV_PATTERN.match(rev):
            raise ValueError("revision is not 8 to 64 lowercase hex digits")
        # Off the event loop: encoding and hashing a large record takes a while.
        built = await asyncio.to_thread(
            build_revision,
            record,
            block_containers,
            semantic_metadata=semantic_metadata,
            reconciliation_metadata=reconciliation_metadata,
            stored_record_fields=stored_record_fields,
            properties=properties,
            part_max_bytes=self.part_max_bytes,
        )
        if not PROPERTIES_FILE.available:
            _drop_properties(built)
        try:
            plan = await self._presign(built, org_id, virtual_record_id, rev)
        except ArtifactRoutesUnavailable:
            return None
        paths = sorted(built.files)

        limit = asyncio.Semaphore(max(1, self.upload_concurrency))

        async def upload(path: str) -> None:
            async with limit:
                file = built.files[path]
                await self.store.put(plan.files[path], file.data, file.content_type)

        # A TaskGroup cancels the remaining uploads on the first failure.
        try:
            async with asyncio.TaskGroup() as uploads:
                for path in paths:
                    uploads.create_task(upload(path))
        except ExceptionGroup as failed:
            raise failed.exceptions[0] from None

        manifest = Manifest(
            virtualRecordId=virtual_record_id,
            rev=rev,
            orgId=org_id,
            producedAt=get_epoch_timestamp_in_ms(),
            producerVersions=self.producer_versions,
            artifacts=built.artifacts,
        )
        await self.store.put(
            plan.files[MANIFEST_PATH],
            canonical_json(manifest.model_dump(mode="json", exclude_none=True)),
            _JSON,
        )
        return WrittenRevision(
            manifest=manifest,
            manifest_ref=ArtifactRef(document_id=plan.document_id, root=plan.root, path=MANIFEST_PATH),
        )
