# Connector checklist

The definition of done for a new connector, and the list a reviewer checks it against. Each item says how to verify it: a behaviour test against a fake source, an integration test against a real one, or a read of the code. How to build each part is in [CONNECTOR.md](./CONNECTOR.md) and the documents it links.

Copy this list into the PR description and tick each item, or say why it does not apply.

## 0. Intake and design

- [ ] The PR description has a "Connector intake" section: editions, scope, objects synced, auth types, volume, filters, and the test account used. ([04-intake-questions.md](./docs/connectors/04-intake-questions.md))
- [ ] Every decision in [03-decisions.md](./docs/connectors/03-decisions.md) is recorded with its reason, and the ones marked "ask" were confirmed by a human.
- [ ] The permission mapping, object type by object type, is in the PR description, with any sharing the model cannot express and which way it errs. ([06-permissions.md](./docs/connectors/06-permissions.md))

## 1. Setup and registration

- [ ] The class extends `BaseConnector`; `tests/unit/connectors/test_connector_contract.py` and `test_graph_call_contract.py` pass with no new entry in their allow-lists.
- [ ] The client is in `app/sources/client/<name>/`, the endpoint wrapper in `app/sources/external/<name>/`.
- [ ] `@ConnectorBuilder` is complete: scopes, permission model, auth, resilience, info, icon, documentation link, filters, sync strategies.
- [ ] `Connectors` and `AppGroups` have the new member; the class is in `ConnectorFactory._connector_registry` under its lower-cased, space-free name (or `_beta_connector_definitions`); `ConnectorNameEnum` in `pipeshub-openapi.yaml` has the new value.
- [ ] The icon is at `frontend/public/icons/connectors/<name>.svg` and mapped in `frontend/app/components/ui/ConnectorIcon.tsx`.
- [ ] Config is read only through `self.config_service.get_config(f"/services/connectors/{connector_id}/config")`.

## 2. Authentication

- [ ] Every declared auth method connects end to end. Every secret auth field has `is_secret=True` (or a `PASSWORD` field type), so the API returns it masked.
- [ ] `test_connection_and_access()` checks the granted scopes or permissions. A missing one raises `ConnectorInitError` and the message names it. Returning `False` shows only "Connection test failed. Please verify your credentials have proper access."
- [ ] Wrong credentials raise `ConnectorInitError` with a message the user can act on, and the log has the vendor's reason.
- [ ] A sync that outlives an OAuth token keeps working: the token is re-read from config before each batch, and a 401 mid-sync calls `TokenRefreshService.refresh_now` with the refresh token already held, then retries, instead of failing the sync. Re-reading config and passing the new refresh token rotates a rotating token a second time (`02-low-level-design.md`). A static API token or bot token has no refresh: a 401 stops the sync and notifies the user, and is not retried.

## 3. Data model

- [ ] Every source object maps to the right entity and record type ([03-decisions.md](./docs/connectors/03-decisions.md) §4).
- [ ] Every record has external id, name, type, mime type, `weburl` that opens the item in the source, source timestamps in epoch milliseconds, and `version=0`.
- [ ] `external_record_id` is immutable: renaming or moving an item does not create a second record. Ids that collide across object types are namespaced. A message segment id does not change when a message is added. (Behaviour test.) Do not copy Slack's burst id, which is an MD5 of message timestamps.
- [ ] `external_revision_id` changes exactly when content changes: a title-only edit is not re-indexed; a content edit is. A wall-clock timestamp on every read is not that. (Behaviour test.) Do not copy Slack's burst revision, which is `get_epoch_timestamp_in_ms()`.
- [ ] Containers are record groups with the right type and their real nesting.
- [ ] Sync points: one per kind, keyed per container by the container's immutable id.
- [ ] Composed content (tickets, threads, CRM objects) streams as `MimeTypes.BLOCKS`; each comment carries its own link.
- [ ] Content that arrives later (a comment, an attachment, a transcript) changes the parent's revision or is its own record, so the next sync indexes it.
- [ ] Links between records, and people on a record (assignee, reporter, creator, mentions), are emitted through `related_external_records` and the ticket/message people fields ([02-low-level-design.md](./docs/connectors/02-low-level-design.md)).

## 4. Streaming and preview

- [ ] `stream_record` works for every record type written, streams files in chunks, and maps source failures through `stream_errors`, converting the client's own `HTTPException`s with `map_source_status` so a source 401 reaches the user as 409, never 401.
- [ ] `get_signed_url` returns a real, short-lived source URL or `None`. A URL that expires before indexing is resolved again at fetch time, not stored on the record at sync.

