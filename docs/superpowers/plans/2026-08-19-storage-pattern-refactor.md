# Storage Pattern Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve 9 merge conflicts on `feature/storage-pattern-bkp`, port storage_search to the agent_loop_lib tool framework, fix security vulnerabilities, and extend storage pattern to collections.

**Architecture:** Take main's codebase as base for all conflicts (agent_loop_lib tools, batch messaging, shared sessions, msgspec, KB cache invalidation, version bumping, `_preserve_indexing_state`). Layer storage pattern additions on top: hierarchical blob paths, PendingMove lifecycle, pattern match search, storage cleanup. Pattern match integration goes in `retrieval.py`'s `search_internal_knowledge`, not in `chatbot.py`.

**Tech Stack:** Python FastAPI, aiohttp, asyncio, agent_loop_lib tool framework, Node.js Express storage service

**Spec:** Design approved in conversation context (no separate spec file — this was a bounded task)

## Global Constraints

- Use `agent_loop_lib` `@tool` decorator (`from app.agent_loop_lib.tools.decorators import tool`), NOT old `app.agents.tools.decorator`
- Use `_maybe_compress_record()` for compression decisions, NOT `self.compression_enabled` boolean
- Use `get_shared_session()` for HTTP downloads, NOT per-call `aiohttp.ClientSession()` — exception: upload/POST calls where aiohttp FormData lifetimes differ
- Use batch `send_messages()` with ack tracking, NOT individual `send_message()` in loops
- KB collection IDs ARE connector IDs — no separate `collection_ids` parameter anywhere
- All test files use main's naming conventions (`"u1"` not `"user-1"`, `update_indexing_status_for_record_ids` not `reset_indexing_status_to_queued_for_record_ids`)
- The git merge conflict markers use: `<<<<<<< Updated upstream` = HEAD/main, `>>>>>>> Stashed changes` = storage pattern branch

## Conflict Resolution Strategy

For each conflicted file, the resolution approach is:
1. Start from HEAD (main): `git show :2:<path> > <path>` 
2. Apply specific additions from stashed version using Edit tool
3. Stage: `git add <path>`

This avoids manual conflict marker editing.

---

### Task 1: Resolve Simple Conflicts (chatbot.py, fetch_full_record.py)

**Files:**
- Resolve: `backend/python/app/api/routes/chatbot.py` (3 conflicts) — take HEAD entirely
- Resolve: `backend/python/app/utils/fetch_full_record.py` (2 conflicts) — take HEAD entirely

**Interfaces:**
- Consumes: nothing
- Produces: clean `chatbot.py` (agent loop architecture), clean `fetch_full_record.py`

**Why take HEAD for both:**
- `chatbot.py`: Main removed `create_internal_search_tool`, `_generate_internal_search_stream`, `_generate_web_search_stream` in favor of `_generate_chat_stream_via_agent_loop()`. The stashed version's ~500 lines of inline tool factory code are obsolete — pattern match integration belongs in the agent actions layer (`retrieval.py`), not in HTTP routes.
- `fetch_full_record.py`: Minor description wording differences. Stashed caps at 5 record IDs; HEAD allows unlimited. Take HEAD.

- [ ] **Step 1: Take HEAD for chatbot.py**

```bash
git show :2:backend/python/app/api/routes/chatbot.py > backend/python/app/api/routes/chatbot.py
git add backend/python/app/api/routes/chatbot.py
```

- [ ] **Step 2: Take HEAD for fetch_full_record.py**

```bash
git show :2:backend/python/app/utils/fetch_full_record.py > backend/python/app/utils/fetch_full_record.py
git add backend/python/app/utils/fetch_full_record.py
```

- [ ] **Step 3: Verify no conflict markers remain**

```bash
grep -rn "^<<<<<<<\|^=======\|^>>>>>>>" backend/python/app/api/routes/chatbot.py backend/python/app/utils/fetch_full_record.py
```

Expected: no output.

- [ ] **Step 4: Commit**

