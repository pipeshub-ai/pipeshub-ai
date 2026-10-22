# Connectors: low-level design

The class-level reference for building a connector. Paths are relative to `backend/python/app/`. Where this file and the code disagree, the code is right; fix this file in the same PR.

## Package layout

A connector named `acme` normally has:

```text
app/sources/client/acme/acme.py            auth-aware client: AcmeClient.build_from_services(...), IClient
app/sources/external/acme/acme.py          API wrapper ("data source"): one async method per endpoint
app/connectors/sources/acme/common/apps.py AcmeApp(App)
app/connectors/sources/acme/connector.py   @ConnectorBuilder(...) class AcmeConnector(BaseConnector)
app/connectors/sources/acme/*.py           optional: access.py, deletes.py, transforms, per-area helpers
```

Keep the connector class about orchestration. Put the mapping of source objects to `Record`s, the permission mapping and the deletion detection in their own modules once they grow past a few functions (Dropbox's `access.py`, `deletes.py` and `shared_folders.py` are an example).

## The API client layer

| Layer | Responsibility | Example |
| --- | --- | --- |
| `sources/client/<vendor>/` | Read auth config, build an authenticated HTTP or SDK client, refresh credentials. Implements `IClient.get_client()` (`sources/client/iclient.py`) and an `async build_from_services(logger, config_service, connector_instance_id)` classmethod that reads `/services/connectors/{connector_id}/config` and branches on `auth.authType`. | `sources/client/linear/linear.py` |
| `sources/external/<vendor>/` | A thin async wrapper with one method per endpoint, returning a response object that carries success, data, error and status. Often generated. | `sources/external/linear/linear.py` (`LinearDataSource`) |

The connector does `client = await AcmeClient.build_from_services(...)` and then `AcmeDataSource(client)`.

- Generators for the `external` wrappers live in `backend/python/code-generator/` (one script per vendor; OpenAPI-based ones take a spec URL). They write into `code-generator/<vendor>/`; copy the result into `app/sources/external/<vendor>/` and review it. They are dev-only and not run in CI.
- HTTP clients take a `ResiliencePolicy` for rate limiting and retries: `HTTPClient(token, ..., resilience=self.resilience)` (`sources/client/http/http_client.py`, transport in `http_resilient_transport.py`). The policy comes from `.with_resilience_config(...)` on the decorator and is cached on the connector (`BaseConnector.resilience`). Prefer this to a hand-written retry loop. Notion, Nextcloud and Drupal Wiki use it.
- Re-read the token from config before each batch of calls, not once at `init()`. OAuth tokens are refreshed in the background and written back to config; a client built at `init()` holds an expired token an hour later. Linear's `_get_fresh_datasource()` is the pattern. For a 401 in the middle of a sync, refresh once and retry the call: `TokenRefreshService.refresh_now(connector_id, connector_type, refresh_token)` (get the service from `startup_service.get_token_refresh_service()`), single-flight as in `gitlab/runtime.py`. The shared HTTP retry does not retry a 401.
- A user-supplied base URL (self-hosted sources) goes through `validate_public_http_url` (`app/utils/url_fetcher.py`) before the client uses it, and must be `https` unless the human agreed otherwise. The Web connector's `address_guard.py` is the reference for fetching arbitrary URLs.
- A synchronous SDK must not run on the event loop. Submit its calls to the connector's share of the shared thread pool: acquire it in `init()` with `self._thread_lease(max_concurrency)`, then `await asyncio.wrap_future(lease.submit(fn, *args))`, and release it in `cleanup()` with `await self._release_thread_lease()`. Google Drive individual is the example.

## `App`

```python
# app/connectors/sources/acme/common/apps.py
from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.interfaces.connector.apps import App

class AcmeApp(App):
    def __init__(self, connector_id: str) -> None:
        super().__init__(Connectors.ACME, AppGroups.ACME, connector_id)
```

Add `ACME` to `Connectors` and `AppGroups` in `config/constants/arangodb.py`. The `Connectors` value is what is stored as `connectorName` on every record.

## `BaseConnector` contract

`connectors/core/base/connector/connector_service.py`. Every abstract method must be implemented or the class cannot be instantiated, and the contract test fails.

| Method | Called by | Must do |
| --- | --- | --- |
| `@classmethod async create_connector(cls, logger, data_store_provider, config_service, connector_id, scope, created_by, data_entities_processor, **kwargs)` | `ConnectorFactory` | Construct and return the instance. Take `scope` and `created_by` as named parameters and accept `**kwargs`; the factory also passes `org_id`. The contract test checks only the five base names, and the factory swallows a `TypeError` here and returns `None`, so a missing `scope` fails silently. Call `super().__init__(app, logger, data_entities_processor, data_store_provider, config_service, connector_id, scope, created_by)` in that order. |
| `async init() -> bool` | event service, on init or first use | Read config, build the client, set up `SyncPoint`s. Return `False` on failure, or raise `ConnectorInitError` with a message the user should see ("the API token was rejected"). Do not start a sync. |
| `async test_connection_and_access() -> bool` | toggle and test routes (awaited, though the base declares it sync) | One cheap authenticated call, such as "current user". Log the vendor's reason on failure. |
| `@classmethod async check_connection(cls, auth_config, logger) -> ConnectionCheckResult`, optional | setup form | Override to test credentials the user has typed but not saved. Returns `ConnectionCheckResult(success, message)`, not a bool. |
| `async run_sync() -> None` | `SyncTaskManager` via `run_sync_task` | The whole sync: full if no checkpoint, incremental otherwise. See "Sync flow" below. |
| `async run_incremental_sync() -> None` | nothing in the platform today | Implement it (delegating to `run_sync()` is acceptable, as Linear does). |
| `full_sync_coverage() -> FullSyncCoverage`, optional | sync runner, after a full `run_sync()` returns | Say how much of the source the run read. Default: all of it. Two different overrides: `complete=False` when the run skipped part of the source after an error (nothing is unlinked); `window_start_ms` (plus `windowed_group_types`) when it reads only a date window by design (records before the window, windowed groups created before it and their records keep hierarchy and inheritance but lose their direct grants; every other unseen node is unlinked). See `01-high-level-design.md`. |
| `async get_signed_url(record) -> Optional[str]` | nothing in the platform; your own `stream_record` may use it | A short-lived download URL, or `None` / `""` if the source has none. To let indexing download directly, set `record.signed_url` on the record instead (sent as `signedUrl` in the index event; only File, Webpage, Link and Ticket records carry it). Never return a URL that embeds a long-lived credential. |
| `async stream_record(record, user_id=None, convertTo=None) -> StreamingResponse` | stream route (indexing and users) | Return the content. The route calls `stream_record(record)`: `user_id` is passed only to the Google Workspace connectors, `convertTo` never (the router converts to PDF itself), and access is checked before the call. Do not rely on either. Files: `create_stream_record_response(stream_content(signed_url, ...), ...)` from `utils/streaming.py`, as Box does. Structured items (issues, pages): JSON blocks with `MimeTypes.BLOCKS`, as Linear does. Map source failures with `stream_errors` (`07-errors-and-logging.md`). |
| `handle_webhook_notification(notification)` | nothing in the platform today | Implement as a no-op or log, unless the human asked for webhooks. |
| `async reindex_records(record_results)` | `<connector>.reindex` | For each record, check the source; upsert changed ones with `on_new_records`, re-publish the rest with `reindex_existing_records`. Linear is the pattern. |
| `async get_filter_options(filter_key, page, limit, search, cursor) -> FilterOptionsResponse` | filter options route | Return options for every `DYNAMIC` filter you declared (for example the list of spaces). Raise for an unknown key. |
| `async cleanup()` | disable, delete, shutdown | Close clients, release the thread lease. Must not raise. |

Other members you will use:

- `self.data_entities_processor`, `self.config_service`, `self.data_store_provider`, `self.connector_id`, `self.scope`, `self.created_by`, `self.logger`.
- `display_name`: the instance name the user sees. Use it in user-facing messages.
- `ensure_creator_user_app_relation()`: personal-scope connectors call it so the creator can reach the App (see `06-permissions.md`). It needs `self.creator_email`, set only by `await self._load_creator_email()`; call that in `init()` first, or the method logs a warning and the creator gets no access (`github/connector.py` and `notion_personal/connector.py` do this).
- `ConnectorSyncSkippedError`: raise from `run_sync()` when the sync cannot run for a reason the user must fix, instead of returning as if it succeeded.
- `notify(type, severity, title, message, ...)`: tell the connector's creator and last syncer about something they must act on (auth failure, a missing scope, a skipped container, an expired cursor). Fire-and-forget; repeats of the same title and message are backed off automatically (from one hour, doubling to seven days) except INFO and SUCCESS. Use `notify_and_wait(...)` and check for `NotificationOutcome.SENT` when you persist that a notice was sent. Name the instance with `display_name`.

## Registration: `ConnectorBuilder`

`connectors/core/registry/connector_builder.py`. The decorator is the single source of the connector's metadata: the UI builds the connector card and settings form from it.

```python
@ConnectorBuilder("Acme")\
    .in_group(AppGroups.ACME.value)\
    .with_description("Sync pages and attachments from Acme")\
    .with_categories(["Knowledge Management"])\
    .with_scopes([ConnectorScope.TEAM.value])\
    .with_auth([
        AuthBuilder.type(AuthType.OAUTH).oauth(
            connector_name="Acme",
            authorize_url="https://acme.example/oauth/authorize",
            token_url="https://acme.example/oauth/token",
            redirect_uri="connectors/oauth/callback/Acme",
            scopes=OAuthScopeConfig(personal_sync=[], team_sync=["read"], agent=[]),
            fields=[CommonFields.client_id("Acme"), CommonFields.client_secret("Acme")],
        ),
        AuthBuilder.type(AuthType.API_TOKEN).fields([CommonFields.api_token("Acme API token")]),
    ])\
    .with_resilience_config(rate_limit=10.0, max_retries=3)\
    .configure(lambda builder: builder
        .with_icon(IconPaths.connector_icon(Connectors.ACME.value))
        .with_sync_strategies([SyncStrategy.SCHEDULED, SyncStrategy.MANUAL])
        .with_scheduled_config(True, 60)
        .with_sync_support(True)
        .with_agent_support(True)
        .add_filter_field(FilterField(name="space_ids", display_name="Spaces",
                                      filter_type=FilterType.LIST, category=FilterCategory.SYNC,
                                      option_source_type=OptionSourceType.DYNAMIC))
        .add_filter_field(CommonFields.modified_date_filter("Only pages modified in this range."))
        .add_filter_field(CommonFields.enable_manual_sync_filter())
    )\
    .build_decorator()
class AcmeConnector(BaseConnector):
    ...
```

Check every builder call against the code before you copy it: `CommonFields` helper names and `with_resilience_config` parameters are in `connector_builder.py`, auth types and `OAuthScopeConfig` in `connectors/core/registry/auth_builder.py`, field types in `connectors/core/registry/types.py`. `examples/` code and old docs have used methods that no longer exist.

| Call | Notes |
| --- | --- |
| `ConnectorBuilder(name)` | The display and registry name. The factory key is this name lower-cased with spaces removed. Integration suites must use it exactly, spaces included. |
| `.with_scopes([...])` | `ConnectorScope.TEAM` (an admin sets it up for the org), `PERSONAL` (each user for their own account), or both. Default is personal only. |
| `.with_permission_model(PermissionModel.X)` | `RECORD_LEVEL` (default): the source has per-item access. `APP_LEVEL`: anyone who can reach the App can read everything in it. `RECORD_GROUP_LEVEL` is rejected here. See `06-permissions.md` before setting `APP_LEVEL`. |
| `.with_auth([AuthBuilder...])` | One entry per supported auth type. OAuth entries register with the OAuth registry. The redirect URI is always `connectors/oauth/callback/<Name>` (`OAuthRedirectPaths` in `connectors/core/constants.py`); the Node callback route is generic. |
| `.with_info(text)` | A note shown on the connector card. Use `CONNECTOR_EMAIL_IDENTITY_INFO` when users are matched by email. |
| `.with_resilience_config(rate_limit=..., max_retries=3, base_delay=1.0, max_delay=60.0)` | `rate_limit` is the source's sustained requests per second, from its documentation; it is required because no default is right across APIs. `max_retries=3` means 4 attempts. Exposed as `self.resilience`. |
| `.configure(lambda b: ...)` | `ConnectorConfigBuilder`: icon, sync strategies and schedule, realtime and agent support, documentation links, custom sync fields, filters, conditional display. |

Register the class in `ConnectorFactory._connector_registry` (`connectors/core/factory/connector_factory.py`), keyed by the lower-cased, space-free name, with its import at the top of that file. Put connectors that are not ready for general use in `_beta_connector_definitions` instead.

## Configuration and credentials

Everything lives in one config document, read with `await self.config_service.get_config(f"/services/connectors/{self.connector_id}/config")`:

| Key | Holds |
| --- | --- |
| `auth` | `authType` and the auth form fields (`apiToken`, `clientId`, ...) |
| `credentials` | `access_token`, `refresh_token` after OAuth |
| `filters.sync.values`, `filters.indexing.values` | the admin's filter choices |
| `sync` | the chosen strategy and schedule |

`use_cache` defaults to `False`; never pass `True` for a credentials path. Pass `raise_on_error=True` where a missing value would lead you to delete records or write empty grants: without it, a failed read returns the default and looks the same as "not set". A shared OAuth app is stored under `/services/oauth/<type>` and fetched with `BaseConnector._fetch_oauth_config_by_id`. Never write credentials anywhere else, and never log them.

## Entities

`models/entities.py`. Build these and hand them to the processor. Timestamps are epoch milliseconds.

**`Record`** (and its subclasses `FileRecord`, `MailRecord`, `WebpageRecord`, `LinkRecord`, `CommentRecord`, `TicketRecord`, `ProjectRecord`, `PullRequestRecord`, ...). Choose the subclass that matches the item; add to `RecordType` only if none fits.

| Field | Set it to |
| --- | --- |
| `external_record_id` | The source's stable id. Never a path or name that changes on rename. |
| `external_revision_id` | Something that changes exactly when content changes: an etag, version number, or `updated_at`. A content change is detected by this field. |
| `version` | `0` for a new record; the processor bumps it when the revision changes. |
| `record_name`, `record_type`, `origin=OriginTypes.CONNECTOR`, `connector_name=Connectors.ACME`, `connector_id` | Required. |
| `external_record_group_id`, `record_group_type` | The container it belongs to. |
| `parent_external_record_id`, `parent_record_type` | The record it nests under, if any. |
| `mime_type` | `MimeTypes.*`. Folders are `MimeTypes.FOLDER`; test for a folder with `in FOLDER_MIME_TYPES`. Structured content is `MimeTypes.BLOCKS`. |
| `weburl` | A link that opens the item in the source. Citations use it. |
| `source_created_at`, `source_updated_at` | From the source. |
| `inherit_permissions`, `inherit_permissions_from_group`, `access_rule`, `rewrite_permissions` | Permission fields. See `06-permissions.md`. |
| `signed_url`, `fetch_signed_url` | Only if the source gives a URL the indexing service can download from. |
| `related_external_records` | Links to other records: a list of `RelatedExternalRecord(external_record_id, record_type, relation_type=RecordRelations.X)` (or `weburl` / `issue_key` to point at a record by link or key). Each write replaces the record's links. A missing target in the same connector becomes a placeholder. |
| people fields | On `TicketRecord`: `assignee_email`, `reporter_email`, `creator_email`. On message records: `mentioned_user_ids`, `mentioned_group_ids`. The processor turns them into assigned-to, reported-by, created-by and mention edges. |
| `indexing_status` | Leave the default unless an indexing filter excludes the record (`AUTO_INDEX_OFF`). |

**`RecordGroup`**: `name`, `external_group_id`, `group_type` (`RecordGroupType`), `connector_name`, `connector_id`, `parent_external_group_id`, `web_url`, plus the permission fields `inherit_permissions` (defaults to `False`) and `access_rule`.

**`AppUser`**: `app_name`, `connector_id`, `source_user_id`, `email`, `full_name`, `is_active` (defaults to `False`; set `True` for active users), `is_guest` (set `True` for guests and external accounts, see `06-permissions.md`). Users are matched to PipesHub users by email.

**`AppUserGroup`**: `app_name`, `connector_id`, `source_user_group_id`, `name`.

**`Permission`** (`models/permission.py`): `external_id` or `email`, `type` (`READ`, `WRITE`, `OWNER`), `entity_type` (`USER`, `GROUP`, `ROLE`, `ORG`, `TEAM`, ...).

## `DataSourceEntitiesProcessor`

`connectors/core/base/data_processor/data_source_entities_processor.py`. The only way a connector writes to the graph. Write order matters: users and groups before the records that grant to them, parents before children where you can (the processor creates restricted placeholders for parents it has not seen, and fills them in later).

| Method | Use |
| --- | --- |
| `on_new_app_users(users)` | Source users. |
| `on_new_user_groups([(group, members)])`, `on_user_group_member_added/_removed`, `on_user_group_deleted` | Source groups and membership. |
| `on_new_app_roles(...)` | Source roles, for connectors with role-based access. |
| `on_new_record_groups([(group, permissions or None)])` | Containers. `None` keeps the stored grants (you could not read them); `[]` clears them. |
| `on_new_records([(record, permissions)], *, replace_permissions=False)` | New and changed records. Written in slices of 100, each in its own transaction; index events are published per committed slice. |
| `on_record_content_update(record, permissions=None)` | Content changed. Publishes `updateRecord`. |
| `on_record_metadata_update(record)` | Rename and other metadata. |
| `on_updated_record_permissions(record, permissions)` | Access changed, content did not. Edges are keyed on `record.id`: pass the stored record from `get_record_by_external_id`, or set `record.id` to the stored id. A freshly built `Record` has a new id and the write goes nowhere. |
| `on_records_moved([(old_external_id, new_record, permissions)], permissions_fetched=...)` | Only for sources whose external id changes on a rename or move (path-keyed: Local FS, GitHub, GitLab). With a stable id, re-send the moved record through `on_new_records` with its new parent and group ids. |
| `delete_record_by_external_id(connector_id, external_id)` | A record gone at the source, by its source id. Attachments go with it; surviving children are re-pointed at what it hung under. |
| `on_record_deleted(record_id)` | Not the same delete: by the internal PipesHub id, it removes only that record and re-parents **every** child, attachments included (with soft delete on, it trashes only that record). For a record gone at the source, use `delete_record_by_external_id`, or a deleted page's files stay behind. |
| `on_records_deleted_cascade(record_ids, connector_id, cascade_children=True, ...)` | Deletes the records and, by default, their whole subtree. With `cascade_children=False` survivors are re-parented. Returns `success: False` on failure rather than raising. |
| `on_record_group_deleted(...)` | Deletes the group's whole subtree. |
| `get_record_by_external_id(...)`, `get_record_group_by_external_id(...)` | Look up what is stored, for incremental decisions. |
| `reindex_existing_records(records)` | Re-publish for indexing without changing the record. |

How `on_new_records` decides between insert and update:

- It matches on `(connector_id, external_record_id)`. A match is updated in place and keeps its PipesHub id.
- A changed `external_revision_id` re-queues the record for indexing, and bumps `version` if you left it at `0`. On an already indexed record this happens even when you set `AUTO_INDEX_OFF`.
- An unchanged record that is already indexed (`COMPLETED`) is not re-queued, so re-sending unchanged records in a full sync is cheap. An unchanged record not yet indexed is re-published as `newRecord`; `on_new_records` never sends `updateRecord`.
- A missing `weburl` or source timestamp is carried over from the stored record.
- Records that are `AUTO_INDEX_OFF`, internal or placeholders are stored but not sent for indexing.

## Sync points

`connectors/core/base/sync_point/sync_point.py`. One `SyncPoint(connector_id, org_id, SyncDataPointType.X, data_store_provider)` per kind of checkpoint (`USERS`, `GROUPS`, `RECORDS`, `RECORD_GROUPS`), created in `init()`. Take `org_id` from `self.data_entities_processor.org_id`, as Linear does.

- `read_sync_point(key)`, `update_sync_point(key, data)`, `delete_sync_point(key)`. Keys are stored as `{org}/{connector}/{type}/{key}`; build per-container keys with `generate_record_sync_point_key(record_type, entity_name, entity_id)`.
- Save a checkpoint only after the items before it are written. Use `FailedItems` so a failed item holds the checkpoint before it: `failed.add(item_modified_ms)` for each failure, then save `failed.checkpoint(max_seen_ms)`.
- Take the next incremental cursor (a delta token, a `start_page_token`, the sync start time) **before** a full listing starts, and save it after the listing finishes. A cursor taken at the end skips every change made while the listing ran. OneDrive, Google Drive team and Dropbox do this.
- When the source rejects a cursor as expired or invalid (Gmail's 404 on an old history id, a Graph delta `410`), drop that container's checkpoint and re-list it in full, and notify the user. Never reset the cursor to "now", which skips the changes in between.
- Store times as UTC epoch milliseconds and send date filters to the source in UTC (or in the time zone the API says it expects).
- Pass `encrypt_fields=[...]` to `update_sync_point` for any secret kept in checkpoint data.
- A full sync deletes all of the connector's sync points before `run_sync()` starts.

## Filters

`connectors/core/registry/filters.py`.

- `FilterCategory.SYNC` filters decide what to fetch; apply them in the API query where the API allows it. `FilterCategory.INDEXING` filters decide what to index; apply them per record by setting `indexing_status`.
- Use the standard keys (`SyncFilterKey`, `IndexingFilterKey`) and `CommonFields` where they fit.
- Load them at the start of every `run_sync()`: `sync_filters, indexing_filters = await load_connector_filters(self.config_service, "<name>", self.connector_id, self.logger)`. Admins change them between runs.
- `DYNAMIC` options are served by your `get_filter_options`.
- Helpers: `extension_passes_filter`, `name_passes_filter`, `FilterCollection.get / get_value / is_enabled`.

## Sync flow

A `run_sync()` that fits the platform:

1. Load filters. Refresh the client's token.
2. Users: list and `on_new_app_users`. Groups and members: `on_new_user_groups`.
3. Containers: list and `on_new_record_groups` with their permissions, parents first.
4. For each container, read its checkpoint; list items changed since (or all, if none); map each to a `Record` plus permissions; write in batches with `on_new_records`; record failures in `FailedItems`; save the checkpoint.
5. Deletions and moves the source can report: apply them (`delete_record_by_external_id`; a moved record with a stable id is re-sent through `on_new_records`). Deletions it cannot report are handled by the full-sync cleanup (`01-high-level-design.md`).
6. Return normally only if the run did what it says. If part of the source was skipped after an error, `full_sync_coverage()` returns `complete=False`; if the run reads a date window, it returns `window_start_ms`; if nothing could run, raise.

Keep memory bounded: process page by page, write batch by batch, and do not hold the whole source in a list.

### Placeholders and filters

When a record names a parent the sync has not written (because it comes later, or a filter excluded it), the processor creates a restricted placeholder. Filling placeholders is the connector's job: after the main pass, list them with `get_placeholder_records(connector_id, record_group_id=None)`, fetch the real parents, and write them. Keep a placeholder when its fetch fails. Do not fetch parents from containers the admin's filters exclude. Jira Cloud ("placeholder sweep", with a depth cap) and Confluence Cloud are the references.

When an admin narrows a sync filter, the platform queues a full sync and its cleanup unlinks whatever that sync did not write. To delete out-of-scope records outright, use the `folder_scope` helpers (`app/connectors/core/registry/folder_scope.py`), as Azure Blob and GCS do. Changing an indexing filter queues nothing: records already left `AUTO_INDEX_OFF` stay unindexed until reindexed.

## Content: signed URL and streaming

The indexing service first tries `record.signed_url` (sent as `signedUrl` in the index event); if absent or failing, it calls the connector's internal stream route, which runs `stream_record()` inside `streaming_for_indexing()` (`connectors/core/base/connector/stream_purpose.py`). Users opening a record go through the public stream route to the same method. Stream; do not read a large file into memory.
