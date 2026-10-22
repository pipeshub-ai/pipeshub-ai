# Connector design decisions

Make each decision below before writing the connector, and record the choice and the reason in the PR description. Each one says what to decide from the documentation yourself, and when to ask the human (see `04-intake-questions.md` for how to ask).

## 1. One connector or several

A source with materially different editions or scopes is usually several connector classes sharing code, not one class with branches.

- **Editions.** Cloud and self-hosted with different APIs or auth are separate connectors (`Confluence` and `Confluence Data Center`, `Jira` and `Jira Data Center`). Share the mapping code in a common package (`connectors/sources/atlassian/core/`).
- **Scope.** A team connector (an admin syncs the whole organisation) and a personal connector (a user syncs their own account) are separate classes with separate registry keys (`jira` and `jiracloudpersonal`, `dropbox` and `dropboxpersonal`).

Ask the human which editions and scopes are wanted. Build one first.

## 2. Scope: team or personal

| Choose | When |
| --- | --- |
| `ConnectorScope.TEAM` | An admin can grant org-wide read access (admin OAuth consent, a service account, an admin API token), and you can list users, groups and per-item sharing. |
| `ConnectorScope.PERSONAL` | The API only acts as the signed-in user, or the content is private per user (mail, personal drives). |

Decide from the documentation; ask only if both are possible and the use case does not settle it.

## 3. Auth

- Prefer OAuth 2.0 authorization code with refresh tokens when the source supports it. Declare the scopes per use (`OAuthScopeConfig(personal_sync, team_sync, agent)`) and request the minimum that reads what you sync.
- Add an API token or basic auth option when self-hosted users commonly cannot register an OAuth app.
- Never use OAuth `client_credentials` for anything that must act as a user.
- If OAuth tokens do not come with a refresh token, say so in the PR: the token refresh service only refreshes connectors that have one, so such a connector needs the user to reconnect when it expires.
- A static API token or bot token never refreshes. Do not retry a 401 as if a refresh would help. Say so in the PR, and see `02-low-level-design.md` for what the OAuth service already does (PKCE, Basic-auth hosts, refresh posted to `token_url`). Do not rebuild that in the client.

## 4. What becomes a record group, what becomes a record

| Source object | Map to |
| --- | --- |
| A top-level container with its own membership or sharing (space, project, team, drive, channel, mailbox) | `RecordGroup`, top level |
| A container inside a container that has its own sharing (sub-space, shared drive folder root) | `RecordGroup` with `parent_external_group_id` |
| A folder that only organises items and has the same access as its parent | `Record` with `MimeTypes.FOLDER` (`FileRecord`, `is_file=False`) under the group |
| A searchable item (page, file, issue, message thread, ticket, article) | `Record` subclass that matches: `FileRecord`, `WebpageRecord`, `TicketRecord`, `MailRecord`, `ProjectRecord`, `PullRequestRecord`, ... |
| An attachment of an item | its own `FileRecord`, with the item as parent |
| Comments | follow the reference connector of the same shape: either part of the parent's streamed content, or `CommentRecord`s under it when they are searched or cited on their own |
| A row of a table, list or sheet | one record streamed as `MimeTypes.BLOCKS`, or one `SQLTableRecord` when the table itself is the searchable item (Postgres, MariaDB). Ask which, when both are plausible. |
| A container with no ACL of its own, but its own checkpoint, or whose deletion must remove everything under it | a nested `RecordGroup`, not a folder `Record`. `delete_record_by_external_id` deletes attachments and re-points other children at what the record hung under, so a table modeled as a folder leaves its rows behind when the table is deleted. `on_record_group_deleted` removes the subtree. |
| A link to another item that is not its parent (a CRM association, a linked row) | `related_external_records`. Not `parent_external_record_id`. The link does not grant access. Do not pick one of several associations as the parent so the item inherits that parent's audience. |
| Messages | one record per conversation segment (a channel or thread for a time window), with an id that stays put when a message is added. Not one record per message at chat volume. |

Adding a `RecordType` or `RecordGroupType` is a framework change. Ask first (`CONNECTOR.md`). Do not add the value to only one of the places that already mirror it, and do not reuse a vendor-specific value (`DRIVE`, `SALESFORCE_ORG`) because the name is unused:

