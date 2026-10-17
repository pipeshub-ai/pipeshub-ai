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

## 4. What becomes a record group, what becomes a record

| Source object | Map to |
| --- | --- |
| A top-level container with its own membership or sharing (space, project, team, drive, channel, mailbox) | `RecordGroup`, top level |
| A container inside a container that has its own sharing (sub-space, shared drive folder root) | `RecordGroup` with `parent_external_group_id` |
| A folder that only organises items and has the same access as its parent | `Record` with `MimeTypes.FOLDER` (`FileRecord`, `is_file=False`) under the group |
| A searchable item (page, file, issue, message thread, ticket, article) | `Record` subclass that matches: `FileRecord`, `WebpageRecord`, `TicketRecord`, `MailRecord`, `ProjectRecord`, `PullRequestRecord`, ... |
| An attachment of an item | its own `FileRecord`, with the item as parent |
| Comments | follow the reference connector of the same shape: either part of the parent's streamed content, or `CommentRecord`s under it when they are searched or cited on their own |

Add a new `RecordType` or `RecordGroupType` only when no existing one fits, and say why in the PR.

## 5. Identity, revisions and links

- `external_record_id` is the source's immutable id. If the source has only paths, ask: a rename would delete and re-create the record, losing its history and re-indexing it.
- `external_revision_id` changes exactly when the content changes: version number, etag, content hash, or `updated_at` as a last resort (it also changes on metadata-only edits, which re-indexes needlessly).
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

Every run writes through `on_new_records`; unchanged revisions are not re-indexed, so re-sending is cheap in a full sync and required there (see `01-high-level-design.md`).

## 8. Deletions and moves

- Prefer the source's own signal (a delete event, a trash listing, a `deleted` flag in the delta).
- Delete only on a confirmed signal. A 404 for one item can be a deletion; a timeout, 5xx, 429 or 403 is not, and must never delete records.
- A deleted container takes everything under it: `on_record_group_deleted`.
- Otherwise the full-sync cleanup handles it, as long as `full_sync_coverage()` is honest. An incremental run alone will not notice a deletion; say how long a deletion can take to show up (until the next full sync).
- A move is not a delete and re-create. With a stable id, re-send the record through `on_new_records` with its new parent; use `on_records_moved` only when the source's id changes on a move.

## 9. Content delivery

- Files: `get_signed_url()` if the source gives temporary download URLs, and `stream_record()` streaming from it; otherwise stream the download yourself. Never buffer a whole file.
- Structured items (issues, tickets, pages from a JSON API): stream `MimeTypes.BLOCKS` JSON. Rich text the source gives as HTML or Markdown can go as that mime type.
- Content the source cannot export (a native format with no export) is `not_downloadable`; set its indexing status so it is not retried forever.

## 10. Rate limits and concurrency

- Take the sustained rate limit from the documentation into `with_resilience_config(rate_limit=...)`. Honour `Retry-After`.
- Bound concurrency: a semaphore around per-container work, or the thread lease's `max_concurrency` for a sync SDK.
- A synchronous SDK never runs on the event loop.

## 11. Filters

Offer the filters admins need to limit volume and scope: containers (`DYNAMIC` list), date ranges, file types, and per-object-type indexing toggles. Use `CommonFields` and the standard keys. Ask the human only about filters beyond these.

## 12. Real-time updates

Scheduled sync is the supported path. Webhook handlers have no caller in the platform today, and declaring `SyncStrategy.WEBHOOK` (or `with_webhook_config`) changes nothing: the scheduler acts only on `SCHEDULED`. Do not declare it. A webhook-based design needs the human's agreement, and platform work (a route, signature verification, removing what it registered at the source), before you build it.

## 13. Framework changes

If the connector needs a change in shared code (`app/connectors/core/`, `app/models/`, the graph providers, the processor), stop and ask. Those changes affect every connector and need their own review and tests.
