# Building a connector, step by step

Each phase ends with a check. Do not start the next phase until the check passes. Paths are relative to the repo root; `<name>` is the package name (`acme`), `<Name>` the display name (`Acme`), `<NAME>` the enum member (`ACME`).

Read `01-high-level-design.md` and `02-low-level-design.md` first. Pick a reference connector of the same shape and keep it open:

| Source shape | Reference |
| --- | --- |
| File storage (drives, folders, files, per-file sharing) | Box (`box/`), Dropbox (`dropbox/`) |
| Wiki or docs (spaces, nested pages) | Confluence Cloud (`atlassian/confluence_cloud/`) |
| Ticketing (projects or teams, issues, comments, attachments) | Linear (`linear/`) |
| Messaging (channels, threads, files) | Slack (`slack/`) |
| Self-hosted, API token, small API | BookStack (`bookstack/`), Zammad (`zammad/`) |

All under `backend/python/app/connectors/sources/`.

## Phase 0: intake

Do `04-intake-questions.md`: read the vendor API documentation, then ask the human the open questions in one message. Make the decisions in `03-decisions.md` and write them, with the intake answers, into the PR description.

**Check:** every decision in `03-decisions.md` has an answer, and the human has confirmed the ones marked "ask".

## Phase 1: API client

1. `backend/python/app/sources/client/<name>/<name>.py`: the auth-aware client with `build_from_services(logger, config_service, connector_instance_id)`, one branch per auth type you support.
2. `backend/python/app/sources/external/<name>/<name>.py`: the endpoint wrapper. Generate it if the vendor publishes an OpenAPI spec (`backend/python/code-generator/`), then trim it to what you use.
3. Wire the resilience policy into the HTTP client. Extract vendor error code, message and status into one helper (`07-errors-and-logging.md`).
4. No synchronous network call on the event loop. If the vendor only has a sync SDK, plan to run it through the thread lease.

