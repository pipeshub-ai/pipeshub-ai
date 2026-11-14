here is the implementation plan for PR `#5490` at commit `3190549`. It covers the unresolved review threads, the latest outside-diff finding, and the failed CI jobs I inspected.

## Review findings

### 1. Scope user-added events to the correct organization

**Issue:** `_handle_user_added` calls `get_user_by_email` without an organization. It can reuse a user ID from another organization and write the event’s organization onto that user. [Review thread](https://github.com/pipeshub-ai/pipeshub-ai/pull/5490#discussion_r4574912356)

**Plan:**

1. Obtain the trusted organization from the event’s established organization context. Check that it agrees with the event payload at the boundary.
2. Pass that organization to `get_user_by_email`, retaining `raise_on_error=True`.
3. Before reusing a returned ID, reject an explicit organization mismatch. If ownership cannot be established safely, do not reuse the ID.
4. Use the same organization for the user write, membership edge, and person adoption.
5. Add tests with the same email in two organizations, a mismatched provider result, a missing user, and a read failure. Assert that no cross-organization write occurs.

Coordinate this change with the organization-context CI fix below. Do not add more direct payload reads to bypass the guard.

### 2. Make user-deletion ownership checks fail closed

**Issue:** `_handle_user_deleted` does not request errors from its scoped user lookup or ownership document read. Its unscoped fallback can also accept a candidate when the ownership document is missing. [Review thread](https://github.com/pipeshub-ai/pipeshub-ai/pull/5490#discussion_r4574912358)

**Plan:**

1. Pass `raise_on_error=True` to the scoped `get_user_by_email` and `get_document` calls.
2. Prefer the scoped lookup. If the legacy unscoped fallback remains, require a successfully fetched user document with verified matching ownership before using its ID.
3. Preserve the normal not-found result for a genuinely absent user. Propagate read failures so the event can be retried without a write.
4. The unscoped `get_entity_id_by_email` API does not expose `raise_on_error`. Do not claim that its failures are retryable. Either remove that fallback where it is unnecessary or separately design an error-reporting scoped API.
5. Test matching and mismatched organizations, missing ownership documents, read exceptions, absent users, and successful cleanup. Assert that failures do not deactivate or move another organization’s user.

### 3. Correct PostgreSQL duplicate selection and legacy matching

**Issue:** The current bulk fallback handles a padded stored email when the vetted lookup finds no candidate. It does **not** run when the exact candidate is inactive. The single lookup also returns the first vetted candidate without checking for a preferred active legacy user. [Original padded-email thread](https://github.com/pipeshub-ai/pipeshub-ai/pull/5490#discussion_r4574205741) · [Duplicate-selection thread](https://github.com/pipeshub-ai/pipeshub-ai/pull/5490#discussion_r4574912353)

**Plan:**

1. In `backend/python/app/services/graph_db/postgres/identity.py`, retain the indexed vetted lookup for normalized addresses.
2. For each address with no active exact candidate, run the organization-scoped legacy match. Combine candidates and select an active user before an inactive user. Use a stable ID tie-break for equal status.
3. Apply the same rule to `get_user_by_email`. Keep normalized result keys and any required compatibility aliases in the bulk method.
4. Keep the `nodes_user_email` indexed expression on the exact path. Check the legacy query plan with PostgreSQL `EXPLAIN`. If a trimmed expression index is needed, add it through the repository’s migration process.
5. The vetted SQL is generated from a manifest and is shared by other principal lookups. Do not edit the generated SQL directly or change its contract without checking those callers.
6. Add real PostgreSQL tests for a padded stored row, an inactive exact user plus an active padded user, both ID orders, inactive-only results, and organization isolation. Cover both single and bulk lookups.

The existing integration test covers **padded input**, not a padded stored row.

### 4. Use one canonical key in PostgreSQL app-user sync

**Issue:** `batch_upsert_app_users` lowercases the raw input without trimming it, while `to_arango_base_user()` stores a trimmed email. A later sync can miss the stored user and create another node. [Latest review’s outside-diff finding](https://github.com/pipeshub-ai/pipeshub-ai/pull/5490#pullrequestreview-5906134410)

**Plan:**

1. Define one canonical key for each `AppUser` before the existence lookup.
2. Use that key consistently for `_users_by_email_rows`, in-batch deduplication, the `have` and `shared` maps, new-user selection, and link selection.
3. Align the key with the serialized value. Check the existing PostgreSQL-versus-Python lowercase behavior for non-ASCII addresses before changing it.
4. Preserve the existing rule that a source account already linked to one duplicate keeps its link. Preserve the single-snapshot behavior documented by `_users_by_email_rows`.
5. Add a PostgreSQL integration test that syncs a padded mixed-case address twice and asserts one user and a stable link. Also test same-batch variants and existing linked duplicates.

### 5. Run the PostgreSQL completion check in a system context

**Issue:** The final unscoped check in `normalize_user_emails.py` runs without an organization or system context. PostgreSQL `_q()` requires one, so the check can return `EXIT_PARTIAL` instead of setting the completion flag. [Review thread](https://github.com/pipeshub-ai/pipeshub-ai/pull/5490#discussion_r4574912332)

**Plan:**

1. Run only the final cross-organization `fetch_unnormalized_users` read inside `org_context.system(...)`. Keep per-organization writes inside `for_org(...)`.
2. Preserve `EXIT_PARTIAL` and leave the flag unset if the read fails or finds a remaining candidate.
3. The baseline schema grants `pipeshub_jobs` read access and defines a jobs policy for `graph.nodes`. Verify that the deployed job uses that role and can see users across organizations; the schema alone does not verify deployed access.
4. Add PostgreSQL integration coverage for a successful all-org apply, a remaining user outside the enumerated organizations, and a failed system read. Verify the flag in each case.

### 6. Remove email addresses from logs

**Issue:** Three new log calls expose email addresses. The PR’s `LOG-PII` check fails on them. The deletion log also fails an existing log-capture test. [ArangoDB](https://github.com/pipeshub-ai/pipeshub-ai/pull/5490#discussion_r4574912349) · [Neo4j](https://github.com/pipeshub-ai/pipeshub-ai/pull/5490#discussion_r4574912350) · [Deletion handler](https://github.com/pipeshub-ai/pipeshub-ai/pull/5490#discussion_r4574912361)

**Plan:**

1. Remove the email placeholder and argument from both post-create warnings. Retain `user.id`.
2. Remove the email placeholder and argument from the deletion failure log. Retain the organization and user identifiers.
3. Add or update log-capture tests for these paths. Assert that the email is absent.
4. Run the PR’s `log-pii` validation as well as Ruff. A standalone Ruff pass does not cover the failing privacy check.

## Failed CI jobs

The following results are from the inspected runs for commit `3190549`. They can change after a push or rerun.

| Failed job | Issue | Fix and validation |
|:---|:---|:---|
| [python-unit (core)](https://github.com/pipeshub-ai/pipeshub-ai/actions/runs/43621283721/job/132527358853) | `test_no_new_direct_org_id_reads` reports increases in the ArangoDB provider, Neo4j provider, and Kafka handler. | Trace each new read. Use the verified ambient organization where appropriate, while preserving explicit ownership checks. Run `tests/unit/core/test_org_context_guards.py`. Only update the baseline for an intentional, reviewed exception; do not use it to hide a tenant-boundary issue. |
| [python-unit (services)](https://github.com/pipeshub-ai/pipeshub-ai/actions/runs/43621283721/job/132527358822) | Two PostgreSQL tests expect only the original email argument, but the lookup now passes original and normalized forms. A third test detects the deletion email in the log. | After implementing the lookup contract, update the argument assertions to match the intended behavior. Keep assertions for ambient organization isolation. Fix the log as described above and rerun the affected tests and services shard. |
| [python-unit (agents)](https://github.com/pipeshub-ai/pipeshub-ai/actions/runs/43621283721/job/132527358813) | Two MCP lifecycle tests fail because the deletion handler awaits a `MagicMock`. | Update the graph-provider fixture in `tests/unit/agents/mcp/test_lifecycle.py` to use an `AsyncMock` for the scoped lookup and any ownership read. Return a user with matching ownership. Keep both cleanup-success and cleanup-failure assertions. |
| [python-unit-tests](https://github.com/pipeshub-ai/pipeshub-ai/actions/runs/43621283721/job/132531562149) | This aggregate job fails because the `python-unit` matrix failed. | Fix and rerun the three failed shards. Then verify the aggregate job passes; it has no separate test failure in the inspected log. |
| [pg-runtime python (17, 5)](https://github.com/pipeshub-ai/pipeshub-ai/actions/runs/43621283597/job/132527325518) and [python (18, 5)](https://github.com/pipeshub-ai/pipeshub-ai/actions/runs/43621283597/job/132527325459) | Both ran 1,465 PostgreSQL graph cases with no failed tests, but the guard expected 1,464. | After adding the required integration tests, collect the actual case counts and update the shard’s expected count through the repository’s established pin process. Rerun both versions and verify there are no unexpected skips. |
| [Connector integration coverage](https://github.com/pipeshub-ai/pipeshub-ai/actions/runs/43621283717/job/132527325376) | `test_pg_runtime_shards.PgRuntimeShardsTest.test_the_pins_add_up_to_the_suite` reports `1740 != 1739`. | Reconcile the shard pins with the total after the final test additions. The CI log names `scripts/test_pg_runtime_shards.py`, but that file was not available in the inspected checkout; locate the authoritative pin configuration before editing it. Rerun the script tests and this job. |
| [ruff-lint-pr](https://github.com/pipeshub-ai/pipeshub-ai/actions/runs/43621283717/job/132527325269) | The `log-pii` step reports the three logging findings above. | Remove the email values, run the same differential `log-pii` check used by CI, and rerun the job. |

## Completion checks

1. Run the targeted tests first, then the affected unit shards and PostgreSQL 17 and 18 runtime shards.
2. Verify the cross-organization jobs-role read and the PostgreSQL lookup plans against real databases.
3. Rerun the failed checks on the final commit. Recheck any checks that remain pending or were skipped.
4. Keep legacy matching available until backfill completion and normalized writes are established for the rollout.