```bash
git add backend/python/app/api/routes/chatbot.py backend/python/app/utils/fetch_full_record.py
git commit -m "resolve: take HEAD for chatbot.py and fetch_full_record.py

chatbot.py: agent loop architecture replaces old inline tool factories
fetch_full_record.py: keep HEAD descriptions and no max-5 cap

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 2: Resolve blob_storage.py Conflicts

**Files:**
- Resolve: `backend/python/app/modules/transformers/blob_storage.py` (4 conflicts at lines 1122, 1657, 1863, 2387)

**Interfaces:**
- Consumes: `build_hierarchical_storage_path` from `app.utils.storage_path` (already staged, no conflicts)
- Produces: `BlobStorage` class with:
  - `_build_hierarchical_storage_path(record, virtual_record_id) -> str`
  - `get_actual_content_path(org_id, virtual_record_id) -> str | None`
  - `_get_current_document_path(org_id, document_id) -> str | None`
  - `_strip_org_prefix(org_id, document_path) -> str`
  - `update_record_buffer(org_id, document_id, record_dict, virtual_record_id) -> tuple`
  - `_update_metadata_buffer(org_id, document_id, metadata_dict, virtual_record_id) -> None`
  - `save_record_to_storage(..., document_path: str | None = None)` — added optional param
  - `save_reconciliation_metadata(..., document_path: str | None = None)` — added optional param
  - `apply(ctx)` — computes hierarchical path, sets `ctx.settings["storage_path"]`

**Merge strategy:** Start from HEAD (main's infrastructure: `_maybe_compress_record`, `get_shared_session`, msgspec, signed URL cache). Then add stashed version's hierarchical path methods and redesigned `apply()` flow, using main's compression where stashed used inline compression.

- [ ] **Step 1: Start from HEAD**

```bash
git show :2:backend/python/app/modules/transformers/blob_storage.py > backend/python/app/modules/transformers/blob_storage.py
```

- [ ] **Step 2: Add storage_path import**

Add to the imports section (near the top, after other `app.` imports):

```python
from app.utils.storage_path import build_hierarchical_storage_path
```

- [ ] **Step 3: Add hierarchical path methods**

Add these methods to the `BlobStorage` class. Place them before the `apply()` method:

```python
async def _build_hierarchical_storage_path(self, record, virtual_record_id) -> str:
    try:
        path = await build_hierarchical_storage_path(
            record, self.graph_provider, virtual_record_id=virtual_record_id, logger=self.logger,
        )
        return path or f"records/{virtual_record_id}"
    except Exception as e:
        self.logger.warning("Failed to build hierarchical storage path: %s", str(e))
        return f"records/{virtual_record_id}"

@staticmethod
def _strip_org_prefix(org_id: str, document_path: str) -> str:
    prefix = f"{org_id}/PipesHub/"
    if document_path.startswith(prefix):
        return document_path[len(prefix):]
    return document_path

async def _get_current_document_path(self, org_id: str, document_id: str) -> str | None:
    try:
        headers, nodejs_endpoint, _ = await self._get_auth_and_config(org_id)
        get_url = f"{nodejs_endpoint}{Routes.STORAGE_GET.value.format(documentId=document_id)}"
        session = get_shared_session()
        async with session.get(get_url, headers=headers) as response:
            if response.status in (404, 500):
                return None
            if response.status != HttpStatusCode.SUCCESS.value:
                return None
            doc = await response.json()
            return doc.get("documentPath")
    except Exception as e:
        self.logger.warning("Failed to get document path for %s: %s", document_id, str(e))
        return None

async def get_actual_content_path(self, org_id: str, virtual_record_id: str) -> str | None:
    if not self.graph_provider:
        return None
    try:
        existing_lookup = await self.get_document_id_by_virtual_record_id(virtual_record_id)
    except Exception:
        return None
    if not existing_lookup or not existing_lookup.get("record_doc_id"):
        return None
    raw_path = await self._get_current_document_path(org_id, existing_lookup["record_doc_id"])
    return self._strip_org_prefix(org_id, raw_path) if raw_path else None
```

- [ ] **Step 4: Add update_record_buffer method**

Add after the hierarchical path methods. This uses main's `_maybe_compress_record` for compression:

```python
async def update_record_buffer(self, org_id, document_id, record_dict, virtual_record_id):
    headers, nodejs_endpoint, _ = await self._get_auth_and_config(org_id)
    buffer_url = f"{nodejs_endpoint}/api/v1/document/{document_id}/buffer"

    compressed_record, use_compression = self._maybe_compress_record(record_dict)
    upload_data = {
        "isCompressed": use_compression,
        "record": compressed_record if use_compression else record_dict,
        "virtualRecordId": virtual_record_id,
    }
    json_data = json.dumps(upload_data).encode('utf-8')

    async with aiohttp.ClientSession() as session:
        async with session.put(
            buffer_url,
            data=json_data,
            headers={**headers, 'Content-Type': 'application/json'},
        ) as response:
            if response.status != HttpStatusCode.SUCCESS.value:
                error_text = await response.text()
                raise Exception(f"Buffer update failed ({response.status}): {error_text}")
            result = await response.json()
            return result.get("_id") or document_id, len(json_data)
```

- [ ] **Step 5: Add _update_metadata_buffer method**

Add after `update_record_buffer`:

```python
async def _update_metadata_buffer(self, org_id, document_id, metadata_dict, virtual_record_id):
    headers, nodejs_endpoint, _ = await self._get_auth_and_config(org_id)
    buffer_url = f"{nodejs_endpoint}/api/v1/document/{document_id}/buffer"

    upload_data = {
        "isCompressed": False,
        "record": metadata_dict,
        "virtualRecordId": virtual_record_id,
    }
    json_data = json.dumps(upload_data).encode('utf-8')

    async with aiohttp.ClientSession() as session:
        async with session.put(
            buffer_url,
            data=json_data,
            headers={**headers, 'Content-Type': 'application/json'},
        ) as response:
            if response.status != HttpStatusCode.SUCCESS.value:
                self.logger.warning("Metadata buffer update failed for %s: %s", document_id, response.status)
