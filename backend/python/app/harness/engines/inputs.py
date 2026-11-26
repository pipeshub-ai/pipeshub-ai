"""``RegistryInputSource``: a durable engine run's input artifacts from ``ArtifactRegistryService`` (E1b, EV2-26).

One per run, as the run's actor: an input must be an artifact of the run's conversation the actor may read
(the registry's ``authorize_read``: an owner or a reader of a shared conversation), live, at a version it
has. Its bytes are stored in the session's sealed object scope, so the workspace can hold them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from app.models.entities import LifecycleStatus
from app.services.artifact_registry.access import (
    AccessDeniedError,
    ArtifactNotFoundError,
)
from app.services.artifact_registry.limits import effective_input_max_bytes
from harness_core.artifacts.types import ArtifactVersionRef
from harness_core.engines.durable.inputs import (
    check_input_size,
    distinct_inputs,
    input_name,
)
from harness_core.engines.errors import EngineInputUnavailable
from harness_core.engines.types import EngineInput

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from app.services.artifact_registry.models import Actor, ArtifactMetadata
    from app.services.artifact_registry.registry import ArtifactRegistryService
    from harness_core.interactions.ports import RunBinding
    from harness_core.journal.types import InputArtifactRef
    from harness_core.workspace.ports import BinaryObjectStore
    from harness_core.workspace.types import ObjectScope

_UNAVAILABLE = "an input artifact is not available to this run"


async def readable_input(
    registry: ArtifactRegistryService, actor: Actor, ref: InputArtifactRef, conversation_id: str
) -> tuple[ArtifactMetadata, int] | None:
    """The artifact ``ref`` names and the version it reads, when ``actor`` may read it as an input of this
    conversation (live, of this conversation, at a version it has); ``None`` otherwise."""
    try:
        record = cast(
            "dict[str, object]",
            await registry.access.authorize_read(actor, ref.artifact_id),  # pyright: ignore[reportUnknownMemberType]
        )
        metadata = await registry.resolve(actor=actor, ref=ref.artifact_id)
    except (ArtifactNotFoundError, AccessDeniedError):
        return None
    version = ref.version if ref.version is not None else metadata.version
    if (
        record.get("isDeleted")
        or metadata.conversation_id != conversation_id
        or metadata.lifecycle_status == LifecycleStatus.REJECTED
        or not 1 <= version <= metadata.version
    ):
        return None
    return metadata, version


async def input_listing(
    registry: ArtifactRegistryService, actor: Actor, refs: Sequence[InputArtifactRef], conversation_id: str
) -> str:
    """What the loop is told of the files a turn names (CTL-6): each one's name, type, size and id, access-checked
    as an engine's inputs are; never its bytes. The loop reads one with its artifact tools, or hands them to
    ``code_task``, whose engine finds them under ``in/``."""
    if not refs:
        return ""
    lines: list[str] = []
    for ref in refs:
        found = await readable_input(registry, actor, ref, conversation_id)
        if found is None:
            lines.append(f"- {ref.artifact_id}: not available to you")
            continue
        metadata, version = found
        name = metadata.logical_name or metadata.name
        lines.append(
            f"- {name} ({metadata.mime_type or 'application/octet-stream'}, {metadata.size_bytes} bytes; "
            f"artifact id {metadata.artifact_id}, version {version})"
        )
    return "Files attached to this message:\n" + "\n".join(lines)


class RegistryInputSource:
    def __init__(
        self,
        registry: ArtifactRegistryService,
        actor: Actor,
        objects: BinaryObjectStore,
        *,
        object_scope: Callable[[RunBinding], ObjectScope],
        max_input_bytes: int | None = None,
    ) -> None:
        """``max_input_bytes``: one input's cap (C3.08); the registry's own cap by default."""
        self._registry = registry
        self._max_bytes = registry.max_bytes if max_input_bytes is None else max_input_bytes
        self._actor = actor
        self._objects = objects
        self._object_scope = object_scope

    async def resolve(self, binding: RunBinding, refs: Sequence[InputArtifactRef]) -> tuple[EngineInput, ...]:
        if binding.tenant != self._actor.org_id:
            raise EngineInputUnavailable(_UNAVAILABLE)
        inputs: list[EngineInput] = []
        for ref in refs:
            found = await readable_input(self._registry, self._actor, ref, binding.scope)
            if found is None:
                raise EngineInputUnavailable(_UNAVAILABLE, name=await self._own_name(ref, binding.scope))
            metadata, version = found
            name = input_name(metadata.logical_name or metadata.name)
            # The metadata's size is the current version's: an older one is checked once read.
            if version == metadata.version:
                check_input_size(name, metadata.size_bytes, self._max_bytes)
            try:
                content = await self._registry.get_content(
                    actor=self._actor, artifact_id=metadata.artifact_id, version=version
                )
            except (ArtifactNotFoundError, AccessDeniedError):
                raise EngineInputUnavailable(_UNAVAILABLE) from None
            check_input_size(name, len(content), self._max_bytes)
            stored = await self._objects.put(self._object_scope(binding), content)
            inputs.append(
                EngineInput(
                    name,
                    stored,
                    metadata.mime_type or "application/octet-stream",
                    ArtifactVersionRef(metadata.artifact_id, version),
                )
            )
        return distinct_inputs(inputs)

    async def _own_name(self, ref: InputArtifactRef, conversation_id: str) -> str | None:
        """The name of an input the run may no longer take, only when it is an artifact of the run's own conversation
        (deleted since it was handed over, or without that version): the person attached it, so saying its name tells
        them nothing new. Another conversation's, or one they may not read, stays unnamed (R36COVER-FU-3)."""
        try:
            metadata = await self._registry.resolve(actor=self._actor, ref=ref.artifact_id)
        except (ArtifactNotFoundError, AccessDeniedError):
            return None
        if metadata.conversation_id != conversation_id:
            return None
        return input_name(metadata.logical_name or metadata.name)


def run_input_source(
    registry: ArtifactRegistryService,
    actor: Actor,
    objects: BinaryObjectStore,
    *,
    object_scope: Callable[[RunBinding], ObjectScope],
    deployment_max_bytes: int,
    pinned_max_mb: int | None,
) -> RegistryInputSource:
    """A run's input source (J-26): one input may weigh up to the deployment's cap (``HARNESS_INPUT_MAX_MB``), lowered
    by the run pin's ``sandbox.inputMaxMb``."""
    return RegistryInputSource(
        registry, actor, objects, object_scope=object_scope,
        max_input_bytes=effective_input_max_bytes(deployment_max_bytes, pinned_max_mb),
    )


__all__ = ["RegistryInputSource", "input_listing", "readable_input", "run_input_source"]