## 5. Hierarchy

- [ ] The browse tree in PipesHub matches the source: every record and group has its parent id and parent type. Attachments sit under their owner.
- [ ] With a sync filter narrowed to a sub-folder or project, parents are still written, or swept in later, so nothing is orphaned.
- [ ] Placeholder parents are filled by the connector's own sweep (`get_placeholder_records`; Jira Cloud and Confluence are references), a stub is kept when the fetch fails, and the sweep does not pull in items from containers outside the filter.
- [ ] A moved or renamed item shows in its new place and is not re-indexed.

## 6. Permissions

- [ ] `with_permission_model` is `RECORD_LEVEL` unless every record is readable by everyone who can use the connector.
- [ ] Users (with email), groups and roles are synced across all pages, and written before the containers and records that grant to them.
- [ ] Users whose email the source hides keep their access through a pseudo-group, migrated with `migrate_group_to_user_by_external_id` once the email is visible (Confluence Cloud is the reference).
- [ ] Nested source groups are flattened into direct members (transitive membership); a group with no direct members is still written, so grants to it land.
- [ ] A deactivated or suspended source user is left out of group members and record grants, so they lose access. The platform does not act on `AppUser.is_active`.
- [ ] Source guests and external accounts are `AppUser(is_guest=True)`; `ensure_team_app_edge` is called only in team scope.
- [ ] Items whose ACL replaces the container's have `inherit_permissions=False` and carry only their own grants; items whose sharing only adds people keep `inherit_permissions=True`; `access_rule` matches the source (`RESTRICTED` where an item narrows its container, `STRICT` where visibility needs every ancestor).
- [ ] Children of a restricted item (attachments, comments, sub-pages) inherit from that item, not from the container: no `inherit_permissions_from_group` on them.
- [ ] No `DOMAIN`, `ANYONE` or `ANYONE_WITH_LINK` share becomes an `ORG` grant; `ORG` grants only for shares the source makes org-wide. `RECORD_GROUP_LEVEL` only on groups where every nested group and record has the group's audience.
- [ ] A sharing-only change updates access without re-indexing (`on_updated_record_permissions` with the stored record, or `rewrite_permissions`). Revoked access and group membership changes take effect after the next sync. (Behaviour tests: share then unshare; add then remove a member.)
- [ ] An unreadable ACL keeps stored access and opens nothing new: no unreadable group written with `None` plus `inherit_permissions=True` and `OPEN`; no existing record re-written through `on_new_records` in a full sync with a guessed grant list.
- [ ] A failed member listing keeps the group's stored members: the group is not re-sent with a partial list (`note_kept_user_groups`, `has_stored_permissions("user_group", ...)`).
- [ ] Personal scope: only the creator reaches the data (`_load_creator_email()` then `ensure_creator_user_app_relation()`).

## 7. Sync

- [ ] An incremental sync fetches only changes since the checkpoint. A forced full sync re-links everything (parents, groups, access) and search works after it.
- [ ] The incremental cursor or start time is taken **before** a full listing begins, so changes made during the first sync arrive on the next one.
- [ ] A checkpoint is saved only after its records are written, never past a failure (`FailedItems`); a sync stopped midway and restarted misses nothing. Secrets in sync point data are encrypted (`update_sync_point(..., encrypt_fields=[...])`).
- [ ] An expired or invalid delta/history cursor re-lists that container in full and notifies the user, instead of skipping changes.
- [ ] Date filters and checkpoints use UTC epoch milliseconds and are correct for a source in another time zone.
- [ ] Every listing is paginated to the end: users, groups, members, containers, items, comments, attachments and permissions. (Behaviour test with more than one page of each.)
- [ ] One bad item or container is logged, counted in `FailedItems` or reported as `FullSyncCoverage(complete=False)`, and skipped; the sync continues. A date-window filter is reported as `window_start_ms`, not `complete=False`. A failure that makes the whole run meaningless (auth, missing scope) fails the sync.
- [ ] `SyncStrategy.WEBHOOK` is not declared: the platform does not call `handle_webhook_notification`.
- [ ] A scheduled run is not described as a full sync. Deletions that depend on full-sync cleanup name the event that sets `fullSync` or `pendingFullSync`.
- [ ] Adding the connector to `ROOT_SCOPED_CONNECTOR_TYPES`, or a new `RecordType` / `RecordGroupType` / `SyncFilterKey`, was asked for as a framework change. Slack's `RECORD_GROUP_LEVEL` on a channel was not copied onto a container that has a private child.