```

- [ ] **Step 6: Add document_path parameter to save_record_to_storage**

In the `save_record_to_storage` method signature, add `document_path: str | None = None` as the last parameter. Inside the method, replace the hardcoded `documentPath` assignment:

Change:
```python
'documentPath': f'records/{virtual_record_id}',
```
To:
```python
'documentPath': document_path or f'records/{virtual_record_id}',
```

- [ ] **Step 7: Add document_path parameter to save_reconciliation_metadata**

Add `document_path: str | None = None` as the last parameter. In the method body, for the create path (when calling `_create_metadata_document`), pass `document_path` through. Also update `_create_metadata_document` to accept and use `document_path`:

In `_create_metadata_document`, add `document_path: str | None = None` parameter and change:
```python
'documentPath': f'records/{virtual_record_id}',
```
To:
```python
'documentPath': document_path or f'records/{virtual_record_id}',
```

For the update path in `save_reconciliation_metadata` (existing metadata doc), use `_update_metadata_buffer` instead of `upload_next_version`:
```python
await self._update_metadata_buffer(org_id, existing_doc_id, metadata_dict, virtual_record_id)
return existing_doc_id
```

- [ ] **Step 8: Redesign apply() to use hierarchical paths**

Replace the `apply(ctx)` method body. The new flow:
1. Compute hierarchical path
2. If existing doc → update buffer in place, fetch actual path
3. If new → save with document_path
4. Set `ctx.settings["storage_path"]`

```python
async def apply(self, ctx: TransformContext) -> TransformContext:
    record = ctx.record
    org_id = record.org_id
    record_id = record.id
    virtual_record_id = record.virtual_record_id
    record_dict = record.model_dump(mode='json', exclude_none=True)
    record_dict = self._clean_empty_values(record_dict)

    storage_path = await self._build_hierarchical_storage_path(record, virtual_record_id)

    existing_lookup = None
    if self.graph_provider:
        try:
            existing_lookup = await self.get_document_id_by_virtual_record_id(virtual_record_id)
        except Exception as e:
            self.logger.warning("Failed to lookup existing doc for %s: %s", virtual_record_id, str(e))
            existing_lookup = None

    actual_storage_path = storage_path

    if existing_lookup and existing_lookup.get("record_doc_id"):
        existing_doc_id = existing_lookup["record_doc_id"]
        try:
            document_id, file_size_bytes = await self.update_record_buffer(
                org_id, existing_doc_id, record_dict, virtual_record_id
            )
            raw_path = await self._get_current_document_path(org_id, existing_doc_id)
            if raw_path:
                actual_storage_path = self._strip_org_prefix(org_id, raw_path)
        except Exception as e:
            self.logger.warning("Buffer update failed, creating new doc: %s", str(e))
            document_id, file_size_bytes = await self.save_record_to_storage(
                org_id, record_id, virtual_record_id, record_dict, document_path=storage_path
            )
    else:
        document_id, file_size_bytes = await self.save_record_to_storage(
            org_id, record_id, virtual_record_id, record_dict, document_path=storage_path
        )

    if document_id and self.graph_provider:
        await self.store_virtual_record_mapping(virtual_record_id, document_id, file_size_bytes)

    ctx.settings["storage_path"] = actual_storage_path
    ctx.record = record
    return ctx
```

- [ ] **Step 9: Resolve conflict 1 (line 1122) — compression in save_record_to_storage**

Take HEAD side: keep `_maybe_compress_record` call. The stashed side's inline `self.compression_enabled` check is replaced.

- [ ] **Step 10: Resolve conflict 2 (line 1657) — compression in upload_next_version**

Take HEAD side: keep `_maybe_compress_record` call.

- [ ] **Step 11: Resolve conflict 3 (line 1863) — _create_metadata_document**

Take HEAD side (compression envelope). Add `document_path` parameter as described in Step 7.

- [ ] **Step 12: Resolve conflict 4 (line 2387) — get_reconciliation_metadata**

Take HEAD side: keep `get_shared_session()` and `_decode_json()` (msgspec). Add signed URL failure handling from stashed side:

After the signed URL fetch, add:
```python
else:
    self.logger.warning("Failed to download from signed URL (status %s)", signed_resp.status)
    return None
```

- [ ] **Step 13: Verify and stage**

```bash
grep -n "^<<<<<<<\|^=======\|^>>>>>>>" backend/python/app/modules/transformers/blob_storage.py
# Expected: no output
git add backend/python/app/modules/transformers/blob_storage.py
```

- [ ] **Step 14: Commit**

```bash
git commit -m "resolve: merge blob_storage.py — hierarchical paths on main's infrastructure

Keep main's compression (_maybe_compress_record), shared sessions, msgspec.
Add hierarchical storage path computation, update_record_buffer,
get_actual_content_path, document_path params, redesigned apply() flow.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 3: Resolve data_source_entities_processor.py Conflicts

**Files:**
- Resolve: `backend/python/app/connectors/core/base/data_processor/data_source_entities_processor.py` (10 conflicts)

**Interfaces:**
- Consumes: `StorageCleanupHelper` from `app.connectors.core.base.data_processor.storage_cleanup` (staged, clean)
- Produces: `DataSourceEntitiesProcessor` with PendingMove lifecycle integrated into main's architecture