**Check:** unit tests for the client against `httpx.MockTransport` (or the SDK's transport hook): one success per endpoint you use, pagination to the last page, 401, 429 with `Retry-After`, 5xx. No real network.

## Phase 2: app, decorator and registration

1. Add `<NAME>` to `Connectors` and `AppGroups` in `backend/python/app/config/constants/arangodb.py`.
2. `backend/python/app/connectors/sources/<name>/common/apps.py`: `<Name>App(App)`.
3. `backend/python/app/connectors/sources/<name>/connector.py`: the `@ConnectorBuilder("<Name>")` decorator and a class implementing every abstract method of `BaseConnector`. Bodies may be stubs for now, except `create_connector`, `init`, `test_connection_and_access` and `cleanup`.
4. Register it in `ConnectorFactory._connector_registry` (`backend/python/app/connectors/core/factory/connector_factory.py`) under `"<name lower-cased, no spaces>"`, with the import at the top of the file.
5. Icon: `frontend/public/icons/connectors/<name lower-cased, no spaces>.svg`, and an entry in `frontend/app/components/ui/ConnectorIcon.tsx` (the `ConnectorType` union, `CONNECTOR_ICONS` and `FUZZY_MATCH_RULES`) so chat citations show it.
6. Add the `Connectors` value to `ConnectorNameEnum` in `backend/nodejs/apps/src/modules/api-docs/pipeshub-openapi.yaml`. A mismatch with the Python enum is blocking in review.

**Check:**

```bash
cd backend/python
pytest tests/unit/connectors/test_connector_contract.py tests/unit/connectors/test_graph_call_contract.py -x
```

## Phase 3: init and connection

1. `init()`: read config, build the client, create the `SyncPoint`s, acquire the thread lease if needed. Personal scope: `await self._load_creator_email()`. Raise `ConnectorInitError` with a user-facing reason for bad credentials or missing scopes.
2. `test_connection_and_access()`: one cheap authenticated call.
3. Optional `check_connection()` for the setup form.
4. `cleanup()`: close the client, release the lease.

**Check:** behaviour tests: valid credentials initialise; a rejected token raises `ConnectorInitError` with the vendor's reason in the log and no secret in it.

## Phase 4: users and groups

1. List source users, map to `AppUser` (with email), write with `on_new_app_users`.
2. List groups and their members (transitively, so nested groups are flattened), write with `on_new_user_groups`. If the API cannot list members, stop and tell the human what that does to access (`06-permissions.md`).
3. Leave deactivated and suspended users out of member lists. For users whose email is hidden, use pseudo-groups (`06-permissions.md`, "Principals the source makes hard").
4. Personal scope: call `ensure_creator_user_app_relation()` instead (it needs the creator email loaded in `init()`).

**Check:** behaviour test: the fake's users and groups come out with the right emails and members.

## Phase 5: containers and hierarchy

1. List containers (spaces, drives, projects), map to `RecordGroup`, with `parent_external_group_id` for nested ones and the access fields from `06-permissions.md`.
2. Write with `on_new_record_groups`, parents before children, each with its permissions (or `None` if they could not be read).

**Check:** behaviour test: the group tree matches the fake, and each group's grants and inheritance match its source sharing.

## Phase 6: records

1. For each container, list items page by page. Map each to the right `Record` subclass with a stable `external_record_id`, an `external_revision_id` that changes with content, the parent and group ids, `weburl`, timestamps and mime type.
2. Build its permissions and access fields (`06-permissions.md`).
3. Write in batches with `on_new_records`. Apply indexing filters by setting `indexing_status`.
4. Comments, attachments and other children are their own records with a parent link, unless the decision in `03-decisions.md` was to fold them into the parent's content.

**Check:** behaviour test of a first sync: every item becomes the expected record, under the expected parent, with the expected grants. Run the permission assertions in `06-permissions.md`.

## Phase 7: content

1. `get_signed_url()` if the source gives temporary download URLs.
2. `stream_record()` for every record type you write: bytes for files, `MimeTypes.BLOCKS` JSON for structured items. Map failures with `stream_errors`.

**Check:** behaviour test per record type; 404 at the source maps to 404, 401 to 409, 429 to 429 with `Retry-After`.

## Phase 8: incremental sync, deletions and moves

1. Take the next cursor before the full listing starts. Save a checkpoint per container after its items are written, with `FailedItems` holding it before any failure.
2. On the next run, list only changes since the checkpoint. If the source rejects the cursor as expired, re-list that container in full and `notify` the user.
3. Placeholder sweep: fill the parents the main pass referenced but did not write (`02-low-level-design.md`, "Placeholders and filters").
4. Apply deletions and moves the source reports, on confirmed signals only: `delete_record_by_external_id` for a deletion; re-send a moved record through `on_new_records` with its new parent, or `on_records_moved` if its id changed. For those it does not, rely on the full-sync cleanup, and make `full_sync_coverage()` honest about anything a run skipped.
5. Permission-only changes: `on_updated_record_permissions`, with the stored record (or its stored id).

**Check:** behaviour tests: change, rename, move, delete and unshare an item in the fake between two runs; each shows up correctly after the second run; an unchanged item is not re-sent for indexing.

## Phase 9: filters, reindex and the rest

1. Implement every filter you declared: sync filters in the API query, indexing filters per record. `get_filter_options()` for each `DYNAMIC` filter.
2. `reindex_records()`.
3. `run_incremental_sync()` and `handle_webhook_notification()`: delegate or no-op, as `02-low-level-design.md` says.

**Check:** behaviour test per filter.

## Phase 10: integration tests and finish

1. Integration suite, marker, workflow shard and env templates (`08-testing.md`). Without a test account, stop and tell the human the connector would need a coverage-baseline exception.
2. Run the error and secret greps in `07-errors-and-logging.md` and fix every hit.
3. Go through `CONNECTOR_CHECKLIST.md` item by item.

**Check:**

```bash
cd backend/python
pytest tests/unit/connectors -x
pytest tests/unit
ruff check app/connectors/sources/<name> app/sources/client/<name> app/sources/external/<name>
```
