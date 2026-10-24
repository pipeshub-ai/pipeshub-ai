"""Shapes of a content revision's artifact set.

A revision is one storage document holding many files under
``{org}/PipesHub/record-revisions/{vrid}/{rev}/``; ``manifest.json`` lists them
and is written last, so a revision without one is incomplete.
"""

from typing import Literal

from pydantic import BaseModel, Field

MANIFEST_PATH = "manifest.json"
#: Bumped when a file's layout changes in a way a reader must know about.
#: 2: image blocks keep a non-canonical media type; record.json holds the container's
#: fields and the stored copy's identity (the owner's, for shared content).
WRITER_VERSION = 2
#: In an image block's data: the media type its data URI spelled, when not the asset's own.
ASSET_MEDIA_TYPE = "asset_media_type"


class ArtifactRef(BaseModel):
    """One file of a stored revision."""

    document_id: str
    root: str
    path: str


class SignedFileUrl(BaseModel):
    """A signed request for one file. ``headers`` must be sent exactly as given."""

    url: str
    method: Literal["GET", "PUT"]
    headers: dict[str, str] = Field(default_factory=dict)

    def __repr__(self) -> str:
        # The URL is a bearer credential; keep it out of logs and tracebacks.
        return f"SignedFileUrl(method={self.method!r}, headers={sorted(self.headers)!r})"

    __str__ = __repr__


class RevisionUploadPlan(BaseModel):
    """Where to PUT each file of one revision, as the storage service signed it."""

    document_id: str
    root: str
    expires_in_seconds: int | None = None
    files: dict[str, SignedFileUrl]

    def __repr__(self) -> str:
        return f"RevisionUploadPlan(document_id={self.document_id!r}, files={len(self.files)})"

    __str__ = __repr__


class ArtifactFile(BaseModel):
    path: str
    size: int  # bytes
    sha256: str


class ArtifactEntry(BaseModel):
    """One kind of artifact: a single file (``path``) or ordered parts."""

    path: str | None = None
    parts: list[ArtifactFile] | None = None
    files: list[ArtifactFile] | None = None
    #: Items held: blocks, groups, text lines, images, entries.
    count: int = 0
    #: Bytes across every file of the kind.
    size: int = 0
    sha256: str | None = None


class Manifest(BaseModel):
    """``manifest.json``. Field names are camelCase as the file stores them."""

    virtualRecordId: str
    rev: str
    orgId: str
    producedAt: int
    writerVersion: int = WRITER_VERSION
    producerVersions: dict[str, str] = Field(default_factory=dict)
    artifacts: dict[str, ArtifactEntry]


class WrittenRevision(BaseModel):
    """A revision whose every file, manifest last, is stored."""

    manifest: Manifest
    manifest_ref: ArtifactRef