**Merge strategy:** Start from HEAD (main). Add the 3 storage cleanup helper methods and PendingMove type from stashed. Wire blob move flushes into `on_new_records`, `on_record_metadata_update`, and `on_records_moved` — AFTER transaction commit, BEFORE kafka events, using main's batch `send_messages`.

- [ ] **Step 1: Start from HEAD**

```bash
git show :2:backend/python/app/connectors/core/base/data_processor/data_source_entities_processor.py > backend/python/app/connectors/core/base/data_processor/data_source_entities_processor.py
```

- [ ] **Step 2: Add imports**

Add to the import section:

```python
from app.connectors.core.base.data_processor.storage_cleanup import StorageCleanupHelper
```

Add the PendingMove type alias near the top of the file (after imports):

```python
PendingMove = tuple[str, str, str]  # (org_id, old_path, new_path)
```

- [ ] **Step 3: Add storage cleanup helper methods**

Add these three methods to the `DataSourceEntitiesProcessor` class, after `__init__`/`initialize` and before the record processing methods:

```python
def _get_storage_cleanup(self) -> StorageCleanupHelper | None:
    if not hasattr(self, '_storage_cleanup'):
        try:
            self._storage_cleanup = StorageCleanupHelper(
                config_service=self.config_service,
                graph_provider=self.graph_provider,
                logger=self.logger,
            )
        except Exception:
            self._storage_cleanup = None
    return self._storage_cleanup

async def _run_bounded(self, coro, *, label: str, timeout: float = 10.0):
    try:
        await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        self.logger.warning("Storage %s timed out after %.1fs", label, timeout)
    except Exception as e:
        self.logger.warning("Storage %s failed: %s", label, str(e))

async def _flush_pending_blob_moves(self, pending_moves: list[PendingMove]):
    if not pending_moves:
        return
    cleanup = self._get_storage_cleanup()
    if not cleanup:
        return
    for org_id, old_path, new_path in pending_moves:
        if old_path and new_path and old_path != new_path:
            await self._run_bounded(
                cleanup.move_record_tree(org_id, old_path, new_path),
                label=f"move {old_path} -> {new_path}",
            )
```

Also add `import asyncio` to the imports if not already present.

- [ ] **Step 4: Modify _handle_updated_record to capture old_path and return PendingMove**

Change `_handle_updated_record` signature from:
```python
async def _handle_updated_record(self, record: Record, existing_record: Record, tx_store: TransactionStore) -> None:
```
To:
```python
async def _handle_updated_record(self, record: Record, existing_record: Record, tx_store: TransactionStore, old_path: str | None = None) -> list[PendingMove]:
```

At the end of the method body (after `tx_store.batch_upsert_records([record])`), add:

```python
pending: list[PendingMove] = []
if old_path:
    cleanup = self._get_storage_cleanup()
    if cleanup:
        try:
            new_path = await cleanup.build_record_path(record)
            if new_path and old_path != new_path:
                pending.append((record.org_id, old_path, new_path))
        except Exception as e:
            self.logger.debug("Could not compute new storage path: %s", str(e))
return pending
```

- [ ] **Step 5: Wire old_path capture into _process_record**

In `_process_record`, before the call to `_handle_updated_record`, add:

```python
old_path = None
cleanup = self._get_storage_cleanup()
if cleanup and existing_record:
    try:
        old_path = await cleanup.build_record_path(existing_record)
    except Exception:
        old_path = None
```

Change the `_handle_updated_record` call to:
```python
moves = await self._handle_updated_record(record, existing_record, tx_store, old_path=old_path)
```

Change `_process_record` return type to include moves. Return `(record, moves)` instead of just `record`. Callers that currently do `processed = await self._process_record(...)` need to unpack: `processed, moves = await self._process_record(...)`.

- [ ] **Step 6: Wire blob moves into on_new_records**

In `on_new_records`, collect pending moves from all `_process_record` calls:

```python
all_pending_moves: list[PendingMove] = []
```

Inside the loop, after each `_process_record` call:
```python
processed, moves = await self._process_record(record, permissions, tx_store)
if processed:
    records_to_publish.append(processed)
    all_pending_moves.extend(moves)
```

After the transaction commits but BEFORE the batch `send_messages` call:
```python
await self._flush_pending_blob_moves(all_pending_moves)
```

Keep main's publishable filtering, batch `send_messages`, and `_mark_queued_after_publish` unchanged.

- [ ] **Step 7: Wire blob moves into on_record_metadata_update**

In `on_record_metadata_update`, after `_process_record` and `_preserve_indexing_state`, capture old_path BEFORE the transaction updates:

```python
old_path = None
cleanup = self._get_storage_cleanup()
if cleanup and existing_record:
    try:
        old_path = await cleanup.build_record_path(existing_record)
    except Exception:
        old_path = None
```

Pass `old_path` to `_handle_updated_record`:
```python
moves = await self._handle_updated_record(processed_record, existing_record, tx_store, old_path=old_path)
```

After transaction commits:
```python
await self._flush_pending_blob_moves(moves)
```

- [ ] **Step 8: Wire connector delete cleanup into on_records_deleted_cascade**

After the existing `notify_kb_records_changed` call (main line ~1464), add:

