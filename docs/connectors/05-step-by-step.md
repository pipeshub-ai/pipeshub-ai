# Building a connector, step by step

Each phase ends with a check. Do not start the next phase until the check passes. Paths are relative to the repo root; `<name>` is the package name (`acme`), `<Name>` the display name (`Acme`), `<NAME>` the enum member (`ACME`).

Read `01-high-level-design.md` and `02-low-level-design.md` first. Pick a reference connector of the same shape and keep it open:

| Source shape | Reference |
| --- | --- |
| File storage (drives, folders, files, per-file sharing) | Box (`box/`), Dropbox (`dropbox/`) |
| Wiki or docs (spaces, nested pages) | Confluence Cloud (`atlassian/confluence_cloud/`) |
| Ticketing (projects or teams, issues, comments, attachments) | Linear (`linear/`) |
| Messaging (channels, threads, files) | Slack (`slack/`). Read the deviations below before copying it. |
| Structured rows (tables, sheets) | Postgres (`postgres/`), MariaDB (`mariadb/`) for a table as one record. A row-per-record source still follows `03-decisions.md` §4. |
| CRM objects linked to each other | No reference that stays inside the processor. Salesforce writes graph edges itself; do not copy that. Map associations with `related_external_records` (`03-decisions.md` §4). |
| Self-hosted, API token, small API | BookStack (`bookstack/`), Zammad (`zammad/`) |

Two references diverge from this guide. Do not copy the divergence.

- Slack sets `permission_model=RECORD_GROUP_LEVEL` on channels (`slack/team/connector.py`). That opens every nested thread to the channel's audience. Copying it onto a guild over-shares private threads. Slack also builds a burst id from an MD5 of message timestamps, sets `version=1`, and sets `external_revision_id` to the wall clock (`get_epoch_timestamp_in_ms()`), so a re-read re-indexes even when the text did not change. Those miss checklist §3. A new messaging connector uses a stable segment id, `version=0`, and a revision that moves with the content (`CONNECTOR_CHECKLIST.md`).
- Linear saves its checkpoint after the scan: `_update_team_sync_checkpoint` stores the current time when no timestamp is passed, and Linear does not use `FailedItems`. Take the cursor before the listing. A time cutoff is held with `FailedItems`; an id cursor is a field on the sync point (`02-low-level-design.md`).

All under `backend/python/app/connectors/sources/`.

## Phase 0: intake

Do `04-intake-questions.md`: read the vendor API documentation, then ask the human the open questions in one message. Make the decisions in `03-decisions.md` and write them, with the intake answers, into the PR description.

**Check:** every decision in `03-decisions.md` has an answer, and the human has confirmed the ones marked "ask".

## Phase 1: API client

Search first. A client under `app/sources/client/<name>/`, a wrapper under `app/sources/external/<name>/`, an `app/agents/actions/<name>/` caller, a beta stub, or an icon may already exist. Reuse them. Airtable already has a client, an external wrapper, an agent action, a beta stub, and an icon (`frontend/app/components/ui/ConnectorIcon.tsx`). The class in `app/connectors/core/registry/connector.py` is the stub (`connect()` prints a line). It is registered in `ConnectorFactory._beta_connector_definitions`, and listed in `KNOWN_INCOMPLETE` (`tests/unit/connectors/test_connector_contract.py`) and in `PLACEHOLDERS` (`scripts/test_connector_coverage.py`). Converting that placeholder means: delete the stub class and its `_beta_connector_definitions` entry, implement `BaseConnector` and register it in `_connector_registry`, and remove the key from `KNOWN_INCOMPLETE` and `PLACEHOLDERS`. Keep the icon and the client the agent toolset already imports.

1. `backend/python/app/sources/client/<name>/<name>.py`: the auth-aware client with `build_from_services(logger, config_service, connector_instance_id)`, one branch per auth type you support. Use the OAuth service that already exists (`02-low-level-design.md`). Do not exchange the token yourself.
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
2. `test_connection_and_access()`: one cheap authenticated call that checks the scopes or permissions this connector needs. Raise `ConnectorInitError` and name the missing grant. Returning `False` shows only "Connection test failed. Please verify your credentials have proper access."
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

1. For each container, list items page by page. Map each to an existing `Record` subclass with a stable `external_record_id`, an `external_revision_id` that changes with content, the parent and group ids, `weburl`, timestamps and mime type. A new `RecordType` or `RecordGroupType` is a framework change: ask, and update every mirror in `03-decisions.md` §4.
2. Build its permissions and access fields (`06-permissions.md`).
3. Write in batches with `on_new_records`. Apply indexing filters by setting `indexing_status`.
4. Comments, attachments and other children are their own records with a parent link, unless the decision in `03-decisions.md` was to fold them into the parent's content.

**Check:** behaviour test of a first sync: every item becomes the expected record, under the expected parent, with the expected grants. Run the permission assertions in `06-permissions.md`.

## Phase 7: content

1. `get_signed_url()` if the source gives temporary download URLs.
2. `stream_record()` for every record type you write: bytes for files, `MimeTypes.BLOCKS` JSON for structured items. Map failures with `stream_errors`.

**Check:** behaviour test per record type; 404 at the source maps to 404, 401 to 409, 429 to 429 with `Retry-After`.

## Phase 8: incremental sync, deletions and moves

1. Take the next cursor before the full listing starts. Save a checkpoint per container after its items are written. `FailedItems` holds a modified-time cutoff before any failure (`add(cutoff_ms)`, then `checkpoint`). An id cursor is stored on the sync point and is not advanced past a failed page; `FailedItems` cannot hold it.
2. On the next run, list only changes since the checkpoint. If the source rejects the cursor as expired, re-list that container in full and `notify` the user.
3. Placeholder sweep: fill the parents the main pass referenced but did not write (`02-low-level-design.md`, "Placeholders and filters").
4. Apply deletions and moves the source reports, on confirmed signals only: `delete_record_by_external_id` for a deletion of one item you requested; re-send a moved record through `on_new_records` with its new parent, or `on_records_moved` if its id changed. A branch or a merge is not a move. For deletions the source does not report, either wait for a real full sync or id-diff with `get_records_in_record_group` after a complete listing (`03-decisions.md`). Make `full_sync_coverage()` honest about anything a run skipped.
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
