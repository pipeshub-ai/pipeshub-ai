# Semantic Cache Security Architecture & Implementation Plan

CodeRabbit identified several risks in the semantic caching architecture across the two reviews. The zero-citation caching gap has **already been resolved** in our recent commit. Below is the updated plan detailing our progress on the security architecture review, followed by the exact implementation steps for the newly caught edge cases, incorporating the final source check feedback.

---

## Part 1: Security Architecture Review Responses

### 1. The "Zero Citation" Authorization Bypass
**The Finding:** "An eligible cached answer with no citations is replayed without a current record-access check... the per-user hash alone cannot establish that its text is still authorized."
**CodeRabbit's Proposal:** "Require a verifiable source-authorization basis for replayable answers, or exclude answers without one."

**Status: Already Fixed.** 
In `chatbot.py`, we added a strict guard block:
```python
if not cached_citations: 
    cached_entry = None
```
By doing this, any cached response that lacks citations is instantly rejected at the replay boundary. This guarantees that we *only* replay answers where we can iterate over the citations and verify the user still holds access to the underlying virtual records.

### 2. Revision Bump Failures & Cache Freshness Recovery
**The Finding:** "Cache freshness depends on a persisted revision bump... that can be lost after an increment or shutdown-flush failure."
**CodeRabbit's Proposal:** "Make revision advancement recoverable after failed bumps..."

**Status: Pending Propagation Fix.**
While the newly added 10-minute `_scheduled_semantic_cache_purge` loop successfully sweeps the cache *after* a persisted revision change, it cannot recover if the revision increment itself fails before persistence. 
Currently, `AccessibleRecordsInvalidator.close()` catches and logs its internal failures instead of propagating them. We acknowledge that the scheduled purge alone does not mark revision-failure recovery as complete. We must implement error propagation or a robust fallback for the increment step in the invalidator to ensure failed bumps are truly recoverable.

---

## Part 2: Implementation Plan for Latest Iteration Findings

### 1. `backend/python/app/query_main.py`
**Issue:** `app_container.semantic_cache_service()` is a resource provider from `dependency-injector`, meaning calling it actually returns an awaitable (coroutine). We missed the `await`.
**Action:** Update the line in `_scheduled_semantic_cache_purge` to properly await the provider:
```python
semantic_cache_svc = await app_container.semantic_cache_service()
```

### 2. `backend/python/app/connectors/api/router.py`
**Issue:** If a graph provider's `delete_record` succeeds but omits the `orgId`, the revision bump is silently skipped.
**Action:** Keep the missing-orgId warning. Add an `else` block to explicitly warn operators when the `orgId` is missing, aligning with how we track orphaned vectors:
```python
org_id = result.get("orgId")
if org_id:
    await increment_org_corpus_revision_with_retry(graph_provider, org_id)
else:
    logger.warning(
        f"Skipped corpus revision bump for record {record_id}: "
        f"delete_record returned no orgId."
    )
```

### 3. `test_focused_coderabbit_fixes.py` (test_redis_purge_initializes_cache)
**Issue:** Because `mock_gp` is an `AsyncMock`, `await graph_provider.get_all_orgs()` returns a `MagicMock`, which evaluates as a non-empty list. This tricks the test into entering the purge loop, where it incorrectly references the global, uninitialized container.
**Action:** Configure `mock_gp.get_all_orgs.return_value = []` (or properly mock an organization to test the purge loop securely) so the test explicitly skips unmocked retrieval-service paths without leaking real network attempts.

### 4. `test_focused_coderabbit_fixes.py` (test_worker_loop_shutdown_path)
**Issue:** The shutdown flush only delegates to the Kafka `worker_loop` if `DATA_STORE=neo4j`. In the test environment, this is absent, meaning the test inadvertently fell back to the main loop and `assert_called()` passed vacuously.
**Action:** Mock `os.environ["DATA_STORE"] = "neo4j"` for the duration of the test. Update assertions to explicitly check that `asyncio.run_coroutine_threadsafe` was called specifically with the `mock_worker_loop` argument.

### 5. `chatbot.py` Test Helper Refactor
**Issue:** The cache-validation logic in the test suite is duplicated from production.
**Action:** Extract the cache-validation `if` logic (virtual record iteration and citation verification) out of the main endpoint and into a module-level helper `_validate_cached_entry(cached_entry, virtual_records, active_org_id)`. Update `test_citation_free_cache_bypass` to import and run this helper rather than duplicating the implementation in the test file.

*(Note: The `arango_http_provider.py` AQL nitpick has been dropped, as `get_corpus_revision()` and `increment_corpus_revision()` correctly use the `CorpusRevision` literal which already matches the constant).*