```python
cleanup = self._get_storage_cleanup()
if cleanup:
    await self._run_bounded(
        cleanup.delete_connector_storage(org_id, connector_id),
        label=f"delete connector storage {connector_id}",
        timeout=30.0,
    )
```

Note: `org_id` and `connector_id` need to be available. Check the method signature and local variables — they should be accessible from the method's parameters or the records being deleted. If connector_id isn't directly available, derive it from the records: `connector_id = records[0].connector_id if records else None`.

- [ ] **Step 9: Verify and stage**

```bash
grep -n "^<<<<<<<\|^=======\|^>>>>>>>" backend/python/app/connectors/core/base/data_processor/data_source_entities_processor.py
# Expected: no output
git add backend/python/app/connectors/core/base/data_processor/data_source_entities_processor.py
```

- [ ] **Step 10: Run existing tests to check for regressions**

```bash
cd backend/python && python -m pytest tests/unit/connectors/core/test_data_processor.py -x -q 2>&1 | head -50
```

- [ ] **Step 11: Commit**

```bash
git commit -m "resolve: merge data_source_entities_processor.py — add PendingMove lifecycle

Keep all main improvements: version bumping, _preserve_indexing_state,
batch send_messages, KB cache invalidation, placeholder promotion.
Add: StorageCleanupHelper integration, PendingMove type, old_path capture,
_flush_pending_blob_moves after transaction commit.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 4: Port storage_search.py to agent_loop_lib + Security Fixes

**Files:**
- Modify: `backend/python/app/agents/actions/storage_search/storage_search.py`
- Modify: `backend/python/app/utils/pattern_match.py` (update imports if needed)

**Interfaces:**
- Consumes: `@tool` from `app.agent_loop_lib.tools.decorators`, `ToolParameter` / `Tag` from `app.agent_loop_lib.tools.base`
- Produces: `StoragePatternMatch` class with `run_command` and `find_records` tools registered via agent_loop_lib

**Changes needed:**
1. Replace old tool framework imports with agent_loop_lib
2. Replace `@tool(app_name=..., tool_name=...)` with `@tool(path=..., parameters=[...])`
3. Fix path traversal vulnerability in `_resolve_connector_path`
4. Switch `find` command validation from denylist to allowlist
5. Add `-delete` to find denylist (immediate fix before allowlist)

- [ ] **Step 1: Replace imports**

Change:
```python
from app.agents.tools.config import ToolCategory
from app.agents.tools.decorator import tool
from app.agents.tools.models import ToolIntent
```
To:
```python
from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
```

- [ ] **Step 2: Replace @tool decorators on run_command**

Change the `@tool(app_name="storage_pattern_match", tool_name="run_command", ...)` decorator to:

```python
@tool(
    path="/tools/storage_pattern_match/run_command",
    short_description="Run read-only search commands on local blob storage",
    description="<keep the existing llm_description text>",
    parameters=[
        ToolParameter(name="connector_id", type=ParameterType.STRING, description="The connector ID to search within", required=True),
        ToolParameter(name="command", type=ParameterType.STRING, description="The shell command pipeline to execute (read-only commands only)", required=True),
        ToolParameter(name="record_date", type=ParameterType.STRING, description="Optional ISO date to filter records by modification time", required=False),
    ],
    tags=[Tag(key="category", value="search"), Tag(key="type", value="read")],
    display_name="Searched local storage",
)
```

Keep the method signature `async def run_command(self, connector_id, command, record_date=None)` unchanged — the `@tool` decorator annotates it without changing runtime behavior.

- [ ] **Step 3: Replace @tool decorators on find_records**

```python
@tool(
    path="/tools/storage_pattern_match/find_records",
    short_description="Find records matching a command and return structured metadata",
    description="<keep the existing llm_description text>",
    parameters=[
        ToolParameter(name="connector_id", type=ParameterType.STRING, description="The connector ID to search within", required=True),
        ToolParameter(name="command", type=ParameterType.STRING, description="The command to find matching record files", required=True),
        ToolParameter(name="max_results", type=ParameterType.INTEGER, description="Maximum records to return (1-20)", required=False, default=10),
    ],
    tags=[Tag(key="category", value="search"), Tag(key="type", value="read")],
    display_name="Found records in local storage",
)
```

- [ ] **Step 4: Fix path traversal in _resolve_connector_path**

Add connector_id validation before path construction:

```python
async def _resolve_connector_path(self, connector_id: str) -> tuple[str | None, str | None]:
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', connector_id):
        return None, f"Error: invalid connector_id '{connector_id}'"

    # ... existing mount_root / org_id resolution ...

    connector_dir = os.path.join(mount_root, org_id, "PipesHub", "records", connector_id)

    # Verify resolved path is within the expected base
    base_dir = os.path.realpath(os.path.join(mount_root, org_id, "PipesHub", "records"))
    resolved_dir = os.path.realpath(connector_dir)
    if not resolved_dir.startswith(base_dir + os.sep) and resolved_dir != base_dir:
        return None, f"Error: connector path escapes base directory"

    if not os.path.isdir(connector_dir):
        return None, f"Error: no records directory found for connector '{connector_id}'."

    return connector_dir, None
