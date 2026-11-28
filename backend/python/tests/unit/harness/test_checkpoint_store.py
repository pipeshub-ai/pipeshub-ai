from __future__ import annotations

import json

import pytest

from app.agent_loop_lib.modules.stores.checkpoint.base import AgentCheckpoint
from app.agent_loop_lib.tools.base import Tag
from app.agent_loop_lib.tools.invocation import InvocationNeedsReconciliation
from app.harness.adapters.checkpoint_store import BlobCheckpointStore
from app.harness.adapters.memory_workspace import InMemoryBinaryObjectStore
from app.harness.contracts.workspace import (
    ObjectRef,
    ObjectScope,
    WorkspaceFile,
    WorkspaceManifest,
    workspace_manifest_content_hash,
)


def _checkpoint(**changes) -> AgentCheckpoint:
    values = {
        "checkpoint_id": "checkpoint-1",
        "run_id": "run-1",
        "agent_id": "agent-1",
        "trace_id": "trace-1",
        "role_name": "assistant",
        "model": "model-1",
        "goal": {"description": "answer"},
        "messages": [],
        "turn_index": 2,
        "budget_snapshot": {},
        "metadata": {
            "skill_version_locks": [
                {"skillId": "pdf", "version": "sha256:abc"}
            ],
            "invocation_ids": ["invocation-1"],
        },
    }
    values.update(changes)
    return AgentCheckpoint.model_validate(values)


@pytest.mark.asyncio
async def test_checkpoint_round_trips_through_scoped_immutable_object() -> None:
    objects = InMemoryBinaryObjectStore()
    saved = []

    async def on_saved(ref) -> None:
        saved.append(ref)

    store = BlobCheckpointStore(
        object_store=objects,
        scope=ObjectScope(tenant_id="org-1"),
        on_saved=on_saved,
    )

    assert await store.save(_checkpoint()) == "checkpoint-1"
    ref = store.latest_ref
    assert ref is not None
    restored = BlobCheckpointStore(
        object_store=objects,
        scope=ObjectScope(tenant_id="org-1"),
        initial_ref=ref,
    )
    assert (await restored.latest("run-1")).checkpoint_id == "checkpoint-1"
    assert saved == [ref]


@pytest.mark.asyncio
async def test_checkpoint_rejects_future_schema_after_hash_verified_read() -> None:
    objects = InMemoryBinaryObjectStore()
    scope = ObjectScope(tenant_id="org-1")
    raw = json.dumps({"schemaVersion": 2, "checkpoint": {}}).encode()
    ref = await objects.put(scope, raw)
    store = BlobCheckpointStore(
        object_store=objects,
        scope=scope,
        initial_ref=ObjectRef(ref.object_id, ref.content_hash, ref.size_bytes),
    )

    with pytest.raises(ValueError, match="Unsupported checkpoint schema"):
        await store.latest("run-1")


@pytest.mark.asyncio
async def test_invocation_journal_replays_known_result_after_process_restart() -> None:
    objects = InMemoryBinaryObjectStore()
    scope = ObjectScope(tenant_id="org-1")
    store = BlobCheckpointStore(object_store=objects, scope=scope)
    await store.save(_checkpoint())
    started = await store.begin(
        run_id="run-1",
        call_id="call-1",
        tool_name="search",
        tool_path="/tools/search",
        arguments={"q": "answer"},
        tags=(Tag("category", "read"),),
    )
    await store.finish(
        started.invocation_id,
        result={"success": True, "data": {"value": 42}, "error": None, "sources": []},
        success=True,
    )
    ref = store.latest_ref
    assert ref is not None

    recovered = BlobCheckpointStore(
        object_store=objects, scope=scope, initial_ref=ref
    )
    replay = await recovered.begin(
        run_id="run-1",
        call_id="call-1",
        tool_name="search",
        tool_path="/tools/search",
        arguments={"q": "answer"},
        tags=(Tag("category", "read"),),
    )
    assert replay.cached_result["data"] == {"value": 42}


@pytest.mark.asyncio
async def test_invocation_journal_requires_reconciliation_for_uncertain_write() -> None:
    objects = InMemoryBinaryObjectStore()
    scope = ObjectScope(tenant_id="org-1")
    store = BlobCheckpointStore(object_store=objects, scope=scope)
    await store.save(_checkpoint())
    await store.begin(
        run_id="run-1",
        call_id="call-write",
        tool_name="send_message",
        tool_path="/toolsets/slack/send_message",
        arguments={"channel": "support"},
        tags=(Tag("category", "write"),),
    )
    recovered = BlobCheckpointStore(
        object_store=objects,
        scope=scope,
        initial_ref=store.latest_ref,
    )

    with pytest.raises(InvocationNeedsReconciliation):
        await recovered.begin(
            run_id="run-1",
            call_id="call-write",
            tool_name="send_message",
            tool_path="/toolsets/slack/send_message",
            arguments={"channel": "support"},
            tags=(Tag("category", "write"),),
        )


@pytest.mark.asyncio
async def test_checkpoint_restores_workspace_manifest_with_versioned_object_refs() -> None:
    objects = InMemoryBinaryObjectStore()
    scope = ObjectScope(tenant_id="org-1")
    content = await objects.put(scope, b"draft")
    files = {
        "drafts/report.txt": WorkspaceFile(
            path="drafts/report.txt", content=content, mime_type="text/plain"
        )
    }
    manifest = WorkspaceManifest(
        workspace_id="agent-run:run-1",
        revision=3,
        parent_revision=2,
        content_hash=workspace_manifest_content_hash(files),
        files=files,
        created_at_ms=10,
    )
    store = BlobCheckpointStore(object_store=objects, scope=scope)
    store.set_workspace_manifest_provider(lambda: manifest)
    await store.save(_checkpoint())
    recovered = BlobCheckpointStore(
        object_store=objects, scope=scope, initial_ref=store.latest_ref
    )

    restored = await recovered.workspace_manifest("run-1")
    assert restored is not None
    assert restored.revision == 3
    assert restored.files["drafts/report.txt"].content == content