- `RecordType` in `app/models/entities.py`, which connectors set on records.
- `RecordTypes` in `app/config/constants/arangodb.py`. Indexing (`app/services/messaging/kafka/handlers/record.py`) and the Neo4j and Arango providers switch on this one, not on `RecordType`.
- `RecordTypeEnum` in `backend/nodejs/apps/src/modules/api-docs/pipeshub-openapi.yaml`, which the spec says mirrors `RecordType`.

The two Python enums have already drifted. `RecordTypes` has `ATTACHMENT` and `WEBPAGE_COMMENT`, which `RecordType` does not. `RecordType` has `INLINE_COMMENT`, `CONFLUENCE_PAGE`, `CONFLUENCE_BLOGPOST`, `SHAREPOINT_LIST_ITEM`, `SHAREPOINT_DOCUMENT_LIBRARY`, `PROJECT`, `SQL_TABLE`, `SQL_VIEW` and `OTHERS`, which `RecordTypes` does not. `RecordGroupType` lives only in `entities.py` today; a new group type is still a framework change because the graph stores the string.

`external_record_id` must be namespaced when the source's id is unique only inside an object type (`0-1:123` versus `0-2:123`).

## 5. Identity, revisions and links

- `external_record_id` is the source's immutable id. If the source has only paths, ask: a rename would delete and re-create the record, losing its history and re-indexing it.
- `external_revision_id` changes exactly when the content changes: version number, etag, content hash, or `updated_at` as a last resort (it also changes on metadata-only edits, which re-indexes needlessly). Hash the canonical content you index, not the raw payload. Leave out volatile fields (signed URLs, thumbnails, view counts). Include computed fields when they are part of what is indexed, and a comment signature when comments are folded into the parent. A listing timestamp that also moves on rename will re-index the item; say so in the PR if that is the only signal you have.
- `weburl` opens the item in the source's UI, not its API.

## 6. Permissions

Map the source's sharing model with `06-permissions.md`, object type by object type, and write the mapping into the PR.

- `RECORD_LEVEL` unless every record in the connector is readable by everyone who can use the connector.
- Ask the human when the source has sharing the model cannot express exactly. Name the gap and which way it errs. Never choose the over-sharing option yourself.
- If the API cannot list group members, or hides user emails without an admin scope, tell the human what content will be hidden as a result, and which scope would fix it.

## 7. Incremental sync

| The source offers | Use |
| --- | --- |
| A changes or delta endpoint with a cursor (Drive changes, Graph delta, Dropbox list_folder/continue) | Store the cursor per container as the checkpoint. Deletions and moves usually come with it. |
| A `modified since` filter on listing | Store the latest modified time per container, held back by `FailedItems`. Detect deletions separately. |
| An audit or events log | Use it for deletions and permission changes the listing does not show. |
| Nothing | Full listing every run; rely on the full-sync cleanup for deletions. Tell the human the cost at their volume. |
| Ids that increase with time (a Discord snowflake) and no modified-since | Take the cursor before the listing starts, the same way as a delta token, and store the last id fully written on the sync point. Do not set it to "now" after the scan. `FailedItems` only holds a modified-time cutoff (`add(cutoff_ms)`); it cannot store a snowflake. On a failed page, leave the saved id where it was. |

Every run writes through `on_new_records`; unchanged revisions are not re-indexed, so re-sending is cheap in a full sync and required there (see `01-high-level-design.md`).

A scheduled run does not set `fullSync`. Cleanup of nodes the run did not write happens only on a full sync (`01-high-level-design.md`). If the source cannot report deletions, say how long a deletion waits, and do not describe the schedule as if it were that full sync. An id cursor and a `modified since` filter have the same rule: take the bound before the listing, and do not advance it past a failure. Linear's `_update_team_sync_checkpoint` stores the current time when no timestamp is passed, and Linear does not use `FailedItems`; do not copy that.

## 8. Deletions and moves

