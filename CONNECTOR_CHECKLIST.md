# Connector checklist

The definition of done for a new connector, and the list a reviewer checks it against. Each item names how to verify it. How to build each part is in [CONNECTOR.md](./CONNECTOR.md) and the documents it links.

Copy this list into the PR description and tick each item, or say why it does not apply.

## Intake and design

- [ ] The PR description has a "Connector intake" section: editions, scope, objects synced, auth types, volume, filters, and the test account used. ([04-intake-questions.md](./docs/connectors/04-intake-questions.md))
- [ ] Every decision in [03-decisions.md](./docs/connectors/03-decisions.md) is recorded with its reason, and the ones marked "ask" were confirmed by a human.
- [ ] The permission mapping, object type by object type, is in the PR description, with any sharing the model cannot express and which way it errs. ([06-permissions.md](./docs/connectors/06-permissions.md))

## Registration

- [ ] `Connectors` and `AppGroups` have the new member (`backend/python/app/config/constants/arangodb.py`).
- [ ] `ConnectorNameEnum` in `backend/nodejs/apps/src/modules/api-docs/pipeshub-openapi.yaml` has the new `Connectors` value.
- [ ] The class is in `ConnectorFactory._connector_registry` under its lower-cased, space-free name (or `_beta_connector_definitions`).
- [ ] The icon is at `frontend/public/icons/connectors/<name>.svg` and mapped in `frontend/app/components/ui/ConnectorIcon.tsx`.
- [ ] `tests/unit/connectors/test_connector_contract.py` and `tests/unit/connectors/test_graph_call_contract.py` pass, with no new entry in their allow-lists.

## Correctness

- [ ] `external_record_id` is the source's immutable id; `external_revision_id` changes exactly when content does.
- [ ] Users and groups are written before the records and record groups that grant to them.
- [ ] A full sync writes every node it can see; `full_sync_coverage()` reports any part of the source a run skipped. (Behaviour test.)
- [ ] Incremental sync picks up changes, renames, moves and deletions the source reports; a failed item holds the checkpoint before it (`FailedItems`). (Behaviour tests.)
- [ ] A failed read of users, groups, ACLs or items is never written as an empty result. No `except` that passes, or returns `[]` / `None` as if the source had nothing.
- [ ] Every declared filter changes what is fetched or indexed; every `DYNAMIC` filter has options in `get_filter_options`.
- [ ] `stream_record` works for every record type written, and maps source failures through `stream_errors`, converting the client's own `HTTPException`s with `map_source_status` so a source 401 reaches the user as 409.

## Permissions

- [ ] `with_permission_model` is `RECORD_LEVEL` unless every record is readable by everyone who can use the connector.
- [ ] Items with their own ACL have `inherit_permissions=False` and carry only their own grants.
- [ ] `access_rule` matches the source: `RESTRICTED` where an item narrows its container, `STRICT` where visibility needs every ancestor.
- [ ] No `DOMAIN`, `ANYONE` or `ANYONE_WITH_LINK` share is turned into an `ORG` grant. `ORG` grants only for shares the source makes org-wide.
- [ ] An unreadable ACL keeps stored access and does not open a new node: no unreadable group written with `None` plus `inherit_permissions=True` and `OPEN`; no existing record re-written through `on_new_records` in a full sync with a guessed grant list.
- [ ] `ensure_team_app_edge` is called only in team scope. Source guests and external accounts are `AppUser(is_guest=True)`.
- [ ] `RECORD_GROUP_LEVEL` only on groups where every nested group and record has the group's audience.
- [ ] Permission changes after the first sync replace the old grants (`on_updated_record_permissions` or `rewrite_permissions`). (Behaviour test: share, then unshare.)

## API error logging

- [ ] Every log of a failed source API call includes the vendor's own error message or reason, not just "failed" or the exception type.
- [ ] It includes the HTTP status and the vendor's error code where the API has one, and names the operation and the object (by source id).
- [ ] No log line, at any level, contains an access token, refresh token, API key, client secret, password, PAT, signed URL, an `Authorization` / `Cookie` header, or a whole header map.
- [ ] No whole response bodies are logged; any raw text kept is bounded and passed through `redact_urls_in_text`.
- [ ] Unexpected exceptions are logged with `exc_info=True` or `logger.exception`.
- [ ] The two greps in [07-errors-and-logging.md](./docs/connectors/07-errors-and-logging.md) were run on the connector and its client, and every hit was read.
- [ ] Behaviour tests for 401, 403, 404, 429 and 5xx assert the logged reason and that no secret is in the log.

## Runtime

- [ ] No synchronous network or file call runs on the event loop; a sync SDK runs through `_thread_lease`.
- [ ] Rate limits come from `with_resilience_config`; `Retry-After` is honoured; concurrency is bounded.
- [ ] Listing and writing go page by page and batch by batch; nothing holds the whole source in memory; files are streamed.
- [ ] The token is re-read from config before each batch of calls, not cached from `init()`.
- [ ] No graph, vector, broker or config-store client is imported; config goes through `ConfigurationService`.

## Tests and CI

- [ ] Behaviour tests under `backend/python/tests/unit/connectors/sources/<name>/` against a fake source with network blocked, covering the cases in [08-testing.md](./docs/connectors/08-testing.md).
- [ ] `pytest tests/unit` passes from `backend/python`, and coverage stays at or above the gate.
- [ ] Ruff reports no new findings on the changed files.
- [ ] An integration suite in `integration-tests/connectors/<registry key>/` with its marker, workflow shard, secrets and env-template entries, plugged into the scenario matrix. If there is no test account, the human agreed to the `scripts/connector_coverage_baseline.json` entry, and the PR says so.