```

- [ ] **Step 5: Switch find validation to allowlist**

Replace the `find` entry in `_DANGEROUS_FLAGS_BY_BINARY` with an allowlist approach. Add a new constant:

```python
_FIND_ALLOWED_PRIMARIES: frozenset[str] = frozenset({
    "-name", "-iname", "-type", "-mtime", "-newer", "-size", "-path", "-ipath",
    "-print", "-print0", "-maxdepth", "-mindepth", "-not", "-and", "-or",
    "-prune", "-regex", "-iregex", "-regextype", "-empty", "-readable",
    "-perm", "-user", "-group", "-links", "-inum", "-samefile",
    "-newer", "-newermt", "-newerat", "-newerct",
    "-daystart", "-follow", "-mount", "-xdev",
    "-true", "-false", "-depth", "-noleaf",
    # Parentheses for grouping
    "(", ")",
})
```

In `_validate_command`, after the `_check_dangerous_flags` call, add a find-specific allowlist check:

```python
if binary == "find":
    for i, arg in enumerate(parts[1:], start=1):
        if arg.startswith("-") and arg not in _FIND_ALLOWED_PRIMARIES:
            # Allow numeric values like -1 (for -mtime -1)
            try:
                float(arg)
                continue
            except ValueError:
                pass
            return False, f"Blocked: find primary '{arg}' is not in the read-only allowlist"
```

Remove `"find"` from `_DANGEROUS_FLAGS_BY_BINARY` since the allowlist supersedes it.

- [ ] **Step 6: Also add -delete to denylist as defense-in-depth**

Even with the allowlist, keep `_DANGEROUS_FLAGS_BY_BINARY` for `find` as a second layer:

```python
"find": frozenset({"-exec", "-execdir", "-ok", "-okdir", "-fprintf", "-fprint", "-fprint0", "-fls", "-delete"}),
```

The allowlist check runs first; this catches anything that slips through.

- [ ] **Step 7: Verify pattern_match.py imports still work**

Read `backend/python/app/utils/pattern_match.py` and check imports from `storage_search.py`. The imports are:

```python
from app.agents.actions.storage_search.storage_search import (
    StoragePatternMatch, _validate_command, is_local_storage,
)
```

These are class/function imports, not decorator imports, so they should work unchanged. The `@tool` decorator on methods only annotates them — calling `storage_tool.find_records(connector_id, command, max_results=10)` directly still works because `BoundMethodTool` wrapping happens at runtime in the tool loader, not at definition time.

- [ ] **Step 8: Stage and commit**

```bash
git add backend/python/app/agents/actions/storage_search/storage_search.py
git commit -m "refactor: port storage_search to agent_loop_lib + security fixes

- Replace app.agents.tools imports with agent_loop_lib
- Use @tool(path=..., parameters=[...]) decorator pattern
- Fix path traversal: validate connector_id, verify realpath
- Switch find validation from denylist to allowlist of read-only primaries
- Add -delete, -fprint0, -fls to find denylist as defense-in-depth

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 5: Resolve retrieval.py Conflicts

**Files:**
- Resolve: `backend/python/app/agents/actions/retrieval/retrieval.py` (10 conflicts)

**Interfaces:**
- Consumes: `KnowledgeScope` from `app.agents.actions.knowledge_graph.ops.scope`, `execute_pattern_match_pipeline` / `merge_pattern_match_results` / `cap_pattern_match_blocks` from `app.utils.pattern_match`, `is_local_storage` from `app.agents.actions.storage_search.storage_search`
- Produces: `search_internal_knowledge` with parallel semantic + pattern match search, using agent_loop_lib `@tool` framework

**Merge strategy:** Start from HEAD (agent_loop_lib tools, KnowledgeScope, unified connector_ids). Add pattern match as a parallel search path integrated into the existing search flow.

- [ ] **Step 1: Start from HEAD**

```bash
git show :2:backend/python/app/agents/actions/retrieval/retrieval.py > backend/python/app/agents/actions/retrieval/retrieval.py
```

- [ ] **Step 2: Add pattern match imports**

Add to the imports section:

```python
from app.utils.pattern_match import (
    execute_pattern_match_pipeline,
    merge_pattern_match_results,
    cap_pattern_match_blocks,
    DEFAULT_PATTERN_MATCH_BLOCK_BUDGET,
)
from app.agents.actions.storage_search.storage_search import is_local_storage
```

- [ ] **Step 3: Integrate pattern match into search_internal_knowledge**

The pattern match runs in parallel with semantic search via `asyncio.gather`. Inside `search_internal_knowledge`, after the semantic search results are collected, merge pattern match results.

Find the section where semantic search is called (the `retrieval_service.search_with_filters()` call or the fan-out loop). Wrap it with an `asyncio.gather` that also runs `execute_pattern_match_pipeline`:

