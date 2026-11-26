"""``SkillBundleBuilder`` over the P3.2 stores: objects in a ``BinaryObjectStore``, the file list as a workspace."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.logs import HarnessLog
from harness_core.skills.types import (
    SkillBundleIntegrityError,
    SkillBundleInvalid,
    SkillBundleLimits,
    SkillBundleRef,
    SkillBundleUnavailable,
    SkillRef,
    is_digest,
    legacy_skill_pin_digest,
    skill_bundle_digest,
    validate_skill_files,
)
from harness_core.workspace.changes import ChangeSet
from harness_core.workspace.types import (
    WorkspaceFile,
    WorkspaceRevisionConflict,
    workspace_manifest_content_hash,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.skills.ports import SkillContentSource
    from harness_core.skills.types import SkillPin
    from harness_core.workspace.ports import BinaryObjectStore, WorkspaceStore
    from harness_core.workspace.types import ObjectScope, WorkspaceManifest

DEFAULT_WORKSPACE_PREFIX = "skill-bundle"
# One MIME type for every bundle file: the manifest hash covers MIME types, and
# guessing them would make it differ between platforms for the same bundle.
BUNDLE_MIME_TYPE = "application/octet-stream"


class StoredSkillBundleBuilder:
    """Bundles are workspaces named after their digest, so building is idempotent and needs no catalog.

    ``workspace_prefix`` places them where the store accepts them (PipesHub:
    inside the session workspace, ``ws:{sessionId}:skills``).
    """

    def __init__(
        self,
        *,
        source: SkillContentSource,
        objects: BinaryObjectStore,
        workspaces: WorkspaceStore,
        workspace_prefix: str = DEFAULT_WORKSPACE_PREFIX,
        limits: SkillBundleLimits | None = None,
        log: HarnessLog | None = None,
    ) -> None:
        self._source = source
        self._objects = objects
        self._workspaces = workspaces
        self._prefix = workspace_prefix
        self._limits = limits or SkillBundleLimits()
        self._log = log or HarnessLog.named("harness_core.skills")

    def workspace_id(self, digest: str) -> str:
        return f"{self._prefix}:{digest.removeprefix('sha256:')}"

    async def build(self, scope: ObjectScope, skill: SkillRef) -> SkillBundleRef:
        files = await self._source.files(scope.tenant_id, skill)
        if files is None:
            raise self._refused(SkillBundleUnavailable(f"Skill {skill.name!r} has no such version"), skill.name)
        try:
            return await self.store(scope, skill.name, files)
        except SkillBundleInvalid as exc:
            raise self._refused(exc, skill.name) from None

    async def store(self, scope: ObjectScope, name: str, files: Mapping[str, bytes]) -> SkillBundleRef:
        """Store ``files`` as the bundle of ``name`` (validated first; nothing is written if invalid)."""
        canonical = validate_skill_files(files, self._limits)
        digest = skill_bundle_digest(canonical)
        workspace_id = self.workspace_id(digest)
        entries: dict[str, WorkspaceFile] = {}
        for path, content in sorted(canonical.items()):
            entries[path] = WorkspaceFile(path, await self._objects.put(scope, content), BUNDLE_MIME_TYPE)
        head = await self._workspaces.head(scope, workspace_id)
        reused = head.revision > 0
        if not reused:
            try:
                head = await self._workspaces.commit(
                    scope, workspace_id, base_revision=0, changes=ChangeSet(entries), reason="seed"
                )
            except WorkspaceRevisionConflict:
                head = await self._workspaces.head(scope, workspace_id)
        if head.content_hash != workspace_manifest_content_hash(entries):
            raise self._refused(SkillBundleIntegrityError(f"Stored bundle {workspace_id} does not match its digest"), name)
        bundle = _ref(name, digest, head)
        self._log.info(
            "skill_bundle.built",
            skill=name,
            digest=digest,
            files=bundle.file_count,
            bytes=bundle.size_bytes,
            reused=reused,
        )
        return bundle

    async def resolve(self, scope: ObjectScope, pin: SkillPin) -> SkillBundleRef:
        head = await self._workspaces.head(scope, self.workspace_id(pin.bundle_digest))
        if head.revision > 0:
            self._log.info("skill_bundle.resolved", skill=pin.name, digest=pin.bundle_digest, via="stored")
            return _ref(pin.name, pin.bundle_digest, head)
        try:
            files = await pinned_content(self._source, scope.tenant_id, pin, self._limits)
        except SkillBundleUnavailable as exc:
            raise self._refused(exc, pin.name) from None
        try:
            bundle = await self.store(scope, pin.name, files)
        except SkillBundleInvalid as exc:
            raise self._refused(exc, pin.name) from None
        via = "history" if bundle.digest == pin.bundle_digest else "legacy_pin"
        self._log.info("skill_bundle.resolved", skill=pin.name, digest=bundle.digest, via=via)
        return bundle

    def _refused[E: Exception](self, error: E, name: str) -> E:
        self._log.warning("skill_bundle.refused", reason=getattr(error, "reason", "skill_bundle_error"), skill=name)
        return error


async def pinned_content(
    source: SkillContentSource, tenant_id: str, pin: SkillPin, limits: SkillBundleLimits | None = None
) -> Mapping[str, bytes]:
    """The content ``pin`` names, from the skill's history whatever it became since: the one meaning of a pin that
    every executor shares (FB-11). A legacy ``digest(name, version)`` pin names that version's content.
    ``SkillBundleUnavailable`` when no stored content matches."""
    candidates: list[str | None] = [None, *await source.versions(tenant_id, pin.name)]
    for version in candidates:
        if legacy_skill_pin_digest(pin.name, version) == pin.bundle_digest:
            files = await source.files(tenant_id, SkillRef(pin.name, version))
            if files is not None:
                return files
    for version in candidates:
        files = await source.files(tenant_id, SkillRef(pin.name, version))
        if files is None:
            continue
        try:
            if skill_bundle_digest(validate_skill_files(files, limits or SkillBundleLimits())) == pin.bundle_digest:
                return files
        except SkillBundleInvalid:
            continue
    raise SkillBundleUnavailable(f"No content of skill {pin.name!r} matches its pin")


def _ref(name: str, digest: str, manifest: WorkspaceManifest) -> SkillBundleRef:
    if not is_digest(digest):
        raise SkillBundleInvalid("A bundle needs a sha256 digest")
    return SkillBundleRef(
        name=name,
        digest=digest,
        workspace_id=manifest.workspace_id,
        revision=manifest.revision,
        file_count=len(manifest.files),
        size_bytes=sum(entry.content.size_bytes for entry in manifest.files.values()),
    )


__all__ = ["BUNDLE_MIME_TYPE", "DEFAULT_WORKSPACE_PREFIX", "StoredSkillBundleBuilder", "pinned_content"]