## 8. Deletions in the source

- [ ] A deleted, trashed or archived item disappears after the next sync, from the source's own signal where it has one, otherwise from the full-sync cleanup.
- [ ] A deleted container takes everything under it (`on_record_group_deleted`).
- [ ] An item moved out of the synced area, or excluded by a narrowed filter, disappears.
- [ ] Records are deleted only on a confirmed signal from the source. A timeout, 5xx, 429 or 403 never deletes anything.

## 9. Connector lifecycle

- [ ] `cleanup()` closes every client and session and releases the thread lease. The connector keeps no state outside the graph, its config and its sync points (nothing registered at the source, such as a webhook or a watch channel, that it cannot tear down).
- [ ] Deleting the connector and adding the same source again gives a clean full sync with no duplicates. (Integration test.)
- [ ] Disabling stops the sync and keeps the data; re-enabling resumes from the saved checkpoints.

## 10. Filters

- [ ] Every declared filter works: sync filters at fetch time, indexing filters mark records `AUTO_INDEX_OFF`.
- [ ] Narrowing a sync filter makes out-of-scope records disappear after the full sync it triggers (saving changed sync filters queues one; its cleanup unlinks what it did not write); widening adds the new ones. If the connector deletes out-of-scope records itself, it uses the `folder_scope` helpers.
- [ ] Toggling an indexing filter does not re-index unchanged records.
- [ ] `get_filter_options` pages through the source and supports `search`.

## 11. Notifications and reindex

- [ ] Auth errors, sync failures and skipped containers are reported to the user with `self.notify(...)` (or `notify_and_wait` where "notified once" is persisted), naming the instance by `display_name`.
- [ ] `reindex_records()` re-queues the records it is given, including unchanged ones.

## 12. API error logging

- [ ] Every log of a failed source API call includes the vendor's own error message or reason, not just "failed" or the exception type.
- [ ] It includes the HTTP status and the vendor's error code where the API has one, and names the operation and the object (by source id).
- [ ] No log line, at any level, contains an access token, refresh token, API key, client secret, password, PAT, signed URL, an `Authorization` / `Cookie` header, or a whole header map.
- [ ] No whole response bodies are logged; any raw text kept is bounded and passed through `redact_urls_in_text`.
- [ ] Unexpected exceptions are logged with `exc_info=True` or `logger.exception`.
- [ ] A failed read of users, groups, ACLs or items is never written as an empty result. No `except` that passes, or returns `[]` / `None` as if the source had nothing.
- [ ] The two searches in [07-errors-and-logging.md](./docs/connectors/07-errors-and-logging.md) were run on the connector and its client, and every hit was read.
- [ ] Behaviour tests for 401, 403, 404, 429 and 5xx assert the logged reason and that no secret is in the log.

## 13. Runtime and security

- [ ] No synchronous network or file call runs on the event loop; a sync SDK runs through `_thread_lease`.
- [ ] Rate limits come from `with_resilience_config`; `Retry-After` is honoured; parallel calls are bounded.
- [ ] Listing and writing go page by page and batch by batch; memory stays bounded on a large tenant; files are streamed.
- [ ] No graph, vector, broker or config-store client is imported.
- [ ] A user-supplied base URL (self-hosted sources) is validated with `validate_public_http_url` (`app/utils/url_fetcher.py`) before use, and requires `https` unless the human agreed otherwise.

## 14. Tests and CI

- [ ] Behaviour tests under `backend/python/tests/unit/connectors/sources/<name>/` against a fake source with network blocked, covering the cases in [08-testing.md](./docs/connectors/08-testing.md), including corner cases: an empty container, a very large file, unicode names, a deleted user, a restricted item, an archived container.
- [ ] `pytest tests/unit` passes from `backend/python`, and coverage stays at or above the gate. Ruff reports no new findings on the changed files.
- [ ] An integration suite in `integration-tests/connectors/<registry key>/`, with its marker, workflow shard, secrets and env-template entries, plugged into the scenario matrix, passing on both Neo4j and ArangoDB, including revoked access and connector deletion. If there is no test account, the human agreed to the `scripts/connector_coverage_baseline.json` entry, and the PR says so. Without that agreement, the connector does not ship: a skip-only suite fails the nightly run. Behaviour tests cover a cross-type id collision, an association-only change, a permission-only change, a list cap, a 200 with an empty body, an expiring URL, and a role-set deny, where the source has that case.
