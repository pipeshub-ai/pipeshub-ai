"""``SkillMaterializer``: pinned bundles into a sandbox's read-only skills mount, verified first."""

from __future__ import annotations

import io
import tarfile
from typing import TYPE_CHECKING

from harness_core.logs import HarnessLog
from harness_core.sandbox.types import MountSpec
from harness_core.skills.types import (
    SkillBundleIntegrityError,
    SkillBundleInvalid,
    skill_bundle_digest,
)
from harness_core.workspace.types import ObjectUnreadable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence

    from harness_core.sandbox.ports import SandboxHandle
    from harness_core.skills.types import SkillBundleRef
    from harness_core.workspace.ports import BinaryObjectStore, WorkspaceStore
    from harness_core.workspace.types import ObjectScope

_DIR_MODE = 0o555
_FILE_MODE = 0o444


def skills_mount(skills_dir: str) -> MountSpec:
    """The mount a sandbox needs at ``skills_dir`` for ``materialize``: read-only scratch."""
    return MountSpec(skills_dir, read_only=True)


class TarSkillMaterializer:
    def __init__(
        self, *, objects: BinaryObjectStore, workspaces: WorkspaceStore, log: HarnessLog | None = None
    ) -> None:
        self._objects = objects
        self._workspaces = workspaces
        self._log = log or HarnessLog.named("harness_core.skills")

    async def materialize(
        self,
        scope: ObjectScope,
        sandbox: SandboxHandle,
        bundles: Sequence[SkillBundleRef],
        *,
        skills_dir: str,
    ) -> None:
        names = [bundle.name for bundle in bundles]
        if len(set(names)) != len(names):
            raise self._refused(SkillBundleInvalid("Two pinned skills share one folder name"), sandbox)
        files: dict[str, bytes] = {}
        for bundle in bundles:
            for path, content in (await self._verified(scope, bundle, sandbox)).items():
                files[f"{bundle.name}/{path}"] = content
        if not files:
            return
        await sandbox.seed_tar(skills_dir, _read_only_tar(files))
        self._log.info(
            "skill_bundle.materialized",
            sandboxId=sandbox.id,
            skills=",".join(sorted(names)),
            files=len(files),
            bytes=sum(len(content) for content in files.values()),
        )

    async def _verified(self, scope: ObjectScope, bundle: SkillBundleRef, sandbox: SandboxHandle) -> dict[str, bytes]:
        manifest = await self._workspaces.revision(scope, bundle.workspace_id, bundle.revision)
        try:
            contents = {path: await self._objects.get(scope, entry.content) for path, entry in manifest.files.items()}
        except ObjectUnreadable as exc:
            raise self._refused(SkillBundleIntegrityError(f"Skill {bundle.name!r} has unreadable bytes"), sandbox) from exc
        if not contents or skill_bundle_digest(contents) != bundle.digest:
            raise self._refused(SkillBundleIntegrityError(f"Skill {bundle.name!r} does not match its digest"), sandbox)
        return contents

    def _refused[E: Exception](self, error: E, sandbox: SandboxHandle) -> E:
        self._log.warning(
            "skill_bundle.refused", reason=getattr(error, "reason", "skill_bundle_error"), sandboxId=sandbox.id
        )
        return error


def _read_only_tar(files: Mapping[str, bytes]) -> AsyncIterator[bytes]:
    """Root-owned, read-only entries with fixed times, parents before children: the same bytes every time."""
    directories = sorted({ancestor for path in files for ancestor in _ancestors(path)})
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for directory in directories:
            info = tarfile.TarInfo(directory)
            info.type = tarfile.DIRTYPE
            info.mode = _DIR_MODE
            archive.addfile(info)
        for path, content in sorted(files.items()):
            info = tarfile.TarInfo(path)
            info.size = len(content)
            info.mode = _FILE_MODE
            archive.addfile(info, io.BytesIO(content))
    return _one(buffer.getvalue())


def _ancestors(path: str) -> list[str]:
    parts = path.split("/")[:-1]
    return ["/".join(parts[: i + 1]) for i in range(len(parts))]


async def _one(payload: bytes) -> AsyncIterator[bytes]:
    yield payload


__all__ = ["TarSkillMaterializer", "skills_mount"]