```python
# Run semantic search and pattern match in parallel
pattern_match_task = None
try:
    config_service = self.state.get("config_service")
    if config_service and is_local_storage(self.state.get("storage_config")):
        pattern_match_task = asyncio.create_task(
            execute_pattern_match_pipeline(
                query=query,
                org_id=org_id,
                user_id=user_id,
                graph_provider=graph_provider,
                config_service=config_service,
                filters=filter_groups,
                logger=self.logger,
            )
        )
except Exception:
    pattern_match_task = None

# ... existing semantic search code ...

# After semantic results are accumulated, merge pattern match results
if pattern_match_task:
    try:
        pm_records = await pattern_match_task
        if pm_records:
            pm_blocks = await merge_pattern_match_results(
                pm_records,
                virtual_record_id_to_result=self.state.get("virtual_record_id_to_result", {}),
                graph_provider=graph_provider,
                org_id=org_id,
                logger=self.logger,
            )
            if pm_blocks:
                pm_blocks = cap_pattern_match_blocks(
                    pm_blocks,
                    budget=DEFAULT_PATTERN_MATCH_BLOCK_BUDGET,
                    virtual_record_id_to_result=self.state.get("virtual_record_id_to_result", {}),
                )
                # Extend the flattened results
                all_results.extend(pm_blocks)
    except Exception as e:
        self.logger.warning("Pattern match failed, continuing with semantic results: %s", str(e))
```

The exact insertion point depends on the structure of HEAD's `search_internal_knowledge`. Read the HEAD version carefully — the pattern match integration should happen AFTER semantic results are collected and BEFORE the final result formatting.

Key points:
- Pattern match is fire-and-forget on failure — semantic search alone is fine
- Deduplication happens inside `merge_pattern_match_results` (checks against `virtual_record_id_to_result`)
- Budget capping prevents context overflow
- The `asyncio.create_task` starts the pattern match concurrently with semantic search

- [ ] **Step 4: Verify no conflict markers remain**

```bash
grep -n "^<<<<<<<\|^=======\|^>>>>>>>" backend/python/app/agents/actions/retrieval/retrieval.py
```

- [ ] **Step 5: Stage and commit**

```bash
git add backend/python/app/agents/actions/retrieval/retrieval.py
git commit -m "resolve: merge retrieval.py — add pattern match to agent_loop_lib search

Keep HEAD's agent_loop_lib framework, KnowledgeScope, unified connector_ids.
Add pattern match as parallel search path alongside semantic search.
Pattern match is fail-soft — semantic-only results returned on failure.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 6: Resolve Test File Conflicts

**Files:**
- Resolve: `backend/python/tests/unit/services/graph_db/test_arango_http_provider.py` (16 conflicts)
- Resolve: `backend/python/tests/unit/services/parsing/providers/test_all_parsing_providers.py` (12 conflicts)
- Resolve: `backend/python/tests/unit/utils/test_fetch_full_record.py` (12 conflicts)
- Resolve: `backend/python/tests/unit/connectors/core/test_data_source_entities_processor.py` (10 conflicts)

**Interfaces:**
- Consumes: resolved production code from Tasks 1-5
- Produces: passing test suites that cover both main features and storage pattern additions

- [ ] **Step 1: test_arango_http_provider.py — take HEAD**

```bash
git show :2:backend/python/tests/unit/services/graph_db/test_arango_http_provider.py > backend/python/tests/unit/services/graph_db/test_arango_http_provider.py
git add backend/python/tests/unit/services/graph_db/test_arango_http_provider.py
```

Take HEAD because:
- HEAD uses `update_indexing_status_for_record_ids` (generalized, accepts status param) — stashed uses the old `reset_indexing_status_to_queued_for_record_ids`
- HEAD includes `TestGetConnectorStatsKB` tests (stashed doesn't)
- HEAD's behavior (updates already-QUEUED records) matches the generalized method
- The stashed `check_vrids_accessible` tests are already staged in a separate test file (no conflict)

- [ ] **Step 2: test_all_parsing_providers.py — take HEAD**

```bash
git show :2:backend/python/tests/unit/services/parsing/providers/test_all_parsing_providers.py > backend/python/tests/unit/services/parsing/providers/test_all_parsing_providers.py
git add backend/python/tests/unit/services/parsing/providers/test_all_parsing_providers.py
```

Take HEAD: uses `parse_pdf_batched`, `model_dump_json`, checks `result.raw_document` — current parsing API.

- [ ] **Step 3: test_fetch_full_record.py — take HEAD**

```bash
git show :2:backend/python/tests/unit/utils/test_fetch_full_record.py > backend/python/tests/unit/utils/test_fetch_full_record.py
git add backend/python/tests/unit/utils/test_fetch_full_record.py
```

Take HEAD: uses `"u1"` naming, `check_record_access_with_details` pattern.

- [ ] **Step 4: test_data_source_entities_processor.py — merge**

```bash
git show :2:backend/python/tests/unit/connectors/core/test_data_source_entities_processor.py > backend/python/tests/unit/connectors/core/test_data_source_entities_processor.py
```

This file has 10 conflicts mostly around method names and the PendingMove type. Since we kept HEAD's production code as base and added PendingMove, we need to:

1. Take HEAD as base
2. Add/update test methods that cover the PendingMove lifecycle:
   - Tests that verify `_flush_pending_blob_moves` is called after transaction commit
   - Tests that verify `StorageCleanupHelper` integration
   - Tests that verify `_handle_updated_record` returns `list[PendingMove]`

Read the stashed test content around lines 5690-5900 (the large conflict block). This likely contains `TestOnNewRecordGroupsMovesBlobsOnRename` and similar tests. Port those test classes into the HEAD base, adapting to use HEAD's method signatures (batch `send_messages`, `_preserve_indexing_state`, etc.).

After editing:
```bash
git add backend/python/tests/unit/connectors/core/test_data_source_entities_processor.py
```

- [ ] **Step 5: Verify no markers remain in any test file**

```bash
grep -rn "^<<<<<<<\|^=======\|^>>>>>>>" backend/python/tests/unit/
```

Expected: no output.

- [ ] **Step 6: Run all resolved test files**

```bash
cd backend/python && python -m pytest tests/unit/services/graph_db/test_arango_http_provider.py tests/unit/services/parsing/providers/test_all_parsing_providers.py tests/unit/utils/test_fetch_full_record.py tests/unit/connectors/core/test_data_source_entities_processor.py -x -q 2>&1 | tail -20
```

- [ ] **Step 7: Commit**

```bash
git add backend/python/tests/unit/services/graph_db/test_arango_http_provider.py backend/python/tests/unit/services/parsing/providers/test_all_parsing_providers.py backend/python/tests/unit/utils/test_fetch_full_record.py backend/python/tests/unit/connectors/core/test_data_source_entities_processor.py
git commit -m "resolve: merge test file conflicts — take HEAD + port storage pattern tests

