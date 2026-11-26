from __future__ import annotations

import asyncio
import hashlib
import json
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agent_loop_lib.modules.stores.checkpoint.base import (
    AgentCheckpoint,
    CheckpointStore,
)
from app.agent_loop_lib.tools.invocation import (
    EffectClass,
    InvocationNeedsReconciliation,
    InvocationStart,
)
from app.harness.contracts.state import ImmutableObjectRef

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.agent_loop_lib.tools.base import Tag
    from app.harness.contracts.workspace import BinaryObjectStore, ObjectScope

from app.harness.contracts.workspace import ObjectRef, WorkspaceFile, WorkspaceManifest

MAX_CHECKPOINT_BYTES = 8 * 1024 * 1024


class CheckpointEnvelope(BaseModel):
    """Portable checkpoint payload; contains data and immutable locks only."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    checkpoint: AgentCheckpoint
    skill_version_locks: tuple[dict[str, str], ...] = Field(
        default=(), alias="skillVersionLocks"
    )
    tool_descriptor_locks: tuple[dict[str, str], ...] = Field(
        default=(), alias="toolDescriptorLocks"
    )
    invocation_ids: tuple[str, ...] = Field(default=(), alias="invocationIds")
    workspace_manifest: dict[str, Any] | None = Field(
        default=None, alias="workspaceManifest"
    )
    invocations: tuple["InvocationRecord", ...] = ()


class InvocationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    invocation_id: str = Field(alias="invocationId")
    run_id: str = Field(alias="runId")
    call_id: str = Field(alias="callId")
    tool_name: str = Field(alias="toolName")
    tool_path: str = Field(alias="toolPath")
    arguments_hash: str = Field(alias="argumentsHash")
    effect_class: EffectClass = Field(alias="effectClass")
    state: Literal["started", "succeeded", "failed", "unknown"]
    attempt_count: int = Field(default=1, alias="attemptCount", ge=1)
    transitions: tuple[str, ...] = ("planned", "authorized", "started")
    result: dict[str, Any] | None = None


CheckpointEnvelope.model_rebuild()


class BlobCheckpointStore(CheckpointStore):
    """Latest-first checkpoint store backed by the configured PipesHub blob CAS.

    The object reference carries no access authority. The tenant scope is held
    separately by this trusted adapter, and every restore verifies size/hash in
    ``BinaryObjectStore`` before Pydantic validates the versioned envelope.
    """

    def __init__(
        self,
        *,
        object_store: BinaryObjectStore,
        scope: ObjectScope,
        initial_ref: ObjectRef | None = None,
        on_saved: Callable[[ObjectRef], Awaitable[None]] | None = None,
    ) -> None:
        self._objects = object_store
        self._scope = scope
        self._initial_ref = initial_ref
        self._on_saved = on_saved
        self._lock = asyncio.Lock()
        self._loaded_initial = False
        self._checkpoints: dict[str, AgentCheckpoint] = {}
        self._refs: dict[str, ObjectRef] = {}
        self._order: list[str] = []
        self._invocations: dict[str, InvocationRecord] = {}
        self._workspace_provider: Callable[[], WorkspaceManifest | None] | None = None
        self._restored_workspace: WorkspaceManifest | None = None

    @property
    def latest_ref(self) -> ObjectRef | None:
        if self._order:
            return self._refs[self._order[-1]]
        return self._initial_ref

    @property
    def object_store(self) -> BinaryObjectStore:
        return self._objects

    @property
    def latest_immutable_ref(self) -> ImmutableObjectRef | None:
        ref = self.latest_ref
        if ref is None:
            return None
        return ImmutableObjectRef(
            objectId=ref.object_id,
            contentHash=ref.content_hash,
            sizeBytes=ref.size_bytes,
        )

    async def save(self, checkpoint: AgentCheckpoint) -> str:
        async with self._lock:
            ref = await self._persist_locked(checkpoint)
            checkpoint_id = checkpoint.checkpoint_id
            self._checkpoints[checkpoint_id] = checkpoint
            self._refs[checkpoint_id] = ref
            if checkpoint_id not in self._order:
                self._order.append(checkpoint_id)
            self._loaded_initial = True
            return checkpoint_id

    def set_workspace_manifest_provider(
        self,
        provider: Callable[[], WorkspaceManifest | None],
    ) -> None:
        self._workspace_provider = provider

    async def workspace_manifest(self, run_id: str) -> WorkspaceManifest | None:
        await self._load_initial()
        if await self.latest(run_id) is None:
            return None
        return self._restored_workspace

    async def begin(
        self,
        *,
        run_id: str,
        call_id: str,
        tool_name: str,
        tool_path: str,
        arguments: dict[str, Any],
        tags: tuple[Tag, ...],
    ) -> InvocationStart:
        await self._load_initial()
        async with self._lock:
            checkpoint = self._current_checkpoint(run_id)
            arguments_hash = self._arguments_hash(arguments)
            invocation_id = hashlib.sha256(
                f"{run_id}\0{call_id}".encode("utf-8")
            ).hexdigest()
            existing = self._invocations.get(invocation_id)
            if existing is not None:
                if (
                    existing.arguments_hash != arguments_hash
                    or existing.tool_path != tool_path
                ):
                    raise ValueError("Invocation identity was reused with different input")
                if existing.result is not None and existing.state in {
                    "succeeded", "failed"
                }:
                    return InvocationStart(
                        invocation_id=invocation_id,
                        cached_result=existing.result,
                    )
                if existing.effect_class == "non_idempotent_write":
                    self._invocations[invocation_id] = existing.model_copy(
                        update={
                            "state": "unknown",
                            "transitions": (*existing.transitions, "unknown"),
                        }
                    )
                    await self._persist_locked(checkpoint)
                    raise InvocationNeedsReconciliation(
                        "needs_reconciliation: a prior tool effect has no durable outcome"
                    )
                record = existing.model_copy(update={
                    "state": "started",
                    "attempt_count": existing.attempt_count + 1,
                    "transitions": (*existing.transitions, "started"),
                })
            else:
                record = InvocationRecord(
                    invocationId=invocation_id,
                    runId=run_id,
                    callId=call_id,
                    toolName=tool_name,
                    toolPath=tool_path,
                    argumentsHash=arguments_hash,
                    effectClass=self._effect_class(tags),
                    state="started",
                )
            self._invocations[invocation_id] = record
            await self._persist_locked(checkpoint)
            return InvocationStart(invocation_id=invocation_id)

    async def finish(
        self,
        invocation_id: str,
        *,
        result: dict[str, Any],
        success: bool,
    ) -> None:
        async with self._lock:
            record = self._invocations[invocation_id]
            state = "succeeded" if success else "failed"
            self._invocations[invocation_id] = record.model_copy(update={
                "state": state,
                "result": result,
                "transitions": (*record.transitions, state),
            })
            await self._persist_locked(self._current_checkpoint(record.run_id))

    async def interrupted(self, invocation_id: str) -> None:
        async with self._lock:
            record = self._invocations[invocation_id]
            self._invocations[invocation_id] = record.model_copy(update={
                "state": "unknown",
                "transitions": (*record.transitions, "unknown"),
            })
            await self._persist_locked(self._current_checkpoint(record.run_id))

    async def load(self, checkpoint_id: str) -> AgentCheckpoint:
        await self._load_initial()
        try:
            return self._checkpoints[checkpoint_id]
        except KeyError as exc:
            raise KeyError(checkpoint_id) from exc

    async def latest(self, run_id: str) -> AgentCheckpoint | None:
        await self._load_initial()
        for checkpoint_id in reversed(self._order):
            checkpoint = self._checkpoints[checkpoint_id]
            if checkpoint.run_id == run_id:
                return checkpoint
        return None

    async def history(self, run_id: str) -> list[AgentCheckpoint]:
        await self._load_initial()
        return [
            self._checkpoints[checkpoint_id]
            for checkpoint_id in self._order
            if self._checkpoints[checkpoint_id].run_id == run_id
        ]

    async def delete_run(self, run_id: str) -> None:
        await self._load_initial()
        remove = [
            checkpoint_id for checkpoint_id in self._order
            if self._checkpoints[checkpoint_id].run_id == run_id
        ]
        self._order = [value for value in self._order if value not in remove]
        for checkpoint_id in remove:
            self._checkpoints.pop(checkpoint_id, None)
            self._refs.pop(checkpoint_id, None)

    async def _load_initial(self) -> None:
        if self._loaded_initial:
            return
        async with self._lock:
            if self._loaded_initial:
                return
            self._loaded_initial = True
            if self._initial_ref is None:
                return
            raw = await self._objects.get(self._scope, self._initial_ref)
            if len(raw) > MAX_CHECKPOINT_BYTES:
                raise ValueError("Checkpoint exceeds the durable payload limit")
            try:
                decoded = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("Checkpoint object is not valid JSON") from exc
            version = decoded.get("schemaVersion") if isinstance(decoded, dict) else None
            if version != 1:
                raise ValueError(f"Unsupported checkpoint schema version: {version!r}")
            envelope = CheckpointEnvelope.model_validate(decoded)
            checkpoint = envelope.checkpoint
            self._invocations = {
                record.invocation_id: record for record in envelope.invocations
            }
            if envelope.workspace_manifest is not None:
                self._restored_workspace = self._workspace_from_dict(
                    envelope.workspace_manifest
                )
            self._checkpoints[checkpoint.checkpoint_id] = checkpoint
            self._refs[checkpoint.checkpoint_id] = self._initial_ref
            self._order.append(checkpoint.checkpoint_id)

    @staticmethod
    def _metadata_list(
        checkpoint: AgentCheckpoint,
        key: str,
    ) -> tuple[dict[str, str], ...]:
        value = checkpoint.metadata.get(key, [])
        if not isinstance(value, list):
            return ()
        return tuple(
            {str(k): str(v) for k, v in item.items()}
            for item in value
            if isinstance(item, dict)
        )

    async def _persist_locked(self, checkpoint: AgentCheckpoint) -> ObjectRef:
        envelope = CheckpointEnvelope(
            checkpoint=checkpoint,
            skillVersionLocks=self._metadata_list(
                checkpoint, "skill_version_locks"
            ),
            toolDescriptorLocks=self._metadata_list(
                checkpoint, "tool_descriptor_locks"
            ),
            invocationIds=tuple(sorted(self._invocations)),
            workspaceManifest=self._workspace_to_dict(
                self._workspace_provider()
                if self._workspace_provider is not None
                else self._restored_workspace
            ),
            invocations=tuple(
                self._invocations[key] for key in sorted(self._invocations)
            ),
        )
        payload = envelope.model_dump_json(
            by_alias=True, exclude_none=True
        ).encode("utf-8")
        if len(payload) > MAX_CHECKPOINT_BYTES:
            raise ValueError("Checkpoint exceeds the durable payload limit")
        ref = await self._objects.put(self._scope, payload)
        if self._on_saved is not None:
            await self._on_saved(ref)
        if checkpoint.checkpoint_id in self._refs:
            self._refs[checkpoint.checkpoint_id] = ref
        self._initial_ref = ref
        return ref

    def _current_checkpoint(self, run_id: str) -> AgentCheckpoint:
        for checkpoint_id in reversed(self._order):
            checkpoint = self._checkpoints[checkpoint_id]
            if checkpoint.run_id == run_id:
                return checkpoint
        raise RuntimeError("Invocation journal requires a pre-tool checkpoint")

    @staticmethod
    def _arguments_hash(arguments: dict[str, Any]) -> str:
        raw = json.dumps(
            arguments,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return f"sha256:{hashlib.sha256(raw).hexdigest()}"

    @staticmethod
    def _effect_class(tags: tuple[Tag, ...]) -> EffectClass:
        operations = {
            tag.value
            for tag in tags
            if tag.key in {"category", "type"}
        }
        if operations & {"read", "search"}:
            return "read"
        # Sandboxed execution is retryable: its externally visible state is
        # the content-addressed VFS commit. Nested provider calls still pass
        # through this journal independently and retain their own effect class.
        if "execute" in operations:
            return "idempotent_write"
        # Unclassified provider actions are conservatively treated as
        # non-idempotent. Tools may opt into safe retry explicitly.
        if "idempotent_write" in operations:
            return "idempotent_write"
        return "non_idempotent_write"

    @staticmethod
    def _workspace_to_dict(
        manifest: WorkspaceManifest | None,
    ) -> dict[str, Any] | None:
        if manifest is None:
            return None
        return {
            "workspaceId": manifest.workspace_id,
            "revision": manifest.revision,
            "parentRevision": manifest.parent_revision,
            "contentHash": manifest.content_hash,
            "createdAtMs": manifest.created_at_ms,
            "files": {
                path: {
                    "path": entry.path,
                    "mimeType": entry.mime_type,
                    "content": {
                        "objectId": entry.content.object_id,
                        "contentHash": entry.content.content_hash,
                        "sizeBytes": entry.content.size_bytes,
                    },
                }
                for path, entry in manifest.files.items()
            },
        }

    @staticmethod
    def _workspace_from_dict(value: dict[str, Any]) -> WorkspaceManifest:
        files_value = value.get("files")
        if not isinstance(files_value, dict):
            raise ValueError("Checkpoint workspace manifest has invalid files")
        files: dict[str, WorkspaceFile] = {}
        for path, raw_entry in files_value.items():
            if not isinstance(path, str) or not isinstance(raw_entry, dict):
                raise ValueError("Checkpoint workspace manifest has invalid entry")
            raw_content = raw_entry.get("content")
            if not isinstance(raw_content, dict):
                raise ValueError("Checkpoint workspace manifest has invalid object reference")
            files[path] = WorkspaceFile(
                path=str(raw_entry.get("path", path)),
                mime_type=str(raw_entry.get("mimeType", "application/octet-stream")),
                content=ObjectRef(
                    object_id=str(raw_content.get("objectId", "")),
                    content_hash=str(raw_content.get("contentHash", "")),
                    size_bytes=int(raw_content.get("sizeBytes", -1)),
                ),
            )
        return WorkspaceManifest(
            workspace_id=str(value.get("workspaceId", "")),
            revision=int(value.get("revision", -1)),
            parent_revision=(
                int(value["parentRevision"])
                if value.get("parentRevision") is not None
                else None
            ),
            content_hash=str(value.get("contentHash", "")),
            files=files,
            created_at_ms=int(value.get("createdAtMs", 0)),
        )