- Prefer the source's own signal (a delete event, a trash listing, a `deleted` flag in the delta).
- Delete only on a confirmed signal. A 404 deletes one item only when that item was requested directly. A 404 on a list, or on a parent, is not a deletion of what you did not ask for. A timeout, 5xx, 429 or 403 never deletes records.
- A deleted container takes everything under it: `on_record_group_deleted`.
- Otherwise the full-sync cleanup handles it, as long as `full_sync_coverage()` is honest. An incremental run alone will not notice a deletion; say how long a deletion can take to show up (until the next full sync).
- A move is not a delete and re-create. With a stable id, re-send the record through `on_new_records` with its new parent; use `on_records_moved` only when the source's id changes on a move.
- A branch, and a many-to-one merge, are not moves. Upsert the survivor and delete ids the source confirms were absorbed. Do not call `on_records_moved` for a merge.
- When the source has no delete feed, an id-diff is allowed only after a listing of that container that finished with no error: page the stored records in that group with `get_records_in_record_group`, and delete stored ids that were absent. `get_records_by_record_type` does not page. It returns every record of that type in one list, and each row is built on the connector's one event loop. Use it only when that set is bounded, as SQL tables are. A type that is not in one group can be the whole connector; do not load it this way to id-diff. A partial page, a 429, a 5xx, or a 403 is not that listing. A missing base is three different signals: the base was deleted, the grant was lost, or the token was narrowed. Do not treat them as one. Ask, and do not delete on that ambiguity.
- A 200 with an empty body is not a deletion and not proof the container is empty. Hold the checkpoint and do not write that result as "there are none". A 200 whose items are present but stripped is the same failure: Discord without the Message Content intent still returns the message objects, with `content`, `embeds`, `attachments`, and `components` blank. Indexing those blanks and advancing the cursor writes a history the token could not read (`07-errors-and-logging.md`).

## 9. Content delivery

- Files: `get_signed_url()` if the source gives temporary download URLs, and `stream_record()` streaming from it; otherwise stream the download yourself. Never buffer a whole file.
- Structured items (issues, tickets, pages from a JSON API): stream `MimeTypes.BLOCKS` JSON. Rich text the source gives as HTML or Markdown can go as that mime type.
- Content the source cannot export (a native format with no export) is `not_downloadable` (HTTP 422 from `stream_record`). Indexing stores that 422 as `FAILED`. Do not set `indexing_status` to `ProgressStatus.FILE_TYPE_NOT_SUPPORTED`: the record is still published, and the 422 overwrites that status (`02-low-level-design.md`).
- Do not store `signed_url` when it expires before indexing. Fetch a fresh URL when the bytes are requested (`02-low-level-design.md`).

## 10. Rate limits and concurrency

- Take the sustained rate limit from the documentation into `with_resilience_config(rate_limit=...)`. That is one limiter per connector in this process. `Retry-After` in seconds is capped at `max_delay`. A per-route bucket, a per-base limit, or a daily quota does not fit; ask before a second limiter. A daily 429 stops the run and keeps the checkpoint (`02-low-level-design.md`).
- Bound concurrency: a semaphore around per-container work, or the thread lease's `max_concurrency` for a sync SDK. `gather_with_concurrency` in `app/utils/concurrency.py` is the existing helper.
- A synchronous SDK never runs on the event loop.
- Some vendors count 401 and 403 toward an invalid-request ban. Do not probe access by calling until you are rejected.

## 11. Filters

Offer the filters admins need to limit volume and scope: containers (`DYNAMIC` list), date ranges, file types, and per-object-type indexing toggles. Use `CommonFields` and the standard keys. Ask the human only about filters beyond these.

## 12. Real-time updates

Scheduled sync is the supported path. Webhook handlers have no caller in the platform today, and declaring `SyncStrategy.WEBHOOK` (or `with_webhook_config`) changes nothing: the scheduler acts only on `SCHEDULED`. Do not declare it. A webhook-based design needs the human's agreement, and platform work (a route, signature verification, removing what it registered at the source), before you build it.

## 13. Framework changes

If the connector needs a change in shared code (`app/connectors/core/`, `app/models/`, the graph providers, the processor), stop and ask. Those changes affect every connector and need their own review and tests. That includes a new `RecordType` or `RecordGroupType`, a new `SyncFilterKey`, and adding the connector to `ROOT_SCOPED_CONNECTOR_TYPES` (`app/services/graph_db/common/utils.py`). OAuth PKCE, Basic-auth token hosts, and refresh-token rotation are already there (`02-low-level-design.md`); extending the hostname list is still shared code.
