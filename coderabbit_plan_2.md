# Implementation Plan for Latest CodeRabbit Findings

Here is the detailed plan to address the 4 new findings from the latest commit.

### 1. `backend/python/app/connectors/sources/localKB/api/kb_router.py`
**Issue:** `_get_kb_context_for_record` might raise an exception, preventing the HTTP endpoint from returning 200 (causing a 500), even though the actual graph update already succeeded.
**Action:** Wrap `_get_kb_context_for_record` in a `try/except` block. If it fails, log the error and proceed without incrementing the corpus revision, preserving the successful update response and event publication.
```python
try:
    kb_context = await request.app.state.graph_provider._get_kb_context_for_record(record_id)
    _bump_org_id = kb_context.get("org_id") if kb_context else None
except Exception as e:
    logger.warning(f"Failed to lookup KB context for record {record_id} prior to revision bump: {e}")
    _bump_org_id = None

if _bump_org_id:
    await increment_org_corpus_revision_with_retry(request.app.state.graph_provider, _bump_org_id)
```

### 2. `backend/python/app/query_main.py`
**Issue:** `SemanticCacheService.initialize(embedding_dimension: int)` has no default value. The background purge loop was calling it without an argument, catching a `TypeError`, and skipping initialization, which means it runs against an uninitialized cache (or fails).
**Action:** Defer initialization until the embedding dimension is known. We will update `_scheduled_semantic_cache_purge` to first check if the cache is already initialized. If it is not, the purge task will gracefully skip execution until a chat request naturally initializes the cache with the correct dimension. 

### 3. `backend/python/app/services/cache/accessible_records_cache.py`
**Issue:** `_trailing_bump` runs a single `sleep(2.0)` and tries to increment the revision once. If it fails, the bump is lost until shutdown, meaning old cached answers remain eligible.
**Action:** Implement bounded retries with backoff inside `_trailing_bump`. We will wrap the `increment_corpus_revision` call in a retry loop (e.g., 3 max attempts) with an escalating `asyncio.sleep()` delay. The task and dirty state will remain tracked until the loop either succeeds or completely exhausts its retries.
```python
success = False
for attempt in range(1, 4):
    try:
        await self.graph_provider.increment_corpus_revision(target_org)
        success = True
        break
    except Exception as bump_exc:
        self.logger.warning(
            "Failed to increment corpus revision for org %s (attempt %d): %s",
            target_org, attempt, str(bump_exc)
        )
        if attempt < 3:
            await asyncio.sleep(2.0 * attempt)
```

### 4. Nitpick: `backend/python/app/services/vector_db/opensearch/opensearch.py`
**Issue:** Unused `_is_cosine_index` call inside `scroll`.
**Action:** Remove the `_is_cosine_index` check line, leaving the rest of the pagination flow completely untouched.