test_arango: HEAD's generalized update_indexing_status + KB stats tests
test_parsing: HEAD's parse_pdf_batched API
test_fetch: HEAD's naming conventions
test_entities_processor: HEAD base + PendingMove lifecycle tests

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 7: Collection Support + Final Validation

**Files:**
- Verify: `backend/python/app/utils/storage_path.py`
- Verify: `backend/python/app/connectors/core/base/data_processor/storage_cleanup.py`
- Verify: `backend/python/app/connectors/services/event_service.py`
- Possibly modify: collection-related connector code

**Interfaces:**
- Consumes: all resolved code from Tasks 1-6
- Produces: storage pattern working for both connector records AND collection records

Collections are now connectors (KB collection ID = connector ID). The hierarchical path logic in `storage_path.py` keys on `connector_id`, so it should already work for collections. This task verifies that assumption and fixes any gaps.

- [ ] **Step 1: Verify storage_path.py handles collections**

Read `backend/python/app/utils/storage_path.py`. Check that `build_hierarchical_storage_path` works for records with `origin == OriginTypes.UPLOAD` (KB collection uploads). The function should:
1. Get `connector_id` from the record
2. Look up the record group
3. Build the path hierarchy

For KB uploads, the record's `connector_id` is the collection's connector ID. The path should be `records/<collectionConnectorId>/<groupName>/<hierarchy>`.

- [ ] **Step 2: Verify event_service.py handles collection connector deletion**

Read the staged diff for `backend/python/app/connectors/services/event_service.py`. Verify that when a collection connector is deleted, `StorageCleanupHelper.delete_connector_storage` is called with the collection's connector ID.

- [ ] **Step 3: Check collection upload flow through BlobStorage.apply()**

Trace the collection upload path:
1. KB upload → creates records with `origin=UPLOAD`, `connector_id=<collection_connector_id>`
2. Indexing pipeline → calls `BlobStorage.apply(ctx)`
3. `apply()` calls `_build_hierarchical_storage_path(record, virtual_record_id)`
4. Path builder uses `record.connector_id` → `records/<collection_connector_id>/...`

Verify this flow works by checking that:
- Collection records have `connector_id` set
- The graph provider can resolve record groups for collection connectors
- The hierarchical path builder handles the collection's record structure

- [ ] **Step 4: Run the full storage-related test suite**

```bash
cd backend/python && python -m pytest tests/unit/utils/test_storage_path.py tests/unit/agents/actions/test_storage_search.py tests/unit/connectors/core/test_storage_cleanup.py tests/unit/modules/transformers/test_blob_storage.py tests/unit/modules/transformers/test_blob_storage_coverage_gaps.py tests/unit/modules/transformers/test_blob_storage_override.py tests/unit/modules/transformers/test_blob_storage_sql_reconciliation.py -x -q 2>&1 | tail -30
```

- [ ] **Step 5: Run the full test suite for all modified files**

```bash
cd backend/python && python -m pytest tests/unit/ -x -q --timeout=120 2>&1 | tail -50
```

Fix any failures. Common issues to watch for:
- Import errors from framework migration
- Method signature mismatches (return type changes in `_handle_updated_record`, `_process_record`)
- Missing `storage_path` key in `ctx.settings` (downstream code may expect it)
- Mock setup in tests not matching new signatures

- [ ] **Step 6: Verify no conflict markers remain anywhere**

```bash
grep -rn "^<<<<<<<\|^=======\|^>>>>>>>" backend/python/ backend/nodejs/
```

Expected: no output.

- [ ] **Step 7: Final commit if any fixes were needed**

```bash
git add -A
git commit -m "fix: collection support verification and test fixes

Verified storage pattern works for collection connectors.
Fixed any test/import issues from merge resolution.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```
